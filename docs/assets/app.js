// エントリーステータスの日本語ラベル (src/report/summary.py の STATUS_LABELS_JA と対で保守)。
const STATUS_LABELS = {
  BREAKOUT: "本日のブレイクアウト",
  BREAKOUT_WEAK: "ブレイクアウト(出来高不足)",
  WATCH_A: "監視A(ピボット待ち)",
  WATCH_B: "監視B(ベース形成中)",
  EXTENDED: "伸びすぎ(追いかけ禁止)",
  REJECTED: "ベース不合格",
  IMMATURE: "ベース形成中(日数不足)",
  TOO_RECENT: "高値更新中(ベース未形成)",
  TOO_VOLATILE: "ボラ過大(VCP評価対象外)",
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
  "TOO_VOLATILE",
  "NO_BASE",
];

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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

// 枯れ度列: dryup_med_10_50 の値と2段階バッジ(枯れ気味/激枯れ)を表示する。
// バッジ種別(dryup.badge)はサーバ側(build_site._build_dryup_badge)がconfig閾値で確定済み。
function dryupBadgeHtml(s) {
  const d = s.dryup;
  if (!d || d.value == null) return "-";
  const v = Number(d.value).toFixed(2);
  if (d.badge === "extreme") return `<span class="dryup-badge dryup-badge-extreme">激枯れ ${v}</span>`;
  if (d.badge === "dryup") return `<span class="dryup-badge dryup-badge-dry">枯れ気味 ${v}</span>`;
  return v; // 閾値超(枯れていない)は数値のみ
}

// リスト画面カードのソートキー定義。横スクロール表を廃止したため、表示項目は
// カード側(renderCardList)に直書きし、ここには並び順の定義だけ残す。
const CARD_SORTS = {
  total_score: (s) => s.total_score ?? -Infinity,
  rs: (s) => s.rs ?? -Infinity,
};

// ---------------------------------------------------------------------------
// Dashboard (index.html)
// ---------------------------------------------------------------------------

let pendingFund = {};

async function initDashboard() {
  if (!window.MINERVINI_CONFIG.passkeyAuthEnabled) {
    hidePasskeyAuthUi();
  } else {
    if (window.MinerviniFundamentalsUI) {
      window.MinerviniFundamentalsUI.onSaved = initDashboard;
    }
    await initVaultUi();
  }

  // no-store: the daily bot commit refreshes these files; a heuristically
  // cached copy is exactly the "dashboard shows two-day-old data" failure.
  // fetchJson: 暗号化封筒(パスキー解錠後のデータ鍵で復号)/平文の両対応。
  const [report, breadth, indices, positionsData] = await Promise.all([
    window.MinerviniData.fetchJson("data/report.json"),
    window.MinerviniData.fetchJson("data/breadth.json", { optional: true }).then((b) => b || { history: [] }),
    // indices.json only exists after the first pipeline run with the market
    // overview feature; render nothing (section stays hidden) until then.
    window.MinerviniData.fetchJson("data/indices.json", { optional: true }),
    // positions.json only exists once manual/positions.csv has at least one row.
    window.MinerviniData.fetchJson("data/positions.json", { optional: true }),
  ]);

  pendingFund = window.MinerviniFundamentalsUI
    ? window.MinerviniFundamentalsUI.reconcilePending(report.generated_at)
    : {};

  renderHeader(report);
  renderMarketSignal(breadth);
  renderVcpFunnel(breadth);
  renderPositionsWarningBanner(positionsData);
  renderStalenessWarning(report);
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
  if (!el) return;
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

// 指数カードのクリック時に詳細モーダルへ渡すためのkey→entryマップ(描画毎に再構築)。
let MARKET_ENTRIES = {};

function renderMarketOverview(indices) {
  const section = document.getElementById("market-overview");
  const cards = document.getElementById("market-cards");
  if (!section || !cards) return;
  if (!indices || !indices.indices || !indices.indices.length) return; // stays hidden

  const staleKeys = new Set(indices.stale_keys || []);
  // クリック時にモーダルへ渡すため、key→entry を保持しておく。
  MARKET_ENTRIES = {};
  cards.innerHTML = indices.indices
    .map((entry) => {
      MARKET_ENTRIES[entry.key] = entry;
      const isUp = (entry.change ?? 0) >= 0;
      const stale = staleKeys.has(entry.key);
      return `
        <div class="market-card${stale ? " is-stale" : ""}" role="button" tabindex="0" data-market-key="${escapeHtml(entry.key)}">
          <div class="market-card-name">${entry.name}${stale ? '<span class="stale-badge" title="最新データの取得に失敗（キャッシュ表示）">stale</span>' : ""}</div>
          <div class="market-card-value">${formatIndexValue(entry)}</div>
          <div class="market-card-change ${isUp ? "chg-up" : "chg-down"}">${formatIndexChange(entry)}</div>
          ${sparklineSvg(entry.series, isUp)}
          <div class="market-card-date">${shortDate(entry.last_date)}</div>
        </div>`;
    })
    .join("");
  section.hidden = false;

  // カードクリック/Enterで指数の詳細モーダルを開く(イベント委譲で一度だけ登録)。
  if (!cards.dataset.wired) {
    cards.dataset.wired = "1";
    cards.addEventListener("click", (e) => {
      const card = e.target.closest(".market-card[data-market-key]");
      if (card) openMarketModal(card.dataset.marketKey);
    });
    cards.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const card = e.target.closest(".market-card[data-market-key]");
      if (card) { e.preventDefault(); openMarketModal(card.dataset.marketKey); }
    });
  }

  const meta = document.getElementById("market-live-meta");
  if (meta && indices.generated_at) {
    const when = new Date(indices.generated_at).toLocaleTimeString("ja-JP");
    meta.textContent = `指数データ取得: ${when} 時点`;
  }
}

