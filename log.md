# 進行状況ログ

(新しい方が上。作業を再開する際は必ず先にここを読むこと)

## 2026-07-13 (35): フロントエンド大規模リファクタ(画面役割見直し+UX改善11件)

### 要望(ユーザー原文、要約)
> ①「ダッシュボードに戻る」リンク不要 ②サマリーをポジ(緑)/ネガ(赤)/中立(色なし)で色分け
> ③ファンダを既存表とビジュアルグラフ(新規)で切替 ④サイト名を MinerviniScreener に
> ⑤初回セットアップ/設定ボタン削除 ⑥初回描画でパスキー未通過時に画面をチラ見えさせない
> ⑦パスキーはアクセス時に要求 ⑧リロード時はできれば再認証なし(必要ならボタン表示可)
> ⑨ヒートマップがスマホ上下スクロールで動く(配置変化)のを固定 ⑩統計行はダッシュボードのみ表示
> ⑪画面役割見直し: ダッシュボード=市況+ヒートマップ / 銘柄リストを新規作成 /
>   ヒートマップタブは消してダッシュボードに寄せる / 市況カードもクリックで詳細表示

事前確認(AskUserQuestion): パスキー永続化は sessionStorage に読み取り用データ鍵を保持(推奨) /
ファンダグラフは EPS+売上の推移バー+YoY(推奨)。

### 変更内容
- `docs/index.html`: title/h1を MinerviniScreener に。設定ボタン削除(vault-unlock🔓は維持)。
  lock-screen を既定表示のスプラッシュ(不透明・全面・z1000)化しチラ見え防止。統計行
  (generated-at/breadth-meter/p1-warning)を view-dashboard 内へ移動。市況の下に
  `.hm-section`(セクターヒートマップ)を統合、view-sectormap を削除。`view-stocklist` を新規追加
  (本命/候補/監視の3ティア)。Dockの sectormap→stocklist(list-ul)。view-stockの戻るリンク削除。
  cache-buster: style.css?v=21 / heatmap.js?v=11 / app.js?v=23。
- `docs/assets/secure-fetch.js`(前回セッションで対応済): sessionStorage("minervini-dk")に
  読み取り用データ鍵を保持、restoreDataKey()でリロード時パスキーレス解錠。
- `docs/assets/app.js`:
  - VIEWS を dashboard/stocklist/invest/positions/batch/stock に。showView の dashboard 表示で initHeatmap()。
  - ensureDataAccess: 起動時 restoreDataKey()→report.json封筒判定→未解錠なら lock-prompt+解錠ボタン表示。
  - renderStockSummary: `summary-item-{positive|negative|neutral}` を付与。cautionsは常にネガ、
    pointsは NEUTRAL_POINT_PREFIXES(時価総額/市場/次回決算等)なら中立、他はポジ。
  - renderMarketOverview: 市況カードを role=button 化+イベント委譲、`MARKET_ENTRIES` に key→entry 保持。
    `openMarketModal(key)` を新規実装(現在値/前日比/大スパークライン/期間別騰落(series から算出)/レンジ)。
  - renderStockFundamentals: 表/グラフの `.segmented` トグル追加(設定は localStorage "minervini-fund-view")。
    `fundChartHtml`/`fundBarPanel`(EPS・売上の推移バー、色=YoY正負、直近8Q)を新規実装。
- `docs/assets/heatmap.js`: `lastRenderWidth` を導入。resize は幅が変わった時のみ再描画
  (スマホのアドレスバー開閉による innerHeight 変化=スクロールでは再描画せず、ブロックが動かない)。
- `docs/assets/style.css`: summary-item トーン色 / market-card[role=button] hover・focus /
  market-modal 系 / fund-view トグル・fund-chart(bar/legend) / .hm-section の余白 を追加。

### 検証
- `node --check` を app.js / heatmap.js / secure-fetch.js に実行 → 全てOK。
- 純粋関数の単体スモーク(node): fundChartHtml が有効なSVGを出力 / periodChange(d1,d5,データ不足)/
  marketBigSparkline を確認 → 期待通り。
