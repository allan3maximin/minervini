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

const COLUMNS = [
  { key: "code", label: "コード" },
  { key: "name", label: "銘柄名" },
  { key: "total_score", label: "総合スコア" },
  { key: "rs", label: "RS" },
  { key: "footprint", label: "フットプリント" },
  { key: "pivot", label: "ピボット" },
  { key: "buy_stop", label: "推奨逆指値" },
  { key: "stop_loss", label: "推奨損切り" },
  { key: "risk_pct", label: "リスク%" },
  { key: "fund_status", label: "ファンダ状態" },
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
  const [report, breadth] = await Promise.all([
    fetch("data/report.json", { cache: "no-store" }).then((r) => r.json()),
    fetch("data/breadth.json", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ history: [] })),
  ]);

  pendingFund = window.MinerviniFundamentalsUI
    ? window.MinerviniFundamentalsUI.reconcilePending(report.generated_at)
    : {};

  renderHeader(report);
  renderBreadth(breadth);
  renderTier(report, "confirmed", "confirmed-tier-body");
  renderTier(report, "pool", "pool-tier-body");
  renderTier(report, "watchlist", "watchlist-tier-body");
}

// Kill switch: hides every passkey/write-related control so the dashboard
// reads as plain read-only while the feature is still being tuned.
function hidePasskeyAuthUi() {
  ["vault-unlock-btn", "rerun-btn", "settings-btn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  });
}

function wireHeaderButtons() {
  const settingsBtn = document.getElementById("settings-btn");
  if (settingsBtn && !settingsBtn.dataset.wired) {
    settingsBtn.dataset.wired = "1";
    settingsBtn.addEventListener("click", () => window.MinerviniFundamentalsUI.openVaultSetupModal({ isRotation: true }));
  }
  const rerunBtn = document.getElementById("rerun-btn");
  if (rerunBtn && !rerunBtn.dataset.wired) {
    rerunBtn.dataset.wired = "1";
    rerunBtn.addEventListener("click", () => {
      if (!window.MinerviniGitHub.hasToken()) return; // guarded by disabled state; safety net
      window.MinerviniFundamentalsUI.triggerManualRerun(rerunBtn);
    });
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

function renderBreadth(breadth) {
  const el = document.getElementById("breadth-meter");
  if (!breadth.history || !breadth.history.length) {
    el.textContent = "地合いデータなし";
    return;
  }
  const latest = breadth.history[breadth.history.length - 1];
  const passRate = latest.template_pass_rate != null ? (latest.template_pass_rate * 100).toFixed(1) + "%" : "-";
  const successRate =
    latest.breakout_success_rate != null ? (latest.breakout_success_rate * 100).toFixed(0) + "%" : "-";
  el.innerHTML = `
    <span>テンプレート通過率: ${passRate}</span>
    <span>セットアップ数: ${latest.watch_count ?? "-"}</span>
    <span>直近ブレイク成功率: ${successRate}</span>
  `;
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

function renderTable(stocks, tier) {
  let sortKey = "total_score";
  let sortDesc = true;

  const wrapper = document.createElement("div");
  wrapper.className = "table-scroll";
  const table = document.createElement("table");
  wrapper.appendChild(table);

  function draw() {
    const sorted = [...stocks].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
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
      row.addEventListener("click", (e) => {
        if (e.target.tagName === "BUTTON") return;
        window.location.href = `stock.html?code=${encodeURIComponent(s.code)}`;
      });
      for (const col of COLUMNS) {
        const td = document.createElement("td");
        td.textContent = col.key === "fund_status" ? fundStatusLabel(s) : s[col.key] ?? "-";
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

function fundStatusLabel(s) {
  if (s.fund_stale) return "再確認推奨";
  if (s.fund_coverage === "full") return "full";
  if (s.fund_coverage === "partial") return "partial";
  return "-";
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
    timeScale: { ...CHART_TIME_SCALE, visible: showTimeAxis, borderColor: CHART_COLORS.grid },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
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

function renderCharts(chart) {
  const monthly = aggregateMonthly(chart);
  const dailyVolume = colorizeVolume(chart);

  const priceEl = document.getElementById("chart-container");
  const volEl = document.getElementById("volume-container");
  const rsEl = document.getElementById("rs-container");
  const hasRs = !!(chart.rs_line && chart.rs_line.length);
  if (!hasRs && rsEl) rsEl.remove();

  // Only the bottom-most pane shows the time axis; the panes are synced.
  const priceChart = makeChart(priceEl, { showTimeAxis: false });
  const volChart = makeChart(volEl, { showTimeAxis: !hasRs });
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

  if (chart.pivot) {
    candleSeries.createPriceLine({ price: chart.pivot, color: CHART_COLORS.up, lineWidth: 1, lineStyle: 2, title: "pivot" });
  }
  if (chart.stop_loss) {
    candleSeries.createPriceLine({ price: chart.stop_loss, color: CHART_COLORS.down, lineWidth: 1, lineStyle: 2, title: "stop loss" });
  }

  const charts = [priceChart, volChart, ...(rsChart ? [rsChart] : [])];
  syncTimeScales(charts);
  window.__minerviniCharts = charts; // debug/testing handle

  function setTimeframe(tf) {
    const isMonthly = tf === "M";
    candleSeries.setData(isMonthly ? monthly.candles : chart.candles);
    volSeries.setData(isMonthly ? monthly.volume : dailyVolume);
    if (rsSeries) rsSeries.setData(isMonthly ? monthly.rs_line : chart.rs_line);
    // Daily MAs have no meaning on monthly bars; hide them there.
    ma50.setData(isMonthly ? [] : chart.ma50 || []);
    ma150.setData(isMonthly ? [] : chart.ma150 || []);
    ma200.setData(isMonthly ? [] : chart.ma200 || []);
    for (const c of charts) c.timeScale().fitContent();
  }

  setTimeframe("D");

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
  });
}

function renderMustChecklist(mustFlags) {
  const el = document.getElementById("must-checklist");
  el.innerHTML = "";
  if (!mustFlags) {
    el.textContent = "データなし";
    return;
  }
  const groupLabels = { tt: "トレンドテンプレート (8条件)", vcp: "VCP (V1-V7)" };
  for (const key of ["tt", "vcp"]) {
    const flags = mustFlags[key];
    if (!flags) continue;
    const h4 = document.createElement("h4");
    h4.textContent = groupLabels[key];
    el.appendChild(h4);
    const ul = document.createElement("ul");
    for (const [name, value] of Object.entries(flags)) {
      const li = document.createElement("li");
      li.textContent = `${value ? "✓" : "✗"} ${name}`;
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

if (document.getElementById("confirmed-tier-body")) {
  initDashboard();
}
if (document.getElementById("chart-container")) {
  initStockPage();
}
