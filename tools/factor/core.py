#!/usr/bin/env python3
"""断面ファクター調査の共通部品(2026-08-19)。

型1(tools/meanrev/)ともミネルヴィニ側とも独立。`data/audit_cache/` は読むだけ。

やること: 毎月末に全銘柄をある指標で並べ、上位から下位まで10等分して
それぞれを1か月持ったら何%だったかを26年ぶん積む。
「どの並べ方に一番差が出るか」の地図を作るのが目的。

前提(型1と揃える):
  ・売買コスト 片道0.15%(往復0.3%)。入れ替えたぶんだけ引く
  ・1日で±50%動く行はデータの壊れとみなして外す
  ・前半 〜2014 / 後半 2015〜 で必ず割って見る
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))
MR = Path(os.environ.get("MNV_MR_WORK", ROOT / "data" / "mr_cache"))

COST_ONEWAY = 0.0015
SPLIT = np.datetime64("2015-01-01")
NQ = 10                 # 何等分するか
LIQ_MIN = 1e8           # 売買代金(20日中央値)の下限。1億円
BAD_JUMP = 0.5


def load(name: str, src: bool = True) -> np.ndarray:
    return np.load((SRC if src else MR) / f"{name}.npy", mmap_mode="r")


def dates() -> np.ndarray:
    return np.load(SRC / "dates.npy", allow_pickle=True)


def codes() -> list[str]:
    return json.loads((SRC / "codes.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 月末の並び
# ---------------------------------------------------------------------------

def month_ends(dts: np.ndarray) -> np.ndarray:
    """各月の最終営業日の行番号。"""
    mo = pd.PeriodIndex(dts, freq="M")
    last = np.zeros(len(dts), dtype=bool)
    last[:-1] = mo[:-1] != mo[1:]
    last[-1] = True
    return np.nonzero(last)[0]


# ---------------------------------------------------------------------------
# 価格まわり
# ---------------------------------------------------------------------------

def close_f64() -> np.ndarray:
    return np.asarray(load("close"), dtype=np.float32).astype(np.float64)


def bad_bar(close: np.ndarray) -> np.ndarray:
    prev = np.full_like(close, np.nan)
    prev[1:] = close[:-1]
    with np.errstate(all="ignore"):
        chg = close / prev - 1.0
    return np.isfinite(chg) & (np.abs(chg) > BAD_JUMP)


def fwd(close: np.ndarray, me: np.ndarray, bad: np.ndarray,
        delay: int = 1) -> np.ndarray:
    """月末の翌営業日の終値で買って、次の月末の翌営業日の終値で売った生リターン。

    ★並べ方(スコア)は月末の終値までで決まるので、その終値で買うことにすると
      「終値を見てから終値で買う」ことになってしまう。1日ずらして翌日の終値で
      買うことで、実際に出せる注文だけで再現できる形にしている。

    形は (月数-1, 銘柄数)。コストはここでは引かない(入れ替えたぶんだけ後で引く)。
    保有期間中に壊れた行が混じるものは nan。
    """
    T = close.shape[0]
    a = np.minimum(me[:-1] + delay, T - 1)
    b = np.minimum(me[1:] + delay, T - 1)
    with np.errstate(all="ignore"):
        r = close[b] / close[a] - 1.0
    r[~np.isfinite(close[a]) | (close[a] <= 0)] = np.nan
    r[~np.isfinite(close[b]) | (close[b] <= 0)] = np.nan

    # 期間中に壊れた行があれば落とす(月ごとに累積)
    cum = np.cumsum(bad.astype(np.int32), axis=0)
    hit = cum[b] - cum[a] > 0
    r[hit] = np.nan
    return r


# ---------------------------------------------------------------------------
# 母集団
# ---------------------------------------------------------------------------

def univ(me: np.ndarray, liq_min: float = LIQ_MIN) -> np.ndarray:
    """月末時点で買える銘柄。形は (月数, 銘柄数)。

    ・上場している(inuniv)
    ・売買代金(20日中央値)が下限以上
    ・株価が有限
    """
    close = np.asarray(load("close"), dtype=np.float32)
    tv = np.asarray(load("tv20med", src=False), dtype=np.float32)
    inu = np.asarray(load("inuniv"), dtype=bool)
    u = inu[me] & np.isfinite(close[me]) & (close[me] > 0)
    u &= np.isfinite(tv[me]) & (tv[me] >= liq_min)
    return u


# ---------------------------------------------------------------------------
# 分位ポートフォリオ
# ---------------------------------------------------------------------------

def quantile_returns(score: np.ndarray, u: np.ndarray, r: np.ndarray,
                     nq: int = NQ, min_n: int = 100):
    """並べて等分し、各分位の月次リターン(等ウェイト)を返す。

    score, u は (月数, 銘柄数)。r は (月数-1, 銘柄数)。
    返り値 q は (月数-1, nq)。持てる月がないところは nan。
    memb は各分位の銘柄集合(次の月の入れ替え率を測るのに使う)。
    """
    M = r.shape[0]
    q = np.full((M, nq), np.nan)
    memb = [[None] * nq for _ in range(M)]
    for i in range(M):
        ok = u[i] & np.isfinite(score[i]) & np.isfinite(r[i])
        idx = np.nonzero(ok)[0]
        if idx.size < min_n:
            continue
        s = score[i][idx]
        order = idx[np.argsort(s, kind="mergesort")]
        edges = np.linspace(0, order.size, nq + 1).astype(int)
        for k in range(nq):
            g = order[edges[k]:edges[k + 1]]
            if g.size == 0:
                continue
            q[i, k] = float(np.nanmean(r[i][g]))
            memb[i][k] = g
    return q, memb


def turnover(memb, k: int) -> float:
    """分位 k の月ごとの入れ替え率(0〜1)の平均。コストを引くのに使う。"""
    vals = []
    prev = None
    for i in range(len(memb)):
        cur = memb[i][k]
        if cur is None:
            prev = None
            continue
        if prev is not None and prev.size:
            keep = np.intersect1d(cur, prev, assume_unique=False).size
            vals.append(1.0 - keep / max(cur.size, 1))
        prev = cur
    return float(np.mean(vals)) if vals else float("nan")


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def curve(mret: np.ndarray) -> np.ndarray:
    x = np.where(np.isfinite(mret), mret, 0.0)
    return np.cumprod(1.0 + x)


def summary(mret: np.ndarray, mdts: np.ndarray, cost: float = 0.0) -> dict:
    """月次リターンの列から年利・最大の落ち込みなどを出す。cost は毎月引く率。"""
    x = np.where(np.isfinite(mret), mret, 0.0) - cost
    eq = np.cumprod(1.0 + x)
    yrs = len(x) / 12.0
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    early = mdts < SPLIT
    return dict(
        n=int(np.isfinite(mret).sum()),
        mean=float(np.nanmean(mret)),
        cagr=eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else float("nan"),
        mdd=float(dd.min()),
        final=float(eq[-1]),
        win=float(np.nanmean(mret > 0)),
        early=float(np.nanmean(mret[early])),
        late=float(np.nanmean(mret[~early])),
        se=float(np.nanstd(mret, ddof=1) / np.sqrt(np.isfinite(mret).sum())),
    )


def market_month(u: np.ndarray, r: np.ndarray) -> np.ndarray:
    """比べる相手。母集団を等ウェイトで全部持った場合の月次リターン。"""
    out = np.full(r.shape[0], np.nan)
    for i in range(r.shape[0]):
        ok = u[i] & np.isfinite(r[i])
        if ok.sum() >= 100:
            out[i] = float(r[i][ok].mean())
    return out
