#!/usr/bin/env python3
"""型1の「条件そのものが結果を潰していないか」を確かめる回(2026-08-19)。

179 で固定日数のイグジットだけを測ったが、平均回帰の売り方としては
固定日数は素直な形ではない(反発し終わっても持ち続けて戻される)。
ここでは前提を1つずつ外して、どれが効いていたのかを分ける。

    python tools/meanrev/exits.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402

MAXH = 10  # 反発待ちの上限。これを超えたら降りる


def _close() -> np.ndarray:
    return np.asarray(C.load("close", src=True), dtype=np.float32)


def entries(slope: float, liq: float, sig: str, last: bool = False):
    """シグナル日を (日index, 銘柄index) の並びで返す。

    ★last=True は使ってはいけない。未来を見ている。★
    「連続して条件を満たした期間の最後の日 = 一番売られた日」のつもりで
    「翌日は条件を満たさない日」を選ぶ実装にしたが、これは
    **翌日にRSI(2)が5以上へ戻る = 翌日上げた日** を選んでいるだけだった。
    実測すると last=True の日の翌日リターンは平均 +2.207%、
    プラス率 94.5%(通常の日は +0.270% / 52.0%)。
    期待Rが +0.023 → +0.319 と10倍以上に化けたのはこれが理由。
    残してあるのは、同じ間違いをもう一度やらないための記録。
    """
    pop = C.population(slope, liq) & C.tradable()
    if sig == "rsi5":
        rsi = np.asarray(C.load("rsi2"))
        m = pop & np.isfinite(rsi) & (rsi < 5)
    elif sig == "streak4":
        m = pop & (np.asarray(C.load("downstreak")) >= 4)
    elif sig == "bb":
        m = pop & np.asarray(C.load("bblow"), dtype=bool)
    else:
        raise ValueError(sig)

    if last:
        nxt = np.zeros_like(m)
        nxt[:-1] = m[1:]
        m = m & ~nxt  # 翌日も条件を満たすならその日は取らない
    m = C.dedup(m, MAXH)
    ts, cs = np.nonzero(m)
    return ts, cs


def exit_index(ts, cs, cond, h) -> np.ndarray:
    """買った翌日から h 日以内で最初に cond が立った日。立たなければ h 日後。"""
    T = cond.shape[0]
    out = np.full(ts.size, -1, dtype=np.int64)
    for k in range(1, h + 1):
        u = ts + k
        ok = (out < 0) & (u < T)
        if not ok.any():
            break
        hit = np.zeros(ts.size, dtype=bool)
        hit[ok] = cond[u[ok], cs[ok]]
        out[hit] = u[hit]
    return np.where(out < 0, ts + h, out)


def evaluate(ts, cs, exit_t, cost: float, name: str) -> None:
    close = _close()
    T = close.shape[0]
    ok = exit_t < T
    ts, cs, exit_t = ts[ok], cs[ok], exit_t[ok]

    # 壊れた行が保有期間に混じるものは外す(179 と同じ扱い)
    bad = C.bad_bar()
    win = np.zeros_like(bad)
    for k in range(0, MAXH + 1):
        win[: T - k] |= bad[k:]
    good = ~win[ts, cs]
    ts, cs, exit_t = ts[good], cs[good], exit_t[good]

    buy = close[ts, cs].astype(np.float64) * (1 + cost)
    sell = close[exit_t, cs].astype(np.float64) * (1 - cost)
    r = sell / buy - 1.0
    sd = C.stop_size("1.5ATR(3-12%)")[ts, cs].astype(np.float64)
    m = np.isfinite(r) & np.isfinite(sd) & (sd > 0)
    r, sd = r[m], sd[m]
    R = r / sd
    dts = C.dates()[ts[m]]
    early = dts < C.SPLIT
    se = R.std(ddof=1) / np.sqrt(R.size)
    print(f"{name:<34} n={R.size:>6,} 期待R {R.mean():+.3f} ±{2*se:.3f} "
          f"勝率 {(r>0).mean()*100:4.1f}% 平均 {r.mean()*100:+.2f}% "
          f"保有 {(exit_t[m]-ts[m]).mean():4.1f}日 "
          f"前半 {R[early].mean():+.3f} 後半 {R[~early].mean():+.3f}")


def main() -> None:
    slope, liq = 0.03, 3e8
    close = _close()
    rsi = np.asarray(C.load("rsi2"))
    ma5 = pd.DataFrame(close).rolling(5, min_periods=5).mean().to_numpy(dtype=np.float32)
    prev = np.full_like(close, np.nan)
    prev[1:] = close[:-1]
    up_day = np.isfinite(prev) & (close > prev)

    for sig in ("rsi5", "streak4", "bb"):
        print(f"\n=== シグナル {sig} / 傾き3%以上 / 3億円以上 ===")
        ts, cs = entries(slope, liq, sig)
        evaluate(ts, cs, ts + 5, C.COST_ONEWAY, "固定5日 (179で測った形)")
        evaluate(ts, cs, ts + 5, 0.0, "  同じ・コストゼロ(グロス)")
        evaluate(ts, cs, exit_index(ts, cs, np.isfinite(rsi) & (rsi > 70), MAXH),
                 C.COST_ONEWAY, "RSI(2)>70で降りる (上限10日)")
        evaluate(ts, cs, exit_index(ts, cs, np.isfinite(ma5) & (close > ma5), MAXH),
                 C.COST_ONEWAY, "5日線を超えたら降りる (上限10日)")
        evaluate(ts, cs, exit_index(ts, cs, up_day, MAXH),
                 C.COST_ONEWAY, "1日でも上げたら降りる (上限10日)")
        ts2, cs2 = entries(slope, liq, sig, last=True)
        evaluate(ts2, cs2, ts2 + 5, C.COST_ONEWAY,
                 "★未来を見ている(採用不可)★")


if __name__ == "__main__":
    main()
