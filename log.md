# 進行状況ログ

(新しい方が上。作業を再開する際は必ず先にここを読むこと)

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
