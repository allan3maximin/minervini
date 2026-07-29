# ミネルヴィニ式スクリーナー 実装ハンドオフドキュメント

最終更新: 2026-07-08 / 対象コミット: (未コミット — EDINET DB決算短信補完の追加 + フロントSPA化)
※ かつてのEDINET API v2連携(四半期報告書ベース)は撤去済み(a5c76fc)。自動ファンダ取得のメインは
  J-Quants API(72ffe1a)。2026-07-08、その12週遅延窓を補うEDINET DB(決算短信ベース、別サービス)
  連携を追加(下記5章)。同日、フロントエンドを1ページSPA化(ダッシュボード/セクターマップ/投資法/
  バッチ実行/個別株詳細の5ビューをDockナビ+hashルーティングで切替、下記7章)。
このドキュメントは、以降の軽微な改修を別モデル(Sonnet等)が引き継げるように全体像と実装詳細をまとめたもの。

---

## 1. プロジェクト概要

日本株を対象にしたマーク・ミネルヴィニ SEPA 手法のスクリーナー。
- **バックエンド**: Python (pandas/yfinance)。GitHub Actions で日次実行し、結果 JSON を `docs/data/` にコミット。
- **フロントエンド**: GitHub Pages (`docs/`)。ビルド無しの素の HTML/CSS/JS。チャートは Lightweight Charts 4.1.3 (unpkg CDN)。
- **リポジトリ**: https://github.com/allan3maximin/minervini (ブランチ: `master`)
- データは EOD(日足終値ベース)。ザラ場中のブレイク検知はしない設計(逆指値の事前設定が前提)。

## 2. リポジトリ構成

```
config.yaml                 全パラメータ(セクション別、後述)
requirements.txt
.github/workflows/
  daily.yml                 日次バッチ (cron 30 7 * * 1-5 = 16:30 JST 平日)
  universe.yml              月次ユニバース再構築 (毎週土曜起動→月初土曜のみ実行、手動可)
  jquants-backfill.yml      J-Quants過去分取得 (workflow_dispatch専用、全銘柄をcode指定で取得)
src/
  config.py                 load_config() = config.yaml のロード, REPO_ROOT
  pipeline.py               日次パイプライン本体 (python -m src.pipeline [--universe-rebuild])
  universe.py               ユニバース構築 (JPX上場一覧→20日平均売買代金1億円以上→セクターmap/発行済株式数)
  indicators.py             MA50/150/200, MA200勾配日数, 52w高安, ATR, RS raw/percentile, RSライン
  backtest.py               簡易バックテストCLI (python -m src.backtest。フェーズ1簡易版、GitHub Actions化なし。2026-07-11追加)
  data/
    prices.py               株価取得 (yfinanceチャンク→stooqフォールバック, parquetキャッシュ data/prices/)
    indices.py              市場指標7種 (日経/TOPIX/グロース250/JGB10y/USDJPY/NASDAQ/SOX) → docs/data/indices.json
                            (CLI: `python -m src.data.indices` で単独更新可。intraday-indices.yml が使用)
    fundamentals.py         手動CSV + J-Quants自動 + EDINET DB自動の3ソースマージ + tier判定 + スコア
    jquants.py              J-Quants API v2 自動ファンダ取得 (→ data/fundamentals_auto.json)
    edinetdb.py              EDINET DB 決算短信取得 (J-Quants 12週遅延窓の直近四半期のみ補完, → data/edinetdb_auto.json)
  screener/
    trend_template.py       トレンドテンプレート8条件(MUST) + テクニカル/フルスコア + EPS加速slope
    priority.py             ハードフィルタ + P1〜P4評価 (※UIからP1-P4は廃止済み。バックエンドは残存)
    vcp.py                  VCPベース検出 (zigzag→収縮列→MUST→品質スコア→フットプリント)
    entry.py                エントリー状態機械 (WATCH_A/B, BREAKOUT等) + ピボット/損切り/ティック丸め
    scoring.py              線形スコア/正規化/合成のユーティリティ
  report/
    build_site.py           report.json / breadth.json / charts/*.json の生成
    heatmap.py              東証33業種ヒートマップ (docs/data/heatmap.json + data/sector_history.json)
    market_signal.py        地合いシグナル (市場ブレッドス + TOPIXトレンド合成、breadth.json historyへ格納。2026-07-11追加)
    positions.py             ポジション管理 (manual/positions.csv → docs/data/positions.json、R倍数/売りシグナル計算。2026-07-11追加)
    summary.py              個別銘柄のルールベース日本語サマリー生成 (LLM不使用。status/VCP詳細/ファンダ/地合いの言語化を
                            {headline, points, cautions} で report.json の各銘柄 "summary" に格納。app.jsの
                            renderStockSummaryが個別銘柄画面の先頭に描画。STATUS_LABELS_JAはapp.jsのSTATUS_LABELSと対で保守。2026-07-12追加)
docs/                       GitHub Pages ルート
  index.html                1ページSPA (2026-07-08〜): view-dashboard/view-sectormap/view-invest/view-positions/view-batch/view-stock の
                            6セクション+下部Dockナビ(#dock-nav)。表示切替は location.hash ベース(app.jsのshowView/initRouter)。
                            view-stockのみDockナビにボタンが無いドリルダウン専用ビューで、hashは"stock/CODE"の形
                            (パラメータ付き)。Lightweight Charts CDNスクリプトもここに追加済み。
  stock.html                旧URL(?code=...)用リダイレクトスタブのみ(JSでindex.html#stock/CODEへ転送)。
                            実体はview-stockに統合済み(2026-07-08〜)。
  heatmap.html              旧URL用リダイレクトスタブ (meta refresh → index.html#sectormap。実体はSPAに統合済み)
  assets/
    app.js                  ダッシュボード+個別株+SPAルーターの全ロジック (~1080行, 唯一の大物JS)
    heatmap.js              ヒートマップ描画 (詳細=既存treemap / 簡易=セクター騰落率のみ、#hm-view-toggleで切替)。
                            SPA化に伴い `hmWired` フラグで一度きりのイベント登録をガードし、
                            initHeatmap() をタブ切替のたびに安全に再呼び出しできるようにした(ファイル末尾の自動起動は削除、呼び出しはapp.jsのrouterが担当)。
    batch.js                バッチ実行ページ(view-batch)の描画。config.jsのworkflows一覧からカード生成+
                            listWorkflowRuns で直近実行履歴を表示。initBatchView() をrouterが呼ぶ。
    config.js               window.MINERVINI_CONFIG (owner/repo/branch, passkeyAuthEnabled キルスイッチ,
                            workflows: バッチ実行ページに出す手動トリガー可能なワークフロー一覧)
    github-api.js           GitHub Contents/Actions API ラッパ (PATはメモリのみ)。dispatchWorkflow(workflowFile)で
                            任意ワークフローをトリガー、listWorkflowRunsは未認証fetchで直近実行履歴を取得(バッチページ用)。
    webauthn-vault.js       WebAuthn PRF でPATを暗号化保管 (docs/auth/vault.json)
    fundamentals-modal.js   ファンダ手動入力モーダル (CSVをGitHub API経由でコミット) + triggerWorkflow(バッチページの汎用実行トリガー)
    style.css               全スタイル (ダークテーマ, CSS変数 --bg/--text/--accent/--danger等)。Dockナビ/
                            view-section/invest-content/batch-cards等のSPA関連スタイルを追加、未使用だった
                            .prio-badge/.priority-* は削除済み。
  data/                     パイプライン出力 (report.json, breadth.json, heatmap.json, indices.json, charts/{code}.json,
                            positions.json ※manual/positions.csvに行がある場合のみ)
                            ★2026-07-27〜 .gitignore 済み = master にコミットしない。実体は gh-pages
                            ブランチ(単一コミットを毎回force-push)にのみ存在する。詳細は §8。
data/                       中間データ (universe.json, prices/*.parquet, indices/*.parquet,
                            history/status.jsonl, history/sector.jsonl ← 追記専用履歴(2026-07-27〜。詳細は §9)、
                            status_history.json / sector_history.json ← 旧形式。移行フォールバック用に残置、
                            sector_map.json,
                            trend_template_debug.json, ※J-Quants実行後: fundamentals_auto.json, jquants_state.json,
                            ※EDINET DB実行後: edinetdb_auto.json, edinetdb_state.json)
src/history_store.py        追記専用JSONLの読み書き基盤 (append_records / load_deduped / compact)
src/analyze.py              履歴JSONLをDuckDBでSQL分析するCLI (--list / --preset / --sql)
scripts/migrate_history_to_jsonl.py  旧履歴JSON → JSONL への一括変換 (冪等・--dry-run あり)
.github/actions/            composite action 2種 (restore-site-data / publish-site)。詳細は §8。
manual/fundamentals.csv     手動ファンダ (code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date)
manual/positions.csv        保有ポジション手動入力 (code,entry_date,entry_price,shares,initial_stop,current_stop,memo。
                            2026-07-11追加。書き込みUI無し、GitHub web編集/ローカル編集で運用)
skills/minervini-analysis/  「分析用データをコピー」の出力をSEPA手法で読み解くClaude用スキル (SKILL.md)
tests/                      pytest 247件 (test_jquants.py, test_edinetdb.py, test_pipeline.py, test_market_signal.py,
                            test_positions.py, test_backtest.py, test_summary.py 含む)
```

## 3. 日次パイプラインの流れ (src/pipeline.py :: run_daily)

1. `jpholiday` で祝日ならスキップ (return 0)
2. 市場指標更新 `indices_mod.update_indices` — 失敗してもスクリーナーは止めない (try/except)
3. `load_universe()` → codes (20日平均売買代金1億円以上。銘柄数は市況で変動する)
4. `prices_mod.update_prices(codes)` — yfinanceを50銘柄チャンク+sleep 2-4s、失敗銘柄はstooqへ
   (`data.stooq_max_codes` 件で打ち切り)。失敗率 > `data.max_fail_ratio`(0.15)でジョブ失敗
5. `compute_all` で指標付与 → `rs_percentile_rank` でRS(1-99パーセンタイル、母集団はユニバース)
6. `trend_template.screen_universe` → 8条件フラグ (debug: data/trend_template_debug.json)
7. `priority_mod.evaluate_priority` — ハードフィルタ通過銘柄にP1〜P4付与。`p1_scarce` = P1数 < priority.p1_warn_threshold(3)
8. **ファンダ**: `load_fundamentals_csv()` (手動CSV) + `jquants_mod.update_fundamentals_auto(codes, config)` (自動取得、try/exceptで失敗無視) → `merge_fundamentals(auto, manual)` (手動が勝ち)
8.5. **ポジション管理 (2026-07-11追加)**: `positions_mod.load_positions_csv()` (manual/positions.csv) →
   `positions_mod.build_positions_report(positions, indicator_by_code, name_by_code)` → `write_positions_json`
   (docs/data/positions.json)。try/exceptで失敗しても本体は止めない。詳細は下記「ポジション管理」節。
9. P1銘柄のみ: VCP評価 → エントリー評価 → `score_stock` → レコード組立 + チャートJSON出力
   - actionable (BREAKOUT/BREAKOUT_WEAK/WATCH_A/WATCH_B/EXTENDED + pivotあり) → confirmed/pool ティア
   - それ以外 → `tier_override="watchlist"` (=フロントの〔候補〕)
   - **P2〜P4は2026-07-11以降 report.jsonへ出力しない**(フロントは`priority===1||null`のP1銘柄
     しか表示しておらず受信して捨てるだけだったため、`assemble_priority_record`ごと削除。
     `priority_counts`はここより上の`priority_by_code`から独立集計されるため
     breadth.jsonのp1_count〜p4_count記録には影響しない)
10. ヒートマップ生成 (try/except) → 各レコードに sector33/sector_strength/sector_direction 付与
11. **地合いシグナル生成 (2026-07-11追加)**: `market_signal_mod.compute_market_signal(latest_by_code,
    config)` (try/except、失敗しても本体は止めない)。市場ブレッドス(MA200/MA50上回り率、新高値/
    新安値件数)+ TOPIXトレンド(MA50/MA200上抜け、MA200の21営業日前比較による上向き判定)を合成し
    green(攻め)/yellow(中立)/red(守り)を判定(詳細は下記「地合いシグナル」節)。
12. `build_report` (docs/data/report.json) + `update_breadth` (docs/data/breadth.json、
    `market_signal`引数で上記シグナルのフィールドをhistoryエントリへマージ)

### ティアとフロント表示の対応
- `tier: "confirmed"` → 〔本命〕ファンダ強度確認済み。**データの存在だけでは昇格しない** (2026-07-09改定):
  直近EPS YoY ≥ +25% **かつ** 売上YoY ≥ +20% (config.yaml `fundamentals.confirmed_eps_yoy_min`/`confirmed_rev_yoy_min`、Minervini Code 33準拠)。
  YoY計算不能(前年比較対象なし/前年値≤0)は強度未確認として pool 止まり。判定は `fund_coverage_tier` (src/data/fundamentals.py)。
- `tier: "pool"` → 〔候補プール〕VCPセットアップあり、ファンダなし **または強度基準未達** (`fund_strong: false` → フロントで「ファンダ弱」表示)
- `tier: "watchlist"` → 〔候補〕トレンドテンプレート8条件合格 (セットアップ形成待ち)
- `tier: "cooled"` → 〔追禁〕ブレイク済みで手遅れ (EXTENDED=伸びすぎ / STALE=鮮度切れ)。
  watchlist とは意味が違う(watchlist=形成待ち、cooled=ブレイク後)ので別ティアで管理する。
  ピボット・損切りレコードは保持(チャート表示・クールダウン管理に必要)。
  **表示可否はステータスフィルタが決める** (2026-07-27改定: 折りたたみ `<details>` は撤廃)。
  既定で EXTENDED/STALE のチェックが外れているため通常は一覧に出ず、ボトムシートで
  チェックを入れると出る。出たときは行を淡色(`.sc-cooled`)にし「追禁」バッジを付ける。
  **スコアにはペナルティを一切課さない**(実測エッジの無い減点は指標の意味を濁らせるため)。
  `ACTIONABLE_ENTRY_STATUSES` からは外れ、`COOLED_ENTRY_STATUSES = {"EXTENDED", "STALE"}` で管理。

