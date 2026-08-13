#!/usr/bin/env python3
"""A-2(200日線の21日傾き)の閾値の感度と、保有枠を絞ったときの実際の成績。

`tools/audit_proposal_compare.py` で A-2 だけが全検査を通った。残った問いが2つ:

  【1】「+6%」は崖か坂か。
       閾値を少し動かしただけで成績が跳ねるなら、それは25年のデータに
       たまたま合っただけの数字を掴んだ疑いが強い(=採らない)。
       0%から12%までなめらかに上がっていくなら、+6%という数字自体には
       意味がなく「傾きが強いほど良い」という素直な話なので、安心して使える。

  【2】合計Rは「シグナルを全部取れる」前提の数字だが、現行は1営業日あたり
       3.5件も出ていて、10日保有なら常時35ポジションになる。そんな運用は
       しない。**同時に持てる本数を決めて、そこに収まるぶんだけ取った場合**の
       成績を出さないと、現行と案の優劣は判定できない。

       枠が埋まっているときにどれを捨てるかで結果が変わるので、
       並べ替えの基準も4通り試す。A-4(ピボットまでの距離を加点に使う)は
       まさにこの並べ替えの話なので、ここで初めてまともに検証できる。

本体のコードも既存の検証スクリプトも触らない(凍結中。log.md 165〜173)。

    python tools/audit_proposal_slope.py
    python tools/audit_proposal_slope.py --part cap --caps 5,8,12,20
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))

import audit_thresholds_dd as dd  # noqa: E402

POST = 10          # 保有日数(audit_thresholds_long.py と同一)
ERA_SPLIT = "2015-01-01"
PRIM = "1.5ATR(3-12%)"

SLOPES = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12]


# ===========================================================================

def base_must(d: pd.DataFrame, slope_min: float) -> np.ndarray:
    """現行の閾値 + 200日線の傾き条件。A-1(RS85)とA-3は入れない。

    A-1 は局面別6勝5敗・後半Δ-0.001 で落ち、A-3 は5本すべてでマイナス・
    局面別1勝10敗で落ちた(compare の結果)。ここで混ぜると
    A-2 単独の効きが見えなくなるので入れない。
    """
    return np.asarray(
        (d["rs"].to_numpy() >= 70)
        & (d["lr"].to_numpy() >= 1.25)
        & (d["hr"].to_numpy() >= 0.75)
        & (d["ma200sl"].to_numpy() >= slope_min)
        & (d["above50"].to_numpy() > 0.5),
        dtype=bool,
    )


def base_entry(d: pd.DataFrame) -> np.ndarray:
    return np.asarray(
        (d["bo_gap"].to_numpy() <= 0.05) & (d["bo_vol"].to_numpy() >= 1.4),
        dtype=bool,
    )


class Book:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.B = df[df["bo"] == 1].reset_index(drop=True)
        B = self.B
        self.atrp = B["atrp"].to_numpy()
        self.mae = B["mae"].to_numpy()
        self.ret = B["ret10"].to_numpy()
        self.defs = dd.stop_defs(self.atrp)
        self.R = {k: dd.R_of(self.mae, self.ret, v) for k, v in self.defs.items()}
        sd = self.defs[PRIM]
        with np.errstate(all="ignore"):
            self.pnl = np.where(self.mae > sd, -sd, self.ret)
        self.early = (B["date"] < ERA_SPLIT).to_numpy()
        self.date = B["date"]
        self.bo_t = B["bo_t"].to_numpy()
        self.slope = B["ma200sl"].to_numpy()
        self.dist = B["dist"].to_numpy()
        self.rs = B["rs"].to_numpy()
        self.n_days = df["date"].dt.normalize().nunique()
        self.years = (df["date"].max() - df["date"].min()).days / 365.25


def mean_finite(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else float("nan")


# ===========================================================================
# 【1】閾値の感度
# ===========================================================================

def section_sweep(bk: Book) -> None:
    print("\n" + "=" * 104)
    print("【1】200日線の21日傾き ── 閾値を動かしたときの変わり方(崖か坂か)")
    print("=" * 104)
    print("  0% の行が現行(『21日前より上向き』)。そこから閾値を上げていったときに、")
    print("  期待Rが**なめらかに**上がるなら「傾きが強いほど良い」という素直な話で、")
    print("  +6% という数字自体には意味がない = 安心して使える。")
    print("  どこか1か所だけ跳ねるなら、その数字を掴んだだけの疑いが強い = 採らない。\n")

    base_sel = base_must(bk.B, 0.0) & base_entry(bk.B)
    base_R = mean_finite(bk.R[PRIM][base_sel])
    base_Re = mean_finite(bk.R[PRIM][base_sel & bk.early])
    base_Rl = mean_finite(bk.R[PRIM][base_sel & ~bk.early])

    hd = (f"  {'傾き閾値':>8s}{'候補数':>10s}{'取引数':>9s}{'件/日':>7s}"
          f"{'期待R':>8s}{'平均損益%':>10s}{'前半R':>8s}{'後半R':>8s}"
          f"{'固定5%R':>9s}{'固定8%R':>9s}{'局面勝敗':>10s}")
    print(hd)
    print("  " + "-" * (len(hd) - 2))

    d = bk.date
    for s in SLOPES:
        must = base_must(bk.df, s)
        sel = base_must(bk.B, s) & base_entry(bk.B)
        n = int(sel.sum())
        if n < 200:
            print(f"  {s:>7.0%}  件数不足({n})")
            continue
        w = l = 0
        for name, a, b in dd.REGIMES:
            inr = ((d >= a) & (d <= b)).to_numpy()
            v = bk.R[PRIM][sel & inr]
            v0 = bk.R[PRIM][base_sel & inr]
            v, v0 = v[np.isfinite(v)], v0[np.isfinite(v0)]
            if v.size < 30 or v0.size < 30:
                continue
            if v.mean() > v0.mean():
                w += 1
            else:
                l += 1
        print(f"  {s:>7.0%}{int(must.sum()):>10,}{n:>9,}{n/bk.n_days:>7.2f}"
              f"{mean_finite(bk.R[PRIM][sel]):>8.3f}"
              f"{mean_finite(bk.pnl[sel])*100:>10.3f}"
              f"{mean_finite(bk.R[PRIM][sel & bk.early]):>8.3f}"
              f"{mean_finite(bk.R[PRIM][sel & ~bk.early]):>8.3f}"
              f"{mean_finite(bk.R['固定5%'][sel]):>9.3f}"
              f"{mean_finite(bk.R['固定8%'][sel]):>9.3f}"
              f"{f'{w}勝{l}敗':>10s}")

    print(f"\n  現行(0%)  期待R {base_R:.3f}  前半 {base_Re:.3f}  後半 {base_Rl:.3f}")
    print("  件/日 = 1営業日あたり何件のブレイクが出るか。10日保有なら "
          "『件/日 × 10』が常時持つ本数になる。")


# ===========================================================================
# 【2】保有枠を絞ったときの実際の成績
# ===========================================================================

RANK_KEYS = {
    "200日線の傾き順": ("slope", -1),   # 大きい順
    "ピボットに近い順": ("dist", +1),    # 小さい順 ← A-4 の中身
    "RSの高い順": ("rs", -1),
    "ランダム": (None, 0),
}


def simulate(bk: Book, sel: np.ndarray, cap: int, key: str, seed: int = 0) -> dict:
    """同時に cap 本までしか持てないとして、実際に取れた取引だけを集計する。

    枠が空くのは「入った日 + 10日」。実際には途中で刈られた取引はもっと早く
    枠を返すが、そこまで再現すると刈られやすい案が有利に出てしまうので、
    どの案にも同じく10日ぶん占有させる(案どうしの比較を歪ませないため)。
    """
    idx = np.flatnonzero(sel & np.isfinite(bk.R[PRIM]) & (bk.bo_t >= 0))
    if idx.size == 0:
        return dict(n=0)

    col, sign = RANK_KEYS[key]
    if col is None:
        rng = np.random.default_rng(seed)
        score = rng.random(idx.size)
    else:
        v = getattr(bk, col)[idx].astype(np.float64)
        # 欠けている値は最後尾に回す
        v = np.where(np.isfinite(v), v, -np.inf if sign < 0 else np.inf)
        score = sign * v

    days = bk.bo_t[idx]
    order = np.lexsort((score, days))   # 日ごと、その中は score 昇順(=良い順)
    idx, days = idx[order], days[order]

    T = int(days.max()) + POST + 2
    release = np.zeros(T + 1, dtype=np.int64)
    taken = np.zeros(idx.size, dtype=bool)
    used, last = 0, -1
    i = 0
    while i < idx.size:
        d = days[i]
        used -= int(release[last + 1: d + 1].sum())
        last = d
        j = i
        while j < idx.size and days[j] == d:
            if used < cap:
                taken[j] = True
                used += 1
                release[min(d + POST, T)] += 1
            j += 1
        i = j

    got = idx[taken]
    R = bk.R[PRIM][got]
    e = bk.early[got]
    # 平均同時保有本数 = 取引数 × 10日 ÷ 営業日数
    return dict(
        n=int(got.size),
        rate=float(got.size) / max(int(sel.sum()), 1),
        meanR=mean_finite(R),
        totR=float(np.nansum(R)),
        peryear=float(np.nansum(R)) / bk.years,
        meanP=mean_finite(bk.pnl[got]) * 100,
        R_e=mean_finite(R[e]),
        R_l=mean_finite(R[~e]),
        hold=got.size * POST / bk.n_days,
    )


def section_cap(bk: Book, caps: list[int], slope_pick: float) -> None:
    print("\n" + "=" * 104)
    print(f"【2】同時に持てる本数を決めた場合 ── 現行 vs 現行+A-2({slope_pick:.0%})")
    print("=" * 104)
    print("  枠が埋まっていたら、その日のシグナルは捨てる。だから『シグナルが多い』ことは")
    print("  それ自体では有利にならない。年あたり合計R = 1年でどれだけ稼いだか(リスク単位)。")
    print("  取得率 = 出たシグナルのうち実際に取れた割合。低いほど『取りこぼしている』。\n")

    sets = {
        "現行": base_must(bk.B, 0.0) & base_entry(bk.B),
        f"現行+A-2({slope_pick:.0%})": base_must(bk.B, slope_pick) & base_entry(bk.B),
    }

    for key in RANK_KEYS:
        print(f"\n  ── 枠が足りないときの優先順: {key}")
        hd = (f"    {'ルール':<18s}{'枠':>4s}{'取引数':>9s}{'取得率':>8s}"
              f"{'期待R':>8s}{'平均損益%':>10s}{'年あたり合計R':>14s}"
              f"{'前半R':>8s}{'後半R':>8s}{'平均保有本数':>12s}")
        print(hd)
        print("    " + "-" * (len(hd) - 4))
        for label, sel in sets.items():
            for cap in caps:
                r = simulate(bk, sel, cap, key)
                if r["n"] == 0:
                    continue
                print(f"    {label:<18s}{cap:>4d}{r['n']:>9,}{r['rate']:>8.1%}"
                      f"{r['meanR']:>8.3f}{r['meanP']:>10.3f}{r['peryear']:>14.1f}"
                      f"{r['R_e']:>8.3f}{r['R_l']:>8.3f}{r['hold']:>12.1f}")

    print("\n  ※ 刈られた取引も10日ぶん枠を占有させている。実際は早く枠が空くので、")
    print("     どの行も本当はもう少し取引数が増える。案どうしの比較を歪ませないための処理。")


def section_notes() -> None:
    print("\n" + "=" * 104)
    print("【3】読むときの注意")
    print("=" * 104)
    print("""
  1. 上場廃止になった銘柄がデータに居ないので、どの行も実際より良く出ている。
  2. 150日線の2条件は現行側にも案側にも掛かっていない(parquet に無い)。
  3. 「200日線が21日で上向き」は本番では日数を数えるが、ここでは21日前比で代用。
  4. 手数料もスリッページも入っていない。
  5. 保有は一律10日で切っている。実際の売り(利確・トレーリング)は入っていない。
""")


# ===========================================================================

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--caps", default="5,8,12,20")
    ap.add_argument("--slope", type=float, default=0.06)
    ap.add_argument("--part", default="all", choices=["all", "sweep", "cap"])
    a = ap.parse_args()

    df = dd.load_setups(a.since, a.until)
    bk = Book(df)
    print(f"\n全セットアップ n={len(df):,}  ブレイク到達 n={len(bk.B):,}  "
          f"期間 {df['date'].min():%Y-%m-%d} 〜 {df['date'].max():%Y-%m-%d}  "
          f"営業日 {bk.n_days:,}")

    if a.part in ("all", "sweep"):
        section_sweep(bk)
    if a.part in ("all", "cap"):
        caps = [int(x) for x in a.caps.split(",") if x.strip()]
        section_cap(bk, caps, a.slope)
    if a.part == "all":
        section_notes()


if __name__ == "__main__":
    main()
