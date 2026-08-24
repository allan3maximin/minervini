#!/usr/bin/env python3
"""イベント検証の共通部品(2026-08-24)。

断面ファクター(tools/factor/)が「毎月末に全銘柄を並べ替える」のに対して、
こっちは「ある日にある銘柄で何かが起きた」を数えて、その後どうなったかを見る。

`data/audit_cache/` は読むだけ。

■ 先読みを防ぐ約束
  イベントが起きた日を t とすると、その日の終値を見て判断するので、
  買えるのは **翌営業日 t+1 の終値** から。売るのは t+1+H の終値。
  (t の終値で買うことにすると「終値を見てから終値で買う」になってしまう)

■ ぶれ幅(band)の出し方 ★ここが断面版と一番違う
  決算は5月中旬と11月中旬に固まるので、イベントを1件ずつ数えると
  「独立した観測の回数」を大幅に水増ししてしまう。
  そこで **同じ日に起きたイベントは1回にまとめて** から幅を出す。
    日ごとの平均 → その列の 2σ/√(日数)
  これが正直な幅。件数ベースの幅は参考として別に出す。
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

COST_ONEWAY = 0.0015          # 片道0.15%。往復0.3%
SPLIT = np.datetime64("2015-01-01")
LIQ_MIN = 1e8                 # 売買代金(20日中央値)の下限。1億円
BAD_JUMP = 0.5                # 1日で±50%動く行はデータの壊れとみなす


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def load(name: str, src: bool = True) -> np.ndarray:
    return np.load((SRC if src else MR) / f"{name}.npy", mmap_mode="r")


def dates() -> np.ndarray:
    return np.load(SRC / "dates.npy", allow_pickle=True)


def codes() -> list[str]:
    return json.loads((SRC / "codes.json").read_text(encoding="utf-8"))


def f64(name: str, src: bool = True) -> np.ndarray:
    return np.asarray(load(name, src), dtype=np.float32).astype(np.float64)


# ---------------------------------------------------------------------------
# 日次の土台
# ---------------------------------------------------------------------------

class Book:
    """26年ぶんの値動きと母集団を1つにまとめたもの。"""

    def __init__(self, liq_min: float = LIQ_MIN):
        self.dts = dates()
        self.codes = codes()
        self.close = f64("close")
        self.volume = f64("volume")
        self.T, self.N = self.close.shape

        prev = np.full_like(self.close, np.nan)
        prev[1:] = self.close[:-1]
        with np.errstate(all="ignore"):
            self.ret = self.close / prev - 1.0
        self.bad = np.isfinite(self.ret) & (np.abs(self.ret) > BAD_JUMP)

        inu = np.asarray(load("inuniv"), dtype=bool)
        tv = f64("tv20med", src=False)
        self.u = (inu & np.isfinite(self.close) & (self.close > 0)
                  & np.isfinite(tv) & (tv >= liq_min))
        self.tv = tv

        # 市場平均(母集団の等ウェイト日次)。超過を出す基準
        # ★前日も母集団に居た銘柄だけを使う。新規上場や復活の初日は
        #   前日の値段が無い/古いので、そのまま入れると平均が壊れる
        held = np.zeros_like(self.u)
        held[1:] = self.u[1:] & self.u[:-1]
        r = np.where(held & ~self.bad, self.ret, np.nan)
        with np.errstate(all="ignore"):
            self.mkt = np.nanmean(r, axis=1)
        self.mkt[~np.isfinite(self.mkt)] = 0.0

        # 壊れた行の累積。保有期間中に壊れが混じったら捨てるのに使う
        self._badcum = np.cumsum(self.bad.astype(np.int32), axis=0)
        self._mktcum = np.cumsum(np.log1p(self.mkt))
        self._cache: dict = {}

    # -- 比べる相手 -------------------------------------------------------

    def peer(self, hold: int, delay: int = 1) -> np.ndarray:
        """★正しい比べ方: 同じ日に、同じように1銘柄だけ買って持ちっぱなしにした平均。

        毎日全銘柄を等ウェイトに組み直した指数と、1銘柄を持ちっぱなしにした結果を
        比べてはいけない。指数の方は毎日ならすので上下のブレが消えるが、1銘柄は
        消えない。同じ期待値でも「上下にブレるものを持ちっぱなし」の方が
        必ず低く出る(上がって下がると元に戻らないため)。荒い銘柄ほど不利になる。

        なので比べる相手は「その日にサイコロで1銘柄選んで同じ期間持った場合の平均」。
        イベントの銘柄と全く同じ持ち方なので、この偏りが両側で打ち消える。
        """
        key = ("peer", hold, delay)
        if key in self._cache:
            return self._cache[key]
        T = self.T
        a = np.minimum(np.arange(T) + delay, T - 1)
        bidx = np.minimum(a + hold, T - 1)
        with np.errstate(all="ignore"):
            pa = self.close[a]
            pb = self.close[bidx]
            r = pb / pa - 1.0
        good = (self.u[a] & np.isfinite(pa) & (pa > 0)
                & np.isfinite(pb) & (pb > 0)
                & (self._badcum[bidx] - self._badcum[a] == 0))
        r = np.where(good, r, np.nan)
        with np.errstate(all="ignore"):
            out = np.nanmean(r, axis=1)
        out[np.arange(T) + delay + hold >= T] = np.nan
        self._cache[key] = out
        return out

    # -- 前向きリターン ---------------------------------------------------

    def fwd(self, t: np.ndarray, i: np.ndarray, hold: int, delay: int = 1,
            bench: str = "peer"):
        """イベント(t, i)を t+delay の終値で買い、t+delay+hold の終値で売る。

        返り値は (銘柄のリターン, 比べる相手) の組。買えない/壊れは nan。
        bench="peer" は同じ日に1銘柄を同じ期間持った場合の平均(既定・こっちが正しい)。
        bench="index" は毎日組み直した等ウェイト指数(参考。荒い銘柄に不利)。
        """
        a = t + delay
        b = a + hold
        ok = b < self.T
        out = np.full(t.shape, np.nan)
        mk = np.full(t.shape, np.nan)
        if not ok.any():
            return out, mk
        aa, bb, ii = a[ok], b[ok], i[ok]
        pa = self.close[aa, ii]
        pb = self.close[bb, ii]
        with np.errstate(all="ignore"):
            r = pb / pa - 1.0
        good = (np.isfinite(pa) & (pa > 0) & np.isfinite(pb) & (pb > 0)
                & (self._badcum[bb, ii] - self._badcum[aa, ii] == 0))
        r[~good] = np.nan
        out[ok] = r
        if bench == "peer":
            mk[ok] = self.peer(hold, delay)[t[ok]]
        else:
            mk[ok] = np.expm1(self._mktcum[bb] - self._mktcum[aa])
        return out, mk


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def band_by_day(exc: np.ndarray, t: np.ndarray) -> tuple[float, int]:
    """同じ日のイベントを1つにまとめてから ぶれ幅 2σ/√n を出す。

    決算は季節に固まるので、件数で割ると幅が狭くなりすぎる。日で割るのが正直。
    """
    ok = np.isfinite(exc)
    if ok.sum() < 2:
        return float("nan"), 0
    s = pd.Series(exc[ok]).groupby(t[ok]).mean()
    if len(s) < 2:
        return float("nan"), len(s)
    return float(2.0 * s.std(ddof=1) / np.sqrt(len(s))), int(len(s))


def stat(exc: np.ndarray, t: np.ndarray, dts: np.ndarray) -> dict:
    """超過リターンの列をまとめる。前半・後半も割る。"""
    ok = np.isfinite(exc)
    if ok.sum() == 0:
        return dict(n=0, nday=0, mean=float("nan"), band=float("nan"),
                    early=float("nan"), late=float("nan"), med=float("nan"),
                    win=float("nan"))
    e, tt = exc[ok], t[ok]
    when = dts[tt]
    early = when < SPLIT
    b, nday = band_by_day(exc, t)
    return dict(
        n=int(ok.sum()),
        nday=nday,
        mean=float(np.mean(e)),
        med=float(np.median(e)),
        win=float(np.mean(e > 0)),
        band=b,
        early=float(np.mean(e[early])) if early.any() else float("nan"),
        late=float(np.mean(e[~early])) if (~early).any() else float("nan"),
    )


def verdict(s: dict) -> str:
    """4条件のうち、1件の集計だけで当てられる 条件1 と 条件4 を当てる。"""
    if not np.isfinite(s["mean"]) or not np.isfinite(s["band"]):
        return "測れない"
    same = np.sign(s["early"]) == np.sign(s["late"])
    big = abs(s["mean"]) > s["band"]
    if not big:
        return "幅の中(条件4で落ちる)"
    if not same:
        return "前半と後半で向きが違う(条件1で落ちる)"
    return "残る" + ("(プラス)" if s["mean"] > 0 else "(マイナス)")


HEAD = (f"{'名前':<26}{'件数':>7}{'日数':>6}{'超過':>9}{'中央':>9}"
        f"{'勝率':>7}{'幅':>8}{'前半':>9}{'後半':>9}  判定")


def line(nm: str, s: dict) -> str:
    def p(x):
        return f"{x*100:>8.2f}" if np.isfinite(x) else "     ---"
    return (f"{nm:<26}{s['n']:>7}{s['nday']:>6}{p(s['mean'])}{p(s['med'])}"
            f"{s['win']*100:>6.1f}%{p(s['band'])[1:]}{p(s['early'])}{p(s['late'])}"
            f"  {verdict(s)}")


def events(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """真偽の板から (行, 列) の並びを取り出す。"""
    t, i = np.nonzero(mask)
    return t, i