- **2026-07-29改定: ティアは並び順に使わない。** 一覧は本命/候補/監視の3タブを廃止し、
  `total_score` 降順の単一リストに統合した。統合できた理由は `combined_score` が
  VCPスコア欠損を 0 扱いに変えたこと(scoring.py)。以前は欠損時 `total_score = tech_score` に
  フォールバックしていたため「テクニカル70・VCP70」と「テクニカル70・VCP未成立」が
  同じ70になり、同じ列で並べられなかった(だからティアで隔離するしかなかった)。0に倒すと
  セットアップ未成立は上限50に沈み、**順序そのものが両者を分離する**。
  `_sort_key` は `(-total_score, status_rank, code)` の単一軸。`TIER_ORDER` /
  `SECTOR_STRENGTH_ORDER` は意味の序列の記録として残っているが、ソートには使っていない。
  ティア・セットアップ進行度・追禁はすべてフロントの行内バッジで表す。詳細は log.md (143)。
- 補足: `full_score`/`eps_accel_slope` はファンダデータがあれば tier に関係なく計算される(個別株画面・コピー機能用)。**2026-07-22改定: full_score は表示専用**となり、ランキング(total_score の phase1 側)は全ティアとも tech_score を使う(純セットアップ品質で順位付け。RSが業績を織り込むためスコアへのファンダ加点は二重計上、という整理)。
- **ファンダのサイズ係数 (2026-07-22追加)**: ファンダはランキングから外した代わりに、エントリー可否とロット管理に反映する。`fund_verdict_and_multiplier` (src/data/fundamentals.py) が Code33基準(confirmed_eps_yoy_min/confirmed_rev_yoy_min、`fund_coverage_tier` と同一閾値)で判定し、`fund_verdict`("pass"|"unknown"|"fail") と `fund_multiplier`(1.0|0.5|0.0) を report.json に出力。**fail=0 はエントリー取り止め**(セットアップ完成でも見送り)、unknown=0.5 はハーフサイズ、pass=1.0 はフルサイズ。フロントの株数計算機 (app.js renderSizingResult) が許容損失に乗数を掛け、係数0なら計算せず取り止め表示。カード一覧には F バッジ(fail=赤「F不合格 取止」/unknown=黄「F未確認 ½」)。旧report.json(フィールドなし)は係数1.0扱いで後方互換。資金額・リスク%はフロントのlocalStorage(`minervini_sizing_settings`)のみでリポジトリには置かない(公開リポジトリのため)。

### tech_score(2026-07-29 全面作り直し)

ランキングの主軸である `tech_score` を、固定重み＋ハードコード境界の合成点から
**「その日のMUST通過銘柄の中での断面パーセンタイルの等ウェイト平均」** に作り替えた。
実装は `src/screener/trend_template.py` の `attach_score_percentiles` / `technical_score`。

使う変数は3つだけ(いずれも「大きいほど良い」向きに符号を揃える):

| 成分キー | 生値 | 出所 |
|---|---|---|
| `ma200_slope` | `ma200_slope_21d` = MA200の21営業日上昇率 | `src/indicators.add_ma200_slope_21d` |
| `low52w_ratio` | `close / low_52w` | latest から直接 |
| `dryup` | `-dryup_med_10_50`(枯れているほど高得点) | `src/indicators.add_dryup_series` |

**設計上の縛り(後から緩めないこと)**
1. **等ウェイト**。重み探索はしない。26年フィットで2年フィットを置き換えるだけに
   ならないための歯止め(Dawes 1979 の improper linear model の頑健性が根拠)。
2. **正規化は当日の断面パーセンタイルのみ**。閾値・境界値を一切持たない。
3. **母集団はその日のMUST通過銘柄に限定**。効果量をこの部分集合の中で測ったので、
   採点も同じ部分集合で行わないと測定と運用がズレる。MUST落ち銘柄には `score_pct`
   を付けず、`technical_score` は None を返す。

**RS と near_high(52週高値からの近さ)はスコア寄与ゼロ。** MUSTフィルタとしては
残す。「通過に必要な条件」と「通過者の順位付けに効く変数」は別物。26年の
アブレーションでは、この2つをスコアに足し戻すと上位20%の期待Rが
+0.209R → +0.199R → +0.177R と単調に下がった。

**呼び出し順序の制約**: `technical_score` は断面が要るので単独銘柄からは計算できない。
`attach_score_percentiles(latest_by_code, config)` を先に通すこと。`screen_universe`
は内部で呼ぶので pipeline 側の追加配線は不要。`scripts/dump_raw_vs_interp.py` は
明示的に呼んでいる。

**フロント表示 (2026-07-29追加)**: `build_stock_record` が `latest_row["score_pct"]` を
そのまま report.json の `score_pct` へ流す。個別画面の `renderScoreBreakdown` (app.js) が
総合/テクニカル/VCP/フルのバーの下に「テクニカルの内訳 — 当日の全銘柄中の順位
(パーセンタイル)」として3成分を `SCORE_PCT_COMPONENTS` の順(200日線の傾き / 52週安値
からの倍率 / 出来高の枯れ)でサブバー表示する。`dryup` は `score_variables` の時点で
符号反転済みなのでフロント側で反転しないこと(枯れているほど高い値が来る)。

**旧スコアの何が問題だったか**: RS 54.5% / near_high 27.3% / MA200上向き日数 18.2% の
配分だったが、MUST通過集合の中ではこの3変数の期待Rの幅は 0.02〜0.03R しかなく、
局面別の符号も 5勝5敗〜7勝4敗 のコイン投げだった。結果として旧スコアの上位20%
(+0.117R)は自分の上位50%(+0.140R)にも無条件ベースライン(+0.126R)にも負けており、
選別力が負だった。新スコアの上位20%は +0.209R、11局面中10局面で旧を上回る。
検証の全文は log.md (140)。

### P1〜P4について(重要な経緯)
- バックエンド(priority.py, report.jsonの `priority`/`priority_counts`/`p1_scarce` フィールド)は**P1〜P4を計算し続けている**。
- **UIからは概念を廃止**: フロントは priority を表示にも並び順にも使っていない(2026-07-29の一覧一本化で `renderPriorityTier` 自体を削除。それ以前は `tier==="watchlist" && (priority===1 || priority==null)` を〔候補〕として全件RS降順表示していた)。**2026-07-11以降 P2〜P4は report.json へ出力自体しない**(`src/pipeline.py`で`continue`するだけになり、軽量レコード組立関数`assemble_priority_record`ごと削除。転送量削減が目的)。
- 弱地合い警告バナー(renderP1Warning)は残存。文言は「8条件完全一致の候補銘柄が◯件と極端に少ない…」(P1という語は使わない)。
- 地合いメーターに「候補(8条件合格): N件」を表示 (renderBreadth)。
- style.css の .prio-badge / .prio-1〜4 / .priority-table は2026-07-08に削除済み(対応するJSがCOLUMNS一本化で消えたため)。

### 地合いシグナル (src/report/market_signal.py) — 2026-07-11追加

ユーザーの毎朝のルーティン(地合い確認→銘柄チェック)を自動化する目的で、日次パイプラインが
市場ブレッドス指標を計算し、攻め(green)/中立(yellow)/守り(red)の3段階シグナルとして
ダッシュボード最上部に表示する機能。SEPAでは市場が弱い時に新規エントリーを控えるのが原則
なので、地合い判定は最重要機能という位置づけ。

- **ブレッドス指標** `compute_breadth_stats(latest_by_code)`: pipeline.run_daily内で構築済みの
  `latest_by_code`(各銘柄の最新行、close/ma50/ma200/high/low/high_52w/low_52w を含む)から
  `pct_above_ma200`/`pct_above_ma50`(close > MA の銘柄比率、MAがNaNの銘柄は分母から除外)、
  `new_high_count`/`new_low_count`(high>=high_52w / low<=low_52w の銘柄数、52w高安が当日を
  含むrolling集計であることを利用)を計算。
- **指数トレンド** `compute_index_trend(index_df)`: `indices_mod.load_cache("topix")` の日足終値
  からMA50/MA200を計算し、`index_above_ma50`/`index_above_ma200`(bool)と
  `index_ma200_slope_up`(MA200の直近値が21営業日前より高いか)を判定。221営業日未満のデータ
  しか無い場合(キャッシュ欠損含む)は`None`を返し、呼び出し側は絶対に例外にしない。
- **合成シグナル** `compute_market_signal(latest_by_code, config, index_df=None)`:
  `config.yaml: market_signal.green_pct_above_ma200`(既定0.50)/`red_pct_above_ma200`(既定0.30)
  を閾値に、
  - `red`: 指数がMA200割れ、またはpct_above_ma200が赤閾値未満
  - `green`: 指数がMA50・MA200上抜けかつMA200上向き、かつpct_above_ma200が緑閾値以上、
    かつ新高値>新安値
  - それ以外は `yellow`(指数トレンド判定不能の場合も理由に「指数データ欠損」を添えてyellow)
  を返す。`reasons`(日本語の根拠文字列リスト)も同時に返す。
- **pipeline.py への組み込み**: ヒートマップ生成の後、`update_breadth`呼び出しの直前で
  try/except実行(失敗しても本体は止めない、`market_signal_mod.compute_market_signal(latest_by_code,
  config)`)。結果は`build_site.update_breadth(..., market_signal=signal_result)`経由で
  breadth.jsonのhistoryエントリへそのままマージされる(`market_signal`がNoneなら何も追加しない)。
- **フロント (app.js)**: `renderMarketSignal(breadth)` がbreadth.jsonのhistory最新エントリから
  `signal`フィールドを読み、`#market-signal-card`(index.html、`#market-overview`の直前に配置)へ
  色付きラベル(攻め/中立/守り)+ 根拠箇条書き + MA200上回り率/新高値/新安値の数値を描画。
  `signal`フィールドが無い(旧データ/計算失敗)場合はカードごと非表示。red時は
  「⚠ 新規エントリーは控えるのが原則です。」を追加表示。CSSは`.market-signal-card`と
  `.signal-green/-yellow/-red`修飾子(style.css)。

**地合い詳細化 (2026-07-18タスク3/4追加)**: 上記の green/yellow/red 判定ロジック本体は
1文字も変更せず、`compute_market_signal`の出力dictへ表示専用の詳細指標を追加した。
- `market_signal.py`: `breadth_today`(当日advancers/decliners、pipeline.pyが
  indicator_by_codeのdf末尾2行終値比較でカウント)・`breadth_history`(既存history、
  I/Oはpipeline側)・`nikkei_df`/`growth_df`を新規引数に追加し、
  `up_down_ratio_25`(騰落レシオ、history25件揃うまでNone)、`breadth_trend_20d`
  (pct_above_ma200の20エントリ前比較)、`net_new_highs`/`nh_nl_cumulative`(旧
  エントリにフィールド無しなら当日netから再スタート=null安全)、`index_trends`
  {topix, nikkei225, growth250}の3指数マルチトレンド、`growth_rel_20d`(グロース250-
  TOPIXの20日リターン差)、`market_score`(0-100、config `market_signal.detail_weights`
  で配点breadth/index_trend/momentum/risk_appetite、`detail_scale`で線形クリップ境界を
  設定化。データ欠損時は各サブスコアを中立50%にフォールバックしてnull安全)、
  `score_breakdown`(内訳)、`score_trend`(5エントリ前比較、±3以内はflat)を返す。
- **フロント (app.js)**: `renderMarketSignal`のヘッダーに`market_score`+`score_trend`
  矢印(↗改善/→横ばい/↘悪化)バッジを追加(market_scoreがnullの旧historyでは非表示)。
  折りたたみ式`<details class="market-detail">`「地合い詳細」パネル(初期は閉)を新設し、
  `renderMarketDetailHtml`がサブスコア4本(既存の`.score-bar-row`を再利用)・指標テーブル
  (○×表示、指数データ不足行は「データ不足」)・`pct_above_ma200`/`nh_nl_cumulative`の
  スパークライン2本(既存の汎用`sparklineSvg`ヘルパーを再利用、**外部チャートライブラリは
  使用しない**方針)を描画する。`up_down_ratio_25`/`breadth_trend_20d`が蓄積不足でnullの
  間は「蓄積中(あとN日)」を表示し、新旧フィールド混在のhistoryでもクラッシュしない
  (2026-07-18(63)のNode `vm`ハーネスでの検証手順はlog.md参照)。
- これらの詳細指標・`market_score`はあくまで表示用の補助情報であり、既存の
  green/yellow/red判定自体には一切使わない(§3冒頭の判定ロジックは不変)。

### ポジション管理 (src/report/positions.py) — 2026-07-11追加

ツールは従来「エントリーするまで」しかカバーしておらず、保有銘柄のR倍数・ストップ距離・
売りシグナルをユーザーが手計算していた。エグジット支援としてこの機能を追加。

- **入力** `manual/positions.csv`(手動編集、`manual/fundamentals.csv`と同じ「手で編集するCSVを
  パイプラインが読む」パターン。書き込みUIは無い — `passkeyAuthEnabled: false`で書き込み系UIは
  全部killされているため、GitHub web編集かローカル編集で行を足す運用)。スキーマ:
  `code,entry_date,entry_price,shares,initial_stop,current_stop,memo`。クローズしたポジションは
  行を削除する(履歴管理はスコープ外)。
- **`load_positions_csv(path=None)`**: CSVをパースし `(positions: list[dict], warnings: list[str])`
  を返す。code空・日付/数値パース不能な行はスキップして警告に載せる(load_fundamentals_csvと
  同じ流儀)。
- **`build_positions_report(positions, indicator_by_code, name_by_code, today=None)`**: 各ポジションに
  ついて `indicator_by_code[code]`(日足指標付きDataFrame)の最新行から現在値・R倍数・売りシグナルを
  計算:
  - `pl_pct`/`pl_jpy`: 建値との差分
  - `r_multiple = (close - entry_price) / (entry_price - initial_stop)`(`entry_price<=initial_stop`の
    異常データはNoneにして警告)
  - `dist_to_stop_pct = (close - current_stop) / close * 100`
  - `days_held`: 暦日(営業日ではない)
  - `sell_signals`(該当するもの全部): `STOP_BREACH`(close<current_stop)、`MA50_BREAK`
    (close<ma50)、`MA200_BREAK`(close<ma200)、`TAKE_PROFIT_ZONE`(r_multiple>=2.0)、
    `BREAKEVEN_READY`(r_multiple>=1.0 かつ current_stop<entry_price)
  - `indicator_by_code`に無いcode(ユニバース外・上場廃止)は`data_missing: true`+数値null
