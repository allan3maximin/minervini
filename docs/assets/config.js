// Repo constants for the dashboard's GitHub Contents/Actions API calls.
// Never put secrets here -- the PAT lives only in the browser's localStorage
// (see github-api.js: TOKEN_KEY), entered via the settings modal.
window.MINERVINI_CONFIG = {
  owner: "allan3maximin",
  repo: "minervini",
  branch: "master",
  fundamentalsPath: "manual/fundamentals.csv",
  workflowFile: "daily.yml",
};
