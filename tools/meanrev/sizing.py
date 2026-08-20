#!/usr/bin/env python3
"""建玉数を決める(2026-08-19)。

184〜186 で測ってきた「1回 +3.88%」は1回あたりの平均で、
**資金がいくら要るか、同時に何本持つか、そもそも全部買えるか**が入っていない。

型1のシグナルは固まって出る。2024年8月5日は1日で85件、2020年2月28日は31件。
普通の日はゼロか1〜2件。資金は有限なので暴落日のシグナルは全部は買えない。
つまり「1回いくら」から「口座がいくら増えるか」への翻訳がまだ済んでいない。

182・184・185・186 と4回続けて 2020年2月を避ける方法が見つからなかった。
残る道は「起きる前提で、起きても死なないサイズにする」しかない。
そのために、同時に持つ本数 N を変えて資産曲線を26年ぶん回し、
**年利と最大の落ち込みのトレードオフ表**を作る。

    python tools/meanrev/sizing.py

やり方:
  ・資産の N分の1 を1本に入れる(常に同じ割合。増えれば増やし、減れば減らす)
  ・空き枠より多くシグナルが出た日は、決めた順で上から埋める(3通り比べる)
  ・買えなかったぶんは見送り(翌日に持ち越さない。実際そうするしかない)
  ・売買コストは1回の損益に既に入っている(往復0.3%)

★注意: これは「同じ日に同じ理由で買う」戦略なので、本数を増やしても
中身は分散しない。N を増やす効果は主に『1日に入る額の上限』として効く。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402
from meanrev import exits as E  # noqa: E402
from meanrev.collateral import measures  # noqa: E402
from meanrev.volfilter import CO_MIN, DEPTH, volmeasures  # noqa: E402
from meanrev.regime_filter import market  # noqa: E402

LIQ = 3e8
STOP = "1.5ATR(3-12%)"
RANK_MIN = 0.80
NS = (3, 5, 8, 10, 15, 20, 30, 50)


def trades():
    """185の採用案の売買を (入った日, 出た日, 銘柄, 損益率, 深さ, 売買代金) で返す。"""
    close = np.asarray(C.load("close", src=True), dtype=np.float32)
    rsi = np.asarray(C.load("rsi2"))
    tv = np.asarray(C.load("tv20med"))
    sd_mat = C.stop_size(STOP)

    m = market()
    _, rank, _, _ = volmeasures(m)
    pop, co, _, _, sret5 = measures()

    bad = C.bad_bar()
    T = bad.shape[0]
    win = np.zeros_like(bad)
    for k in range(0, E.MAXH + 1):
        win[: T - k] |= bad[k:]

    sel = pop & C.tradable() & np.isfinite(rsi) & (np.asarray(rsi) < 5)
    sel = C.dedup(sel, E.MAXH)
    ts, cs = np.nonzero(sel)
    ex = E.exit_index(ts, cs, np.isfinite(rsi) & (rsi > 70), E.MAXH)
    sd = sd_mat[ts, cs].astype(np.float64)
    ok = (ex < T) & ~win[ts, cs] & np.isfinite(sd) & (sd > 0)
    ts, cs, ex = ts[ok], cs[ok], ex[ok]

    r = (close[ex, cs].astype(np.float64) * (1 - C.COST_ONEWAY)) / \
        (close[ts, cs].astype(np.float64) * (1 + C.COST_ONEWAY)) - 1.0

    dep = sret5[ts, cs]
    keep = np.isfinite(r) & np.isfinite(co[ts]) & (co[ts] >= CO_MIN) \
        & np.isfinite(dep) & (dep <= DEPTH) \
        & np.isfinite(rank[ts]) & (rank[ts] >= RANK_MIN)
    ts, cs, ex, r, dep = ts[keep], cs[keep], ex[keep], r[keep], dep[keep]
    liq = tv[ts, cs].astype(np.float64)
    return ts, cs, ex, r, dep, liq


def run(ts, cs, ex, r, order, n_slot):
    """資産曲線を日次で返す。order は同じ日の中での優先順(小さいほど先に買う)。"""
    T = C.dates().size
    by_day = [[] for _ in range(T)]
    for i in range(len(ts)):
        by_day[int(ts[i])].append(i)
    for d in range(T):
        if len(by_day[d]) > 1:
            by_day[d].sort(key=lambda i: order[i])

    back_cash = np.zeros(T)   # その日に戻ってくる現金(損益込み)
    back_inv = np.zeros(T)    # その日に拘束が解ける元本
    back_cnt = np.zeros(T, dtype=int)

    cash, inv, held = 1.0, 0.0, 0
    equity = np.zeros(T)
    used = np.zeros(T)
    taken = missed = 0

    for d in range(T):
        if back_cnt[d]:
            cash += back_cash[d]
            inv -= back_inv[d]
            held -= int(back_cnt[d])
            if inv < 1e-12:
                inv = 0.0

        free = n_slot - held
        for i in by_day[d]:
            if free <= 0:
                missed += 1
                continue
            eq = cash + inv
            amt = min(eq / n_slot, cash)
            if amt <= 1e-9:
                missed += 1
                continue
            e = int(ex[i])
            cash -= amt
            inv += amt
            back_cash[e] += amt * (1.0 + r[i])
            back_inv[e] += amt
            back_cnt[e] += 1
            free -= 1
            held += 1
            taken += 1

        used[d] = held
        equity[d] = cash + inv

    return equity, used, taken, missed


def stats(equity, used, taken, missed, n_slot, dts):
    yrs = (dts[-1] - dts[0]).astype("timedelta64[D]").astype(float) / 365.25
    cagr = equity[-1] ** (1 / yrs) - 1
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    mdd = dd.min()
    i = int(dd.argmin())
    mo = pd.PeriodIndex(dts, freq="M")
    s = pd.Series(equity, index=mo)
    mret = s.groupby(level=0).last().pct_change()
    worst = mret.nsmallest(3)
    feb = mret.get(pd.Period("2020-02"), np.nan)
    return dict(
        n=n_slot, cagr=cagr, mdd=mdd, mdd_at=str(dts[i])[:10],
        worst=worst, feb=feb, taken=taken, missed=missed,
        fill=used.mean() / n_slot, final=equity[-1],
    )


def main() -> None:
    ts, cs, ex, r, dep, liq = trades()
    dts = C.dates()
    print(f"185の採用案: {len(ts):,}件 / 1回あたり平均 {r.mean()*100:+.2f}% "
          f"勝率 {(r > 0).mean()*100:.1f}%")

    per_day = pd.Series(1, index=pd.DatetimeIndex(dts[ts])).groupby(level=0).sum()
    print(f"シグナルが出た日 {len(per_day):,}日 / 1日あたり "
          f"中央値 {per_day.median():.0f}件 上位1% {per_day.quantile(0.99):.0f}件 "
          f"最大 {per_day.max():.0f}件")
    print("多かった日: " + " / ".join(
        f"{str(k)[:10]} {v}件" for k, v in per_day.nlargest(5).items()))

    orders = {
        "深く落ちた順": np.argsort(np.argsort(dep)),
        "浅い順": np.argsort(np.argsort(-dep)),
        "売買代金が大きい順": np.argsort(np.argsort(-liq)),
    }

    for onm, order in orders.items():
        print(f"\n=== 埋める順: {onm} ===")
        print(f"{'枠':>4s}{'年利':>9s}{'最大の落ち込み':>14s}{'いつ':>12s}"
              f"{'2020年2月':>10s}{'最悪の月':>10s}{'買えた':>9s}"
              f"{'見送り':>9s}{'枠の埋まり':>10s}{'26年で':>9s}")
        for n in NS:
            eq, used, taken, missed = run(ts, cs, ex, r, order, n)
            st = stats(eq, used, taken, missed, n, dts)
            w = st["worst"]
            print(f"{n:>4d}{st['cagr']*100:>8.1f}%{st['mdd']*100:>13.1f}%"
                  f"{st['mdd_at']:>12s}{st['feb']*100:>9.1f}%"
                  f"{w.iloc[0]*100:>9.1f}%{st['taken']:>8,d}件"
                  f"{st['missed']:>8,d}件{st['fill']*100:>9.1f}%"
                  f"{st['final']:>8.1f}倍")

    # 最悪の月の並び(代表として深く落ちた順・枠10)
    print("\n=== 悪かった月(深く落ちた順・枠10本) ===")
    eq, used, taken, missed = run(ts, cs, ex, r, orders["深く落ちた順"], 10)
    st = stats(eq, used, taken, missed, 10, dts)
    mo = pd.PeriodIndex(dts, freq="M")
    s = pd.Series(eq, index=mo).groupby(level=0).last().pct_change()
    for k, v in s.nsmallest(8).items():
        print(f"  {str(k)}  {v*100:+7.2f}%")
    print("  --- 良かった月 ---")
    for k, v in s.nlargest(4).items():
        print(f"  {str(k)}  {v*100:+7.2f}%")

    print("\n=== 年ごと(深く落ちた順・枠10本) ===")
    y = pd.DatetimeIndex(dts).year
    sy = pd.Series(eq, index=y).groupby(level=0).last().pct_change()
    for k, v in sy.items():
        if np.isfinite(v):
            print(f"  {k}  {v*100:+7.1f}%")


if __name__ == "__main__":
    main()