- **既知の制約**: `indicator_by_code`はユニバース銘柄のみなので、ユニバースから外れた保有銘柄は
  必ず`data_missing`になる。将来的に保有銘柄をprices取得対象へ加える改修が必要になる可能性がある
  (§12参照)。
- **出力** `docs/data/positions.json`: `{generated_at, warnings, positions: [...]}`。
  `write_positions_json`が書き出す。pipeline.pyは`load_positions_csv`の警告と
  `build_positions_report`の警告(R計算異常等)を結合してから書き込む。
- **フロント (view-positions, 2026-07-11追加)**: Dockナビに「保有」ボタン(bi-briefcase-fill)を
  追加、`VIEWS`配列に`"positions"`を追加。`initPositionsView()`がpositions.jsonをfetchし表を描画:
  コード/銘柄名/建値/現在値/損益%/R/ストップ/ストップまで%/保有日数/シグナル。
  `sell_signals`は日本語バッジ(`SELL_SIGNAL_LABELS`、STOP_BREACH/MA50_BREAK/MA200_BREAKは
  danger色、TAKE_PROFIT_ZONEはaccent色、BREAKEVEN_READYはwarn色)。シグナルありの行を上に
  ソート。行クリックで`#stock/CODE`へ遷移(`data_missing`行は`.row-static`でクリック不可)。
  0件なら「manual/positions.csvに行を追加してください」+ GitHub編集画面へのリンク。
  ダッシュボード側にも導線: `renderPositionsWarningBanner`が保有銘柄に`sell_signals`が1つでも
  あれば`#positions-warning`(市場概況の上)に「⚠ 保有N銘柄に売りシグナル」を表示、
  クリックで`#positions`へ。

### 信用残(信用取引週末残高) (src/data/margin.py) — 2026-07-18追加

需給(買い長/売り長)を見たいという要望を受けて追加。**方針は一貫して「スコアは順位付け、
フラグは事実」— 信用残バッジ・需給サマリー文はすべて表示専用のレイヤーで、総合スコア
(`total_score`、scoring/priority/vcp)には一切組み込まない。** これは既存の dryup(枯れ)
バッジと同じ設計思想を踏襲したもの。

- **取得** `src/data/margin.py`: JPXの信用取引週末残高(週次更新、最大5営業日遅れる)を
  取得・整形し、銘柄ごとに買残/売残/信用倍率(買残÷売残)/買残回転日数(買残÷平均出来高)/
  買残前週比を計算。データは `data/margin_weekly.json` に保存(store形式、pipeline.pyが
  1回だけ読み込んで各処理へ渡す)。
- **バッジ判定** `build_margin_metrics(code, latest_row, store)` がメトリクスを計算し、
  `src/report/build_site.py::margin_badge(metrics, config)` が
  `_build_dryup_badge`と同じ「サーバ側でconfig閾値を見て判定文字列を確定し、フロントは
  表示のみ」の流儀で判定: `ratio >= high_ratio_warn` かつ `days_to_cover >= dtc_warn` で
  `"heavy_buy"`(買残重い)、`ratio <= low_ratio_info` で `"short"`(売り長)、それ以外は
  `None`。`positions.py::_margin_for`もこの`margin_badge`を再利用(保有ビューの信用残
  バッジも本体と同じ判定基準にするため)。
- **サマリー文** `src/report/summary.py::build_stock_summary`: `margin`がNoneなら何も
  出さない。badge=heavy_buyはcautions(「買残が重く上値の重さに注意」)、badge=shortは
  points(「売り長で踏み上げ余地あり」)、それ以外は中立のpoints行。売残0は「売残なし」表記。
- **フロント (app.js)**: `marginBadgeHtml(m, {detail})`。一覧カード(`renderCardList`の
  `sc-row-sub`行、`sc-margin`セル)は場所が窮屈なため「買残重い」のみ表示、「売り長」は
  個別銘柄詳細ページの需給カードのみで表示(既存の`sell-signal-badge`系クラスを再利用、
  新規CSSクラス無し)。`renderStockMargin(stock)`が個別銘柄ページ(`data-panel="fund"`、
  ファンダカードの下)に「需給(信用取引)」カードを追加: 信用倍率(大きく表示)、買残
  (前週比%色付き)/売残、買残回転日数、「M/D申込時点」+週次データにつき最大5営業日
  遅れる旨の注記。`record.margin`がnullなら「信用残データなし」のみ表示。
- **次のタスク候補**: 信用残をスコアに組み込む(逆張り的な「買い長すぎる=過熱」フラグを
  priorityへ反映する等)かどうかは、`src/backtest.py`(§13)で13週間程度の実績を見てから
  再検討する(現時点では表示専用のまま様子見。ユーザーからの明示的な指示があるまでは
  スコア組み込みをしない方針)。

## 4. J-Quants 自動ファンダ取得 (src/data/jquants.py) — EDINETから移行済み

### 経緯
- 当初 EDINET API v2 で自動ファンダ取得を実装していたが、**2024年4月の四半期報告書廃止で
  Q1/Q3が単体で取れなくなり粒度不足**と判明 → `a5c76fc` で撤去。
- 決算短信サマリーをそのまま四半期粒度で返す J-Quants `/v2/fins/summary` に置き換え (`72ffe1a`)。
  post-2024でも1Q/3Qが単体で取得できるのがEDINET比での決定的な利点。

### 仕組み
- **API**: J-Quants API v2。`GET {api_url}/fins/summary?date=YYYY-MM-DD`(日別全銘柄)または
  `?code=XXXX`(銘柄別全期間)。認証はヘッダ `x-api-key: <JQUANTS_API_KEY>`。ページングは `pagination_key`。
- **APIキー**: 環境変数 `JQUANTS_API_KEY`。GitHub Secret に登録済み想定。
  **キーが無い場合はネットワークに一切触れず既存ストアを返すだけ**(テスト/ローカルが壊れない設計)。
- **Freeプランの制約**: データが**12週間(84日)遅延**で提供される。`config.yaml: jquants.data_delay_days`(既定85日
  =84日+1日マージン)で取得対象を `今日 - delay` までに制限し、state もそこまでしか進めない
  (でないと遅延データを永久に取りこぼす。`abb001c` で対応)。有償プランに上げたら 0 にしてよい。
- **レコード種別**: `DocType` に `"FinancialStatements"` を含むもののみ対象(業績予想修正・配当予想修正等は除外)。
  `CurPerType`(1Q/2Q/3Q/4Q/FY) → 四半期番号 n にマップ(`_PERIOD_TO_N`、5Q変則決算は対象外)。
- **値の抽出**: EPS = `EPS`(空なら `NCEPS`=非連結)。売上 = `Sales`(空なら `NCSales`)。値はカンマ除去して数値化、
  `-`/全角ダッシュは欠損扱い。
- **四半期ラベル**: `f"{fy_start[:4]}Q{n}"`。CurPerTypeがそのままYTD点の四半期番号になるので、
  EDINETのような期間月数からの逆算ロジックは不要(J-Quantsの方が単純で頑丈)。
- **YTD差分導出**: `derive_quarters` — 会計年度(`fy_start`)ごとにYTD点を昇順に並べ
  `値(Qn) = ytd(n) − ytd(直前の点)`。同一四半期の重複(訂正短信等)は開示日が新しい方を採用。
  年度をまたぐ差分はしない。**EDINET版と同一ロジックを踏襲**。
- **年度前半の再取得**: `_refetch_incomplete` — 日次インクリメンタルで2Q以降の点だけ拾った銘柄は、
  YTD差分の基準となる前Q点が無いため、その銘柄だけ `?code=` で全期間を取り直して整合させる。
- **永続化**:
  - `data/fundamentals_auto.json`: `{code: {"quarters":[{fiscal_quarter,eps,revenue}], "checked_date":"YYYY-MM-DD", "source":"jquants"}}`
    (checked_date = 最新開示日。銘柄あたり `max_quarters_keep`=12 四半期に切り詰め)。ストア機構自体は
    `fundamentals.load_auto_store`/`AUTO_PATH` としてEDINET時代から汎用化して流用している。
  - `data/jquants_state.json`: `{"last_list_date":"YYYY-MM-DD"}`
- **インクリメンタル**: 日次実行は `state.last_list_date+1` 〜 `end_day`(=今日−delay、上限 `lookback_days`=7)。
  全日失敗時(APIキー不正など)は state を進めない。
- **CLI**: `python -m src.data.jquants --backfill` (jquants-backfill.yml が呼ぶ。全銘柄を code 指定で1件ずつ取得、
  ~1000銘柄・60req/分で約17〜20分。50銘柄ごとに中間セーブ)
- **会社予想(ガイダンス)取得(2026-07-12追加)**: `record_to_guidance` — 同じ /fins/summary レスポンスから
  `FEPS`/`FSales`(当期通期予想)、`NxFEPS`/`NxFSales`(翌期予想、本決算短信に載る来期計画)、`ShOutFY`
  (期末発行済株式数)を抽出する。決算短信に加えて業績予想修正(`ForecastRevision`、ただし
  `Dividend`を含む配当予想修正は除外)も対象。銘柄ごとに開示日が最新の1件を
  `fundamentals_auto.json` の entry `"guidance"` に格納(`_apply_guidance`)。日次増分と `--backfill` の両方が
  対象なので、**既存ストアへの一括反映は jquants-backfill.yml を1回回すのが早い**。
  merge_fundamentals→write_public_json を透過し、pipeline が `summary.derive_guidance_view`
  (計画YoY・進捗率・予想PER算出、summary.py)で解釈してrecord `"guidance_view"` とサマリー文面に載せる。
- **決算発表予定日(2026-07-12追加)**: `update_earnings_calendar` — `GET /v2/equities/earnings-calendar`
  (Freeプラン可、**3月期・9月期決算企業のみ提供**という提供側制約あり)を日次1〜数reqで取得し、
  ユニバース銘柄の「今日以降で直近の予定日」を `data/earnings_calendar.json` に保存。record
  `"next_earnings_date"` → サマリー(14日以内なら発表跨ぎ注意caution、それより先ならpoint)と
  個別銘柄画面チップに出る。カレンダーに無い銘柄は従来の「前回開示から75日超」推定にフォールバック。

### マージ (src/data/fundamentals.py :: merge_fundamentals)
- `merge_fundamentals(auto_by_code, manual_by_code)` — 同一 (code, fiscal_quarter) は**手動CSVが勝ち**。
  monthly_yoy は手動のみ(自動には無い)。checked_date は手動優先、無ければ自動。
- pipeline.py での呼び出し: `fundamentals_by_code = merge_fundamentals(auto_by_code, build_fundamentals_by_code(csv_df))`
- 効果: J-Quantsデータが入った銘柄は quarters が存在する → ~~`fund_coverage_tier` が "confirmed" を返す → 〔本命〕へ自動昇格~~
  **(2026-07-09改定)** データ存在だけでは昇格せず、強度基準(EPS YoY≥+25%かつ売上YoY≥+20%)合格で初めて confirmed。

### フロントエンドへの公開 (docs/data/fundamentals_public.json) — 2026-07-07 追加
- **バグ**: 「ファンダ入力欄にバッチで取得したはずのデータが表示されない」と報告あり。原因は
  `data/fundamentals_auto.json`(J-Quants自動取得の生データ)が **docs/ の外**にあり GitHub Pages から
  配信されず、かつ `docs/assets/fundamentals-modal.js` のプリフィル処理が `manual/fundamentals.csv`
  (当時は空)しか読んでいなかったこと。バックフィル自体は成功していた(`54c09e9`で
  data/fundamentals_auto.json に997銘柄分の実データが投入済み・jquants_state.json も遅延日数分まで
  追いついている)ので、パイプライン側の不具合ではなくフロント側の配線漏れだった。
- **修正**: `src/data/fundamentals.py :: write_public_json(fundamentals_by_code, path=None)` を追加。
  マージ済み(自動+手動)データのうち quarters が存在する銘柄だけをトリムして
  `docs/data/fundamentals_public.json` に書き出す(`PUBLIC_JSON_PATH`)。pipeline.py で
  `merge_fundamentals` 直後に呼び出し(`src/pipeline.py`)。daily.yml は既存の `git add data/ docs/` で
  自動的にコミット対象になるためワークフロー変更は不要。
- `fundamentals-modal.js :: openFundamentalsModal` はモーダルを開くたびにこのJSONを取得し、
  **手動CSVに行が無い四半期だけ**バッチ値でプリフィルする(手動値は常に優先。`pickValue`ヘルパー、
  0とundefined/nullを区別)。バッチ由来で埋まった行には `.auto-prefill-row`
  (style.css: 薄い accent 背景 + 破線input)を付けて「未確定の自動取得値」であることを視覚的に示す。
  保存すると通常どおり manual/fundamentals.csv に確定値として書き込まれる。
- キャッシュバスター: `docs/index.html` の `fundamentals-modal.js` を `?v=6` に更新(style.cssは既に`?v=7`)。
- テスト: `tests/test_fundamentals.py`(write_public_jsonの空quarters除外・手動優先)、
  `tests/test_pipeline.py` の `wired` フィクスチャに `write_public_json` のno-opモンキーパッチを追加
  (これが無いと pytest 実行のたびに実リポジトリの docs/data/fundamentals_public.json が空dictで
  上書きされてしまう — docs/data/indices.json の既知の汚染問題と同種)。

### 制約(ユーザー了解済み)
- Freeプランは12週間遅延のため、直近四半期の開示が〔本命〕に反映されるのは数ヶ月遅れる。
- EPSのYTD差分は株式数変動時に厳密でない(許容)。
- 年度途中からの取得だと最初の点がYTDそのままになる → **バックフィル(全期間)実行が前提**。

## 5. EDINET DB 決算短信補完 (src/data/edinetdb.py)

### 経緯
- J-Quants Freeプランは12週間(84日)遅延するため、直近四半期の開示が〔本命〕へ反映されるまで
  数ヶ月のタイムラグがある。IRBANKスクレイピングでの補完も検討したが規約違反のためクローズ済み。