- 未実施: ブラウザ実描画確認。data/*.json が AES-GCM 封筒で WebAuthn パスキー解錠が必須のため、
  ヘッドレス環境では実データでの画面確認ができない。次回、実機(iOS Safari/パスキー)で
  市況モーダル・ファンダグラフ切替・サマリー色分け・ヒートマップのスクロール固定を目視確認すること。

## 2026-07-13 (34): VCP MUST条件(V1〜V7)の許容度緩和 — 「壊れたベースの排除」のみに限定

### 要望(ユーザー原文、要約)
> 教科書的なVCP形状のみを厳格に判定していると実運用でのヒット数が過少になる。
> MUSTは「壊れたベースの排除」のみを担い、「美しさ」はSCORE側の加点/減点で表現して
> 人間の目視に委ねる方針で緩和したい。

事前確認で2点、当初案から方針転換(ユーザーとすり合わせ済み):
- `monotonic_tolerance`(V2)は前日(2026-07-12)に1.0→1.2へ緩和済み・バックテスト確認済みのため、
  1.15への再変更は撤回し1.2を維持。新規に「前半1回までの逆転許容」「最終/初回比バックストップ」を追加。
- `swing_low_tolerance`(V7)の0.99→0.97緩和は、同バックテストで質の劣化(期待R 0.33)が確認済みのため見送り。
  0.99を維持し、MUSTを緩めない2点(シェイクアウトのスコア加点、最終安値がベース内最安値を
  下回ったら即不合格というフロアガード)のみ追加。

### 変更内容
- `config.yaml` vcp: 新規 `early_violation_allowance`(1)・`overall_contraction_ratio`(0.6)・
  `last_depth_perfect`(0.05)・`volume_trend_ratio`(0.75)・`shakeout_bonus`(5)・
  `vol_trend_bonus_fraction`(0.15)を追加。`last_depth_max` 0.10→0.12。
  `volume_dryup_ratio`(平均ベース0.80)→`volume_dryup_median_ratio`(中央値ベース0.85)にリネーム。
- `src/screener/vcp.py`: `_check_v2`(前半1回まで逆転許容+全体比バックストップ)、
  `_check_v5`(中央値ベースの枯れ判定(a)+出来高トレンド判定(b)のOR)、
  `_check_v7`(既存0.99許容はそのまま+最安値フロアガード+シェイクアウト検出をスコアのみに反映)を実装。
  `check_vcp_must_conditions`/`vcp_quality_score`はdiagnostics dictを返すよう変更(呼び出し元は
  `evaluate_vcp`のみ、後方互換は不要と判断)。`evaluate_vcp`結果に`shakeout_detected`と
  `vcp_diagnostics`をトップレベルで追加。
- `src/report/build_site.py`: `vcp_detail`に`depth_last_pct`・`last_depth_max_pct`・
  `volume_dryup`(recent10_median等)・`shakeout_detected`を追加(vcp_diagnosticsからの転記のみ、再計算しない)。
- `src/report/summary.py`: `volume_dryup_ratio`参照を`volume_dryup_median_ratio`にリネーム(V5判定文言・
  根拠テキスト両方)。`compute_momentum`に`vol_median_ratio_10_50`(既存の平均ベース`vol_ratio_10_50`は
  残置、乖離自体がスパイクシグナルとして有用なため)。根拠テキストがV5と同じ統計量(中央値)を
  参照するよう統一。
- `docs/assets/app.js` / `index.html`: MUST_FLAG_LABELS.vcp のV4/V5固定%表記を削除(設定駆動・OR判定
  のため)。`renderMustChecklist`にvcpDetail引数を追加し実測値/閾値・シェイクアウトバッジを表示。
  フットプリント欄とbuildAnalysisMarkdownにdepth_last_pct併記。キャッシュバスターapp.js?v=20→21、
  style.css?v=17→18。
- テスト: `tests/test_vcp.py`に新規4件(前半逆転許容、V5中央値の単発スパイク耐性、V7シェイクアウト検出、
  V4緩和後のタイトネススコア非最大)。`tests/test_summary.py`の`_CFG`を新config形状に更新。

### 詰まった点と教訓
- `FRONT_HALF_VIOLATION_CONTROL_POINTS`の初期設計が誤り: peak2の価格をT0超に設定したため
  `find_base_origin`がpeak2を新T0として選び直し、意図した形状が崩れて1件失敗した。
  さらに一般に、「ピークがT0を超えない」制約下では**収縮1→収縮2間**の深さ逆転は
  安値が単調非減少のままでは原理的に作れない(安値の床がT0で決まる上限を超えられないため)ことが
  判明。既存の`REVERSED_CONTROL_POINTS`(収縮3で逆転・後半判定)の形状はそのまま流用し、
  末尾に同価格の「平坦延長」を1点追加してbase_daysだけ伸ばし、逆転位置の相対位置(30/43→30/64)を
  後半→前半に押し込む形に修正して解決。
- 検証中、Bashツールの安全性分類器(python実行系コマンドのみ)が長時間(1時間以上)断続的に
  利用不能になる障害が発生。ScheduleWakeupで待機しつつ手計算による検算を進め、復旧後にpytest実行
  →1件の失敗発見・修正→全261件パス、`node --check`もOKまで確認した。

### 検証結果
- `python3 -m pytest tests/ -q` → 261件全パス。
- `node --check docs/assets/app.js` → OK。
- ローカルhttp.serverでの目視確認(report.jsonが旧ロジック生成のため新フィールド欠損時のフォールバック
  確認含む)は未実施(任意項目、優先度低のため保留)。

## 2026-07-12 (33): パスキー必須化 — docs/dataの暗号化 + 起動時ゲート(アクセス制御の実体化)

### 要望(ユーザー原文)
> パスキー機能を実装して。
> 入力だけでなく、アクセス自体をパスキー必須にして

### 設計判断
- 公開リポジトリ+Pagesでは「画面ロック」だけだとJSONのURL直叩きで中身が見えるため、
  **docs/data/*.json 自体をAES-256-GCMで暗号化**し、復号鍵を既存のWebAuthn PRF保管庫
  (vault.json)にPATと一緒に格納する方式にした(パスキー→データ鍵→復号の2段構え)。
- 詳細は HANDOFF.md「アクセス制御: データ暗号化 + 起動時パスキーゲート」参照。

### 変更内容
- `src/report/secure_io.py` 新設 + 全docs/data writerの差し替え(report/breadth/charts/
  fundamentals_public/heatmap/indices/positions)。鍵はenv `DASHBOARD_DATA_KEY`(未設定なら平文)。
  load_breadthの読み戻しも復号対応。リカバリCLI付き。requirements.txt に cryptography 追加。
- daily.yml / intraday-indices.yml に Secret `DASHBOARD_DATA_KEY` を配線。
- フロント: `secure-fetch.js`(封筒自動判別fetch層)新設、**全data fetchを置き換え**
  (app.js×8箇所, heatmap.js, fundamentals-modal.js)。起動時ゲート(`ensureDataAccess`+
  `#lock-screen`)。保管庫v2({pat, dataKey}、v1後方互換)。セットアップモーダルに
  データ鍵入力欄。`passkeyAuthEnabled: true` に変更(解錠/設定/バッチ実行ボタン表示)。
- テスト: tests/test_secure_io.py 10件。**pytest 257件全パス**。
- 検証: テスト鍵でローカル暗号化→preview実機確認(ロック画面表示・背後の初期化停止・
  鍵注入後にダッシュボード/個別銘柄/暗号化チャートまで全描画・コンソールエラー無し)。
  検証後にローカルファイルは平文へ復元済み(テスト鍵の暗号文は未コミット)。

### 有効化手順(ユーザー作業、これをやるまでは平文のまま動く)
1. 鍵生成: `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`
2. GitHub → Settings → Secrets and variables → Actions → New repository secret:
   Name `DASHBOARD_DATA_KEY`、Value に1の出力。**この値は再セットアップ時にも使うので安全な場所に控える**。
3. push後、dailyワークフローを1回実行(以後の出力が暗号化され、ダッシュボードにロック画面が出る)。
4. ダッシュボードのロック画面 →「初回セットアップ」→ PAT + データ鍵(1と同じ値)を入力
   → パスキー登録。以後のアクセスは毎回 Face ID/Touch ID で解錠。
- ロールバック: Secretを削除してdailyを1回回す(平文に戻りゲート消滅)。緊急時の中身確認:
  `DASHBOARD_DATA_KEY=<鍵> python -m src.report.secure_io --decrypt docs/data/report.json`

### 既知の限界(HANDOFF.mdにも記載)
- リポジトリ自体は公開のまま: ソース・log.md・**manual/positions.csv(保有ポジション)**・data/配下は
  暗号化対象外。本気で隠すならリポジトリのprivate化が正道(要ユーザー判断)。

## 2026-07-12 (32): サマリー情報の厚み増強A/B/C(会社予想+進捗率・決算発表予定日・時価総額/市場区分)

### 要望(ユーザー原文)
> ABCいけるとこまでいって

### 変更内容
- **A: 会社予想(ガイダンス)+進捗率+予想PER**
  - `jquants.py`: `record_to_guidance` — /fins/summary の FEPS/FSales(当期通期予想)・
    NxFEPS/NxFSales(翌期予想)・ShOutFY を抽出(フィールド名はJ-Quants v2公式仕様
    jpx-jquants.com/ja/spec/fin-summary で確認済み)。業績予想修正も対象(配当予想修正は除外)。
    銘柄ごと最新開示を fundamentals_auto.json の `"guidance"` に格納。日次増分と --backfill 両対応。
  - `fundamentals.py`: merge_fundamentals / write_public_json が guidance を透過。
  - `summary.py`: `derive_guidance_view` — 進行期計画の選択(本決算開示なら翌期NxF、
    四半期開示なら当期F)、計画YoY(前期実績4Q合計比)、進捗率(実績累計÷計画)、
    予想PER(終値÷計画EPS)。サマリーに「会社計画/進捗率/予想PER」行、
    直近実績YoYと計画YoYの食い違い明示(減益だが計画増益 等)、進捗±15pt乖離で
    上方修正余地/下方修正リスクの言及。
- **B: 決算発表予定日**
  - `jquants.py`: `update_earnings_calendar` — GET /v2/equities/earnings-calendar(Free可、
    3月期・9月期企業のみ提供)→ data/earnings_calendar.json。pipelineがrecord
    `next_earnings_date` に載せ、サマリー(14日以内=発表跨ぎ注意caution/それより先=point)と
    メタチップに表示。無い銘柄は従来の75日推定にフォールバック。
- **C: 時価総額・市場区分**
  - `universe.py`: build_universe が JPX一覧の market segment を universe.json に保存するように。
  - `pipeline.py`: 時価総額(universe.jsonのshares_outstanding×終値)を `market_cap_oku`、
    市場区分を `market_segment` としてrecordに付与。サマリー「時価総額 約N億円(小型/中型/大型株・
    市場区分)」+メタチップ。**既存universe.jsonにはJPX一覧を再取得して1000/1000銘柄分の
    segmentを注入済み**(再構築不要)。
- フロント: メタチップ(時価総額/市場/次回決算、値がある時のみ)+分析用コピーに
  時価総額・市場区分・次回決算・会社計画・進捗率・予想PERの行を追加。
- 即時反映: report.json に時価総額・市場区分・サマリー再生成を注入済み。
  **guidanceと発表予定日はAPIキーが必要なため夜間run以降に反映**。

### 検証
- pytest 247件全パス(232+新規15: guidance抽出4・カレンダー1・ガイダンスビュー4・サマリー6)。
- preview(docs静的配信)で9247の時価総額/市場チップとサマリー新行の表示・JSエラー無しを確認。

### 次のステップ
- **ガイダンスを全銘柄に一括反映するには jquants-backfill.yml を1回手動実行**
  (ダッシュボードのバッチ実行ページから可能、~20分)。やらなくても日次増分で徐々に入る。
- 発表予定日はカレンダー提供対象(3月期・9月期)外の銘柄は75日推定のまま(仕様)。

## 2026-07-12 (31): 個別銘柄画面にルールベース日本語サマリー(LLM不使用)+ステータス日本語化

### 要望(ユーザー原文)
> 次は個別銘柄画面にサマリーを書ける？ LLMがかまずにどこまで行けるだろう
> 情報にさらに厚みを持たせることはできる？情報量・質的な意味で。必要なら追加情報取得も検討
> あとTooReacentとかは日本語に

### 変更内容
- `src/report/summary.py` 新設: 既存判定の言語化のみを行うサマリー生成
  (`build_stock_summary` → {headline, points, cautions})。状態別見出し(WATCH_Aは
  ピボット距離/逆指値/リスク、TOO_RECENT/IMMATUREは「あと何日」、REJECTED/WATCH_Bは
  落ちたV条件の日本語列挙)+ 根拠(8条件/ベース収縮列/騰落率/出来高ドライアップ/
  セクター強度/EPS YoY 4Q推移と加速減速判定/地合い)+ 注意(ファンダ弱・鮮度・
  決算接近推定75日・MA50乖離>15%・リスク幅>7%・地合い赤黄)。閾値はconfigから埋める。
- `src/screener/vcp.py`: find_base_origin/evaluate_vcp が days_from_high を返すように
  (TOO_RECENT/IMMATUREの残日数表示用。既存ロジック不変の追加のみ)。
- `src/report/build_site.py`: レコードに `vcp_detail` (base_days/days_from_high/
  t0_date/depths_pct) を追加。
- `src/pipeline.py`: レコードに `momentum` (5/20/60日騰落率・出来高10/50日比) を付与し、
  セクター強度・地合い確定後に全レコードへ `summary` を生成(失敗しても本体は止めない)。
- フロント: index.htmlに`#stock-summary`、app.jsに`renderStockSummary`+ステータスチップの
  日本語化。STATUS_LABELSを全日本語化(「監視A(ピボット待ち)」等。summary.pyの
  STATUS_LABELS_JAと対で保守)。style.cssにサマリーカードのスタイル。
- 即時反映: 現行report.jsonにワンショットでsummary/momentumを注入済み
  (scratchpadスクリプト。vcp_detail由来のベース収縮行は次回daily実行から出る)。

### 検証
- pytest 232件全パス(214+新規18 test_summary.py)。
- preview_start(docs静的配信)で9247(WATCH_A)/319A(REJECTED)の実表示・コンソール
  エラー無しを確認。スクリーンショット確認済み。

### 次のステップ候補(未着手・ユーザー判断待ち)
- 厚み増強の追加データ候補: (A)会社予想(ガイダンス)EPS/売上と進捗率 —
  J-Quants /fins/summary のレスポンスに予想フィールドがあれば取得フィールド追加のみで
  可能(要実地確認)。(B)決算発表予定日 — J-Quants /fins/announcement(無料枠か要確認。
  現状は前回開示+75日の推定で代替済み)。(C)時価総額・市場区分 — listed_info+株式数。
  (D)信用残 — 有料プラン必要。

## 2026-07-12 (30): ファンダ欠落の原因特定とリペア(EDINET DB backlog再投入CLI)

### 要望(ユーザー原文)
> ファンダメンタルズが埋まらないのはなんでだろう
> 埋められるようにしたい

### 原因(データとgit履歴で確定)
- ユニバース997銘柄中**406銘柄が2025Q3止まり**(P1 184銘柄中93銘柄が該当)。
  5月開示の3月期本決算(2025Q4)が入っていない。
- J-Quants Freeは85日遅延で現在2026-04-17分まで → 5月開示はまだ来ない(8月上旬に自然回復)。
  補完役のEDINET DBは、**初回稼働日2026-07-08にパース不具合を段階修正しながら日次ジョブを
  11回再実行した**ため、修正完了「前」に消化されたbacklog約450銘柄(909→459)が
  成果ゼロのままキューから落ち、以後再試行されない穴になっていた
  (git履歴: store=0のままbacklogだけ減少 → 修正後の後半450件のみstoreが90ずつ増加)。
  backlog消化は「取得できたが0件採用」も消費済みとして落とす仕様(無限リトライ防止)のため。

### 変更内容
- `src/data/edinetdb.py`: リペア関数 `requeue_stale`(J-Quants+EDINET DB両ストアの最新
  checked_dateがstale_days=120日超の銘柄をbacklogへ再投入。ネットワーク不使用)+
  argparse CLI `--requeue-stale [--stale-days N]` を追加。
- `tests/test_edinetdb.py`: `test_requeue_stale_*` 4件追加。
- `HANDOFF.md` §5: リペアCLIの説明を追記。
- **実行済み**: `python -m src.data.edinetdb --requeue-stale` → **580銘柄をbacklogに再投入**
  (欠落406銘柄中385 + checked_dateが古い他銘柄。残り21銘柄は非3月決算でデータ最新のため対象外)。

### 検証
- pytest 214件全パス(210+新規4)。
- requeue後の data/edinetdb_state.json: backlog=580、last_events_date等は不変。

### 次のステップ(自動で進む)
- 日次GitHub Actionsが90req/日×約7日でbacklogを消化(P1銘柄優先ソート済み)。
  **完全回復は7/18頃**。P1の欠落93銘柄は初日〜2日目でほぼ埋まる見込み。
- 万一また埋まらない銘柄が出たら同CLIを再実行すればよい。

## 2026-07-12 (29): ZigZag適応的粗視化のバックテスト検証(実装は未着手・ユーザー判断待ち)

### 要望(ユーザー原文)
> (8550が目視ではきれいな収縮に見えるのにVCP判定NGな件について) いったんバックテストしてください。あなたの正直な意見も併せてよろしくニキ

### 調査(8550の落選原因)
- ZigZag閾値 max(3%, 1.2×ATR20)=4.05% が低ボラ・高頻度振動の銘柄に細かすぎ、
  101日のベースで収縮を**31個**検出 → V1/V2/V7が連鎖的にNG(測定粒度の問題)。
- ただし V5(出来高ドライアップ、直近10日=50日平均の105%)と、粗く測っても
  最終押し12%>10%(V4)は実態としてNG。目視の収縮(15日レンジ27.9%→12.1%)は本物だが
  ミネルヴィニの買い基準では時期尚早、が結論。

### 検証した変更案とバックテスト結果
- 変更案: 収縮数が上限6を超えたらZigZag閾値を1.3倍ずつ引き上げて再測定(適応的粗視化)。
  V1-V7の要求水準は不変。基準緩和ではなく測定スケールの補正。
- 全ユニバース・400営業日・週次グリッド: 現行75件(期待R 0.92、ストップ31.4%)→
  適応85件(期待R 0.94、ストップ30.0%)。**現行の75件は全て包含(失うものゼロ)**。
  増分10件: 期待R 1.04、ストップ到達20%、+20日勝率80% — 質は現行合格組と同等以上。
- 当日スナップショット: REJECTED→WATCH_Bが2件(8550栃木銀行、5830いよぎんHD)のみ。
  WATCH_A増加はゼロ。
- 留保: 増分n=10で証拠力は弱い。増分に地銀が多くレジーム依存の可能性。
- レポート: data/backtest/vcp_adaptive_zigzag_20260712.md

### 次のステップ
- ユーザーが採用を決めたら src/screener/vcp.py の evaluate_vcp に適応ループを実装
  (試作コードはscratchpadに作成済み、ESCALATION_FACTOR=1.3, MAX_ESCALATIONS=8)+テスト追加。

## 2026-07-12 (28): VCP MUST条件V2の緩和(monotonic_tolerance 1.0→1.2)— バックテスト検証に基づく

### 要望(ユーザー原文)
> dashboardの銘柄が監視〕8条件合格・セットアップ形成待が多くなりがちでそれより上が少ないのはチェックが機能している以上仕方がないのか？
> 現在の上昇トレンドでこの状況は少なく感じるが妥当か
> (診断の結果、V7/V2緩和+バックテスト比較を提案 →) １，２両方やって

### 調査(184銘柄=P1全件の実データ診断)
- 内訳: TOO_RECENT 85 + IMMATURE 45 = 130件(71%)は「高値から15日未満でベース未形成」。
  上昇初動で銘柄が走っている局面の構造的な結果であり妥当(=将来のセットアップ在庫)。
- REJECTED 53件の落選理由: V7(安値切り上げ、tol 0.99)が52/53件(98%)、V2(収縮単調減、
  tol 1.0=許容ゼロ)が48/53件(91%)に集中。両者が重複して効くため単独緩和では救済ほぼゼロ。

### バックテスト検証(全ユニバース992銘柄・直近400営業日・週次グリッド)
- 現行設定: セットアップ57件、期待R 0.82、ストップ到達率27.8%。
- V2単独緩和(1.0→1.2)の追加分18件: 期待R 1.25、ストップ到達率43.8% — 現行合格組より良好。
  ZigZag測定ノイズ程度の深さ逆転による誤落選の救済と判断し**採用**。
- V7単独緩和(0.99→0.95)の追加分44件: 期待R 0.33、+20日勝率38.5%、ストップ到達率55% —
  明確に質が低い。「ベース内で安値を切り下げる銘柄は実際に弱い」ことがデータで裏付けられたため**不採用**。
- 詳細レポート: data/backtest/vcp_tolerance_comparison_20260712.md

### 変更内容
- config.yaml: `vcp.monotonic_tolerance: 1.0 → 1.2`(経緯コメント付き)。swing_low_toleranceは0.99のまま。
- data/backtest/vcp_tolerance_comparison_20260712.md: 検証レポートを記録として追加。

### 検証
- pytest 210件全パス(test_vcp のV2逆転テストは深さ18% vs 12%×1.2=14.4%なので判定不変)。
- 新設定での当日スナップショット: WATCH_A 0件→1件(9247、3W 6/7/7、vcp_score 51.4)。
  TOO_RECENT/IMMATURE/REJECTEDの件数は不変(85/45/53)。反映は次回daily実行後。

## 2026-07-11 (27): 全タスク完了後の仕上げ(HANDOFF.md §12更新・最終テスト確認)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施、HANDOFF_TASKS.txt「最後に」節)

### 変更内容
- `HANDOFF.md` §12「未対応・次のタスク候補」:
  - 項目7(daily.ymlガード)を完了済みに更新(取り消し線+2026-07-11復旧の記載)。
  - 項目5(P2-P4出力停止)を完了済みに更新。
  - 項目8として今回追加した新機能(ポジション管理/バックテスト/地合いシグナル)の
    次候補(既知の制約・フェーズ2アイデア)を追記。
- §2構成図・§10テストコマンドの試験件数を210件に更新(既に各タスクで個別反映済みの
  内容の最終確認)。

### 検証
- `python -m pytest tests/ -q` 210 passed (2026-07-08時点152件から58件増加: タスク2/3の
  重複解消テスト+タスク5〜8の新規テスト)。
- `node --check docs/assets/app.js`: このセッションの環境に`node`コマンドが存在しなかった
  ため、代替として`preview_start`でdocs/を配信し`preview_console_logs`でJSエラー無しを
  都度確認する方法を使用(タスク4/5/6/7それぞれで実施済み)。最終確認としてダッシュボード
  リロード+セクターマップ切替でconsole error無しを再確認。
- タスク1〜8すべてlog.mdにエントリ済み(このエントリの直前、通し番号19〜26)。
- コミット/push未実施分の確認: タスク1〜8はそれぞれ個別コミット済み(計8コミット)。
  push はユーザーが実行(サンドボックスからは不可)。

### 次のステップ(ユーザー向け)
- push後、GitHub Pagesの実サイトで以下を目視確認してください:
  - ダッシュボード最上部に地合いシグナルカード(攻め/中立/守り)
  - Dockに「保有」ビュー、個別株画面にポジションサイジング計算機
  - 翌営業日のdaily.ymlが1日1回だけフル実行される(2回目以降はskip)こと
- `manual/positions.csv`に実際の保有銘柄を追記すると保有ビューが機能します(現状ヘッダのみ)。
- `python -m src.backtest --limit 150 --days 400`のような形でバックテストを手元実行できます
  (所要時間は銘柄数×日数に比例、フル実行は数十分想定)。

## 2026-07-11 (26): 簡易バックテスト(ブレイクアウト成功率の実績検証CLI)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク8)

### 変更内容
- `src/backtest.py` 新規(CLI: `python -m src.backtest [--days 400] [--limit N] [--rs-min N]
  [--vol-mult N] [--stop-pct N]`)。GitHub Actions化はしない(ローカル/手動実行のみ)。
  - `load_universe_frames(limit)`: data/universe.json の銘柄について data/prices/{code}.parquet
    を読み `compute_all` で指標付与(RS_LOOKBACKS最大252に満たない銘柄はスキップ)。
  - `build_rs_by_date(frames)`: 全銘柄のrs_rawを日付×銘柄でピボットし、各日付の行で
    `rank(pct=True)`して1-99に変換(`indicators.rs_percentile_rank`と同じ式のpoint-in-time近似)。
    ma/atr/52w高安/rs_rawは全てbackward-lookingなrolling計算のみのため、フルhistoryに対して
    事前計算してからスライスしても未来データが混入しない性質を利用。
  - `scan_setups`: 週次(5営業日ごと)グリッドで、トレンドテンプレート合格
    (`trend_template.check_must_conditions`をその日の行に直接適用)かつRS>=rs_minの銘柄のみ
    `vcp_mod.evaluate_vcp(df.iloc[:i+1], config)` を実行(計算量削減+ルックアヘッド対策)。
    status=="WATCH_A"かつcontractionsありをセットアップとして記録、同一銘柄でpivotが近い
    (±1%以内)ものは1件に統合。
  - `find_breakout_index`: セットアップ後60営業日以内にcloseが最初にpivotを上抜けた行
    (無ければ「不発」)。`is_strong_breakout`: 出来高/vol_ma50 >= breakout_vol_mult。
  - `measure_performance`: ブレイク翌日始値(無ければブレイク日終値)を仮エントリー価格とし、
    終値ベースのストップ到達で以降のリターンを固定。+5/+10/+20営業日リターン・最大ドロー
    ダウン・R倍数(`(exit-entry)/(entry-stop)`)を算出。
  - `run_backtest`/`build_report_markdown`/`write_report`: セットアップ検出数(月別)、
    ブレイク発生率、強/弱ブレイク別の成績・勝率・ストップ到達率・期待Rをまとめ
    `data/backtest/backtest_YYYYMMDD.md`に保存(標準出力にも表示)。レポート冒頭に
    ユニバースの生存者バイアスを既知の限界として明記。
- `tests/test_backtest.py` 新規8件: `find_breakout_index`(検出/不発/max_wait_days境界)、
  `is_strong_breakout`(出来高倍率の閾値)、`measure_performance`(次日始値エントリー、
  ストップ到達後のリターン固定、次日データが無い場合のフォールバック)を合成データでカバー。
  仕様どおり`scan_setups`(VCP統合部分)は自動テスト対象外。
- `HANDOFF.md`: §2構成図・§10テスト・新設§13(簡易バックテストCLI)を追記、
  §12-7(daily.ymlガード)を完了済みに更新。

### 検証
- `python -m pytest tests/ -q` 210 passed。
- 実データでの限定実行を2回確認: `--limit 8 --days 120`(0件検出、完走・レポート生成を確認)、
  `--limit 150 --days 400`(約25秒で完走、8件のセットアップ・8件のブレイク発生・強/弱別の
  成績が妥当な形でレポートに出力されることを確認)。生成された確認用レポートはコミットに
  含めない(スモークテスト用に削除済み)。

## 2026-07-11 (25): ポジションサイジング計算機(個別株画面)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク7)

### 変更内容
- `docs/index.html`: view-stock内、ファンダメンタルズカードの直前に`#sizing-card`
  (ポジションサイジング)を新設。入力: 総資金(円、`#sizing-capital`)、1トレードあたり
  リスク%(プリセットボタン0.5/0.75/1.0/1.25%、`#sizing-risk-toggle`、既存`.segmented`流用)。
  `app.js?v=18→19`, `style.css?v=16→17`。
- `docs/assets/app.js`:
  - `setupSizingCalculator(stock)`: `initStockPage`から`renderStockMeta`の直後に呼ぶ。
    localStorage(`minervini_sizing_settings`)から総資金・リスク%を復元し入力欄に反映。
    イベントリスナーはモジュール変数`sizingWired`でガードして1回だけ登録(SPAで銘柄切替の
    たびに積み上がらない。settingsBtn.dataset.wiredと同じ発想)。
  - `renderSizingResult(stock)`: `stock.buy_stop`/`stock.stop_loss`のどちらか欠損(watchlist銘柄等)
    なら「セットアップ未確定のため計算不可」。riskPerShare = buy_stop - stop_loss、
    許容損失額 = 総資金×リスク%、理論株数 = 許容損失額/riskPerShare、
    発注株数 = floor(理論株数/100)*100(単元100株切り下げ)。100株未満なら
    「リスク許容内で1単元買えません(1単元の損失=X円=資金のY%)」。それ以外は
    投入額・資金比・実損失額を表示、資金比25%超で注意表示、50%超で強い警告色。
  - `initStockPage`の先頭リセット処理に`#sizing-result`のクリアを追加。
- `docs/assets/style.css`: `.sizing-inputs`/`.sizing-field`/`.sizing-output`/`.sizing-warn`
  (`-strong`修飾子でdanger色)を新設。

### 検証
- preview_evalで buy_stop=2937, stop_loss=2797.44, 資金1000万円, リスク1% の計算例を確認:
  riskPerShare=139.56, 発注株数=700株, 投入額=2,055,900円, 資金比=20.6%, 実損失=97,692円
  (仕様書の期待値と完全一致)。スクリーンショットで表示も確認。
  セットアップ未確定銘柄(buy_stop/stop_loss=null)で「計算不可」表示を確認。
  リスクプリセット0.75%クリック→localStorage保存→別銘柄へのsetupSizingCalculator呼び出しで
  総資金・リスク%が復元されることを確認(SPA銘柄切替をまたいだ永続化)。
- `python -m pytest tests/ -q` 202 passed(純フロント機能のためバックエンドテストへの影響なし)。

## 2026-07-11 (24): ポジション管理(保有銘柄ビュー + 売りシグナル)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク6)

### 変更内容
- `manual/positions.csv` 新規(ヘッダのみ): `code,entry_date,entry_price,shares,initial_stop,current_stop,memo`。
  書き込みUIは作らない方針(`passkeyAuthEnabled: false`と同じ思想。GitHub web編集/ローカル編集で運用)。
- `src/report/positions.py` 新規:
  - `load_positions_csv(path=None)`: CSVパース。code空/日付・数値パース不能な行はスキップして警告
    (`load_fundamentals_csv`と同じ流儀)。
  - `build_positions_report(positions, indicator_by_code, name_by_code, today=None)`: 各ポジションの
    現在値・pl_pct/pl_jpy・`r_multiple = (close-entry_price)/(entry_price-initial_stop)`
    (entry_price<=initial_stopは警告してNone)・`dist_to_stop_pct`・`days_held`(暦日)・
    `sell_signals`(STOP_BREACH/MA50_BREAK/MA200_BREAK/TAKE_PROFIT_ZONE(2R到達)/
    BREAKEVEN_READY(1R到達かつストップ未引き上げ))を計算。indicator_by_codeに無いcodeは
    `data_missing: true`+数値null。
  - `write_positions_json(report, path=None)`: docs/data/positions.json書き出し。
- `src/pipeline.py`: indicator_by_code構築後、fundamentals処理の直後でtry/except実行
  (失敗しても本体は止めない)。csv警告とbuild警告を結合してから書き込む。
- `tests/test_positions.py` 新規14件: CSVパース(欠損/不正行/空memo)、R計算、各sell_signal発火条件、
  data_missing、entry_price<=initial_stopの異常系をカバー。
- `tests/test_pipeline.py`: `wired`フィクスチャにpositions_mod用モック追加。
  `test_run_daily_writes_positions_report`追加(pipeline経由でpositions.json生成が呼ばれ、
  R計算等が反映されることを確認)。
- `docs/index.html`: `#view-positions`(保有ポジション表 + 空状態メッセージ + 警告表示)新設、
  Dockナビに「保有」ボタン(bi-briefcase-fill)追加、`#positions-warning`バナー追加(market-overviewの上)。
  `app.js?v=17→18`, `style.css?v=15→16`。
- `docs/assets/app.js`: `VIEWS`に`"positions"`追加、router からの`initPositionsView()`呼び出し。
  `initPositionsView()`がpositions.jsonを描画(列: コード/銘柄名/建値/現在値/損益%/R/ストップ/
  ストップまで%/保有日数/シグナル、シグナルありの行を上にソート、data_missing行は`.row-static`で
  クリック不可、0件時はGitHub編集画面へのリンク)。`SELL_SIGNAL_LABELS`で日本語バッジ化。
  `renderPositionsWarningBanner(positionsData)`をinitDashboardから呼び、保有銘柄にsell_signalsが
  1件でもあれば`#positions-warning`に「⚠ 保有N銘柄に売りシグナル」+ `#positions`へのリンクを表示。
- `docs/assets/style.css`: `.sell-signal-badge` + `.signal-badge-danger/-warn/-accent`修飾子を新設。
- `HANDOFF.md`: §2構成図・§3パイプライン手順・§7フロント(SPA構造/新設「ポジション管理」節)を更新。

### 検証
- `python -m pytest tests/ -q` 202 passed。
- preview_evalで空状態(保有0件のメッセージ)、fetchモックによるダミー3件(通常/売りシグナル複数/
  data_missing)の表表示、バッジ色分け、`.row-static`付与、ダッシュボードの警告バナー表示をそれぞれ
  スクリーンショット確認。

## 2026-07-11 (23): 地合いシグナル(市場ブレッドス指標 + 攻め/中立/守りの3段階表示)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク5)

### 変更内容
- `config.yaml`: `market_signal` セクション新設(`green_pct_above_ma200: 0.50`,
  `red_pct_above_ma200: 0.30`)。
- `src/report/market_signal.py` 新規:
  - `compute_breadth_stats(latest_by_code)`: pct_above_ma200/pct_above_ma50(MAがNaNの銘柄は
    分母から除外)、new_high_count/new_low_count(high>=high_52w / low<=low_52w)を計算。
  - `compute_index_trend(index_df)`: TOPIX日足終値からMA50/MA200を計算し
    index_above_ma50/index_above_ma200/index_ma200_slope_up(MA200が21営業日前より上向き)を
    判定。221営業日未満のデータならNone(判定不能)。
  - `compute_market_signal(latest_by_code, config, index_df=None)`: 上記を合成し
    green(攻め)/yellow(中立)/red(守り)+ reasons(日本語根拠文字列)を返す。
    index_df省略時は`indices_mod.load_cache("topix")`を読む。
- `src/pipeline.py`: ヒートマップ生成後・update_breadth呼び出し前に
  `market_signal_mod.compute_market_signal(latest_by_code, config)` をtry/except実行
  (失敗しても本体は止めない)。結果を `update_breadth(..., market_signal=signal_result)` へ渡す。
- `src/report/build_site.py :: update_breadth`: `market_signal: dict | None = None` 引数を追加、
  Noneでなければ `entry.update(market_signal)`(priority_countsと同じパターン)。
- `docs/assets/app.js`: `renderMarketSignal(breadth)` 新設。breadth.jsonのhistory最新エントリの
  `signal`を読み `#market-signal-card` へ色付きラベル+根拠箇条書き+MA200上回り率/新高値/新安値を
  描画。redの時は「⚠ 新規エントリーは控えるのが原則です。」を追加表示。`signal`フィールドが
  無い(旧データ)場合はカード非表示。`initDashboard`から`renderHeader`直後に呼び出し。
- `docs/index.html`: `#view-dashboard`内、`#market-overview`の直前に
  `<div id="market-signal-card" class="market-signal-card" hidden></div>` を新設。
  `app.js?v=16→17`, `style.css?v=14→15`。
- `docs/assets/style.css`: `.market-signal-card` + `.signal-green/-yellow/-red` 修飾子を新設。
- `tests/test_market_signal.py` 新規: green/yellow/red境界、指数データ欠損時yellow、
  NaN銘柄の分母除外、new_high/new_low正しくカウント、を計11件でカバー。
- `tests/test_pipeline.py`: `wired`フィクスチャに`market_signal_mod.compute_market_signal`の
  固定値モックを追加(TOPIXキャッシュの実ファイルに触れないため)。end-to-endテストに
  breadth entryへsignalフィールドが載ることのアサーションを追加。
- `HANDOFF.md`: §3に地合いシグナルの節を追記、§7ダッシュボード節に
  renderMarketSignal/renderStalenessWarningの説明を追記、§2リポジトリ構成に
  market_signal.py追記、キャッシュバスター表を更新。

### 検証
- `python -m pytest tests/ -q` 187 passed。
- preview_evalでgreen/yellow/red全3色+シグナル無し時のカード非表示をスクリーンショット確認
  (市場概況カードの上に表示、redでは注意文言も表示)。

## 2026-07-11 (22): report.jsonの軽量化(P2〜P4レコード出力停止)+ データ鮮度警告バナー

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク4)

### 変更内容(P2-P4出力停止)
- `src/pipeline.py`: `run_daily()` 内、`pr_eval["priority"] != 1` ブロックで
  `assemble_priority_record` を呼んでいた処理を削除し `continue` のみに変更
  (フロントは `priority===1||null` のP1銘柄しか表示しておらず、P2〜P4は受信して
  捨てているだけだったため)。`priority_counts`(pr_counts)は `priority_by_code` から
  独立に計算されるため breadth.json の p1_count〜p4_count には影響しない。
- `src/report/build_site.py`: 完全に未使用になった `assemble_priority_record` を削除。
  `attach_priority` はP1レコードで引き続き使用するため維持。

### 変更内容(鮮度警告バナー)
- `docs/assets/app.js`: `getStalenessInfo(generatedAt, now)` を新設。JSTシフト時計
  トリック(`now.getTime() + 9h` を UTC getterで読む)で、直近の平日(土日はFriday扱い)の
  21:00 JSTを過ぎてもその平日日付のデータが無い場合に `{stale: true}` を返す。
  `renderStalenessWarning(report)` が `#staleness-warning` の表示/非表示と文言を制御
  (`initDashboard` から `renderHeader` の直後に呼び出し)。祝日は考慮しない
  (バナー文言に「祝日明けは誤検知の場合あり」と明記)。
- `docs/index.html`: `#view-dashboard` 内、`#market-overview` の直前に
  `<div id="staleness-warning" class="warning-banner" hidden></div>` を新設。
  `app.js?v=15→16`, `style.css?v=13→14`。
- `docs/assets/style.css`: `.warning-banner` クラス新設(danger色ボーダー/背景、
  既存 `.p1-warning` は無改修)。

### 検証
- `python -m pytest tests/ -q` 176 passed(pyarrow欠如によるtest_indices失敗は今回発生せず)。
- preview_evalで `getStalenessInfo` の境界値を確認: 平日21:00前は非stale、21:00後に
  当日データ無しでstale、土曜日はFriday扱いでFridayデータありなら非stale・無ければstale。
  `renderStalenessWarning({generated_at:"2026-07-01T10:00:00+09:00"})` でバナー実表示を
  スクリーンショット確認(市場概況の上に赤枠で表示)。実データ(2026-07-10生成)では
  バナー非表示を確認。

## 2026-07-11 (21): status_history.jsonの同日重複を解消(record_statusの同日replace)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク3)

### 変更内容
- `src/screener/entry.py :: record_status`: 同じ `date` のエントリを削除してからappendする
  よう変更(1日1件を前提とする `extended_cooldown_ready`/`compute_breakout_success_rate`/
  `keep_days=90`切り詰めの前提を守る)。
- `tests/test_entry.py`: `test_record_status_same_date_replaces_instead_of_appending` 追加
  (同日2回呼んでもエントリは1件、値は後勝ち)。
- `data/status_history.json`: ガード無効化中の同日重複(1銘柄24件→ユニーク日付6件)を
  ワンショットクリーンアップ(各codeで同一dateは最後のエントリのみ残す)。

### 検証
- `python -m pytest tests/test_entry.py -q` 23 passed。
- クリーンアップ後、対象銘柄で `len(dates) == len(set(dates))` を確認。

## 2026-07-11 (20): breadth.jsonの同日重複エントリを解消(append → 同日replace)

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク2)

### 変更内容
- `src/report/build_site.py :: update_breadth`: appendの前に同じ `date` のエントリを
  historyから除去するよう変更(同日再実行は最新値で上書き)。
- `tests/test_build_site.py`: `test_update_breadth_same_date_replaces_instead_of_appending`
  追加(同日2回呼んでもhistoryは1件、値は2回目のもの)。
- `docs/data/breadth.json`: ガード無効化中の同日重複(24件→ユニーク日付6件)をワンショット
  クリーンアップ(同一dateは最後のエントリのみ残し、date昇順で保存)。

### 検証
- `python -m pytest tests/test_build_site.py -q` 8 passed。
- クリーンアップ後、`len(dates) == len(set(dates))` を確認(6件、重複無し)。

## 2026-07-11 (19): daily.ymlの「当日実行済みチェック」ガードを復旧

### 要望(ユーザー原文)
> 全部やりたい。(2026-07-11の改善提案タスク一括実施の一部。HANDOFF_TASKS.txt タスク1)

### 変更内容
- `.github/workflows/daily.yml`: 「Check if already run today」ステップを、2026-07-08に
  EDINET DB検証のため一時無効化していた `skip=false` 固定から、元の `git log --format=%s -20`
  による当日コミット判定に復旧。無効化時のコメントも削除。
- `fetch-depth: 0` はガードのgit log参照に必要なため変更なし。

### 検証
- `python -c "import yaml,io;yaml.safe_load(io.open('.github/workflows/daily.yml',encoding='utf-8'))"` OK。

## 2026-07-09 (18): 〔本命〕昇格基準を「データ存在」から「ファンダ強度合格」に改定

### 要望(ユーザー原文)
> ファンだ確認済み の基準を見直して欲しい。いま確認済みのものを見て ファンだ別に強くないけどなんでここにいるんだろう

### 原因
- 旧 `fund_coverage_tier`: quarters が1件でもあれば confirmed。J-Quants自動取得で全997銘柄に
  データが入った結果、事実上の全員confirmed化。実例: 8418 山口FG が直近EPS YoY **-57.4%**・
  eps_accel_slope -30.6 なのに〔本命〕にいた。

### 変更内容 (AskUserQuestionで確認: 基準=本家準拠、弱い銘柄=候補プールに「ファンダ弱」表示)
- `config.yaml` fundamentals節: `confirmed_eps_yoy_min: 25` / `confirmed_rev_yoy_min: 20` 追加。
- `src/data/fundamentals.py :: fund_coverage_tier`: 直近EPS YoY≥+25% **かつ** 売上YoY≥+20%
  (latest_yoy_growth使用)で初めて confirmed。YoY計算不能(前年比較なし/前年値≤0)は pool。
  戻り値に `fund_strong` / `fund_eps_yoy` / `fund_rev_yoy` 追加。
- `score_stock`: full_score/加速slopeは tier ではなく `fund_coverage != "none"` で計算
  (pool落ちした銘柄でも個別株画面・コピーで見えるように)。
- `src/report/build_site.py :: assemble_stock_record`: fund_strong/fund_eps_yoy/fund_rev_yoy をreport.jsonへ。
- `docs/assets/app.js`: fundStatusLabel が基準未達を「ファンダ弱 (EPS -57.4%)」表示。
  コピー機能に「ファンダ強度判定: 合格/不合格」行を追加。TIER_COPY_LABELS文言更新。
- `docs/index.html`: 本命/候補のtier-note文言更新、`app.js?v=14→15`。
- `skills/minervini-analysis/SKILL.md`: ティアの意味を新基準に更新。
- テスト更新+回帰テスト追加(減益銘柄はfull coverageでもpool)。tests 173 passed
  (test_indices 1 failは砂箱のpyarrow欠如で無関係)。

### 検証
- 実データで 8418 → `{tier: pool, fund_strong: False, fund_eps_yoy: -57.4, fund_rev_yoy: 42.9}` を確認。
- 新基準ならファンダ保有997銘柄中 confirmed資格は104銘柄(10%)。
- 反映は次回バッチ実行後(report.json再生成が必要)。

## 2026-07-09 (17): 個別株画面に「分析用データをコピー」ボタン + minervini-analysisスキル追加

### 要望(ユーザー原文)
> 個別銘柄にデータコピーボタンを追加
> コピーした情報をclaudeに貼り付けて相談したい。
> コピーできる内容の精査とボタンの追加実装
> claudeが貼り付けられた情報を元にminervini手法で読み解くスキルの作成

(AskUserQuestionで確認済み: 内容=全部盛り、形式=Markdown、スキル置き場=Cowork用スキル)

### 変更内容
- `docs/index.html`: `#view-stock` の stock-meta 直下に
  `<div class="stock-actions"><button id="copy-stock-data-btn" hidden>分析用データをコピー</button></div>` を追加。
  キャッシュバスト `app.js?v=13→14`, `style.css?v=12→13`。
- `docs/assets/app.js`:
  - `buildAnalysisMarkdown(stock, chart, report, fundEntry, breadthLast, indicesData)` 新設。
    自己完結Markdownを生成: ヘッダ(ティア/ステータス=STATUS_LABELS併記/セクター強度・方向)、
    価格・テクニカル表(終値/RS/ピボット/逆指値/損切り/リスク%/ピボット距離/52週高値距離
    〔ダッシュボード同様マイナス表記〕/MA乖離50・150・200/EPS加速slope)、スコア4種、
    8条件✓✗(MUST_FLAG_LABELS流用)、VCP V1〜V7✓✗+footprint、直近20営業日OHLCV表+
    5/20/60日騰落率+出来高10日/50日平均比(≤0.8でドライアップ注記)、ファンダ四半期表
    (既存のshiftFiscalQuarterYoy/growthPct/formatEps/formatRevenue流用でYoY計算済み)、
    市況(indices前日比表+breadth最新行の8条件合格率+p1_scarce時の警告文言※P1という語は不使用)、
    末尾にSEPA手法で分析せよという1行の指示文。
  - `setupStockCopyButton(stock, chart, report)`: initStockPage内(`if (!chart) return`より前)から
    呼び出し。stockが無ければ非表示。クリック時に fundamentals_public.json / breadth.json /
    indices.json を no-store で追加fetch(いずれも失敗はnullで握りつぶし、ある分だけで生成)→
    生成→コピー→ボタン文言を「コピーしました/コピー失敗」に1.8秒トグル。
    `btn.onclick` 上書き方式でSPA銘柄切替時のリスナー累積を回避。
  - `copyTextToClipboard`: navigator.clipboard優先、非対応時はtextarea+execCommandフォールバック。
  - ヘルパー: `copyNum`/`copySignedPct`/`copyFlagLines`/`closeChangePct`/`volumeAvg`、`TIER_COPY_LABELS`。
  - initStockPage冒頭のリセット処理に copyBtn.hidden = true を追加。
- `docs/assets/style.css`: `.stock-actions { margin: 6px 0 10px; }` のみ追加(ボタンはグローバルbutton様式)。
- `skills/minervini-analysis/SKILL.md` 新規: 貼り付けられたコピー出力をSEPA手法で読み解くスキル。
  入力データの構造説明(ティア/ステータスの意味、フットプリントの読み方、Q4=FY通期・最古Q4の
  累計値歪みという既知の癖)、分析手順(ステージ判定→VCP品質→ファンダCode33→エントリー計画→地合い)、
  出力フォーマット(総合判定4択+強み/弱み/アクション/免責)、禁止事項(データに無い数値の捏造禁止、
  古いデータでの当日判断禁止、断定予測禁止)。ユーザーがCoworkのスキルとして登録して使う想定。

### 検証
- `node --check docs/assets/app.js` OK。
- node vmサンドボックス(DOMスタブ)で実データ(8418)を通してbuildAnalysisMarkdownの出力を目視確認
  (8条件✓8個、20日OHLCV表、YoY計算、指数表、ドライアップ注記まで全セクション正常)。
- pytestは対象外(フロントエンド+スキルのみの変更)。

### 次のステップ
- ユーザーが実機で: 個別株画面→「分析用データをコピー」→Claudeに貼り付けて動作確認。
- スキルはリポジトリ内 `skills/minervini-analysis/` に置いたので、Coworkのスキルとして登録
  (設定→スキル→フォルダ追加 or プラグイン化)すれば貼り付け→即分析が効く。
- コミット/push未実施(ユーザーのローカルで実行)。

---

## 2026-07-08 (16): EDINET DB backlog優先順位付け -- P1〜P4ランク順にファンダ取得

### 要望(ユーザー原文)
> ok
>
> で、いま候補に上がっているやつのファンダを取り急ぎとりたいから先行して次の実行で取得するようにして

> あ、優先度を決めてファンダ取ってって話や。Tier1がなくても２、３の順でやってってこと

(初回実装は fund_coverage の〔候補〕(pool) tier を基準にしてしまったが、ユーザーの
真意は機能A(P1〜P4)のプライオリティ評価順にファンダを取得することだったので設計を
差し替えた。以下は最終版。)

### 変更内容
- `src/data/edinetdb.py`: `update_fundamentals_auto()`に`priority_by_code: dict[str, int] |
  None = None`引数を追加。backlog消化(step 3)の直前で
  `backlog.sort(key=lambda c: priority_by_code.get(c, 99))`を実行し、backlogをランク
  (P1=1〜P4=4、未指定コードは99)の昇順に並べ替える。二値(優先/非優先)ではなく
  ランクそのものでソートするため、「P1が無くてもP2→P3→P4の順で優先される」という
  要件を満たす。Pythonの`list.sort()`は安定ソートなので、同ランク内の相対順序
  (events検出順)は維持される。budget予算自体は変えない。
- `src/pipeline.py`: `priority_by_code`(機能Aのプライオリティ評価結果、P1〜P4は
  技術指標のみで決まりファンダメンタルに依存しないため、EDINET DB呼び出しより前の
  トレンドテンプレート直後の時点で当日分がすでに確定している)から
  `priority_rank_by_code = {code: ev["priority"] for code, ev in priority_by_code.items()}`
  を作り、EDINET DB呼び出しに`priority_by_code=priority_rank_by_code`として渡す。
  - 初回実装で懸念していた「今回実行分のtierは鶏と卵で未確定」という問題は、
    実はP1〜P4ランク自体には当てはまらなかった(ランクは価格/出来高等の技術指標のみで
    決まり、ファンダメンタル取得結果に依存しない)。そのため前回report.jsonを読む
    迂回策は不要と判明し、この実行自身のpriority_by_codeをそのまま使う設計に変更。
  - 呼び出し箇所: J-Quants自動取得の直後、EDINET DB自動取得の直前
    (`tanshin_by_code = edinetdb_mod.update_fundamentals_auto(codes, config,
    base_store=auto_by_code, priority_by_code=priority_rank_by_code)`)。

### テスト
- `tests/test_edinetdb.py`: `test_update_priority_by_code_reorders_backlog_by_rank`
  (P1が存在しない状態でもP2→P3→P4の順で優先消化されることを確認)、
  `test_update_priority_by_code_unlisted_codes_sort_last`(ランク未指定コードは
  rank=99扱いで後回し)、`test_update_priority_by_code_none_leaves_backlog_order_unchanged`
  (未指定時は従来通りbacklog順)を追加。
- `tests/test_pipeline.py`: `test_run_daily_passes_priority_rank_to_edinetdb`
  (run_daily実行時にEDINET DB呼び出しへ渡るpriority_by_codeの中身を検証)を追加。
  既存の`wired`fixtureのモックシグネチャを`priority_by_code=None`に更新。
- `python3 -m pytest -q` -- 173 passed, 0 failed。

### 次のステップ
- まだコミットしていない。ユーザーに commit/push 手順を提示すること。

## 2026-07-08 (15): UI変更 -- ファンダ入力ボタンを個別株画面に移設 + EPS/売上高成長率表を追加

### 要望(ユーザー原文)
> ファンだ入力は個別株画面に移して。ファンダは個別株画面に表示して。EPSと売上の伸び率が
> わかるように表にして

### 変更内容
- `docs/assets/app.js`:
  - `renderTable()`(ダッシュボード〔本命〕/〔候補〕/〔監視〕共通のtier table)から
    「ファンダ入力/編集」ボタン列(`fund-edit-btn`)と入力済みバッジ列を削除。
    ヘッダ行の空th、`authEnabled`変数、`actionTd`ブロックを丸ごと撤去。
  - 個別株画面(`#view-stock`)用に`renderStockFundamentals(code, name, reportGeneratedAt)`を
    新設。`docs/data/fundamentals_public.json`(3ソースマージ済み、`{code: {quarters:
    [{fiscal_quarter, eps, revenue}], monthly_yoy, checked_date}}`、四半期は昇順)を
    `cache: "no-store"`で取得し、四半期降順(直近が先頭)のテーブルを描画。
  - 前年同期比(YoY)は`shiftFiscalQuarterYoy("2025Q1") -> "2024Q1"`のように1年前の同じ
    四半期ラベルを引いて比較(`growthPct`)。Q4はFY(通期)相当として扱う既存の規約を
    そのまま踏襲。プラスは`--accent`(緑)、マイナスは`--danger`(赤)で色分け。
  - 表内に「ファンダ入力/編集」ボタン(`fund-edit-btn`クラスは維持 -- vault開錠状態の
    グローバルトグル`applyLockState()`が`.fund-edit-btn`をクラス名で拾う既存の仕組みに
    そのまま乗るため、page跨ぎでも自動で有効/無効が反映される)と入力済みバッジを設置。
  - `initStockPage()`から`renderStockFundamentals()`を呼び出すよう配線(チャート
    データが無い銘柄でもファンダ表は出るよう、`if (!chart) return`より前に呼ぶ)。
    保存後の再描画用に`window.MinerviniFundamentalsUI.onSaved`をこの関数内で
    `renderStockFundamentals`自身に差し替え(ダッシュボード側の`initDashboard`上書きより
    後に評価されるため、個別株画面が最後にアクティブだった場合はそちらが優先される)。
  - `fundamentals-modal.js`は無改修(`openFundamentalsModal(code, name)`が既に
    汎用だったため、呼び出し元を変えるだけで再利用できた)。
- `docs/index.html`: `#view-stock`に`<section class="detail-card">ファンダメンタルズ
  <div id="fund-detail-body"></div></section>`を追加(MUST条件/スコア内訳の下)。
  キャッシュバスト用クエリを`app.js?v=11→12`, `style.css?v=11→12`に更新。
- `docs/assets/style.css`: `.fund-detail-table`(モーダル用`table-layout: fixed`を
  上書きして通常の可変幅に)、`.yoy-positive`/`.yoy-negative`を追加。

### 既知の注意点(未修正・監視のみ)
- `fundamentals_public.json`の最古の`XXXXQ4`エントリはJ-Quants由来で通期(累計)値が
  入っているケースがあり(例: 8051の`2023Q4`はrevenue 5,068億円 vs 翌`2024Q1〜Q4`は
  各1,200〜1,300億円台)、その次の年の同`Q4`とのYoY比較が実態より大きく歪む可能性が
  ある。データ層(J-Quants取り込み)の既知の癖でありUI側の表示バグではないため、
  今回は対処せず現状の値をそのまま表示している。将来ファンダ表の見た目で違和感が
  出たら、この記事を起点に調査すること。

### 検証
- `node -c docs/assets/app.js` / `node -c docs/assets/fundamentals-modal.js` 構文OK。
- `docs/data/fundamentals_public.json`をpython3でパースし、期待スキーマ
  (`quarters`昇順、`fiscal_quarter`/`eps`/`revenue`)を確認。
- pytestは対象外(フロントエンドのみの変更)。

### 次のステップ
- ユーザーに実際のダッシュボード/個別株画面を見てもらい、表の見た目・列幅・
  YoY表示のスマホでの見え方を確認してもらう。
- コミット/push未実施(このセッションではmountedリポジトリへのgit書き込みを
  行わない方針を継続)。

## 2026-07-08 (14): record_to_point本修正 -- 実データ68フィールドの全ダンプが取れた

### 経緯
- (13)の1フィールド1行診断を本番実行したところ、今度こそ68フィールド全件が途切れず
  取得できた(code 9024の`quarter=4`の決算短信サンプル)。これで長年の推測フィールド名を
  実データで確定できた。

### 判明した実スキーマ(推測との差分)
- `quarter`: 文字列("Q1"/"FY"等)ではなく**整数1〜4**(FY相当=4)。
- `fiscal_year_start`相当のフィールドは**存在しない**。代わりに`fiscal_year_end`
  (会計年度の期末日、例`'2026-03-31'`)のみ存在する。
- `disclosure_date`は**RFC2822形式**(例`'Thu, 14 May 2026 00:00:00 GMT'`)。ISO形式
  という当初の推測は誤り。
- `eps`/`revenue`はバレキーで存在し、値は決算短信の通期/累計値と整合(revenue=513286は
  百万円単位と解釈すると通期売上と一致 -- 単位換算×1,000,000の推測は正しかった)。

### 修正
- `src/data/edinetdb.py`:
  - `_resolve_quarter_n(rec)`を新設。整数1〜4をそのまま採用し、文字列ラベル
    ("Q1"〜"Q4"/"FY")にも後方互換で対応。
  - `_fy_start_from_fy_end(fy_end)`を新設。`fiscal_year_end`から1年前+1日を算出して
    fy_startとする(日本の決算短信は原則12ヶ月決算という前提)。
  - `_resolve_fy_start`の優先順位を変更: ①`fiscal_year_end`系フィールドから逆算
    (新規・最優先) → ②`fiscal_year_start`系フィールドの直接値(未確認だが将来の保険として
    維持) → ③`_estimate_fy_start`による開示日からの推定(最終フォールバック)。
  - `_parse_disclosure_date(raw)`を新設。RFC2822形式を`email.utils.parsedate_to_datetime`
    でパースし、ISO日付文字列に正規化(ISO形式で来た場合の後方互換も維持)。
  - `record_to_point`をこれら新ヘルパー経由に書き換え。
  - `update_fundamentals_auto`内の調査用診断print(全フィールド1行ずつダンプ)は
    役目を終えたため撤去し、軽量なキー一覧のみのフォールバック診断に戻した
    (将来また噛み合わなくなった場合の気付き用として維持)。
  - モジュール冒頭のdocstringを「未検証」から「実地確認ステータス」に更新、全項目
    確認済みである旨を記載。
- `tests/test_edinetdb.py`: 実スキーマ(整数quarter、fiscal_year_end、RFC2822
  disclosure_date)を反映した新規テスト7件追加(`_fy_start_from_fy_end`/
  `_parse_disclosure_date`/`_resolve_quarter_n`の単体テスト含む)、診断print
  アサーションを新フォーマットに更新。フルスイート169件全パス。

### 次にやること
- コミット/push → daily.yml再実行 → `data/edinetdb_auto.json`に実際にデータが
  入るか確認 → ダッシュボードの「ファンダ入力/編集」モーダルにEDINET DB分の
  数値が表示されるか目視確認(revenue単位・eps値が決算短信の実際の数値と一致するか)。
- 確認が取れたら、まだ保留中の「当日実行済みスキップ」ガード再有効化
  (`.github/workflows/daily.yml`、現在`skip=false`固定)に着手する。

---

## 2026-07-08 (13): (12)の2行診断もまだ途中で切れた → 1フィールド1行方式に変更

### 経緯
- (12)のfixを本番実行したところ、2行に分けてもまだ途中で切れた。
  - 1行目(全キー一覧, `sorted(sample.keys())`): `'inte` の途中で切断。
  - 2行目(候補フィールドのみ): `'forecast_operating_inco` の途中で切断。
  - つまりGitHub Actionsのログ行長制限は、68キーのリストや候補フィールドのdict reprでも
    まだ長すぎる(1000〜2000文字程度でも切れる)水準らしい。ただしこの2回の切断で以下は
    確認できた: `eps`(バレキー、値805.05で存在)、`fiscal_year_end`(FY末日、例
    `'2026-09-30'` — `_FY_START_FIELD_CANDIDATES`のどれとも一致しない)、`disclosure_date`
    がRFC2822形式(`'Thu, 14 May 2026 00:00:00 GMT'`、ISO形式ではない)。
    `quarter`相当のフィールドはまだ未確認(アルファベット順で`inte...`より後ろにあるはず)。

### 修正
- `src/data/edinetdb.py` `update_fundamentals_auto`: 診断printを1フィールド1行方式に変更。
  `for k in sorted(sample.keys()): print(f"EDINET DB:   {code}.{k!r} = {sample[k]!r}")`。
  レコードの総フィールド数(68件など)に関わらず、各行は必ず短くなるので途中で打ち切られる
  ことがなくなる(打ち切られても、それまでに出力済みの行は失われない)。
- `tests/test_edinetdb.py`: `test_update_prints_sample_when_records_fetched_but_none_usable`の
  アサーションを`"'period': 'Q1'" in out`(dict repr形式)から`"'period' = 'Q1'"`
  (新フォーマット)に修正、`eps_value`の行も追加確認。フルスイート163件全パス。

### 次にやること
- ユーザーにコミット/push依頼 → daily.yml再実行 → 今度こそ68フィールド全部を1行1件で確認 →
  `quarter`相当のフィールド名、`eps`/`revenue`が単一四半期かYTD累積か、fy_start相当の
  フィールド(なければ`fiscal_year_end`から逆算する方式に切替検討)、`disclosure_date`の
  RFC2822パース方法を確定 → `record_to_point`/`_QUARTER_TO_N`/`_FY_START_FIELD_CANDIDATES`を
  実データに合わせて本修正。

---

## 2026-07-08 (12): 診断printがログ表示で途中切れて実フィールド名が見えなかった件を修正

### 経緯
- (11)の診断printを本番実行したところ発火はした(`fetched 8 earnings record(s) but 0 were
  usable ...`)が、`sample record: {...}`の1行が長すぎてGitHub Actionsのログ表示/コピペで
  `disclosure_date`の途中(アルファベット順で先頭寄りのキー)で切れてしまい、本来知りたかった
  `quarter`/`eps`/`revenue`相当のフィールド(アルファベット順で後ろの方)が見えなかった。
  ただしキー自体は確認できた実データ: `accounting_standard`, `cf_operating`等の営業CF系,
  `comprehensive_income_*`, `diluted_eps*`, `bps`, `average_shares`など、J-Quants型とは
  かなり違う会計項目の並び(決算短信の詳細項目そのものに近い)であることが判明。

### 修正
- `src/data/edinetdb.py` `update_fundamentals_auto`: 診断printを2本立てに分割。
  (1) 全キー一覧だけの短い行(`sorted(sample.keys())`、必ず全部見える)、
  (2) `quarter`/`period`/`fiscal`/`eps`/`revenue`/`sales`/`income`/`type`/`label`/`year`/`date`
  のいずれかを含むキーだけに絞った値付きの行(短くなるので途切れにくい)。
- テストは同等の内容のまま(候補フィールドの部分一致で確認)。フルスイート163件全パス。

### 次にやること
- ユーザーにコミット/push依頼 → daily.yml再実行 → 新しい2行診断で実フィールド名(特に
  quarter/eps/revenue/fy_start相当)を確認 → `record_to_point`/`_QUARTER_TO_N`/
  `_FY_START_FIELD_CANDIDATES`を実データに合わせて本修正。

---

## 2026-07-08 (11): EDINET DB `/earnings` レコードが取れても0件のまま(ユーザー指摘「ファンダ入力/編集に出てこない」)

### 経緯
- (9)(10)のfix適用後、daily.ymlはpushまで正常完了(`generated_at`更新確認済み)。ネストリスト
  抽出も`found nested list at 'data.earnings'`で動いている。しかしユーザーが「ダッシュボードの
  『ファンダ入力/編集』モーダルにEDINET DB分のデータが出てこない」と報告。
- 調査: 「ファンダ入力/編集」(`docs/assets/fundamentals-modal.js` `openFundamentalsModal`)は
  `docs/data/fundamentals_public.json`をprefillに使う設計(`fundamentals.py write_public_json`が
  `merge_fundamentals`の結果=CSV/J-Quants/EDINET DBの3ソース統合済みデータを書き出す)。配線自体は
  正しい(`pipeline.py`で`merge_fundamentals(auto_by_code, csv, tanshin_by_code=tanshin_by_code)`
  → `write_public_json`)。
- origin masterの`data/edinetdb_auto.json`を直接確認したところ **`total codes: 0`**。つまり
  `/earnings`のリスト取り出しは直ったのに、その先で全レコードが黙って捨てられていた。
  `update_fundamentals_auto`のbacklogループには、`recs`は取れたのに`derived`が0件になった場合の
  診断出力が一切無かった(サイレント消滅)。
- 疑わしい箇所: `record_to_point(rec, code)`が見ている`quarter`/`eps`/`revenue`/
  `disclosure_date`/`_FY_START_FIELD_CANDIDATES`はすべて実地未検証の推測フィールド名
  (HANDOFF.md「実地確認について」参照)。`/companies`・`/events`のトップレベルキーと同様、
  レコード内の個別フィールド名も外れている可能性が高い。さらに`record_to_point`呼び出し時に
  `fiscal_year_end_month`を渡していないため、`_estimate_fy_start`フォールバックも機能しない
  (fy_start解決がフィールド名一致に完全依存している)。

### 修正
- `src/data/edinetdb.py` `update_fundamentals_auto`: backlogループで`recs`が非空なのに
  `derived`が0件だった場合、**その日の最初の1件だけ**サンプルレコードの生データをprintする
  診断出力を追加(`EDINET DB: {code} fetched N earnings record(s) but 0 were usable ...`)。
  これで次回実行時、実際のフィールド名が判明する。
- `tests/test_edinetdb.py`: 未知フィールド名(`period`/`eps_value`)のレコードを与えて診断printが
  出ることを確認するテストを1件追加。フルスイート163件全パス。

### 次にやること
- ユーザーにコミット/push依頼 → daily.yml再実行 → 新しい診断printで実際の`/earnings`レコードの
  フィールド名(quarter/eps/revenue/disclosure_date相当のキー)を確認。
- 判明したら`record_to_point`/`_QUARTER_TO_N`/`_FY_START_FIELD_CANDIDATES`を実データに合わせて
  修正(これが直れば`fundamentals_public.json`にEDINET DB分が載り、「ファンダ入力/編集」にも
  出るようになるはず)。
- 引き続き未着手: daily.ymlの「当日実行済みスキップ」ガードを元に戻す((8)から持ち越し)。

---

## 2026-07-08 (10): daily.ymlのpush競合でjob全体失敗→ダッシュボード未更新の件を修正

### 経緯
- ユーザー報告: daily.ymlのActions実行ログで `git pull --rebase origin master` が
  `docs/data/fundamentals_public.json`/`heatmap.json`/`indices.json`/`report.json`で
  CONFLICT、job全体が`exit code 1`で失敗。フロントエンドにも新しいデータが反映されて
  いなかった。
- 原因: pipeline実行(数分〜数十分)中に、intraday-indices workflow(1〜2時間おきに
  `docs/data/indices.json`だけ更新)や、この日はユーザーの手動push(`edinetdb`修正)も
  重なり、daily.ymlがcommit後にpull--rebaseする時点でorigin側がかなり進んでいた。
  `docs/data/*.json`はどれも実行のたびに丸ごと再生成される出力ファイルで行単位マージが
  無意味なため、コンフリクトで即job失敗 → **そのrunの結果が一切pushされずダッシュボードが
  更新されない**という事態になっていた。

### 修正
- `.github/workflows/daily.yml`: 最後のpushステップを
  `git pull --rebase origin ...` → `git pull --rebase -X theirs origin ...` に変更。
  rebase中の ours/theirs はmergeと意味が逆(ours=rebase先=origin、theirs=replayされる
  自分の新規commit)なので、`-X theirs`で「今回生成した新しい方を採用」になる。
  これでdocs/data/*.json競合時にjob失敗せず、常に最新runの結果が反映されるようになる。

### 次にやること
- ユーザーにコミット/push依頼。
- push後、次回のdaily.yml実行(cronは7-11 UTC毎時リトライ中、または手動workflow_dispatch)で
  pushが正常完了し、ダッシュボードに反映されるか確認。
- 動作確認が済んだら「当日実行済みスキップ」ガード(§(8))を元に戻すこと(まだ未着手)。

---

## 2026-07-08 (9): EDINET DB `/earnings` のネストレスポンス解析バグを修正(1回目の修正が誤診断だった件)

### 経緯
- (8)の修正で `/companies`・`/events` は本番で正常動作を確認(3830社中3829社マッチ、997銘柄backlog投入)。
  一方 `/companies/{edinet_code}/earnings` は複数銘柄で「no list field at all; top-level keys: ['data', 'meta']」
  というログが出続けていた。`_extract_list_of_dicts()`に「known_keysのdict値を単一レコードとして[dict]で
  ラップする」フォールバックを追加してコミット(527e629)・push・本番実行までしたが、ユーザーが貼った
  次回実行ログで誤診断が判明:
  ```
  EDINET DB: /companies/E01542/earnings 'data' was a single dict, not a list
  (wrapping as one record; keys: ['count', 'earnings', 'edinet_code'])
  ```
  ラップされた「レコード」のキーが `count`/`earnings`/`edinet_code` になっており、これは単一レコードでは
  なく**ラッパーdict**だった。本当のリストはさらに1階層下の `data.earnings` にある。
  `record_to_point()`は`rec.get("quarter")`を見るため、ラッパーdictをそのままレコード扱いすると
  quarterキーが無く黙って`None`(=取りこぼし)になる。エラーにはならないので気づきにくい不具合だった。

### 修正
- `src/data/edinetdb.py`: `_extract_list_of_dicts()`に新しいフォールバック階層を追加。
  「単一レコードとしてラップする」より前に、known_keysの値がdictならその中身も
  known_keys→自動検出の順で再探索し、ネストしたlistが見つかればそれを返すようにした
  (例: `data.earnings`)。見つからない場合のみ、従来通り「単一レコードとしてラップ」にフォールバック。
- `tests/test_edinetdb.py`: 実際の`/earnings`形状(`{"data": {"count":N, "edinet_code":..., "earnings":[...]}}`)
  を再現したテストを2件追加(`_extract_list_of_dicts`本体・`fetch_earnings`経由)。
  既存の「単一dictラップ」テスト2件(真にフラットな1件レスポンス用)は非list値のみのdictなので
  そのまま通ることを確認済み。
- `pytest tests/test_edinetdb.py` 30件全パス、フルスイート162件全パス確認済み。

### 次にやること
- ユーザーにコミット/push依頼(サンドボックスからは直接pushしない方針。ローカルmac側でgit操作)。
- 次回daily実行後、`EDINET DB: X codes processed, Y left in backlog.`および
  `data/edinetdb_auto.json`の中身を確認し、実際にquarterデータが登録されているか検証。
- 動作確認が済んだらdaily.ymlの「当日実行済みスキップ」ガードを元に戻す((8)から持ち越し、未着手)。

---

## 2026-07-08 (8): EDINET DB「0 codes processed」バグ調査・修正(ユーザー指摘「直近ファんだにデータ入ってない」)

### 経緯
- edinetdb.enabled=true に切替後、dailyを回してもファンダが更新されないとユーザーから報告。
  APIキー設定ミス・当日実行済みガードでのスキップ、両方を調査したが該当せず。ユーザーが実際の
  pipeline実行ログ(`EDINET DB: 0 codes processed, 0 left in backlog.`のみで他の診断出力なし)を
  貼ってくれたことで原因を特定できた。

### 原因
- `fetch_events()`/`fetch_companies_map()`/`fetch_earnings()` がAPIレスポンスの配列フィールドを
  `body.get("data") or body.get("events") or []` のように決め打ちのキー名で取り出していた。
  実際のトップレベルキー名がこれと違うと、エラーも出さず黙って空リストを返す。ログに他の診断
  print が一切出ていない(=eventsループに入る前の時点で空)ことから、まさにこのパターンだと判断。

### やったこと
- `src/data/edinetdb.py`: `_extract_list_of_dicts(body, known_keys, context)` ヘルパーを新設。
  known_keysで見つからなければ「値がlist[dict]の最初のトップレベルキー」を自動検出し、それも無ければ
  実際のトップレベルキー一覧をprintして原因究明できるようにした。`fetch_companies_map`/`fetch_events`/
  `fetch_earnings`/`update_fundamentals_auto`のeventsループを全てこの経由に書き換え。
- `tests/test_edinetdb.py`: `_extract_list_of_dicts`本体(known_key一致/bare list/fallback自動検出/
  全滅ケース)と、`fetch_companies_map`/`fetch_events`のfallback経由テストを6件追加。
  `pytest`フルスイート158件全パス確認済み(既存152 + 新規6)。
- `data/edinetdb_state.json` を `{}` にリセット。`last_events_date`が誤って2026-07-08まで進んでいた
  ため、直さないと次回実行が2026-07-09以降しか再スキャンせず1〜7月分の開示を恒久的に取りこぼす所だった。
- `.github/workflows/daily.yml`: 「当日実行済みチェック」ガードを**一時的に無効化**(常に`skip=false`)。
  ユーザーが動作確認のため当日中に複数回再実行したいとの指示のため。コメントで元に戻すべき旨を明記。
- HANDOFF.md §5「実地確認について」・§12項目7 を更新。

### 次にやること
- ユーザーにコミット/push依頼(サンドボックスからはpush不可)。
- daily.ymlを手動実行し、EDINET DBの診断printで実際のトップレベル/フィールドキー名を確認。
  auto-detectで拾えていればOKだが、拾えていなければ`_COMPANY_CODE_KEYS`等の定数を実地確認して
  修正が必要。
- **動作確認が済んだらdaily.ymlのガードを元のgit log判定に戻すこと**(このままだと同日複数回
  コミットされ得る)。
- 初回成功分の `data/edinetdb_auto.json` の値(revenue単位・fiscal_quarterラベル)を決算短信の
  実際の数値と目視で突き合わせ。

---

## 2026-07-08 (6): ダッシュボード表のスティッキー列をrevert(ユーザー指摘「表の固定は無しで戻して」)

- 前エントリ(5)で入れたコード・銘柄名列のsticky固定を撤去。個別株ページのSPA統合(view-stock)自体は維持。
- `docs/assets/app.js`: `renderTable`のwrapper classを`"table-scroll stock-table-scroll"` → `"table-scroll"`に戻した。
- `docs/assets/style.css`: `.stock-table-scroll`関連のCSSブロック(sticky/left/width固定/fund-stale・row-static背景)を全削除。
  `th`ルールに追加していた`z-index: 2`も、sticky列専用の重ね順対策だったため削除(元の値に復元)。
- `HANDOFF.md`: 冒頭サマリ・§2・§7のsticky列に関する記述を削除。
- キャッシュバスター: `app.js` v=9→v=10, `style.css` v=10→v=11(いずれもindex.htmlのみ)。
- 未コミットで残っていた`docs/assets/batch.js`(バッチ履歴テーブルのtable-scrollラップ)と
  `.github/workflows/daily.yml`(cron再試行対応)はこのrevertとは無関係の別作業。ユーザーがローカルで
  rebase前に別コミットとして処理する予定。

## 2026-07-08 (7): ビュー切替時にスクロール位置をトップにリセット(ユーザー指摘「前のページのスクロール状態を継承しちゃってる」)

- SPA化(hidden切り替えのみで実DOMナビゲーションが発生しない)により、ページ切替してもブラウザが
  スクロール位置を自動で先頭に戻してくれなくなっていた問題を修正。
- `docs/assets/app.js`の`showView(hash)`冒頭に`window.scrollTo(0, 0)`を追加。dockボタンクリック・
  hashchange・初期表示いずれの経路でも`showView`を通るため、この1箇所の追加で全パターンをカバー。
- キャッシュバスター: `app.js` v=10→v=11(index.htmlのみ)。

### 次にやること
- ユーザーにコミット/push依頼(サンドボックスからはpush不可)。

---

## 2026-07-08 (6): daily.ymlのcronドロップ調査 → hourly window + 当日実行済みスキップに変更 (Sonnet, ユーザー指摘)

### 経緯
- ユーザーから「daily.yml(Daily screener run)が定期的に実行されない」と指摘。GitHub Actionsの
  実行履歴(公開リポジトリのActionsページをfetch)を確認したところ、直近6件中Scheduledトリガーは
  1件のみ(2026-07-07 10:38 UTC)。cron指定は`30 7 * * 1-5`(07:30 UTC)なのに実際は3時間超遅延し、
  さらに前営業日2026-07-06分はScheduled実行が履歴に1件も存在せず(発火せずドロップされたと推定)。
  YAML構文・Secrets(実行時は成功しているので無関係と判断)・リポジトリ非アクティブ化(60日ルール、
  Public+直近も活発なため非該当)は問題なしと確認。GitHub公式が「scheduleイベントは高負荷時に
  遅延・ドロップされ得る」と明言している既知の仕様に起因すると判断。

### やったこと
- `.github/workflows/daily.yml`: cronを単発`30 7 * * 1-5`から`0 7-11 * * 1-5`
  (07:00-11:00 UTC = 16:00-20:00 JST、平日毎時)に変更。1回ドロップ/遅延しても後続の時間帯で
  リトライできるようにした。
- 二重実行防止のため、checkout直後(pip installより前)に「当日実行済みチェック」ステップを追加。
  `git log --format=%s -20`に当日日付の`chore: daily screener run YYYY-MM-DD`があれば
  `skip=true`を出力し、setup-python/依存インストール/pipeline実行/commitの各ステップを
  `if: steps.check.outputs.skip != 'true'`でスキップ(スキップ時は数秒でジョブ終了)。
  この判定にはcheckout時の履歴が必要なため`fetch-depth: 0`に変更(従来のデフォルトshallow cloneでは
  不足)。
- `python3 -c "import yaml; yaml.safe_load(...)"`でYAML構文確認済み。HANDOFF.md §8を更新。

### 次にやること
- ユーザーにコミット/プッシュを依頼(`git fetch`済み、origin/masterと1コミットずつ乖離あり
  〔bot側`intraday index refresh`〕。通常どおりrebaseで解消可能)。
- 次回平日の実際の発火状況(hourly retryが機能しているか、スキップ判定が正しく効くか)を
  Actions実行履歴で確認するのが望ましい。

---

## 2026-07-08 (5): 個別株ページをSPA統合 + ダッシュボード表のコード/銘柄名列をスティッキー化 (Sonnet, ユーザー指摘)

### やったこと
- **個別株ページのSPA統合**: `docs/stock.html`(チャート3ペイン等の独立ページ)を`index.html`の5番目の
  ビュー`view-stock`として統合。`index.html`の`<head>`にLightweight Charts CDNスクリプトを追加。
  `stock.html`は`?code=X`を`index.html#stock/X`へJSリダイレクトするだけのスタブに書き換え
  (meta refreshだとクエリをhashへ引き継げないため、heatmap.htmlとは違いJS実装)。
  - `VIEWS`に`"stock"`を追加、`showView(hash)`が`hash.split("/")`で`[name, param]`に分解し
    `stock/CODE`形式のパラメータ付きルートに対応。`initStockPage(param)`をrouterから呼ぶ。
    Dockナビには`view-stock`用ボタンは追加しない(ダッシュボード表の行クリック専用のドリルダウン)。
  - ダッシュボード表の行クリック遷移を`window.location.href = "stock.html?code=..."`から
    `window.location.hash = "stock/" + code`に変更。
  - **SPA再初期化の後始末**: フルリロード前提だった`renderCharts()`は、同じDOMのまま銘柄を切り替える
    SPAでは前回分のチャートインスタンス・`resize`リスナー・チェックボックス/期間トグルのイベント
    リスナーが積み上がって壊れるため、`teardownCharts()`(モジュール変数`stockChartState`が前回の
    `{charts, resizeHandler, dateLabels}`を保持)を`initStockPage()`の先頭で必ず呼び、チャートの
    `.remove()`・リスナー解除・日付ラベルDOM除去を行ってから再構築するように修正。
    `#toggle-pivot`/`#toggle-stop`/`#timeframe-toggle`は`cloneNode(true)`+`replaceWith`で要素ごと
    差し替えてリスナー重複を防止。RS無し銘柄で`rs-card`を`remove()`していた処理は、RS有り銘柄に
    戻した時に復活できなくなるバグがあったため`hidden`切替に変更。
- **コード・銘柄名列のスティッキー化**: 横スクロール中にどの行の銘柄か分からなくなる問題への対応。
  `renderTable`のwrapperに`stock-table-scroll`クラスを追加し、`style.css`で1列目(コード, 84px固定幅)・
  2列目(銘柄名)を`position: sticky`で左固定。batch履歴テーブル等、他の`.table-scroll`には影響しない
  ようクラスでスコープ。fund-stale行の黄色背景・row-static行も個別に背景色を追従させた。
- キャッシュバスター: `app.js` v=8→v=9、`style.css` v=9→v=10(いずれもindex.htmlのみ、stock.htmlは
  リダイレクトスタブ化でscriptタグ自体を持たなくなった)。
- `node --check`でapp.js/batch.jsの構文確認済み。HANDOFF.md §2/§7/§12/§13/キャッシュバスター表を更新。

### 次にやること
- ユーザーにコミット/プッシュを依頼(サンドボックスからはpush不可、`git fetch`で乖離確認→
  必要ならrebase→pushの手順を提示予定)。
- 実機確認推奨: view-stockへの遷移・戻る・別銘柄への連続遷移(チャートリークが無いか)、
  横スクロールでのスティッキー列の見た目(特にモバイル幅)。

---

## 2026-07-08 (4): Dockアイコン差し替え + バッチ履歴テーブルの横溢れ修正 (Sonnet, ユーザー指摘)

- Dockナビの絵文字+テキストラベルをBootstrap Icons(CDN, `bootstrap-icons@1.11.3`)に置き換え、
  アイコンのみ表示に変更(`title`/`aria-label`でツールチップ・アクセシビリティは確保)。
  `.dock-btn`を縦積み(アイコン+文字)から48px角の円形アイコンボタンに変更。
- `docs/assets/batch.js`の実行履歴テーブルが`.table-scroll`でラップされておらず、
  グローバルな`table { min-width: 760px }`を継承してバッチページ全体が横に溢れていた不具合を修正。
  ラッパーdivを追加+`.run-history-table`に`min-width: 0`を上書き。
- キャッシュバスター: style.css v=9(index.html/stock.html両方)、batch.js v=2。
- `node --check`で構文確認済み。

---

## 2026-07-08 (3): フロントエンドをSPA化 + 列統一 + 投資法/バッチ実行ページ追加 (Sonnet)

### やったこと
- **列統一**: `docs/assets/app.js` の `renderTable`/`renderPriorityTier` をCOLUMNS配列一本化で書き換え、
  本命/候補プール/監視の3ティアが同じレンダリング・ソート実装を共有するように統一(旧
  `PRIORITY_COLUMNS`/`renderPriorityTable`/`maDeviationSummary`/`sectorSummary` は削除)。
  各列に任意の `sortValue(s)` を持たせ、フォーマット済み文字列ではなく元の値でソートするよう修正
  (セクター/高値距離/ファンダ状況などの列で従来ソートが効いていなかったバグの修正も兼ねる)。
  セクター強度列を `.sector-strength-strong/-mid/-weak` で文字色分け、銘柄名は10文字トリム。
  チャート未生成行(`has_chart===false`)は `.row-static` でクリック不可に。
- **投資法ページ追加**: `view-invest` セクションにSEPAサイクル/トレンドテンプレート8条件/VCP(V1〜V7)/
  エントリー/損切り・サイズ管理/利益確定の要点を静的HTMLで追加(ユーザーが手法を見返せるように)。
- **バッチ実行ページ追加**: `config.js` にワークフロー一覧(daily/universe/jquants-backfill/
  intraday-indices)を定義、`github-api.js` に汎用 `dispatchWorkflow(file)`/未認証 `listWorkflowRuns(file,n)`
  を追加(旧 `dispatchDailyWorkflow` はラッパとして存置)、`fundamentals-modal.js` に汎用
  `triggerWorkflow(btn, file)` を追加、新規 `docs/assets/batch.js` で `view-batch` にワークフローカード+
  直近実行履歴(`.run-status-badge` 色分け)を描画。実行履歴表示自体は未認証で閲覧可、実行トリガーのみ
  🔓解錠(書き込みPAT)必須で `.passkey-gated` により非表示。
- **SPA化**: `index.html` を4セクション(`view-dashboard`/`view-sectormap`/`view-invest`/`view-batch`)+
  下部Dock風ナビ(`#dock-nav`)の1ページに統合。`app.js` に `showView`/`initRouter` を追加し
  `location.hash` ベースで切替(sectormap/batchタブは開くたびに `initHeatmap()`/`initBatchView()` を再実行)。
  `heatmap.js` は `hmWired` フラグで一度きりのイベント登録だけガードし `initHeatmap()` を安全に再呼び出し
  可能にした(非表示中の `clientWidth` 0によるツリーマップ崩壊を回避)。旧 `heatmap.html` は
  `index.html#sectormap` へのリダイレクトスタブに変更(旧URL互換)。
  書き込み系UI非表示は `hidePasskeyAuthUi` の固定idリストから `.passkey-gated` クラス方式に変更
  (ヘッダーボタン+バッチページのカードを同じクラスで一括制御するため)。
- 未使用だった `.prio-badge`/`.priority-*` CSSを削除。
- キャッシュバスター全更新: app.js/style.css v=8(index.html/stock.html両方)、heatmap.js v=8、
  config.js/github-api.js v=6、fundamentals-modal.js v=7、batch.js v=1(新規)、webauthn-vault.js v=5(変更なし)。
- 検証: `node --check` を全JSファイルに実行、全パス。`python -m pytest tests/ -q` は
  サンドボックスに `pyarrow`/`jpholiday` が入っておらず一部収集エラー・1件失敗
  (`test_indices.py::test_update_indices_writes_json_and_survives_failures`、pyarrow欠如が原因)が出たが、
  今回touchしたのはフロントのみでPythonロジックは無変更のため無関係と判断(148/149パス)。
  ユーザーのローカル環境で改めて `python -m pytest tests/ -q` を実行して確認を推奨。
- HANDOFF.md §2/§3/§7/§13を更新(EDINET DB §5節は無変更のまま保持)。

### 次にやること
- ユーザーにローカルでの目視確認(SPA切替・ダッシュボード列表示・バッチ実行ページの見た目)と
  `python -m pytest tests/ -q` の実行を依頼。
- 問題なければコミット→push(サンドボックスからはpush不可、ユーザーのローカルで実行)。

---

## 2026-07-08 (2): edinetdb.enabled を true に切替 (Sonnet, ユーザー指示)

- ユーザー指示により `config.yaml: edinetdb.enabled` を `false` → `true` に変更。
  Claude側ではサンドボックス制約により実地確認(curl検証)は未実施のまま(下記参照)。
- HANDOFF.md 5章「実地確認について」を更新: enabled化した旨と、daily.yml初回実行後に
  `data/edinetdb_auto.json` の値(revenue単位・fiscal_quarterラベル)を実際の決算短信と
  突き合わせ確認することを推奨する記述に変更。§12チェックリストの該当項目も更新。
- `docs/assets/app.js` と `CLAUDE.md` はユーザーがローカルで作業中とのことなので、この
  コミットには含めていない(未コミットのまま手元に残る)。
- 次: ユーザーがdaily.ymlの `EDINETDB_API_KEY` Secret登録状況を確認し、workflow_dispatchで
  手動実行するか翌営業日のcronを待つ。

---

## 2026-07-08: EDINET DB統合を実装 (Sonnet) — enabled: false で導入、実地確認は未完了

### やったこと
- サンドボックスから `edinetdb.jp` へのネットワークアクセスがブロックされており
  (`irbank.net`と同様、許可リスト外)、DESIGN_EDINETDB.md 1節が求める実地確認(curl検証)が
  実行不可と判明。ユーザーに確認したところ「検証スキップで実装(推奨)」を選択 →
  防御的な実装で進め、`config.yaml: edinetdb.enabled: false` のまま導入する方針で確定。
- `config.yaml` に `edinetdb:` セクション追加(enabled: false, requests_per_day: 90 等、設計書4節どおり)。
- `src/data/edinetdb.py` 新規実装(~330行): codemap管理、events検出→backlogキュー、budget消費、
  YTD差分導出(`derive_with_base`、J-Quantsストアをbase_storeとして使用)、fy_start推定
  (`_estimate_fy_start`、APIレスポンスにフィールドが無い場合の数学的フォールバック)、
  state/store永続化。design doc §3どおりの構成。
  - 実装中に自分で見つけたバグ: 「codemapに無いコード」の扱いを当初サイレントdropしていたが、
    設計書の「次回refreshで拾う」という記述からbacklogへの再投入が必要と気づき修正
    (`test_update_code_missing_from_codemap_stays_in_backlog`で担保)。
- `tests/test_edinetdb.py` 新規作成、20件全パス。
- `src/data/fundamentals.py :: merge_fundamentals` を2ソース→3ソース (manual > auto(jquants) >
  tanshin(edinetdb)) に拡張。`tanshin_by_code` はキーワード引数・既定Noneで後方互換維持。
  `tests/test_fundamentals.py` に3ソースのマージ優先度テスト4件追加、14件全パス。
- `src/pipeline.py` に組み込み(J-Quantsブロック直後、try/exceptで失敗無視)。
  `tests/test_pipeline.py` の `wired` fixture に edinetdb_mod のno-opモックを追加。
- `.github/workflows/daily.yml` に `EDINETDB_API_KEY: ${{ secrets.EDINETDB_API_KEY }}` を追加
  (JQUANTS_API_KEYの隣)。Secret登録自体はユーザー依頼(未登録でも enabled: false の間は無害)。
- `python -m pytest tests/ -q` 152件全パス確認。
- `HANDOFF.md` に新5章「EDINET DB 決算短信補完」を追加、以降の章番号を+1リナンバー(6〜13)、
  2章のリポジトリ構成ツリー・8章のActions表・10章のテスト件数を更新。

### 未完了・次回への申し送り
- **実地確認が最優先タスク**: ユーザーが `EDINETDB_API_KEY` を発行しローカルで
  `/companies` `/events` `/earnings` の実レスポンスをcurl確認 → 設計書1節・HANDOFF.md 5章の想定
  (revenue単位=百万円、fiscal_year_startフィールド名、5件の未検証点)との差異を洗い出すこと。
  差異があれば `record_to_point` / `_estimate_fy_start` の修正が必要。
- 問題なければ `config.yaml: edinetdb.enabled: true` に切り替え、daily.yml の Secret登録を依頼。
- このセッションではコミット未作成(次のタスクで作成予定)。push は従来どおりユーザー依頼。

---

## 2026-07-07 (2): EDINET DB統合の設計完了 (Fable) → 実装をSonnetに申し送り

### やったこと
- EDINET DB API仕様を実地検証 (edinetdb.jp/docs/api 全文精読 + ブログ記事)。主な判明事項:
  - 認証は `X-API-Key` ヘッダ。Free = アカウント単位100req/日。リクエスト間隔制約なし。
  - `/v1/companies/{edinet_code}/earnings` は **EDINETコード必須** → `/v1/companies?per_page=5000`
    (1req) で証券コード→EDINETコードのマップを構築する。
  - **`/v1/events?event_type=earnings_summary` で当日開示銘柄を検出可能** (J-Quants `?date=` 相当)。
    全銘柄ポーリング不要。決算集中日は Free 枠を超えるので backlog キューで数日かけて消化する設計。
  - 短信の値は YTD累計 → J-Quants と同じ差分導出が必要。ただし EDINET DB は 2026-01 以降しか
    無いため、差分基準は **J-Quants ストアの確定四半期から再構成** する (設計書3.4)。
  - revenue は百万円単位 (J-Quants は円のはず) → ×1e6 換算。**要実地確認**。
- ユーザー確認: 統合方針 = **案A (J-Quantsメイン、EDINET DBは12週遅延窓の補完)**、プラン = **Free**。
- **`DESIGN_EDINETDB.md` に実装可能な設計書を作成** (関数シグネチャ・マージ拡張・config追加・
  テスト・HANDOFF更新箇所・実装手順まで)。

### 次のタスク (Sonnet)
1. DESIGN_EDINETDB.md を読む。まず **ユーザーに EDINETDB_API_KEY の発行を依頼**
   (https://edinetdb.jp/developers、無料) し、設計書1節の「実地確認1〜5」を実施。
   特に (2) earnings レスポンスに会計年度フィールドがあるか、(3) revenue の単位、が設計の前提。
2. 差異があれば設計書を修正 → 設計書8節の手順どおり実装。enabled: false で入れてから検証→true。
3. daily.yml への Secret 登録 (EDINETDB_API_KEY) と push はユーザーに依頼。

---

## 2026-07-07: IRBANKスクレイピング検討 → 断念、EDINET DB代替案の設計をFableに申し送り

### 経緯
- ユーザーから「IRBANKをスクレイピングしてファンダ取ってこれるか、まずローカルで試したい」と相談。
- 実装前に利用規約を確認 → **IRBANK・株探・Yahoo!ファイナンスいずれもスクレイピング/自動取得/DB構築目的の利用を明示的に禁止**。
  特にIRBANKの禁止事項(体系的蓄積・データベース構築・再配布)は、本プロジェクトの用途
  (取得したファンダをGitHub Pagesで公開するscreenerのDBに組み込む)と直接抵触するため実装を見送った。
- サンドボックス側もネットワーク許可リストで `irbank.net` がブロックされており、技術的にも直接アクセス不可(参考情報)。
- **結論: IRBANKスクレイピングの件は完全にクローズ。再浮上させる場合もirbank.net等への自動アクセスは規約違反なので不可。**

### 代替案として浮上: EDINET DB (edinetdb.com / edinetdb.jp)
第三者サービスだが、スクレイピングではなく金融庁EDINETの公開データをXBRLから構造化して配信するAPI/MCP。規約はクリーン。

- **get_earnings エンドポイント**: 決算短信ベースで1Q/2Q/3Qを含む四半期粒度データを2026年1月から提供開始。
  → 本プロジェクトが2024年にEDINET(有価証券報告書ベース)を撤去した理由
    (`a5c76fc`: 四半期報告書廃止でQ1/Q3が単体取得不可)を、**データソースを決算短信に切り替えることで
    解消できる可能性がある**。J-Quants (`/v2/fins/summary`) と発想的には同じ(決算短信=四半期粒度)。
- 料金: 一般利用は無料枠1日100リクエストまで。研究目的(.ac.jp/.eduメール)ならAcademic Planで無料フル利用可。
- 制約(未検証の懸念点も含む):
  - get_earnings(決算短信)は**2026年1月以降のデータのみ**(バックフィル不可)。
  - get_financials(有報ベース、6年分)は既存の旧EDINET実装と同じQ1/Q3粒度問題を抱えている可能性が高い(未検証)。
  - J-Quantsとの統合方針(併用/バックアップ/置換のどれにするか)は未決定。
  - API認証方式・レートリミットの実挙動は未検証(ドキュメント上の情報のみ)。

### 次のタスク: Fableへの申し送り(設計フェーズ)
**このタスクの実装は別セッションのSonnetが行う前提。** Fableは以下を検討し、
HANDOFF.md流(既存の4章 J-Quants節と同じ粒度)で、Sonnetがそのまま着手できる実装可能な設計に
落とし込むこと(具体的なファイルパス・関数シグネチャ・マージロジック・config.yaml追加項目・
テスト更新箇所まで明記する。既存の `src/data/jquants.py` / `src/data/fundamentals.py` の
コード規約・命名規則に合わせること)。

1. EDINET DBのAPI仕様を実地検証(get_earningsのレスポンス形式、認証方式、レートリミットの挙動)。
2. J-Quantsとの統合方針を決定:
   - 案A: J-Quantsをメイン、EDINET DBはJ-Quantsの12週間遅延で欠けている直近四半期のみを補完するバックアップ
   - 案B: 完全併用(両方叩いて開示日が新しい方を採用)
   - 案C: 将来的な置き換え候補として並行運用のみ(マージはせず比較検証に留める)
3. 新モジュール `src/data/edinetdb.py` (仮)の設計:
   - fetch関数、認証(APIキー環境変数名)、レート制御、ストア形式(`data/edinetdb_auto.json` 案)
   - `merge_fundamentals`(現状はauto(jquants) + manualの2ソースマージのみ)を3ソースにどう拡張するか
4. `config.yaml` への追加パラメータ案(有効化フラグ、APIキー環境変数名、リクエスト上限、バックオフ等)
5. `src/pipeline.py` への組み込み箇所と `tests/test_pipeline.py` の `wired` fixture へのモック追加箇所
6. `HANDOFF.md` の更新箇所(4章 J-Quants節と並列で「5. EDINET DB (決算短信補完)」節を新設する想定、
   以降の章番号はリナンバー)

### 参考: 変更時のチェックリスト (HANDOFF.md 12章、実装時にSonnetが遵守すること)
- docs/ のJS/CSSを触ったら3つのhtmlの `?v=N` を全部上げたか
- pipelineに外部I/Oを足したら test_pipeline.py の wired fixture にモックを足したか
- `python -m pytest tests/ -q` 全パス + `node --check docs/assets/app.js`
- コミット前に `.git/*.lock` をmvで退避したか(サンドボックスの場合)
- `git fetch` してoriginとの乖離を確認したか(botが毎日コミットする)
- pushはユーザーに依頼したか
- UI文言にP1/P2/P3/P4という語を新たに出していないか