// series(={t,v}[])から n営業日前比の騰落を算出。unit=="%"(金利)はpt差、それ以外は%。
function periodChange(entry, days) {
  const s = entry.series || [];
  if (s.length < days + 1) return null;
  const last = s[s.length - 1].v;
  const past = s[s.length - 1 - days].v;
  if (last == null || past == null) return null;
  if (entry.unit === "%") return { txt: (last - past >= 0 ? "+" : "") + (last - past).toFixed(3) + "pt", up: last - past >= 0 };
  const pct = past !== 0 ? (last / past - 1) * 100 : 0;
  return { txt: (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%", up: pct >= 0 };
}

function closeMarketModal() {
  const el = document.getElementById("market-modal");
  if (el) el.remove();
}

// 指数カードのクリックで詳細(現在値/前日比/大きめスパークライン/期間別騰落/レンジ)を表示。
function openMarketModal(key) {
  const entry = MARKET_ENTRIES[key];
  if (!entry) return;
  closeMarketModal();

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "market-modal";
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeMarketModal();
  });

  const isUp = (entry.change ?? 0) >= 0;
  const periods = [
    { d: 1, label: "1日" },
    { d: 5, label: "1週" },
    { d: 20, label: "1ヶ月" },
    { d: 60, label: "3ヶ月" },
  ];
  const chips = periods
    .map((p) => {
      const c = periodChange(entry, p.d);
      const val = c ? c.txt : "-";
      const cls = c ? (c.up ? "chg-up" : "chg-down") : "";
      return `<span class="chip"><span class="chip-label">${p.label}</span><span class="chip-value ${cls}">${val}</span></span>`;
    })
    .join("");

  const vals = (entry.series || []).map((p) => p.v).filter((v) => v != null);
  let rangeHtml = "";
  if (vals.length) {
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const fmt = (v) => (entry.unit === "%" ? v.toFixed(3) + "%" : v.toLocaleString("ja-JP", { maximumFractionDigits: 2 }));
    rangeHtml = `<h4>期間レンジ(直近${vals.length}営業日)</h4><p class="market-modal-range">安値 ${fmt(lo)} 〜 高値 ${fmt(hi)}</p>`;
  }

  // 大きめスパークライン(カードの120x32より大)。
  const bigSpark = marketBigSparkline(entry.series, isUp);

  overlay.innerHTML = `
    <div class="modal-box market-modal-box">
      <div class="hm-popup-head">
        <h3>${escapeHtml(entry.name)}</h3>
        <button type="button" class="secondary" id="market-modal-close">閉じる</button>
      </div>
      <div class="market-modal-value">${formatIndexValue(entry)}</div>
      <div class="market-card-change ${isUp ? "chg-up" : "chg-down"}">${formatIndexChange(entry)}</div>
      ${bigSpark}
      <h4>期間別騰落</h4>
      <div class="meta-chips">${chips}</div>
      ${rangeHtml}
      <p class="market-modal-date">最終データ: ${entry.last_date || "-"}</p>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("market-modal-close").addEventListener("click", closeMarketModal);
}

function marketBigSparkline(series, isUp) {
  const points = (series || []).map((p) => p.v).filter((v) => v != null);
  if (points.length < 2) return "";
  const w = 320;
  const h = 96;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = w / (points.length - 1);
  const coords = points
    .map((v, i) => `${(i * step).toFixed(1)},${(h - 4 - ((v - min) / span) * (h - 8)).toFixed(1)}`)
    .join(" ");
  const color = isUp ? "var(--accent)" : "var(--danger)";
  return `<svg class="market-modal-spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
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
      const indices = await window.MinerviniData.fetchJson("data/indices.json", { optional: true });
      if (indices) renderMarketOverview(indices);
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

// 地合いシグナル(市場ブレッドス + TOPIXトレンド合成の攻め/中立/守り3段階)。
// breadth.json の history最新エントリに signal/reasons/pct_above_ma200等が
// 載っている前提(src/report/market_signal.py -> update_breadth)。旧データで
// signalが無い場合はカード自体を隠す。
const MARKET_SIGNAL_META = {
  green: { label: "攻め", className: "signal-green" },
  yellow: { label: "中立", className: "signal-yellow" },
  red: { label: "守り", className: "signal-red" },
};

function renderMarketSignal(breadth) {
  const el = document.getElementById("market-signal-card");
  if (!el) return;
  const history = breadth && Array.isArray(breadth.history) ? breadth.history : [];
  const latest = history.length ? history[history.length - 1] : null;
  if (!latest || !latest.signal) {
    el.hidden = true;
    return;
  }

  const meta = MARKET_SIGNAL_META[latest.signal] || MARKET_SIGNAL_META.yellow;
  el.hidden = false;
  el.className = "market-signal-card " + meta.className;

  const pct200 = latest.pct_above_ma200 != null ? (latest.pct_above_ma200 * 100).toFixed(1) + "%" : "-";
  const newHigh = latest.new_high_count ?? "-";
  const newLow = latest.new_low_count ?? "-";
  const reasons = (latest.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
  const caution = latest.signal === "red"
    ? '<p class="market-signal-caution">⚠ 新規エントリーは控えるのが原則です。</p>'
    : "";

  el.innerHTML = `
    <div class="market-signal-label">${meta.label}</div>
    <ul class="market-signal-reasons">${reasons}</ul>
    <div class="market-signal-stats">MA200上回り率 ${pct200} / 新高値 ${newHigh}件 vs 新安値 ${newLow}件</div>
    ${caution}
  `;
}

// VCPファネル(P1銘柄の origin/status 内訳)。breadth.json history の各エントリの
// vcp_funnel(pipeline -> update_breadth)に依存。origin=ok は V判定に到達した銘柄
// (WATCH_A+WATCH_B+REJECTED)。TOO_RECENT は「リーダーが新高値近辺でベース未形成」で、
// 高値追い局面→調整入りでセットアップが増える先行指標として直近60日を折れ線表示する。
function renderVcpFunnel(breadth) {
  const el = document.getElementById("vcp-funnel-card");
  if (!el) return;
  const history = breadth && Array.isArray(breadth.history) ? breadth.history : [];
  const withFunnel = history.filter((h) => h && h.vcp_funnel);
  if (!withFunnel.length) {
    el.hidden = true;
    return;
  }
  const originOk = (f) =>
    (f.WATCH_A || 0) + (f.WATCH_B || 0) + (f.REJECTED || 0);
  const latest = withFunnel[withFunnel.length - 1].vcp_funnel;

  // TOO_RECENT の直近60日推移。減少(高値追い→調整入り)を accent、増加を danger 表示。
  const series = withFunnel.slice(-60).map((h) => ({ v: h.vcp_funnel.TOO_RECENT || 0 }));
  const declining = series.length >= 2 && series[series.length - 1].v <= series[0].v;
  const spark = sparklineSvg(series, declining);

  el.hidden = false;
  el.innerHTML = `
    <div class="vcp-funnel-title">VCPファネル(P1銘柄の内訳)</div>
    <div class="vcp-funnel-stats">
      <span>ベース到達(origin_ok): ${originOk(latest)}件</span>
      <span>高値更新中(TOO_RECENT): ${latest.TOO_RECENT || 0}件</span>
      <span>形成中(IMMATURE): ${latest.IMMATURE || 0}件</span>
      <span>ボラ過大(TOO_VOLATILE): ${latest.TOO_VOLATILE || 0}件</span>
    </div>
    <div class="vcp-funnel-spark">
      <span class="vcp-funnel-spark-label">TOO_RECENT 直近60日(減少=セットアップ増の先行シグナル)</span>
      ${spark}
    </div>
  `;
}

// データ鮮度チェック: 直近の平日(月〜金、土日はFriday扱い)の21:00 JSTを過ぎても
// その平日の日付のデータが無い場合に stale=true を返す。祝日は考慮しない
// (祝日明けの誤検知は許容 -- バナー文言で注記)。
const JST_OFFSET_MS = 9 * 60 * 60 * 1000;

function getStalenessInfo(generatedAt, now) {
  now = now || new Date();
  const nowJstMs = now.getTime() + JST_OFFSET_MS;
  const nowJst = new Date(nowJstMs);
  const day = nowJst.getUTCDay(); // 0=Sun .. 6=Sat (JST calendar day, via shifted-clock trick)
  const todayMidnightJstMs = Date.UTC(nowJst.getUTCFullYear(), nowJst.getUTCMonth(), nowJst.getUTCDate());

  let expectedMidnightJstMs = todayMidnightJstMs;
  if (day === 0) expectedMidnightJstMs -= 2 * 86400000; // Sunday -> Friday
  else if (day === 6) expectedMidnightJstMs -= 1 * 86400000; // Saturday -> Friday

  const thresholdMs = expectedMidnightJstMs + 21 * 3600000; // expected date 21:00 JST
  if (nowJstMs <= thresholdMs) return { stale: false };

  if (!generatedAt) return { stale: true };
  const genJstMs = new Date(generatedAt).getTime() + JST_OFFSET_MS;
  const genJst = new Date(genJstMs);
  const genMidnightJstMs = Date.UTC(genJst.getUTCFullYear(), genJst.getUTCMonth(), genJst.getUTCDate());

  return { stale: genMidnightJstMs < expectedMidnightJstMs };
}

function renderStalenessWarning(report) {
  const el = document.getElementById("staleness-warning");
  if (!el) return;
  const info = getStalenessInfo(report.generated_at);
  if (info.stale) {
    const when = report.generated_at
      ? new Date(report.generated_at).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "-";
    el.textContent = `⚠ データが最新ではありません(最終更新: ${when})。日次バッチが失敗している可能性があります。バッチ実行ページから daily.yml の実行履歴を確認してください。(祝日明けは誤検知の場合あり)`;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
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
  section.appendChild(renderCardList(stocks, tier));
  return section;
}

// 終値の前日比%(report.jsonのchange_pct)。旧report.json(フィールドなし)は空表示。
// 表記は「終値（±X.XX%）」で、%部分だけ色付けする。
function changePctHtml(s) {
  const v = s.change_pct;
  if (v == null) return "";
  const cls = v > 0 ? "chg-pos" : v < 0 ? "chg-neg" : "chg-flat";
  const sign = v > 0 ? "+" : "";
  return `<span class="sc-chg-wrap">（<span class="sc-chg ${cls}">${sign}${Number(v).toFixed(2)}%</span>）</span>`;
}

// リスト画面: 横スクロール表を廃止し、1銘柄=薄型2段カードでスマホ幅に収める。
// 表示項目はコード/銘柄名/総合スコア/RS/終値(前日比%)/セクター(強度)/枯れ度のみ。
// 並びは総合スコア降順(監視タブはRS降順)固定。列ヘッダソートは表とともに廃止。
function renderCardList(stocks, tier, options = {}) {
  const sortVal = CARD_SORTS[options.initialSortKey || "total_score"] || CARD_SORTS.total_score;
  const sorted = [...stocks].sort((a, b) => {
    const av = sortVal(a);
    const bv = sortVal(b);
    return av === bv ? 0 : av > bv ? -1 : 1;
  });

  const list = document.createElement("div");
  list.className = "card-list";
  for (const s of sorted) {
    const card = document.createElement("div");
    card.className = "stock-card";
    if (s.fund_stale) card.classList.add("fund-stale");
    // has_chart===false の銘柄(チャートJSON未生成)は詳細ページへ遷移できない。
    if (s.has_chart !== false) {
      card.addEventListener("click", () => {
        window.location.hash = `stock/${encodeURIComponent(s.code)}`;
      });
    } else {
      card.classList.add("row-static");
    }
    // 上段: 「銘柄名（コード）」+ SC/RS
    // 下段: 終値（±前日比%）+ セクター(強度) + 枯れ度
    card.innerHTML = `
      <div class="sc-row">
        <span class="sc-name">${escapeHtml(s.name ?? "-")}（${escapeHtml(s.code)}）</span>
        <span class="sc-metrics">SC <b>${s.total_score ?? "-"}</b>・RS <b>${s.rs ?? "-"}</b></span>
      </div>
      <div class="sc-row sc-row-sub">
        <span class="sc-close">${formatClose(s.close)}${changePctHtml(s)}</span>
        <span class="sc-sector">${sectorStrengthHtml(s)}</span>
        <span class="sc-dryup">${dryupBadgeHtml(s)}</span>
      </div>`;
    list.appendChild(card);
  }
  return list;
}

function formatClose(v) {
  return v == null ? "-" : Number(v).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
}

// ---------------------------------------------------------------------------
// 〔監視〕8条件合格・セットアップ形成待ち(旧P1)一覧。P2〜P4はUI廃止(データは
// report.jsonに残るがダッシュボードには出さない)。全件をRS降順で表示する。
// カードは本命/候補プールと共通のrenderCardListを使い、初期ソートをRS降順に上書きする。
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
  container.appendChild(renderCardList(stocks, "watchlist", { initialSortKey: "rs" }));
}

// ---------------------------------------------------------------------------
// Stock detail page (stock.html)
// ---------------------------------------------------------------------------

// SPAルーターから#stock/CODEへ遷移するたびに呼ばれる。codeOverrideがあれば
// それを使い(SPAルート経由)、なければ旧来の?code=クエリを見る(stock.html直リンク互換)。
async function initStockPage(codeOverride) {
  const code = codeOverride || new URLSearchParams(window.location.search).get("code");

  // 前の銘柄のチャート・イベントリスナーを必ず片付けてから描画し直す
  // (teardownしないと銘柄を切り替えるたびにチャート/リスナーが積み上がる)。
  teardownCharts();

  const titleEl = document.getElementById("stock-title");
  const metaEl = document.getElementById("stock-meta");
  const mustEl = document.getElementById("must-checklist");
  const scoreEl = document.getElementById("score-breakdown");
  const fundEl = document.getElementById("fund-detail-body");
  const copyBtn = document.getElementById("copy-stock-data-btn");
  const sizingResultEl = document.getElementById("sizing-result");
  if (metaEl) metaEl.innerHTML = "";
  if (mustEl) mustEl.innerHTML = "";
  if (scoreEl) scoreEl.innerHTML = "";
  if (fundEl) fundEl.innerHTML = "";
  if (sizingResultEl) sizingResultEl.innerHTML = "";
  if (copyBtn) copyBtn.hidden = true;

  if (!code) {
    if (titleEl) titleEl.textContent = "銘柄コードが指定されていません";
    return;
  }
  if (titleEl) titleEl.textContent = "読み込み中...";

  const [report, chart] = await Promise.all([
    window.MinerviniData.fetchJson("data/report.json"),
    window.MinerviniData.fetchJson(`data/charts/${encodeURIComponent(code)}.json`, { optional: true }),
  ]);
  const stock = report.stocks.find((s) => s.code === code);

  if (titleEl) titleEl.textContent = `${code} ${stock ? stock.name : ""}`;
  if (stock) renderStockMeta(stock);
  setupYahooFinanceLink(code);
  setupStockPanels();
  renderStockSummary(stock);
  if (stock) renderStockFundamentals(code, stock.name, report.generated_at);
  setupSizingCalculator(stock);
  setupStockCopyButton(stock, chart, report);

  if (!chart) {
    const chartContainer = document.getElementById("chart-container");
    if (chartContainer) chartContainer.textContent = "チャートデータがありません";
    return;
  }
  renderCharts(chart);

  if (stock) {
    renderMustChecklist(stock.must_flags, stock.vcp_detail);
    renderScoreBreakdown(stock);
  }
}

// 個別銘柄のYahoo!ファイナンス(日本)リンク。東証銘柄はコード+".T"。
function setupYahooFinanceLink(code) {
  const link = document.getElementById("yahoo-finance-link");
  if (!link) return;
  const digits = String(code || "").trim();
  if (!digits) {
    link.hidden = true;
    return;
  }
  link.href = `https://finance.yahoo.co.jp/quote/${encodeURIComponent(digits)}.T`;
  link.hidden = false;
}

// 個別画面のパネル高さはCSSのapp shell(flex)が決める(JSでの実測上書きは廃止)。
// bodyがスクロールしない前提で .stock-panels{flex:1;min-height:0} が残り高さを埋める。

// アクティブなタブ表示を切り替える。
function updateStockActiveTab(panelName) {
  const tabs = document.getElementById("stock-tabs");
  if (!tabs) return;
  tabs.querySelectorAll(".stock-tab").forEach((b) => {
    b.classList.toggle("active", b.dataset.panel === panelName);
  });
}

// 個別画面の横スワイプ/タブUI。パネルを横スクロールスナップで並べ、
// タブクリック↔スクロール位置を双方向同期。銘柄遷移のたびに先頭へリセット。
function setupStockPanels() {
  const panels = document.getElementById("stock-panels");
  const tabs = document.getElementById("stock-tabs");
  if (!panels || !tabs) return;

  panels.scrollLeft = 0; // 常にサマリーから開始
  updateStockActiveTab("summary");

  if (panels.dataset.wired) return;
  panels.dataset.wired = "1";

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".stock-tab");
    if (!btn) return;
    const items = Array.from(tabs.querySelectorAll(".stock-tab"));
    const idx = items.indexOf(btn);
    if (idx < 0) return;
    panels.scrollTo({ left: idx * panels.clientWidth, behavior: "smooth" });
    updateStockActiveTab(btn.dataset.panel);
  });

  let raf = 0;
  panels.addEventListener(
    "scroll",
    () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const idx = Math.round(panels.scrollLeft / Math.max(1, panels.clientWidth));
        const cur = panels.querySelectorAll(".stock-panel")[idx];
        if (cur) updateStockActiveTab(cur.dataset.panel);
      });
    },
    { passive: true }
  );
}

