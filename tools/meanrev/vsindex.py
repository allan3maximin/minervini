#!/usr/bin/env python3
"""型1は指数を買っておくより良いのか(2026-08-19)。

187 で「1回 +3.88% は年利 7.6% にしかならない」「枠は5%しか埋まらない」と出た。
そうなると当然の疑問が出る。**それなら指数を買っておけばいいのでは?**

ここで3つに分けて答えを出す。

  1. 指数の買い持ち
     母集団の全銘柄の日次平均を積んだ等ウェイト指数(market.parquet の idx)。
     TOPIX ではなく「同じ銘柄群を等しく持ったらどうなったか」。比較としてはこちらが正しい。
     ★配当も売買コストもリバランス費用も入っていない理想値なので、指数側に有利。

  2. 型1を単独で回した場合(187 の結果)
     資金の95%が寝ている。

  3. 型1 + 寝ている資金を指数に置く
     これが本命。空き枠のぶんは指数を持ち、シグナルが出たら指数を N分の1 売って
     その銘柄を買い、出たら指数に戻す。
     指数を土台にして、荒れた日だけ型1に乗り換える形。

さらに「1回ごとに指数に勝っているか」も測る。
同じ日に入って同じ日に出た場合の指数の動きを引く。

    python tools/meanrev/vsindex.py

★指数の売買コストは入れていない(片道0.05%程度だが、乗り換え回数が少ないので影響は小さい)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402
from meanrev.regime_filter import market  # noqa: E402
from meanrev.sizing import trades  # noqa: E402

NS = (5, 8, 10, 15, 20, 30)


def perf(equity, dts, label):
    yrs = (dts[-1] - dts[0]).astype("timedelta64[D]").astype(float) / 365.25
    cagr = equity[-1] ** (1 / yrs) - 1
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    mo = pd.PeriodIndex(dts, freq="M")
    s = pd.Series(equity, index=mo).groupby(level=0).last().pct_change()
    return dict(label=label, cagr=cagr, mdd=dd.min(), at=str(dts[int(dd.argmin())])[:10],
                worst=s.min(), worst_at=str(s.idxmin()), final=equity[-1])


def run(ts, ex, r, order, n_slot, idxret=None):
    """idxret を渡すと、空いている枠のぶんは指数に置かれる。"""
    T = C.dates().size
    by_day = [[] for _ in range(T)]
    for i in range(len(ts)):
        by_day[int(ts[i])].append(i)
    for d in range(T):
        if len(by_day[d]) > 1:
            by_day[d].sort(key=lambda i: order[i])

    back_cash = np.zeros(T)
    back_inv = np.zeros(T)
    back_cnt = np.zeros(T, dtype=int)

    cash, inv, held = 1.0, 0.0, 0
    equity = np.zeros(T)
    used = np.zeros(T)

    for d in range(T):
        # 寝ている資金は指数と一緒に動く
        if idxret is not None and d > 0 and np.isfinite(idxret[d]) and cash > 0:
            cash *= 1.0 + idxret[d]

        if back_cnt[d]:
            cash += back_cash[d]
            inv -= back_inv[d]
            held -= int(back_cnt[d])
            if inv < 1e-12:
                inv = 0.0

        free = n_slot - held
        for i in by_day[d]:
            if free <= 0:
                continue
            amt = min((cash + inv) / n_slot, cash)
            if amt <= 1e-9:
                continue
            e = int(ex[i])
            cash -= amt
            inv += amt
            back_cash[e] += amt * (1.0 + r[i])
            back_inv[e] += amt
            back_cnt[e] += 1
            free -= 1
            held += 1

        used[d] = held
        equity[d] = cash + inv

    return equity, used


def main() -> None:
    ts, cs, ex, r, dep, liq = trades()
    dts = C.dates()
    m = market()
    lvl = m["idx"].to_numpy(dtype=float)
    idxret = np.full(len(lvl), np.nan)
    idxret[1:] = lvl[1:] / lvl[:-1] - 1.0

    order = np.argsort(np.argsort(dep))

    # ---------------------------------------------------- 1. 指数の買い持ち
    bh = lvl / lvl[0]
    p_idx = perf(bh, dts, "指数を買い持ち")

    print("=== 1. 比べる相手: 母集団の等ウェイト指数を買い持ち ===")
    print(f"  {dts[0]} 〜 {dts[-1]}")
    print(f"  年利 {p_idx['cagr']*100:.1f}%  最大の落ち込み {p_idx['mdd']*100:.1f}%"
          f" ({p_idx['at']})  最悪の月 {p_idx['worst']*100:.1f}% ({p_idx['worst_at']})"
          f"  {p_idx['final']:.1f}倍")
    print("  ★配当も売買コストも入っていない理想値。指数側に有利な比較")

    # ---------------------------------------------------- 2/3. 型1
    print("\n=== 2. 型1を単独で回す(現金は寝かせる) vs 3. 空き枠を指数に置く ===")
    print(f"{'枠':>4s}{'単独:年利':>11s}{'落ち込み':>10s}{'26年で':>9s}"
          f"{'  |':>3s}{'指数併用:年利':>15s}{'落ち込み':>10s}{'26年で':>9s}"
          f"{'指数との差':>12s}")
    rows = []
    for n in NS:
        eq_a, used = run(ts, ex, r, order, n, idxret=None)
        eq_b, _ = run(ts, ex, r, order, n, idxret=idxret)
        pa = perf(eq_a, dts, f"単独N={n}")
        pb = perf(eq_b, dts, f"併用N={n}")
        rows.append((n, pa, pb))
        print(f"{n:>4d}{pa['cagr']*100:>10.1f}%{pa['mdd']*100:>9.1f}%"
              f"{pa['final']:>8.1f}倍   |{pb['cagr']*100:>14.1f}%"
              f"{pb['mdd']*100:>9.1f}%{pb['final']:>8.1f}倍"
              f"{(pb['cagr']-p_idx['cagr'])*100:>+11.1f}%")

    # ---------------------------------------------------- 4. 1回ごとに指数に勝ったか
    print("\n=== 4. 1回ごとに見て、指数に勝っているか ===")
    print("   同じ日に入って同じ日に出た場合の指数の動きを引く")
    ir = lvl[ex] / lvl[ts] - 1.0
    exc = r - ir
    hold = ex - ts
    print(f"  型1の手取り     {r.mean()*100:+.2f}%  (勝率 {(r>0).mean()*100:.1f}%)")
    print(f"  同じ期間の指数  {ir.mean()*100:+.2f}%")
    print(f"  差(超過)      {exc.mean()*100:+.2f}%  "
          f"(指数に勝った割合 {(exc>0).mean()*100:.1f}%)")
    print(f"  持っていた日数の平均 {hold.mean():.1f}日")

    print("\n  局面ごとの超過:")
    y = pd.DatetimeIndex(dts[ts]).year
    g = pd.DataFrame({"y": y, "r": r, "i": ir, "e": exc}).groupby("y").agg(
        n=("r", "size"), r=("r", "mean"), i=("i", "mean"), e=("e", "mean"))
    print(f"    {'年':6s}{'件数':>7s}{'型1':>9s}{'指数':>9s}{'差':>9s}")
    for k, v in g.iterrows():
        print(f"    {k:<6d}{int(v['n']):>6d}件{v['r']*100:>8.2f}%"
              f"{v['i']*100:>8.2f}%{v['e']*100:>+8.2f}%")

    # ---------------------------------------------------- 5. 年別の勝ち負け(併用N=15)
    print("\n=== 5. 年ごとに指数と併用案(枠15)を並べる ===")
    eq_b, _ = run(ts, ex, r, order, 15, idxret=idxret)
    yy = pd.DatetimeIndex(dts).year
    sa = pd.Series(bh, index=yy).groupby(level=0).last().pct_change()
    sb = pd.Series(eq_b, index=yy).groupby(level=0).last().pct_change()
    print(f"    {'年':6s}{'指数':>9s}{'併用':>9s}{'差':>9s}")
    win = 0
    tot = 0
    for k in sa.index:
        a, b = sa[k], sb[k]
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        tot += 1
        win += b > a
        print(f"    {k:<6d}{a*100:>+8.1f}%{b*100:>+8.1f}%{(b-a)*100:>+8.1f}%")
    print(f"  勝った年 {win}/{tot}")


if __name__ == "__main__":
    main()
