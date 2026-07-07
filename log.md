# 進行状況ログ

(新しい方が上。作業を再開する際は必ず先にここを読むこと)

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