// リスト画面(本命/候補/監視)はタブでのみ切替。横スワイプでの画面切替は廃止し、
// 各パネル内の横スクロールは表の列閲覧専用にした(スワイプでパネルが動かない)。
function initListView() {
  const panels = document.getElementById("list-panels");
  const tabs = document.getElementById("list-tabs");
  if (!panels || !tabs) return;

  const setActive = (name) => {
    tabs.querySelectorAll(".list-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.panel === name);
    });
    panels.querySelectorAll(".list-panel").forEach((p) => {
      const on = p.dataset.panel === name;
      p.classList.toggle("active", on);
      if (on) p.scrollTop = 0; // 切替のたびに先頭へ
    });
  };

  // 初期表示は本命(既にactiveなタブがあればそれを尊重)。
  const initial = (tabs.querySelector(".list-tab.active") || tabs.querySelector(".list-tab"));
  setActive(initial ? initial.dataset.panel : "confirmed");

  if (panels.dataset.wired) return;
  panels.dataset.wired = "1";

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".list-tab");
    if (!btn) return;
    setActive(btn.dataset.panel);
  });
}

// ルールベース日本語サマリー (src/report/summary.py が生成した
// {headline, points, cautions})。無ければセクションごと隠す
// (次回daily実行前の古いreport.jsonでも壊れないように)。
function renderStockSummary(stock) {
  const el = document.getElementById("stock-summary");
  if (!el) return;
  el.innerHTML = "";
  const s = stock && stock.summary;
  if (!s || !s.headline) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  const head = document.createElement("p");
  head.className = "summary-headline";
  head.textContent = s.headline;
  el.appendChild(head);

  // ポジ/ネガで色分け: cautions(注意点)は常にネガティブ(赤)。points は原則
  // ポジティブ(緑)だが、時価総額・市場・次回決算などの中立な事実は色なし。
  const lists = [
    { items: s.points, className: "summary-points", prefix: "", tone: pointTone },
    { items: s.cautions, className: "summary-cautions", prefix: "⚠ ", tone: () => "negative" },
  ];
  for (const { items, className, prefix, tone } of lists) {
    if (!Array.isArray(items) || !items.length) continue;
    const ul = document.createElement("ul");
    ul.className = className;
    for (const text of items) {
      const li = document.createElement("li");
      li.className = "summary-item summary-item-" + tone(text);
      li.textContent = prefix + text;
      ul.appendChild(li);
    }
    el.appendChild(ul);
  }
}

// point 文の中で「中立な事実」を判定する接頭辞(時価総額/市場区分/次回決算など)。
// これらは色を付けず、それ以外の point はポジティブ(緑)として扱う。
const NEUTRAL_POINT_PREFIXES = ["時価総額", "市場", "次回決算", "EPS YoY推移", "予想PER", "会社計画", "セクター"];

function pointTone(text) {
  const t = String(text || "");
  if (NEUTRAL_POINT_PREFIXES.some((p) => t.startsWith(p))) return "neutral";
  return "positive";
}

function renderStockMeta(stock) {
  const items = [
    ["終値", stock.close],
    ["ステータス", STATUS_LABELS[stock.status] || stock.status],
    ["総合スコア", stock.total_score],
    ["RS", stock.rs],
    ["ピボット", stock.pivot],
    ["推奨逆指値", stock.buy_stop],
    ["推奨損切り", stock.stop_loss],
    ["リスク%", stock.risk_pct],
    // 以下は値がある場合のみ表示 (時価総額/市場区分/次回決算は2026-07-12追加。
    // 市場区分は次回ユニバース再構築後、次回決算は3月期・9月期企業のみ入る)
    ["時価総額", stock.market_cap_oku != null ? `${Number(stock.market_cap_oku).toLocaleString("ja-JP")}億円` : null],
    ["市場", stock.market_segment ?? null],
    ["次回決算", stock.next_earnings_date ?? null],
  ];
  document.getElementById("stock-meta").innerHTML = items
    .filter(([, value], i) => i < 8 || value != null)
    .map(
      ([label, value]) =>
        `<span class="chip"><span class="chip-label">${label}</span><span class="chip-value">${value ?? "-"}</span></span>`
    )
    .join("");
}

// ---------------------------------------------------------------------------
// Stock detail page: fundamentals table (EPS/売上高 + 前年同期比)
// ---------------------------------------------------------------------------

// "2025Q1" -> "2024Q1" (前年同期のラベル)。Q4はFY(通期)の意味で使われている
// ため、そのまま1年前のQ4と比較する。
function shiftFiscalQuarterYoy(label) {
  const m = /^(\d{4})Q([1-4])$/.exec(label || "");
  if (!m) return null;
  return `${Number(m[1]) - 1}Q${m[2]}`;
}

function growthPct(cur, prev) {
  if (cur == null || prev == null || prev === 0) return null;
  return ((cur - prev) / Math.abs(prev)) * 100;
}

