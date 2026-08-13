#!/usr/bin/env python3
"""売り方(いつ手仕舞うか)と、資金の回し方の検証。

エントリー側は 174 で決着した(A-2=200日線の21日傾き+5%、A-4=ピボットに近い順)。
残った改善余地は**売り方**にある。25年の検証で一度も触っていない領域なので、
ここを動かすのは過剰適合には当たらない。

これまで全部の検証が「ブレイクの10営業日後に無条件で手仕舞う」前提だった。
この10日は誰も検証していない仮置きの数字で、本番の運用にも存在しない。
しかも A-2 を入れたことで回転の仕方が変わった(枠8のとき平均保有 7.1本→4.9本)ので、
10日のままで良い保証がない。

振るのは4つ。混ぜると何が効いたか分からなくなるので**1軸ずつ**動かす。

  【1】保有日数        5 / 10 / 15 / 20日
  【2】固定%の利確     なし / +5% / +8% / +10% / +15% / +20%
  【3】リスク単位の利確 なし / 1R / 1.5R / 2R / 3R  (ストップ幅の何倍で利確するか)
  【4】トレーリング     なし / 1Rで建値に上げる / 高値から1.5・2.5・3.5ATR下

判定の作法は 174 で決めたものをそのまま使う:

  * 前半(〜2014)と後半(2015〜)を必ず割る。全体のRが最大でも、それが前半だけで
    稼いだ数字なら採らない(傾き12%が全体0.279・後半0.085だった前例)。
  * 崖ではなく坂か。1か所だけ跳ねるなら、その数字を掴んだだけ。
  * 「11日が一番」みたいな細かさまで詰めない。

**setups.parquet には10日ぶんの値動きしか入っていない**(mae/mfe/ret10)。
15日・20日を出すには値動きを取り直すしかないので、このモジュールは
data/audit_cache/ の close/high/low の行列から自分で拾い直す。
取り直しが正しいかは【0】の自己検算で確認する(ここがズレたら以降は全部無効)。

本体のコードも既存の検証スクリプトも触らない(凍結中。log.md 165〜174)。

    python tools/audit_exit.py --part check     # まず自己検算だけ
    python tools/audit_exit.py --part hold      # 保有日数
    python tools/audit_exit.py --part take      # 利確
    python tools/audit_exit.py --part trail     # トレーリング
    python tools/audit_exit.py --part cap       # 枠数
    python tools/audit_exit.py                  # 全部
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

HMAX = 40                 # ここまでの日数を取り直す(--hmax で変える)
BASE_HOLD = 10            # これまでの前提
ERA_SPLIT = "2015-01-01"
PRIM = "1.5ATR(3-12%)"
SLOPE = 0.05              # A-2(採用値)
BASE_CAP = 8              # 枠。ここを基準に他の軸を振る


# ===========================================================================
# 母集団(174で決めたエントリー条件で固定。ここは動かさない)
# ===========================================================================

def rule_mask(d: pd.DataFrame, slope_min: float = SLOPE) -> np.ndarray:
    return np.asarray(
        (d["rs"].to_numpy() >= 70)
        & (d["lr"].to_numpy() >= 1.25)
        & (d["hr"].to_numpy() >= 0.75)
        & (d["ma200sl"].to_numpy() >= slope_min)
        & (d["above50"].to_numpy() > 0.5)
        & (d["bo_gap"].to_numpy() <= 0.05)
        & (d["bo_vol"].to_numpy() >= 1.4),
        dtype=bool,
    )


class Book:
    """条件を通ったブレイクだけを持ち、その先20日ぶんの値動きを取り直す。"""

    def __init__(self, df: pd.DataFrame, slope_min: float):
        B = df[(df["bo"] == 1) & (df["bo_t"] >= 0)].reset_index(drop=True)
        sel = rule_mask(B, slope_min)
        B = B[sel].reset_index(drop=True)
        self.B = B
        self.n_days = df["date"].dt.normalize().nunique()
        self.years = (df["date"].max() - df["date"].min()).days / 365.25
        self.n_signal = len(B)

        self.atrp = B["atrp"].to_numpy(dtype=np.float64)
        self.defs = dd.stop_defs(self.atrp)
        self.bo_t = B["bo_t"].to_numpy()
        self.dist = B["dist"].to_numpy(dtype=np.float64)
        self.bo_date = pd.to_datetime(B["bo_date"])
        self.early = (self.bo_date < ERA_SPLIT).to_numpy()

        # parquet 側の数字(自己検算用)
        self.mae_pq = B["mae"].to_numpy(dtype=np.float64)
        self.ret_pq = B["ret10"].to_numpy(dtype=np.float64)

        self._load_paths(B)

    def _load_paths(self, B: pd.DataFrame) -> None:
        close = np.load(WORK / "close.npy", mmap_mode="r")
        high = np.load(WORK / "high.npy", mmap_mode="r")
        low = np.load(WORK / "low.npy", mmap_mode="r")
        T = close.shape[0]
        bt = B["bo_t"].to_numpy()
        ci = B["ci"].to_numpy()
        n = len(B)

        self.entry = np.asarray(close[bt, ci], dtype=np.float64)
        self.lo = np.empty((n, HMAX + 1), dtype=np.float64)
        self.hi = np.empty((n, HMAX + 1), dtype=np.float64)
        self.cl = np.empty((n, HMAX + 1), dtype=np.float64)
        # データの終わりに掛かった分は最終日で止める(元のコードと同じ扱い)
        self.truncated = (bt + HMAX) > (T - 1)
        for k in range(HMAX + 1):
            j = np.minimum(bt + k, T - 1)
            self.lo[:, k] = low[j, ci]
            self.hi[:, k] = high[j, ci]
            self.cl[:, k] = close[j, ci]
        print(f"値動きの取り直し: {n:,}件 × {HMAX}日  "
              f"(データ末尾に掛かって短くなった {int(self.truncated.sum()):,}件)")


# ===========================================================================
# 手仕舞いの規則
# ===========================================================================

def run_exit(bk: Book, sd: np.ndarray, hold: int = BASE_HOLD,
             take_pct: float | None = None, take_r: float | None = None,
             trail_atr: float | None = None, be_r: float | None = None):
    """1本ずつ日足を進めて、いつ・いくらで手仕舞ったかを出す。

    同じ日に損切りと利確の両方に触った場合は損切りを先に取る(甘く出さないため)。
    戻り値は (手仕舞いまでの日数, 損益率)。
    """
    e = bk.entry
    n = e.size
    stop = e * (1.0 - sd)          # 損切りの値段
    hw = e.copy()                  # そこまでの高値
    alive = np.ones(n, dtype=bool)
    days = np.full(n, hold, dtype=np.int64)
    ret = np.full(n, np.nan)

    tp = None
    if take_pct is not None:
        tp = e * (1.0 + take_pct)
    elif take_r is not None:
        tp = e * (1.0 + take_r * sd)

    for k in range(1, hold + 1):
        lo, hi, _ = bk.lo[:, k], bk.hi[:, k], bk.cl[:, k]

        hit = alive & (lo <= stop)
        if hit.any():
            ret[hit] = stop[hit] / e[hit] - 1.0
            days[hit] = k
            alive &= ~hit

        if tp is not None:
            hit = alive & (hi >= tp)
            if hit.any():
                ret[hit] = tp[hit] / e[hit] - 1.0
                days[hit] = k
                alive &= ~hit

        if not alive.any():
            break

        hw = np.where(alive, np.maximum(hw, hi), hw)
        if trail_atr is not None:
            # 含み益が乗ってから初めて効かせる。乗る前から効かせると
            # 「損切り幅を最初から狭めた」のと区別がつかなくなる。
            cand = hw * (1.0 - trail_atr * bk.atrp)
            up = alive & (hw > e)
            stop = np.where(up, np.maximum(stop, cand), stop)
        if be_r is not None:
            reached = alive & (hw >= e * (1.0 + be_r * sd))
            stop = np.where(reached, np.maximum(stop, e), stop)

    if alive.any():
        ret[alive] = bk.cl[alive, hold] / e[alive] - 1.0

    return days, ret


def stats(bk: Book, sd: np.ndarray, days: np.ndarray, ret: np.ndarray,
          who: np.ndarray | None = None) -> dict:
    m = np.isfinite(ret) if who is None else (who & np.isfinite(ret))
    R = np.where(m, ret / sd, np.nan)
    e = bk.early
    return dict(
        n=int(m.sum()),
        meanR=_mn(R[m]),
        R_e=_mn(R[m & e]),
        R_l=_mn(R[m & ~e]),
        meanP=_mn(ret[m]) * 100,
        win=float((ret[m] > 0).mean()) if m.any() else float("nan"),
        avgday=_mn(days[m].astype(float)),
        totR=float(np.nansum(R[m])),
    )


def _mn(a: np.ndarray) -> float:
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else float("nan")


# ===========================================================================
# 枠を絞った運用(実際に取れた分だけ)
# ===========================================================================

def simulate(bk: Book, days: np.ndarray, ret: np.ndarray, sd: np.ndarray,
             cap: int) -> dict:
    """同時に cap 本までしか持てないとして、取れた取引だけ集計する。

    枠が空くのは**実際に手仕舞った日**。傾きの検証(audit_proposal_slope.py)では
    一律10日ぶん占有させていたが、今回は「早く手仕舞えば次が取れる」こと自体が
    検証の対象なので、実際の保有日数で返す。
    優先順は A-4(ピボットに近い順)で固定。
    """
    ok = np.isfinite(ret) & np.isfinite(bk.bo_t) & np.isfinite(sd)
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        return dict(n=0)

    d0 = bk.bo_t[idx]
    score = bk.dist[idx].astype(np.float64)
    score = np.where(np.isfinite(score), score, np.inf)   # 欠けは最後尾
    order = np.lexsort((score, d0))
    idx, d0 = idx[order], d0[order]

    T = int(d0.max()) + HMAX + 2
    release = np.zeros(T + 2, dtype=np.int64)
    taken = np.zeros(idx.size, dtype=bool)
    used, last, i = 0, -1, 0
    while i < idx.size:
        d = int(d0[i])
        used -= int(release[last + 1: d + 1].sum())
        last = d
        j = i
        while j < idx.size and d0[j] == d:
            if used < cap:
                taken[j] = True
                used += 1
                release[min(d + int(days[idx[j]]), T)] += 1
            j += 1
        i = j

    got = idx[taken]
    who = np.zeros(bk.entry.size, dtype=bool)
    who[got] = True
    st = stats(bk, sd, days, ret, who)
    st["rate"] = got.size / max(bk.n_signal, 1)
    st["peryear"] = st["totR"] / bk.years
    st["hold"] = float(days[got].sum()) / bk.n_days      # 平均同時保有本数
    st["eff"] = st["peryear"] / st["hold"] if st["hold"] > 0 else float("nan")
    return st


# ===========================================================================
# 表示
# ===========================================================================

HD_FREE = (f"  {'案':<22s}{'取引数':>8s}{'期待R':>8s}{'前半R':>8s}{'後半R':>8s}"
           f"{'平均損益%':>10s}{'勝率':>7s}{'平均保有日':>11s}{'局面勝敗':>10s}")
HD_CAP = (f"  {'案':<22s}{'取引数':>8s}{'取得率':>8s}{'期待R':>8s}{'前半R':>8s}"
          f"{'後半R':>8s}{'年あたり合計R':>14s}{'平均保有本数':>12s}{'資本効率':>10s}")


def line_free(label: str, st: dict, wl: str = "") -> None:
    print(f"  {label:<22s}{st['n']:>8,}{st['meanR']:>8.3f}{st['R_e']:>8.3f}"
          f"{st['R_l']:>8.3f}{st['meanP']:>10.3f}{st['win']:>7.1%}"
          f"{st['avgday']:>11.1f}{wl:>10s}")


def line_cap(label: str, st: dict) -> None:
    if st.get("n", 0) == 0:
        return
    print(f"  {label:<22s}{st['n']:>8,}{st['rate']:>8.1%}{st['meanR']:>8.3f}"
          f"{st['R_e']:>8.3f}{st['R_l']:>8.3f}{st['peryear']:>14.1f}"
          f"{st['hold']:>12.1f}{st['eff']:>10.2f}")


def regime_wl(bk: Book, sd: np.ndarray, ret: np.ndarray,
              ret0: np.ndarray) -> str:
    """局面ごとに、基準(保有10日・利確なし)より良かったか。"""
    R = ret / sd
    R0 = ret0 / sd
    d = bk.bo_date
    w = l = 0
    for _, a, b in dd.REGIMES:
        inr = ((d >= a) & (d <= b)).to_numpy()
        v, v0 = R[inr], R0[inr]
        v, v0 = v[np.isfinite(v)], v0[np.isfinite(v0)]
        if v.size < 30 or v0.size < 30:
            continue
        if v.mean() > v0.mean():
            w += 1
        else:
            l += 1
    return f"{w}勝{l}敗"


def stopdef_row(bk: Book, kw: dict) -> str:
    """5本のストップ定義すべてで符号が揃うか。1本でも逆なら採らない。"""
    out = []
    for name, sd in bk.defs.items():
        _, r0 = run_exit(bk, sd, hold=BASE_HOLD)
        dsp, rsp = run_exit(bk, sd, **kw)
        d = _mn(rsp / sd) - _mn(r0 / sd)
        out.append(f"{name} {d:+.3f}")
    return "  ".join(out)


# ===========================================================================
# 【0】自己検算
# ===========================================================================

def part_check(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【0】自己検算 ── 取り直した値動きが、これまでの数字と合うか")
    print("=" * 108)
    print("  保有10日・利確なし・トレーリングなしなら、parquet の記録から出る期待Rと")
    print("  一致するはず。ここがズレていたら以降の数字は全部無効。\n")

    sd = bk.defs[PRIM]
    days, ret = run_exit(bk, sd, hold=BASE_HOLD)
    R_new = ret / sd
    R_old = dd.R_of(bk.mae_pq, bk.ret_pq, sd)

    print(f"  取り直し   期待R {_mn(R_new):.4f}  平均損益 {_mn(ret)*100:.4f}%  "
          f"n={int(np.isfinite(R_new).sum()):,}")
    print(f"  parquet    期待R {_mn(R_old):.4f}  平均損益 "
          f"{_mn(np.where(bk.mae_pq > sd, -sd, bk.ret_pq))*100:.4f}%  "
          f"n={int(np.isfinite(R_old).sum()):,}")
    diff = np.abs(R_new - R_old)
    diff = diff[np.isfinite(diff)]
    big = int((diff > 0.01).sum())
    print(f"  1件ずつの差 平均 {diff.mean():.5f}  最大 {diff.max():.5f}  "
          f"0.01超 {big:,}件 ({big/max(diff.size,1):.2%})")
    print("\n  ※ どちらも『損切り幅を割ったら -1R、生き残ったら10日後の損益 ÷ 損切り幅』")
    print("     なので、値動きの拾い方が同じなら1件ずつ完全に一致する。")
    print("     ここが合わないなら、ブレイク日の復元か行列の読み方が違う。")
    stopped = (ret <= -sd + 1e-12)
    print(f"\n  刈られた割合  取り直し {float(stopped.mean()):.1%}  "
          f"parquet {float((bk.mae_pq > sd).mean()):.1%}")
    print(f"  うち10日目ちょうどで刈られた {float((stopped & (days == BASE_HOLD)).mean()):.1%}")


# ===========================================================================
# 【1】保有日数
# ===========================================================================

HOLDS = [3, 5, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 32, 35, 40]


def part_hold(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【1】何日で手仕舞うか")
    print("=" * 108)
    print("  10日はこれまでの仮置き。長く持つほど1回の利は伸びるが、そのぶん枠が空かない。")
    print("  枠を決めない場合(上)と、枠8本で回した場合(下)を両方見る。\n")

    sd = bk.defs[PRIM]
    _, ret0 = run_exit(bk, sd, hold=BASE_HOLD)
    holds = [h for h in HOLDS if h <= HMAX]

    print("  ── 枠を決めない場合(出たシグナルを全部取れる前提。実際には無理)")
    print(HD_FREE)
    print("  " + "-" * (len(HD_FREE) - 2))
    keep = {}
    for h in holds:
        days, ret = run_exit(bk, sd, hold=h)
        keep[h] = (days, ret)
        tag = "  ← 現行" if h == BASE_HOLD else ""
        wl = "-" if h == BASE_HOLD else regime_wl(bk, sd, ret, ret0)
        line_free(f"{h}日{tag}", stats(bk, sd, days, ret), wl)

    print(f"\n  ── 枠 {BASE_CAP} 本で回した場合(枠が空くのは実際に手仕舞った日)")
    print(HD_CAP)
    print("  " + "-" * (len(HD_CAP) - 2))
    for h in holds:
        days, ret = keep[h]
        tag = "  ← 現行" if h == BASE_HOLD else ""
        line_cap(f"{h}日{tag}", simulate(bk, days, ret, sd, BASE_CAP))

    print("\n  ── 5本のストップ定義すべてで同じ向きか(現行10日との差)")
    print("     1本でも逆を向いたら、その日数は採らない。")
    for h in holds:
        if h == BASE_HOLD:
            continue
        print(f"    {f'{h}日':<8s}{stopdef_row(bk, dict(hold=h))}")

    print("\n  資本効率 = 年あたり合計R ÷ 平均保有本数。ただし**この母集団では読むな**。")
    print("  シグナルが 1.24件/日 しか出ないので枠は埋まりきらず(枠8で平均4.2本)、")
    print("  短く持つほど空き枠が増えるだけで、その空きを使う先が無い。")
    print("  空き枠を数えていない指標なので、短い保有が不当に良く出る。")
    print("  枠が埋まっているかは『平均保有本数 ÷ 枠』で見ること。")


# ===========================================================================
# 【2】【3】利確
# ===========================================================================

TAKE_PCT = [0.05, 0.08, 0.10, 0.15, 0.20]
TAKE_R = [1.0, 1.5, 2.0, 3.0]


def part_take(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【2】利確を入れるか ── 決まった値幅で降りる")
    print("=" * 108)
    print("  今は利確が無い(10日後に成り行き)。上がったところで降りると、")
    print("  勝率は上がるが伸びる1本を捨てることになる。差し引きどうか。\n")

    sd = bk.defs[PRIM]
    _, ret0 = run_exit(bk, sd, hold=BASE_HOLD)

    print("  ── 固定%で利確(保有上限は10日のまま)")
    print(HD_FREE)
    print("  " + "-" * (len(HD_FREE) - 2))
    line_free("なし  ← 現行", stats(bk, sd, *run_exit(bk, sd, hold=BASE_HOLD)), "-")
    for p in TAKE_PCT:
        days, ret = run_exit(bk, sd, hold=BASE_HOLD, take_pct=p)
        line_free(f"+{p:.0%}", stats(bk, sd, days, ret),
                  regime_wl(bk, sd, ret, ret0))

    print("\n" + "=" * 108)
    print("【3】利確を入れるか ── 損切り幅の何倍かで降りる")
    print("=" * 108)
    print("  値動きの荒い銘柄ほど損切り幅も広いので、%より倍率の方が銘柄をまたいで揃う。\n")
    print(HD_FREE)
    print("  " + "-" * (len(HD_FREE) - 2))
    line_free("なし  ← 現行", stats(bk, sd, *run_exit(bk, sd, hold=BASE_HOLD)), "-")
    for r in TAKE_R:
        days, ret = run_exit(bk, sd, hold=BASE_HOLD, take_r=r)
        line_free(f"{r:g}R", stats(bk, sd, days, ret),
                  regime_wl(bk, sd, ret, ret0))

    print(f"\n  ── 枠 {BASE_CAP} 本で回した場合(早く降りれば次が取れる)")
    print(HD_CAP)
    print("  " + "-" * (len(HD_CAP) - 2))
    d, r = run_exit(bk, sd, hold=BASE_HOLD)
    line_cap("なし  ← 現行", simulate(bk, d, r, sd, BASE_CAP))
    for p in TAKE_PCT:
        d, r = run_exit(bk, sd, hold=BASE_HOLD, take_pct=p)
        line_cap(f"+{p:.0%}", simulate(bk, d, r, sd, BASE_CAP))
    for rr in TAKE_R:
        d, r = run_exit(bk, sd, hold=BASE_HOLD, take_r=rr)
        line_cap(f"{rr:g}R", simulate(bk, d, r, sd, BASE_CAP))


# ===========================================================================
# 【4】トレーリング
# ===========================================================================

TRAILS: list[tuple[str, dict]] = [
    ("1Rで建値に上げる", dict(be_r=1.0)),
    ("1.5Rで建値に上げる", dict(be_r=1.5)),
    ("高値-1.5ATR", dict(trail_atr=1.5)),
    ("高値-2.5ATR", dict(trail_atr=2.5)),
    ("高値-3.5ATR", dict(trail_atr=3.5)),
    ("高値-2.5ATR + 1Rで建値", dict(trail_atr=2.5, be_r=1.0)),
]


def part_trail(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【4】損切り線を持ち上げるか(トレーリング)")
    print("=" * 108)
    print("  『1Rで建値に上げる』= 損切り幅ぶん上がったら、損切り線を買値まで上げる。")
    print("  『高値-2.5ATR』= そこまでの高値から、1日の値幅2.5個ぶん下に損切り線を置く。")
    print("  どちらも刈られやすくなるので、勝率は下がって当たり前。期待Rで見る。\n")

    sd = bk.defs[PRIM]
    d0, ret0 = run_exit(bk, sd, hold=BASE_HOLD)

    print("  ── 枠を決めない場合")
    print(HD_FREE)
    print("  " + "-" * (len(HD_FREE) - 2))
    line_free("なし  ← 現行", stats(bk, sd, d0, ret0), "-")
    for name, kw in TRAILS:
        days, ret = run_exit(bk, sd, hold=BASE_HOLD, **kw)
        line_free(name, stats(bk, sd, days, ret), regime_wl(bk, sd, ret, ret0))

    print(f"\n  ── 枠 {BASE_CAP} 本で回した場合")
    print(HD_CAP)
    print("  " + "-" * (len(HD_CAP) - 2))
    line_cap("なし  ← 現行", simulate(bk, d0, ret0, sd, BASE_CAP))
    for name, kw in TRAILS:
        days, ret = run_exit(bk, sd, hold=BASE_HOLD, **kw)
        line_cap(name, simulate(bk, days, ret, sd, BASE_CAP))

    print("\n  ── 5本のストップ定義すべてで同じ向きか(1本でも逆なら採らない)")
    for name, kw in TRAILS:
        print(f"    {name:<24s}{stopdef_row(bk, dict(hold=BASE_HOLD, **kw))}")


# ===========================================================================
# 【5】枠の数
# ===========================================================================

CAPS = [3, 5, 8, 12, 20, 30]


def part_cap(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【5】同時に何本まで持つか")
    print("=" * 108)
    print("  枠を増やすほど取りこぼしは減るが、優先順の後ろ(=ピボットから遠い)まで")
    print("  拾うことになるので1本あたりは悪くなる。年あたり合計Rと資本効率は逆を向く。\n")

    sd = bk.defs[PRIM]
    days, ret = run_exit(bk, sd, hold=BASE_HOLD)
    print(HD_CAP)
    print("  " + "-" * (len(HD_CAP) - 2))
    for c in CAPS:
        tag = "  ← 基準" if c == BASE_CAP else ""
        line_cap(f"{c}本{tag}", simulate(bk, days, ret, sd, c))

    print(f"\n  シグナルは全部で {bk.n_signal:,} 件 / 営業日 {bk.n_days:,} 日 = "
          f"{bk.n_signal/bk.n_days:.2f} 件/日")


# ===========================================================================
# 【6】保有日数 × 枠数
# ===========================================================================

GRID_HOLDS = [5, 10, 15, 20, 30, 40]
GRID_CAPS = [5, 8, 12, 20, 30]


def part_grid(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【6】保有日数 × 枠数 ── 1軸ずつでは見えない組み合わせ")
    print("=" * 108)
    print("  【1】で、枠を決めなければ長く持つほど良く、枠8本では長く持つほど悪い、")
    print("  という逆の結果が出た。長く持つには枠がいる、というだけの話かもしれん。")
    print("  だとすれば『20日 × 枠20』のような右下の組み合わせが誰も測っていない。")
    print("  ここで格子にして、山がどこにあるかを一度で見る。\n")

    sd = bk.defs[PRIM]
    holds = [h for h in GRID_HOLDS if h <= HMAX]
    res: dict[tuple[int, int], dict] = {}
    for h in holds:
        days, ret = run_exit(bk, sd, hold=h)
        for c in GRID_CAPS:
            res[(h, c)] = simulate(bk, days, ret, sd, c)

    def table(title: str, key: str, fmt: str, note: str) -> None:
        print(f"\n  ── {title}")
        hd = "    " + f"{'保有':>6s}" + "".join(f"{f'枠{c}本':>12s}" for c in GRID_CAPS)
        print(hd)
        print("    " + "-" * (len(hd) - 4))
        for h in holds:
            row = "".join(format(res[(h, c)].get(key, float('nan')), fmt).rjust(12)
                          for c in GRID_CAPS)
            tag = " ←現行" if h == BASE_HOLD else ""
            print(f"    {f'{h}日':>6s}{row}{tag}")
        print(f"    {note}")

    table("年あたり合計R(1年でどれだけ稼いだか。大きいほど良い)",
          "peryear", ".1f", "※ ここの最大が『一番稼ぐ組み合わせ』。")
    table("後半R(2015年以降の1本あたり。ここが前半より落ちる案は採らない)",
          "R_l", ".3f", "※ 全体Rが良くても、ここが薄いなら前半で稼いだだけ。")
    table("前半R(〜2014年の1本あたり。後半と比べる用)",
          "R_e", ".3f", "※ 前半と後半の差が小さいほど、先々も同じように効く見込み。")
    table("平均保有本数(枠がどれだけ埋まっているか)",
          "hold", ".1f", "※ 枠の数に近いほど『枠が効いている』。遠いなら枠は制約でない。")
    table("取得率(出たシグナルのうち実際に取れた割合)",
          "rate", ".1%", "※ 低いほど取りこぼしている。")

    print("\n  読み方: 年あたり合計Rの山が『長い保有 × 多い枠』の側にあるなら、")
    print("  10日という数字は枠8本に縛られていただけで、枠を増やせば伸びる余地がある。")
    print("  山が10日の列に留まるなら、保有日数の話はここで終わり。")
    print("  どちらにせよ、後半Rが痩せる組み合わせは採らない。")


# ===========================================================================
# 【7】保有を伸ばしたとき、どの局面で負けるか
# ===========================================================================

REG_HOLDS = [10, 12, 15, 18, 20, 30]


def part_regime(bk: Book) -> None:
    print("\n" + "=" * 108)
    print("【7】保有を伸ばすと、どの局面で負けるか")
    print("=" * 108)
    print("  全体では 15日以降どれも 6勝5敗 のコイン投げだった。")
    print("  負けている5局面が暴落のような構造で説明できるなら許容(A-2はリーマンと")
    print("  コロナで負けたが、暴落直後に200日線が急に上向くはずがないので納得できた)。")
    print("  上昇相場にばらけているなら、伸びは一部の局面が引っ張っているだけ。\n")

    sd = bk.defs[PRIM]
    holds = [h for h in REG_HOLDS if h <= HMAX]
    R = {h: run_exit(bk, sd, hold=h)[1] / sd for h in holds}
    d = bk.bo_date

    hd = "  " + f"{'局面':<30s}{'件数':>7s}" + "".join(f"{f'{h}日':>9s}" for h in holds)
    print(hd)
    print("  " + "-" * (len(hd) - 2))
    for name, a, b in dd.REGIMES:
        inr = ((d >= a) & (d <= b)).to_numpy()
        n = int(np.isfinite(R[BASE_HOLD][inr]).sum())
        if n < 30:
            print(f"  {name:<30s}{n:>7,}   件数不足")
            continue
        cells = "".join(f"{_mn(R[h][inr]):>9.3f}" for h in holds)
        print(f"  {name:<30s}{n:>7,}{cells}")

    print("\n  ── 現行(10日)との差")
    print(hd)
    print("  " + "-" * (len(hd) - 2))
    for name, a, b in dd.REGIMES:
        inr = ((d >= a) & (d <= b)).to_numpy()
        n = int(np.isfinite(R[BASE_HOLD][inr]).sum())
        if n < 30:
            continue
        base = _mn(R[BASE_HOLD][inr])
        cells = "".join(f"{_mn(R[h][inr]) - base:>+9.3f}" for h in holds)
        print(f"  {name:<30s}{n:>7,}{cells}")

    print("\n  ※ 局面ごとの件数が少ないので、1つ2つの取引で符号が変わる。")
    print("     勝敗の数を数えるのは【1】の表。ここは『どこで負けたか』の中身を見る用。")


def part_notes() -> None:
    print("\n" + "=" * 108)
    print("【6】読むときの注意")
    print("=" * 108)
    print("""
  1. 上場廃止になった銘柄がデータに居ないので、どの行も実際より良く出ている。
  2. 損切りは、その日の安値が線を割ったら**線の値段ちょうどで**降りたことにしている。
     寄り付きで飛んだ場合は実際にはもっと悪い。利確も同じく、触れたら約定扱い。
  3. 同じ日に損切りと利確の両方に触った場合は損切りを先に取っている(甘く出さないため)。
  4. 手数料もスリッページも入っていない。
  5. 150日線の2条件は掛かっていない(parquet に無い)。
  6. エントリー側は 174 で決めた条件で固定(傾き+5%・ピボットに近い順)。
     ここを一緒に動かすと何が効いたか分からなくなるので触らない。