- EDINET DB (`https://edinetdb.jp`) の `get_earnings` は決算短信ベースで開示後ほぼ即日〜翌日に
  取得できるため、**「案A: 補完バックアップ」**として採用 (`DESIGN_EDINETDB.md` 参照)。
  J-Quantsを置き換えるのではなく、その遅延窓の直近四半期だけを補って昇格を早める役割。

### 仕組み
- **API**: `GET {api_url}/companies`(証券コード↔EDINETコード対応表)、
  `GET {api_url}/events?event_type=earnings_summary`(当日決算短信の開示イベント一覧、backlog検出用)、
  `GET {api_url}/companies/{edinet_code}/earnings`(銘柄別の決算短信サマリー、YTD値)。
  認証はヘッダ `X-API-Key: <EDINETDB_API_KEY>`。
- **APIキー**: 環境変数 `EDINETDB_API_KEY`。**キーが無い場合はネットワークに一切触れず既存ストアを返すだけ**
  (J-Quantsと同じフェイルセーフ設計)。`config.yaml: edinetdb.enabled` が `false` の間はキーの有無に関わらず
  既存ストアを返すだけで完全にスキップされる(APIキー登録+実地確認完了までの安全弁)。
- **Free枠バックログ設計**: アカウント単位100req/日(`requests_per_day`で90に制限し10残す)。
  全銘柄を毎日ポーリングすると枠が枯渇するため、
  1. `data/edinetdb_state.json` に証券コード→EDINETコードの「codemap」を保持(`codemap_refresh_days`=30日ごとに再取得)。
  2. 日次実行のたびに `/events` で当日の決算短信開示イベントを取得し、対象コードを `backlog` キューに追加。
  3. `backlog` を予算(`requests_per_day` − 消費済み)の範囲で先頭から消費して `/earnings` を取得。
     予算切れで残ったコードは翌日以降に持ち越す(`test_update_budget_exceeded_leaves_backlog`)。
  4. codemapに無いコード(新規上場等)はbacklogに留め置き、次回codemap更新後に再試行する
     (`test_update_code_missing_from_codemap_stays_in_backlog`)。
  5. `/events` が全滅した日は `last_events_date` を進めない(取りこぼし防止、
     `test_update_events_all_fail_does_not_advance_last_events_date`)。
- **backlog優先順位付け(2026-07-08追加)**: `update_fundamentals_auto(..., priority_by_code=None)`。
  backlog消化(上記3)の直前で `priority_by_code: dict[code, rank]`(rank=P1=1〜P4=4、
  未指定コードは99)の昇順にbacklog全体を並べ替える(`list.sort()`の安定性を利用し、
  同ランク内の相対順序=検出順は維持)。二値(優先/非優先)ではなくランクそのもので
  比較するため、P1銘柄が無くてもP2→P3→P4の順で優先される。budget自体は変えないため、
  優先銘柄がその日の予算に収まる保証はないが、同じ予算内での消化順を変える。
  呼び出し元は `pipeline.py` — 機能A(P1〜P4)のプライオリティ評価 `priority_by_code`
  (トレンドテンプレート直後、EDINET DB呼び出しより前に確定)から
  `priority_rank_by_code = {code: ev["priority"] for code, ev in priority_by_code.items()}`
  を作って渡す。P1〜P4ランクは技術指標(価格・出来高等)のみで決まりファンダメンタル
  取得結果に依存しないため、鶏と卵問題は発生しない(前回report.jsonを参照する迂回策は
  不要 — 当初はfund_coverage由来の〔候補〕tierで実装し、その回避策として前回report.json
  を使っていたが、ユーザーの意図はP1〜P4ランク順だったため設計変更)。テスト:
  `test_update_priority_by_code_reorders_backlog_by_rank`,
  `test_update_priority_by_code_unlisted_codes_sort_last`,
  `test_update_priority_by_code_none_leaves_backlog_order_unchanged`(edinetdb.py側)、
  `test_run_daily_passes_priority_rank_to_edinetdb`(pipeline.py側)。
- **リペアCLI `--requeue-stale`(2026-07-12追加)**: `python -m src.data.edinetdb --requeue-stale
  [--stale-days N]`。backlog消化は「取得できたが1件も採用できなかった」銘柄も消費済みとして
  落とす(無限リトライ防止)ため、パース不具合の期間に消化された銘柄は成果ゼロのまま二度と
  再取得されない穴になる — 2026-07-08の初回稼働時に約450銘柄がこれに該当し、5月開示の
  本決算(2025Q4)がJ-Quants 85日遅延窓にもEDINET DBにも入らない状態が発生した。
  `requeue_stale` は J-Quants+EDINET DB両ストアの最新 `checked_date` が閾値(省略時
  `fundamentals.stale_days`=120日)より古い銘柄をbacklogへ再投入する(ネットワーク不使用・
  実取得は以後の日次runが90req/日ずつ優先度順に消化)。テスト: `test_requeue_stale_*` 4件。
- **YTD差分の四半期化**: J-Quants(`fundamentals_auto.json`)を `base_store` として渡し、直前四半期までの
  YTD値との差分から単四半期値を導出する(`derive_with_base`)。Q1はYTDそのまま。前四半期が
  base側に無い/欠損している場合はそのレコードごと破棄する(半端な値を出さない)。
- **値の変換**: `record_to_point` — 四半期ラベル(`Q1`〜`Q4` → `n`)と開示日から
  `{fy_start}Q{n}` を組み立て、revenue は百万円→円に `×1,000,000` 変換(EPSはそのまま)。
  fiscal_year_start が API レスポンスに無い場合は決算月+開示日+四半期番号から数学的に推定
  (`_estimate_fy_start`。両方失敗したらそのレコードは破棄)。
- **永続化**:
  - `data/edinetdb_auto.json`: `{code: {"quarters":[...], "checked_date":"YYYY-MM-DD", "source":"edinetdb"}}`
    (`fundamentals.load_auto_store`と同形式。銘柄あたり `max_quarters_keep`=12 四半期に切り詰め)。
  - `data/edinetdb_state.json`: `{"codemap":{...}, "codemap_date":..., "last_events_date":..., "backlog":[...]}`

### マージ (src/data/fundamentals.py :: merge_fundamentals) — 3ソース拡張
- `merge_fundamentals(auto_by_code, manual_by_code, tanshin_by_code=None)` — 優先度は
  **manual > auto(jquants) > tanshin(edinetdb)**。同一 (code, fiscal_quarter) は投入順
  (tanshin → auto → manual)の後勝ちで実現。J-Quantsの遅延が追いつけば同ラベルを自動的に
  上書きするため、tanshin値は暫定速報の扱いになる。`tanshin_by_code` はキーワード引数・既定 `None` で
  後方互換を維持(既存呼び出しはそのまま動く)。
  monthly_yoy は従来どおり手動のみ。checked_date は manual があれば manual、無ければ auto と
  tanshin の新しい方(ISO日付文字列の辞書順比較)。
- pipeline.py での呼び出し: J-Quantsブロックの直後に
  `tanshin_by_code = edinetdb_mod.update_fundamentals_auto(codes, config, base_store=auto_by_code,
  priority_by_code=priority_rank_by_code)`(try/exceptで失敗を無視、`priority_rank_by_code`の
  由来は上記「backlog優先順位付け」参照)を追加し、
  `merge_fundamentals(auto_by_code, build_fundamentals_by_code(csv_df), tanshin_by_code=tanshin_by_code)` に変更。
- 効果: EDINET DBで直近四半期が入った銘柄も quarters が非空になり、強度基準の判定材料
  (直近EPS/売上YoY)が最大12週早く揃う(2026-07-09以降、confirmed昇格には強度基準合格も必要)。

### 実地確認について
- サンドボックス環境からは `edinetdb.jp` へのネットワークアクセスがブロックされており
  (`irbank.net`と同様、許可リスト外)、`DESIGN_EDINETDB.md` 1節が求めるAPI実地確認(curl検証)は
  実装セッション(2026-07-08)では実行できなかった。ユーザーの判断で**検証をスキップし、防御的な
  実装で進める**方針を採用(fy_start推定ロジック・revenue単位変換・フィールド名の一部は未検証)。
- **2026-07-08、ユーザーの指示で `config.yaml: edinetdb.enabled` を `true` に切替**。ローカルでの
  実地確認結果はこのドキュメントには反映されていない(Claude側では未確認)。daily.yml 実行後に
  `data/edinetdb_auto.json` の値(特にrevenue単位・fiscal_quarterラベル)が決算短信の実際の数値と
  一致しているか、初回実行分は必ず目視確認すること。差異があれば `record_to_point` /
  `_estimate_fy_start` の修正が必要になる可能性がある。
- **2026-07-08(バグ修正): `fetch_events()`/`fetch_companies_map()`/`fetch_earnings()` がAPIレスポンスの
  トップレベルキー名を `body.get("data") or body.get("events") or []` のように決め打ちで推測していたため、
  実際のキー名が違う場合に**エラーも出さず無言で空リストを返す**バグがあった。有効化後の初回daily実行で
  `EDINET DB: 0 codes processed, 0 left in backlog.` のみが出力され、他の診断出力が一切無いログから発覚。
  対策として `_extract_list_of_dicts(body, known_keys, context)` ヘルパーを追加し、
  (1) known_keysで見つからなければ「値がlist[dict]である最初のトップレベルキー」を自動検出して使い、
  (2) それも無ければ `EDINET DB: {context} response had no list field at all; top-level keys: [...]` の形で
  実際のトップレベルキー一覧をprintするようにした。`fetch_companies_map`/`fetch_events`/`fetch_earnings`と
  `update_fundamentals_auto`のeventsループを全てこのヘルパー経由に書き換え済み(`tests/test_edinetdb.py`に
  fallback/自動検出/全滅ケースのテスト6件追加、既存152件+新規6件=158件全パス)。
  併せて `data/edinetdb_state.json` を `{}` にリセット(`last_events_date` が誤って2026-07-08まで
  進んでいたため、直すと2026-07-09以降しか再スキャンされず1〜7月分の開示を恒久的に取りこぼす所だった)。
  `.github/workflows/daily.yml` の「当日実行済みチェック」ガードは**動作確認のため一時的に無効化**
  (常に`skip=false`)。確認が済んだら元のgit log判定に戻すこと。
  なお、このヘルパーは「キー名の推測ミス」は救えるが、**行内の個別フィールド名の推測ミス**
  (`_COMPANY_CODE_KEYS`/`_EVENT_CODE_KEYS`/`_FY_START_FIELD_CANDIDATES`)までは救えない。
  もし次回実行でも0件が続くなら、追加された診断printで実際のフィールド名を確認し、該当の
  `_*_KEYS`/`_FY_START_FIELD_CANDIDATES` 定数を実地確認1〜5に沿って更新すること。
- **2026-07-08(バグ修正3件目・解決済み): ネスト抽出fix後も`data/edinetdb_auto.json`が0件のまま**。
  `/earnings`のリスト取り出し自体(`data.earnings`)は直ったが、その先の
  `record_to_point()`が見ている`quarter`/`eps`/`revenue`/`disclosure_date`/
  `_FY_START_FIELD_CANDIDATES`は全て実地未検証の推測フィールド名で、実レコードと噛み合っていない
  可能性が高い(黙ってNoneを返すだけでエラーにならないため発覚しにくい)。加えて
  `update_fundamentals_auto`内の`record_to_point(rec, code)`呼び出しに`fiscal_year_end_month`を
  渡していないため、`_estimate_fy_start`フォールバックも機能しない。この結果、
  `docs/data/fundamentals_public.json`にEDINET DB分が一切載らず、ダッシュボードの
  「ファンダ入力/編集」モーダルにも表示されない状態が続いていた。**対策として、backlogループで
  レコードは取れたのに0件しか採用できなかった場合にサンプルレコードの生データをprintする診断を
  追加**(次回実行で実際のフィールド名が判明する見込み)。フィールド名が判明したら
  `record_to_point`/`_QUARTER_TO_N`/`_FY_START_FIELD_CANDIDATES`の実データ対応がまだ必要。
  - **診断print、第4弾(2行分割)〜第5弾(1フィールド1行)**: 第3弾の1行dump診断を本番実行したところ
    発火はしたが、`sample record: {...}`の1行がGitHub Actionsのログ表示/コピペで長すぎて途中
    (`disclosure_date`付近)で切れ、知りたかった`quarter`/`eps`/`revenue`相当のフィールドが
    見えなかった。第4弾として「全キー一覧のみの行」+「候補キーワードで絞った値付きの行」の
    2行に分割したが、これも両方とも途中(キー一覧は`'inte...`、候補フィールドは
    `'forecast_operating_inco...`)で切れた — GitHub Actionsのログ行長制限は68キー分の
    リストやdict reprでもまだ超える水準らしい。この2回の切断までに確認できた実フィールド:
    `eps`(バレキーで存在、値805.05)、`fiscal_year_end`(FY末日、例`'2026-09-30'` —
    `_FY_START_FIELD_CANDIDATES`のどれとも不一致)、`disclosure_date`はRFC2822形式
    (`'Thu, 14 May 2026 00:00:00 GMT'`、ISO形式ではない)。`quarter`相当のフィールドは
    アルファベット順で`inte...`より後ろにあるはずだが未確認。第5弾として、診断printを
    「1フィールド1行」方式(`for k in sorted(sample.keys()): print(f"... {k!r} = {v!r}")`)に
    変更 — レコードの総フィールド数に関わらず各行は短くなるため、途中で打ち切られても
    それまでの行は残る。次回daily.yml実行で今度こそ68フィールド全部の確認が取れる見込み。
  - **第6弾(解決): 68フィールド全件の実データ取得に成功、`record_to_point`を本修正**。
    確定した実スキーマ: `quarter`は整数1〜4(FY相当=4。文字列"Q1"等ではない)、
    `fiscal_year_start`相当のフィールドは存在せず`fiscal_year_end`(期末日、例
    `'2026-03-31'`)のみ存在、`disclosure_date`はRFC2822形式。`eps`/`revenue`は
    バレキーで存在し、revenue=513286(百万円単位、×1,000,000換算)は決算短信の
    通期売上と整合 — 単位換算の推測は正しかった。
    `_resolve_quarter_n()`(整数優先・文字列後方互換)、`_fy_start_from_fy_end()`
    (期末日の1年前+1日を算出)、`_parse_disclosure_date()`(RFC2822→ISO正規化)を
    新設し`record_to_point`を書き換え。`_resolve_fy_start`の優先順位は
    ①`fiscal_year_end`系(新規最優先) → ②`fiscal_year_start`系(未確認だが保険で維持)
    → ③開示日からの推定(最終フォールバック)。調査用の全フィールドダンプ診断printは
    撤去し、軽量なキー一覧のみの診断に戻した。`tests/test_edinetdb.py`に実スキーマ
    ベースのテスト7件追加、フルスイート169件全パス。**未確認**: 次回daily.yml実行で
    `data/edinetdb_auto.json`に実際にデータが入るか、ダッシュボードの数値が決算短信と
    一致するかの本番目視確認がまだ残っている。