function formatYoy(pct) {
  if (pct == null || !Number.isFinite(pct)) return "-";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

function yoyClass(pct) {
  if (pct == null || !Number.isFinite(pct)) return "";
  return pct > 0 ? "yoy-positive" : pct < 0 ? "yoy-negative" : "";
}

function formatEps(v) {
  return v == null ? "-" : Number(v).toLocaleString("ja-JP", { maximumFractionDigits: 2 });
}

// 円単位の売上高を億円表示に丸める(百万円/円単位のままだと桁が多く読みにくいため)。
function formatRevenue(v) {
  if (v == null) return "-";
  return `${(Number(v) / 100000000).toLocaleString("ja-JP", { maximumFractionDigits: 1 })}億円`;
}

// ファンダの表/グラフ表示切替の設定を localStorage に保持(引数ありでset)。
const FUND_VIEW_KEY = "minervini-fund-view";
function fundViewPref(v) {
  if (v != null) {
    try { localStorage.setItem(FUND_VIEW_KEY, v); } catch (e) {}
    return v;
  }
  // 初期表示はグラフ。明示的に "table" を選んだ時だけ表にする。
  let stored = "chart";
  try { stored = localStorage.getItem(FUND_VIEW_KEY) || "chart"; } catch (e) {}
  return stored === "table" ? "table" : "chart";
}

// EPS・売上高の推移バーチャート(YoY色分け付き)をSVGで生成する。
// 直近最大8四半期を古い→新しい順(左→右)で描画。棒の色は前年同期比の
// 正負(緑/赤)、YoYが無い期は中立色。EPSと売上は単位が違うので別パネル。
function fundBarPanel(title, quarters, byQuarter, valueOf, fmt, yoyMetric) {
  const qs = quarters.slice(-8);
  if (!qs.length) return "";
  const vals = qs.map(valueOf).filter((v) => v != null);
  if (!vals.length) return "";
  const maxV = Math.max(...vals, 0);
  const minV = Math.min(...vals, 0);
  const range = maxV - minV || 1;

  const w = 320;
  const h = 150;
  const padTop = 22;
  const padBottom = 30;
  const plotH = h - padTop - padBottom;
  const zeroY = padTop + (maxV / range) * plotH; // 0 の位置
  const slot = w / qs.length;
  const barW = Math.min(28, slot * 0.6);

  const bars = qs
    .map((q, i) => {
      const v = valueOf(q);
      if (v == null) return "";
      const prev = byQuarter.get(shiftFiscalQuarterYoy(q.fiscal_quarter));
      const yoy = growthPct(yoyMetric(q), prev ? yoyMetric(prev) : null);
      const fill = yoy == null ? "var(--text-dim)" : yoy > 0 ? "var(--accent)" : yoy < 0 ? "var(--danger)" : "var(--text-dim)";
      const cx = slot * i + slot / 2;
      const vH = (Math.abs(v) / range) * plotH;
      const y = v >= 0 ? zeroY - vH : zeroY;
      const label = escapeHtml(q.fiscal_quarter.replace(/^\d{2}/, ""));
      const yoyTxt = yoy == null ? "" : `<text x="${cx.toFixed(1)}" y="${(y - 4).toFixed(1)}" class="fund-bar-yoy" text-anchor="middle" fill="${fill}">${formatYoy(yoy)}</text>`;
      return `
        <rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(1, vH).toFixed(1)}" rx="2" fill="${fill}" opacity="0.85"/>
        ${yoyTxt}
        <text x="${cx.toFixed(1)}" y="${(h - 14).toFixed(1)}" class="fund-bar-x" text-anchor="middle">${label}</text>`;
    })
    .join("");

  return `
    <div class="fund-chart-panel">
      <div class="fund-chart-title">${title}</div>
      <svg viewBox="0 0 ${w} ${h}" class="fund-bar-svg" preserveAspectRatio="xMidYMid meet" role="img">
        <line x1="0" y1="${zeroY.toFixed(1)}" x2="${w}" y2="${zeroY.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>
        ${bars}
      </svg>
    </div>`;
}

function fundChartHtml(quarters, byQuarter) {
  const eps = fundBarPanel("EPS 推移(棒=EPS / 色=前年同期比)", quarters, byQuarter, (q) => q.eps, formatEps, (q) => q.eps);
  const rev = fundBarPanel("売上高 推移(棒=売上高 / 色=前年同期比)", quarters, byQuarter, (q) => (q.revenue == null ? null : q.revenue / 1e8), formatRevenue, (q) => q.revenue);
  if (!eps && !rev) return '<p class="tier-note">グラフ化できるデータがありません。</p>';
  return `<div class="fund-chart-wrap">${eps}${rev}<p class="fund-chart-legend"><span class="lg-up">■</span> 前年同期比プラス　<span class="lg-down">■</span> マイナス　<span class="lg-neutral">■</span> 前年同期比なし</p></div>`;
}

async function renderStockFundamentals(code, name, reportGeneratedAt) {
  const container = document.getElementById("fund-detail-body");
  if (!container) return;
  container.innerHTML = "読み込み中...";

  const authEnabled = window.MINERVINI_CONFIG.passkeyAuthEnabled;
  const canEdit = !authEnabled || (window.MinerviniGitHub && window.MinerviniGitHub.hasToken());
  const pending = window.MinerviniFundamentalsUI
    ? window.MinerviniFundamentalsUI.reconcilePending(reportGeneratedAt)
    : {};
  const isPending = !!(window.MinerviniFundamentalsUI && window.MinerviniFundamentalsUI.isPending(pending, code));

  let entry = null;
  try {
    const all = await window.MinerviniData.fetchJson("data/fundamentals_public.json", { optional: true });
    entry = (all && all[code]) || null;
  } catch (e) {
    /* fetch failure: fall through to empty state below */
  }

  const btnHtml = `
    <button type="button" id="fund-detail-edit-btn" class="fund-edit-btn"${canEdit ? "" : ' disabled title="先に🔓解錠してください"'}>ファンダ入力/編集</button>
    ${isPending ? '<span class="pending-badge">入力済み・次回実行で本命に昇格予定</span>' : ""}
  `;

  const quarters = (entry && entry.quarters) || [];
  if (!quarters.length) {
    container.innerHTML = `${btnHtml}<p class="tier-note">ファンダデータがまだありません。「ファンダ入力/編集」から入力できます。</p>`;
  } else {
    const byQuarter = new Map(quarters.map((q) => [q.fiscal_quarter, q]));
    const rowsHtml = quarters
      .slice()
      .reverse() // 直近の四半期を先頭に表示
      .map((q) => {
        const prev = byQuarter.get(shiftFiscalQuarterYoy(q.fiscal_quarter));
        const epsYoy = growthPct(q.eps, prev ? prev.eps : null);
        const revYoy = growthPct(q.revenue, prev ? prev.revenue : null);
        return `
        <tr>
          <td>${escapeHtml(q.fiscal_quarter)}</td>
          <td>${formatEps(q.eps)}</td>
          <td class="${yoyClass(epsYoy)}">${formatYoy(epsYoy)}</td>
          <td>${formatRevenue(q.revenue)}</td>
          <td class="${yoyClass(revYoy)}">${formatYoy(revYoy)}</td>
        </tr>`;
      })
      .join("");

    const metaLine = entry.checked_date
      ? `<p class="tier-note">確認日: ${escapeHtml(entry.checked_date)}</p>`
      : "";

    // 表 / グラフ の切替(設定はlocalStorageに保持)。トグルは入力/編集ボタンと同じ行に置く。
    const view = fundViewPref();
    container.innerHTML = `
      <div class="fund-detail-head">
        ${btnHtml}
        <div class="fund-view-toggle segmented" role="tablist">
          <button type="button" class="${view === "chart" ? "active" : ""}" data-fund-view="chart">グラフ</button>
          <button type="button" class="${view === "table" ? "active" : ""}" data-fund-view="table">表</button>
        </div>
      </div>
      <div id="fund-view-chart" class="fund-view-panel"${view === "table" ? " hidden" : ""}>
        ${fundChartHtml(quarters, byQuarter)}
      </div>
      <div id="fund-view-table" class="fund-view-panel"${view === "chart" ? " hidden" : ""}>
        <div class="table-scroll">
          <table class="fund-table fund-detail-table">
            <thead>
              <tr><th>会計四半期</th><th>EPS</th><th>EPS前年同期比</th><th>売上高</th><th>売上高前年同期比</th></tr>
            </thead>
            <tbody>${rowsHtml}</tbody>
          </table>
        </div>
      </div>
      ${metaLine}
    `;

    const toggle = container.querySelector(".fund-view-toggle");
    if (toggle) {
      toggle.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-fund-view]");
        if (!btn) return;
        const v = btn.dataset.fundView;
        fundViewPref(v);
        toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        const tbl = container.querySelector("#fund-view-table");
        const cht = container.querySelector("#fund-view-chart");
        if (tbl) tbl.hidden = v !== "table";
        if (cht) cht.hidden = v !== "chart";
      });
    }
  }

  const editBtn = document.getElementById("fund-detail-edit-btn");
  if (editBtn && !editBtn.disabled) {
    editBtn.addEventListener("click", () => window.MinerviniFundamentalsUI.openFundamentalsModal(code, name));
  }
  if (window.MinerviniFundamentalsUI) {
    window.MinerviniFundamentalsUI.onSaved = () => renderStockFundamentals(code, name, reportGeneratedAt);
  }
}

// ---------------------------------------------------------------------------
// Stock detail page: 分析用データコピー
// 個別株の全コンテキスト(テクニカル/8条件/VCP/直近値動き/ファンダ/市況)を
// 自己完結のMarkdownテキストに整形してクリップボードへコピーする。
// Claude等のAIに貼り付けてミネルヴィニ手法での分析相談に使う想定
// (リポジトリの skills/minervini-analysis/SKILL.md がこの形式を読む前提)。
// ---------------------------------------------------------------------------

const TIER_COPY_LABELS = {
  confirmed: "本命 (ファンダ強度確認済み: EPS YoY+25%/売上YoY+20%以上)",
  pool: "候補プール (VCPセットアップあり・ファンダ未確認または基準未達)",
  watchlist: "候補 (トレンドテンプレート8条件合格・セットアップ形成待ち)",
};

function copyNum(v, digits = 2) {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return "-";
  return n.toLocaleString("ja-JP", { maximumFractionDigits: digits });
}

