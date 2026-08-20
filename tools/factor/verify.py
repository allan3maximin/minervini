#!/usr/bin/env python3
"""勝ったように見えた軸を、採用の4条件で検算する(2026-08-19)。

scan.py と combo.py で「荒れ具合が小さい側」が一番マシに見えた。
だが見えただけで採用するわけにはいかない。log.md の判定基準に当てて潰す。

  条件1 前半(〜2014)と後半(2015〜)で向きが同じか
  条件2 決め方を変えても向きが同じか(荒れ具合の窓・売買代金の下限・買う日のずらし)
  条件3 分位が坂になっているか、それとも端だけの崖か
  条件4 効果の大きさが、ぶれ幅(±2)より大きいか

さらに「指数に勝つ」が目的なので、超過ぶんそのものを月次で取って
  ・平均が0から離れているか(ぶれ幅の2倍を超えているか)
  ・一発屋ではないか(良かった年を抜いても残るか)
も見る。

    python tools/factor/verify.py

★このデータには今も上場している会社しか入っていない(消えた銘柄0件)。
  「市場に何%勝った」の絶対値は信用できない。信用できるのは同じ土俵の中の並び順。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factor import core as F  # noqa: E402
from factor.combo import hold, pct  # noqa: E402


def vol_window(close: np.ndarray, me: np.ndarray, k: int) -> np.ndarray:
    """直近k営業日の日次リターンの散らばり(荒れ具合)。"""
    T, N = close.shape
    dret = np.full((T, N), np.nan, dtype=np.float32)
    with np.errstate(all="ignore"):
        dret[1:] = (close[1:] / close[:-1] - 1.0).astype(np.float32)
    dret[np.abs(dret) > F.BAD_JUMP] = np.nan
    out = np.full((len(me), N), np.nan)
    for i, t in enumerate(me):
        a = max(t - k + 1, 0)
        with np.errstate(all="ignore"):
            out[i] = np.nanstd(dret[a:t + 1], axis=0)
    return out


def band(x: np.ndarray) -> float:
    """月次の列のぶれ幅(±2)。0から離れているかを見るのに使う。"""
    n = int(np.isfinite(x).sum())
    return 2.0 * float(np.nanstd(x, ddof=1)) / np.sqrt(max(n, 1))


def main() -> None:
    dts = F.dates()
    close = F.close_f64()
    me = F.month_ends(dts)
    bad = F.bad_bar(close)
    mdts = dts[me[1:]]
    early = mdts < F.SPLIT

    print("★このデータには今も上場している会社しか入っていない(消えた銘柄0件)。")
    print("  絶対値ではなく、同じ土俵の中の並び順だけを見る。\n")

    # ---------------------------------------------------------- 条件1・4
    print("=== 条件1・4: 前半と後半で向きが同じか / ぶれ幅より大きいか ===")
    print("   荒れ具合(60日)が下位◯%だけを持ち、母集団を全部持った場合との差を月次で取る")
    r = F.fwd(close, me, bad, delay=1)
    u = F.univ(me)[:-1]
    sd60 = vol_window(close, me, 60)[:-1]
    p60 = pct(sd60, u)
    mkt, _ = hold(u, r)

    print(f"{'持ち方':22s}{'超過の月平均':>13s}{'ぶれ幅(±2)':>12s}"
          f"{'前半':>9s}{'後半':>9s}{'判定':>8s}")
    keep = {}
    for th in (0.50, 0.30, 0.20, 0.10, 0.05):
        c = p60 < th
        m, to = hold(u & c, r)
        cst = F.COST_ONEWAY * 2 * to
        exc = (m - cst) - mkt
        b = band(exc)
        e1, e2 = np.nanmean(exc[early]), np.nanmean(exc[~early])
        ok = (np.sign(e1) == np.sign(e2)) and (abs(np.nanmean(exc)) > b)
        keep[th] = (m, to, exc)
        print(f"  下位{th*100:>2.0f}%だけ持つ      {np.nanmean(exc)*100:>+11.3f}%"
              f"{b*100:>11.3f}%{e1*100:>+8.3f}%{e2*100:>+8.3f}%"
              f"{'合格' if ok else '不合格':>8s}")

    # ---------------------------------------------------------- 条件2
    print("\n=== 条件2: 決め方を変えても向きが同じか(下位5%だけ持つ) ===")
    print(f"{'変えたところ':30s}{'年利':>8s}{'落ち込み':>10s}"
          f"{'超過の月平均':>13s}{'前半':>9s}{'後半':>9s}")

    def one(label, sc, uu, rr):
        p = pct(sc, uu)
        c = p < 0.05
        m, to = hold(uu & c, rr)
        mk, _ = hold(uu, rr)
        cst = F.COST_ONEWAY * 2 * to
        s = F.summary(m, mdts, cost=cst)
        exc = (m - cst) - mk
        print(f"{label:30s}{s['cagr']*100:>7.1f}%{s['mdd']*100:>9.1f}%"
              f"{np.nanmean(exc)*100:>+12.3f}%"
              f"{np.nanmean(exc[early])*100:>+8.3f}%"
              f"{np.nanmean(exc[~early])*100:>+8.3f}%")

    one("基準(60日・1億円・翌日買い)", sd60, u, r)
    for k in (20, 120, 250):
        one(f"荒れ具合の窓を{k}日にする", vol_window(close, me, k)[:-1], u, r)
    for liq, nm in ((3e8, "3億円"), (1e9, "10億円")):
        uu = F.univ(me, liq_min=liq)[:-1]
        one(f"売買代金の下限を{nm}にする", sd60, uu, r)
    for d, nm in ((0, "月末の終値で買う(先読み)"), (2, "2日ずらして買う")):
        one(f"{nm}", sd60, u, F.fwd(close, me, bad, delay=d))
    # 値幅(ATR%)という別の測り方
    atr = np.asarray(F.load("atr"), dtype=np.float32)
    with np.errstate(all="ignore"):
        atrp = atr[me] / np.where(close[me] > 0, close[me], np.nan)
    one("荒れ具合を値幅(ATR%)で測る", np.asarray(atrp, dtype=np.float64)[:-1], u, r)

    # ---------------------------------------------------------- 条件3
    print("\n=== 条件3: 坂か、それとも端だけの崖か ===")
    print("   荒れ具合で10等分したときの年利。左が退屈な株、右が荒い株")
    q, memb = F.quantile_returns(sd60, u, r, F.NQ)
    cg = []
    for k in range(F.NQ):
        to = F.turnover(memb, k)
        s = F.summary(q[:, k], mdts,
                      cost=F.COST_ONEWAY * 2 * (to if np.isfinite(to) else 1.0))
        cg.append(s["cagr"] * 100)
    print("   " + " ".join(f"D{i+1}:{v:5.1f}%" for i, v in enumerate(cg)))
    lo = np.mean(cg[:9])
    print(f"   D1〜D9の平均 {lo:.1f}%  D10 {cg[9]:.1f}%  "
          f"D1とD9の差 {cg[0]-cg[8]:+.1f}%")
    print("   → D1〜D9はほぼ横並びで、D10だけが落ちている。坂ではなく崖。")
    print("     正しい言い方は『低ボラが良い』ではなく『一番荒い10%が毒』。")

    # ---------------------------------------------------------- 一発屋か
    print("\n=== 一発屋ではないか(下位5%だけ持つ、年ごとの超過) ===")
    m, to, exc = keep[0.05]
    y = pd.DatetimeIndex(mdts).year
    ea = pd.Series(np.where(np.isfinite(mkt), mkt, 0.0), index=y)
    eb = pd.Series(np.where(np.isfinite(m), m, 0.0) - F.COST_ONEWAY * 2 * to,
                   index=y)
    ga = ea.groupby(level=0).apply(lambda s: (1 + s).prod() - 1)
    gb = eb.groupby(level=0).apply(lambda s: (1 + s).prod() - 1)
    d = (gb - ga) * 100
    print(f"    {'年':6s}{'市場':>9s}{'下位5%':>9s}{'差':>9s}")
    for k in ga.index:
        print(f"    {k:<6d}{ga[k]*100:>+8.1f}%{gb[k]*100:>+8.1f}%{d[k]:>+8.1f}%")
    ds = d.sort_values()
    print(f"  勝った年 {int((d>0).sum())}/{len(d)}  差の中央値 {d.median():+.1f}%")
    print(f"  一番良かった年を抜いた平均 {ds[:-1].mean():+.1f}%  "
          f"上位3年を抜いた平均 {ds[:-3].mean():+.1f}%")

    # ---------------------------------------------------------- まとめ
    print("\n=== 検算のまとめ ===")
    s5 = F.summary(m, mdts, cost=F.COST_ONEWAY * 2 * to)
    sm = F.summary(mkt, mdts)
    print(f"  市場(全部持つ)      年利 {sm['cagr']*100:5.1f}%  "
          f"落ち込み {sm['mdd']*100:6.1f}%  {sm['final']:5.1f}倍")
    print(f"  荒れ具合が下位5%     年利 {s5['cagr']*100:5.1f}%  "
          f"落ち込み {s5['mdd']*100:6.1f}%  {s5['final']:5.1f}倍  "
          f"入替 {to*100:.0f}%")
    print(f"  年利の差 {(s5['cagr']-sm['cagr'])*100:+.1f}%  "
          f"落ち込みの差 {(s5['mdd']-sm['mdd'])*100:+.1f}%")


if __name__ == "__main__":
    main()
