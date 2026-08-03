/**
 * レビュータブの「過去の成績」(docs/data/stats.json)まわりの回帰テスト
 * (依存なし・node だけで動く)。
 *
 *     node tests/js/test_review_stats.js [app.js のパス]
 *
 * 見張りたいのは3点:
 *   1. stats.json は断面(大引/前場)のサフィックスを付けずに読むこと。レビューと同じで
 *      大引にしか作れない集計なので、_maezyou を付けると一生 404 になる。
 *   2. ファイルが無い日(履歴が溜まるまでは、これが正常な状態)にレビュー全体を
 *      巻き添えにしないこと。
 *   3. 件数が足りない行 (reliable:false) を消さずに薄字で残すこと。消すと
 *      「その帯にはまだサンプルが無い」ことに気付けないまま残りの帯だけで語ってしまう。
 * ついでに、割合(0〜1)と%表記の値を取り違えていないかも見る。win_rate は100倍、
 * median_return は既に%なので100倍しない。
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

// レビュータブの2つの器だけ実体を返す(他は null で十分)。
const els = new Map();
const KNOWN_IDS = ["review-body", "review-stats-body"];
const document = {
  body: noopEl(), documentElement: noopEl(), activeElement: null,
  addEventListener() {}, removeEventListener() {},
  getElementById(id) {
    if (!KNOWN_IDS.includes(id)) return null;
    if (!els.has(id)) els.set(id, noopEl());
    return els.get(id);
  },
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

// キャッシュ用の let は vm のグローバル "プロパティ" にならないので、
// ctx への代入では消せない。リセットはコンテキスト内の代入式で行う。
const resetStatsCache = () => vm.runInContext("reviewStatsLoadPromise = null;", ctx);

const failures = [];
function check(label, ok, detail) {
  console.log(`${ok ? "PASS" : "FAIL"} ${label}${detail ? ` -- ${detail}` : ""}`);
  if (!ok) failures.push(label);
}

const statsBody = () => document.getElementById("review-stats-body").innerHTML;

(async () => {
  // --- (1) 通常どおり読めた日 --------------------------------------------
  files = {
    "data/stats.json": {
      generated_at: "2026-07-31T16:22:10+09:00",
      window: { from: "2026-03-17", to: "2026-07-30", days: 92 },
      forward_days: 10,
      regime: {
        min_n: 20,
        rows: [
          { label: "40未満", n: 120, win_rate: 0.31, median_return: -0.8, reliable: true },
          { label: "70以上", n: 6, win_rate: 0.5, median_return: 2.5, reliable: false },
        ],
      },
      volume: {
        min_n: 20,
        rows: [{ label: "1.4倍未満", n: 80, win_rate: 0.28, median_return: -1.1, reliable: true }],
      },
      notes: ["同じ銘柄が何度も入っています。"],
    },
  };
  fetched = [];
  await ctx.ensureReviewStatsLoaded();
  check(
    "stats.json は断面サフィックス無しで1回だけ読む",
    fetched.length === 1 && fetched[0] === "data/stats.json",
    `fetched=${JSON.stringify(fetched)}`
  );

  const html = statsBody();
  check("見出しに集計期間と追跡日数が出る", html.includes("2026-03-17〜2026-07-30 の候補を10営業日追跡"));
  check("帯のラベルはそのまま出る", html.includes("40未満") && html.includes("1.4倍未満"));
  check("win_rate は100倍して%にする", html.includes("31.0%") && html.includes("28.0%"), "0.31 -> 31.0%");
  check(
    "median_return は既に%なので100倍しない",
    html.includes("-0.8%") && html.includes("-1.1%") && !html.includes("-80.0%"),
    "-0.8 -> -0.8%"
  );
  check(
    "件数が足りない帯は消さずに薄字+参考値で残す",
    html.includes("70以上") && html.includes("review-unreliable") && html.includes("参考値"),
    "reliable:false の行"
  );
  check("notes はレビュー本体と同じ見た目で並ぶ", html.includes("review-note") && html.includes("同じ銘柄が何度も入っています。"));

  // 2回目のタブ表示では読みに行かない(1回だけの遅延読み込み)。
  fetched = [];
  await ctx.ensureReviewStatsLoaded();
  check("2回目は取りに行かない", fetched.length === 0, `fetched=${JSON.stringify(fetched)}`);

  // --- (2) ファイルが無い日 ------------------------------------------------
  // 履歴が溜まるまではこれが正常。ブロックごと黙って畳むだけで、エラーは出さない。
  resetStatsCache();
  document.getElementById("review-stats-body").innerHTML = "x";
  files = {};
  await ctx.ensureReviewStatsLoaded();
  check("stats.json が無い日はブロックごと出さない", statsBody() === "", `html="${statsBody()}"`);

  // レビュー本体は stats.json とは独立して描ける。
  files = { "data/review.json": { date: "2026-07-31", generated_at: "2026-07-31T16:22:10+09:00", notes: [] } };
  await ctx.ensureReviewLoaded();
  check("stats.json が無くてもレビュー本体は描ける", document.getElementById("review-body").innerHTML.includes("今日の振り返り"));

  // --- (3) だましの実測率 (review.json の stocks.baseline) ------------------
  const b = ctx.reviewBaselineHtml({ days: 18, sample: 145, held_rate: 0.62, today_held_rate: 0.5, reliable: true });
  check(
    "実測率は割合を100倍して出す",
    b.includes("62.0%") && b.includes("50.0%") && b.includes("145件") && b.includes("直近18営業日"),
    "0.62 -> 62.0%"
  );
  check("件数が足りていれば薄字にしない", !b.includes("review-unreliable"));

  const bw = ctx.reviewBaselineHtml({ days: 3, sample: 8, held_rate: 0.62, today_held_rate: null, reliable: false });
  check(
    "件数不足の実測率は薄字+「参考値(件数が足りません)」",
    bw.includes("review-unreliable") && bw.includes("参考値(件数が足りません)"),
    "reliable:false"
  );
  check("キーが丸ごと無い日は何も出さない", ctx.reviewBaselineHtml(null) === "" && ctx.reviewBaselineHtml({}) === "");

  // --- (4) 決算バッジ --------------------------------------------------------
  // 日数はバッチが確定させた値をそのまま使う。画面では出す/出さないの境目だけを決める。
  const badge = (d) => ctx.earningsBadgeHtml({ days_to_earnings: d, next_earnings_date: "2026-08-05" });
  check("5日先までは出す", badge(5).includes("決算まで5日") && badge(4).includes("決算まで4日"));
  check("当日は「今日決算」", badge(0).includes("今日決算"));
  check("6日以上先は出さない", badge(6) === "");
  check("過ぎた銘柄には出さない", badge(-1) === "");
  check("予定日が取れていない銘柄には出さない", badge(null) === "" && ctx.earningsBadgeHtml({}) === "");
  check("既存のバッジと同じ作り(.sc-badge)に乗せる", badge(4).includes('class="sc-badge sc-badge-earnings"'));

  // --- (5) 決算発表が近い銘柄(レビューの小見出し) --------------------------
  // ここもバッジと同じで、日数はバックエンドが確定させた値を出すだけ。近い銘柄が
  // いない日は小見出しごと畳む(「該当なし」を並べない)。
  check(
    "空・未定義なら小見出しごと出さない",
    ctx.reviewEarningsSoonHtml(undefined) === "" &&
      ctx.reviewEarningsSoonHtml(null) === "" &&
      ctx.reviewEarningsSoonHtml([]) === "",
    "earnings_soon が来ない日"
  );

  const es = ctx.reviewEarningsSoonHtml([
    { code: "7203", name: "トヨタ", days_to_earnings: 2, next_earnings_date: "2026-08-05" },
    { code: "6758", name: "ソニー", days_to_earnings: 0, next_earnings_date: "2026-08-03" },
  ]);
  check(
    "行が来たら銘柄コードと日数が出る",
    es.includes("7203") && es.includes("トヨタ") && es.includes("決算まで2日") && es.includes("2026-08-05"),
    "days_to_earnings をそのまま出す"
  );
  check("当日は銘柄カードのバッジと同じ「今日決算」", es.includes("6758") && es.includes("今日決算"));
  check(
    "行は既存のレビュー銘柄行と同じ作り(タップで個別画面へ)",
    es.includes('class="review-stock-row"') && es.includes('data-review-code="7203"')
  );

  console.log(failures.length ? `\n${failures.length} FAILED` : "\nALL PASS");
  process.exit(failures.length ? 1 : 0);
})();
