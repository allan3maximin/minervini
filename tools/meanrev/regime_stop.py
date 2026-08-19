#!/usr/bin/env python3
"""型1の残り2つの宿題(2026-08-19)。

180 で「跳ねたら降りる」に直したところまでは出た。残っていたのは:

  1. 下げ相場でもプラスが残るか(元々の申し送りの判断ポイント)
  2. 損切りを入れると尻尾が切れて改善するか
     (180 の分布で 100回に1回 -20% を食らっていた。損切りを入れていなかった)

    python tools/meanrev/regime_stop.py

損切りの約定価格は当てられない。寄りで飛んだらもっと下で約定する。
そこで楽観(ストップ値ちょうど)と悲観(その日の終値)の両方を出して挟む。
本当の値はこの間にある。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402
from meanrev import exits as E  # noqa: E402

STOP = "1.5ATR(3-12%)"

REGIMES = [
    ("2000-02 ITバブル崩壊", "2000-01-01", "2002-12-31"),
    ("2003-06 小泉相場", "2003-01-01", "2006-12-31"),
    ("2007-09 リーマン", "2007-01-01", "2009-12-31"),
    ("2010-12 震災・超円高", "2010-01-01", "2012-12-31"),
    ("2013-14 アベノミクス初期", "2013-01-01", "2014-12-31"),
    ("2015-17 アベノミクス中期", "2015-01-01", "2017-12-31"),
    ("2018-19 米中摩擦・年末急落", "2018-01-01", "2019-12-31"),
    ("2020    コロナ", "2020-01-01", "2020-12-31"),
    ("2021-22 グロース崩壊", "2021-01-01", "2022-12-31"),
    ("2023-24 日経高値・24年8月暴落", "2023-01-01", "2024-12-31"),
    ("2025-26 直近", "2025-01-01", "2026-12-31"),
]


def _f(name: str, src: bool = True) -> np.ndarray:
    return np.asarray(C.load(name, src=src), dtype=np.float32)


def good_mask(ts: np.ndarray) -> np.ndarray:
    """保有期間に壊れた行が混じるエントリーを落とすための窓(179と同じ)。"""
    bad = C.bad_bar()
    T = bad.shape[0]
    win = np.zeros_like(bad)
    for k in range(0, E.MAXH + 1):
        win[: T - k] |= bad[k:]
    return win


def trade(ts, cs, sig_exit, stop_frac, low, close, pessimistic: bool):
    """損切りとシグナルイグジットの早い方で降りる。手取りと保有日数を返す。

    stop_frac が None なら損切りなし。
    """
    n = ts.size
    buy_raw = close[ts, cs].astype(np.float64)
    exit_t = sig_exit.copy()
    sell_raw = np.full(n, np.nan)

    if stop_frac is None:
        ok = exit_t < close.shape[0]
        sell_raw[ok] = close[exit_t[ok], cs[ok]]
        return buy_raw, sell_raw, exit_t

    stop_px = buy_raw * (1.0 - stop_frac)
    hit_t = np.full(n, 10**9, dtype=np.int64)
    T = close.shape[0]
    for k in range(1, E.MAXH + 1):
        u = ts + k
        alive = (hit_t == 10**9) & (u <= exit_t) & (u < T)
        if not alive.any():
            break
        lo = np.full(n, np.inf)
        lo[alive] = low[u[alive], cs[alive]]
        hit = alive & (lo <= stop_px)
        hit_t[hit] = u[hit]

    stopped = hit_t < 10**9
    out_t = np.where(stopped, hit_t, exit_t)
    ok = out_t < T
    sell_raw[ok & ~stopped] = close[out_t[ok & ~stopped], cs[ok & ~stopped]]
    s = ok & stopped
    if pessimistic:
        # その日の終値で投げた場合。ストップ値より下で引けていればそちら。
        cl = close[out_t[s], cs[s]].astype(np.float64)
        sell_raw[s] = np.minimum(cl, stop_px[s])
    else:
        sell_raw[s] = stop_px[s]
    return buy_raw, sell_raw, out_t


def summarize(buy_raw, sell_raw, out_t, ts, cs, sd, dts, win, name, show_tail=True):
    T = win.shape[0]
    ok = (out_t < T) & np.isfinite(sell_raw) & np.isfinite(buy_raw) & (buy_raw > 0)
    ok &= ~win[ts, cs]
    ok &= np.isfinite(sd) & (sd > 0)
    b = buy_raw[ok] * (1 + C.COST_ONEWAY)
    s = sell_raw[ok] * (1 - C.COST_ONEWAY)
    r = s / b - 1.0
    R = r / sd[ok]
    hold = out_t[ok] - ts[ok]
    d = dts[ts[ok]]
    early = d < C.SPLIT
    se = R.std(ddof=1) / np.sqrt(R.size)
    line = (f"{name:<30} n={R.size:>6,} 期待R {R.mean():+.3f} ±{2*se:.3f} "
            f"勝率 {(r>0).mean()*100:4.1f}% 平均 {r.mean()*100:+.2f}% "
            f"保有 {hold.mean():4.1f}日 "
            f"前半 {R[early].mean():+.3f} 後半 {R[~early].mean():+.3f}")
    if show_tail:
        w, l = r[r > 0], r[r <= 0]
        line += (f"\n{'':30}   勝ち平均 {w.mean()*100:+.2f}% / 負け平均 {l.mean()*100:+.2f}% "
                 f"/ 下位1% {np.percentile(r,1)*100:+.2f}% "
                 f"/ 下位5% {np.percentile(r,5)*100:+.2f}%")
    print(line)
    return r, R, d


def regime_table(r, R, d, name):
    print(f"\n--- 局面別: {name} ---")
    print(f"{'局面':32s} {'n':>6s} {'期待R':>8s} {'±2':>7s} {'勝率':>6s} {'平均%':>7s}")
    for nm, a, b in REGIMES:
        m = (d >= np.datetime64(a)) & (d <= np.datetime64(b))
        k = int(m.sum())
        if k < 30:
            print(f"{nm:32s} {k:6,d}  (件数不足)")
            continue
        Rm = R[m]
        se2 = 2 * Rm.std(ddof=1) / np.sqrt(k)
        print(f"{nm:32s} {k:6,d} {Rm.mean():+8.3f} {se2:7.3f} "
              f"{(r[m]>0).mean()*100:5.1f}% {r[m].mean()*100:+7.2f}%")


def main() -> None:
    close = _f("close")
    low = _f("low")
    rsi = np.asarray(C.load("rsi2"))
    ma5 = pd.DataFrame(close).rolling(5, min_periods=5).mean().to_numpy(dtype=np.float32)
    dts = C.dates()
    win = good_mask(None)
    sd_mat = C.stop_size(STOP)

    for sig in ("rsi5", "streak4", "bb"):
        ts, cs = E.entries(0.03, 3e8, sig)
        sd = sd_mat[ts, cs].astype(np.float64)
        exA = E.exit_index(ts, cs, np.isfinite(rsi) & (rsi > 70), E.MAXH)
        exB = E.exit_index(ts, cs, np.isfinite(ma5) & (close > ma5), E.MAXH)

        print(f"\n=== {sig} / 傾き3%以上 / 3億円以上 / 損切り幅 {STOP} ===")
        keep = {}
        for tag, ex in (("A RSI戻りで降りる", exA), ("B 5日線超えで降りる", exB)):
            out = trade(ts, cs, ex, None, low, close, False)
            r, R, d = summarize(*out, ts, cs, sd, dts, win, tag + " / 損切りなし")
            keep[tag] = (r, R, d)
            for pes, lbl in ((False, "楽観"), (True, "悲観")):
                out = trade(ts, cs, ex, sd, low, close, pes)
                summarize(*out, ts, cs, sd, dts, win, f"{tag} / 損切り1R({lbl})")

        if sig == "rsi5":
            for tag in ("A RSI戻りで降りる", "B 5日線超えで降りる"):
                regime_table(*keep[tag], tag + " / 損切りなし")


if __name__ == "__main__":
    main()
