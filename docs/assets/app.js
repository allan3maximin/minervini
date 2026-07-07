const STATUS_LABELS = {
  BREAKOUT: "本日のBREAKOUT",
  BREAKOUT_WEAK: "BREAKOUT (出来高不足)",
  WATCH_A: "WATCH_A (ピボット待ち)",
  WATCH_B: "WATCH_B (ベース形成中)",
  EXTENDED: "EXTENDED (追いかけ禁止)",
  REJECTED: "ベース崩れ",
  IMMATURE: "ベース形成中(未成熟)",
  TOO_RECENT: "高値更新中(ベース未形成)",
  NO_BASE: "ベース未検出",
};
const STATUS_ORDER = [
  "BREAKOUT",
  "BREAKOUT_WEAK",
  "WATCH_A",
  "WATCH_B",
  "EXTENDED",
  "REJECTED",
  "IMMATURE",
  "TOO_RECENT",
  "NO_BASE",
];

// 本命/候補プール/監視(旧P1)の3ティア共通の列定義。以前はCOLUMNS(本命/候補プール)
// とPRIORITY_COLUMNS(監視)が別々だったが、表示列を統一するために1本化した。
// value()が表示文字列(またはhtml:trueならHTML)を返し、sortValue()があれば
// それをソートキーとして使う(無ければ s[key] を直接参照)。
function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 銘柄名は横幅を取りすぎるため10文字でトリムする。フルネームはtitle属性(ホバー)で確認可能。
function trimName(name) {
  const s = String(name ?? "");
  return s.length > 10 ? s.slice(0, 10) + "…" : s;
}

const SECTOR_STRENGTH_CLASS = { 強: "sector-strength-strong", 中: "sector-strength-mid", 弱: "sector-strength-weak" };

// セクター(強度)列: 強度の文字だけ色付けする(緑=強/グレー=中/赤=弱)。
function sectorStrengthHtml(s) {
  if (!s.sector33) return "-";
  const strength = s.sector_strength;
  const cls = SECTOR_STRENGTH_CLASS[strength] || "";
  const badge = strength ? `<span class="${cls}">${escapeHtml(strength)}${escapeHtml(s.sector_direction || "")}</span>` : "";
  return `${escapeHtml(s.sector33)} ${badge}`;
}

const COLUMNS = [
  { key: "code", label: "コード", value: (s) => s.code },
  { key: "name", label: "銘柄名", value: (s) => trimName(s.name), title: (s) => s.name },
  { key: "close", label: "終値", value: (s) => formatClose(s.close) },
  { key: "total_score", label: "総合スコア", value: (s) => s.total_score ?? "-" },
  { key: "rs", label: "RS", value: (s) => s.rs ?? "-" },
  {
    key: "sector",
    label: "セクター(強度)",
    value: sectorStrengthHtml,
    html: true,
    sortValue: (s) => ({ 強: 2, 中: 1, 弱: 0 }[s.sector_strength] ?? -1),
  },
  {
    key: "high_dist",
    label: "52週高値距離",
    value: (s) => (s.high52w_distance_pct != null ? `-${s.high52w_distance_pct}%` : "-"),
    sortValue: (s) => (s.high52w_distance_pct != null ? -s.high52w_distance_pct : -Infinity),
  },
  { key: "footprint", label: "フットプリント", value: (s) => s.footprint ?? "-" },
  { key: "pivot", label: "ピボット", value: (s) => s.pivot ?? "-" },
  { key: "buy_stop", label: "推奨逆指値", value: (s) => s.buy_stop ?? "-" },
  { key: "stop_loss", label: "推奨損切り", value: (s) => s.stop_loss ?? "-" },
  { key: "risk_pct", label: "リスク%", value: (s) => s.risk_pct ?? "-" },
  {
    key: "fund_status",
    label: "ファンダ状態",
    value: fundStatusLabel,
    sortValue: (s) => (s.fund_stale ? -1 : s.fund_coverage === "full" ? 2 : s.fund_coverage === "partial" ? 1 : 0),
  },
];

// ---------------------------------------------------------------------------
// Dashboard (index.html)
// ---------------------------------------------------------------------------

let pendingFund = {};