""")


# ===========================================================================

PARTS = {
    "check": part_check,
    "hold": part_hold,
    "take": part_take,
    "trail": part_trail,
    "cap": part_cap,
    "grid": part_grid,
    "regime": part_regime,
}


def main() -> None:
    global BASE_CAP, HMAX
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--slope", type=float, default=SLOPE)
    ap.add_argument("--cap", type=int, default=BASE_CAP)
    ap.add_argument("--hmax", type=int, default=HMAX,
                    help="値動きを何日ぶん取り直すか")
    ap.add_argument("--part", default="all",
                    choices=["all", *PARTS.keys()])
    a = ap.parse_args()
    BASE_CAP = a.cap
    HMAX = a.hmax

    df = dd.load_setups(a.since, a.until)
    bk = Book(df, a.slope)
    print(f"\n全セットアップ n={len(df):,}  "
          f"条件を通ったブレイク n={bk.n_signal:,}  "
          f"期間 {df['date'].min():%Y-%m-%d} 〜 {df['date'].max():%Y-%m-%d}  "
          f"営業日 {bk.n_days:,}  年数 {bk.years:.1f}")
    print(f"エントリー条件: RS>=70 / 52週安値比>=1.25 / 52週高値比>=0.75 / "
          f"200日線の21日傾き>={a.slope:.0%} / 50日線の上 / "
          f"ピボット超過<=5% / ブレイク日の出来高>=1.4倍")

    if a.part == "all":
        for fn in PARTS.values():
            fn(bk)
        part_notes()
    else:
        PARTS[a.part](bk)


if __name__ == "__main__":
    main()