- **2026-07-08(バグ修正2件目): `/companies`/`/events`は上記対応で本番動作確認済み(3830社中3829社
  マッチ、997銘柄backlog投入)だったが、`/companies/{edinet_code}/earnings`だけ複数銘柄で
  「no list field at all; top-level keys: ['data', 'meta']」が続いていた。1回目の対応として
  「known_keysのdict値を単一レコードとして`[dict]`でラップする」フォールバックを追加・pushしたが、
  これは誤診断だった: 実際のログで確認したところ、ラップされた「レコード」のキーが
  `count`/`earnings`/`edinet_code`であり、`/earnings`の実際の形は
  `{"data": {"count": N, "edinet_code": "...", "earnings": [...]}, "meta": {...}}` という
  **ラッパーdict**で、本当のレコード配列はさらに1階層下の `data.earnings` にあった。
  `record_to_point()`は`rec.get("quarter")`を見るため、ラッパーdictをそのままレコード扱いすると
  quarterキーが存在せず黙って`None`(取りこぼし)になる — エラーにならないため発覚が遅れた。
  対策として `_extract_list_of_dicts()` に新しいフォールバック階層を追加: 「単一レコードとして
  ラップする」より前に、known_keysの値がdictであればその中身も known_keys→自動検出の順で
  再探索し、ネストしたlistが見つかればそれを返す(例: `data.earnings`)。ネストが見つからない
  場合のみ、従来通り「単一レコードとしてラップ」にフォールバックする(真にフラットな1件だけの
  レスポンス用の最終防衛線として維持)。`tests/test_edinetdb.py`に実際の`/earnings`形状を再現した
  テストを2件追加、フルスイート162件全パス確認済み。

### 制約(ユーザー了解済み)
- EDINET DBの決算短信データは2026-01-01以降のみ存在(バックフィル不可)。
- Free枠100req/日のため、当日中に全対象コードを消化できないことがある(backlogで翌日以降に持ち越し)。

## 6. config.yaml の要点

- `universe`: size 1000, jpx_list_url (東証上場一覧xls), shares_sleep_sec 0.5
- `data`: history_days 520, chunk_size 50, sleep_range [2,4], max_fail_ratio 0.10, topix_proxy_ticker "1306.T"
- `trend_template`: rs_min 70, score_weights {technical:55, eps_accel:25, rev_accel:10, monthly:10}
  (2026-07-29改定: 技術面の内訳ウェイト rs/ma200_days/near_high は廃止。tech_score は
  自由パラメータを持たない断面ランクになった。下記「tech_score」節を参照)
- `priority`: high_dist_mid_pct 25, high_dist_bad_pct 35, rs_soft_min 60, p1_warn_threshold 3
- `heatmap`: periods [1,5,20,60], strength/direction窓と閾値
- `vcp`: zigzag/収縮/スコアの全パラメータ
- `entry`: breakout_vol_mult 1.4, stop_loss_pct 0.05, tick_table (JPX呼値簡易版)
- `fundamentals`: min_quarters_for_full 7, stale_days 120
- `jquants`: enabled true, api_url, data_delay_days 85(Free 12週遅延+マージン), lookback_days 7, sleep_sec 1.1, max_quarters_keep 12
- `edinetdb`: enabled **true**(2026-07-08〜), api_url, requests_per_day 90, earnings_limit 8, codemap_refresh_days 30, max_quarters_keep 12
- `scoring`: phase1_weight 0.5, vcp_weight 0.5

## 7. フロントエンド詳細 (docs/assets/app.js)

index.html 1ファイルのみが担当 (末尾で initDashboard / initRouter を起動。initStockPage はrouterのshowViewから
"stock/CODE" hashの時だけ呼ばれる)。stock.html は2026-07-08にリダイレクトスタブ化され、スクリプトを持たない。

### SPA構造 (2026-07-08〜、2026-07-11にview-positions追加)
- index.html は6つの `<section class="view-section" id="view-{dashboard|sectormap|invest|positions|batch|stock}">` を持つ1ページ。
  初期状態は view-dashboard のみ表示、他は `hidden`。
- 下部固定 `<nav class="dock-nav" id="dock-nav">` にmacOS Dock風の5ボタン(`.dock-btn[data-view=...]`、
  dashboard/sectormap/invest/positions/batch)。`view-stock` はドリルダウン専用でDockには出さない
  (ダッシュボード表・保有ビューの行クリックからのみ遷移)。
- ルーター (app.js): `showView(hash)` が `hash.split("/")` で `[name, param]` に分解し、対象セクションの
  `hidden` を切り替え、`.dock-btn.active` を付け替える(`name` がVIEWSに無ければ"dashboard"扱い)。
  `initRouter()` が dock ボタンの click → `location.hash` 変更、`hashchange` → `showView` の配線と、
  初期表示(hashが無ければ "dashboard")を行う。
  - `view-sectormap` を開くたび `initHeatmap()` を再実行(非表示中は `clientWidth` が0でツリーマップが
    壊れるため、表示された瞬間に再計測させる設計。heatmap.js側は `hmWired` フラグで一度きりの
    イベント登録だけガードし、render自体は毎回実行)。
  - `view-batch` を開くたび `window.MinerviniBatch.initBatchView()` を実行(カード自体は初回のみ生成、
    実行履歴は毎回再取得して最新化)。
  - `view-stock` (hash = `stock/CODE`) を開くたび `initStockPage(param)` を実行。ダッシュボード表の行クリックは
    `window.location.hash = "stock/" + code` (旧: `stock.html?code=...` への遷移)。
- 旧 `heatmap.html` は `index.html#sectormap` への meta-refresh リダイレクトスタブのみ残存(旧URL互換)。
  旧 `stock.html?code=X` は同様にJSで `index.html#stock/X` へリダイレクトするスタブ(?codeをhashへ引き継ぐ
  必要があるためmeta refreshではなくJS実装)。
- `showView(hash)` の先頭で `window.scrollTo(0, 0)` を実行(2026-07-08追加)。SPA化により前ビューの
  スクロール位置がそのまま残ってしまう(ページ遷移してもブラウザが自動で先頭に戻さない)問題への対応。
- **縦スクロールコンテナには `overflow-x: hidden` を明示すること (2026-07-30)**。CSSの規定で
  `overflow-y` を auto/scroll にすると `overflow-x: visible` は `auto` に化けるため、
  「縦だけスクロールさせたい」つもりの指定が、中身が1pxはみ出した瞬間に横スクロール
  (画面が指で横に揺れる)を生む。リスト/保有/設定で実際に起きた。現在
  `.view-section` / `.bottom-sheet` / `.batch-view .settings-subpanels` /
  `#stock-list-body` に明示済み。
  - **横スクロールが意図的な要素は対象外**: `.table-scroll` / `.invest-table-wrap` /
    ヒートマップ / 個別株・市況の横スワイプパネル。
  - 塞ぐのに `touch-action: pan-y` は使わないこと。横スワイプ操作を殺す。

### ダッシュボード (view-dashboard)
- `initDashboard`: report.json / breadth.json / indices.json を `cache: "no-store"` でfetch → 各render

**一覧は単一リスト (2026-07-29改定)**。本命/候補プール/監視の3タブ・3ティアbody・
`#cooled-tier-body` はすべて廃止し、描画先は `#stock-list-body` ただ1つ。実現の根拠は
`combined_score` がVCPスコア欠損を0扱いに変えたこと(上記「ティアとフロント表示の対応」節)。
経緯は log.md (143)。

- `visibleListStocks(report)` → `{stocks, excluded}`: **並び順を決める唯一の場所**。
  `applyListFilter` で絞ってから `CARD_SORTS[getCardSortKey()]` で降順ソート、同点は
  `STATUS_ORDER` の並び → コード昇順で割る。
  - `renderStockList` と `orderedListCodes`(個別画面の前後スワイプ順)が**同じ関数を呼ぶ**ので、
    画面上の並びとスワイプ順が食い違うことがない。分けて実装しないこと。
- `renderStockList(report)`: `#stock-list-body` をクリア → フィルタ注記 → `renderCardList(stocks)`。
  0件時は「該当銘柄なし」/(フィルタで消えた場合)「フィルタ条件に合う銘柄なし」。
  `updateListSummary` が `#list-summary` に `N件 (M件除外)` を出す。**この要素は
  ツールバーではなくページヘッダ右上** (`.page-header-count`) にある (2026-07-30移動)。
  出し分けは `showView` の担当で、`updateListSummary` は中身を書くだけ。
- **ページヘッダ (`.page-header`) は全ビューで常時表示** (2026-07-30改定。以前は
  ダッシュボードのみ)。縦領域を稼ぐより「現在地の手掛かり」を優先した結果。
  嵩む要素だけ `showView` がビュー名で出し分ける: 最終更新行 `#generated-at` は
  dashboard のみ、件数 `#list-summary` は stocklist のみ。
- `renderCardList(stocks)`: **呼び出し側が並べ替え済みの前提**で、内部ソートはしない。
  カードは3段固定 (2026-07-30に作り直し。経緯は log.md (146))。
  **設計原則は「1つの行で伸縮するのは1要素だけ」**:
  1. `.sc-row-main` = `.sc-name`(伸縮・省略) + `.sc-chips`(SC/RS、`flex:0 0 auto`)
  2. `.sc-row-sub` = `.sc-flags`(状態/F不/買重) + `.sc-close`(終値・前日比) …
     右端に `.sc-dryup`(`margin-left:auto`)。**`flex-wrap: nowrap`**
  3. `.sc-row-meta` = 業種(強度) ・ 進行度/不足理由。**1行に省略**(全文は title 属性)
  - 旧構成(1段目に名前もバッジもSC/RSも全部積む)は実機iPhone(幅390px)で
    RSチップが右端で切れていた。**可変長(銘柄名)と固定幅(チップ)を同じ行で
    競わせないこと**。2段目を wrap させると DU の有無で右端位置が行ごとに動き、
    3段目を折り返させると「未達: A / B / C / D」が3行に伸びてカードを支配する。
  - バッジ = `statusBadgeHtml(s)` + `fundVerdictBadgeHtml(s)` + `marginBadgeHtml(s.margin)`。
  - `statusBadgeHtml`: `STATUS_BADGE` に一致すれば〔ブレイク〕〔ブレイク弱〕〔待機A/B〕〔追禁〕。
    無ければ `setupStageGroupKey(s)` から〔あと一歩〕/〔形成中〕。
  - `s.tier === "cooled"` のカードに `.sc-cooled`(opacity 0.55、hover/focusで1.0)を付ける。
    **スコアは減点しない**(理由は「ティアとフロント表示の対応」節)。
  - チャートJSON未生成(`has_chart === false`)はクリック不可。それ以外は
    `window.location.hash = "stock/" + code` で view-stock へ遷移。
  - 枯れ度は `dryupBadgeHtml` が `DU 0.50` 形式で出す (2026-07-30改定。旧:〔激枯れ/枯れ気味〕)。
    強弱は文字ではなく背景の濃さ (`-extreme` > `-dry` > `-flat`=無彩色) で表す。
    **値が無い銘柄は空文字を返す**(`"-"` を出さない)。`.sc-dryup:empty { display:none }`
    で要素ごと消えるので gap の余白も残らない。
- 並び替えキーは `CARD_SORTS`(`total_score` / `rs` / `change_pct` / `dryup`)、既定 `total_score`。
  localStorage `minervini-card-sort` に**ティア別ではなく単一キー `list` で**保存する。
  - **`dryup` だけ符号を反転している**。他は「大きいほど良い」で降順に並べるが、
    `dryup.value` は生値(小さいほど枯れている=良い)。tech_score 内の dryup 成分は
    `score_variables` の時点で反転済みだが report.json の値は生のままなので、
    フロント側で `-value` にして向きを揃える。
- `rerenderStockList()`: フィルタ/並び替え変更時に `reportCache` から引き直して再描画。
- **一覧フィルタは2層 (恒久 + アドホック、AND結合)**: 恒久=設定画面、localStorage
  `minervini_list_filters`。アドホック=ボトムシート、メモリ上の `adhocListFilter`(リロードで消える)。
  両方を `stockPassesListFilter` に通し、`applyListFilter` が `{kept, excluded}` を返す。
  - **表示ステータス (`showStatuses`) はアドホック側だけが持つ** (2026-07-27追加)。既定は
    `defaultShownStatuses()` = `FILTER_STATUS_ORDER` から `LIST_FILTER_DEFAULT_HIDDEN_STATUSES`
    (= EXTENDED / STALE = 追禁、および FORMING = 形成中) を除いたもの。チェックボックスは
    `initAdhocFilter` が `FILTER_STATUS_ORDER` から動的生成する(`#alf-show-statuses`)ので、
    ステータスを増やしてもHTML修正不要。
  - **`FORMING`(形成中)は擬似ステータス** (2026-07-30追加)。実体は `status` ではなく
    `setup_stage` 由来のカードバッジなので `STATUS_ORDER` には無く、**フィルタUI上だけ**
    同列に並べる。判定は `statusVisible(s)`: `cardBadgeIsForming(s)` が真なら素の
    `status`(REJECTED/IMMATURE/…)ではなく `FORMING` チップだけを見る。
    - **なぜ専用トグルではなく擬似ステータスなのか**: 形成中も追禁も利用者から見れば
      同じ「毎朝まず消したい塊」で、消し方が2箇所に分かれていると管理が破綻する。
      1つの `showStatuses` に寄せれば既定値も差分判定も1箇所で済む。
    - `cardBadgeIsForming` は **`statusBadgeHtml` と同じ判定順で書くこと**。ズレると
      チップに「形成中」と書いてあるのにカードが消えない、が起きる。
  - 実装上の注意2点: (a) ステータス絞り込みは他フィルタが未設定でも効く必要があるため、
    `applyListFilter` の早期returnより**前**で filter する。(b) 除外件数 `excluded` には
    **含めない**(含めると「フィルタでN件を除外中」バナーが常時点灯する)。同じ理由で
    `statusFilterCustom()` は全ステータスではなく**既定セット**との差分でカスタム判定する。
