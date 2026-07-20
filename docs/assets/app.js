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

// ---------------------------------------------------------------------------
// 用語ヘルプ(？ボタン→ポップアップ説明)。2026-07-19タスク。表示専用機能で、
// スコア計算・判定ロジック・データ構造には一切影響しない
// (HANDOFF_TASKS_20260719.txt)。
// ---------------------------------------------------------------------------
const TERM_HELP = {
  market_signal: {
    title: "地合いシグナル(攻め/中立/守り)",
    body:
      "市場全体の環境を3段階で判定したもの。攻め=新規エントリー積極可、\n" +
      "中立=サイズ・銘柄数を控えめに、守り=新規エントリーは原則見送り。\n" +
      "MA200上回り率や新高値・新安値銘柄数などの内部指標から機械的に判定される。\n" +
      "個別銘柄がどれだけ良くても、地合いが悪い時に買うと勝率が大きく落ちる、\n" +
      "というのがミネルヴィニの基本姿勢。",
  },
  market_score: {
    title: "市場スコア",
    body:
      "地合いを0〜100点で数値化した参考値。ブレッドス40%・指数トレンド30%・\n" +
      "モメンタム20%・リスク選好10%の加重平均。矢印はスコアの方向\n" +
      "(↗改善 / →横ばい / ↘悪化)。攻め/中立/守りの色判定とは独立した\n" +
      "表示専用の補助指標で、色と食い違うこともある(その時は色が優先)。",
  },
  breadth: {
    title: "ブレッドス(市場の広がり)",
    body:
      "上昇に参加している銘柄がどれだけ多いかを見る指標群。指数だけが一部の\n" +
      "大型株で吊り上げられ、中身(個別銘柄)が付いてきていない相場を見分ける。\n" +
      "ブレッドスが弱い上昇はブレイクアウトの成功率が落ちる。",
  },
  index_trend_score: {
    title: "指数トレンド",
    body:
      "TOPIX・日経225・グロース250の3指数が50日線・200日線の上にあるか、\n" +
      "200日線が上向きかをスコア化したもの。主要指数が揃って上向きなら\n" +
      "追い風、割れていれば逆風。",
  },
  momentum_score: {
    title: "モメンタム",
    body: "騰落レシオや上昇/下落銘柄数など、市場の直近の勢いをスコア化したもの。",
  },
  risk_appetite: {
    title: "リスク選好",
    body:
      "グロース250のTOPIXに対する相対的な強さから、市場がリスクを取りに\n" +
      "行っているかを見る。小型グロースに資金が入る局面は、この手法が狙う\n" +
      "成長株ブレイクアウトの成功率が上がりやすい。",
  },
  pct_above_ma200: {
    title: "MA200上回り率",
    body:
      "スクリーニング対象銘柄のうち、終値が200日移動平均線より上にある割合。\n" +
      "過半数が上なら市場の長期トレンドは健全。カッコ内の20日差分は\n" +
      "20営業日前からの変化幅で、プラスなら地合いが改善方向。",
  },
  pct_above_ma50: {
    title: "MA50上回り率",
    body: "終値が50日移動平均線より上にある銘柄の割合。中期トレンドへの参加率で、\nMA200版より敏感に動く。",
  },
  up_down_ratio_25: {
    title: "騰落レシオ25",
    body:
      "過去25営業日の値上がり銘柄数合計÷値下がり銘柄数合計。\n" +
      "一般に120%(1.2)超は過熱気味、70%(0.7)近辺は売られすぎの目安。\n" +
      "データが25日分貯まるまでは「蓄積中」と表示される。",
  },
  nh_nl: {
    title: "NH-NL(新高値-新安値)",
    body:
      "新高値を付けた銘柄数から新安値を付けた銘柄数を引いた値。当日値と、\n" +
      "それを毎日積み上げた累積値を表示。累積線が右肩上がりなら市場の中身は\n" +
      "健全、指数が高値でもこの線が下がり始めたら内部悪化のサイン。",
  },
  growth_rel_20d: {
    title: "グロース-TOPIX 20日相対",
    body:
      "グロース250指数のTOPIXに対する直近20営業日の相対リターン。\n" +
      "プラスなら小型グロース優位(リスク選好が強い)、マイナスなら大型・\n" +
      "ディフェンシブ優位。",
  },
  index_trend_table: {
    title: "指数トレンド表の見方",
    body:
      "○=その指数が当該移動平均線(50日/200日)の上にある。×=下にある。\n" +
      "「傾き↑」は200日線自体が上向きかどうか。3指数が揃って○なら強い地合い。",
  },
  vcp_funnel: {
    title: "VCPファネル",
    body:
      "トレンドテンプレート合格銘柄が、VCP(ベース)判定のどの段階にいるかの内訳。\n" +
      "・ベース到達: ベース(値固め)が検出され、VCP条件の判定まで進んだ銘柄\n" +
      "・高値更新中: 高値圏を走っていてまだベース(調整)を作っていない銘柄\n" +
      "・形成中: ベースを作り始めたが日数不足でまだ判定できない銘柄\n" +
      "・ボラ過大: 値動きが荒すぎてVCPの対象外になった銘柄\n" +
      "「高値更新中」の減少は、リーダー銘柄が調整入り=数週間後にセットアップが\n" +
      "増える先行サインとして折れ線で監視している。",
  },
  tech_score: {
    title: "テクニカルスコア",
    body:
      "RS(相対力)・52週高値からの近さ・200日線の上向き継続日数など、\n" +
      "テクニカル要素のみで付けた点数(0-100)。",
  },
  full_score: {
    title: "フルスコア",
    body:
      "テクニカルスコアの要素に加え、EPS成長率・売上成長率など業績の伸びも\n" +
      "加味した点数(0-100)。ファンダデータが一部欠けている場合は、\n" +
      "取得できた要素だけで100点満点に再計算される。",
  },
  vcp_score: {
    title: "VCPスコア",
    body:
      "VCP(収縮パターン)の質の点数(0-100)。収縮のタイトさ・出来高の枯れ具合・\n" +
      "ベースの形などを評価。高いほど「教科書的な」セットアップ。",
  },
  total_score: {
    title: "総合スコア",
    body:
      "各スコアを合成した順位付け用の点数。エントリー可否の判定そのものではなく、\n" +
      "リスト内の並び順を決めるためのもの(判定はMUST条件が担う)。",
  },
  margin_ratio: {
    title: "信用倍率",
    body:
      "信用買い残高÷信用売り残高。高いほど将来の売り圧力(利確・投げ売り)が\n" +
      "溜まっている状態で、一般に5倍超は重い。1倍未満は売り方優勢で、\n" +
      "買い戻しによる踏み上げが期待できる形。週次データのため最大5営業日遅れる。",
  },
  margin_buy: {
    title: "買残(信用買い残高)",
    body:
      "信用取引で買われてまだ決済されていない株数。将来必ず売り決済される\n" +
      "「予約された売り」でもあるため、多すぎると上値が重くなる。\n" +
      "カッコ内は前週比の増減率。",
  },
  margin_sell: {
    title: "売残(信用売り残高)",
    body: "空売りされてまだ買い戻されていない株数。将来必ず買い戻されるため、\n多いと踏み上げ(買い戻しによる急騰)の燃料になる。",
  },
  days_to_cover: {
    title: "買残回転日数",
    body:
      "信用買残÷平均出来高。溜まった買残を消化するのに何日分の出来高が\n" +
      "必要かの目安。大きいほど需給が重く、ブレイクアウトの上値が抑えられやすい。",
  },
  pivot: {
    title: "ピボット",
    body:
      "ベース(値固め)の上端にあたる抵抗ライン。ここを平時より多い出来高を\n" +
      "伴って上抜けた瞬間が本来のエントリーポイント。ピボットから+5%超\n" +
      "離れて追いかけるのは禁止(伸びすぎ)。",
  },
  stop: {
    title: "損切り(ストップ)",
    body: "エントリー時に決めておく撤退価格。買値から-7〜8%が機械的な上限。\nポジションサイズはこの損切り幅から逆算する。",
  },
  rs_line: {
    title: "RS(対TOPIX)",
    body:
      "株価をTOPIXで割った相対強さの推移。右肩上がりなら市場平均より強い。\n" +
      "株価が横ばいでもRS線が上がっていれば相対的に強い(市場が下げる中で\n" +
      "耐えている)ことを意味し、先行指標になる。",
  },
  rs_rating: {
    title: "RSレーティング",
    body: "全銘柄の株価パフォーマンスを相対順位化して1〜99で表した値。\n70以上がトレンドテンプレートの必須条件、80〜90以上が理想。",
  },
  risk_pct: {
    title: "1トレードあたりリスク(%)",
    body:
      "そのトレードで失ってよい金額の、総資金に対する割合。\n" +
      "ポジションサイズ=許容損失額÷(エントリー価格-損切り価格)で逆算する。\n" +
      "ミネルヴィニの推奨は0.5〜1.25%程度。",
  },
  r_multiple: {
    title: "R(アール)と2R",
    body:
      "R=エントリー価格-損切り価格(1トレードの想定リスク幅)。\n" +
      "2R到達=リスクの2倍の含み益。一部利確や損切りラインの引き上げを\n" +
      "検討する目安。",
  },
  breakeven_sl: {
    title: "建値SL",
    body: "損切りラインを買値(建値)まで引き上げること。以後そのポジションは\n最悪でも損失ゼロになり、無リスクで利を伸ばせる。",
  },
  tier_confirmed: {
    title: "〔本命〕ファンダ強度確認済み",
    body:
      "VCPセットアップ完成に加え、直近EPS YoY+25%以上かつ売上YoY+20%以上\n" +
      "(Minervini Code 33準拠)を満たした銘柄。",
  },
  tier_pool: {
    title: "〔候補〕セットアップ完成・エントリー可能",
    body:
      "VCPベース完成済み(ピボット・ストップあり)だが、ファンダが未確認または\n" +
      "昇格基準(EPS YoY+25%/売上YoY+20%)未達。純テクニカル評価によるランキング。\n" +
      "「ファンダ弱」はデータはあるが基準未達の銘柄。",
  },
  tier_watchlist: {
    title: "〔監視〕8条件合格・セットアップ形成待ち",
    body:
      "トレンドテンプレート8条件をすべて満たしたが、VCPベースが未完成でまだ\n" +
      "エントリーできない銘柄。ベースが検出されると〔候補〕/〔本命〕に昇格する。\n" +
      "全件RS降順。",
  },
};

