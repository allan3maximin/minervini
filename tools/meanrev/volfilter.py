#!/usr/bin/env python3
"""恐怖指数(VIX/日経VI)にあたるものを型1の入口に使えるか(2026-08-19)。

日経VI そのものは持っていない(data/indices に日経225・TOPIX・ナスダック・
SOX・グロース250・ドル円だけ)。オプション価格が要るので簡単には作れない。

代わりに、恐怖指数が測ろうとしているもの(=相場がどれだけ荒れているか)を
実際の値動きから作る。VIX が「これから荒れる」の予想であるのに対し、
こちらは「今まさに荒れている」の実測という違いはあるが、暴落局面では
両者はほぼ同じ形で跳ねる。

  V1 全銘柄の値幅の中央値(mvol)            = 市場全体のいまの荒れ具合
  V2 その1年内での位置(mvol_rank)          = 平常時と比べて何%点か
  V3 21日前からの跳ね上がり(mvol / 21日前) = 急に荒れ始めたか
  V4 指数の実現ボラ21日(年率)              = VIXに一番近い作り方

    python tools/meanrev/volfilter.py

183 で「ボラが1年の高値圏」を危険フィルタとして試したときは、
一番儲かる日を弾いてしまった。ただしあれは母集団(RS70)の素の型1に
当てた話で、184 の採用案(一斉8%以上 かつ 深さ10%以上)の上ではまだ測っていない。
ここでは両方に当てて、結論が採用案の上でも同じかを確かめる。

★注意: これらはすべて「その日の終値まで」で計算している。
21日前との比較も過去だけを見ている。先読みはしていない。
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

CO_MIN = 0.08   # 184で採用: 母集団の8%以上が同時に売られすぎ
DEPTH = -0.10   # 184で採用: 5日で10%以上落ちている


def volmeasures(m: pd.DataFrame):
    """V1〜V4 を日付ごとの1本の列で返す。すべて当日までの値。"""
    mvol = m["mvol"].to_numpy(dtype=float)
    rank = m["mvol_rank"].to_numpy(dtype=float)

    # V3 21日前からの跳ね上がり
    spike = np.full(len(m), np.nan)
    with np.errstate(all="ignore"):
        spike[21:] = mvol[21:] / mvol[:-21] - 1.0

    # V4 指数の実現ボラ21日(年率)。VIXの作り方に一番近い
    lvl = m["idx"].to_numpy(dtype=float)
    dret = np.full(len(m), np.nan)
    with np.errstate(all="ignore"):
        dret[1:] = lvl[1:] / lvl[:-1] - 1.0
    rv = pd.Series(dret).rolling(21, min_periods=21).std().to_numpy() * np.sqrt(252)

    return mvol, rank, spike, rv


def cuts(label, v, ts, tb, sig, base, edges, fmt="{:.0f}"):
    print(f"\n--- {label} ---")
    for lo, hi in edges:
        d = np.isfinite(v) & (v >= lo) & (v < hi)
        nm = f"{fmt.format(lo)}〜{fmt.format(hi)}"
        row(nm, d[ts], d[tb], sig, base)


def main() -> None:
    m = market()
    mvol, rank, spike, rv = volmeasures(m)
    pop, co, mret5, excess, sret5 = measures()
    sig, base = build(pop)
    r, R, ts, cs = sig
    tb = base[2]
    dts = C.dates()

    # 184 の採用案に絞ったぶん
    keep_s = (co[ts] >= CO_MIN) & np.isfinite(co[ts]) & \
        (sret5[ts, cs] <= DEPTH) & np.isfinite(sret5[ts, cs])
    print(f"母集団: 市場より強い上位3割 / 売買代金3億円以上")
    print(f"採用案(184): 一斉{CO_MIN*100:.0f}%以上 かつ 深さ{-DEPTH*100:.0f}%以上"
          f" → {int(keep_s.sum()):,}件")
    print(f"荒れ具合の中央値: いまの値幅 {np.nanmedian(mvol)*100:.2f}% / "
          f"指数の実現ボラ {np.nanmedian(rv)*100:.1f}%")

    # ---------------------------------------------------------- 1. 素の型1に当てる
    print("\n=== 1. 素の型1(母集団まるごと)に荒れ具合を当てる ===")
    print("   183 の再確認。ここでの結論は『荒れているほど儲かる』だったはず")
    cuts("V2 1年内での位置(mvol_rank)", rank, ts, tb, sig, base,
         [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.01)],
         fmt="{:.2f}")
    cuts("V4 指数の実現ボラ21日(年率)", rv, ts, tb, sig, base,
         [(0.0, 0.12), (0.12, 0.18), (0.18, 0.25), (0.25, 0.35), (0.35, 9.0)],
         fmt="{:.0%}")

    # ---------------------------------------------------------- 2. 採用案の上に当てる
    print("\n=== 2. 184の採用案の上に荒れ具合を足す(本題) ===")
    print("   採用案だけで +2.58%。ここから荒れ具合で切って上がるか下がるか")

    def krow(label, d, extra=""):
        sel = keep_s & d[ts]
        row(label, sel, d[tb], sig, base, extra)

    print("\n--- V2 1年内での位置(mvol_rank) ---")
    for lo, hi in [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6),
                   (0.6, 0.8), (0.8, 0.95), (0.95, 1.01)]:
        krow(f"{lo:.2f}〜{hi:.2f}", np.isfinite(rank) & (rank >= lo) & (rank < hi))

    print("\n--- V4 指数の実現ボラ21日(年率) ---")
    for lo, hi in [(0.0, 0.12), (0.12, 0.18), (0.18, 0.25),
                   (0.25, 0.35), (0.35, 9.0)]:
        krow(f"{lo:.0%}〜{hi:.0%}", np.isfinite(rv) & (rv >= lo) & (rv < hi))

    print("\n--- V3 21日前からの跳ね上がり ---")
    for lo, hi in [(-9.0, 0.0), (0.0, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, 99.0)]:
        krow(f"{lo:+.0%}〜{hi:+.0%}",
             np.isfinite(spike) & (spike >= lo) & (spike < hi))

    # ---------------------------------------------------------- 3. 上限として使えるか
    print("\n=== 3. 『荒れすぎたら休む』は成立するか ===")
    print("   恐怖指数の使い道として一番自然な形。上限を1本引いて上下を比べる")
    for th in (0.80, 0.90, 0.95, 0.98):
        hi = np.isfinite(rank) & (rank >= th)
        row(f"位置{th:.2f}以上で休む→残り", keep_s & ~hi[ts], ~hi[tb], sig, base)
        row(f"  └ 休んだぶん(捨てる側)", keep_s & hi[ts], hi[tb], sig, base)
    for th in (0.25, 0.30, 0.40):
        hi = np.isfinite(rv) & (rv >= th)
        row(f"実現ボラ{th:.0%}以上で休む→残り", keep_s & ~hi[ts], ~hi[tb], sig, base)
        row(f"  └ 休んだぶん(捨てる側)", keep_s & hi[ts], hi[tb], sig, base)

    # ---------------------------------------------------------- 4. 2020年2月
    print("\n=== 4. 2020年2月に恐怖指数は間に合ったか ===")
    d = pd.PeriodIndex(dts, freq="M")
    feb = np.array([str(x) == "2020-02" for x in d])
    sub = m.loc[feb].copy()
    sub["rank"] = rank[feb]
    sub["rv"] = rv[feb]
    sub["spike"] = spike[feb]
    ret = np.full(feb.sum(), np.nan)
    lvl = m["idx"].to_numpy(dtype=float)
    dr = np.full(len(m), np.nan)
    dr[1:] = lvl[1:] / lvl[:-1] - 1.0
    sub["日次"] = dr[feb]
    print(f"{'日付':12s}{'指数の日次':>10s}{'1年内での位置':>14s}"
          f"{'実現ボラ':>10s}{'21日前比':>10s}{'型1の入り':>10s}")
    tsel = feb[ts] & keep_s
    for i, (_, v) in enumerate(sub.iterrows()):
        day = np.datetime64(v["date"], "D") if "date" in sub else None
        cnt = int((dts[ts[tsel]] == day).sum()) if day is not None else 0
        print(f"{str(day):12s}{v['日次']*100:+9.2f}%{v['rank']:>14.2f}"
              f"{v['rv']*100:>9.1f}%{v['spike']*100:>+9.1f}%{cnt:>9,d}件")

    # ---------------------------------------------------------- 5. 局面別
    print("\n=== 5. 局面別に『荒れているとき』の中身を見る ===")
    print(f"{'局面':30s}{'位置0.8以上':>20s}{'位置0.8未満':>20s}")
    dd = dts[ts]
    hi = np.isfinite(rank) & (rank >= 0.80)
    for nm, a, b in REGIMES:
        cal = (dd >= np.datetime64(a)) & (dd <= np.datetime64(b))
        cells = []
        for sel in (cal & keep_s & hi[ts], cal & keep_s & ~hi[ts]):
            k = int(sel.sum())
            cells.append(f"{k:4,d}件 {r[sel].mean()*100:+6.2f}%" if k >= 30
                         else f"{k:4,d}件      -")
        print(f"{nm:30s}" + "".join(f"{c:>20s}" for c in cells))


if __name__ == "__main__":
    main()
