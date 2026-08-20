#!/usr/bin/env python3
"""「崩れ始めて何日目か」と「52週安値を更新した銘柄の数」を型1の入口に使えるか(2026-08-19)。

182 から一貫して残っている穴は 2020年2月だけで、それも月の後半2日に集中している。
184・185 で足した材料(一斉に売られた割合 / 落ちた深さ / 荒れ具合)は、どれも
**「今どれだけ売られているか」**しか測っていない。2020年2月と2024年8月は
どちらも同じくらい売られていて、前者は -13.7%、後者は +8.4% だった。
つまり「売られている量」では二つを分けられない。

ここでは向きと経過を測る材料を2つ足す。

  P 崩れ始めて何日目か
      指数が250日高値から何%落ちたか(下落幅)と、
      その状態が何日続いているか(経過日数)。
      2020年2月28日は「崩れて2日目」、2020年3月末は「崩れて1か月目」。
      荒れ具合は振れ幅なので上下どちらでも跳ねるが、これは方向と時間を持つ。

  N 52週安値を更新した銘柄の割合
      183 で「安値の系列が無い」と書いたのは market.parquet の話で、
      data/audit_cache/l250.npy(250日の安値)は普通にある。
      押し目では安値更新は増えない(みんな上昇トレンドの中にいる)。
      本物の崩れでは一気に増える。いま使っている「一斉に売られた割合」は
      RSI基準なので深さの情報が入っておらず、これは別の軸になるはず。

    python tools/meanrev/crashphase.py

★すべて当日の終値までで計算している。250日高値も経過日数も過去だけを見ている。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402
from meanrev.collateral import build, measures, row  # noqa: E402
from meanrev.regime_filter import REGIMES, market  # noqa: E402
from meanrev.volfilter import CO_MIN, DEPTH, volmeasures  # noqa: E402

RANK_MIN = 0.80   # 185で採用: 荒れ具合が1年内で0.80以上
DD_ON = -0.05     # 「崩れている」とみなす下落幅


def phase(m: pd.DataFrame):
    """P: 指数の下落幅と、崩れが続いている日数。両方とも当日までの値。"""
    lvl = m["idx"].to_numpy(dtype=float)
    top = pd.Series(lvl).rolling(250, min_periods=100).max().to_numpy()
    with np.errstate(all="ignore"):
        dd = lvl / top - 1.0

    # 崩れが何日続いているか(切れたら0に戻る)
    on = np.isfinite(dd) & (dd <= DD_ON)
    days = np.zeros(len(m), dtype=int)
    run = 0
    for i, v in enumerate(on):
        run = run + 1 if v else 0
        days[i] = run
    return dd, days


def newlow(pop: np.ndarray):
    """N: 母集団のうち52週安値ぎわにいる割合と、52週高値ぎわにいる割合の差。"""
    close = np.asarray(C.load("close", src=True), dtype=np.float32)
    lo = np.asarray(C.load("l250", src=True), dtype=np.float32)
    hi = np.asarray(C.load("h250", src=True), dtype=np.float32)
    with np.errstate(all="ignore"):
        atlow = pop & np.isfinite(lo) & (lo > 0) & (close <= 1.02 * lo)
        athigh = pop & np.isfinite(hi) & (hi > 0) & (close >= 0.98 * hi)
    denom = pop.sum(axis=1).astype(np.float64)
    with np.errstate(all="ignore"):
        nl = np.where(denom >= 50, atlow.sum(axis=1) / denom, np.nan)
        nh = np.where(denom >= 50, athigh.sum(axis=1) / denom, np.nan)
    return nl, nh, nh - nl


def main() -> None:
    m = market()
    mvol, rank, spike, rv = volmeasures(m)
    pop, co, mret5, excess, sret5 = measures()
    sig, base = build(pop)
    r, R, ts, cs = sig
    tb = base[2]
    dts = C.dates()

    dd, days = phase(m)
    nl, nh, diff = newlow(pop)

    keep = (co[ts] >= CO_MIN) & np.isfinite(co[ts]) \
        & (sret5[ts, cs] <= DEPTH) & np.isfinite(sret5[ts, cs]) \
        & np.isfinite(rank[ts]) & (rank[ts] >= RANK_MIN)
    keep_b = np.isfinite(rank[tb]) & (rank[tb] >= RANK_MIN)
    print("185の採用案(一斉8%以上 かつ 深さ10%以上 かつ 荒れ具合0.80以上)"
          f" → {int(keep.sum()):,}件 / これを土台にする")
    print(f"指数の下落幅の中央値 {np.nanmedian(dd)*100:.1f}% / "
          f"52週安値ぎわの割合の中央値 {np.nanmedian(nl)*100:.2f}%")

    def k(label, d, extra=""):
        row(label, keep & d[ts], keep_b & d[tb], sig, base, extra)

    # ------------------------------------------------ P1 下落幅
    print("\n=== P1. 指数が250日高値から何%落ちているか ===")
    for lo, hi in [(-9.0, -0.20), (-0.20, -0.12), (-0.12, -0.07),
                   (-0.07, -0.03), (-0.03, 9.0)]:
        k(f"{lo:+.0%}〜{hi:+.0%}", np.isfinite(dd) & (dd >= lo) & (dd < hi))

    # ------------------------------------------------ P2 経過日数
    print(f"\n=== P2. 下落幅{DD_ON:+.0%}以下が何日続いているか(崩れ始めてから何日目) ===")
    for lo, hi, nm in [(0, 1, "崩れていない(0日)"), (1, 4, "1〜3日目"),
                       (4, 11, "4〜10日目"), (11, 31, "11〜30日目"),
                       (31, 61, "31〜60日目"), (61, 10**9, "61日目以降")]:
        k(nm, (days >= lo) & (days < hi))

    # ------------------------------------------------ N
    print("\n=== N1. 52週安値ぎわにいる銘柄の割合 ===")
    for lo, hi in [(0.0, 0.002), (0.002, 0.01), (0.01, 0.03),
                   (0.03, 0.07), (0.07, 1.01)]:
        k(f"{lo:.1%}〜{hi:.1%}", np.isfinite(nl) & (nl >= lo) & (nl < hi))

    print("\n=== N2. 52週高値ぎわ − 52週安値ぎわ(新高値と新安値の差) ===")
    for lo, hi in [(-1.01, -0.03), (-0.03, 0.0), (0.0, 0.03),
                   (0.03, 0.10), (0.10, 1.01)]:
        k(f"{lo:+.0%}〜{hi:+.0%}", np.isfinite(diff) & (diff >= lo) & (diff < hi))

    # ------------------------------------------------ 本題: 2020-02 と 2024-08
    print("\n=== ★ 2020年2月と2024年8月を、この2つの材料で分けられるか ===")
    print("   どちらも同じくらい売られた月。前者 -13.7%、後者 +8%台")
    print(f"{'日付':12s}{'指数の日次':>10s}{'高値から':>9s}{'崩れ何日目':>11s}"
          f"{'安値ぎわ':>9s}{'高値-安値':>10s}{'入り':>7s}")
    for label, a, b in [("2020-02", "2020-02-01", "2020-03-05"),
                        ("2024-08", "2024-07-29", "2024-08-16")]:
        print(f"--- {label} ---")
        w = (dts >= np.datetime64(a)) & (dts <= np.datetime64(b))
        lvl = m["idx"].to_numpy(dtype=float)
        dr = np.full(len(m), np.nan)
        dr[1:] = lvl[1:] / lvl[:-1] - 1.0
        for i in np.nonzero(w)[0]:
            cnt = int(((ts == i) & keep).sum())
            if cnt == 0 and abs(dr[i]) < 0.015:
                continue
            print(f"{str(dts[i]):12s}{dr[i]*100:+9.2f}%{dd[i]*100:+8.1f}%"
                  f"{days[i]:>10d}日{nl[i]*100:>8.2f}%{diff[i]*100:>+9.2f}%"
                  f"{cnt:>6,d}件")

    # ------------------------------------------------ 組み合わせ
    print("\n=== ★ 組み合わせ: 崩れ始めの数日を外すと2020年2月は消えるか ===")
    for cut in (3, 5, 8, 12):
        d = (days == 0) | (days > cut)
        row(f"崩れて{cut}日目までは休む", keep & d[ts], keep_b & d[tb], sig, base)
        row(f"  └休んだぶん(捨てる側)", keep & ~d[ts], keep_b & ~d[tb], sig, base)

    print("\n=== ★ 組み合わせ: 安値ぎわが多い日を外すと2020年2月は消えるか ===")
    for cut in (0.03, 0.05, 0.08):
        d = np.isfinite(nl) & (nl < cut)
        row(f"安値ぎわ{cut:.0%}以上の日は休む", keep & d[ts], keep_b & d[tb], sig, base)
        row(f"  └休んだぶん(捨てる側)", keep & ~d[ts], keep_b & ~d[tb], sig, base)

    # ------------------------------------------------ 採用候補の検算
    print("\n=== 採用候補を前半後半・年別・2020年月別で検算 ===")
    early = dts[ts] < C.SPLIT
    eb = dts[tb] < C.SPLIT
    cands = {
        "土台のみ(185)": np.ones(len(dts), dtype=bool),
        "+崩れて5日目までは休む": (days == 0) | (days > 5),
        "+崩れて8日目までは休む": (days == 0) | (days > 8),
        "+安値ぎわ5%以上は休む": np.isfinite(nl) & (nl < 0.05),
        "+両方": ((days == 0) | (days > 5)) & np.isfinite(nl) & (nl < 0.05),
    }
    for nm, d in cands.items():
        sel = keep & d[ts]
        row(nm, sel, keep_b & d[tb], sig, base, f" 年{sel.sum()/23:.0f}回")
        row("  └前半(〜2014)", sel & early, keep_b & d[tb] & eb, sig, base)
        row("  └後半(2015〜)", sel & ~early, keep_b & d[tb] & ~eb, sig, base)
        y = pd.DatetimeIndex(dts[ts[sel]]).year
        g = pd.DataFrame({"y": y, "r": r[sel]}).groupby("y").agg(
            n=("r", "size"), t=("r", "mean"))
        bad = g[g["t"] < 0]
        print("      負けた年: " + (" / ".join(
            f"{i}({int(v['n'])}件{v['t']*100:+.1f}%)" for i, v in bad.iterrows())
            or "なし") + f"  (全{len(g)}年)")
        mo = pd.PeriodIndex(dts[ts[sel]], freq="M")
        f2 = mo.astype(str) == "2020-02"
        print(f"      2020年2月: {int(f2.sum()):,}件 "
              f"{r[sel][f2].mean()*100:+.2f}%" if f2.sum() else
              "      2020年2月: 0件")


if __name__ == "__main__":
    main()
