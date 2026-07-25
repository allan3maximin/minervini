/**
 * 銘柄切替レースの回帰テスト (依存なし・node だけで動く)。
 *
 *     node tests/js/test_stock_page_race.js [app.js のパス]
 *
 * initStockPage() は showView() から fire-and-forget で呼ばれるので、連続で銘柄を
 * 送ると複数回分が同時に走る。チャートJSONは先読み済みなら即時・未取得なら
 * ネットワーク待ちで、解決順が呼び出し順と入れ替わる。teardownCharts() は await の
 * 前に済んでいるため、遅れて解決した古い実行が renderCharts() すると新しい銘柄の
 * チャートの上に重ねて作られ、「銘柄を切り替えてもグラフが変わらない」ように見える。
 * initStockPage の世代番号 (stockPageSeq) が古い実行を降ろすことを確認する。
 *
 * 実物の docs/assets/app.js を vm で読み込み、DOM/データ層は最小限のスタブを当てて
 * renderCharts が「どの銘柄で何回」呼ばれたかだけを見る。
 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const SRC = process.argv[2] || path.join(__dirname, "..", "..", "docs", "assets", "app.js");
const source = fs.readFileSync(SRC, "utf8");

function makeEl(id) {
  return {
    id,
    textContent: "",
    innerHTML: "",
    hidden: false,
    href: "",
    dataset: {},
    style: {},
    clientWidth: 800,
    clientHeight: 300,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    appendChild() {},
    remove() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 300 }),
  };
}

// 個別株ビューが触る id だけ実体を返し、それ以外は null (呼び出し側は null 許容)。
const KNOWN_IDS = [
  "stock-title", "chart-container", "volume-container", "rs-container",
  "fund-detail-body", "margin-detail-body", "stock-meta", "must-checklist",
  "score-breakdown", "sizing-result", "stock-panels", "chart-date-axis",
];
const els = new Map();
const document = {
  body: makeEl("body"),
  documentElement: makeEl("html"),
  activeElement: null,
  addEventListener() {},
  removeEventListener() {},
  getElementById(id) {
    if (!KNOWN_IDS.includes(id)) return null;
    if (!els.has(id)) els.set(id, makeEl(id));
    return els.get(id);
  },
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (t) => makeEl(t),
};

const window = {
  MINERVINI_CONFIG: { passkeyAuthEnabled: false },
  location: { hash: "", search: "" },
  devicePixelRatio: 1,
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
};

// チャートJSONの解決タイミングを外から握るための fetch スタブ。
const pending = new Map(); // url -> resolve
window.MinerviniData = {
  hasDataKey: () => true,
  isEnvelope: () => false,
  setDataKey() {},
  fetchJson(url) {
    if (url === "data/report.json") {
      return Promise.resolve({
        generated_at: "2026-07-25",
        stocks: [
          { code: "1111", name: "AAA", must_flags: {}, vcp_detail: {} },
          { code: "2222", name: "BBB", must_flags: {}, vcp_detail: {} },
        ],
      });
    }
    if (url.startsWith("data/charts/")) return new Promise((res) => pending.set(url, res));
    return Promise.resolve(null);
  },
};

const ctx = {
  window, document, console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Promise, Map, Set, Date, Math, JSON, URLSearchParams, Intl,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  LightweightCharts: null, // makeChart までは到達させない(renderCharts をスパイに差し替える)
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(source, ctx, { filename: path.basename(SRC) });

// 描画系は「誰がいつ描いたか」だけ見たいので全部スパイ/no-op に差し替える。
// 関数宣言は vm のグローバルに乗るので、initStockPage 内の参照ごと差し替わる。
const rendered = [];
ctx.renderCharts = (chart) => rendered.push(chart.code);
for (const fn of ["teardownCharts", "teardownMarginChart", "initStockNav", "updateStockNav",
                  "prefetchAdjacentCharts", "setupStockPanels", "renderStockSummary",
                  "renderStockFundamentals", "renderStockMargin", "setupSizingCalculator",
                  "setupStockCopyButton", "renderStockMeta", "setupYahooFinanceLink",
                  "renderMustChecklist", "renderScoreBreakdown"]) {
  ctx[fn] = () => {};
}

const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
  ctx.initStockPage("1111"); // 未取得 → ネットワーク待ち
  await tick();
  ctx.initStockPage("2222"); // 追い越して開始
  await tick();

  pending.get("data/charts/2222.json")({ code: "2222", candles: [], volume: [] }); // 新しい方が先に解決
  await tick();
  pending.get("data/charts/1111.json")({ code: "1111", candles: [], volume: [] }); // 古い方が遅れて解決
  await tick();
  await tick();

  const title = document.getElementById("stock-title").textContent;
  const ok = rendered.length === 1 && rendered[0] === "2222" && title.startsWith("2222");
  console.log(`renderCharts: ${JSON.stringify(rendered)} / stock-title: "${title}"`);
  console.log(ok ? "PASS 遅れて解決した古い銘柄は描画されない"
                 : "FAIL 古い銘柄が新しい銘柄を上書きしている");
  process.exit(ok ? 0 : 1);
})();