function helpBtnHtml(key) {
  const t = TERM_HELP[key];
  if (!t) return ""; // 未定義キーは静かに何も出さない(壊さない)
  return `<button type="button" class="help-btn" data-help="${key}" aria-label="用語説明: ${escapeHtml(t.title)}">?</button>`;
}

let savedHelpScrollY = 0;

function closeHelpPopover() {
  const el = document.getElementById("help-popover-overlay");
  if (!el) return;
  el.remove();
  // 他のモーダル(fundamentals-modal.js等)が同時に開いていなければロック解除。
  if (!document.getElementById("minervini-modal-overlay") && !document.getElementById("market-modal")) {
    document.body.classList.remove("modal-open");
    document.body.style.top = "";
    window.scrollTo(0, savedHelpScrollY);
  }
}

function openHelpPopover(key) {
  const t = TERM_HELP[key];
  if (!t) return;
  closeHelpPopover();

  const overlay = document.createElement("div");
  overlay.className = "help-popover-overlay";
  overlay.id = "help-popover-overlay";
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeHelpPopover();
  });

  const box = document.createElement("div");
  box.className = "help-popover";

  const titleEl = document.createElement("h3");
  titleEl.className = "help-popover-title";
  titleEl.textContent = t.title;

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "help-popover-close";
  closeBtn.setAttribute("aria-label", "閉じる");
  closeBtn.textContent = "×";
  closeBtn.addEventListener("click", closeHelpPopover);

  const head = document.createElement("div");
  head.className = "help-popover-head";
  head.appendChild(titleEl);
  head.appendChild(closeBtn);

  const bodyEl = document.createElement("p");
  bodyEl.className = "help-popover-body";
  bodyEl.textContent = t.body; // innerHTML不可(エスケープ問題を構造的に消す)。改行はCSSのwhite-space:pre-lineで表現。

  box.appendChild(head);
  box.appendChild(bodyEl);
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  savedHelpScrollY = window.scrollY || 0;
  document.body.style.top = `-${savedHelpScrollY}px`;
  document.body.classList.add("modal-open");
}

// イベント委任(document に1つだけ)。カード類のクリックナビゲーションや
// <details>のトグルより先に止める(stopPropagation必須)。
document.addEventListener("click", (e) => {
  const helpBtn = e.target.closest(".help-btn");
  if (!helpBtn) return;
  e.preventDefault();
  e.stopPropagation();
  openHelpPopover(helpBtn.dataset.help);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.getElementById("help-popover-overlay")) closeHelpPopover();
});

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

// 需給(信用取引週末残高)バッジ。表示専用(総合スコアには一切使わない)。
// バッジ種別(margin.badge)はサーバ側(build_site.margin_badge)がconfig閾値で確定済み。
// カード(一覧)では「買残重い」のみ表示し、「売り長」は個別銘柄詳細ページの
// 需給カードでのみ表示する(3段目のバッジ行が混み合うのを避けるため)。
const MARGIN_BADGE_LABELS = {
  heavy_buy: { label: "買残重い", className: "signal-badge-warn" },
  short: { label: "売り長", className: "signal-badge-accent" },
};
function marginBadgeHtml(m, { detail = false } = {}) {
  if (!m || !m.badge) return "";
  if (!detail && m.badge !== "heavy_buy") return "";
  const meta = MARGIN_BADGE_LABELS[m.badge];
  return meta ? `<span class="sell-signal-badge ${meta.className}">${meta.label}</span>` : "";
}

// リスト画面カードのソートキー定義。横スクロール表を廃止したため、表示項目は
// カード側(renderCardList)に直書きし、ここには並び順の定義だけ残す。
const CARD_SORTS = {
  total_score: (s) => s.total_score ?? -Infinity,
  rs: (s) => s.rs ?? -Infinity,
  change_pct: (s) => s.change_pct ?? -Infinity,
};

// 並び替えチップ(リスト画面)。tierごとの選択をlocalStorageに保存し、
// リロード後も維持する。既定は従来の並び(本命/候補=総合スコア、監視=RS)。
const CARD_SORT_STORAGE_KEY = "minervini-card-sort";
const CARD_SORT_DEFAULTS = { confirmed: "total_score", pool: "total_score", watchlist: "rs" };
const CARD_SORT_LABELS = { total_score: "スコア", rs: "RS", change_pct: "前日比" };

function getCardSortKey(tier) {
  try {
    const prefs = JSON.parse(localStorage.getItem(CARD_SORT_STORAGE_KEY) || "{}") || {};
    const key = prefs[tier];
    if (key && CARD_SORTS[key]) return key;
  } catch (e) {
    /* 破損データ・プライベートブラウズ等は既定値へフォールバック */
  }
  return CARD_SORT_DEFAULTS[tier] || "total_score";
}

function setCardSortKey(tier, key) {
  let prefs = {};
  try {
    prefs = JSON.parse(localStorage.getItem(CARD_SORT_STORAGE_KEY) || "{}") || {};
  } catch (e) {
    prefs = {};
  }
  prefs[tier] = key;
  try {
    localStorage.setItem(CARD_SORT_STORAGE_KEY, JSON.stringify(prefs));
  } catch (e) {
    /* 保存できなくても表示上の並び替えは機能する */
  }
}

// ---------------------------------------------------------------------------
// Dashboard (index.html)
// ---------------------------------------------------------------------------

let pendingFund = {};

// report.json の取得+復号結果のキャッシュ。initStockPage(銘柄詳細)が遷移の
// たびにfetch+復号し直すのを避ける。データは日次更新なのでTTLは持たず、
// initDashboard(ダッシュボード再訪)が常に再fetchして上書きする。
let reportCache = null;

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
  reportCache = { data: report, fetchedAt: Date.now() };

  pendingFund = window.MinerviniFundamentalsUI
    ? window.MinerviniFundamentalsUI.reconcilePending(report.generated_at)
    : {};

  renderHeader(report);
  initMarketTabs();
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