function copySignedPct(v, digits = 2) {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return "-";
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function copyFlagLines(flags, labels, detailFn) {
  return Object.entries(flags)
    .map(([name, value]) => `- ${value ? "✓" : "✗"} ${labels[name] || name}${detailFn ? detailFn(name) : ""}`)
    .join("\n");
}

// N本前の終値に対する騰落率 (直近値動きサマリー用)
function closeChangePct(candles, bars) {
  if (!candles || candles.length <= bars) return null;
  const cur = candles[candles.length - 1].close;
  const prev = candles[candles.length - 1 - bars].close;
  return growthPct(cur, prev);
}

function volumeAvg(volume, bars) {
  if (!volume || !volume.length) return null;
  const slice = volume.slice(-bars);
  if (!slice.length) return null;
  return slice.reduce((sum, v) => sum + (v.value || 0), 0) / slice.length;
}

function buildAnalysisMarkdown(stock, chart, report, fundEntry, breadthLast, indicesData) {
  const L = [];
  L.push(`# ${stock.code} ${stock.name} — ミネルヴィニ式スクリーナー 分析用データ`);
  L.push("");
  L.push(`- データ生成日時: ${report.generated_at ?? "-"}`);
  L.push(`- ティア: ${TIER_COPY_LABELS[stock.tier] || stock.tier || "-"}`);
  L.push(`- エントリーステータス: ${stock.status ?? "-"}${STATUS_LABELS[stock.status] ? ` = ${STATUS_LABELS[stock.status]}` : ""}`);
  L.push(`- セクター: ${stock.sector33 ?? "-"} (強度: ${stock.sector_strength ?? "-"} / 方向: ${stock.sector_direction ?? "-"})`);
  if (stock.market_cap_oku != null || stock.market_segment) {
    L.push(`- 時価総額: ${stock.market_cap_oku != null ? `約${Number(stock.market_cap_oku).toLocaleString("ja-JP")}億円` : "-"}${stock.market_segment ? ` / 市場区分: ${stock.market_segment}` : ""}`);
  }
  if (stock.next_earnings_date) L.push(`- 次回決算発表予定日: ${stock.next_earnings_date}`);
  L.push("");

  L.push("## 価格・テクニカル");
  L.push("");
  L.push("| 項目 | 値 |");
  L.push("|---|---|");
  L.push(`| 終値 | ${copyNum(stock.close, 1)} |`);
  L.push(`| RSレーティング (1-99) | ${stock.rs ?? "-"} |`);
  L.push(`| ピボット | ${copyNum(stock.pivot, 1)} |`);
  L.push(`| 推奨逆指値 | ${copyNum(stock.buy_stop, 1)} |`);
  L.push(`| 推奨損切り | ${copyNum(stock.stop_loss, 1)} |`);
  L.push(`| リスク% (逆指値→損切り) | ${copyNum(stock.risk_pct)}% |`);
  L.push(`| ピボットまでの距離 | ${copySignedPct(stock.dist_to_pivot)} |`);
  L.push(`| 52週高値からの距離 | ${copySignedPct(stock.high52w_distance_pct != null ? -stock.high52w_distance_pct : null)} |`);
  const dev = stock.ma_deviation_pct || {};
  L.push(`| MA50乖離 | ${copySignedPct(dev.ma50)} |`);
  L.push(`| MA150乖離 | ${copySignedPct(dev.ma150)} |`);
  L.push(`| MA200乖離 | ${copySignedPct(dev.ma200)} |`);
  L.push(`| EPS加速slope | ${copyNum(stock.eps_accel_slope)} |`);
  L.push("");

  L.push("## スコア");
  L.push("");
  L.push(`- テクニカルスコア: ${stock.tech_score ?? "-"}`);
  L.push(`- フルスコア: ${stock.full_score ?? "-"}`);
  L.push(`- VCPスコア: ${stock.vcp_score ?? "-"}`);
  L.push(`- 総合スコア: ${stock.total_score ?? "-"}`);
  L.push("");

  const mustFlags = stock.must_flags || {};
  L.push("## トレンドテンプレート8条件 (MUST)");
  L.push("");
  L.push(mustFlags.tt ? copyFlagLines(mustFlags.tt, MUST_FLAG_LABELS.tt) : "- データなし");
  L.push("");
  L.push("## VCP条件 (V1〜V7)");
  L.push("");
  const vcpDetail = stock.vcp_detail;
  L.push(
    mustFlags.vcp
      ? copyFlagLines(mustFlags.vcp, MUST_FLAG_LABELS.vcp, (name) => _mustFlagDetailSuffix("vcp", name, vcpDetail))
      : "- VCP評価なし (セットアップ未形成 or 評価対象外)"
  );
  if (vcpDetail && vcpDetail.shakeout_detected) L.push("- シェイクアウト検出: 直近安値のわずかな下抜け後、直前高値を更新済み");
  if (stock.footprint) {
    const depthLast = vcpDetail && vcpDetail.depth_last_pct;
    L.push(`- フットプリント: ${stock.footprint}${depthLast != null ? ` (最終${depthLast}%)` : ""}`);
  }
  L.push("");

  if (chart && Array.isArray(chart.candles) && chart.candles.length) {
    const candles = chart.candles;
    L.push("## 直近の値動き (直近20営業日)");
    L.push("");
    L.push("| 日付 | 始値 | 高値 | 安値 | 終値 | 出来高 |");
    L.push("|---|---|---|---|---|---|");
    const volByTime = new Map((chart.volume || []).map((v) => [v.time, v.value]));
    for (const c of candles.slice(-20)) {
      L.push(
        `| ${c.time} | ${copyNum(c.open, 1)} | ${copyNum(c.high, 1)} | ${copyNum(c.low, 1)} | ${copyNum(c.close, 1)} | ${copyNum(volByTime.get(c.time), 0)} |`
      );
    }
    L.push("");
    L.push(`- 騰落率: 5日 ${copySignedPct(closeChangePct(candles, 5), 1)} / 20日 ${copySignedPct(closeChangePct(candles, 20), 1)} / 60日 ${copySignedPct(closeChangePct(candles, 60), 1)}`);
    const vol10 = volumeAvg(chart.volume, 10);
    const vol50 = volumeAvg(chart.volume, 50);
    if (vol10 != null && vol50 != null && vol50 > 0) {
      L.push(`- 出来高: 直近10日平均 ${copyNum(vol10, 0)} / 50日平均 ${copyNum(vol50, 0)} (比率 ${(vol10 / vol50).toFixed(2)}${vol10 / vol50 <= 0.8 ? " → ドライアップ水準" : ""})`);
    }
    L.push("");
  }

  L.push("## ファンダメンタルズ (四半期・Q4はFY通期扱い)");
  L.push("");
  const quarters = (fundEntry && fundEntry.quarters) || [];
  if (quarters.length) {
    const byQuarter = new Map(quarters.map((q) => [q.fiscal_quarter, q]));
    L.push("| 会計四半期 | EPS | EPS前年同期比 | 売上高 | 売上高前年同期比 |");
    L.push("|---|---|---|---|---|");
    for (const q of quarters.slice().reverse()) {
      const prev = byQuarter.get(shiftFiscalQuarterYoy(q.fiscal_quarter));
      const epsYoy = growthPct(q.eps, prev ? prev.eps : null);
      const revYoy = growthPct(q.revenue, prev ? prev.revenue : null);
      L.push(`| ${q.fiscal_quarter} | ${formatEps(q.eps)} | ${formatYoy(epsYoy)} | ${formatRevenue(q.revenue)} | ${formatYoy(revYoy)} |`);
    }
    if (fundEntry.monthly_yoy != null) L.push(`- 月次YoY: ${fundEntry.monthly_yoy}`);
    if (fundEntry.checked_date) L.push(`- 確認日: ${fundEntry.checked_date}`);
  } else {
    L.push("- ファンダデータなし");
  }
  const gv = stock.guidance_view;
  if (gv) {
    const gvParts = [];
    if (gv.eps_plan != null) gvParts.push(`EPS ${copyNum(gv.eps_plan)}円${gv.eps_plan_yoy != null ? ` (前期比 ${copySignedPct(gv.eps_plan_yoy, 1)})` : ""}`);
    if (gv.sales_plan_yoy != null) gvParts.push(`売上前期比 ${copySignedPct(gv.sales_plan_yoy, 1)}`);
    if (gvParts.length) L.push(`- 会社計画(${gv.plan_fy}年度通期): ${gvParts.join(" / ")}`);
    if (gv.eps_progress_pct != null) L.push(`- 通期計画進捗率: EPS ${copyNum(gv.eps_progress_pct, 1)}% (Q${gv.quarters_reported}時点、目安${(gv.quarters_reported * 25).toFixed(0)}%)`);
    if (gv.forward_per != null) L.push(`- 予想PER: ${copyNum(gv.forward_per)}倍 (会社計画EPSベース)`);
  }
  L.push(`- カバレッジ: ${stock.fund_coverage ?? "-"}${stock.fund_stale ? " (古いデータ・再確認推奨)" : ""}`);
  if (stock.fund_strong != null) {
    const yoyParts = [];
    if (stock.fund_eps_yoy != null) yoyParts.push(`直近EPS YoY ${stock.fund_eps_yoy > 0 ? "+" : ""}${stock.fund_eps_yoy}%`);
    if (stock.fund_rev_yoy != null) yoyParts.push(`売上YoY ${stock.fund_rev_yoy > 0 ? "+" : ""}${stock.fund_rev_yoy}%`);
    L.push(`- ファンダ強度判定: ${stock.fund_strong ? "合格" : "不合格"} (基準: EPS YoY≥+25%かつ売上YoY≥+20%${yoyParts.length ? ` / 実績: ${yoyParts.join("、")}` : ""})`);
  }
  L.push("");

  L.push("## 市況コンテキスト");
  L.push("");
  const indices = (indicesData && indicesData.indices) || [];
  if (indices.length) {
    L.push("| 指数 | 終値 | 前日比 |");
    L.push("|---|---|---|");
    for (const ix of indices) {
      L.push(`| ${ix.name} | ${copyNum(ix.last)} | ${copySignedPct(ix.change_pct)} |`);
    }
    L.push("");
  }
  if (breadthLast) {
    L.push(`- ユニバース: ${breadthLast.universe_size ?? "-"}銘柄中、トレンドテンプレート8条件合格 ${breadthLast.template_pass ?? "-"}件 (合格率 ${breadthLast.template_pass_rate != null ? (breadthLast.template_pass_rate * 100).toFixed(1) + "%" : "-"})`);
    L.push(`- ブレイクアウト成功率(直近): ${breadthLast.breakout_success_rate != null ? (breadthLast.breakout_success_rate * 100).toFixed(0) + "%" : "-"}`);
  }
  if (report.p1_scarce) {
    L.push("- 警告: 8条件完全一致の候補銘柄が極端に少なく、地合いが弱い可能性あり");
  }
  L.push("");
  L.push("---");
  L.push("上記は日本株ミネルヴィニ式(SEPA)スクリーナーの出力データです。SEPA手法(トレンドテンプレート/ステージ分析/VCP/エントリー・リスク管理基準)に基づいて分析してください。");
  return L.join("\n");
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // 非HTTPS/旧ブラウザ向けフォールバック
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

// ---------------------------------------------------------------------------
// ポジションサイジング計算機 (view-stock, 2026-07-11追加)。純フロント機能
// (バックエンド変更なし)。総資金/リスク%はlocalStorageに保存し銘柄切替をまたいで復元する。
// ---------------------------------------------------------------------------

const SIZING_SETTINGS_KEY = "minervini_sizing_settings";

let sizingStock = null;
let sizingWired = false;

function loadSizingSettings() {
  try {
    const raw = localStorage.getItem(SIZING_SETTINGS_KEY);
    if (!raw) return { capital: null, riskPct: 1.0 };
    const parsed = JSON.parse(raw);
    return {
      capital: typeof parsed.capital === "number" ? parsed.capital : null,
      riskPct: typeof parsed.riskPct === "number" ? parsed.riskPct : 1.0,
    };
  } catch (e) {
    return { capital: null, riskPct: 1.0 };
  }
}

function saveSizingSettings(settings) {
  try {
    localStorage.setItem(SIZING_SETTINGS_KEY, JSON.stringify(settings));
  } catch (e) {
    // localStorageが使えない環境(プライベートモード等)は永続化を諦めるだけで致命的ではない
  }
}

function renderSizingResult(stock) {
  const el = document.getElementById("sizing-result");
  if (!el) return;

  if (!stock || stock.buy_stop == null || stock.stop_loss == null) {
    el.innerHTML = '<p class="tier-note">セットアップ未確定のため計算不可</p>';
    return;
  }

  const capitalInput = document.getElementById("sizing-capital");
  const capital = Number(capitalInput ? capitalInput.value : NaN);
  const activeBtn = document.querySelector("#sizing-risk-toggle button.active");
  const riskPct = activeBtn ? Number(activeBtn.dataset.risk) : 1.0;

  if (!capital || capital <= 0) {
    el.innerHTML = '<p class="tier-note">総資金を入力してください</p>';
    return;
  }

  const riskPerShare = stock.buy_stop - stock.stop_loss;
  if (!(riskPerShare > 0)) {
    el.innerHTML = '<p class="tier-note">逆指値/損切りのデータ不整合のため計算できません</p>';
    return;
  }

  const allowedLoss = capital * (riskPct / 100);
  const theoreticalShares = allowedLoss / riskPerShare;
  const orderShares = Math.floor(theoreticalShares / 100) * 100;

  if (orderShares < 100) {
    const oneUnitLoss = riskPerShare * 100;
    const oneUnitPct = (oneUnitLoss / capital) * 100;
    el.innerHTML = `<p class="sizing-warn">リスク許容内で1単元買えません(1単元の損失 = ${Math.round(oneUnitLoss).toLocaleString("ja-JP")}円 = 資金の${oneUnitPct.toFixed(2)}%)</p>`;
    return;
  }

  const investedAmount = orderShares * stock.buy_stop;
  const capitalRatio = (investedAmount / capital) * 100;
  const actualLoss = orderShares * riskPerShare;

  let concentrationWarning = "";
  if (capitalRatio > 50) {
    concentrationWarning = '<p class="sizing-warn sizing-warn-strong">⚠ 1銘柄への集中が50%を超えます</p>';
  } else if (capitalRatio > 25) {
    concentrationWarning = '<p class="sizing-warn">⚠ 1銘柄への集中が25%を超えます</p>';
  }

  el.innerHTML = `
    <div class="sizing-output">
      <div><span>発注株数</span><strong>${orderShares.toLocaleString("ja-JP")}株</strong></div>
      <div><span>投入額</span><strong>${Math.round(investedAmount).toLocaleString("ja-JP")}円</strong></div>
      <div><span>資金比</span><strong>${capitalRatio.toFixed(1)}%</strong></div>
      <div><span>実損失額</span><strong>${Math.round(actualLoss).toLocaleString("ja-JP")}円</strong></div>
    </div>
    ${concentrationWarning}
  `;
}

function setupSizingCalculator(stock) {
  sizingStock = stock || null;
  const capitalInput = document.getElementById("sizing-capital");
  const riskToggle = document.getElementById("sizing-risk-toggle");
  if (!capitalInput || !riskToggle) return;

  const settings = loadSizingSettings();
  if (settings.capital != null) capitalInput.value = settings.capital;
  riskToggle.querySelectorAll("button").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.risk) === settings.riskPct);
  });

  if (!sizingWired) {
    sizingWired = true;
    capitalInput.addEventListener("input", () => {
      saveSizingSettings({
        capital: Number(capitalInput.value) || null,
        riskPct: Number(document.querySelector("#sizing-risk-toggle button.active")?.dataset.risk) || 1.0,
      });
      renderSizingResult(sizingStock);
    });
    riskToggle.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-risk]");
      if (!btn) return;
      riskToggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      saveSizingSettings({ capital: Number(capitalInput.value) || null, riskPct: Number(btn.dataset.risk) });
      renderSizingResult(sizingStock);
    });
  }

  renderSizingResult(sizingStock);
}

