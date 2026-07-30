/**
 * データ断面(大引 / 前場)の既定選択の回帰テスト (依存なし・node だけで動く)。
 *
 *     node tests/js/test_snapshot_switch.js [app.js のパス]
 *
 * 既定断面を「時計」で決めてはいけない。前場バッチ(maezyou.yml)の cron は
 * 高負荷時にドロップし得る(だから 11:35 と 12:05 の2回投げている)ので、
 * 時計だけで「今は前場だから前場データ」と決めると、落ちた日に前日の古い前場断面を
 * 黙って表示してしまう。resolveDefaultSnapshot() が report.json /
 * report_maezyou.json の generated_at を比べて新しい方を選ぶこと、および
 * maezyouCouldBeNewer() が無駄な約870KBのフェッチを避けることを確認する。
 *
 * 実物の docs/assets/app.js を vm で読み込み、DOM/データ層は最小限のスタブを当てる。
 */
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const SRC = process.argv[2] || path.join(__dirname, "..", "..", "docs", "assets", "app.js");
const source = fs.readFileSync(SRC, "utf8");

const noopEl = () => ({
  textContent: "", innerHTML: "", hidden: false, dataset: {}, style: {}, options: [],
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, removeEventListener() {},
  querySelector: () => null, querySelectorAll: () => [], appendChild() {}, remove() {},
});

const document = {
  body: noopEl(), documentElement: noopEl(), activeElement: null,
  addEventListener() {}, removeEventListener() {},
  getElementById: () => null, // 断面決定ロジックだけ見るので DOM は全部 null で十分
  querySelector: () => null, querySelectorAll: () => [], createElement: () => noopEl(),
};

const window = {
  MINERVINI_CONFIG: { passkeyAuthEnabled: false },
  location: { hash: "", search: "" },
  devicePixelRatio: 1,
  addEventListener() {}, removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
};

// どの URL を何回叩いたか記録する fetch スタブ。files に無い URL は null
// (= optional 扱いでファイル未生成)。
let files = {};
let fetched = [];
window.MinerviniData = {
  hasDataKey: () => true,
  isEnvelope: () => false,
  setDataKey() {},
  fetchJson(url) {
    fetched.push(url);
    return Promise.resolve(Object.prototype.hasOwnProperty.call(files, url) ? files[url] : null);
  },
};

