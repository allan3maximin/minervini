// Repo constants for the dashboard's GitHub Contents/Actions API calls.
// Never put secrets here -- the PAT only ever lives in memory for the tab's
// lifetime (see github-api.js), unlocked each visit via the WebAuthn PRF
// vault at docs/auth/vault.json (see webauthn-vault.js).
window.MINERVINI_CONFIG = {
  owner: "allan3maximin",
  repo: "minervini",
  branch: "master",
  fundamentalsPath: "manual/fundamentals.csv",
  vaultPath: "docs/auth/vault.json",
  workflowFile: "daily.yml",
  // Kill switch for the passkey auth / write feature. When false, the
  // unlock/rerun/settings buttons are hidden and no vault.json fetch happens;
  // the dashboard is read-only. 2026-07-12: データ暗号化ゲート導入に伴い有効化。
  // (注: 起動時ゲート自体はこのフラグではなく「report.jsonが暗号化されているか」
  //  で発動する。このフラグは解錠/設定ボタン等の表示制御のみ。)
  passkeyAuthEnabled: false,
  // バッチ実行ページ(view-batch)で手動トリガー可能にするワークフロー一覧。
  // .github/workflows/ の実ファイル名と一致させること。
  //
  // inputs: workflow_dispatch へ渡す値。そのワークフローが inputs を宣言して
  // いない場合に送ると GitHub API が 422 を返すので、宣言があるものだけ書く。
  // 2026-07-29時点で force を宣言しているのは daily.yml と maezyou.yml のみ
  // (universe.yml / jquants-backfill.yml / intraday-indices.yml は inputs なし)。
  workflows: [
    // force: 手動実行は「今すぐ回したい」意思表示なので当日実行済みガードを迂回する。
    { file: "daily.yml", label: "日次パイプライン", desc: "スクリーニング全体を再実行 (15-30分)", inputs: { force: true } },
    { file: "universe.yml", label: "ユニバース再構築", desc: "上場銘柄一覧を再取得 (40-60分、通常は月初のみ自動実行)" },
    { file: "jquants-backfill.yml", label: "J-Quants バックフィル", desc: "全銘柄のファンダ全期間を再取得 (20分前後)" },
    { file: "intraday-indices.yml", label: "市場指標の即時更新", desc: "指数データのみ再取得 (数分、市場時間中は自動15分間隔)" },
  ],
};