function setupStockCopyButton(stock, chart, report) {
  const btn = document.getElementById("copy-stock-data-btn");
  if (!btn) return;
  if (!stock) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  btn.disabled = false;
  // onclick上書き方式: SPAで銘柄を切り替えてもリスナーが積み上がらない
  btn.onclick = async () => {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "生成中...";
    try {
      const [fundEntry, breadthLast, indicesData] = await Promise.all([
        window.MinerviniData.fetchJson("data/fundamentals_public.json", { optional: true })
          .then((all) => (all && all[stock.code]) || null)
          .catch(() => null),
        window.MinerviniData.fetchJson("data/breadth.json", { optional: true })
          .then((b) => (b && b.history && b.history.length ? b.history[b.history.length - 1] : null))
          .catch(() => null),
        window.MinerviniData.fetchJson("data/indices.json", { optional: true }).catch(() => null),
      ]);
      const text = buildAnalysisMarkdown(stock, chart, report, fundEntry, breadthLast, indicesData);
      await copyTextToClipboard(text);
      btn.textContent = "コピーしました";
    } catch (e) {
      btn.textContent = "コピー失敗";
    }
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 1800);
  };
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

// クロスヘア/凡例の時刻ラベルは実日付(MM/DD)のまま。
// 軸ティックだけを「当日 / -N営業日」の相対表示にするため、tickMarkFormatterは
// renderCharts側で銘柄毎に生成し、makeChartに渡す(モジュール定数からは外す)。
const CHART_LOCALIZATION = { timeFormatter: formatChartDate };
const CHART_TIME_SCALE = {};
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

function makeChart(el, { showTimeAxis, tickMarkFormatter }) {
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
    // tickMarkFormatter: 「当日/-N営業日」の相対表示 (renderCharts側で生成)。
    timeScale: {
      ...CHART_TIME_SCALE,
      tickMarkFormatter: tickMarkFormatter || formatChartDate,
      visible: showTimeAxis,
      borderColor: CHART_COLORS.grid,
      fixRightEdge: true,
      rightOffset: 0,
    },
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

// SPA化前は銘柄詳細ページを開くたびにフルリロードされていたため
// renderCharts()の中身(チャートインスタンス・resizeリスナー・チェックボックス/
// 期間トグルのイベントリスナー)は使い捨てで問題なかった。SPA化後は同じDOMを
// 再利用して別銘柄を描画し直すため、前回分を確実に破棄してから作り直す。
let stockChartState = null;

function teardownCharts() {
  if (!stockChartState) return;
  const { charts, resizeHandler, dateLabels } = stockChartState;
  window.removeEventListener("resize", resizeHandler);
  for (const c of charts) c.remove();
  for (const { label } of dateLabels) label.remove();
  stockChartState = null;
}

function renderCharts(chart) {
  const monthly = aggregateMonthly(chart);
  const dailyVolume = colorizeVolume(chart);

  const priceEl = document.getElementById("chart-container");
  const volEl = document.getElementById("volume-container");
  const rsEl = document.getElementById("rs-container");
  const hasRs = !!(chart.rs_line && chart.rs_line.length);
  // 銘柄を切り替えた時にRS無し→ありへ戻せるよう、DOMからremove()するのではなく
  // hidden切り替えにする(remove()すると次にRSありの銘柄を見てもrs-cardが復活しない)。
  const rsCard = document.getElementById("rs-card");
  if (rsCard) rsCard.hidden = !hasRs;

  // 相対表示用の time→"当日/-N営業日" 変換マップ。
  // 日足: chart.candles の末尾を「当日」(N=0)、そこから 1本ずつ遡って -N営業日 とする。
  // 月足: monthly.candles の末尾を「当月」、遡って -Nヶ月 とする。
  const dailyBackByTime = new Map();
  for (let i = 0; i < chart.candles.length; i++) {
    dailyBackByTime.set(chart.candles[i].time, chart.candles.length - 1 - i);
  }
  const monthlyBackByTime = new Map();
  for (let i = 0; i < monthly.candles.length; i++) {
    monthlyBackByTime.set(monthly.candles[i].time, monthly.candles.length - 1 - i);
  }
  // 現在の足種(D=日足, M=月足)。crosshair/凡例と共有するので renderCharts の
  // ローカル状態。ここで先に宣言しておかないと、下の relativeTickFormatter が
  // 初回createChart時に呼ばれた場合に TDZ で落ちる。
  let currentTf = "D";

  // 軸ティック用フォーマッタ(モジュール定数だと currentTf を参照できないので
  // ここでクロージャとして作る)。crosshair や凡例の日付は formatChartDate のまま。
  function relativeTickFormatter(time) {
    const key = timeKey(time);
    const isMonthly = currentTf === "M";
    const back = isMonthly ? monthlyBackByTime.get(key) : dailyBackByTime.get(key);
    if (back == null) return formatChartDate(time); // 想定外はフォールバック
    if (back === 0) return isMonthly ? "当月" : "当日";
    return isMonthly ? `-${back}ヶ月` : `-${back}営業日`;
  }

  // 日付軸は最下段のペイン(RSがあればRS、無ければ出来高)だけに表示する。
  // 3ペイン全部に出すと同じ日付が重複するため。パン/ズームはtimeScale.visible
  // と独立してsyncTimeScales()で同期されるので、表示を1つに絞っても連動は保たれる。
  const priceChart = makeChart(priceEl, { showTimeAxis: false, tickMarkFormatter: relativeTickFormatter });
  const volChart = makeChart(volEl, { showTimeAxis: !hasRs, tickMarkFormatter: relativeTickFormatter });
  const rsChart = hasRs ? makeChart(rsEl, { showTimeAxis: true, tickMarkFormatter: relativeTickFormatter }) : null;

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
    const boxOld = document.getElementById(id);
    if (!boxOld) return;
    // SPA再描画のたびに呼ばれるので、cloneNodeで前回分のchangeリスナーごと
    // 使い捨てる(そのままだと銘柄を切り替えるたびにリスナーが積み上がる)。
    const box = boxOld.cloneNode(true);
    boxOld.replaceWith(box);
    box.disabled = false;
    box.closest("label")?.classList.remove("disabled");
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
  // RSラインの時刻→値 (クロスヘアを3チャートに同期する際、RSペインの横線を
  // その日のRS値に合わせるため)。
  const rsLookup = {
    D: new Map((chart.rs_line || []).map((p) => [p.time, p.value])),
    M: new Map((monthly.rs_line || []).map((p) => [p.time, p.value])),
  };
  // currentTf は上方(相対軸フォーマッタ定義の直前)に前倒し宣言済み。

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

  // チャート→そのペインの主系列。クロスヘアを他ペインへ複製する際に使う。
  const chartSeries = new Map([[priceChart, candleSeries], [volChart, volSeries]]);
  if (rsChart && rsSeries) chartSeries.set(rsChart, rsSeries);

  // 他ペインの横線を「その日の各ペインの値」に合わせる(株価=終値/出来高=出来高/RS=RS値)。
  function paneValue(targetChart, key, bar) {
    if (targetChart === priceChart) return bar ? bar.close : null;
    if (targetChart === volChart) return bar ? bar.volume : null;
    return rsLookup[currentTf].get(key);
  }

  // ホバー中の日をどのペインでもクロスヘアで指せるように、他の2ペインへ複製する。
  let syncingCross = false;
  for (const c of charts) {
    c.subscribeCrosshairMove((param) => {
      const key = param.time != null ? timeKey(param.time) : null;
      const bar = key ? barLookup[currentTf].get(key) : null;
      updateLegend(bar || latestBar()); // fall back to the latest bar when not hovering
      if (syncingCross) return; // setCrosshairPositionの再入を防ぐ
      syncingCross = true;
      for (const other of charts) {
        if (other === c) continue;
        if (param.time == null) {
          other.clearCrosshairPosition();
        } else {
          const series = chartSeries.get(other);
          const val = paneValue(other, key, bar);
          if (series && val != null) other.setCrosshairPosition(val, param.time, series);
          else other.clearCrosshairPosition();
        }
      }
      syncingCross = false;
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

  // 最新日付は日付軸を表示している最下段ペインにのみ表示する
  // (オートticksは最新日を保証しないため自前で描画。表示は年なしの「月/日」形式)。
  const lastDate = chart.candles.length ? chart.candles[chart.candles.length - 1].time : null;
  let dateLabels = [];
  if (lastDate) {
    const [, m, d] = lastDate.split("-");
    const shortDate = `${parseInt(m, 10)}/${parseInt(d, 10)}`;
    const bottomEl = rsChart ? rsEl : volEl;
    dateLabels = [{ el: bottomEl, label: addLatestDateLabel(bottomEl, shortDate) }];
  }

  const toggleOld = document.getElementById("timeframe-toggle");
  if (toggleOld) {
    // 同上の理由でcloneして前回分のclickリスナーを捨てる。あわせて
    // 表示状態(1ヶ月ボタンがactive)をデフォルトにリセットしておく。
    const toggle = toggleOld.cloneNode(true);
    toggleOld.replaceWith(toggle);
    toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tf === String(DEFAULT_DAILY_BARS)));
    toggle.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tf]");
      if (!btn) return;
      toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      setTimeframe(btn.dataset.tf);
    });
  }

  const resizeHandler = () => {
    for (const [c, el] of [[priceChart, priceEl], [volChart, volEl], ...(rsChart ? [[rsChart, rsEl]] : [])]) {
      c.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    }
    for (const { el, label } of dateLabels) alignLatestDateLabel(el, label);
  };
  window.addEventListener("resize", resizeHandler);

  stockChartState = { charts, resizeHandler, dateLabels };
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
    V4: "最後の収縮が一定以内",
    V5: "出来高ドライアップ",
    V6: "ベース期間が15〜200日",
    V7: "収縮の安値が切り上がり",
  },
};