async function initDashboard() {
  if (!window.MINERVINI_CONFIG.passkeyAuthEnabled) {
    hidePasskeyAuthUi();
  } else {
    wireHeaderButtons();
    if (window.MinerviniFundamentalsUI) {
      window.MinerviniFundamentalsUI.onSaved = initDashboard;
    }
    await initVaultUi();
  }

  // no-store: the daily bot commit refreshes these files; a heuristically
  // cached copy is exactly the "dashboard shows two-day-old data" failure.
  const [report, breadth, indices] = await Promise.all([
    fetch("data/report.json", { cache: "no-store" }).then((r) => r.json()),
    fetch("data/breadth.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ history: [] })),
    // indices.json only exists after the first pipeline run with the market
    // overview feature; render nothing (section stays hidden) until then.
    fetch("data/indices.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);

  pendingFund = window.MinerviniFundamentalsUI
    ? window.MinerviniFundamentalsUI.reconcilePending(report.generated_at)
    : {};

  renderHeader(report);
  renderMarketOverview(indices);
  renderBreadth(breadth, report);
  renderP1Warning(report);
  renderTier(report, "confirmed", "confirmed-tier-body");
  renderTier(report, "pool", "pool-tier-body");
  renderPriorityTier(report, "watchlist-tier-body");
  startLiveIndices();
}

// Kill switch: hides every passkey/write-related control so the dashboard
// reads as plain read-only while the feature is still being tuned. Uses a
// class (".passkey-gated") rather than a fixed id list, since write-gated
// buttons are now spread across the dashboard header AND the batch view.
function hidePasskeyAuthUi() {
  document.querySelectorAll(".passkey-gated").forEach((el) => {
    el.style.display = "none";
  });
}

function wireHeaderButtons() {
  const settingsBtn = document.getElementById("settings-btn");
  if (settingsBtn && !settingsBtn.dataset.wired) {
    settingsBtn.dataset.wired = "1";
    settingsBtn.addEventListener("click", () => window.MinerviniFundamentalsUI.openVaultSetupModal({ isRotation: true }));
  }
}

// ---------------------------------------------------------------------------
// Vault (passkey unlock) state -- controls whether write actions are enabled.
// ---------------------------------------------------------------------------

async function initVaultUi() {
  const unlockBtn = document.getElementById("vault-unlock-btn");
  if (!unlockBtn || unlockBtn.dataset.wired) {
    applyLockState(window.MinerviniGitHub.hasToken());
    return;
  }
  unlockBtn.dataset.wired = "1";

  let vault = null;
  try {
    vault = await window.MinerviniVault.fetchVault();
  } catch (e) {
    unlockBtn.textContent = "🔓 解錠 (エラー)";
    unlockBtn.title = e.message || String(e);
  }

  if (!vault) {
    unlockBtn.textContent = "🔐 初回セットアップ";
    unlockBtn.addEventListener("click", () => window.MinerviniFundamentalsUI.openVaultSetupModal({ isRotation: false }));
    applyLockState(false);
    return;
  }

  unlockBtn.textContent = "🔓 解錠";
  unlockBtn.addEventListener("click", async () => {
    unlockBtn.disabled = true;
    const original = unlockBtn.textContent;
    unlockBtn.textContent = "認証中...";
    try {
      await window.MinerviniVault.unlock(vault);
      unlockBtn.textContent = "🔒 解錠済み";
      applyLockState(true);
    } catch (e) {
      unlockBtn.textContent = original;
      alert(`解錠に失敗しました: ${e.message || e}`);
    } finally {
      unlockBtn.disabled = window.MinerviniGitHub.hasToken(); // stays disabled once unlocked (nothing more to do)
    }
  });

  applyLockState(window.MinerviniGitHub.hasToken());
}

// Enables/disables every write-capable button (fund edit, manual rerun)
// based on whether the vault is currently unlocked.
function applyLockState(unlocked) {
  document.querySelectorAll(".write-btn").forEach((btn) => {
    if (btn.id === "vault-unlock-btn") return; // has its own state, not a plain toggle
    btn.disabled = !unlocked;
  });
  document.querySelectorAll(".fund-edit-btn").forEach((btn) => {
    btn.disabled = !unlocked;
    btn.title = unlocked ? "" : "先に🔓解錠してください";
  });
}

function renderHeader(report) {
  const el = document.getElementById("generated-at");
  const when = report.generated_at ? new Date(report.generated_at).toLocaleString("ja-JP") : "-";
  el.textContent = `最終更新: ${when} / ユニバース: ${report.universe_size}銘柄 / テンプレート通過: ${report.template_pass}銘柄`;
}

// ---------------------------------------------------------------------------
// Market overview (indices.json): one card per index with last value,
// day-over-day change, and an inline SVG sparkline of the recent series.
// ---------------------------------------------------------------------------

function formatIndexValue(entry) {
  const v = entry.last;
  if (v == null) return "-";
  if (entry.unit === "%") return v.toFixed(3) + "%";
  return v.toLocaleString("ja-JP", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatIndexChange(entry) {
  if (entry.change == null) return "-";
  const sign = entry.change > 0 ? "+" : "";
  // Yields move in points, not percent-of-themselves; everything else in %.
  if (entry.unit === "%") return `${sign}${entry.change.toFixed(3)}pt`;
  const pct = entry.change_pct != null ? ` (${sign}${entry.change_pct.toFixed(2)}%)` : "";
  return `${sign}${entry.change.toLocaleString("ja-JP")}${pct}`;
}

function sparklineSvg(series, isUp) {
  const points = (series || []).slice(-60).map((p) => p.v);
  if (points.length < 2) return "";
  const w = 120;
  const h = 32;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = w / (points.length - 1);
  const coords = points
    .map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`)
    .join(" ");
  const color = isUp ? "var(--accent)" : "var(--danger)";
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

// "YYYY-MM-DD" -> "M/D" (年なし)。不正値はそのまま返す。
function shortDate(dateStr) {
  if (!dateStr) return "";
  const parts = String(dateStr).split("-");
  if (parts.length !== 3) return dateStr;
  return `${parseInt(parts[1], 10)}/${parseInt(parts[2], 10)}`;
}

function renderMarketOverview(indices) {
  const section = document.getElementById("market-overview");
  const cards = document.getElementById("market-cards");
  if (!section || !cards) return;
  if (!indices || !indices.indices || !indices.indices.length) return; // stays hidden

  const staleKeys = new Set(indices.stale_keys || []);
  cards.innerHTML = indices.indices
    .map((entry) => {
      const isUp = (entry.change ?? 0) >= 0;
      const stale = staleKeys.has(entry.key);
      return `
        <div class="market-card${stale ? " is-stale" : ""}">
          <div class="market-card-name">${entry.name}${stale ? '<span class="stale-badge" title="最新データの取得に失敗（キャッシュ表示）">stale</span>' : ""}</div>
          <div class="market-card-value">${formatIndexValue(entry)}</div>
          <div class="market-card-change ${isUp ? "chg-up" : "chg-down"}">${formatIndexChange(entry)}</div>
          ${sparklineSvg(entry.series, isUp)}
          <div class="market-card-date">${shortDate(entry.last_date)}</div>
        </div>`;
    })
    .join("");
  section.hidden = false;

  const meta = document.getElementById("market-live-meta");
  if (meta && indices.generated_at) {
    const when = new Date(indices.generated_at).toLocaleTimeString("ja-JP");
    meta.textContent = `指数データ取得: ${when} 時点`;
  }
}

// ---------------------------------------------------------------------------
// 指数カードの擬似リアルタイム更新。
// このサイトは静的GitHub Pages(バックエンドサーバーなし)のため、ティック
// 単位の真のリアルタイム配信はできない。その代わり、市場時間中はGitHub
// Actions側(intraday-indices.yml)が indices.json を15分間隔で更新する
// 運用にし、フロント側はこの間隔でポーリングして再描画することで、ページを
// 開きっぱなしでも手動リロードなしに追従できるようにする。
// ---------------------------------------------------------------------------
const LIVE_INDICES_POLL_MS = 60_000; // ポーリング自体は60秒毎(データの更新頻度自体は15分毎)

function startLiveIndices() {
  const section = document.getElementById("market-overview");
  const badge = document.getElementById("market-live-badge");
  if (!section) return;
  if (badge) badge.hidden = false;

  setInterval(async () => {
    if (document.hidden) return; // バックグラウンドタブでは無駄にフェッチしない
    try {
      const res = await fetch("data/indices.json", { cache: "no-store" });
      if (!res.ok) return;
      const indices = await res.json();
      renderMarketOverview(indices);
    } catch (e) {
      // 通信エラーは無視し、既存表示を維持したまま次回ポーリングに任せる。
    }
  }, LIVE_INDICES_POLL_MS);
}

function renderBreadth(breadth, report) {
  const el = document.getElementById("breadth-meter");
  if (!breadth.history || !breadth.history.length) {
    el.textContent = "地合いデータなし";
    return;
  }
  const latest = breadth.history[breadth.history.length - 1];
  const passRate = latest.template_pass_rate != null ? (latest.template_pass_rate * 100).toFixed(1) + "%" : "-";
  const successRate =
    latest.breakout_success_rate != null ? (latest.breakout_success_rate * 100).toFixed(0) + "%" : "-";
  // 地合い指標: 8条件完全一致の件数。breadth履歴優先、なければreport.jsonから。
  const pc = (report && report.priority_counts) || null;
  const p1 = latest.p1_count ?? (pc ? pc.p1 : null);
  const prioLine = p1 != null ? `<span>8条件合格: ${p1}件</span>` : "";
  el.innerHTML = `
    <span>テンプレート通過率: ${passRate}</span>
    <span>セットアップ数: ${latest.watch_count ?? "-"}</span>
    <span>直近ブレイク成功率: ${successRate}</span>
    ${prioLine}
  `;
}

// 8条件完全一致の銘柄が極端に少ない場合の弱地合い警告バナー。
function renderP1Warning(report) {
  const el = document.getElementById("p1-warning");
  if (!el) return;
  if (report.p1_scarce) {
    const p1 = report.priority_counts ? report.priority_counts.p1 : 0;
    el.textContent = `⚠ 8条件完全一致の銘柄が${p1}件と極端に少ない状態です。地合いが弱い可能性が高く、新規エントリーは慎重に。`;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
}

function renderTier(report, tier, containerId) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  const stocks = report.stocks.filter((s) => s.tier === tier);
  if (!stocks.length) {
    container.innerHTML = '<p class="tier-note">該当銘柄なし</p>';
    return;
  }
  for (const status of STATUS_ORDER) {
    const group = stocks.filter((s) => s.status === status);
    if (!group.length) continue;
    container.appendChild(renderStatusSection(status, group, tier));
  }
}

function renderStatusSection(status, stocks, tier) {
  const section = document.createElement("div");
  section.className = "status-section status-" + status;
  const h3 = document.createElement("h3");
  h3.textContent = `${STATUS_LABELS[status] || status} (${stocks.length})`;
  section.appendChild(h3);
  section.appendChild(renderTable(stocks, tier));
  return section;
}

function renderTable(stocks, tier, options = {}) {
  let sortKey = options.initialSortKey || "total_score";
  let sortDesc = options.initialSortDesc ?? true;

  const wrapper = document.createElement("div");
  wrapper.className = "table-scroll";
  const table = document.createElement("table");
  wrapper.appendChild(table);

  function sortValueFor(s, col) {
    if (col.sortValue) return col.sortValue(s);
    return s[col.key] ?? -Infinity;
  }

  function draw() {
    const activeCol = COLUMNS.find((c) => c.key === sortKey) || COLUMNS[0];
    const sorted = [...stocks].sort((a, b) => {
      const av = sortValueFor(a, activeCol);
      const bv = sortValueFor(b, activeCol);
      if (av === bv) return 0;
      const cmp = av > bv ? 1 : -1;
      return sortDesc ? -cmp : cmp;
    });

    table.innerHTML = "";
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const col of COLUMNS) {
      const th = document.createElement("th");
      th.textContent = col.label + (sortKey === col.key ? (sortDesc ? " ▼" : " ▲") : "");
      th.addEventListener("click", () => {
        if (sortKey === col.key) sortDesc = !sortDesc;
        else {
          sortKey = col.key;
          sortDesc = true;
        }
        draw();
      });
      headRow.appendChild(th);
    }
    headRow.appendChild(document.createElement("th")); // fund input/edit button column
    thead.appendChild(headRow);
    table.appendChild(thead);

    const authEnabled = window.MINERVINI_CONFIG.passkeyAuthEnabled;
    const tbody = document.createElement("tbody");
    for (const s of sorted) {
      const row = document.createElement("tr");
      if (s.fund_stale) row.classList.add("fund-stale");
      // has_chart===false の銘柄(チャートJSON未生成)は詳細ページへ遷移できない。
      const navigable = s.has_chart !== false;
      if (navigable) {
        row.addEventListener("click", (e) => {
          if (e.target.tagName === "BUTTON") return;
          window.location.href = `stock.html?code=${encodeURIComponent(s.code)}`;
        });
      } else {
        row.classList.add("row-static");
      }
      for (const col of COLUMNS) {
        const td = document.createElement("td");
        const val = col.value(s);
        if (col.html) td.innerHTML = val;
        else td.textContent = val;
        if (col.title) td.title = col.title(s);
        row.appendChild(td);
      }
      const actionTd = document.createElement("td");
      const btn = document.createElement("button");
      btn.textContent = "ファンダ入力/編集";
      btn.className = "fund-edit-btn";
      // With passkey auth enabled the button unlocks via the vault; with it
      // disabled ("no git key" mode) the modal falls back to clipboard +
      // manual GitHub commit, so the button always works.
      if (authEnabled && !window.MinerviniGitHub.hasToken()) {
        btn.disabled = true;
        btn.title = "先に🔓解錠してください";
      }
      btn.addEventListener("click", () => window.MinerviniFundamentalsUI.openFundamentalsModal(s.code, s.name));
      actionTd.appendChild(btn);
      if (window.MinerviniFundamentalsUI && window.MinerviniFundamentalsUI.isPending(pendingFund, s.code)) {
        const badge = document.createElement("span");
        badge.className = "pending-badge";
        badge.textContent = "入力済み・次回実行で本命に昇格予定";
        actionTd.appendChild(badge);
      }
      row.appendChild(actionTd);
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
  }

  draw();
  return wrapper;
}

function formatClose(v) {
  return v == null ? "-" : Number(v).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
}

function fundStatusLabel(s) {
  if (s.fund_stale) return "再確認推奨";
  if (s.fund_coverage === "full") return "full";
  if (s.fund_coverage === "partial") return "partial";
  return "-";
}

// ---------------------------------------------------------------------------
// 〔監視〕8条件合格・セットアップ形成待ち(旧P1)一覧。P2〜P4はUI廃止(データは
// report.jsonに残るがダッシュボードには出さない)。全件をRS降順で表示する。
// 列は本命/候補プールと共通のCOLUMNSを使い、renderTableのみ初期ソートをRS降順に上書きする。
// ---------------------------------------------------------------------------

function renderPriorityTier(report, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";
  // 8条件合格(旧P1)のみ。priority未設定の旧データも合格扱いで拾う。
  const stocks = report.stocks
    .filter((s) => s.tier === "watchlist" && (s.priority === 1 || s.priority == null))
    .sort((a, b) => (b.rs ?? 0) - (a.rs ?? 0));
  if (!stocks.length) {
    container.innerHTML = '<p class="tier-note">該当銘柄なし</p>';
    return;
  }
  const note = document.createElement("p");
  note.className = "tier-note";
  note.textContent = `全${stocks.length}件 (RS降順)`;
  container.appendChild(note);
  container.appendChild(renderTable(stocks, "watchlist", { initialSortKey: "rs", initialSortDesc: true }));
}

// ---------------------------------------------------------------------------
// Stock detail page (stock.html)
// ---------------------------------------------------------------------------

async function initStockPage() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (!code) {
    document.getElementById("stock-title").textContent = "銘柄コードが指定されていません";
    return;
  }

  const [report, chart] = await Promise.all([
    fetch("data/report.json", { cache: "no-store" }).then((r) => r.json()),
    fetch(`data/charts/${encodeURIComponent(code)}.json`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
  ]);
  const stock = report.stocks.find((s) => s.code === code);

  document.getElementById("stock-title").textContent = `${code} ${stock ? stock.name : ""}`;
  if (stock) renderStockMeta(stock);

  if (!chart) {
    document.getElementById("chart-container").textContent = "チャートデータがありません";
    return;
  }
  renderCharts(chart);

  if (stock) {
    renderMustChecklist(stock.must_flags);
    renderScoreBreakdown(stock);
  }
}

function renderStockMeta(stock) {
  const items = [
    ["終値", stock.close],
    ["ステータス", stock.status],
    ["総合スコア", stock.total_score],
    ["RS", stock.rs],
    ["ピボット", stock.pivot],
    ["推奨逆指値", stock.buy_stop],
    ["推奨損切り", stock.stop_loss],
    ["リスク%", stock.risk_pct],
  ];
  document.getElementById("stock-meta").innerHTML = items
    .map(
      ([label, value]) =>
        `<span class="chip"><span class="chip-label">${label}</span><span class="chip-value">${value ?? "-"}</span></span>`
    )
    .join("");
}

// Formats the hovered/crosshair date label as "MM/DD" (zero-padded). Chart
// data uses "YYYY-MM-DD" strings, which Lightweight Charts parses into a
// {year, month, day} BusinessDay object.
function formatChartDate(time) {
  let d;
  if (typeof time === "string") {
    // "YYYY-MM-DD" business-day string, as used by tickMarkFormatter
    const [y, m, day] = time.split("-").map(Number);
    d = new Date(y, m - 1, day);
  } else if (typeof time === "object" && time !== null) {
    // {year, month, day} BusinessDay object, as used by timeFormatter
    d = new Date(time.year, time.month - 1, time.day);
  } else {
    // UTCTimestamp (seconds since epoch)
    d = new Date(time * 1000);
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

const CHART_LOCALIZATION = { timeFormatter: formatChartDate };
const CHART_TIME_SCALE = { tickMarkFormatter: formatChartDate };
const CHART_COLORS = {
  bg: "#131720",
  grid: "#232a38",
  text: "#98a2b3",
  up: "#34d399",
  down: "#f87171",
  volUp: "rgba(52, 211, 153, 0.45)",
  volDown: "rgba(248, 113, 113, 0.45)",
};

// Aggregates the daily series into monthly bars (open=first, high=max,
// low=min, close=last, volume=sum, rs=last), keyed by "YYYY-MM". The bar's
// `time` is the month's first trading day; the last month may be partial.
function aggregateMonthly(chart) {
  const byMonth = new Map();
  for (const c of chart.candles) {
    const key = c.time.slice(0, 7);
    const m = byMonth.get(key);
    if (!m) {
      byMonth.set(key, { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, volume: 0, rs: null });
    } else {
      m.high = Math.max(m.high, c.high);
      m.low = Math.min(m.low, c.low);
      m.close = c.close;
    }
  }
  for (const v of chart.volume || []) {
    const m = byMonth.get(v.time.slice(0, 7));
    if (m) m.volume += v.value;
  }
  for (const r of chart.rs_line || []) {
    const m = byMonth.get(r.time.slice(0, 7));
    if (m) m.rs = r.value;
  }
  const months = Array.from(byMonth.values());
  return {
    candles: months.map((m) => ({ time: m.time, open: m.open, high: m.high, low: m.low, close: m.close })),
    volume: months.map((m) => ({ time: m.time, value: m.volume, color: m.close >= m.open ? CHART_COLORS.volUp : CHART_COLORS.volDown })),
    rs_line: months.filter((m) => m.rs != null).map((m) => ({ time: m.time, value: m.rs })),
  };
}

function colorizeVolume(chart) {
  const dirByTime = new Map(chart.candles.map((c) => [c.time, c.close >= c.open]));
  return (chart.volume || []).map((v) => ({
    ...v,
    color: dirByTime.get(v.time) === false ? CHART_COLORS.volDown : CHART_COLORS.volUp,
  }));
}

function makeChart(el, { showTimeAxis }) {
  return LightweightCharts.createChart(el, {
    width: el.clientWidth,
    height: el.clientHeight,
    layout: { background: { color: CHART_COLORS.bg }, textColor: CHART_COLORS.text },
    grid: { vertLines: { color: CHART_COLORS.grid }, horzLines: { color: CHART_COLORS.grid } },
    // Fixed-width right axis keeps all panes horizontally aligned.
    rightPriceScale: { minimumWidth: 72, borderColor: CHART_COLORS.grid },
    localization: CHART_LOCALIZATION,
    // fixRightEdge: the latest bar stays pinned to the right edge, so the
    // axis dates always count back from the newest date.
    timeScale: { ...CHART_TIME_SCALE, visible: showTimeAxis, borderColor: CHART_COLORS.grid, fixRightEdge: true, rightOffset: 0 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    // 価格軸ドラッグでの縮尺変更を無効化(オートスケール固定)。時間軸は可。
    handleScale: {
      axisPressedMouseMove: { time: true, price: false },
      mouseWheel: true,
      pinch: true,
    },
    // スマホ: 縦スワイプはページスクロールに渡す(チャートは横のみ掴む)。
    handleScroll: {
      vertTouchDrag: false,
      horzTouchDrag: true,
      pressedMouseMove: true,
      mouseWheel: true,
    },
  });
}

// Pins a small "latest date" label onto the time axis (bottom-right of the
// pane, just left of the price axis) -- Lightweight Charts' auto ticks don't
// guarantee the newest date gets a label, so we draw our own.
function addLatestDateLabel(el, dateStr) {
  const label = document.createElement("span");
  label.className = "latest-date-label";
  label.textContent = dateStr;
  el.appendChild(label);
  alignLatestDateLabel(el, label);
  return label;
}

// Lightweight Chartsは日付目盛を専用の細いcanvas帯として最下部に描画する。
// このオーバーレイは決め打ちのpx値(以前は bottom:2px 固定)ではなく、その
// canvas帯の実際の高さ・位置を毎回計測して合わせる。これによりライブラリの
// デフォルト(フォントサイズ/パディング)が変わっても他パネルの目盛と常に
// 揃う(以前は下にズレて見えていた)。
function alignLatestDateLabel(el, label) {
  const canvases = el.querySelectorAll("canvas");
  if (!canvases.length) return;
  const axisCanvas = canvases[canvases.length - 1];
  const axisRect = axisCanvas.getBoundingClientRect();
  if (axisRect.height <= 0 || axisRect.height > 60) return; // 想定外のDOM構造ならCSSの既定値のまま
  const containerRect = el.getBoundingClientRect();
  const bottomOffset = Math.max(0, containerRect.bottom - axisRect.bottom);
  label.style.bottom = `${bottomOffset}px`;
  label.style.height = `${axisRect.height}px`;
}

// Two-way pan/zoom sync so dragging any pane moves the others in lockstep.
function syncTimeScales(charts) {
  let syncing = false;
  for (const source of charts) {
    source.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (syncing || !range) return;
      syncing = true;
      for (const target of charts) {
        if (target !== source) target.timeScale().setVisibleLogicalRange(range);
      }
      syncing = false;
    });
  }
}

// 期間ボタン(1ヶ月〜2年)の営業日換算。初期表示は日足1ヶ月。
const DEFAULT_DAILY_BARS = 22;

function renderCharts(chart) {
  const monthly = aggregateMonthly(chart);
  const dailyVolume = colorizeVolume(chart);

  const priceEl = document.getElementById("chart-container");
  const volEl = document.getElementById("volume-container");
  const rsEl = document.getElementById("rs-container");
  const hasRs = !!(chart.rs_line && chart.rs_line.length);
  if (!hasRs) {
    const rsCard = document.getElementById("rs-card");
    if (rsCard) rsCard.remove();
  }

  // Each pane lives in its own card now, so each shows its own time axis
  // (they still pan/zoom in lockstep via syncTimeScales).
  const priceChart = makeChart(priceEl, { showTimeAxis: true });
  const volChart = makeChart(volEl, { showTimeAxis: true });
  const rsChart = hasRs ? makeChart(rsEl, { showTimeAxis: true }) : null;

  const candleSeries = priceChart.addCandlestickSeries({
    upColor: CHART_COLORS.up,
    downColor: CHART_COLORS.down,
    borderUpColor: CHART_COLORS.up,
    borderDownColor: CHART_COLORS.down,
    wickUpColor: CHART_COLORS.up,
    wickDownColor: CHART_COLORS.down,
  });
  const ma50 = priceChart.addLineSeries({ color: "#60a5fa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const ma150 = priceChart.addLineSeries({ color: "#fbbf24", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  const ma200 = priceChart.addLineSeries({ color: "#c084fc", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

  const volSeries = volChart.addHistogramSeries({ priceFormat: { type: "volume" } });
  // Pin the histogram base to the pane's bottom edge so the auto-scaled
  // axis never extends into negative territory.
  volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.15, bottom: 0 } });

  const rsSeries = rsChart ? rsChart.addLineSeries({ color: "#2dd4bf", lineWidth: 1 }) : null;

  // Pivot / stop-loss horizontal lines: OFF by default, toggled via the
  // checkboxes in the toolbar. Handles are kept so the lines can be removed.
  let pivotLine = null;
  let stopLine = null;

  function wireLineToggle(id, price, apply) {
    const box = document.getElementById(id);
    if (!box) return;
    if (price == null) {
      box.disabled = true;
      box.closest("label")?.classList.add("disabled");
      return;
    }
    box.checked = false;
    box.addEventListener("change", () => apply(box.checked));
  }

  wireLineToggle("toggle-pivot", chart.pivot, (on) => {
    if (on && !pivotLine) {
      pivotLine = candleSeries.createPriceLine({ price: chart.pivot, color: CHART_COLORS.up, lineWidth: 1, lineStyle: 2, title: "ピボット" });
    } else if (!on && pivotLine) {
      candleSeries.removePriceLine(pivotLine);
      pivotLine = null;
    }
  });
  wireLineToggle("toggle-stop", chart.stop_loss, (on) => {
    if (on && !stopLine) {
      stopLine = candleSeries.createPriceLine({ price: chart.stop_loss, color: CHART_COLORS.down, lineWidth: 1, lineStyle: 2, title: "損切り" });
    } else if (!on && stopLine) {
      candleSeries.removePriceLine(stopLine);
      stopLine = null;
    }
  });

  const charts = [priceChart, volChart, ...(rsChart ? [rsChart] : [])];
  syncTimeScales(charts);
  window.__minerviniCharts = charts; // debug/testing handle

  // ---- crosshair OHLCV legend -------------------------------------------
  // Lookup tables keyed by bar time so hovering ANY pane can resolve the
  // full 日付/OHLC/出来高 row for that day (or month, on the monthly view).
  function buildBarLookup(candles, volume) {
    const volByTime = new Map((volume || []).map((v) => [v.time, v.value]));
    const map = new Map();
    for (const c of candles) map.set(c.time, { ...c, volume: volByTime.get(c.time) });
    return map;
  }
  const barLookup = {
    D: buildBarLookup(chart.candles, chart.volume),
    M: buildBarLookup(monthly.candles, monthly.volume),
  };
  let currentTf = "D";

  const legendEl = document.getElementById("ohlc-legend");

  function timeKey(time) {
    if (typeof time === "string") return time;
    if (typeof time === "object" && time !== null) {
      const mm = String(time.month).padStart(2, "0");
      const dd = String(time.day).padStart(2, "0");
      return `${time.year}-${mm}-${dd}`;
    }
    return null;
  }

  function fmtPrice(v) {
    return v == null ? "-" : v.toLocaleString("ja-JP");
  }

  function fmtVolume(v) {
    if (v == null) return "-";
    if (v >= 1e8) return (v / 1e8).toFixed(2) + "億";
    if (v >= 1e4) return (v / 1e4).toFixed(1) + "万";
    return String(Math.round(v));
  }

  function updateLegend(bar) {
    if (!legendEl) return;
    if (!bar) {
      legendEl.innerHTML = "";
      return;
    }
    const dirClass = bar.close >= bar.open ? "chg-up" : "chg-down";
    legendEl.innerHTML = `
      <span class="lg-date">${bar.time}</span>
      <span>始 <b class="${dirClass}">${fmtPrice(bar.open)}</b></span>
      <span>高 <b class="${dirClass}">${fmtPrice(bar.high)}</b></span>
      <span>安 <b class="${dirClass}">${fmtPrice(bar.low)}</b></span>
      <span>終 <b class="${dirClass}">${fmtPrice(bar.close)}</b></span>
      <span>出来高 <b>${fmtVolume(bar.volume)}</b></span>`;
  }

  function latestBar() {
    const candles = currentTf === "M" ? monthly.candles : chart.candles;
    if (!candles.length) return null;
    return barLookup[currentTf].get(candles[candles.length - 1].time) || null;
  }

  for (const c of charts) {
    c.subscribeCrosshairMove((param) => {
      const key = param.time != null ? timeKey(param.time) : null;
      const bar = key ? barLookup[currentTf].get(key) : null;
      updateLegend(bar || latestBar()); // fall back to the latest bar when not hovering
    });
  }

  // tf: "M" (月足・全期間) or 表示する日足本数 (数値文字列)。
  function setTimeframe(tf) {
    const isMonthly = tf === "M";
    currentTf = isMonthly ? "M" : "D";
    candleSeries.setData(isMonthly ? monthly.candles : chart.candles);
    volSeries.setData(isMonthly ? monthly.volume : dailyVolume);
    if (rsSeries) rsSeries.setData(isMonthly ? monthly.rs_line : chart.rs_line);
    // Daily MAs have no meaning on monthly bars; hide them there.
    ma50.setData(isMonthly ? [] : chart.ma50 || []);
    ma150.setData(isMonthly ? [] : chart.ma150 || []);
    ma200.setData(isMonthly ? [] : chart.ma200 || []);
    if (isMonthly) {
      for (const c of charts) c.timeScale().fitContent();
    } else {
      // 最新バーを右端に固定し、そこから指定本数だけ遡って表示。
      const bars = parseInt(tf, 10) || DEFAULT_DAILY_BARS;
      const n = chart.candles.length;
      const range = { from: Math.max(0, n - bars), to: n };
      for (const c of charts) c.timeScale().setVisibleLogicalRange(range);
    }
    updateLegend(latestBar());
  }

  setTimeframe(String(DEFAULT_DAILY_BARS));

  // 最新日付を各ペインの日付軸上に常時表示(オートticksは最新日を保証しないため)。
  // 表示は年なしの「月/日」形式。
  const lastDate = chart.candles.length ? chart.candles[chart.candles.length - 1].time : null;
  let dateLabels = [];
  if (lastDate) {
    const [, m, d] = lastDate.split("-");
    const shortDate = `${parseInt(m, 10)}/${parseInt(d, 10)}`;
    dateLabels = [priceEl, volEl, ...(rsChart ? [rsEl] : [])].map((el) => ({ el, label: addLatestDateLabel(el, shortDate) }));
  }

  const toggle = document.getElementById("timeframe-toggle");
  if (toggle) {
    toggle.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tf]");
      if (!btn) return;
      toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      setTimeframe(btn.dataset.tf);
    });
  }

  window.addEventListener("resize", () => {
    for (const [c, el] of [[priceChart, priceEl], [volChart, volEl], ...(rsChart ? [[rsChart, rsEl]] : [])]) {
      c.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    }
    for (const { el, label } of dateLabels) alignLatestDateLabel(el, label);
  });
}

// Japanese labels for the MUST-condition flags. Unknown keys fall back to
// the raw flag name so新規条件を足してもUIが壊れない.
const MUST_FLAG_LABELS = {
  tt: {
    close_above_ma150_ma200: "終値が150日線・200日線より上",
    ma150_above_ma200: "150日線が200日線より上",
    ma200_uptrend_1m: "200日線が1ヶ月以上上向き",
    ma_stack_50_150_200: "50日線 > 150日線 > 200日線",
    close_above_ma50: "終値が50日線より上",
    above_low52w_margin: "52週安値から+25%以上",
    within_high52w_margin: "52週高値から-25%以内",
    rs_above_min: "RSレーティング70以上",
  },
  vcp: {
    V1: "収縮回数が2〜6回",
    V2: "収縮の深さが段階的に減少",
    V3: "最初の収縮が35%以内",
    V4: "最後の収縮が10%以内",
    V5: "出来高ドライアップ(直近10日 ≤ 50日平均×0.8)",
    V6: "ベース期間が15〜200日",
    V7: "収縮の安値が切り上がり",
  },
};

function renderMustChecklist(mustFlags) {
  const el = document.getElementById("must-checklist");
  el.innerHTML = "";
  if (!mustFlags) {
    el.textContent = "データなし";
    return;
  }
  const groupLabels = { tt: "トレンドテンプレート (8条件)", vcp: "VCP (V1〜V7)" };
  for (const key of ["tt", "vcp"]) {
    const flags = mustFlags[key];
    if (!flags) continue;
    const h4 = document.createElement("h4");
    h4.textContent = groupLabels[key];
    el.appendChild(h4);
    const ul = document.createElement("ul");
    const labels = MUST_FLAG_LABELS[key] || {};
    for (const [name, value] of Object.entries(flags)) {
      const li = document.createElement("li");
      li.textContent = `${value ? "✓" : "✗"} ${labels[name] || name}`;
      li.className = value ? "flag-pass" : "flag-fail";
      ul.appendChild(li);
    }
    el.appendChild(ul);
  }
}

function renderScoreBreakdown(stock) {
  const el = document.getElementById("score-breakdown");
  el.innerHTML = "";
  const items = [
    { label: "テクニカルスコア", value: stock.tech_score },
    { label: "フルスコア", value: stock.full_score },
    { label: "VCPスコア", value: stock.vcp_score },
    { label: "総合スコア", value: stock.total_score },
  ];
  for (const item of items) {
    if (item.value == null) continue;
    const row = document.createElement("div");
    row.className = "score-bar-row";
    const pct = Math.max(0, Math.min(100, item.value));
    row.innerHTML = `<span>${item.label}</span><div class="score-bar"><div class="score-bar-fill" style="width:${pct}%"></div></div><span>${item.value}</span>`;
    el.appendChild(row);
  }
}

// ---------------------------------------------------------------------------
// SPA router (Dockナビ): index.htmlの4ビューをlocation.hashで切り替える。
// セクターマップ/バッチ実行は表示のたびに再init(ヒートマップはコンテナが
// 見えて初めてclientWidthが正しく取れるため、バッチ実行は履歴を毎回最新化
// するため)。
// ---------------------------------------------------------------------------

const VIEWS = ["dashboard", "sectormap", "invest", "batch"];

function showView(name) {
  if (!VIEWS.includes(name)) name = "dashboard";
  for (const v of VIEWS) {
    const section = document.getElementById(`view-${v}`);
    if (section) section.hidden = v !== name;
  }
  document.querySelectorAll(".dock-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  if (name === "sectormap" && typeof initHeatmap === "function") {
    initHeatmap();
  }
  if (name === "batch" && window.MinerviniBatch) {
    window.MinerviniBatch.initBatchView();
  }
}

function initRouter() {
  const dock = document.getElementById("dock-nav");
  if (!dock) return;
  dock.querySelectorAll(".dock-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      window.location.hash = btn.dataset.view;
    });
  });
  window.addEventListener("hashchange", () => {
    showView(window.location.hash.replace("#", "") || "dashboard");
  });
  showView(window.location.hash.replace("#", "") || "dashboard");
}

// ---------------------------------------------------------------------------

if (document.getElementById("confirmed-tier-body")) {
  initDashboard();
}
if (document.getElementById("chart-container")) {
  initStockPage();
}
if (document.getElementById("dock-nav")) {
  initRouter();
}
