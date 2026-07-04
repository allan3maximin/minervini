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
  // Kill switch for the passkey auth / write feature while it's still being
  // tuned. When false, the unlock/rerun/settings buttons are hidden and no
  // vault.json fetch happens; the dashboard is read-only.
  passkeyAuthEnabled: false,
};