// 前営業日比の騰落率に応じてカード枠色を連続グラデーションで返す。
// 上げ=緑・下げ=赤で、変動が大きいほど濃く(彩度↑・明度↓)なる。±CAP%で最大濃度。
// 金利(unit=="%")は騰落率が無いのでpt差を小さめのCAPで代用。ぱっと見で
// 指数の動きの大きさを掴めるようにするのが狙い。無風(≈0)は既定枠色のまま。
function indexEdgeStyle(entry) {
  let t; // -1..+1 に正規化した変動の強さ(符号=方向)
  if (entry.change_pct != null) {
    t = entry.change_pct / 2.5; // ±2.5%で最大濃度
  } else if (entry.unit === "%" && entry.change != null) {
    t = entry.change / 0.08; // 金利は±0.08ptで最大濃度
  } else {
    return "";
  }
  t = Math.max(-1, Math.min(1, t));
  const mag = Math.abs(t);
  if (mag < 0.03) return ""; // ほぼ無風は既定の枠色を維持
  const hue = t > 0 ? 145 : 2; // 緑 / 赤
  const sat = Math.round(38 + mag * 50); // 38%→88%
  const light = Math.round(60 - mag * 26); // 60%→34%(大きいほど濃く暗く)
  const edge = `hsl(${hue} ${sat}% ${light}%)`;
  const glow = `hsla(${hue}, ${sat}%, ${light}%, 0.16)`;
  // 枠(border) + 1px内側リングで実質2px化しつつ、淡いグローで面としても視認。
  return `border-color:${edge};box-shadow:inset 0 0 0 1px ${edge},0 0 10px ${glow};`;
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
      const edge = stale ? "" : indexEdgeStyle(entry);
      return `
        <div class="market-card${stale ? " is-stale" : ""}" role="button" tabindex="0" data-market-key="${escapeHtml(entry.key)}"${edge ? ` style="${edge}"` : ""}>
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

// initDashboardはダッシュボード再訪のたびに呼ばれるため、interval IDを保持して
// 開始前に必ず止める(多重登録するとポーリングが呼ばれた回数分だけ増殖する)。
let liveIndicesTimer = null;

function startLiveIndices() {
  const section = document.getElementById("market-overview");
  const badge = document.getElementById("market-live-badge");
  if (!section) return;
  if (badge) badge.hidden = false;

  if (liveIndicesTimer) clearInterval(liveIndicesTimer);
  liveIndicesTimer = setInterval(async () => {
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
  const prioLine = p1 != null ? `<span>8条件合格: <b>${p1}件</b></span>` : "";
  el.innerHTML = `
    <div class="breadth-meter-title">スクリーニング状況</div>
    <div class="breadth-meter-stats">
      <span>テンプレート通過率: <b>${passRate}</b></span>
      <span>セットアップ数: <b>${latest.watch_count ?? "-"}</b></span>
      <span>直近ブレイク成功率: <b>${successRate}</b></span>
      ${prioLine}
    </div>
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

// 地合い詳細パネル(2026-07-18 タスク4)。以下はすべて表示専用の補助指標
// (src/report/market_signal.py タスク3で追加)であり、上の green/yellow/red
// 判定・カード色分けには一切影響しない。旧history(これらのフィールドが無い
// エントリ)でも壊れないよう、値は必ずnullガードしてから表示する。
const MARKET_SCORE_TREND_ARROW = {
  improving: "↗改善",
  flat: "→横ばい",
  deteriorating: "↘悪化",
};

// weight は src/report/market_signal.py DEFAULTS["detail_weights"] と一致させた
// 表示用の固定値(バックエンドから配点自体は送られてこないため)。
// 表示は「寄与度方式」: 各項目の生スコア(0-100)×配点で寄与ptを出し、
// 「16.0/40pt」のように配点満点に対する寄与で見せる。4項目の寄与合計が
// 総合スコア(market_score)と一致するので、%表記(40%等)の混在より辻褄が追いやすい。
const MARKET_DETAIL_SCORE_ITEMS = [
  { key: "breadth", label: "ブレッドス", weight: 40, helpKey: "breadth" },
  { key: "index_trend", label: "指数トレンド", weight: 30, helpKey: "index_trend_score" },
  { key: "momentum", label: "モメンタム", weight: 20, helpKey: "momentum_score" },
  { key: "risk_appetite", label: "リスク選好", weight: 10, helpKey: "risk_appetite" },
];

// 寄与pt(生スコア×配点/100)。丸めは0.1pt単位で行い、合計行との誤差を抑える。
function contributionPt(rawScore, weight) {
  return Math.round(rawScore * weight) / 100;
}

// 「16.5pt」「16pt」のように、小数が不要なら省いて表示する。
function formatPt(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

// up_down_ratio_25 は history 24件+当日で計25件、breadth_trend_20d(20日差分)は
// 20日前history+当日で計21件が必要(src/report/market_signal.py の
// _UP_DOWN_WINDOW=25 / _N_DAY_RETURN=20 に対応)。蓄積不足の間は「蓄積中」を出す。
function accumulationNote(historyLen, requiredLen) {
  const remain = requiredLen - historyLen;
  return remain > 0 ? `蓄積中(あと${remain}日)` : null;
}

function okx(v) {
  if (v === true) return "○";
  if (v === false) return "×";
  return "-";
}

function formatPct1(v, digits = 1) {
  return v != null ? `${(v * 100).toFixed(digits)}%` : "-";
}

function formatSignedPctPoints(v, digits = 1) {
  if (v == null) return "-";
  const pts = v * 100;
  return `${pts > 0 ? "+" : ""}${pts.toFixed(digits)}pt`;
}

function formatSignedPct(v, digits = 1) {
  if (v == null) return "-";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function indexTrendRowHtml(label, t) {
  if (!t) return `<tr><td>${label}</td><td colspan="3">データ不足</td></tr>`;
  return `<tr><td>${label}</td><td>${okx(t.index_above_ma50)}</td><td>${okx(t.index_above_ma200)}</td><td>${okx(t.index_ma200_slope_up)}</td></tr>`;
}

function renderMarketDetailHtml(latest, history) {
  const sb = latest.score_breakdown || {};
  // 寄与度方式: バーの塗りは生スコア(0-100)のまま、右の数値は「寄与pt/配点pt」。
  // 4行の寄与ptを足すと下の合計行(=総合スコア)になる。
  let contribSum = 0;
  let hasAnyScore = false;
  const scoreBarsHtml = MARKET_DETAIL_SCORE_ITEMS.map((item) => {
    const v = sb[item.key];
    const pct = v != null ? Math.max(0, Math.min(100, v)) : 0;
    let valueText = `-/${item.weight}pt`;
    if (v != null) {
      hasAnyScore = true;
      const pt = contributionPt(Math.max(0, Math.min(100, v)), item.weight);
      contribSum += pt;
      valueText = `${formatPt(pt)}/${item.weight}pt`;
    }
    return `<div class="score-bar-row"><span>${item.label}${helpBtnHtml(item.helpKey)}</span><div class="score-bar"><div class="score-bar-fill" style="width:${pct}%"></div></div><span>${valueText}</span></div>`;
  }).join("");
  const scoreTotalHtml = hasAnyScore
    ? `<div class="score-bar-total"><span>合計</span><span>${formatPt(Math.round(contribSum * 10) / 10)}/100pt</span></div>`
    : "";

  const historyLen = history.length;
  const udNote = accumulationNote(historyLen, 25);
  const udText = latest.up_down_ratio_25 != null ? latest.up_down_ratio_25.toFixed(2) : (udNote || "-");

  const btNote = accumulationNote(historyLen, 21);
  const btText = latest.breadth_trend_20d != null ? formatSignedPctPoints(latest.breadth_trend_20d) : (btNote || "-");

  const trends = latest.index_trends || {};

  // 詳細パネルの数値指標はいずれも当日値だけでなく直近最大60日の推移を出す
  // (breadth.json history が keep_days=60 で持っている分をそのまま可視化)。
  // 各フィールドは追加時期がバラバラで旧entryでは欠けるため、系列ごとに null を
  // filter で除外し、2点未満なら sparklineSvg が空文字→「データ蓄積中」に落とす。
  const MARKET_SPARK_ITEMS = [
    { key: "market_score", label: "地合いスコア" },
    { key: "pct_above_ma200", label: "MA200上回り率" },
    { key: "pct_above_ma50", label: "MA50上回り率" },
    { key: "up_down_ratio_25", label: "騰落レシオ25" },
    { key: "nh_nl_cumulative", label: "NH-NL累積" },
    { key: "growth_rel_20d", label: "グロース-TOPIX相対" },
  ];
  const sparklinesHtml = MARKET_SPARK_ITEMS.map((item) => {
    const series = history.filter((h) => h && h[item.key] != null).slice(-60).map((h) => ({ v: h[item.key] }));
    const isUp = series.length >= 2 && series[series.length - 1].v >= series[0].v;
    const spark = sparklineSvg(series, isUp) || '<p class="tier-note">データ蓄積中</p>';
    return `<div class="market-detail-spark"><div class="market-detail-spark-label">${item.label}</div>${spark}</div>`;
  }).join("");

  // 4セクション構成: スコア内訳 → 主要指標 → 指数トレンド → 推移。
  // 見出しを付けて役割の切れ目を明示する(2026-07-20 UI再構成)。
  return `
    <div class="market-detail-body">
      <section class="market-detail-section">
        <h4 class="market-detail-heading">スコア内訳<span class="market-detail-heading-sub">寄与pt/配点pt</span></h4>
        <div class="market-detail-scores">${scoreBarsHtml}${scoreTotalHtml}</div>
      </section>
      <section class="market-detail-section">
        <h4 class="market-detail-heading">主要指標<span class="market-detail-heading-sub">当日値</span></h4>
        <div class="market-detail-indicators">
          <div class="market-detail-row"><span>MA200上回り率${helpBtnHtml("pct_above_ma200")}</span><span>${formatPct1(latest.pct_above_ma200)}<span class="market-detail-sub">(20日差分 ${btText})</span></span></div>
          <div class="market-detail-row"><span>MA50上回り率${helpBtnHtml("pct_above_ma50")}</span><span>${formatPct1(latest.pct_above_ma50)}</span></div>
          <div class="market-detail-row"><span>騰落レシオ25${helpBtnHtml("up_down_ratio_25")}</span><span>${udText}</span></div>
          <div class="market-detail-row"><span>NH-NL(当日/累積)${helpBtnHtml("nh_nl")}</span><span>${latest.net_new_highs ?? "-"} / ${latest.nh_nl_cumulative ?? "-"}</span></div>
          <div class="market-detail-row"><span>グロース-TOPIX 20日相対${helpBtnHtml("growth_rel_20d")}</span><span>${formatSignedPct(latest.growth_rel_20d)}</span></div>
        </div>
      </section>
      <section class="market-detail-section">
        <h4 class="market-detail-heading">指数トレンド${helpBtnHtml("index_trend_table")}</h4>
        <div class="market-detail-table-wrap">
          <table class="market-detail-table">
            <thead><tr><th></th><th>50日線</th><th>200日線</th><th>傾き↑</th></tr></thead>
            <tbody>
              ${indexTrendRowHtml("TOPIX", trends.topix)}
              ${indexTrendRowHtml("日経225", trends.nikkei225)}
              ${indexTrendRowHtml("グロース250", trends.growth250)}
            </tbody>
          </table>
        </div>
      </section>
      <section class="market-detail-section">
        <h4 class="market-detail-heading">推移<span class="market-detail-heading-sub">直近最大60日</span></h4>
        <div class="market-detail-sparklines">${sparklinesHtml}</div>
      </section>
    </div>
  `;
}

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

  // market_score/score_trendはタスク3で追加された表示専用の補助指標。旧history
  // (これらが無いエントリ)ではバッジ自体を出さない。
  const scoreBadge = latest.market_score != null
    ? `<span class="market-score-badge">スコア ${Math.round(latest.market_score)}${latest.score_trend && MARKET_SCORE_TREND_ARROW[latest.score_trend] ? " " + MARKET_SCORE_TREND_ARROW[latest.score_trend] : ""}${helpBtnHtml("market_score")}</span>`
    : "";

  // 地合い(攻め/中立/守り)の過去推移。signalを持つ履歴を色付きティックで並べ、
  // レジームがいつ切り替わったかを一目で追えるようにする。signal欠損の旧entryは除外。
  // 2点未満(=当日のみ)なら意味がないのでストリップ自体を出さない。
  const signalHist = history.filter((h) => h && h.signal).slice(-60);
  const signalStripHtml = signalHist.length >= 2
    ? `<div class="market-signal-history">
        <span class="market-signal-history-label">地合い推移(直近${signalHist.length}日)</span>
        <div class="market-signal-history-track">${signalHist.map((h) => {
          const m = MARKET_SIGNAL_META[h.signal] || MARKET_SIGNAL_META.yellow;
          return `<span class="msh-tick ${m.className}" title="${escapeHtml(h.date || "")}: ${m.label}"></span>`;
        }).join("")}</div>
      </div>`
    : "";

  el.innerHTML = `
    <div class="market-signal-top">
      <div class="market-signal-label">${meta.label}${helpBtnHtml("market_signal")}</div>
      ${scoreBadge}
    </div>
    <ul class="market-signal-reasons">${reasons}</ul>
    <div class="market-signal-stats">MA200上回り率 ${pct200} / 新高値 ${newHigh}件 vs 新安値 ${newLow}件</div>
    ${signalStripHtml}
    ${caution}
    <details class="market-detail">
      <summary>地合い詳細</summary>
      ${renderMarketDetailHtml(latest, history)}
    </details>
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
    <div class="vcp-funnel-title">VCPファネル(スクリーニング通過銘柄の内訳)${helpBtnHtml("vcp_funnel")}</div>
    <div class="vcp-funnel-stats">
      <span>ベース到達: <b>${originOk(latest)}件</b></span>
      <span>高値更新中: <b>${latest.TOO_RECENT || 0}件</b></span>
      <span>形成中: <b>${latest.IMMATURE || 0}件</b></span>
      <span>ボラ過大: <b>${latest.TOO_VOLATILE || 0}件</b></span>
    </div>
    <div class="vcp-funnel-spark">
      <span class="vcp-funnel-spark-label">高値更新中 直近60日(減少=セットアップ増の先行シグナル)</span>
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

// ---------------------------------------------------------------------------
// 銘柄リストの永続フィルタ (設定画面で設定 → 次に変更するまでリストに適用)
// localStorage に保存し、本命/候補/監視の全ティアで renderTier /
// renderPriorityTier が描画前に適用する。空欄の項目は無視。指標が欠損(null)の
// 銘柄はそのフィルタでは除外しない(データ無しで黙って消えると気づけないため)。
// ---------------------------------------------------------------------------
const LIST_FILTER_SETTINGS_KEY = "minervini_list_filters";
const LIST_FILTER_SEGMENTS = ["プライム", "スタンダード", "グロース"];

// 全項目が無効(=絞り込みなし)のフィルタ。showSegmentsは「表示する市場」で、
// 初期値は全市場チェック(=全表示=絞り込みなし)。恒久・一時どちらの初期値にも使う。
function emptyListFilter() {
  return {
    minClose: null, maxClose: null,
    minRs: null, minScore: null, minMcap: null, maxMcap: null,
    showSegments: [...LIST_FILTER_SEGMENTS],
  };
}

// リスト画面の一時フィルタ(「その時用」)。localStorageには保存せずメモリ保持
// のみ = リロードで自動リセット。恒久フィルタ(設定画面/localStorage)とAND合成。
let adhocListFilter = emptyListFilter();

// 個別株画面の前後ナビが辿るリストのティア(本命/候補/監視)。カードから遷移した
// ときに記録し、個別株画面の ＜/＞ で同ティアのフィルタ済み並び順を前後移動する。
let listNavTier = null;

function loadListFilters() {
  const def = emptyListFilter();
  try {
    const raw = localStorage.getItem(LIST_FILTER_SETTINGS_KEY);
    if (!raw) return def;
    const p = JSON.parse(raw) || {};
    const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
    // showSegments(表示する市場)。旧データの excludeSegments(除外する市場)は
    // 反転して読み込む。どちらも無ければ全市場表示(絞り込みなし)。
    let showSegments;
    if (Array.isArray(p.showSegments)) {
      showSegments = p.showSegments.filter((x) => LIST_FILTER_SEGMENTS.includes(x));
    } else if (Array.isArray(p.excludeSegments)) {
      showSegments = LIST_FILTER_SEGMENTS.filter((x) => !p.excludeSegments.includes(x));
    } else {
      showSegments = [...LIST_FILTER_SEGMENTS];
    }
    return {
      minClose: num(p.minClose),
      maxClose: num(p.maxClose),
      minRs: num(p.minRs),
      minScore: num(p.minScore),
      minMcap: num(p.minMcap),
      maxMcap: num(p.maxMcap),
      showSegments,
    };
  } catch (e) {
    return def;
  }
}

function saveListFilters(f) {
  try {
    localStorage.setItem(LIST_FILTER_SETTINGS_KEY, JSON.stringify(f));
  } catch (e) {
    // 永続化不可(プライベートモード等)でも致命的ではない
  }
}

// 設定画面「銘柄リストのフィルタ」フォームの初期化。batchビュー表示のたびに
// 呼ばれ、保存値をフォームへ反映する。入力の変更で即保存し、リスト全ティアを
// 裏で再描画しておく(リストが非表示でも次に開いた時点で反映済み)。
function initListFilterSettings() {
  const form = document.getElementById("list-filter-form");
  if (!form) return;
  const segWrap = document.getElementById("lf-show-segments");

  const f = loadListFilters();
  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v == null ? "" : v;
  };
  setVal("lf-min-close", f.minClose);
  setVal("lf-max-close", f.maxClose);
  setVal("lf-min-rs", f.minRs);
  setVal("lf-min-score", f.minScore);
  setVal("lf-min-mcap", f.minMcap);
  setVal("lf-max-mcap", f.maxMcap);
  if (segWrap) {
    segWrap.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.checked = f.showSegments.includes(cb.value);
    });
  }
  updateListFilterStatus();

  if (form.dataset.wired) return;
  form.dataset.wired = "1";

  const readForm = () => {
    const num = (id) => {
      const el = document.getElementById(id);
      if (!el || el.value === "") return null;
      const n = Number(el.value);
      return isFinite(n) && n >= 0 ? n : null;
    };
    const segs = [];
    if (segWrap) {
      segWrap.querySelectorAll("input[type=checkbox]:checked").forEach((cb) => segs.push(cb.value));
    }
    return {
      minClose: num("lf-min-close"),
      maxClose: num("lf-max-close"),
      minRs: num("lf-min-rs"),
      minScore: num("lf-min-score"),
      minMcap: num("lf-min-mcap"),
      maxMcap: num("lf-max-mcap"),
      showSegments: segs,
    };
  };

  const persistAndRerender = () => {
    saveListFilters(readForm());
    updateListFilterStatus();
    // 全ティアを再描画(reportCacheがあれば)。リスト非表示中でも裏で更新。
    if (reportCache && reportCache.data) {
      rerenderTierBody("confirmed");
      rerenderTierBody("pool");
      rerenderTierBody("watchlist");
    }
  };

  form.addEventListener("change", persistAndRerender);
  form.addEventListener("input", (e) => {
    if (e.target && e.target.tagName === "INPUT" && e.target.type === "number") {
      persistAndRerender();
    }
  });

  const clearBtn = document.getElementById("lf-clear-btn");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      form.querySelectorAll("input[type=number]").forEach((el) => { el.value = ""; });
      if (segWrap) {
        // クリア=絞り込みなし=全市場表示なので全チェック。
        segWrap.querySelectorAll("input[type=checkbox]").forEach((cb) => { cb.checked = true; });
      }
      persistAndRerender();
    });
  }

  // 永続フィルタは入力の都度も反映されるが、ユーザーが「確定した」と分かるよう
  // 明示の適用ボタンも用意。押下時に保存+全ティア再描画し、一瞬フィードバックを出す。
  const applyBtn = document.getElementById("lf-apply-btn");
  if (applyBtn) {
    applyBtn.addEventListener("click", () => {
      persistAndRerender();
      const status = document.getElementById("lf-status");
      if (status) {
        const active = listFilterActive(loadListFilters());
        status.textContent = "✓ 適用しました";
        status.classList.toggle("lf-status-active", active);
        clearTimeout(applyBtn._t);
        applyBtn._t = setTimeout(updateListFilterStatus, 1500);
      }
    });
  }
}

function updateListFilterStatus() {
  const el = document.getElementById("lf-status");
  if (!el) return;
  const active = listFilterActive(loadListFilters());
  el.textContent = active ? "● フィルタ適用中" : "フィルタなし";
  el.classList.toggle("lf-status-active", active);
}

// リスト画面の一時フィルタ(alf-*)。initListView から毎回呼ばれる。
// 値はメモリ(adhocListFilter)にだけ持ち、リロードで消える=「その時用」。
// ビューを離れて戻っても保持されるよう、フォームは毎回 adhocListFilter から同期。
function initAdhocFilter() {
  const form = document.getElementById("adhoc-filter-form");
  if (!form) return;
  const segWrap = document.getElementById("alf-show-segments");

  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v == null ? "" : v;
  };
  setVal("alf-min-close", adhocListFilter.minClose);
  setVal("alf-max-close", adhocListFilter.maxClose);
  setVal("alf-min-rs", adhocListFilter.minRs);
  setVal("alf-min-score", adhocListFilter.minScore);
  setVal("alf-min-mcap", adhocListFilter.minMcap);
  setVal("alf-max-mcap", adhocListFilter.maxMcap);
  if (segWrap) {
    segWrap.querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.checked = adhocListFilter.showSegments.includes(cb.value);
    });
  }
  updateAdhocFilterBadge();

  if (form.dataset.wired) return;
  form.dataset.wired = "1";

  // 反映は「適用」ボタン(initListTools側で配線)だけ。入力の都度反映はしない。
  const clearBtn = document.getElementById("alf-clear-btn");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      // フォームの見た目だけリセット(絞り込みなし=全市場チェック)。
      // 実際の反映は「適用」を押したとき。
      form.querySelectorAll("input[type=number]").forEach((el) => { el.value = ""; });
      if (segWrap) {
        segWrap.querySelectorAll("input[type=checkbox]").forEach((cb) => { cb.checked = true; });
      }
    });
  }
}

// リスト画面の一時フィルタフォーム(alf-*)を読んで adhocListFilter に反映+再描画。
// 「適用」ボタン押下時に呼ぶ。
function applyAdhocFilterFromForm() {
  const form = document.getElementById("adhoc-filter-form");
  if (!form) return;
  const segWrap = document.getElementById("alf-show-segments");
  const num = (id) => {
    const el = document.getElementById(id);
    if (!el || el.value === "") return null;
    const n = Number(el.value);
    return isFinite(n) && n >= 0 ? n : null;
  };
  const segs = [];
  if (segWrap) {
    segWrap.querySelectorAll("input[type=checkbox]:checked").forEach((cb) => segs.push(cb.value));
  }
  adhocListFilter = {
    minClose: num("alf-min-close"),
    maxClose: num("alf-max-close"),
    minRs: num("alf-min-rs"),
    minScore: num("alf-min-score"),
    minMcap: num("alf-min-mcap"),
    maxMcap: num("alf-max-mcap"),
    showSegments: segs,
  };
  updateAdhocFilterBadge();
  if (reportCache && reportCache.data) {
    rerenderTierBody("confirmed");
    rerenderTierBody("pool");
    rerenderTierBody("watchlist");
  }
}

// フィルタバーのsummaryに出す「適用中」バッジ。有効な項目数を数字で出す。
function updateAdhocFilterBadge() {
  const badge = document.getElementById("adhoc-filter-badge");
  if (!badge) return;
  const f = adhocListFilter;
  let n = 0;
  ["minClose", "maxClose", "minRs", "minScore", "minMcap", "maxMcap"].forEach((k) => {
    if (f[k] != null) n++;
  });
  // 市場: 全表示なら絞り込みなし。チェックを外した数だけ絞り込みとして数える。
  const shown = f.showSegments ? f.showSegments.length : LIST_FILTER_SEGMENTS.length;
  n += Math.max(0, LIST_FILTER_SEGMENTS.length - shown);
  if (n > 0) {
    badge.textContent = n;
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

// 何かしらフィルタが有効か(件数表示やステータス文言の要否判定に使う)
function listFilterActive(f) {
  const shown = f.showSegments ? f.showSegments.length : LIST_FILTER_SEGMENTS.length;
  return (
    f.minClose != null || f.maxClose != null ||
    f.minRs != null || f.minScore != null || f.minMcap != null ||
    f.maxMcap != null || shown < LIST_FILTER_SEGMENTS.length
  );
}

function stockPassesListFilter(s, f) {
  if (f.minClose != null && s.close != null && s.close < f.minClose) return false;
  if (f.maxClose != null && s.close != null && s.close > f.maxClose) return false;
  if (f.minRs != null && s.rs != null && s.rs < f.minRs) return false;
  if (f.minScore != null && s.total_score != null && s.total_score < f.minScore) return false;
  if (f.minMcap != null && s.market_cap_oku != null && s.market_cap_oku < f.minMcap) return false;
  if (f.maxMcap != null && s.market_cap_oku != null && s.market_cap_oku > f.maxMcap) return false;
  // showSegments=表示する市場。全チェック(=全表示)のときは絞り込みなし。
  // 一部だけ表示のとき、市場区分が判明していてリストに無い銘柄を除外。
  const seg = f.showSegments;
  if (seg && seg.length < LIST_FILTER_SEGMENTS.length && s.market_segment != null &&
      !seg.includes(s.market_segment)) return false;
  return true;
}

// 与えられた銘柄配列にフィルタを適用し、通過分と除外件数を返す。
// 恒久フィルタ(設定画面/localStorage)と一時フィルタ(リスト画面/メモリ)の
// 両方を満たした銘柄だけ通す(AND合成)。
function applyListFilter(stocks) {
  const perm = loadListFilters();
  const adhoc = adhocListFilter;
  if (!listFilterActive(perm) && !listFilterActive(adhoc)) {
    return { kept: stocks, excluded: 0 };
  }
  const kept = stocks.filter(
    (s) => stockPassesListFilter(s, perm) && stockPassesListFilter(s, adhoc)
  );
  return { kept, excluded: stocks.length - kept.length };
}

// 除外件数の小さな告知バナー(除外>0のときだけ返す。設定画面へのリンク付き)。
function listFilterNoteEl(excluded) {
  if (!excluded) return null;
  const p = document.createElement("p");
  p.className = "tier-note list-filter-note";
  p.innerHTML =
    `<i class="bi bi-funnel-fill"></i> フィルタで${excluded}件を除外中` +
    ` <a href="#batch" class="list-filter-edit">設定</a>`;
  return p;
}

function renderTier(report, tier, containerId) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  const all = report.stocks.filter((s) => s.tier === tier);
  const { kept: stocks, excluded } = applyListFilter(all);
  const note = listFilterNoteEl(excluded);
  if (note) container.appendChild(note);
  if (!stocks.length) {
    const empty = document.createElement("p");
    empty.className = "tier-note";
    empty.textContent = excluded ? "フィルタ条件に合う銘柄なし" : "該当銘柄なし";
    container.appendChild(empty);
    return;
  }
  const sortKey = getCardSortKey(tier);
  for (const status of STATUS_ORDER) {
    const group = stocks.filter((s) => s.status === status);
    if (!group.length) continue;
    container.appendChild(renderStatusSection(status, group, tier, sortKey));
  }
}

function renderStatusSection(status, stocks, tier, sortKey) {
  const section = document.createElement("div");
  section.className = "status-section status-" + status;
  const h3 = document.createElement("h3");
  h3.textContent = `${STATUS_LABELS[status] || status} (${stocks.length})`;
  section.appendChild(h3);
  section.appendChild(renderCardList(stocks, tier, { initialSortKey: sortKey }));
  return section;
}

// ---------------------------------------------------------------------------
// リスト画面の並び替え/フィルタ(タブ横のツールボタン + ボトムシート)。
// 並び替えは「今開いているタブ(ティア)」のカードを対象に、スコア/RS/前日比で
// 実行。選択はティアごとlocalStorage(CARD_SORT_STORAGE_KEY)に保存するので、
// リロードや個別株画面からの復帰後も維持される。フィルタ(その時用)はメモリ保持
// (adhocListFilter)のためSPA内では維持、リロードで解除。
// ---------------------------------------------------------------------------

function rerenderTierBody(tier) {
  const report = reportCache && reportCache.data;
  if (!report) return;
  if (tier === "watchlist") {
    renderPriorityTier(report, "watchlist-tier-body");
  } else {
    renderTier(report, tier, `${tier}-tier-body`);
  }
}

// 今アクティブなリストのティア(タブ)。ソートシートはこのティアを対象にする。
function currentListTier() {
  const active = document.querySelector("#list-tabs .list-tab.active");
  return (active && active.dataset.panel) || "confirmed";
}

// 指定ティアの「リスト画面に表示されているのと同じ並び順」の銘柄コード配列を返す。
// フィルタ(恒久+一時)適用後、renderTier/renderPriorityTier と同じグループ順・
// ソートで並べる。個別株画面の前後ナビ(＜/＞)が辿る順序に使う。
// チャート未生成(has_chart===false)の銘柄は詳細ページに行けないので除外する。
function orderedTierCodes(tier) {
  const report = reportCache && reportCache.data;
  if (!report || !tier) return [];
  const sortKey = getCardSortKey(tier);
  const sortVal = CARD_SORTS[sortKey] || CARD_SORTS.total_score;
  const sortDesc = (arr) =>
    [...arr].sort((a, b) => {
      const av = sortVal(a);
      const bv = sortVal(b);
      return av === bv ? 0 : av > bv ? -1 : 1;
    });

  let ordered = [];
  if (tier === "watchlist") {
    const all = report.stocks.filter(
      (s) => s.tier === "watchlist" && (s.priority === 1 || s.priority == null)
    );
    const { kept } = applyListFilter(all);
    if (!kept.some((s) => s.setup_stage)) {
      ordered = sortDesc(kept);
    } else {
      const byGroup = new Map(SETUP_STAGE_GROUPS.map((g) => [g.key, []]));
      for (const s of kept) {
        byGroup.get(setupStageGroupKey(s) || "inactive").push(s);
      }
      for (const g of SETUP_STAGE_GROUPS) {
        ordered = ordered.concat(sortDesc(byGroup.get(g.key)));
      }
    }
  } else {
    const all = report.stocks.filter((s) => s.tier === tier);
    const { kept } = applyListFilter(all);
    for (const status of STATUS_ORDER) {
      ordered = ordered.concat(sortDesc(kept.filter((s) => s.status === status)));
    }
  }
  return ordered.filter((s) => s.has_chart !== false).map((s) => s.code);
}

// 個別株画面の前後ナビ(＜=次へ / ＞=前へ)。listNavTier のフィルタ済み並び順で
// 現在銘柄の前後を割り出し、ボタンの遷移先(dataset.target)と有効/無効を更新。
function updateStockNav(code) {
  const leftBtn = document.getElementById("stock-nav-next");  // ＜ = リストの上へ(index-1)
  const rightBtn = document.getElementById("stock-nav-prev"); // ＞ = リストの下へ(index+1)
  if (!leftBtn || !rightBtn) return;
  const codes = orderedTierCodes(listNavTier);
  const idx = codes.indexOf(code);
  const upCode = idx > 0 ? codes[idx - 1] : null;
  const downCode = idx >= 0 && idx < codes.length - 1 ? codes[idx + 1] : null;
  // ＜=リストの上へ / ＞=リストの下へ(体感に合わせて左右反転)。参照先が無い側は
  // 右詰めにならないよう、スペースを保ったまま不可視化(is-empty)する。
  const set = (btn, target) => {
    btn.hidden = false;
    btn.dataset.target = target || "";
    btn.classList.toggle("is-empty", !target);
  };
  set(leftBtn, upCode);
  set(rightBtn, downCode);
}

// 前後ナビボタンのクリック配線(初回のみ)。dataset.target へハッシュ遷移する。
function initStockNav() {
  const wire = (id) => {
    const btn = document.getElementById(id);
    if (!btn || btn.dataset.wired) return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", () => {
      const target = btn.dataset.target;
      if (target) window.location.hash = `stock/${encodeURIComponent(target)}`;
    });
  };
  wire("stock-nav-next");
  wire("stock-nav-prev");
}

// ソートボタンのラベルを、今のティアの並び替えキーに合わせて更新。
function updateListSortLabel() {
  const el = document.getElementById("list-sort-label");
  if (!el) return;
  el.textContent = CARD_SORT_LABELS[getCardSortKey(currentListTier())] || "スコア";
}

// ソートシートの選択状態を、今のティアの並び替えキーに同期。
function syncSortSheet() {
  const opts = document.getElementById("list-sort-options");
  if (!opts) return;
  const key = getCardSortKey(currentListTier());
  opts.querySelectorAll("button[data-sort]").forEach((b) => {
    b.classList.toggle("active", b.dataset.sort === key);
  });
}

// タブ横のフィルタ/並び替えボタン + ボトムシートの配線。initListView から毎回
// 呼ばれるが、イベント登録は初回のみ(dataset.wired)。ラベル/バッジ同期は毎回。
function initListTools() {
  const filterBtn = document.getElementById("list-filter-btn");
  const sortBtn = document.getElementById("list-sort-btn");
  const backdrop = document.getElementById("list-sheet-backdrop");
  const filterSheet = document.getElementById("filter-sheet");
  const sortSheet = document.getElementById("sort-sheet");
  if (!filterBtn || !sortBtn || !backdrop) return;

  const closeSheets = () => {
    backdrop.hidden = true;
    if (filterSheet) { filterSheet.classList.remove("open"); filterSheet.hidden = true; }
    if (sortSheet) { sortSheet.classList.remove("open"); sortSheet.hidden = true; }
  };
  const openSheet = (sheet) => {
    if (!sheet) return;
    sheet.hidden = false;
    backdrop.hidden = false;
    // 表示直後にopenを付けてスライドイン(transitionを効かせる)。
    requestAnimationFrame(() => sheet.classList.add("open"));
  };

  // 毎回: ラベル/選択状態を最新へ同期。
  updateListSortLabel();

  if (filterBtn.dataset.wired) return;
  filterBtn.dataset.wired = "1";

  filterBtn.addEventListener("click", () => openSheet(filterSheet));
  sortBtn.addEventListener("click", () => { syncSortSheet(); openSheet(sortSheet); });
  backdrop.addEventListener("click", closeSheets);
  document.querySelectorAll("#view-stocklist .sheet-close").forEach((b) => {
    b.addEventListener("click", closeSheets);
  });
  // 「適用」ボタン: フォームの内容を一時フィルタへ反映してシートを閉じる。
  const applyBtn = document.getElementById("alf-apply-btn");
  if (applyBtn) applyBtn.addEventListener("click", () => { applyAdhocFilterFromForm(); closeSheets(); });

  const opts = document.getElementById("list-sort-options");
  if (opts) {
    const applySort = (btn) => {
      if (!btn || !CARD_SORTS[btn.dataset.sort]) return;
      const tier = currentListTier();
      setCardSortKey(tier, btn.dataset.sort);
      syncSortSheet();
      updateListSortLabel();
      rerenderTierBody(tier);
      closeSheets();
    };
    opts.addEventListener("click", (e) => applySort(e.target.closest("button[data-sort]")));
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSheets();
  });
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
      const go = () => {
        listNavTier = tier; // 前後ナビはこのティアのフィルタ済み並び順を辿る
        window.location.hash = `stock/${encodeURIComponent(s.code)}`;
      };
      // キーボード操作対応: divのままフォーカス可能+Enter/Spaceで遷移。
      card.setAttribute("role", "button");
      card.tabIndex = 0;
      card.addEventListener("click", go);
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go();
        }
      });
    } else {
      card.classList.add("row-static");
    }
    // 上段: 「銘柄名（コード）」+ SC/RS(チップ化して視覚的に強弱を出す)
    // 下段: 終値（±前日比%。前日比は太字で強調） + セクター(強度) + 枯れ度 + 信用
    //   (下段はflex-wrapで、狭い画面でもバッジが潰れず自然に折り返す)
    // 監視タブ(stageBadgeオプション時)のみ3段目: セットアップ進行度・不足理由
    const stageLine =
      options.stageBadge && s.setup_stage
        ? `<div class="sc-row sc-row-stage${s.setup_stage.near ? " sc-stage-near" : ""}">${escapeHtml(setupStageBadgeText(s))}</div>`
        : "";
    card.innerHTML = `
      <div class="sc-row">
        <span class="sc-name">${escapeHtml(s.name ?? "-")}<span class="sc-code">（${escapeHtml(s.code)}）</span></span>
        <span class="sc-metrics">
          <span class="sc-score-chip">SC ${s.total_score ?? "-"}</span>
          <span class="sc-rs-chip">RS ${s.rs ?? "-"}</span>
        </span>
      </div>
      <div class="sc-row sc-row-sub">
        <span class="sc-close">${formatClose(s.close)}${changePctHtml(s)}</span>
        <span class="sc-sector">${sectorStrengthHtml(s)}</span>
        <span class="sc-dryup">${dryupBadgeHtml(s)}</span>
        <span class="sc-margin">${marginBadgeHtml(s.margin)}</span>
      </div>${stageLine}`;
    list.appendChild(card);
  }
  return list;
}

function formatClose(v) {
  return v == null ? "-" : Number(v).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
}

// ---------------------------------------------------------------------------
// 〔監視〕8条件合格・セットアップ形成待ち(旧P1)一覧。P2〜P4はUI廃止(データは
// report.jsonに残るがダッシュボードには出さない)。
// 2026-07-17: 100件超を毎日全部見るのは不可能なので、report.jsonの
// setup_stage(バックエンドbuild_setup_stageが付与)でセットアップ進行度別に
// グループ表示する。毎日見るべき「あと一歩」を最上段に、実質見送りの
// ボラ過大/ベース無しは折りたたみに隔離する。setup_stage未付与の旧データは
// 従来どおりの単一リスト(RS降順)へフォールバックする。
// ---------------------------------------------------------------------------

// 表示順: あと一歩(near=true横断) -> ベース形成中 -> 高値直後 -> VCP未達 -> 対象外
const SETUP_STAGE_GROUPS = [
  { key: "near", label: "🔥 あと一歩", desc: "ベース熟成間近 or VCP残り1条件", collapsed: false },
  { key: "forming", label: "ベース形成中", desc: "最短日数まで熟成待ち", collapsed: false },
  { key: "fresh_high", label: "高値更新直後", desc: "押し・ベース開始待ち", collapsed: false },
  { key: "rejected", label: "VCP条件未達", desc: "ベースはあるが2条件以上不足", collapsed: true },
  { key: "inactive", label: "対象外 (ボラ過大/ベース無し)", desc: "実質見送り", collapsed: true },
];

function setupStageGroupKey(s) {
  const st = s.setup_stage;
  if (!st) return null;
  if (st.near) return "near";
  if (st.stage === "volatile" || st.stage === "no_base") return "inactive";
  if (st.stage === "forming" || st.stage === "fresh_high" || st.stage === "rejected") return st.stage;
  return "inactive";
}

// カード下段に出す進行度バッジ文言。REJECTEDはVコード -> 日本語ラベルに展開。
function setupStageBadgeText(s) {
  const st = s.setup_stage;
  if (!st) return "";
  if (st.stage === "rejected" && st.missing && st.missing.length) {
    const labels = st.missing.map((m) => MUST_FLAG_LABELS.vcp[m] || m);
    return `未達: ${labels.join(" / ")}`;
  }
  return st.detail || "";
}

function renderPriorityTier(report, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";
  // 8条件合格(旧P1)のみ。priority未設定の旧データも合格扱いで拾う。
  const all = report.stocks
    .filter((s) => s.tier === "watchlist" && (s.priority === 1 || s.priority == null));
  const { kept: stocks, excluded } = applyListFilter(all);
  const filterNote = listFilterNoteEl(excluded);
  if (filterNote) container.appendChild(filterNote);
  if (!stocks.length) {
    const empty = document.createElement("p");
    empty.className = "tier-note";
    empty.textContent = excluded ? "フィルタ条件に合う銘柄なし" : "該当銘柄なし";
    container.appendChild(empty);
    return;
  }
  const sortKey = getCardSortKey("watchlist");

  // 旧report.json(setup_stage無し)へのフォールバック: 従来の単一リスト。
  if (!stocks.some((s) => s.setup_stage)) {
    const note = document.createElement("p");
    note.className = "tier-note";
    note.textContent = `全${stocks.length}件`;
    container.appendChild(note);
    container.appendChild(renderCardList(stocks, "watchlist", { initialSortKey: sortKey }));
    return;
  }

  const byGroup = new Map(SETUP_STAGE_GROUPS.map((g) => [g.key, []]));
  for (const s of stocks) {
    const key = setupStageGroupKey(s) || "inactive";
    byGroup.get(key).push(s);
  }

  for (const g of SETUP_STAGE_GROUPS) {
    const group = byGroup.get(g.key);
    if (!group.length) continue;
    container.appendChild(renderStageSection(g, group, sortKey));
  }
}

function renderStageSection(groupDef, stocks, sortKey) {
  const details = document.createElement("details");
  details.className = "stage-section stage-" + groupDef.key;
  details.open = !groupDef.collapsed;
  const summary = document.createElement("summary");
  summary.innerHTML =
    `<span class="stage-label">${groupDef.label} (${stocks.length})</span>` +
    `<span class="stage-desc">${groupDef.desc}</span>`;
  details.appendChild(summary);
  details.appendChild(
    renderCardList(stocks, "watchlist", { initialSortKey: sortKey, stageBadge: true })
  );
  return details;
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
  const marginEl = document.getElementById("margin-detail-body");
  const copyBtn = document.getElementById("copy-stock-data-btn");
  const sizingResultEl = document.getElementById("sizing-result");
  if (metaEl) metaEl.innerHTML = "";
  if (mustEl) mustEl.innerHTML = "";
  if (scoreEl) scoreEl.innerHTML = "";
  if (fundEl) fundEl.innerHTML = "";
  if (marginEl) marginEl.innerHTML = "";
  if (sizingResultEl) sizingResultEl.innerHTML = "";
  if (copyBtn) copyBtn.hidden = true;

  if (!code) {
    if (titleEl) titleEl.textContent = "銘柄コードが指定されていません";
    return;
  }
  if (titleEl) titleEl.textContent = "読み込み中...";

  // report.jsonはダッシュボード表示時のキャッシュ(reportCache)を再利用し、
  // 直リンク等でキャッシュが無い時だけfetch+復号して格納する。
  const reportPromise = reportCache
    ? Promise.resolve(reportCache.data)
    : window.MinerviniData.fetchJson("data/report.json").then((r) => {
        reportCache = { data: r, fetchedAt: Date.now() };
        return r;
      });
  const [report, chart] = await Promise.all([
    reportPromise,
    window.MinerviniData.fetchJson(`data/charts/${encodeURIComponent(code)}.json`, { optional: true }),
  ]);
  const stock = report.stocks.find((s) => s.code === code);

  if (titleEl) titleEl.textContent = `${code} ${stock ? stock.name : ""}`;
  if (stock) renderStockMeta(stock);
  setupYahooFinanceLink(code);
  initStockNav();
  updateStockNav(code);
  setupStockPanels();
  renderStockSummary(stock);
  if (stock) renderStockFundamentals(code, stock.name, report.generated_at);
  if (stock) renderStockMargin(stock);
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

  const goToTab = (btn) => {
    const items = Array.from(tabs.querySelectorAll(".stock-tab"));
    const idx = items.indexOf(btn);
    if (idx < 0) return;
    panels.scrollTo({ left: idx * panels.clientWidth, behavior: "smooth" });
    updateStockActiveTab(btn.dataset.panel);
  };

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".stock-tab");
    if (!btn) return;
    goToTab(btn);
  });
  wireTabSlide(tabs, ".stock-tab", goToTab);

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

// ピル型タブバー共通の「スライド切替」。タブボタンを押したまま横に滑らせると
// 指の下のタブが .slide-target でハイライトされ、そこで離すとそのタブへ切り替わる
// (例: 市況タブを押したまま右へスライド→市況分析の上で離す)。押した場所と同じ
// タブで離した/バー外で離した場合は何もしない(タップは従来のclickで処理)。
// スライド成立後に発火する合成clickはキャプチャ段階で握りつぶす(initDockSwipeと
// 同じ手法)。activate(btn) には各タブ実装の setActive 相当を渡す。
function wireTabSlide(tabs, tabSelector, activate) {
  if (tabs.dataset.slideWired) return;
  tabs.dataset.slideWired = "1";

  let pointerId = null;
  let startBtn = null;
  let previewBtn = null;
  let slid = false;

  const btnAt = (x, y) => {
    const el = document.elementFromPoint(x, y);
    const btn = el && el.closest ? el.closest(tabSelector) : null;
    return btn && tabs.contains(btn) ? btn : null;
  };
  const setPreview = (btn) => {
    if (previewBtn === btn) return;
    if (previewBtn) previewBtn.classList.remove("slide-target");
    previewBtn = btn;
    if (previewBtn) previewBtn.classList.add("slide-target");
  };

  tabs.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest(tabSelector);
    if (!btn || !tabs.contains(btn)) return;
    pointerId = e.pointerId;
    startBtn = btn;
    slid = false;
    // バー外に指が出てもpointermove/upを追い続けるためのキャプチャ。
    try { tabs.setPointerCapture(e.pointerId); } catch (_) { /* 古いブラウザは追跡なしで動作 */ }
  });

  tabs.addEventListener("pointermove", (e) => {
    if (pointerId === null || e.pointerId !== pointerId) return;
    const btn = btnAt(e.clientX, e.clientY);
    if (!btn) return; // バーから縦に外れた間は直前のハイライトを維持(指ブレ対策)
    if (btn !== startBtn) slid = true;
    setPreview(btn === startBtn ? null : btn); // 開始タブへ戻ったらキャンセル扱い
  });

  const finish = (e, commit) => {
    if (pointerId === null || e.pointerId !== pointerId) return;
    const target = commit && slid ? (btnAt(e.clientX, e.clientY) || previewBtn) : null;
    setPreview(null);
    pointerId = null;
    startBtn = null;
    if (target) activate(target);
    // slid は直後の合成click握りつぶし用に残す(下のcaptureリスナーが消費)。
  };
  tabs.addEventListener("pointerup", (e) => finish(e, true));
  tabs.addEventListener("pointercancel", (e) => finish(e, false));

  tabs.addEventListener(
    "click",
    (e) => {
      if (slid) {
        e.stopPropagation();
        e.preventDefault();
        slid = false;
      }
    },
    true
  );
}

// リスト画面(本命/候補/監視)はタブでのみ切替。横スワイプでの画面切替は廃止し、
// 各パネル内の横スクロールは表の列閲覧専用にした(スワイプでパネルが動かない)。
function initListView() {
  const panels = document.getElementById("list-panels");
  const tabs = document.getElementById("list-tabs");
  if (!panels || !tabs) return;

  initAdhocFilter();
  initListTools();

  const setActive = (name) => {
    tabs.querySelectorAll(".list-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.panel === name);
    });
    panels.querySelectorAll(".list-panel").forEach((p) => {
      const on = p.dataset.panel === name;
      p.classList.toggle("active", on);
      if (on) p.scrollTop = 0; // 切替のたびに先頭へ
    });
    // 並び替えはティアごとに保存するため、タブ切替でボタンのラベルも更新。
    updateListSortLabel();
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
  wireTabSlide(tabs, ".list-tab", (btn) => setActive(btn.dataset.panel));
}

// 設定画面のサブタブ切替(バッチ実行/履歴・フィルター設定・投資法)。
// batchビュー表示のたびに呼ばれる。イベント登録は初回のみ(dataset.wired)。
function initSettingsSubtabs() {
  const tabs = document.getElementById("settings-subtabs");
  if (!tabs) return;
  const panels = document.querySelectorAll("#view-batch .settings-subpanel");

  const setActive = (name) => {
    tabs.querySelectorAll(".settings-subtab").forEach((b) => {
      b.classList.toggle("active", b.dataset.subtab === name);
    });
    panels.forEach((p) => p.classList.toggle("active", p.dataset.subpanel === name));
  };

  const initial = tabs.querySelector(".settings-subtab.active") || tabs.querySelector(".settings-subtab");
  setActive(initial ? initial.dataset.subtab : "batch");

  if (tabs.dataset.wired) return;
  tabs.dataset.wired = "1";
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".settings-subtab");
    if (!btn) return;
    setActive(btn.dataset.subtab);
  });
}

// 市況画面(市況データ/市況分析)のタブ切替。initListView と同じパターン
// (パネルは横スワイプせず、タブクリックのみで切替。初期表示は既にactiveな
// タブを尊重)。initDashboard はファンダ保存後などに再実行されうるため、
// dataset.wired でイベント登録の重複を防ぐ。
function initMarketTabs() {
  const panels = document.getElementById("market-panels");
  const tabs = document.getElementById("market-tabs");
  if (!panels || !tabs) return;

  const setActive = (name) => {
    tabs.querySelectorAll(".market-tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.panel === name);
    });
    panels.querySelectorAll(".market-panel").forEach((p) => {
      const on = p.dataset.panel === name;
      p.classList.toggle("active", on);
      if (on) p.scrollTop = 0;
    });
  };

  const initial = (tabs.querySelector(".market-tab.active") || tabs.querySelector(".market-tab"));
  setActive(initial ? initial.dataset.panel : "data");

  if (panels.dataset.wired) return;
  panels.dataset.wired = "1";

  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".market-tab");
    if (!btn) return;
    setActive(btn.dataset.panel);
  });
  wireTabSlide(tabs, ".market-tab", (btn) => setActive(btn.dataset.panel));
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

// 需給(信用取引週末残高)カード。表示専用(総合スコアには一切使わない)。
// data/margin_weekly.json はJPXが週1回更新のため最大5営業日遅れる旨を注記する。
function marginNum(v, digits = 1) {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return "-";
  return n.toLocaleString("ja-JP", { maximumFractionDigits: digits });
}

function formatMarginDate(dateStr) {
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function renderStockMargin(stock) {
  const container = document.getElementById("margin-detail-body");
  if (!container) return;
  const m = stock && stock.margin;
  if (!m) {
    container.innerHTML = '<p class="tier-note">信用残データなし</p>';
    return;
  }

  const ratioText = m.ratio != null ? `${marginNum(m.ratio, 2)}倍` : "売残なし";
  const wowHtml =
    m.buy_wow_pct != null
      ? `<span class="sc-chg ${m.buy_wow_pct > 0 ? "chg-pos" : m.buy_wow_pct < 0 ? "chg-neg" : "chg-flat"}">（${m.buy_wow_pct > 0 ? "+" : ""}${marginNum(m.buy_wow_pct, 1)}%）</span>`
      : "";
  const dtcText = m.days_to_cover != null ? `平均出来高の${marginNum(m.days_to_cover, 1)}日分` : "-";
  const dateText = m.date ? formatMarginDate(m.date) : "-";

  container.innerHTML = `
    <div class="margin-detail">
      <div class="margin-ratio-big">${ratioText}${helpBtnHtml("margin_ratio")} ${marginBadgeHtml(m, { detail: true })}</div>
      <div class="margin-row"><span>買残${helpBtnHtml("margin_buy")}</span><span>${marginNum(m.buy, 0)}株${wowHtml}</span></div>
      <div class="margin-row"><span>売残${helpBtnHtml("margin_sell")}</span><span>${marginNum(m.sell, 0)}株</span></div>
      <div class="margin-row"><span>買残回転日数${helpBtnHtml("days_to_cover")}</span><span>${dtcText}</span></div>
      <p class="tier-note">${dateText}申込時点(週次データのため最大5営業日遅れることがあります)</p>
    </div>
  `;
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

// Formats every axis-level date as zero-padded "MM/DD": the auto tick marks
// (tickMarkFormatter), the crosshair hover label (timeFormatter), and the
// always-visible latest-date overlay (addLatestDateLabel) all go through the
// same format so 目盛/ホバー/最新日 の3つの日付表示が完全に一致する。
// Chart data uses "YYYY-MM-DD" strings, which Lightweight Charts parses into
// a {year, month, day} BusinessDay object.
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
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${mm}/${dd}`;
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
    // fontSize 11 はCSS側 .latest-date-label の font-size と揃えるための明示指定
    // (自動目盛と自前の最新日ラベルの文字サイズ・色を一致させる)。
    layout: { background: { color: CHART_COLORS.bg }, textColor: CHART_COLORS.text, fontSize: 11 },
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

// ---- 自前の日付軸 (#chart-date-axis) -----------------------------------
// ライブラリの時間軸(visible:false)の代わりに、目盛・ホバー・最新日の全日付を
// 1本のstripにDOMで描画する。縦位置はCSS(.chart-date-axis .cda-label の top)で
// 完全に制御できるため、ライブラリ内部の固定オフセットに縛られず微調整できる。
const DATE_AXIS_MIN_GAP = 46; // 静的ラベル同士の最小間隔(px)

// 静的な目盛ラベル群を再描画。最新バーを起点に右→左へ、最小間隔を空けて配置する
// (fixRightEdgeで最新が右端に固定されるため、この並びが自然)。
function renderDateAxis(axisEl, chart, candles, formatFn) {
  if (!axisEl || !chart) return;
  axisEl.querySelectorAll(".cda-label:not(.cda-hover)").forEach((n) => n.remove());
  const width = axisEl.clientWidth;
  if (!candles || !candles.length || width <= 0) return;
  const ts = chart.timeScale();
  const placed = [];
  let lastX = Infinity;
  for (let i = candles.length - 1; i >= 0; i--) {
    const t = candles[i].time;
    const x = ts.timeToCoordinate(t);
    if (x == null || x < 0 || x > width) continue;
    if (lastX - x < DATE_AXIS_MIN_GAP) continue;
    placed.push({ x, t });
    lastX = x;
  }
  for (const { x, t } of placed) {
    const el = document.createElement("span");
    el.className = "cda-label";
    el.textContent = formatFn(t);
    el.style.left = `${x}px`;
    axisEl.appendChild(el);
  }
}

// ホバー中の日付を強調ラベルとしてstrip上に表示(ライブラリのクロスヘア日付の代替)。
// time が null のときは消す。静的ラベルより前面・太字で、重なっても上に乗る。
function setDateAxisHover(axisEl, chart, time, formatFn) {
  if (!axisEl || !chart) return;
  let hover = axisEl.querySelector(".cda-hover");
  const x = time != null ? chart.timeScale().timeToCoordinate(time) : null;
  if (x == null) {
    if (hover) hover.remove();
    return;
  }
  if (!hover) {
    hover = document.createElement("span");
    hover.className = "cda-label cda-hover";
    axisEl.appendChild(hover);
  }
  hover.textContent = formatFn(time);
  hover.style.left = `${x}px`;
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
  const { charts, resizeHandler, dateAxisEl } = stockChartState;
  window.removeEventListener("resize", resizeHandler);
  for (const c of charts) c.remove();
  if (dateAxisEl) dateAxisEl.innerHTML = "";
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

  // 日付軸は最下段のペイン(RSがあればRS、無ければ出来高)だけに表示する。
  // 3ペイン全部に出すと同じ日付が重複するため。パン/ズームはtimeScale.visible
  // と独立してsyncTimeScales()で同期されるので、表示を1つに絞っても連動は保たれる。
  // ライブラリの時間軸は全ペインで非表示にし、日付は下部の自前strip
  // (#chart-date-axis)にDOMで描く。ライブラリの軸テキストは strip 上端からの
  // 固定オフセットで縦位置が決まり動かせないため、縦位置を自前で制御できるよう
  // にした(目盛/ホバー/最新日をすべて renderDateAxis / setDateAxisHover が描画)。
  const priceChart = makeChart(priceEl, { showTimeAxis: false });
  const volChart = makeChart(volEl, { showTimeAxis: false });
  const rsChart = hasRs ? makeChart(rsEl, { showTimeAxis: false }) : null;

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

  // 自前日付軸の描画先と、その基準となる最下段チャート(x座標の変換元)。
  const dateAxisEl = document.getElementById("chart-date-axis");
  const bottomChart = rsChart || volChart;

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

  // bar.time は常にゼロ埋め済みの "YYYY-MM-DD" 文字列(日足・月足集計後とも
  // 同形式)なので、区切りを「/」に変えるだけで桁揺れなしの表示になる。
  // 常時表示の最新日付ラベル(addLatestDateLabel)と区切り文字を揃えることで
  // ホバー時/非ホバー時/チャート右下の最新日表示すべてを同じデザインにする。
  function formatLegendDate(dateStr) {
    return dateStr ? dateStr.replaceAll("-", "/") : "-";
  }

  function updateLegend(bar) {
    if (!legendEl) return;
    if (!bar) {
      legendEl.innerHTML = "";
      return;
    }
    const dirClass = bar.close >= bar.open ? "chg-up" : "chg-down";
    legendEl.innerHTML = `
      <span class="lg-date">${formatLegendDate(bar.time)}</span>
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
      setDateAxisHover(dateAxisEl, bottomChart, param.time, formatChartDate);
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

  // 自前日付軸: 現在の期間(日足/月足)に応じた目盛ラベルを描画し、パン/ズーム・
  // 期間変更(setVisibleLogicalRange/fitContent)で発火する範囲変更イベントに
  // 合わせて再描画する。formatChartDate は目盛/ホバー/最新日で共通(MM/DD)。
  const drawAxis = () =>
    renderDateAxis(dateAxisEl, bottomChart, currentTf === "M" ? monthly.candles : chart.candles, formatChartDate);
  if (bottomChart) {
    bottomChart.timeScale().subscribeVisibleLogicalRangeChange(drawAxis);
    requestAnimationFrame(drawAxis);
  }

  const toggleOld = document.getElementById("timeframe-toggle");
  if (toggleOld) {
    // 同上の理由でcloneして前回分のclickリスナーを捨てる。あわせて
    // 表示状態(1ヶ月ボタンがactive)をデフォルトにリセットしておく。
    const toggle = toggleOld.cloneNode(true);
    toggleOld.replaceWith(toggle);
    toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tf === String(DEFAULT_DAILY_BARS)));
    const applyTf = (btn) => {
      toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      setTimeframe(btn.dataset.tf);
    };
    toggle.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tf]");
      if (!btn) return;
      applyTf(btn);
    });
    // cloneNodeでdata-slide-wired属性ごと複製されるとwireTabSlideが再配線を
    // スキップしてしまう(リスナーはcloneされないので死ぬ)ため、必ず消してから配線。
    delete toggle.dataset.slideWired;
    wireTabSlide(toggle, "button[data-tf]", applyTf);
  }

  const resizeHandler = () => {
    for (const [c, el] of [[priceChart, priceEl], [volChart, volEl], ...(rsChart ? [[rsChart, rsEl]] : [])]) {
      c.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    }
    drawAxis();
  };
  window.addEventListener("resize", resizeHandler);

  stockChartState = { charts, resizeHandler, dateAxisEl };
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
    { label: "テクニカルスコア", value: stock.tech_score, helpKey: "tech_score" },
    { label: "フルスコア", value: stock.full_score, helpKey: "full_score" },
    { label: "VCPスコア", value: stock.vcp_score, helpKey: "vcp_score" },
    { label: "総合スコア", value: stock.total_score, helpKey: "total_score" },
  ];
  for (const item of items) {
    if (item.value == null) continue;
    const row = document.createElement("div");
    row.className = "score-bar-row";
    const pct = Math.max(0, Math.min(100, item.value));
    row.innerHTML = `<span>${item.label}${helpBtnHtml(item.helpKey)}</span><div class="score-bar"><div class="score-bar-fill" style="width:${pct}%"></div></div><span>${item.value}</span>`;
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
      // data_missing行は詳細ページが無いのでクリック/フォーカス不可(row-static)。
      const rowAttrs = p.data_missing ? 'class="row-static"' : 'tabindex="0" role="button"';
      return `
        <tr ${rowAttrs} data-code="${escapeHtml(p.code)}">
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
            <th>R${helpBtnHtml("r_multiple")}</th><th>ストップ</th><th>ストップまで%</th><th>保有日数</th><th>シグナル${helpBtnHtml("breakeven_sl")}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  container.querySelectorAll("tr[data-code]:not(.row-static)").forEach((tr) => {
    const go = () => {
      window.location.hash = "stock/" + tr.dataset.code;
    };
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        go();
      }
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

// 投資法ページ(view-invest)の一覧→詳細ナビ。
// #invest        = 一覧(invest-menu)を表示。
// #invest/<id>   = 対応する data-section の詳細1枚だけ表示(戻るリンクで #invest に戻る)。
// ハッシュ遷移で駆動するため、ブラウザ/端末の「戻る」でも一覧に戻れる。
function initInvestView(param) {
  const menu = document.getElementById("invest-menu");
  const detail = document.getElementById("invest-detail");
  if (!menu || !detail) return;
  const sections = detail.querySelectorAll(".invest-section");
  let target = null;
  sections.forEach((s) => {
    const on = !!param && s.dataset.section === param;
    s.hidden = !on;
    if (on) target = param;
  });
  const showDetail = target !== null;
  menu.hidden = showDetail;
  detail.hidden = !showDetail;
  // 前回のスクロール位置を引き継がないようリセット。
  const body = document.getElementById("invest-detail-body");
  if (body) body.scrollTop = 0;
  (showDetail ? detail : menu).scrollTop = 0;
}

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
    const active = btn.dataset.view === name;
    btn.classList.toggle("active", active);
    // スクリーンリーダー向けに現在地を通知(見た目の.activeと常に同期)。
    if (active) {
      btn.setAttribute("aria-current", "page");
    } else {
      btn.removeAttribute("aria-current");
    }
  });
  // ヒートマップは独立ページ。コンテナが見えて初めてclientWidthが
  // 正しく取れるため、ヒートマップ表示のたびに再init。
  if (name === "heatmap" && typeof initHeatmap === "function") {
    initHeatmap();
  }
  if (name === "batch" && window.MinerviniBatch) {
    window.MinerviniBatch.initBatchView();
  }
  if (name === "batch") {
    initSettingsSubtabs();
    initListFilterSettings();
  }
  if (name === "invest") {
    initInvestView(param);
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

// 旧initDockSwipe(スワイプ方向→隣のビューへ移動)は廃止。方向とビュー順の
// 対応が直感と逆に感じられる問題があったため、タブと同じ wireTabSlide
// (押したまま滑らせて指の下のボタンで離す=そのビューへ移動)に統一した。
// 離した場所がそのまま行き先なので方向の解釈違いが起きない。

function initRouter() {
  const dock = document.getElementById("dock-nav");
  if (!dock) return;
  wireTabSlide(dock, ".dock-btn", (btn) => {
    window.location.hash = btn.dataset.view;
  });
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