// V4/V5は設定駆動・V5はOR判定のため、vcpDetailの実測値/閾値があれば行に追記する。
function _mustFlagDetailSuffix(key, name, vcpDetail) {
  if (key !== "vcp" || !vcpDetail) return "";
  if (name === "V4") {
    const last = vcpDetail.depth_last_pct;
    const threshold = vcpDetail.last_depth_max_pct;
    if (last == null || threshold == null) return "";
    return ` (実測${last}% ≤ ${threshold}%)`;
  }
  if (name === "V5") {
    const dryup = vcpDetail.volume_dryup;
    if (!dryup || dryup.recent10_median == null || dryup.vol_ma50 == null) return "";
    const ratio = dryup.vol_ma50 ? Math.round((dryup.recent10_median / dryup.vol_ma50) * 100) : null;
    const thresholdPct = dryup.median_ratio_threshold != null ? Math.round(dryup.median_ratio_threshold * 100) : null;
    const via = dryup.sub_a_pass ? "水準" : dryup.sub_b_pass ? "トレンド" : "";
    const ratioText = ratio != null && thresholdPct != null ? ` (直近10日中央値 ${ratio}% ≤ ${thresholdPct}%)` : "";
    return via ? `${ratioText}[${via}判定]` : ratioText;
  }
  return "";
}