const ctx = {
  window, document, console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Promise, Map, Set, Date, Math, JSON, Number, Object, Array, String, URLSearchParams, Intl,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  LightweightCharts: null,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(source, ctx, { filename: path.basename(SRC) });

const failures = [];
function check(label, ok, detail) {
  console.log(`${ok ? "PASS" : "FAIL"} ${label}${detail ? ` -- ${detail}` : ""}`);
  if (!ok) failures.push(label);
}

// JST の日時から UTC の Date を作る(テストの意図を読みやすくするため)。
const jst = (y, m, d, hh, mm) => new Date(Date.UTC(y, m - 1, d, hh - 9, mm));

(async () => {
  // --- maezyouCouldBeNewer: 前場 report を取りに行くかの事前判定 -------------
  // 朝イチ(9:10)は前場バッチがまだ走っていないので取りに行かない。
  check(
    "9:10 JST は前場を取りに行かない",
    ctx.maezyouCouldBeNewer(jst(2026, 7, 29, 16, 22).toISOString(), jst(2026, 7, 30, 9, 10)) === false
  );
  // 11:40 で canonical が前日のまま = 当日の前場が新しい可能性あり → 取りに行く。
  check(
    "11:40 JST・canonical が前日なら前場を取りに行く",
    ctx.maezyouCouldBeNewer(jst(2026, 7, 29, 16, 22).toISOString(), jst(2026, 7, 30, 11, 40)) === true
  );
  // 夕方、当日の大引バッチが済んでいれば前場より必ず新しいので取りに行かない。
  check(
    "当日16:22の canonical があれば前場は取りに行かない",
    ctx.maezyouCouldBeNewer(jst(2026, 7, 30, 16, 22).toISOString(), jst(2026, 7, 30, 18, 0)) === false
  );

  // --- resolveDefaultSnapshot: 実際の既定断面 -------------------------------
  // (1) 前場バッチが成功した日の昼 → 前場断面になる。
  files = {
    "data/report.json": { generated_at: jst(2026, 7, 29, 16, 22).toISOString(), stocks: [] },
    "data/report_maezyou.json": { generated_at: jst(2026, 7, 30, 11, 41).toISOString(), stocks: [] },
  };
  fetched = [];
  const now1 = jst(2026, 7, 30, 12, 0);
  // resolveDefaultSnapshot は内部で new Date() を使うので、この時刻を返す Date に差し替える。
  const RealDate = Date;
  ctx.Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : [now1.getTime()])); }
    static now() { return now1.getTime(); }
  };
  const r1 = await ctx.resolveDefaultSnapshot();
  check("前場バッチ成功日の昼は前場断面", r1.suffix === "_maezyou", `suffix="${r1.suffix}"`);

  // (2) 前場バッチが落ちた日(report_maezyou.json が前日のまま) → 大引へフォールバック。
  //     時計で決めていたらここで前日の前場データを表示してしまう。
  files = {
    "data/report.json": { generated_at: jst(2026, 7, 29, 16, 22).toISOString(), stocks: [] },
    "data/report_maezyou.json": { generated_at: jst(2026, 7, 29, 11, 41).toISOString(), stocks: [] },
  };
  const r2 = await ctx.resolveDefaultSnapshot();
  check("前場バッチが落ちた日は大引へフォールバック", r2.suffix === "", `suffix="${r2.suffix}"`);

  // (3) 前場スナップショット自体が未生成でも落ちない。
  files = { "data/report.json": { generated_at: jst(2026, 7, 29, 16, 22).toISOString(), stocks: [] } };
  const r3 = await ctx.resolveDefaultSnapshot();
  check("前場ファイル未生成でも大引で描画できる", r3.suffix === "" && r3.report != null);

  // (4) 当日大引が済んでいれば、前場 report を一度も取りに行かない(約870KBの節約)。
  files = {
    "data/report.json": { generated_at: jst(2026, 7, 30, 16, 22).toISOString(), stocks: [] },
    "data/report_maezyou.json": { generated_at: jst(2026, 7, 30, 11, 41).toISOString(), stocks: [] },
  };
  const now2 = jst(2026, 7, 30, 18, 0);
  ctx.Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : [now2.getTime()])); }
    static now() { return now2.getTime(); }
  };
  fetched = [];
  const r4 = await ctx.resolveDefaultSnapshot();
  check(
    "大引後は前場 report を取りに行かない",
    r4.suffix === "" && !fetched.includes("data/report_maezyou.json"),
    `fetched=${JSON.stringify(fetched)}`
  );
  ctx.Date = RealDate;

  // --- loadSnapshotBundle: コピーボタンが断面ごとに揃った束を取ること -------
  files = {
    "data/report_maezyou.json": { generated_at: "x", stocks: [] },
    "data/breadth_maezyou.json": { history: [] },
    "data/indices_maezyou.json": {},
    "data/positions_maezyou.json": {},
  };
  fetched = [];
  const bundle = await ctx.loadSnapshotBundle("_maezyou");
  check(
    "前場束は _maezyou 4本を取る",
    bundle != null && fetched.every((u) => u.includes("_maezyou")) && fetched.length === 4,
    `fetched=${JSON.stringify(fetched)}`
  );
  files = {};
  const missing = await ctx.loadSnapshotBundle("_maezyou");
  check("前場束が無ければ null を返す(呼び出し側が大引へ落とす)", missing === null);

  console.log(failures.length ? `\n${failures.length} FAILED` : "\nALL PASS");
  process.exit(failures.length ? 1 : 0);
})();
