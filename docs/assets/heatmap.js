// 機能B: セクターヒートマップ (Finviz風ツリーマップ)。
// 依存ライブラリなしのバニラJS squarifiedツリーマップ実装。
// データは日次バッチ出力の data/heatmap.json のみ (フロントは描画に徹する)。

let HM = null;
let SECTOR_HISTORY = null; // 簡易ビュー履歴 (docs/data/sector_history.json)。未公開ならnull
let currentPeriod = "d1";
let currentView = "detail"; // "detail" (既存treemap) | "simple" (セクター騰落率のみ)
// SPA化に伴い、initHeatmap()はセクターマップタブに切り替えるたび毎回呼ばれる
// (非表示中はcontainerの幅が0でtreemapが描けないため、表示時に再計算・再描画
// する必要がある)。イベントリスナーの重複登録だけは防ぐためのフラグ。
let hmWired = false;
// 直近レンダリング時のコンテナ幅。スマホでスクロールするとアドレスバーの
// 表示/非表示で innerHeight が変わり resize が発火するが、幅が変わらない限り
// treemapを再描画しない(=ブロックが動かない)ためのガード。
let lastRenderWidth = 0;

// 期間ごとの色クランプ(±%): これを超える騰落率は最濃色で飽和
const COLOR_CLAMP = { d1: 3, d5: 6, d20: 12, d60: 25 };

const PERIOD_LABELS = { d1: "1日", d5: "5日", d20: "20日", d60: "60日" };

// 機能A: 未達条件の日本語ラベル (app.jsと同一定義; このページはapp.js非依存)
const HM_COND_LABELS = {
  close_above_ma50: "終値≦50日線",
  near_high52w: "52週高値から遠い",
  ma50_above_ma150: "50≦150日線(並び崩れ)",
  rs_above_min: "RS70未満",
};

async function initHeatmap() {
  HM = await window.MinerviniData.fetchJson("data/heatmap.json", { optional: true }).catch(() => null);
  // 簡易ビューの履歴(市況カード同等)。まだ公開されていなければnullのまま。
  SECTOR_HISTORY = await window.MinerviniData
    .fetchJson("data/sector_history.json", { optional: true })
    .catch(() => null);

  const empty = document.getElementById("hm-empty");
  if (!HM || !HM.sectors || !HM.sectors.length) {
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.remove();

  const meta = document.getElementById("hm-meta");
  if (meta) {
    const when = HM.generated_at ? new Date(HM.generated_at).toLocaleString("ja-JP") : "-";
    const nStocks = HM.sectors.reduce((s, sec) => s + (sec.stock_count || sec.stocks.length), 0);
    meta.textContent = `最終更新: ${when} / ${HM.sectors.length}セクター ${nStocks}銘柄`;
  }

  if (!hmWired) {
    const toggle = document.getElementById("hm-period-toggle");
    if (toggle) {
      toggle.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-period]");
        if (!btn) return;
        toggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
        currentPeriod = btn.dataset.period;
        render();
      });
    }

    // 詳細/簡易は横スワイプ(scroll-snap)で切替。トグルはそのパネルへスクロール
    // するショートカット。パネルのスクロール位置とトグルの選択を双方向同期。
    const viewToggle = document.getElementById("hm-view-toggle");
    const panels = document.getElementById("hm-panels");
    if (viewToggle && panels) {
      viewToggle.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-view]");
        if (!btn) return;
        scrollToHmView(btn.dataset.view);
      });

      let raf = 0;
      panels.addEventListener(
        "scroll",
        () => {
          if (raf) return;
          raf = requestAnimationFrame(() => {
            raf = 0;
            const idx = Math.round(panels.scrollLeft / Math.max(1, panels.clientWidth));
            const el = panels.querySelectorAll(".hm-panel")[idx];
            if (el && el.dataset.view !== currentView) {
              currentView = el.dataset.view;
              updateHmViewToggle();
            }
          });
        },
        { passive: true }
      );
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        // 幅が変わっていなければ(=スクロールに伴う高さ変化だけなら)再描画しない。
        const c = document.getElementById("hm-container-detail");
        const w = c ? c.clientWidth : 0;
        if (w && w !== lastRenderWidth) render();
      }, 150);
    });

    hmWired = true;
  }

  render();
  // 表示のたびに現在ビューのパネルへ位置を合わせる(非表示中はscrollLeftが0に戻るため)。
  requestAnimationFrame(() => scrollToHmView(currentView, false));
}

