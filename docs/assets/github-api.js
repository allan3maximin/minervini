// GitHub Contents/Actions API client + CSV parse/merge/serialize helpers for
// the browser-based fundamentals editor. No build step: plain globals on
// `window.MinerviniGitHub`, loaded before webauthn-vault.js/fundamentals-modal.js/app.js.
//
// The PAT is held in memory only (module-scope variable) -- never written to
// localStorage/sessionStorage/IndexedDB. It's set once per tab by
// webauthn-vault.js after a successful passkey unlock, and disappears when
// the tab closes. This is intentional: the dashboard is used exclusively in
// private browsing, where any persistent browser storage is unavailable anyway.
(function () {
  const GH_API = "https://api.github.com";
  const CSV_HEADER = ["code", "fiscal_quarter", "eps", "revenue", "monthly_yoy", "checked_date"];

  let activeToken = "";

  function getToken() {
    return activeToken;
  }

  function setToken(token) {
    activeToken = token || "";
  }

  function hasToken() {
    return !!activeToken;
  }

  // ---------------------------------------------------------------------
  // CSV parse / merge / serialize
  // ---------------------------------------------------------------------

  function parseCsv(text) {
    const lines = text.split(/\r\n|\n/).filter((l) => l.trim().length > 0);
    if (lines.length === 0) return [];
    const header = lines[0].split(",").map((h) => h.trim());
    return lines.slice(1).map((line) => {
      const cells = line.split(",");
      const row = {};
      header.forEach((col, i) => {
        row[col] = cells[i] !== undefined ? cells[i].trim() : "";
      });
      return row;
    });
  }

  function quarterSortValue(fq) {
    const m = /^(\d{4})Q([1-4])$/.exec(fq || "");
    if (!m) return 0;
    return parseInt(m[1], 10) * 10 + parseInt(m[2], 10);
  }

  function serializeCsv(rows) {
    const sorted = [...rows].sort((a, b) => {
      if (a.code !== b.code) return a.code < b.code ? -1 : 1;
      return quarterSortValue(b.fiscal_quarter) - quarterSortValue(a.fiscal_quarter);
    });
    const lines = [CSV_HEADER.join(",")];
    for (const row of sorted) {
      lines.push(CSV_HEADER.map((h) => row[h] ?? "").join(","));
    }
    return lines.join("\n") + "\n";
  }

  // Overlays `newRows` onto `existingRows` keyed by (code, fiscal_quarter).
  // Rows for other codes, and other quarters of the same code not present in
  // `newRows`, are left untouched.
  function mergeRows(existingRows, newRows) {
    const key = (r) => `${r.code}__${r.fiscal_quarter}`;
    const map = new Map();
    for (const r of existingRows) map.set(key(r), r);
    for (const r of newRows) map.set(key(r), r);
    return Array.from(map.values());
  }

  // ---------------------------------------------------------------------
  // base64 <-> UTF-8 (GitHub Contents API transports file bodies as base64)
  // ---------------------------------------------------------------------

  function encodeBase64Utf8(str) {
    const bytes = new TextEncoder().encode(str);
    let binary = "";
    bytes.forEach((b) => (binary += String.fromCharCode(b)));
    return btoa(binary);
  }

  function decodeBase64Utf8(b64) {
    const binary = atob(b64.replace(/\n/g, ""));
    const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
    return new TextDecoder("utf-8").decode(bytes);
  }

  // ---------------------------------------------------------------------
  // GitHub API calls
  // ---------------------------------------------------------------------

  class GitHubApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  async function ghFetch(path, options = {}) {
    const token = getToken();
    if (!token) {
      throw new GitHubApiError(0, "GitHubトークンが設定されていません。設定からPersonal Access Tokenを登録してください。");
    }
    const headers = {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    };
    return fetch(`${GH_API}${path}`, { ...options, headers });
  }

  async function toApiError(resp) {
    let detail = "";
    try {
      const data = await resp.json();
      detail = data.message || "";
    } catch (e) {
      /* ignore */
    }
    if (resp.status === 401) {
      return new GitHubApiError(401, "トークンが無効です。設定でPersonal Access Tokenを確認してください。");
    }
    if (resp.status === 403) {
      return new GitHubApiError(403, `権限が不足しています(${detail || "Contents: Read and write を確認してください"})`);
    }
    if (resp.status === 404) {
      return new GitHubApiError(404, `リポジトリまたはファイルが見つかりません(${detail}）`);
    }
    return new GitHubApiError(resp.status, detail || `GitHub APIエラー (${resp.status})`);
  }

  // Generic Contents API read/write, reused by the fundamentals CSV editor
  // and the WebAuthn vault (docs/auth/vault.json).
  async function getRepoFile(path) {
    const { owner, repo, branch } = window.MINERVINI_CONFIG;
    const resp = await ghFetch(`/repos/${owner}/${repo}/contents/${path}?ref=${encodeURIComponent(branch)}`);
    if (resp.status === 404) {
      return { content: null, sha: null };
    }
    if (!resp.ok) throw await toApiError(resp);
    const data = await resp.json();
    return { content: decodeBase64Utf8(data.content), sha: data.sha };
  }

  async function putRepoFile(path, contentText, sha, message) {
    const { owner, repo, branch } = window.MINERVINI_CONFIG;
    const body = { message, content: encodeBase64Utf8(contentText), branch };
    if (sha) body.sha = sha;
    return ghFetch(`/repos/${owner}/${repo}/contents/${path}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  async function getFundamentalsFile() {
    const { content, sha } = await getRepoFile(window.MINERVINI_CONFIG.fundamentalsPath);
    return { rows: content === null ? [] : parseCsv(content), sha };
  }

  // Unauthenticated read of the fundamentals CSV via raw.githubusercontent.com
  // (public repo, CORS-enabled). Used to prefill the input form when no PAT
  // is available -- the "no git key" manual-commit mode.
  async function fetchFundamentalsCsvPublic() {
    const { owner, repo, branch, fundamentalsPath } = window.MINERVINI_CONFIG;
    const url = `https://raw.githubusercontent.com/${owner}/${repo}/${encodeURIComponent(branch)}/${fundamentalsPath}`;
    const resp = await fetch(url, { cache: "no-store" });
    if (resp.status === 404) return [];
    if (!resp.ok) throw new Error(`fundamentals.csvの取得に失敗しました (${resp.status})`);
    return parseCsv(await resp.text());
  }

  async function putFundamentalsFile(rows, sha, code) {
    return putRepoFile(
      window.MINERVINI_CONFIG.fundamentalsPath,
      serializeCsv(rows),
      sha,
      `fund: ${code} updated via dashboard`
    );
  }

  // GET current file -> merge -> PUT. On a 409 (sha changed under us, e.g.
  // another edit or the daily bot commit landed first), retries exactly once
  // by re-fetching and re-merging. Any other failure (or a second 409)
  // surfaces to the caller so the input isn't silently lost.
  async function saveFundamentalsRows(code, newRows) {
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      const { rows: existingRows, sha } = await getFundamentalsFile();
      const merged = mergeRows(existingRows, newRows);
      const resp = await putFundamentalsFile(merged, sha, code);
      if (resp.ok) {
        return await resp.json();
      }
      if (resp.status === 409 && attempt === 0) {
        lastError = await toApiError(resp);
        continue; // one automatic retry
      }
      throw await toApiError(resp);
    }
    throw lastError || new GitHubApiError(409, "競合が解消しませんでした。もう一度お試しください。");
  }

  async function getExistingRowsForCode(code) {
    const { rows } = await getFundamentalsFile();
    return rows.filter((r) => r.code === code);
  }

  // ---------------------------------------------------------------------
  // Actions API (manual re-run button / バッチ実行ページ)
  // ---------------------------------------------------------------------

  // 任意のワークフローファイルをworkflow_dispatchでトリガーする汎用版。
  async function dispatchWorkflow(workflowFile) {
    const { owner, repo, branch } = window.MINERVINI_CONFIG;
    const resp = await ghFetch(`/repos/${owner}/${repo}/actions/workflows/${workflowFile}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: branch }),
    });
    if (resp.status === 204) return true;
    throw await toApiError(resp);
  }

  // 後方互換: 既存の「今すぐ再スクリーニング」相当の呼び出し元向け薄いラッパ。
  async function dispatchDailyWorkflow() {
    return dispatchWorkflow(window.MINERVINI_CONFIG.workflowFile);
  }

  // 直近の実行履歴一覧。公開リポジトリのActions実行履歴は認証不要で読める
  // ため(60req/hr制限はあるが本用途では十分)、ghFetchではなく素のfetchを使う
  // -- 未解錠(PATなし)でもバッチ実行ページの履歴表示自体は見られるようにするため。
  async function listWorkflowRuns(workflowFile, perPage = 5) {
    const { owner, repo } = window.MINERVINI_CONFIG;
    const resp = await fetch(
      `${GH_API}/repos/${owner}/${repo}/actions/workflows/${workflowFile}/runs?per_page=${perPage}`,
      { headers: { Accept: "application/vnd.github+json" } }
    );
    if (!resp.ok) throw await toApiError(resp);
    const data = await resp.json();
    return data.workflow_runs || [];
  }

  window.MinerviniGitHub = {
    GitHubApiError,
    getToken,
    setToken,
    hasToken,
    parseCsv,
    serializeCsv,
    mergeRows,
    getRepoFile,
    putRepoFile,
    getFundamentalsFile,
    fetchFundamentalsCsvPublic,
    putFundamentalsFile,
    saveFundamentalsRows,
    getExistingRowsForCode,
    dispatchDailyWorkflow,
    dispatchWorkflow,
    listWorkflowRuns,
    toApiError,
    CSV_HEADER,
  };
})();
