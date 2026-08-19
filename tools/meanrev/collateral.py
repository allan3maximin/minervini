#!/usr/bin/env python3
"""「巻き添えで落ちたやつ」だけ狙えるか(2026-08-19)。

183 で分かったのは、地合いが「攻め」のときに RSI が5を割る銘柄は
その銘柄に固有の悪い理由があって跳ね返らない、ということだった。
なら地合いという遠回りをせず、**その銘柄が巻き添えで落ちたのかどうかを
直接測ればいい**というのがこの回の問い。

当日に分かる測り方を3通り用意する。どれも先読みしていない。

  A 一斉に売られたか
      その日、母集団の何%が同時に RSI(2)<5 になったか。
      200銘柄が一斉ならそれは市場の事故。3銘柄だけならその銘柄の事故。
  B 市場より余分に落ちた分
      銘柄の5日下落率 − 市場の5日下落率。
      市場と同じだけ落ちただけなら巻き添え。市場の倍落ちていたら固有の理由。
  C 市場そのものが落ちたか
      市場の5日下落率。
  D 落ちた深さそのもの
      銘柄の5日下落率。B と混ざりやすいので必ず分けて見る。

    python tools/meanrev/collateral.py

★ここでいちど間違えた。B だけを見ると「単独で急落した側」も良く見えるが、
それは B が D(深さ)と混ざっているから。深さを揃えて B で切り直すと、
巻き添え側が一方的に良い(§4b)。B と D は必ず分けて測る。

いちばん大事なのは §4。A〜C が単に地合いの言い換えなら、
地合いで切ったあとに差が消えるはず。消えなければ新しい情報がある。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402
from meanrev import exits as E  # noqa: E402
from meanrev.regime_filter import REGIMES, market, signal_hist  # noqa: E402

LIQ = 3e8
STOP = "1.5ATR(3-12%)"
BASE_HOLD = 5
RS_MIN = 70


def _f(name: str, src: bool = True) -> np.ndarray:
    return np.asarray(C.load(name, src=src), dtype=np.float32)


def measures():
    """A/B/C を date×code と date の行列で返す。すべて当日までの値。"""
    close = _f("close")
    rsi = np.asarray(C.load("rsi2"), dtype=np.float32)
    rs = _f("rs")
    tv = np.asarray(C.load("tv20med"))

    fin = np.isfinite(close) & (close > 0)
    pop = fin & np.isfinite(rs) & (rs >= RS_MIN) & np.isfinite(tv) & (tv >= LIQ)

    # A: その日、母集団のうち何%が同時に売られすぎになったか
    over = pop & np.isfinite(rsi) & (rsi < 5)
    denom = pop.sum(axis=1).astype(np.float64)
    with np.errstate(all="ignore"):
        co = np.where(denom >= 50, over.sum(axis=1) / denom, np.nan)

    # B: 銘柄の5日下落率 − 市場の5日下落率
    m = market()
    lvl = m["idx"].to_numpy(dtype=float)
    mret5 = np.full(len(m), np.nan)
    mret5[5:] = lvl[5:] / lvl[:-5] - 1.0
    with np.errstate(all="ignore"):
        sret5 = np.full_like(close, np.nan, dtype=np.float32)
        sret5[5:] = close[5:] / close[:-5] - 1.0
    excess = sret5.astype(np.float64) - mret5[:, None]

    return pop, co, mret5, excess, sret5.astype(np.float64)


def build(pop):
    close = _f("close")
    rsi = np.asarray(C.load("rsi2"))
    sd_mat = C.stop_size(STOP)

    bad = C.bad_bar()
    T = bad.shape[0]
    win = np.zeros_like(bad)
    for k in range(0, E.MAXH + 1):
        win[: T - k] |= bad[k:]

    m = pop & C.tradable() & np.isfinite(rsi) & (np.asarray(rsi) < 5)
    m = C.dedup(m, E.MAXH)
    ts, cs = np.nonzero(m)
    ex = E.exit_index(ts, cs, np.isfinite(rsi) & (rsi > 70), E.MAXH)
    sd = sd_mat[ts, cs].astype(np.float64)
    ok = (ex < T) & ~win[ts, cs] & np.isfinite(sd) & (sd > 0)
    ts, cs, ex, sd = ts[ok], cs[ok], ex[ok], sd[ok]
    r = (close[ex, cs].astype(np.float64) * (1 - C.COST_ONEWAY)) / \
        (close[ts, cs].astype(np.float64) * (1 + C.COST_ONEWAY)) - 1.0
    g = np.isfinite(r)
    sig = (r[g], (r[g] / sd[g]), ts[g], cs[g])

    ret = C.forward_return(BASE_HOLD)
    mb = C.thin_baseline(pop & C.tradable(), BASE_HOLD)
    mb &= np.isfinite(ret) & np.isfinite(sd_mat) & (sd_mat > 0) & ~win
    tb, cb = np.nonzero(mb)
    rb = ret[tb, cb].astype(np.float64)
    base = (rb, rb / sd_mat[tb, cb].astype(np.float64), tb, cb)
    return sig, base


def row(label, sel_s, sel_b, sig, base, extra=""):
    r, R = sig[0], sig[1]
    rb = base[0]
    n = int(sel_s.sum())
    if n < 50:
        print(f"  {label:<24} n={n:>6,}  (件数不足)")
        return
    se2 = 2 * R[sel_s].std(ddof=1) / np.sqrt(n)
    bm = rb[sel_b].mean() * 100 if sel_b.sum() > 50 else float("nan")
    take = r[sel_s].mean() * 100
    print(f"  {label:<24} n={n:>6,} 勝率 {(r[sel_s] > 0).mean()*100:4.1f}% "
          f"手取り {take:+.2f}% (無条件 {bm:+.2f}% / 上乗せ {take-bm:+.2f}%) "
          f"期待R {R[sel_s].mean():+.3f}±{se2:.3f}{extra}")


def qbuckets(v, qs=(0.2, 0.4, 0.6, 0.8)):
    """有限値の分位点で境界を作る。"""
    f = v[np.isfinite(v)]
    return np.quantile(f, qs)


def main() -> None:
    pop, co, mret5, excess, sret5 = measures()
    sig, base = build(pop)
    ts, cs = sig[2], sig[3]
    tb, cb = base[2], base[3]
    dts = C.dates()
    d = dts[ts]
    m = market()
    cls = signal_hist(m)

    print(f"母集団: 市場より強い上位3割(RS{RS_MIN}以上) / 売買代金 {LIQ/1e8:.0f}億円以上")
    print(f"シグナル RSI(2)<5 → RSI(2)>70で降りる(上限10日) / "
          f"コスト往復 {C.COST_ONEWAY*200:.1f}%")
    print(f"全体 n={sig[0].size:,} 手取り {sig[0].mean()*100:+.2f}%")

    # ---- A ----
    print("\n=== A. その日、母集団の何%が一斉に売られすぎになったか ===")
    print("   多いほど「市場の事故」= 巻き添え。少ないほど「その銘柄の事故」。")
    a_s, a_b = co[ts], co[tb]
    edges = [(0, 0.01), (0.01, 0.02), (0.02, 0.04), (0.04, 0.08), (0.08, 1.01)]
    for lo, hi in edges:
        ss = np.isfinite(a_s) & (a_s >= lo) & (a_s < hi)
        bb = np.isfinite(a_b) & (a_b >= lo) & (a_b < hi)
        row(f"同時 {lo*100:.0f}〜{hi*100:.0f}%", ss, bb, sig, base)

    # ---- B ----
    print("\n=== B. 市場より余分に落ちた分(銘柄の5日 − 市場の5日) ===")
    print("   0に近いほど市場と一緒に落ちただけ=巻き添え。大きくマイナスほど固有の理由。")
    b_s = excess[ts, cs]
    b_b = excess[tb, cb]
    qs = qbuckets(b_s)
    lab = [f"〜{qs[0]*100:+.0f}%(単独で急落)"] + \
        [f"{qs[i]*100:+.0f}〜{qs[i+1]*100:+.0f}%" for i in range(len(qs) - 1)] + \
        [f"{qs[-1]*100:+.0f}%〜(市場ほど落ちてない)"]
    bnd = [-np.inf, *qs, np.inf]
    for i in range(len(bnd) - 1):
        ss = np.isfinite(b_s) & (b_s >= bnd[i]) & (b_s < bnd[i + 1])
        bb = np.isfinite(b_b) & (b_b >= bnd[i]) & (b_b < bnd[i + 1])
        row(lab[i], ss, bb, sig, base)

    # ---- C ----
    print("\n=== C. 市場そのものの5日下落率 ===")
    c_s, c_b = mret5[ts], mret5[tb]
    for lo, hi in [(-1, -0.05), (-0.05, -0.02), (-0.02, 0.0),
                   (0.0, 0.02), (0.02, 1.0)]:
        ss = np.isfinite(c_s) & (c_s >= lo) & (c_s < hi)
        bb = np.isfinite(c_b) & (c_b >= lo) & (c_b < hi)
        row(f"市場 {lo*100:+.0f}〜{hi*100:+.0f}%", ss, bb, sig, base)

    # ---- 4. 地合いの言い換えではないかの確認 ----
    print("\n=== ★4. 地合いで切ったあとにも差が残るか(残らなければ新情報なし) ===")
    print("   縦: 地合い / 横: B の余分に落ちた分。各マスは 件数 と 手取り。")
    cut = np.quantile(b_s[np.isfinite(b_s)], [0.33, 0.67])
    colnm = [f"単独急落(〜{cut[0]*100:+.0f}%)",
             f"中間({cut[0]*100:+.0f}〜{cut[1]*100:+.0f}%)",
             f"巻き添え({cut[1]*100:+.0f}%〜)"]
    print(f"{'地合い':10s}" + "".join(f"{c:>24s}" for c in colnm))
    for lab_ in ("攻め", "中立", "守り"):
        cells = []
        for i in range(3):
            lo = -np.inf if i == 0 else cut[i - 1]
            hi = np.inf if i == 2 else cut[i]
            sel = (cls[ts] == lab_) & np.isfinite(b_s) & (b_s >= lo) & (b_s < hi)
            k = int(sel.sum())
            cells.append(f"{k:5,d}件 {sig[0][sel].mean()*100:+6.2f}%" if k >= 50
                         else f"{k:5,d}件      -")
        print(f"{lab_:10s}" + "".join(f"{c:>24s}" for c in cells))

    # ---- 4b. 深さを揃えたうえで B が効くか ----
    print("\n=== ★4b. 深さを -10〜-4% に揃えたうえで、余分に落ちた分で切り直す ===")
    print("   §B の U字は B と深さが混ざっていただけ。揃えると巻き添え側が一方的に良い。")
    dep_s = sret5[ts, cs]
    dep_b = sret5[tb, cb]
    lvl = np.isfinite(dep_s) & (dep_s >= -0.10) & (dep_s < -0.04)
    lvlb = np.isfinite(dep_b) & (dep_b >= -0.10) & (dep_b < -0.04)
    for lo, hi in [(-1, -0.05), (-0.05, -0.02), (-0.02, 0.02), (0.02, 1)]:
        ss = lvl & np.isfinite(b_s) & (b_s >= lo) & (b_s < hi)
        bb = lvlb & np.isfinite(b_b) & (b_b >= lo) & (b_b < hi)
        row(f"余分 {lo*100:+.0f}〜{hi*100:+.0f}%", ss, bb, sig, base)

    # ---- 4c. 深さそのもの ----
    print("\n=== ★4c. 落ちた深さそのもの(D) ===")
    for lo, hi in [(-1, -0.15), (-0.15, -0.10), (-0.10, -0.07),
                   (-0.07, -0.04), (-0.04, 1)]:
        ss = np.isfinite(dep_s) & (dep_s >= lo) & (dep_s < hi)
        bb = np.isfinite(dep_b) & (dep_b >= lo) & (dep_b < hi)
        row(f"深さ {lo*100:+.0f}〜{hi*100:+.0f}%", ss, bb, sig, base)

    # ---- 5. 作戦を組んだ場合 ----
    print("\n=== 5. 作戦として組んだ場合 ===")
    hi_co = np.isfinite(a_s) & (a_s >= 0.08)
    hi_cob = np.isfinite(a_b) & (a_b >= 0.08)
    deep = np.isfinite(dep_s) & (dep_s <= -0.10)
    deepb = np.isfinite(dep_b) & (dep_b <= -0.10)
    notko = cls[ts] != "攻め"
    notkob = cls[tb] != "攻め"
    early = d < C.SPLIT
    eb = dts[tb] < C.SPLIT
    plans = [
        ("そのまま全部", np.ones_like(hi_co), np.ones_like(hi_cob)),
        ("183案: 攻めの日を外す", notko, notkob),
        ("A: 一斉に売られた日だけ", hi_co, hi_cob),
        ("D: 深さ10%以上だけ", deep, deepb),
        ("★A かつ D", hi_co & deep, hi_cob & deepb),
        ("A かつ D かつ 攻めを外す", hi_co & deep & notko, hi_cob & deepb & notkob),
    ]
    for nm, ss, bb in plans:
        row(nm, ss, bb, sig, base,
            f" 年{ss.sum()/((d.max()-d.min())/np.timedelta64(365,'D')):.0f}回")
        row("  └ 前半(〜2014)", ss & early, bb & eb, sig, base)
        row("  └ 後半(2015〜)", ss & ~early, bb & ~eb, sig, base)

    # ---- 5b. 坂か崖か ----
    print("\n=== 5b. しきい値を動かす(採用条件の『坂であって崖でない』の確認) ===")
    r, R = sig[0], sig[1]
    yrs = (d.max() - d.min()) / np.timedelta64(365, "D")
    print(f"  {'一斉のしきい':>12s}{'件数':>8s}{'手取り':>9s}{'前半':>9s}{'後半':>9s}{'年回数':>8s}"
          f"   (深さ10%以上は固定)")
    for t in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20):
        s = np.isfinite(a_s) & (a_s >= t) & deep
        if s.sum() < 50:
            continue
        print(f"  {t:12.0%}{s.sum():8,d}{r[s].mean()*100:+8.2f}%"
              f"{r[s & early].mean()*100:+8.2f}%{r[s & ~early].mean()*100:+8.2f}%"
              f"{s.sum()/yrs:8.0f}")
    print(f"  {'深さのしきい':>12s}{'件数':>8s}{'手取り':>9s}{'前半':>9s}{'後半':>9s}{'年回数':>8s}"
          f"   (一斉8%以上は固定)")
    for t in (0.0, 0.03, 0.05, 0.07, 0.10, 0.13, 0.16, 0.20):
        s = hi_co & np.isfinite(dep_s) & (dep_s <= -t)
        if s.sum() < 50:
            continue
        print(f"  {-t:12.0%}{s.sum():8,d}{r[s].mean()*100:+8.2f}%"
              f"{r[s & early].mean()*100:+8.2f}%{r[s & ~early].mean()*100:+8.2f}%"
              f"{s.sum()/yrs:8.0f}")

    # ---- 6. 局面と2020年2月 ----
    best = hi_co & deep
    w, l = r[best][r[best] > 0], r[best][r[best] <= 0]
    print(f"\n採用案「一斉 かつ 深さ10%以上」 n={int(best.sum()):,} "
          f"期待R {R[best].mean():+.3f}±{2*R[best].std(ddof=1)/np.sqrt(best.sum()):.3f}")
    print(f"  勝ち平均 {w.mean()*100:+.2f}% / 負け平均 {l.mean()*100:+.2f}% "
          f"/ 下位1% {np.percentile(r[best],1)*100:+.2f}% "
          f"/ 下位5% {np.percentile(r[best],5)*100:+.2f}%")
    print("\n=== 6. 「A かつ D」の局面別(切り方が結論を作っていないか) ===")
    print(f"{'局面':32s}{'件数':>8s}{'勝率':>8s}{'手取り':>9s}{'±2':>9s}")
    for nm, a, b in REGIMES:
        sel = best & (d >= np.datetime64(a)) & (d <= np.datetime64(b))
        k = int(sel.sum())
        if k < 30:
            print(f"{nm:32s}{k:8,d}  (件数不足)")
            continue
        Rm = sig[1][sel]
        print(f"{nm:32s}{k:8,d}{(sig[0][sel]>0).mean()*100:7.1f}%"
              f"{sig[0][sel].mean()*100:+8.2f}%"
              f"{2*Rm.std(ddof=1)/np.sqrt(k):9.3f}")

    yr = pd.Series(pd.DatetimeIndex(d).year)[best]
    gy = pd.DataFrame({"y": yr.to_numpy(), "r": r[best]}).groupby("y")["r"] \
        .agg(["size", "mean"])
    neg = [f"{int(i)}({int(v['size'])}件{v['mean']*100:+.1f}%)"
           for i, v in gy.iterrows() if v["mean"] < 0]
    print(f"  負けの年: {', '.join(neg)}  / 全{len(gy)}年中")

    print("\n=== 7. 月別の悪い方から15か月(作戦を通したあと) ===")
    df = pd.DataFrame({"mo": pd.PeriodIndex(d, freq="M"), "r": sig[0], "keep": best})
    g = df.groupby("mo").agg(n=("r", "size"), take=("r", "mean"))
    g = g[g["n"] >= 20].nsmallest(15, "take")
    print(f"{'月':10s}{'元の件数':>9s}{'元の手取り':>11s}{'通過':>7s}{'通過後':>12s}")
    for k, v in g.iterrows():
        sub = df[df["mo"] == k]
        kp = sub[sub["keep"]]
        after = f"{kp['r'].mean()*100:+.2f}%" if len(kp) else "-"
        print(f"{str(k):10s}{int(v['n']):9,d}{v['take']*100:+10.2f}%"
              f"{len(kp):6,d}件{after:>12s}")


if __name__ == "__main__":
    main()