// トグルの選択表示を currentView に合わせる。
function updateHmViewToggle() {
  const viewToggle = document.getElementById("hm-view-toggle");
  if (!viewToggle) return;
  viewToggle.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.view === currentView));
}

// 指定ビューのパネルへ横スクロール。smooth=false なら即時(初期表示用)。
function scrollToHmView(view, smooth = true) {
  const panels = document.getElementById("hm-panels");
  if (!panels) return;
  const list = Array.from(panels.querySelectorAll(".hm-panel"));
  const idx = Math.max(0, list.findIndex((el) => el.dataset.view === view));
  currentView = list[idx] ? list[idx].dataset.view : "detail";
  updateHmViewToggle();
  panels.scrollTo({ left: idx * panels.clientWidth, behavior: smooth ? "smooth" : "auto" });
}

// ヒートマップ描画高さ = ビューポート - パネル上端 - 下部バー - ドック余白。
function heatmapHeight() {
  const panels = document.getElementById("hm-panels");
  const top = panels ? panels.getBoundingClientRect().top : 0;
  const bar = document.querySelector(".hm-bottom-bar");
  const barH = bar ? bar.getBoundingClientRect().height : 0;
  const reserve = barH + 58; // 下部トグルバー + ドック余白(見出し/凡例削除でマップを拡大)
  return Math.max(360, Math.round(window.innerHeight - top - reserve));
}

// ---------------------------------------------------------------------------
// squarified treemap
// items: [{ weight, ... }] weight降順ソート済み。矩形リストを返す。
// ---------------------------------------------------------------------------
function squarify(items, x, y, w, h) {
  const rects = [];
  if (!items.length || w <= 0 || h <= 0) return rects;
  const total = items.reduce((s, i) => s + i.weight, 0);
  if (total <= 0) return rects;
  const scale = (w * h) / total;

  let rx = x, ry = y, rw = w, rh = h;
  let row = [], rowSum = 0;

  function worst(list, sum, side) {
    let maxA = -Infinity, minA = Infinity;
    for (const it of list) {
      const a = it.weight * scale;
      if (a > maxA) maxA = a;
      if (a < minA) minA = a;
    }
    const s2 = (sum * scale) ** 2;
    return Math.max((side * side * maxA) / s2, s2 / (side * side * minA));
  }

  function layoutRow(list, sum) {
    const areaSum = sum * scale;
    if (rw >= rh) {
      const stripW = rh > 0 ? areaSum / rh : 0;
      let cy = ry;
      for (const it of list) {
        const hgt = stripW > 0 ? (it.weight * scale) / stripW : 0;
        rects.push({ item: it, x: rx, y: cy, w: stripW, h: hgt });
        cy += hgt;
      }
      rx += stripW;
      rw -= stripW;
    } else {
      const stripH = rw > 0 ? areaSum / rw : 0;
      let cx = rx;
      for (const it of list) {
        const wid = stripH > 0 ? (it.weight * scale) / stripH : 0;
        rects.push({ item: it, x: cx, y: ry, w: wid, h: stripH });
        cx += wid;
      }
      ry += stripH;
      rh -= stripH;
    }
  }

  for (const it of items) {
    const side = Math.max(1, Math.min(rw, rh));
    if (row.length && worst([...row, it], rowSum + it.weight, side) > worst(row, rowSum, side)) {
      layoutRow(row, rowSum);
      row = [it];
      rowSum = it.weight;
    } else {
      row.push(it);
      rowSum += it.weight;
    }
  }
  if (row.length) layoutRow(row, rowSum);
  return rects;
}