- **フィルタのボタンは1つ (`#list-filter-btn` → `#filter-sheet`) だけ**。
  2026-07-30に「かんたんフィルタ」を足したが同日中に撤去した。ユーザーの結論は
  **「トグルを増やすより既定を正しくしろ」**で、既定で追禁と形成中を隠し、
  出したいときだけ詳細フィルタのステータスチップから戻す形に落ち着いた。
  同種の提案をするときはこの経緯を先に読むこと。log.md (145)(146)。
- `renderP1Warning`: report.p1_scarce で警告バナー (#p1-warning)
- `renderMarketSignal(breadth)` (2026-07-11追加、2026-07-18に地合い詳細パネル+
  market_score/score_trendバッジ+スパークライン2本を追加): breadth.jsonのhistory
  最新エントリの`signal`(green/yellow/red)を`#market-signal-card`(market-overviewの
  直前)へ描画。詳細はデータ生成側の「地合いシグナル」節(§3内)参照。
- `renderStalenessWarning(report)` (2026-07-11追加): `getStalenessInfo(generatedAt, now)`が
  直近の平日(土日はFriday扱い)21:00 JSTを過ぎてもその日のデータが無い場合を判定し、
  `#staleness-warning`(market-overviewの直前)に警告文言を表示。JSTシフト時計トリック
  (`now.getTime()+9h`をUTC getterで読む)でブラウザのローカルタイムゾーンに依存せず判定。
  祝日は考慮しない(文言に「祝日明けは誤検知の場合あり」と明記)。過去のdaily.ymlサイレント
  失敗事故(1週間ダッシュボード未更新)を受けた対策。
- `renderBreadth`: テンプレ通過率 / セットアップ数 / ブレイク成功率 / 候補(8条件合格)件数
- `renderMarketOverview`: indices.json → カード + SVGスパークライン
- **スパークラインの軸 (2026-07-27)**: `sparklineSvg(series, isUp, {format, xLeft, xRight})` は
  `preserveAspectRatio="none"` で横に伸ばす都合上、SVG内にテキストを置くと文字が潰れる。
  そこで**軸ラベルはSVGの外にHTMLで出し**、`.spark-box` のCSSグリッドで「左=縦軸 / 下=横軸」に
  配置する(線は `vector-effect="non-scaling-stroke"`)。縦軸ラベルは max / 中央値 / min の3点。
  同じ `.spark-box` 一式を `heatmap.js` の `histSparkline(values, colorByLast, dates)`
  (セクター履歴ポップアップ、高さ64pxなので `.spark-box-lg`) でも流用している。
  新しくスパークラインを足すときはこの2関数のどちらかを使うこと(独自実装を増やさない)。
- `startLiveIndices`: 指数カードの擬似リアルタイム更新。60秒間隔で indices.json を再fetch (`cache: "no-store"`) して
  `renderMarketOverview` を再実行するだけ(ページリロード不要)。バックグラウンドタブ (`document.hidden`) では止める。
  データ自体の更新頻度は intraday-indices.yml 側の15分間隔が上限 (静的サイトなのでティック単位の真のリアルタイムではない)。
- WebAuthn/書き込み系: `passkeyAuthEnabled` (config.js) のキルスイッチ。**2026-07-12に有効化済み**。
  対象は `.passkey-gated` クラスを持つ全要素(旧: id固定リストの `hidePasskeyAuthUi`。ヘッダーのボタンに加え
  view-batch の実行カードも同クラスで一括隠蔽できるよう、2026-07-08にクラスベースへ変更)。
  有効化すると: 解錠ボタン(WebAuthn PRF→PAT復号)→ファンダ入力モーダル/バッチ実行ボタンが活性化。

### アクセス制御: データ暗号化 + 起動時パスキーゲート (2026-07-12追加)
- **動機**: 公開リポジトリ+Pagesでは画面をロックしてもJSONのURL直アクセスで中身が見える。
  そこで docs/data/*.json 自体を暗号化し、「アクセス=復号鍵の所持」にした。
- **書き出し側** (`src/report/secure_io.py`): env `DASHBOARD_DATA_KEY`(GitHub Secret、base64の32バイト)が
  あると `write_docs_json` が AES-256-GCM封筒 `{"__enc__":"aesgcm-v1","iv","ct"}` で書く。**鍵未設定なら平文**
  (ローカル開発・テストは従来どおり)。対象: report/breadth/charts/fundamentals_public/heatmap/indices/positions。
  読み戻し(load_breadth)は `read_docs_json`(封筒なら復号、鍵無しで封筒に当たると明示エラー)。
  リカバリCLI: `python -m src.report.secure_io --decrypt <file>` / `--encrypt <file>`(鍵はenv)。
  ワークフロー: daily.yml / intraday-indices.yml が Secrets から `DASHBOARD_DATA_KEY` を渡す。
- **読み出し側** (`docs/assets/secure-fetch.js` = `window.MinerviniData`): `fetchJson(path,{optional})` が
  封筒/平文を自動判別して復号。データ鍵はメモリのみ。`setDataKey()` 成功時に `minervini-unlocked`
  イベントを発火(起動ゲートを閉じる合図)。**data/*.json のfetchは必ずこれを通すこと**(素のfetchだと
  暗号化時に壊れる)。
- **起動ゲート** (`app.js ensureDataAccess` + index.html `#lock-screen`): report.jsonが封筒なら全ビュー
  初期化前にロック画面を表示。「タップして解錠」→ vault解錠(PRF)→ dataKey注入 → ゲート解除+書き込み系も
  同時解錠。平文なら素通し(=暗号化を有効にした時点でゲートが自動的に立ち上がる。ロールバックも
  Secretを消して1回再生成するだけ)。
- **なぜ自動でパスキーを要求できないか (2026-07-27)**: `navigator.credentials.get()` は
  **ユーザーアクティベーション必須**の仕様で、ページロード時に自動で Face ID を出すことは
  どのブラウザでもできない。次善策として解錠処理 `doUnlock` をロック画面**全体**の
  click / Enter / Space に紐づけ、「どこをタップしても解錠」にしてある(`busy` で再入ガード)。
  なお同一セッション内のリロードは `restoreDataKey()` が効くため元々パスキー不要。
- **保管庫v2** (`webauthn-vault.js`): 平文ペイロードが `{"pat","dataKey"}` のJSONに(v1=生PAT文字列も
  解錠のみ後方互換)。セットアップモーダル(fundamentals-modal.js)にデータ鍵入力欄を追加。
  データ鍵は GitHub Secret `DASHBOARD_DATA_KEY` と同じ値を入れる。
- **既知の限界**: リポジトリ自体が公開のため、ソース・log.md・manual/positions.csv(保有ポジション!)・
  data/配下の中間データは暗号化対象外で公開のまま。**本気で隠すならリポジトリをprivateにするのが正道**
  (Pagesは動き続けるが、github-api.jsの未認証 listWorkflowRuns はPAT必須になる)。

### ローカルで生成物を読む (tools/pull_site_data.py、2026-07-29追加)
- **動機**: 上の暗号化は配信物を守るためのもので維持したいが、手元で中身を見たい
  (目視確認・集計スクリプト・Claudeに分析させる) ときは封筒のままだと何もできない。
  「配信物は暗号化のまま、ローカルの作業コピーだけ平文」に分ける。
- `python tools/pull_site_data.py` で origin/gh-pages の `data/` を
  `git fetch --depth=1` + `git archive` で一時ディレクトリへ取り出し、復号して
  `data/plain/` へ書く。**作業ツリーの `docs/data/` は触らない**
  (パイプラインの読み戻し対象を勝手に書き換えないため)。
- 鍵は env `DASHBOARD_DATA_KEY` → 無ければ `.secrets/data_key` の順。
  どちらも無ければ手順を出して終了する。値は GitHub Secret / パスキー保管庫の
  dataKey と同じもの。
- オプション: `--charts` (charts/*.json も、約15MB)、`--all` (`*_maezyou.json` 等も
  含めて直下の全JSON)、`--source local` (fetchせず手元の `docs/data/` を復号)。
- 平文/封筒は自動判別するので、Secretを消して平文運用に戻しても同じコマンドで動く。
- `.secrets/` と `data/plain/` は .gitignore 済み。**公開リポジトリなので、
  この2つを追跡対象に入れたら暗号化の意味が消える**。

### 投資法ページ (view-invest)
- 静的コンテンツ(fetch無し)。SEPAの基本サイクル、トレンドテンプレート8条件、VCP(V1〜V7)、
  エントリー/損切り・ポジションサイズ/利益確定の要点をプレーンなHTMLで記載(ユーザーが手法を
  ダッシュボードから離れずに見返せるようにする目的)。他ロジックとの依存関係は無し。

### バッチ実行ページ (view-batch, docs/assets/batch.js)
- `window.MINERVINI_CONFIG.workflows` (config.js): daily.yml/universe.yml/jquants-backfill.yml/
  intraday-indices.yml の4件を `{file, label, desc}` で定義。GitHub Actionsの実ワークフローファイル名と
  一致させる必要あり(ワークフロー追加時はここに追記)。
- `initBatchView()`: `#batch-cards` にワークフローごとの `.batch-card` を生成(初回のみ、`wired` フラグでガード)、
  実行ボタンは `window.MinerviniFundamentalsUI.triggerWorkflow(btn, wf.file)` → `github-api.js::dispatchWorkflow`。
  `#batch-history` は毎回 `GH.listWorkflowRuns(wf.file, 5)` で直近5件を再取得し `.run-status-badge` で色分け表示
  (success=accent, failure=danger, in_progress/queued=warn)。実行トリガーには🔓解錠(書き込み権限PAT)が必要
  (`.passkey-gated` でボタンごと非表示)だが、履歴閲覧自体は認証不要(公開リポジトリのActions実行履歴は
  未認証でも読めるため `listWorkflowRuns` はプレーン `fetch` を使用、60req/hr制限はこの用途では十分)。

### 個別株 (view-stock, 2026-07-08にSPA統合)
- 3ペイン: 価格+MA(50/150/200) / 出来高 / RSライン(対TOPIX)。`makeChart` で共通生成、時間軸は最下段のみ表示、
  `timeScale.fixRightEdge: true`, `handleScale.axisPressedMouseMove.price: false` (価格軸ドラッグで縮尺変更しない),
  `handleScroll.vertTouchDrag: false` (スマホ縦スワイプはページスクロール)
- ペイン間で `subscribeVisibleLogicalRangeChange` により表示範囲を同期
- 期間切替 (#timeframe-toggle): data-tf = "5"(1週)/"22"(1ヶ月・初期)/"66"(3ヶ月)/"130"(半年)/"250"(1年)/"500"(2年)/"M"(月足)。
  `setTimeframe(tf)`: 日足はバー数で `setVisibleLogicalRange({from: n-bars, to: n})`、"M" は月足集計データに切替
- 最新日付ラベル: Lightweight Charts は最新日の目盛りを保証しないため、`addLatestDateLabel` で
  `.latest-date-label` (absolute配置, style.css参照) を各ペイン右下にオーバーレイ。
  縦位置は決め打ちpxではなく `alignLatestDateLabel` が各ペインの時間軸canvas(最後のcanvas要素)の
  実測bottom/heightを取得して合わせている(以前は下にズレて見えていた不具合の修正。resize時も再計測)。
- ピボット/損切りの水平線トグル (#toggle-pivot / #toggle-stop)。
  **ラベルは左端に自前描画 (2026-07-27)**: Lightweight Charts の `createPriceLine({title})` は
  右のプライスケール側にしか文字を出せず左寄せオプションが無い。プライスケール自体を左に
  移すと3ペイン同期に影響するため、`title: ""` にした上で Series Primitive
  `createEdgeLabelPrimitive(getItems)` がキャンバス左端(x≈7)に「ピボット」「損切り」を
  自前描画する(`createVcpZigzagPrimitive` と同じ手法)。トグル切替時は
  `edgeLabelPrimitive.requestUpdate()` を呼ぶこと。
- データは docs/data/charts/{code}.json (candles, volumes, rs_line, ma各種, pivot, stop_loss, 収縮マーカー)
- **SPA化に伴う再初期化の後始末 (2026-07-08)**: 以前はページ遷移のたびにフルリロードされていたため
  `renderCharts()` の中身は使い捨てで良かったが、SPA化で同じDOMのまま銘柄を切り替えるようになったため、
  `teardownCharts()` (モジュール変数 `stockChartState` に前回の `{charts, resizeHandler, dateLabels}` を保持)
  で毎回 `initStockPage()` の先頭で: 各チャートインスタンスの `.remove()`、`resize` リスナーの `removeEventListener`、
  `.latest-date-label` の除去、を行ってから再構築する。`#toggle-pivot`/`#toggle-stop`/`#timeframe-toggle` は
  `cloneNode(true)` + `replaceWith` でDOM要素ごと差し替え、前回分のイベントリスナーが積み上がらないようにしている。
  `rs-card` (RSラインの無い銘柄では非表示) は以前 `remove()` していたが、RS無し→有りの銘柄に切り替えた時に
  戻せなくなるため `hidden` 切り替えに変更。
- **ファンダメンタルズ (2026-07-08 追加、`#fund-detail-card` / `#fund-detail-body`)**: 「ファンダ入力/編集」
  ボタンはダッシュボードのtier table列から撤去し、この個別株画面のみに移設(`renderStockFundamentals`)。
  `docs/data/fundamentals_public.json` (3ソースマージ済み、`{code: {quarters: [{fiscal_quarter, eps,
  revenue}], monthly_yoy, checked_date}}`、四半期は昇順) を `cache: "no-store"` でfetchし、四半期降順
  (直近が先頭) の表を描画。列: 会計四半期 / EPS / EPS前年同期比 / 売上高(億円換算) / 売上高前年同期比。
  前年同期比は `shiftFiscalQuarterYoy` で1年前の同ラベル("2025Q1"→"2024Q1")を引き `growthPct` で算出、
  プラスは `.yoy-positive`(accent)、マイナスは `.yoy-negative`(danger) で色分け。Q4はFY(通期)扱いという
  既存の四半期規約をそのまま踏襲。
  ボタンのクラス名は `fund-edit-btn` のまま維持しているため、既存の `applyLockState()`
  (`.fund-edit-btn` をクラス名で一括querySelectorAll)による🔓解錠状態の活性/非活性トグルがページを
  跨いでもそのまま効く。保存後は `window.MinerviniFundamentalsUI.onSaved` をこの画面の再描画関数に
  差し替えて更新(ダッシュボード側の同名フックより後に評価されるため、個別株画面側が優先)。
  `fundamentals-modal.js` 自体は無改修(`openFundamentalsModal(code, name)` が元々ページ非依存の
  汎用実装だったため呼び出し元を変えるだけで再利用できた)。
  **既知の注意**: `fundamentals_public.json` の最古の `XXXXQ4` はJ-Quants由来で通期(累計)値が
  混じっているケースがあり、翌年の同 `Q4` とのYoY比較が実態より歪む場合がある(データ層の既知の癖、
  UI側は現状未対処)。
- **需給(信用取引) (2026-07-18追加、`#margin-detail-body`)**: ファンダカードの下に
  `renderStockMargin(stock)`が「需給(信用取引)」カードを描画(表示専用、総合スコアには
  一切影響しない。詳細は§3「信用残」節参照)。信用倍率・買残(前週比)/売残・買残回転日数・
  申込日(週次データにつき最大5営業日遅れる旨の注記)。データが無い銘柄は「信用残データなし」。
- **分析用データコピー (2026-07-09 追加、`#copy-stock-data-btn`)**: stock-metaチップの直下に
  「分析用データをコピー」ボタンを設置(`setupStockCopyButton`、report.jsonに銘柄が無い場合は非表示)。
  クリック時に fundamentals_public.json / breadth.json / indices.json を`cache: "no-store"`で追加fetchし、
  `buildAnalysisMarkdown(stock, chart, report, fundEntry, breadthLast, indicesData)` で自己完結の
  Markdownを生成 → `copyTextToClipboard`(navigator.clipboard、非対応時はtextarea+execCommandフォールバック)で
  クリップボードへ。内容: ヘッダ(ティア/ステータス/セクター) / 価格・テクニカル表(RS・ピボット・
  損切り・MA乖離等) / スコア / 8条件✓✗ / VCP V1〜V7✓✗+フットプリント / 直近20営業日OHLCV表+
  5/20/60日騰落率+出来高10日/50日平均比 / ファンダ四半期表(YoY計算済み) / 市況(指数前日比+
  8条件合格率+p1_scarce警告※P1という語は使わずに文言化)。Claude等のAIに貼り付けて相談する用途で、
  読み解き側は `skills/minervini-analysis/SKILL.md`(Coworkスキルとして登録可能)が対応。
  イベントは`btn.onclick`上書き方式でSPAの銘柄切替でもリスナーが積み上がらない。

### キャッシュバスター
**docs のJS/CSSを変更したら参照している全HTMLの `?v=N` を必ずインクリメントする。2026-07-11時点:
app.js v=18 (index.htmlのみ。stock.htmlはリダイレクトスタブ化されscriptタグ自体を持たない), style.css v=16 (index.htmlのみ),
heatmap.js v=8, config.js v=6, github-api.js v=6, fundamentals-modal.js v=7(無改修のため据え置き), batch.js v=2,
webauthn-vault.js v=5(今回未変更)。
heatmap.html / stock.html は本文自体がリダイレクトスタブ化されたためscriptタグを持たない(対象外)。**

## 8. GitHub Actions

| workflow | トリガ | 内容 | 所要時間 |
|---|---|---|---|
| daily.yml | 平日 07:00-11:00 UTC(16:00-20:00 JST)毎時 + 手動 | 当日実行済みチェック→未実行なら restore-site-data →`python -m src.pipeline` → **data/ のみ**コミット→ pull --rebase → push → publish-site | 15-30分(スキップ時は数秒) |
| universe.yml | 月初土曜 + 手動 | `python -m src.pipeline --universe-rebuild` (同上のrestore→publish付き) | **40-60分** (timeout 120分) |
| jquants-backfill.yml | 手動のみ | `python -m src.data.jquants --backfill` → data/fundamentals_auto.json, data/jquants_state.json コミット | 全銘柄で20分前後 (timeout 120分) |
| intraday-indices.yml | 平日15分間隔(東証+米国市場時間帯、手動可) | `python -m src.data.indices` のみ実行 → **data/indices/ のみ**コミット→ pull --rebase → push → publish-site | 数分 |
| pages-deploy.yml | master の `docs/**` push + 手動 | restore-site-data → publish-site。フロントのソース(html/js/css)やパスキー vault.json の更新を即Pagesへ反映する | 1分未満 |
| backfill-breadth.yml | 手動のみ | 地合い履歴の再計算 → **コミットせず** publish-site のみ (breadth.json は gh-pages にしか存在しないため) | 数分 |

### GitHub Pages の公開方式 (2026-07-27 変更 — 重要)

**変更前**: `docs/` を master にコミットし、Pages のソースを `master / docs` にしていた。
**問題**: 1日ぶんのコミットで `docs/data/charts` が約15MB、`docs/data/*.json` が約2.4MB 増える
(実測: commit 37ef244a で計16.9MB / 1コミット。1日2〜3コミット)。これが積み上がり `.git` が
**804MB** まで肥大していた。`data/` 配下の中間JSONは合計0.03MB程度で、肥大の原因ではなかった。

**変更後**: `docs/data/` を `.gitignore` に入れて master から外し(`git rm -r --cached docs/data` 済み)、
`docs/` 全体を **gh-pages ブランチへ単一コミットで force-push** する。
毎回 orphan ブランチを作り直すので、公開ブランチ側の履歴は永久に1コミット = リポジトリは
生成物ぶんだけ肥大しなくなる。

composite action 2種で実現している:

- `.github/actions/restore-site-data` — パイプライン実行**前**に gh-pages から `docs/data/` を復元する。
  **これは必須**。`update_breadth()` は `docs/data/breadth.json` を読み戻して履歴を伸ばす実装なので、
  復元せずに走らせると地合い履歴が毎回リセットされる。ブランチが存在するのに breadth.json を
  復元できなかった場合は**わざとジョブを失敗させる**(静かに履歴を失うより止める)。
  ブランチがまだ無い初回だけは空の `docs/data` で続行する。
- `.github/actions/publish-site` — `docs/` に一時的な git リポジトリを作り、orphan ブランチとして
  `--force` push する。`.nojekyll` もここで置く。

**ユーザーが手で1回だけやること**: GitHub の Settings → Pages → Source を
`gh-pages` ブランチ / `/ (root)` に切り替える。切り替えるまで公開内容は更新されない。

**パスキー vault との関係**: フロントは `docs/auth/vault.json` を GitHub Contents API 経由で
**master へ**書き込み、読むときは Pages の相対URLから取る。master に置いただけでは Pages に
反映されないので、`pages-deploy.yml`(`docs/**` の push で発火)を追加して差を埋めている。

### ブランチ構成と運用ルール (2026-07-27〜 — 必読)

| ブランチ | 中身 | 書き込む主体 | 履歴 |
|---|---|---|---|
| `master` | ソース全部 + ワークフロー定義 + `data/` の軽い中間データ | 人間 と bot | 通常どおり積む |
| `gh-pages` | `docs/` のスナップショット(生成物込み) | `publish-site` action のみ | **常に1コミット**(毎回 orphan で作り直し) |
| `price-cache` | `data/prices/*.parquet` | `publish-price-cache` action のみ | **常に1コミット**(同上) |

- **ワークフローは master にしか置けない**。`schedule` / `workflow_dispatch` は
  デフォルトブランチの定義しか読まないうえ、公開ブランチは publish のたびに
  作り直されるので置いても消える。ダッシュボードのバッチ画面も
  `docs/assets/config.js` の `branch: "master"` を `ref` に渡して dispatch している。
- **`gh-pages` / `price-cache` を手で編集・コミットしない**。次の run の `--force`
  push で無言で消える。作業は必ず master で行う。実質「git ブランチの形をした
  データ置き場」であってブランチではない。
- **この2ブランチを削除すると復旧できないものがある**。`docs/data/breadth.json` の
  地合い履歴は「前回ぶんを読み戻して1日足す」で伸びているため、`gh-pages` を消すと
  過去の地合いが**恒久的に失われる**(唯一の実体がそこにしかない)。`price-cache` は
  消しても自己修復するが、次の run が全銘柄の全履歴を取り直すので極端に遅くなる。
  ブランチ保護を掛けておくのが望ましい。
- **ローカルの `docs/data/` と `data/prices/` は git 管理外**。clone しても付いてこない。
  必要なら `git fetch origin gh-pages && git checkout origin/gh-pages -- data` のように
  公開ブランチから引くか、パイプラインを1回回す。
- **publish ステップは master への push 可否と独立**に走る。rebase 競合で push だけ
  失敗してもサイトとキャッシュは更新される。逆に publish が落ちると master が
  進んでいてもサイトは古いままになる。
- `backfill-breadth.yml` だけは master に一切コミットせず publish のみ行う
  (breadth.json が master に存在しないため)。

- daily.yml は `JQUANTS_API_KEY: ${{ secrets.JQUANTS_API_KEY }}` と `EDINETDB_API_KEY: ${{ secrets.EDINETDB_API_KEY }}` を env に設定済み。
  `edinetdb.enabled: true`(2026-07-08〜)のため、**`EDINETDB_API_KEY` の GitHub Secret 登録が
  未了だとネットワークに出られず既存ストアのみ返す**(エラーにはならないが補完も効かない) →
  Secret未登録なら早めに登録すること。
  jquants-backfill.yml は `JQUANTS_API_KEY` のみ(EDINET DBにバックフィルCLIは無い)。
- コミット→`git pull --rebase`→push の順(先にコミットしてツリーを綺麗にしてからrebase)。
- concurrency グループでワークフロー多重起動を防止。
- **daily.ymlのcron hourly化 (2026-07-08)**: 単発cron(`30 7 * * 1-5`)だと、GitHub Actions側の
  「schedule イベントは高負荷時に遅延・まれにドロップされる」既知の仕様に引っかかり、実際に
  2026-07-06分は発火せず、2026-07-07分も07:30予定が10:38まで3時間超遅延して発火したことが実測で
  確認された(ユーザー指摘により調査・対処)。対策として `07:00-11:00 UTC` 毎時に変更し、
  jobの先頭(checkout直後・pip installより前)に「当日実行済みチェック」ステップを追加。
  `git log --format=%s -20` に当日日付の `chore: daily screener run YYYY-MM-DD` があれば
  `skip=true` を出力し、以降の setup-python/依存インストール/pipeline実行/commit の各ステップを
  `if: steps.check.outputs.skip != 'true'` でスキップする(スキップ時は数秒でジョブ終了、
  Actions分の浪費を抑える)。当日分コミットの有無を判定するため checkout は `fetch-depth: 0` に変更
  (従来のデフォルトshallow cloneだとgit logで参照できる履歴が足りない)。

## 9. 出力JSONスキーマ(要点)

### docs/data/report.json
```
{ generated_at, universe_size, template_pass,
  priority_counts: {p1,p2,p3,p4}, p1_scarce: bool,
  data_warnings: {failed_tickers, stale_tickers, csv_errors},
  stocks: [ { code, name, close, tier("confirmed"|"pool"|"watchlist"),
    rs, total_score, tech_score, full_score, footprint,
    pivot, buy_stop, stop_loss, risk_pct, entry_status,
    fund_coverage("full"|"partial"|"none"), fund_strong(bool|null), fund_eps_yoy, fund_rev_yoy, fund_stale, fund_checked_date,
    fund_verdict("pass"|"unknown"|"fail"|null), fund_multiplier(1.0|0.5|0.0|null),
    priority(1-4), unmet_conditions, high_dist, ma_dev系, sector33, sector_strength, sector_direction,
    has_chart, new_breakout_today?, market_guard_warning? } ] }
```
### docs/data/breadth.json
`{history: [{date, template_pass_rate, watch_count, breakout_success_rate, p1_count..p4_count}]}`

### data/history/*.jsonl — 追記専用の履歴ストア (2026-07-27〜)

エントリー状態の履歴 (ピボットのロック、EXTENDEDクールダウン、ブレイク成功率算出に使用) と
セクター履歴を、**1行1レコードのJSONL**として追記だけしていく方式に変えた。

| ファイル | 1行の形 | 重複排除キー | 旧ファイル(移行元) |
|---|---|---|---|
| `data/history/status.jsonl` | `{code, date, status, pivot, stop_ref_low}` | `(code, date)` | `data/status_history.json` |
| `data/history/sector.jsonl` | `{date, topix_d1, sectors: {業種名: {...}}}` | `(date)` | `data/sector_history.json` |

**なぜ変えたか**: 旧方式は `{code: [エントリ...]}` の全量を毎回書き戻すので、1日ぶんの追記でも
ファイル全行が書き換わり、日次コミットの差分が毎回数千行に膨れていた。追記だけなら差分は
追記行のみになる。加えて JSONL は DuckDB の `read_json_auto()` がそのまま読めるので、
中間ETLもDBファイルも無しでSQL分析できる。

**「同日再実行は上書き」の互換性**: 追記専用なので同じキーの行が複数ありうる。
`load_deduped()` が**後勝ち (last-write-wins)** で畳むため、外から見た挙動は旧方式と同じ。
`load_status_history()` / `load_sector_history()` の返り値の形も移行前と完全に同一なので、
呼び出し側 (`record_status`, `publish_sector_history` など) には手を入れていない。

**compaction は遅延実行**: 毎回書き直すと追記専用の意味が無いので、行数がしきい値を超えたときだけ
`compact()` で dedup + 期限切れ間引きをする(status は 2000行かつ想定キー数の4倍、sector は
`max(keep_days*2, 100)` 行)。したがって**ディスク上には一時的に古い行や重複行が残る**。
公開データ(`docs/data/sector_history.json`)側は `history_keep_days` で別途間引いているので、
公開内容は移行前と一致する。

**旧JSONの扱い**: 削除していない。JSONLが存在しない場合のみ自動で取り込む
(`_migrate_legacy_status_history` / `_migrate_legacy_sector_history`)。手動移行は
`python scripts/migrate_history_to_jsonl.py`(`--dry-run` あり、冪等)。

**SQL分析**:
```bash
pip install duckdb
python -m src.analyze --list                    # ビューとプリセット一覧
python -m src.analyze --preset status-daily     # 日別ステータス件数
python -m src.analyze --preset status-streak    # 同ステータス継続日数
python -m src.analyze --preset sector-strength  # 直近日のセクター相対強度
python -m src.analyze --sql "SELECT ... FROM status_latest"
```
生ビュー `status` / `sector` は**重複行を含む**。dedup済みを見たいときは必ず
`status_latest` / `sector_latest` を使うこと(プリセットは全てこちらを使用)。

## 10. テスト・検証

```bash
python -m pytest tests/ -q        # 210件 (2026-07-11時点全パス)
node --check docs/assets/app.js   # JS構文チェック
```
- tests/test_pipeline.py の `wired` fixture は全外部I/Oをmonkeypatchでモック。
  **pipelineに新モジュールを足したら必ずここにもモックを追加**
  (例: `monkeypatch.setattr(pipeline.jquants_mod, "update_fundamentals_auto", lambda codes, config: {})`、
  `monkeypatch.setattr(pipeline.edinetdb_mod, "update_fundamentals_auto", lambda codes, config, base_store=None,
  priority_by_code=None: {})`)。
- tests/test_jquants.py: record_to_point / derive_quarters / _refetch_incomplete / ストア / merge_fundamentals をカバー。
- tests/test_edinetdb.py: record_to_point / derive_with_base / update_fundamentals_auto(backlog/budget/events失敗系) / state・storeの永続化をカバー。
- tests/test_market_signal.py: compute_breadth_stats / compute_index_trend / compute_market_signal の
  green/yellow/red境界・NaN除外・指数データ欠損をカバー。
- tests/test_positions.py: load_positions_csv(パース/警告) / build_positions_report(R倍数/売りシグナル/
  data_missing)をカバー。
- tests/test_backtest.py: find_breakout_index / is_strong_breakout / measure_performance を合成データで
  カバー(scan_setupsとVCP統合部分はユニットテスト対象外、下記§14参照)。
- フロントは自動テスト無し。手動確認 or node で DOM stub を書いて smoke。
  (このセッションでは環境にnodeコマンドが無かったため、`preview_start`でdocs/を配信し
  preview_console_logsでエラー無しを確認する代替手段を使った)。

## 11. 開発環境の注意 (Cowork/Claudeサンドボックス固有)

- **マウントフォルダ上で git はファイルを書き換えられない** (unlink が Operation not permitted)。
  - `git commit` 等は `.git/index.lock` / `.git/HEAD.lock` / `.git/objects/maintenance.lock` が残留しやすい。
    対処: `mv .git/index.lock .git/index.lock.stale_$(date +%s)` のように**mvで退避**(rmは効かない)。
  - `git checkout` / `git reset --hard` / `git rebase` は**働かない**(ワークツリー書き換えが unlink 依存)。
    ファイル復元は `git show <commit>:<path> > <path>` のシェルリダイレクトで行う(truncate+writeは可能)。
    コミット作成は plumbing (`git read-tree` + `git update-index --cacheinfo` + `git write-tree` + `git commit-tree` + `git update-ref`) が確実。
  - サンドボックスから `git push` は不可(認証なし)。**pushは必ずユーザーがローカルで実行**。
  - ユーザーのローカル(macOS)では `.git/*.lock` や `lockdump_*`, `*.stale_*` が残ることがある →
    `rm -f .git/index.lock .git/HEAD.lock .git/objects/maintenance.lock .git/lockdump_*` してから pull/push。
- daily.yml が平日毎日 docs/data/ と data/ にコミットする → **作業前に必ず `git fetch` して origin/master との乖離を確認**。
  乖離時は生成データ(data/, docs/data/)はリモート(bot)側を正、ソースはローカル側を正として統合する。
- 現在の状態 (2026-07-07): ローカルmasterが origin/master より複数コミット先行
  (指数リアルタイム化・ヒートマップ簡易表示・日付ラベル修正 → その後 ファンダ入力欄プリフィル修正
  〔fundamentals_public.json新設〕)。**ユーザーが `git pull --rebase` → `git push` すれば同期完了**。
  `git log --oneline -5` で最新ハッシュを確認。

## 12. 未対応・次のタスク候補

0. **市場概況カードの「リアルタイム」表示について**: 本サイトは静的GitHub Pagesでバックエンドが無いため、
   ティック単位の真のリアルタイムは不可能。代わりに intraday-indices.yml が市場時間中15分間隔で
   docs/data/indices.json を更新し、app.js の `startLiveIndices` がそれを60秒間隔でポーリング・再描画する
   構成にした(ページ開きっぱなしでも手動リロード不要)。intraday-indices.yml と daily.yml は同じ
   docs/data/indices.json・data/indices/ を触るため、稀に `git pull --rebase` がコンフリクトして
   そのIntraday実行だけ失敗することがある(次回実行で復旧する想定・致命的ではない)。
1. ~~J-Quantsキー登録+バックフィル実行の確認~~ → **完了確認済み** (`54c09e9`実行済み、
   data/fundamentals_auto.json に997銘柄・jquants_state.json も遅延日数分まで追いついている)。
   ただしフロントの「ファンダ入力/編集」モーダルがこのデータを一切参照していなかったため
   2026-07-07に fundamentals_public.json 経由のプリフィルを追加 (上記4章参照)。
2. J-Quantsの `EPS`/`Sales` 列は実データ検証がまだ。
   バックフィル後に fundamentals_auto.json の値を数銘柄、決算短信と突き合わせて検証するのが望ましい。
   銀行・保険等の特殊業種は Sales が取れず NCSales にもフォールバックしない可能性 → 必要ならフィールド追加で対応。
3. ~~style.css の未使用 .prio-badge / .prio-1〜4 の削除~~ → 2026-07-08、COLUMNS一本化に伴い削除済み。
4. passkeyAuth (書き込み系UI) はキルスイッチOFFのまま。再有効化するなら config.js の passkeyAuthEnabled を true に。
5. ~~report.json から P2-P4 レコードの出力自体を止める軽量化~~ → **2026-07-11完了**
   (`src/pipeline.py`で`continue`のみに変更、`assemble_priority_record`削除。breadthの
   p1_count〜p4_countはpriority_by_codeから独立集計のため無影響)。
6. RSパーセンタイルの母集団はユニバース内銘柄(全市場ではない)— 既知の仕様。
   **2026-07-27: ユニバースを「流動性上位1000」から「20日平均売買代金1億円以上」へ変更したため、
   母集団の顔ぶれと大きさがここで不連続になる。** 相対的に弱い中小型が母集団に加わるぶん
   既存銘柄のRSパーセンタイルは押し上がる方向に動くので、`trend_template.rs_min`(70)の
   実効的な厳しさも変わる。`data/history/status.jsonl` / `data/dryup_log.jsonl` /
   `data/backtest/` の切替日をまたいだ集計は、前後を混ぜて評価しないこと。
   さらに、この方式では銘柄数自体が市況で変動する(売買代金が全体的に細れば母集団が縮む)。
   `data/universe.json` の `min_trading_value` / `size_cap` / `size` に、その時点の
   採用基準と結果の銘柄数が記録されているので、履歴を解釈するときはそこを見る。
7. ~~EDINET DB実地確認~~ → 2026-07-08、ユーザー指示で `config.yaml: edinetdb.enabled: true` に切替済み。
   **daily.yml の `EDINETDB_API_KEY` Secret が未登録なら登録すること**(未登録でもエラーにはならず
   既存ストアを返すだけで補完が効かないだけ、なので気付きにくい)。初回実行後は
   `data/edinetdb_auto.json` の値(revenue単位・fiscal_quarterラベル)を決算短信の実際の数値と
   突き合わせて確認するのが望ましい(§5「実地確認について」参照)。
   → 同日、`0 codes processed` バグを発見・修正(§5「実地確認について」の追記参照)。
   → ~~daily.yml のガードを一時無効化中~~ → **2026-07-11、元のgit log判定に復旧済み**
   (`.github/workflows/daily.yml`)。
8. **2026-07-11新機能の次候補**:
   - ポジション管理: 保有銘柄がユニバース外(流動性下位/新規上場直後等)だと`data_missing`に
     なる既知の制約(§3「ポジション管理」節参照)。必要になったら保有銘柄をprices取得対象に
     加える改修を検討。
   - 簡易バックテスト: 現状はフェーズ1(週次グリッド近似)。精度を上げるなら全日付スキャン+
     RSのより厳密なpoint-in-time化(フェーズ2、計算量が大きく増える点に注意)。
   - 地合いシグナル: 現状は閾値ベースの単純合成。将来的に履歴(breadth.json)を使った
     シグナルの精度検証(バックテストとの組み合わせ)が考えられる。
9. **2026-07-18新機能の次候補(宿題)**:
   - 信用残(§3「信用残」節参照)は現時点では表示専用(スコア非組み込み)。13週間程度
     `data/margin_weekly.json`が蓄積したら、`src/backtest.py`(§13)で「買残重い/売り長
     バッジがブレイク後の成績にどう影響するか」を検証し、スコア組み込みの是非を再検討
     すること。ユーザーからの明示的な指示があるまではスコアに組み込まない。
   - 地合い詳細パネルの`market_score`も同様に表示専用。ある程度history(score_breakdown
     付き)が蓄積したら、market_scoreとその後のブレイク成功率/地合い(green/yellow/red)の
     的中率との相関を見て、閾値(green_pct_above_ma200/red_pct_above_ma200)の妥当性を
     再検証する材料に使える。

## 13. 簡易バックテストCLI (src/backtest.py) — 2026-07-11追加

rs_min / breakout_vol_mult / stop_loss_pct 等のパラメータが一度も実績検証されておらず、
breadth.jsonのbreakout_success_rateもセットアップがほぼ出ないため常にnullに近い状態が
続いていた(VCP検出が厳しすぎるのか地合いのせいなのか切り分けたい、という動機)。
data/prices/ の520営業日分の日足キャッシュを使ったイベントスタディCLIを追加。

- **実行**: `python -m src.backtest [--days 400] [--limit 20] [--rs-min N] [--vol-mult N]
  [--stop-pct N]`。GitHub Actions化はしない(ローカル/手動実行のみ、想定所要時間は
  対象銘柄数と検証日数に比例。手元smokeテストでは150銘柄・400営業日で約25秒)。
- **スコープ(フェーズ1に限定、完全なウォークフォワードではない)**:
  - RSはpoint-in-timeの厳密な再計算ではなく近似: 全銘柄のrs_rawを日付×銘柄でピボットし、
    各日付の行で`rank(pct=True)`して1-99に変換する(`indicators.rs_percentile_rank`と同じ式)。
    ma/atr/52w高安/rs_rawは全てbackward-lookingなrolling計算のみで構成されているため、
    フルhistoryに対して`compute_all()`を1回走らせてから任意の日付でスライスしても
    未来データは混入しない、という性質を利用している。
  - VCPスキャンは全日付ではなく週次(5営業日ごと)の日付グリッドで、トレンドテンプレート
    合格(`trend_template.check_must_conditions`をその日の行に対して直接呼ぶ)かつ
    RS>=rs_minの銘柄のみを対象に`vcp_mod.evaluate_vcp(df.iloc[:i+1], config)`を実行
    (計算量削減。`df.iloc[:i+1]`のスライドがルックアヘッド対策そのもの)。
  - 同一銘柄でpivotが近い(±1%以内)セットアップは1件に統合(`scan_setups`)。
- **ブレイク判定** `find_breakout_index`: セットアップ後60営業日以内にcloseが最初にpivotを
  上抜けた行。無ければ「不発」扱い(None)。
- **成績測定** `measure_performance`: ブレイク日の翌日始値(無ければブレイク日終値)を
  仮エントリー価格とし、終値ベースでストップ(`entry_price*(1-stop_loss_pct)`)を下回ったら
  その時点で建玉を閉じた扱いにする(以降のホライズンのリターンはストップ価格で固定)。
  +5/+10/+20営業日リターン・最大ドローダウン・R倍数(`(exit-entry)/(entry-stop)`)を返す。
- **出力**: 標準出力にMarkdownサマリーを表示 + `data/backtest/backtest_YYYYMMDD.md`に保存。
  セットアップ検出数(月別)、ブレイク発生率、強/弱ブレイク別の+5/+10/+20日平均・中央値
  リターン・勝率・ストップ到達率・期待R、使用パラメータを含む。レポート冒頭に
  ユニバースの生存者バイアス(現在のユニバースで過去を見る)を既知の限界として明記。
- **テスト**: `tests/test_backtest.py`は`find_breakout_index`/`is_strong_breakout`/
  `measure_performance`を合成データでカバー(ブレイク検出・ストップ到達・リターン計算・
  次日始値エントリーのフォールバック)。`scan_setups`(VCP統合部分)は実データでの
  smoke実行(`--limit 8〜150`)で完走・レポート生成を確認済みだが、自動テストの対象外
  (仕様上「フルバックテストの実行はユーザーのマシンで行う」ため、軽量ユニットテストのみ)。

## 14. 変更時のチェックリスト (Sonnet向け)

- [ ] docs/ の JS/CSS を触ったら index.html(唯一scriptタグ/linkタグを持つHTML)の `?v=N` を全部上げたか
      (stock.html/heatmap.htmlはリダイレクトスタブのみでアセット参照なし)
- [ ] pipeline に外部I/Oを足したら test_pipeline.py の wired fixture にモックを足したか
- [ ] `python -m pytest tests/ -q` 全パス + `node --check docs/assets/app.js`
- [ ] コミット前に `.git/*.lock` を mv で退避したか (サンドボックスの場合)
- [ ] `git fetch` して origin との乖離を確認したか (botが毎日コミットする)
- [ ] push はユーザーに依頼したか
- [ ] UI文言に P1/P2/P3/P4 という語を新たに出していないか (概念はUI廃止済み)
