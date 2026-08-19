#!/usr/bin/env python3
"""型1(短期売られすぎリバウンド)の検証CLI。

    python tools/meanrev/run.py --stage build
    python tools/meanrev/run.py --stage baseline
    python tools/meanrev/run.py --stage signals --slope 0 --liq 3

思い込みが混ざらないよう、シグナルは1つに決めてから測るのではなく
**全部並べて同じ表に出す**。ベースライン(同じ母集団で無条件に買った場合)を
先に置き、それとの差だけを見る。既存ミネルヴィニの数字とは母集団が違うので
直接比較しない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402


def signal_defs(pop: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """4系統のシグナル。閾値は横並びで比較するため全部作る。"""
    rsi = C.load("rsi2")
    streak = C.load("downstreak")
    dd = C.load("dd20")
    bb = np.asarray(C.load("bblow"), dtype=bool)

    out: list[tuple[str, np.ndarray]] = []
    for thr in (5, 10, 15):
        out.append((f"RSI(2) < {thr}", pop & np.isfinite(rsi) & (np.asarray(rsi) < thr)))
    for k in (2, 3, 4):
        out.append((f"{k}日連続陰線", pop & (np.asarray(streak) >= k)))
    for thr in (5, 8, 10):
        out.append((f"20日高値から -{thr}%",
                    pop & np.isfinite(dd) & (np.asarray(dd) <= -thr / 100.0)))
    out.append(("BB下限(2σ)割れ", pop & bb))
    return out


def run_one(slope: float, liq: float, holds=C.HOLD_DAYS,
            stop: str = "1.5ATR(3-12%)", with_signals: bool = True) -> None:
    dts = C.dates()
    pop = C.population(slope, liq) & C.tradable()
    print(f"\n=== 200日線の傾き(21日前比) >= {slope*100:.0f}% / "
          f"売買代金20日中央値 >= {liq/1e8:.0f}億円 / 分母 {stop} / "
          f"コスト往復 {C.COST_ONEWAY*200:.1f}% ===")
    print(f"母集団セル数 {int(pop.sum()):,} "
          f"(1日あたり平均 {pop.sum()/pop.shape[0]:.0f} 銘柄)")

    sd = C.stop_size(stop)
    rows = [("ベースライン(無条件買い)", None)]
    if with_signals:
        rows += signal_defs(pop)

    for h in holds:
        ret = C.forward_return(h)
        print(f"\n--- 保有 {h} 日 ---")
        base = None
        for name, m in rows:
            mask = C.thin_baseline(pop, h) if m is None else C.dedup(m, h)
            s = C.stats(mask, ret, sd, dts)
            if m is None:
                base = s
            tag = ""
            if base is not None and m is not None and s["n"] > 1:
                lo, hi = s["meanR"] - 2 * s["seR"], s["meanR"] + 2 * s["seR"]
                tag = "  " + ("差あり" if not (lo <= base["meanR"] <= hi) else "幅の中")
            print(C.fmt(name, s) + tag)
        del ret


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["build", "baseline", "signals"])
    ap.add_argument("--slope", type=float, default=0.0, help="200日線の傾き(%)")
    ap.add_argument("--liq", type=float, default=3.0, help="売買代金下限(億円)")
    ap.add_argument("--stop", default="1.5ATR(3-12%)", choices=list(C.STOP_DEFS))
    ap.add_argument("--hold", type=int, default=0, help="0なら3/5/10全部")
    a = ap.parse_args()

    if a.stage == "build":
        from meanrev import build
        build.build_a()
        build.build_b()
        return

    holds = (a.hold,) if a.hold else C.HOLD_DAYS
    if a.stage == "baseline":
        for slope in C.SLOPE_LEVELS:
            for liq in C.LIQ_LEVELS:
                run_one(slope, liq, holds=holds, stop=a.stop, with_signals=False)
    else:
        run_one(a.slope / 100.0, a.liq * 1e8, holds=holds, stop=a.stop)


if __name__ == "__main__":
    main()