// ---------------------------------------------------------------------------
// 色・重み
// ---------------------------------------------------------------------------
function tileColor(ret) {
  if (ret == null) return "rgb(58,64,78)"; // データなし=グレー
  const clamp = COLOR_CLAMP[currentPeriod] || 5;
  const t = Math.max(-1, Math.min(1, ret / clamp));
  const base = [58, 64, 78]; // 変わらず=グレー
  const target = t >= 0 ? [13, 148, 88] : [190, 44, 54]; // 緑 / 赤
  const k = Math.abs(t);
  const rgb = base.map((b, i) => Math.round(b + (target[i] - b) * k));
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

// 時価総額不明タイルの固定最小面積: 既知mcapの中央値の1/4 (それもなければ1)
function fallbackWeight() {
  const known = [];
  for (const sec of HM.sectors) for (const s of sec.stocks) if (s.mcap) known.push(s.mcap);
  if (!known.length) return 1;
  known.sort((a, b) => a - b);
  return known[Math.floor(known.length / 2)] / 4;
}

function fmtPct(v) {
  if (v == null) return "-";
  return (v > 0 ? "+" : "") + v.toFixed(currentPeriod === "d1" ? 2 : 1) + "%";
}

// ---------------------------------------------------------------------------
// 描画
// ---------------------------------------------------------------------------
const SECTOR_HEADER_H = 20;
const GAP = 1;

function render() {
  const detailC = document.getElementById("hm-container-detail");
  const simpleC = document.getElementById("hm-container-simple");
  if (!detailC || !HM) return;
  detailC.innerHTML = "";
  if (simpleC) simpleC.innerHTML = "";

  const W = detailC.clientWidth;
  lastRenderWidth = W; // resize時の幅変化判定用に記録
  // 下部トグルバー + ドックを避けた実効高さ(縦スクロール不要)。
  const H = heatmapHeight();
  detailC.style.height = H + "px";
  if (simpleC) simpleC.style.height = H + "px";

  updateLegend();

  const fbw = fallbackWeight();
  const sectors = HM.sectors
    .map((sec) => {
      const stocks = sec.stocks.map((s) => ({ ...s, weight: s.mcap || fbw }));
      return { ...sec, stocksW: stocks, weight: stocks.reduce((t, s) => t + s.weight, 0) };
    })
    .filter((sec) => sec.weight > 0)
    .sort((a, b) => b.weight - a.weight);

  const sectorRects = squarify(sectors, 0, 0, W, H);

  // 詳細/簡易の両パネルを描画しておき、横スワイプで即切替できるようにする。
  renderDetail(detailC, sectorRects);
  if (simpleC) renderSimple(simpleC, sectorRects);
}

// 詳細表示: 既存のセクター×銘柄の二段squarifiedツリーマップ。
function renderDetail(container, sectorRects) {
  for (const { item: sec, x, y, w, h } of sectorRects) {
    const box = document.createElement("div");
    box.className = "hm-sector";
    Object.assign(box.style, {
      left: x + "px",
      top: y + "px",
      width: Math.max(0, w - GAP) + "px",
      height: Math.max(0, h - GAP) + "px",
    });

    const showHeader = h >= SECTOR_HEADER_H + 14 && w >= 50;
    if (showHeader) {
      const head = document.createElement("div");
      head.className = "hm-sector-head";
      head.style.height = SECTOR_HEADER_H + "px";
      // セクター相対強度は矢印ではなく数値(対TOPIX 20日相対リターン%)+色で表現。
      const rsVal = sec.rs ? sec.rs.rel_strength_pct : null;
      const rsTxt =
        rsVal != null
          ? `<span class="${rsVal >= 0 ? "hm-rs-pos" : "hm-rs-neg"}">RS${rsVal > 0 ? "+" : ""}${rsVal.toFixed(1)}</span>`
          : "";
      head.innerHTML = `<span class="hm-sector-name">${sec.sector}</span><span class="hm-sector-info">${rsTxt} ${fmtPct(sec.returns[currentPeriod])}</span>`;
      head.title = `${sec.sector} ${fmtPct(sec.returns[currentPeriod])} / 対TOPIX相対強度: ${rsVal != null ? rsVal + "%" : "-"}`;
      box.appendChild(head);
    }

    const innerY = showHeader ? SECTOR_HEADER_H : 0;
    const innerH = Math.max(0, h - GAP - innerY);
    const innerW = Math.max(0, w - GAP);
    const tileRects = squarify([...sec.stocksW].sort((a, b) => b.weight - a.weight), 0, 0, innerW, innerH);

    for (const { item: s, x: tx, y: ty, w: tw, h: th } of tileRects) {
      const tile = document.createElement("div");
      const ret = s.returns ? s.returns[currentPeriod] : null;
      tile.className = "hm-tile";
      Object.assign(tile.style, {
        left: tx + "px",
        top: innerY + ty + "px",
        width: Math.max(0, tw - GAP) + "px",
        height: Math.max(0, th - GAP) + "px",
        background: tileColor(ret),
      });

      // ラベル段階表示: 広→コード+騰落率 / 中→コードのみ / 極小→なし
      const iw = tw - GAP, ih = th - GAP;
      if (iw >= 56 && ih >= 30) {
        tile.innerHTML = `<span class="hm-tile-code">${s.code}</span><span class="hm-tile-ret">${fmtPct(ret)}</span>`;
      } else if (iw >= 34 && ih >= 14) {
        tile.innerHTML = `<span class="hm-tile-code hm-tile-code-sm">${s.code}</span>`;
      }
      tile.title = `${s.code} ${s.name} ${fmtPct(ret)}`;
      tile.addEventListener("click", () => openTilePopup(s, sec));
      box.appendChild(tile);
    }
    container.appendChild(box);
  }
}

// 簡易表示: 個別銘柄タイルを省き、セクター全体を1枚の色付きボックスとして
// 選択期間の騰落率のみを表示する(全体感をざっくり把握したい用途)。
// 面積は詳細表示と同じくセクター内銘柄の時価総額合計(weight)を使う。
function renderSimple(container, sectorRects) {
  for (const { item: sec, x, y, w, h } of sectorRects) {
    const box = document.createElement("div");
    box.className = "hm-sector hm-sector-simple";
    const ret = sec.returns ? sec.returns[currentPeriod] : null;
    Object.assign(box.style, {
      left: x + "px",
      top: y + "px",
      width: Math.max(0, w - GAP) + "px",
      height: Math.max(0, h - GAP) + "px",
      background: tileColor(ret),
    });

    const rsVal = sec.rs ? sec.rs.rel_strength_pct : null;
    const showRs = rsVal != null && w >= 70 && h >= 48;
    const rsHtml = showRs
      ? `<span class="hm-simple-rs ${rsVal >= 0 ? "hm-rs-pos" : "hm-rs-neg"}">RS${rsVal > 0 ? "+" : ""}${rsVal.toFixed(1)}</span>`
      : "";
    box.innerHTML = `<span class="hm-simple-name">${sec.sector}</span><span class="hm-simple-ret">${fmtPct(ret)}</span>${rsHtml}`;
    box.title = `${sec.sector} ${fmtPct(ret)}${rsVal != null ? ` / 対TOPIX相対強度: ${rsVal}%` : ""} — タップで履歴`;
    box.classList.add("hm-sector-clickable");
    box.addEventListener("click", () => openSectorHistoryPopup(sec));
    container.appendChild(box);
  }
}

// 簡易ビューのセクターをタップ → 対TOPIX相対強度と日次騰落率の履歴を
// 大きめスパークラインで表示(市況カードのポップアップと同じ発想)。
function openSectorHistoryPopup(sec) {
  closeTilePopup();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "hm-popup";
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeTilePopup();
  });

  const rsVal = sec.rs ? sec.rs.rel_strength_pct : null;
  const rsTxt = rsVal != null ? `RS${rsVal > 0 ? "+" : ""}${rsVal.toFixed(1)}` : "-";

  const series = SECTOR_HISTORY && SECTOR_HISTORY.sectors ? SECTOR_HISTORY.sectors[sec.sector] : null;
  const dates = (SECTOR_HISTORY && SECTOR_HISTORY.dates) || [];

  let historyHtml;
  if (!series || !dates.length) {
    historyHtml = `<p class="hm-none">履歴データはまだありません(次回のバッチ実行後に表示されます)。</p>`;
  } else {
    const rel = series.rel_strength_pct || [];
    const d1 = series.d1 || [];
    const relLast = lastNonNull(rel);
    const d1Last = lastNonNull(d1);
    historyHtml = `
      <h4>対TOPIX相対強度の推移 <span class="hm-hist-sub">(直近${dates.length}営業日)</span></h4>
      <div class="hm-hist-spark">${histSparkline(rel, true)}</div>
      <p class="hm-none">最新: 相対強度 ${relLast != null ? (relLast > 0 ? "+" : "") + relLast.toFixed(1) + "%" : "-"} / 前日比 ${d1Last != null ? (d1Last > 0 ? "+" : "") + d1Last.toFixed(2) + "%" : "-"}</p>
      <h4>前日比(d1)の推移</h4>
      <div class="hm-hist-spark">${histSparkline(d1, false)}</div>`;
  }

  overlay.innerHTML = `
    <div class="modal-box hm-popup-box">
      <div class="hm-popup-head">
        <h3>${sec.sector}</h3>
        <button type="button" class="secondary" id="hm-popup-close">閉じる</button>
      </div>
      <div class="meta-chips">
        <span class="chip"><span class="chip-label">${PERIOD_LABELS[currentPeriod] || currentPeriod}</span><span class="chip-value">${fmtPctRaw(sec.returns ? sec.returns[currentPeriod] : null)}</span></span>
        <span class="chip"><span class="chip-label">相対強度</span><span class="chip-value">${rsTxt}</span></span>
        <span class="chip"><span class="chip-label">銘柄数</span><span class="chip-value">${sec.stock_count || (sec.stocks ? sec.stocks.length : "-")}</span></span>
      </div>
      ${historyHtml}
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("hm-popup-close").addEventListener("click", closeTilePopup);
}

function lastNonNull(arr) {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] != null) return arr[i];
  }
  return null;
}

// 依存なしのSVGスパークライン。0ラインを基準に、上=緑/下=赤で塗り分ける。
function histSparkline(values, colorByLast) {
  const W = 280;
  const H = 64;
  const pad = 4;
  const nums = values.map((v) => (v == null ? null : Number(v)));
  const valid = nums.filter((v) => v != null);
  if (valid.length < 2) return `<span class="hm-none">データ不足</span>`;
  let min = Math.min(...valid, 0);
  let max = Math.max(...valid, 0);
  if (max === min) max = min + 1;
  const n = nums.length;
  const xAt = (i) => pad + (i / (n - 1)) * (W - 2 * pad);
  const yAt = (v) => pad + (1 - (v - min) / (max - min)) * (H - 2 * pad);

  let dPath = "";
  let started = false;
  nums.forEach((v, i) => {
    if (v == null) return;
    dPath += `${started ? "L" : "M"}${xAt(i).toFixed(1)} ${yAt(v).toFixed(1)} `;
    started = true;
  });

  const last = lastNonNull(nums);
  const up = colorByLast ? last >= 0 : last >= 0;
  const stroke = up ? "var(--accent)" : "var(--danger)";
  const zeroY = yAt(0).toFixed(1);

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none" role="img">
    <line x1="${pad}" y1="${zeroY}" x2="${W - pad}" y2="${zeroY}" stroke="var(--border-strong)" stroke-width="1" stroke-dasharray="3 3"/>
    <path d="${dPath.trim()}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}

function updateLegend() {
  const clamp = COLOR_CLAMP[currentPeriod] || 5;
  const minEl = document.getElementById("hm-legend-min");
  const maxEl = document.getElementById("hm-legend-max");
  if (minEl) minEl.textContent = `-${clamp}%`;
  if (maxEl) maxEl.textContent = `+${clamp}%`;
}

// ---------------------------------------------------------------------------
// タイルタップ: 機能A詳細ポップアップ
// ---------------------------------------------------------------------------
function openTilePopup(s, sec) {
  closeTilePopup();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.id = "hm-popup";
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeTilePopup();
  });

  const d = s.detail || {};
  const cond8 = s.priority === 1 ? "合格" : s.priority != null ? "未達あり" : "対象外(ハードフィルタ未達)";
  const returnRows = (HM.periods || [1, 5, 20, 60])
    .map((p) => `<span class="chip"><span class="chip-label">${PERIOD_LABELS["d" + p] || p + "日"}</span><span class="chip-value">${fmtPctRaw(s.returns ? s.returns["d" + p] : null)}</span></span>`)
    .join("");

  let unmetHtml = "";
  if (d.priority_unmet && d.priority_unmet.length) {
    unmetHtml =
      "<h4>未達条件(距離)</h4><ul class='hm-unmet'>" +
      d.priority_unmet
        .map((u) => {
          const label = HM_COND_LABELS[u.condition] || u.condition;
          const dist = u.distance_pct != null ? (u.condition === "rs_above_min" ? ` (差${u.distance_pct})` : ` (${u.distance_pct}%)`) : "";
          return `<li>✗ ${label}${dist} [+${u.penalty}]</li>`;
        })
        .join("") +
      "</ul>";
  } else if (s.priority === 1) {
    unmetHtml = "<h4>未達条件</h4><p class='hm-none'>なし(8条件完全一致)</p>";
  }

  let devHtml = "";
  if (d.ma_deviation_pct) {
    const dev = d.ma_deviation_pct;
    const f = (v) => (v == null ? "-" : (v > 0 ? "+" : "") + v.toFixed(1) + "%");
    devHtml = `<h4>移動平均線乖離</h4><p class='hm-none'>50日: ${f(dev.ma50)} / 150日: ${f(dev.ma150)} / 200日: ${f(dev.ma200)}${d.high52w_distance_pct != null ? ` / 52週高値まで -${d.high52w_distance_pct}%` : ""}</p>`;
  }

  const rsVal = sec.rs ? sec.rs.rel_strength_pct : null;
  const rsTxt = rsVal != null ? `RS${rsVal > 0 ? "+" : ""}${rsVal.toFixed(1)}` : "-";
  const chartLink = d.has_chart
    ? `<a href="stock.html?code=${encodeURIComponent(s.code)}" class="nav-btn hm-detail-link">チャート・詳細ページへ →</a>`
    : "";

  overlay.innerHTML = `
    <div class="modal-box hm-popup-box">
      <div class="hm-popup-head">
        <h3>${s.code} ${s.name}</h3>
        <button type="button" class="secondary" id="hm-popup-close">閉じる</button>
      </div>
      <div class="meta-chips">
        <span class="chip"><span class="chip-label">8条件</span><span class="chip-value">${cond8}</span></span>
        <span class="chip"><span class="chip-label">RS</span><span class="chip-value">${s.rs ?? "-"}</span></span>
        <span class="chip"><span class="chip-label">終値</span><span class="chip-value">${s.close != null ? s.close.toLocaleString("ja-JP") : "-"}</span></span>
        <span class="chip"><span class="chip-label">セクター</span><span class="chip-value">${sec.sector} ${rsTxt}</span></span>
      </div>
      <h4>騰落率</h4>
      <div class="meta-chips">${returnRows}</div>
      ${unmetHtml}
      ${devHtml}
      ${chartLink}
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById("hm-popup-close").addEventListener("click", closeTilePopup);
}

function fmtPctRaw(v) {
  if (v == null) return "-";
  return (v > 0 ? "+" : "") + v.toFixed(2) + "%";
}

function closeTilePopup() {
  const el = document.getElementById("hm-popup");
  if (el) el.remove();
}

// SPAルーター(app.js)がセクターマップタブ表示時に呼び出す。単独ページ
// (heatmap.html経由の旧URL)では存在しないため、末尾の自動起動は行わない。
