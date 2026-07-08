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
  universe.py               ユニバース構築 (JPX上場一覧→流動性上位1000→セクターmap/発行済株式数)
  indicators.py             MA50/150/200, MA200勾配日数, 52w高安, ATR, RS raw/percentile, RSライン
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
docs/                       GitHub Pages ルート
  index.html                1ページSPA (2026-07-08〜): view-dashboard/view-sectormap/view-invest/view-batch/view-stock の
                            5セクション+下部Dockナビ(#dock-nav)。表示切替は location.hash ベース(app.jsのshowView/initRouter)。
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
  data/                     パイプライン出力 (report.json, breadth.json, heatmap.json, indices.json, charts/{code}.json)
data/                       中間データ (universe.json, prices/*.parquet, indices/*.parquet,
                            status_history.json, sector_history.json, sector_map.json,
                            trend_template_debug.json, ※J-Quants実行後: fundamentals_auto.json, jquants_state.json,
                            ※EDINET DB実行後: edinetdb_auto.json, edinetdb_state.json)
manual/fundamentals.csv     手動ファンダ (code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date)
tests/                      pytest 152件 (test_jquants.py, test_edinetdb.py 含む)
```

## 3. 日次パイプラインの流れ (src/pipeline.py :: run_daily)

1. `jpholiday` で祝日ならスキップ (return 0)
2. 市場指標更新 `indices_mod.update_indices` — 失敗してもスクリーナーは止めない (try/except)
3. `load_universe()` → codes (~1000銘柄)
4. `prices_mod.update_prices(codes)` — yfinanceを50銘柄チャンク+sleep 2-4s、失敗銘柄はstooqへ。失敗率>10%でジョブ失敗
5. `compute_all` で指標付与 → `rs_percentile_rank` でRS(1-99パーセンタイル、母集団はユニバース)
6. `trend_template.screen_universe` → 8条件フラグ (debug: data/trend_template_debug.json)
7. `priority_mod.evaluate_priority` — ハードフィルタ通過銘柄にP1〜P4付与。`p1_scarce` = P1数 < priority.p1_warn_threshold(3)
8. **ファンダ**: `load_fundamentals_csv()` (手動CSV) + `jquants_mod.update_fundamentals_auto(codes, config)` (自動取得、try/exceptで失敗無視) → `merge_fundamentals(auto, manual)` (手動が勝ち)
9. P1銘柄のみ: VCP評価 → エントリー評価 → `score_stock` → レコード組立 + チャートJSON出力
   - actionable (BREAKOUT/BREAKOUT_WEAK/WATCH_A/WATCH_B/EXTENDED + pivotあり) → confirmed/pool ティア
   - それ以外 → `tier_override="watchlist"` (=フロントの〔候補〕)
   - P2〜P4: `assemble_priority_record` の軽量レコード (VCP評価なし, tier="watchlist", has_chart無し)
10. ヒートマップ生成 (try/except) → 各レコードに sector33/sector_strength/sector_direction 付与
11. `build_report` (docs/data/report.json) + `update_breadth` (docs/data/breadth.json)

### ティアとフロント表示の対応
- `tier: "confirmed"` → 〔本命〕ファンダ確認済み (ファンダquartersが1件でもあれば confirmed)
- `tier: "pool"` → 〔候補プール〕テクニカルのみ (VCPセットアップあり、ファンダなし)
- `tier: "watchlist"` → 〔候補〕トレンドテンプレート8条件合格 (セットアップ形成待ち)

### P1〜P4について(重要な経緯)
- バックエンド(priority.py, report.jsonの `priority`/`priority_counts`/`p1_scarce` フィールド)は**P1〜P4を計算し続けている**。
- **UIからは概念を廃止**: フロントは `tier==="watchlist" && (priority===1 || priority==null)` のみを〔候補〕として**全件RS降順**表示 (app.js renderPriorityTier)。P2〜P4レコードは受信するが表示しない。
- 弱地合い警告バナー(renderP1Warning)は残存。文言は「8条件完全一致の候補銘柄が◯件と極端に少ない…」(P1という語は使わない)。
- 地合いメーターに「候補(8条件合格): N件」を表示 (renderBreadth)。
- style.css の .prio-badge / .prio-1〜4 / .priority-table は2026-07-08に削除済み(対応するJSがCOLUMNS一本化で消えたため)。

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

### マージ (src/data/fundamentals.py :: merge_fundamentals)
- `merge_fundamentals(auto_by_code, manual_by_code)` — 同一 (code, fiscal_quarter) は**手動CSVが勝ち**。
  monthly_yoy は手動のみ(自動には無い)。checked_date は手動優先、無ければ自動。
- pipeline.py での呼び出し: `fundamentals_by_code = merge_fundamentals(auto_by_code, build_fundamentals_by_code(csv_df))`
- 効果: J-Quantsデータが入った銘柄は quarters が存在する → `fund_coverage_tier` が "confirmed" を返す → 〔本命〕へ自動昇格。

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
  `tanshin_by_code = edinetdb_mod.update_fundamentals_auto(codes, config, base_store=auto_by_code)`
  (try/exceptで失敗を無視)を追加し、
  `merge_fundamentals(auto_by_code, build_fundamentals_by_code(csv_df), tanshin_by_code=tanshin_by_code)` に変更。
- 効果: EDINET DBで直近四半期が入った銘柄も quarters が非空になり `fund_coverage_tier` が
  "confirmed" を返す → 〔本命〕昇格が最大12週早まる。

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
- `trend_template`: rs_min 70, score_weights {rs:30, ma200_days:10, near_high:15, eps_accel:25, rev_accel:10, monthly:10}
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

### SPA構造 (2026-07-08〜)
- index.html は5つの `<section class="view-section" id="view-{dashboard|sectormap|invest|batch|stock}">` を持つ1ページ。
  初期状態は view-dashboard のみ表示、他は `hidden`。
- 下部固定 `<nav class="dock-nav" id="dock-nav">` にmacOS Dock風の4ボタン(`.dock-btn[data-view=...]`)。
  `view-stock` はドリルダウン専用でDockには出さない(ダッシュボード表の行クリックからのみ遷移)。
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

### ダッシュボード (view-dashboard)
- `initDashboard`: report.json / breadth.json / indices.json を `cache: "no-store"` でfetch → 各render
- `COLUMNS` (本命/候補プール/監視の3ティア共通、1本化済み): code, name(10文字トリム), close(終値), total_score, rs,
  footprint, pivot, buy_stop, stop_loss, risk_pct, fund_status, sector(セクター強度で文字色分け)
  - 終値は `formatClose` (ja-JP ロケール, 小数1桁まで)
  - `renderTable(stocks, tier, options)` が3ティア共通の描画/ソート実装。`options.initialSortKey`/
    `initialSortDesc` で初期ソート列を指定(confirmed/poolはtotal_score降順、watchlistはrs降順)。
    各列は `sortValue(s)` を任意で持てる(フォーマット済み文字列ではなく元の数値でソートするため)。
  - セクター強度の文字色: `.sector-strength-strong`(accent) / `-mid`(text-dim) / `-weak`(danger)。
  - チャートJSON未生成の行(`has_chart === false`)は `.row-static` でクリック不可(view-stockへ遷移させない)。
  - 行クリックは `window.location.hash = "stock/" + code` で view-stock へ遷移(旧: `stock.html?code=...`)。
- `renderPriorityTier(report, "watchlist-tier-body")`: watchlist かつ priority 1 or null を RS降順・全件、
  上記共通 `renderTable` に `initialSortKey: "rs"` を渡して描画(旧 `PRIORITY_COLUMNS`/`renderPriorityTable` は削除)。
- `renderP1Warning`: report.p1_scarce で警告バナー (#p1-warning)
- `renderBreadth`: テンプレ通過率 / セットアップ数 / ブレイク成功率 / 候補(8条件合格)件数
- `renderMarketOverview`: indices.json → カード + SVGスパークライン
- `startLiveIndices`: 指数カードの擬似リアルタイム更新。60秒間隔で indices.json を再fetch (`cache: "no-store"`) して
  `renderMarketOverview` を再実行するだけ(ページリロード不要)。バックグラウンドタブ (`document.hidden`) では止める。
  データ自体の更新頻度は intraday-indices.yml 側の15分間隔が上限 (静的サイトなのでティック単位の真のリアルタイムではない)。
- WebAuthn/書き込み系: `passkeyAuthEnabled: false` (config.js) のキルスイッチで現在**全部非表示**。
  対象は `.passkey-gated` クラスを持つ全要素(旧: id固定リストの `hidePasskeyAuthUi`。ヘッダーのボタンに加え
  view-batch の実行カードも同クラスで一括隠蔽できるよう、2026-07-08にクラスベースへ変更)。
  有効化すると: 解錠ボタン(WebAuthn PRF→PAT復号)→ファンダ入力モーダル/バッチ実行ボタンが活性化。

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
- ピボット/損切りの水平線トグル (#toggle-pivot / #toggle-stop)
- データは docs/data/charts/{code}.json (candles, volumes, rs_line, ma各種, pivot, stop_loss, 収縮マーカー)
- **SPA化に伴う再初期化の後始末 (2026-07-08)**: 以前はページ遷移のたびにフルリロードされていたため
  `renderCharts()` の中身は使い捨てで良かったが、SPA化で同じDOMのまま銘柄を切り替えるようになったため、
  `teardownCharts()` (モジュール変数 `stockChartState` に前回の `{charts, resizeHandler, dateLabels}` を保持)
  で毎回 `initStockPage()` の先頭で: 各チャートインスタンスの `.remove()`、`resize` リスナーの `removeEventListener`、
  `.latest-date-label` の除去、を行ってから再構築する。`#toggle-pivot`/`#toggle-stop`/`#timeframe-toggle` は
  `cloneNode(true)` + `replaceWith` でDOM要素ごと差し替え、前回分のイベントリスナーが積み上がらないようにしている。
  `rs-card` (RSラインの無い銘柄では非表示) は以前 `remove()` していたが、RS無し→有りの銘柄に切り替えた時に
  戻せなくなるため `hidden` 切り替えに変更。

### キャッシュバスター
**docs のJS/CSSを変更したら参照している全HTMLの `?v=N` を必ずインクリメントする。2026-07-08時点:
app.js v=11 (index.htmlのみ。stock.htmlはリダイレクトスタブ化されscriptタグ自体を持たない), style.css v=11 (index.htmlのみ),
heatmap.js v=8, config.js v=6, github-api.js v=6, fundamentals-modal.js v=7, batch.js v=2, webauthn-vault.js v=5(今回未変更)。
heatmap.html / stock.html は本文自体がリダイレクトスタブ化されたためscriptタグを持たない(対象外)。**

## 8. GitHub Actions

| workflow | トリガ | 内容 | 所要時間 |
|---|---|---|---|
| daily.yml | 平日 07:00-11:00 UTC(16:00-20:00 JST)毎時 + 手動 | 当日実行済みチェック→未実行なら`python -m src.pipeline` → data/ docs/ をコミット→ pull --rebase → push | 15-30分(スキップ時は数秒) |
| universe.yml | 月初土曜 + 手動 | `python -m src.pipeline --universe-rebuild` | **40-60分** (timeout 120分) |
| jquants-backfill.yml | 手動のみ | `python -m src.data.jquants --backfill` → data/fundamentals_auto.json, data/jquants_state.json コミット | 全銘柄で20分前後 (timeout 120分) |
| intraday-indices.yml | 平日15分間隔(東証+米国市場時間帯、手動可) | `python -m src.data.indices` のみ実行 → data/indices/ docs/data/indices.json をコミット→ pull --rebase → push | 数分 |

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
    fund_coverage("full"|"partial"|"none"), fund_stale, fund_checked_date,
    priority(1-4), unmet_conditions, high_dist, ma_dev系, sector33, sector_strength, sector_direction,
    has_chart, new_breakout_today?, market_guard_warning? } ] }
```
### docs/data/breadth.json
`{history: [{date, template_pass_rate, watch_count, breakout_success_rate, p1_count..p4_count}]}`

### data/status_history.json
エントリー状態の履歴 (ピボットのロック、EXTENDEDクールダウン、ブレイク成功率算出に使用)

## 10. テスト・検証

```bash
python -m pytest tests/ -q        # 152件 (2026-07-08時点全パス)
node --check docs/assets/app.js   # JS構文チェック
```
- tests/test_pipeline.py の `wired` fixture は全外部I/Oをmonkeypatchでモック。
  **pipelineに新モジュールを足したら必ずここにもモックを追加**
  (例: `monkeypatch.setattr(pipeline.jquants_mod, "update_fundamentals_auto", lambda codes, config: {})`、
  `monkeypatch.setattr(pipeline.edinetdb_mod, "update_fundamentals_auto", lambda codes, config, base_store=None: {})`)。
- tests/test_jquants.py: record_to_point / derive_quarters / _refetch_incomplete / ストア / merge_fundamentals をカバー。
- tests/test_edinetdb.py: record_to_point / derive_with_base / update_fundamentals_auto(backlog/budget/events失敗系) / state・storeの永続化をカバー。
- フロントは自動テスト無し。手動確認 or node で DOM stub を書いて smoke。

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
5. report.json から P2-P4 レコードの出力自体を止める軽量化(現状フロントで捨てているだけ)。
   ※やる場合 breadth の p2-p4 カウント履歴と priority.py テストへの影響に注意。
6. RSパーセンタイルの母集団はユニバース内銘柄(全市場ではない)— 既知の仕様。
7. ~~EDINET DB実地確認~~ → 2026-07-08、ユーザー指示で `config.yaml: edinetdb.enabled: true` に切替済み。
   **daily.yml の `EDINETDB_API_KEY` Secret が未登録なら登録すること**(未登録でもエラーにはならず
   既存ストアを返すだけで補完が効かないだけ、なので気付きにくい)。初回実行後は
   `data/edinetdb_auto.json` の値(revenue単位・fiscal_quarterラベル)を決算短信の実際の数値と
   突き合わせて確認するのが望ましい(§5「実地確認について」参照)。
   → 同日、`0 codes processed` バグを発見・修正(§5「実地確認について」の追記参照)。
   **daily.yml のガードを一時無効化中** — 動作確認が済んだら元に戻すこと(要フォローアップ)。

## 13. 変更時のチェックリスト (Sonnet向け)

- [ ] docs/ の JS/CSS を触ったら index.html(唯一scriptタグ/linkタグを持つHTML)の `?v=N` を全部上げたか
      (stock.html/heatmap.htmlはリダイレクトスタブのみでアセット参照なし)
- [ ] pipeline に外部I/Oを足したら test_pipeline.py の wired fixture にモックを足したか
- [ ] `python -m pytest tests/ -q` 全パス + `node --check docs/assets/app.js`
- [ ] コミット前に `.git/*.lock` を mv で退避したか (サンドボックスの場合)
- [ ] `git fetch` して origin との乖離を確認したか (botが毎日コミットする)
- [ ] push はユーザーに依頼したか
- [ ] UI文言に P1/P2/P3/P4 という語を新たに出していないか (概念はUI廃止済み)