function renderMustChecklist(mustFlags, vcpDetail) {
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
    if (key === "vcp" && vcpDetail && vcpDetail.shakeout_detected) {
      const badge = document.createElement("span");
      badge.className = "sell-signal-badge signal-badge-accent";
      badge.textContent = "シェイクアウト検出";
      el.appendChild(badge);
    }
    const ul = document.createElement("ul");
    const labels = MUST_FLAG_LABELS[key] || {};
    for (const [name, value] of Object.entries(flags)) {
      const li = document.createElement("li");
      li.textContent = `${value ? "✓" : "✗"} ${labels[name] || name}${_mustFlagDetailSuffix(key, name, vcpDetail)}`;
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
// 保有ポジション (view-positions, 2026-07-11追加): docs/data/positions.json を
// 描画する。書き込みUIは無い(manual/positions.csvをGitHub web編集/ローカル編集で
// 運用する前提。passkeyAuthEnabled: false と同じ思想)。
// ---------------------------------------------------------------------------

const SELL_SIGNAL_LABELS = {
  STOP_BREACH: { label: "ストップ割れ", className: "signal-badge-danger" },
  MA50_BREAK: { label: "50日線割れ", className: "signal-badge-danger" },
  MA200_BREAK: { label: "200日線割れ", className: "signal-badge-danger" },
  TAKE_PROFIT_ZONE: { label: "2R到達", className: "signal-badge-accent" },
  BREAKEVEN_READY: { label: "建値SL推奨", className: "signal-badge-warn" },
};

function renderPositionsWarningBanner(positionsData) {
  const el = document.getElementById("positions-warning");
  if (!el) return;
  const withSignals = (positionsData?.positions || []).filter((p) => (p.sell_signals || []).length > 0);
  if (!withSignals.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.innerHTML = `⚠ 保有${withSignals.length}銘柄に売りシグナル。<a href="#positions">保有ビューを確認</a>`;
}

async function initPositionsView() {
  const container = document.getElementById("positions-table-wrap");
  const emptyEl = document.getElementById("positions-empty");
  const warnEl = document.getElementById("positions-warnings");
  const metaEl = document.getElementById("positions-meta");
  if (!container) return;

  let data = null;
  try {
    data = await window.MinerviniData.fetchJson("data/positions.json", { optional: true });
  } catch (e) {
    data = null;
  }

  const positions = data && Array.isArray(data.positions) ? data.positions : [];

  if (metaEl) {
    metaEl.textContent = data && data.generated_at
      ? `最終更新: ${new Date(data.generated_at).toLocaleString("ja-JP")}`
      : "";
  }

  if (!positions.length) {
    container.innerHTML = "";
    if (warnEl) warnEl.hidden = true;
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.innerHTML = '保有ポジションはありません。'
        + '<a href="https://github.com/allan3maximin/minervini/edit/master/manual/positions.csv" target="_blank" rel="noopener">manual/positions.csv</a> に行を追加してください。';
    }
    return;
  }
  if (emptyEl) emptyEl.hidden = true;

  const sorted = [...positions].sort((a, b) => {
    const aHas = (a.sell_signals || []).length > 0 ? 1 : 0;
    const bHas = (b.sell_signals || []).length > 0 ? 1 : 0;
    return bHas - aHas;
  });

  const rows = sorted
    .map((p) => {
      const badges = (p.sell_signals || [])
        .map((sig) => {
          const meta = SELL_SIGNAL_LABELS[sig] || { label: sig, className: "" };
          return `<span class="sell-signal-badge ${meta.className}">${escapeHtml(meta.label)}</span>`;
        })
        .join(" ");
      const signalsCell = badges || (p.data_missing ? "データなし" : "-");
      const rowClass = p.data_missing ? "row-static" : "";
      return `
        <tr class="${rowClass}" data-code="${escapeHtml(p.code)}">
          <td>${escapeHtml(p.code)}</td>
          <td>${escapeHtml(p.name || "")}</td>
          <td>${formatClose(p.entry_price)}</td>
          <td>${p.close != null ? formatClose(p.close) : "-"}</td>
          <td>${p.pl_pct != null ? p.pl_pct.toFixed(2) + "%" : "-"}</td>
          <td>${p.r_multiple != null ? p.r_multiple.toFixed(2) : "-"}</td>
          <td>${formatClose(p.current_stop)}</td>
          <td>${p.dist_to_stop_pct != null ? p.dist_to_stop_pct.toFixed(2) + "%" : "-"}</td>
          <td>${p.days_held}</td>
          <td>${signalsCell}</td>
        </tr>
      `;
    })
    .join("");

  container.innerHTML = `
    <div class="table-scroll">
      <table class="positions-table">
        <thead>
          <tr>
            <th>コード</th><th>銘柄名</th><th>建値</th><th>現在値</th><th>損益%</th>
            <th>R</th><th>ストップ</th><th>ストップまで%</th><th>保有日数</th><th>シグナル</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  container.querySelectorAll("tr[data-code]").forEach((tr) => {
    tr.addEventListener("click", () => {
      window.location.hash = "stock/" + tr.dataset.code;
    });
  });

  if (warnEl) {
    if (data.warnings && data.warnings.length) {
      warnEl.hidden = false;
      warnEl.innerHTML = data.warnings.map((w) => escapeHtml(w)).join("<br>");
    } else {
      warnEl.hidden = true;
    }
  }
}

// ---------------------------------------------------------------------------
// SPA router (Dockナビ): index.htmlの5ビューをlocation.hashで切り替える。
// セクターマップ/バッチ実行は表示のたびに再init(ヒートマップはコンテナが
// 見えて初めてclientWidthが正しく取れるため、バッチ実行は履歴を毎回最新化
// するため)。銘柄詳細は"stock/CODE"の形でパラメータ付きハッシュを取る
// (Dockメニューには出さない、ダッシュボードからのドリルダウン専用ビュー)。
// ---------------------------------------------------------------------------

const VIEWS = ["dashboard", "heatmap", "stocklist", "invest", "positions", "batch", "stock"];

function showView(hash) {
  const [rawName, param] = hash.split("/");
  const name = VIEWS.includes(rawName) ? rawName : "dashboard";
  for (const v of VIEWS) {
    const section = document.getElementById(`view-${v}`);
    if (section) section.hidden = v !== name;
  }
  // bodyはスクロールしない(app shell)。ビュー自身が縦スクロールを持つので、
  // ページ切替時に前回のスクロール位置を引き継がないようリセットする。
  const activeSection = document.getElementById(`view-${name}`);
  if (activeSection) activeSection.scrollTop = 0;
  // タイトル(MinerviniScreener)はダッシュボードのみ表示。他ビューは縦領域を確保するため隠す。
  const pageHeader = document.querySelector(".page-header");
  if (pageHeader) pageHeader.hidden = name !== "dashboard";
  document.querySelectorAll(".dock-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  // ヒートマップは独立ページ。コンテナが見えて初めてclientWidthが
  // 正しく取れるため、ヒートマップ表示のたびに再init。
  if (name === "heatmap" && typeof initHeatmap === "function") {
    initHeatmap();
  }
  if (name === "batch" && window.MinerviniBatch) {
    window.MinerviniBatch.initBatchView();
  }
  if (name === "positions") {
    initPositionsView();
  }
  if (name === "stocklist") {
    initListView();
  }
  if (name === "stock") {
    initStockPage(param ? decodeURIComponent(param) : null);
  }
}

// ドック上を横スライド(スワイプ)すると、ドックの並び順で隣のビューへ切り替える。
// (ドックアイテムの並べ替えではなく画面切り替え。)タップは従来どおり各ボタンの
// ビューへ直接遷移。スワイプ直後の合成clickはキャプチャ段階で握りつぶして誤遷移を防ぐ。
const DOCK_SWIPE_THRESHOLD = 36;

function currentViewName() {
  const raw = (window.location.hash.replace("#", "").split("/")[0]) || "dashboard";
  return raw;
}

function initDockSwipe(dock) {
  let startX = 0;
  let startY = 0;
  let tracking = false;
  let swiped = false;

  dock.addEventListener("pointerdown", (e) => {
    startX = e.clientX;
    startY = e.clientY;
    tracking = true;
    swiped = false;
  });

  dock.addEventListener("pointermove", (e) => {
    if (!tracking || swiped) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) < DOCK_SWIPE_THRESHOLD || Math.abs(dx) <= Math.abs(dy)) return;
    swiped = true; // 横スワイプ確定
    const order = Array.from(dock.querySelectorAll(".dock-btn")).map((b) => b.dataset.view);
    let idx = order.indexOf(currentViewName());
    if (idx < 0) idx = 0;
    // 左スワイプ(dx<0)=次のビュー / 右スワイプ(dx>0)=前のビュー
    const nextIdx = dx < 0 ? Math.min(order.length - 1, idx + 1) : Math.max(0, idx - 1);
    if (order[nextIdx] && nextIdx !== idx) window.location.hash = order[nextIdx];
  });

  const end = () => {
    tracking = false;
  };
  dock.addEventListener("pointerup", end);
  dock.addEventListener("pointercancel", end);

  // スワイプ直後の合成clickを握りつぶし、タップ扱いの誤遷移を防ぐ。
  dock.addEventListener(
    "click",
    (e) => {
      if (swiped) {
        e.stopPropagation();
        e.preventDefault();
        swiped = false;
      }
    },
    true
  );
}

// ドック小型化: スクロールコンテナ毎に前回スクロール位置を保持し、
// 下方向へ動いたら .compact を付与、上方向で剥がす。ページ最上部に戻ったら
// 必ず展開状態に戻す(ユーザーが「上に戻ったのに小さいまま」で困惑しないため)。
// 対象は view-section 配下の要素含む全ての scroll イベントで、
// stock-panel など複数スクロール要素があってもキャプチャで拾える。
const DOCK_SHRINK_DELTA = 8;
const DOCK_SHRINK_TOP_RESET = 24;
function initDockScrollShrink(dock) {
  const lastByTarget = new WeakMap();
  let raf = 0;
  let pending = null;
  function applyState(target) {
    const y = (target && typeof target.scrollTop === "number") ? target.scrollTop : (window.scrollY || 0);
    const prev = lastByTarget.get(target) ?? y;
    lastByTarget.set(target, y);
    if (y < DOCK_SHRINK_TOP_RESET) {
      dock.classList.remove("compact");
      return;
    }
    const d = y - prev;
    if (d > DOCK_SHRINK_DELTA) dock.classList.add("compact");
    else if (d < -DOCK_SHRINK_DELTA) dock.classList.remove("compact");
  }
  function onScroll(e) {
    pending = e.target;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      applyState(pending);
    });
  }
  // capture=trueで view-section 内スクロール(=各ビューのoverflow-y:auto)も拾う。
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });
}

function initRouter() {
  const dock = document.getElementById("dock-nav");
  if (!dock) return;
  initDockSwipe(dock);
  initDockScrollShrink(dock);
  dock.querySelectorAll(".dock-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      window.location.hash = btn.dataset.view;
      // ビュー切替時はドックを元に戻す(前ビューで小さくなった状態を引きずらない)。
      dock.classList.remove("compact");
    });
  });
  window.addEventListener("hashchange", () => {
    showView(window.location.hash.replace("#", "") || "dashboard");
    dock.classList.remove("compact");
  });
  showView(window.location.hash.replace("#", "") || "dashboard");
}

// ---------------------------------------------------------------------------
// 起動時パスキーゲート: report.json が暗号化封筒ならデータ鍵が入るまで
// 全ビューの初期化を止め、ロック画面を出す。平文なら従来どおり素通し
// (=データ暗号化を有効にした時点で自動的にゲートが立ち上がる)。
// 鍵は webauthn-vault.js の解錠(パスキー) or 初回セットアップで入り、
// secure-fetch.js の setDataKey() が "minervini-unlocked" を発火して閉じる。
// ---------------------------------------------------------------------------

async function ensureDataAccess() {
  const overlay = document.getElementById("lock-screen");
  const hideOverlay = () => { if (overlay) overlay.hidden = true; };

  // リロード時: sessionStorageに保持した読み取り用データ鍵で先に解錠を試みる
  // (パスキー不要)。成功すればこのままゲートを素通しできる。
  if (!window.MinerviniData.hasDataKey()) {
    try { await window.MinerviniData.restoreDataKey(); } catch (e) { /* 無視 */ }
  }

  let probe = null;
  try {
    const resp = await fetch("data/report.json", { cache: "no-store" });
    if (resp.ok) probe = await resp.json();
  } catch (e) {
    probe = null;
  }

  // 平文 or 取得不能、または既に鍵あり(復元成功含む) → ゲート不要。
  // オーバーレイ(初期描画から出しっぱなしのスプラッシュ)を消して素通し。
  if (!window.MinerviniData.isEnvelope(probe) || window.MinerviniData.hasDataKey()) {
    hideOverlay();
    return;
  }

  // 暗号化+未解錠 → アクセスしたこのタイミングでパスキー解錠を促す。
  const loadingEl = document.getElementById("lock-loading");
  const promptEl = document.getElementById("lock-prompt");
  const unlockBtn = document.getElementById("lock-unlock-btn");
  const errEl = document.getElementById("lock-error");
  if (loadingEl) loadingEl.hidden = true;
  if (promptEl) promptEl.hidden = false;
  if (unlockBtn) unlockBtn.hidden = false;
  if (!overlay || !unlockBtn) return;

  let vault = null;
  try {
    vault = await window.MinerviniVault.fetchVault();
  } catch (e) {
    /* 取得失敗は解錠ボタン押下時に再試行する */
  }

  await new Promise((resolve) => {
    function check() {
      if (window.MinerviniData.hasDataKey()) {
        overlay.hidden = true;
        window.removeEventListener("minervini-unlocked", check);
        resolve();
      }
    }
    window.addEventListener("minervini-unlocked", check);

    unlockBtn.addEventListener("click", async () => {
      if (errEl) errEl.hidden = true;
      unlockBtn.disabled = true;
      const original = unlockBtn.textContent;
      unlockBtn.textContent = "認証中...";
      try {
        if (!vault) vault = await window.MinerviniVault.fetchVault();
        if (!vault) {
          throw new Error("保管庫(vault.json)がまだありません。GitHub上でセットアップしてください。");
        }
        const result = await window.MinerviniVault.unlock(vault);
        if (!result || !result.hasDataKey) {
          throw new Error("保管庫にデータ鍵が入っていません。データ鍵込みで再セットアップしてください。");
        }
        // ついでに書き込み系ボタンも解錠状態にしておく(二度目のFace IDを要求しない)。
        applyLockState(true);
        const hdrBtn = document.getElementById("vault-unlock-btn");
        if (hdrBtn) {
          hdrBtn.textContent = "🔒 解錠済み";
          hdrBtn.disabled = true;
        }
        check();
      } catch (e) {
        if (errEl) {
          errEl.textContent = e.message || String(e);
          errEl.hidden = false;
        }
      } finally {
        unlockBtn.disabled = false;
        unlockBtn.textContent = original;
      }
    });
  });
}

async function bootApp() {
  await ensureDataAccess();
  if (document.getElementById("confirmed-tier-body")) {
    initDashboard();
  }
  // 銘柄詳細(view-stock)はDockナビを持つSPAシェル(index.html)内の1ビューに
  // なったため、initStockPage()はここで直接呼ばず、initRouter()内のshowView()が
  // hashが"stock/CODE"の時にだけ呼ぶ。
  if (document.getElementById("dock-nav")) {
    initRouter();
  }
}

bootApp();
