#!/usr/bin/env python3
"""型1(短期売られすぎリバウンド)の共通部品。

既存ミネルヴィニ側とは独立。`data/audit_cache/` は読むだけで書かない。
型1固有の中間データは `data/mr_cache/` に置く。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]

# 既存側の行列(読み取り専用)
SRC = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))
# 型1固有の中間データ
WORK = Path(os.environ.get("MNV_MR_WORK", ROOT / "data" / "mr_cache"))

# --- ユーザーと決めた前提 (2026-08-19) -------------------------------------
COST_ONEWAY = 0.0015          # 片道0.15% = 往復0.3%。手数料+滑り
LIQ_LEVELS = (1e8, 3e8, 10e8)  # 売買代金(20日中央値)の下限。横並びで測る
SLOPE_LEVELS = (0.0, 0.03, 0.05)  # 200日線の21日前比。既存のA-2は引き継がない
HOLD_DAYS = (3, 5, 10)
SPLIT = np.datetime64("2015-01-01")  # 前半 〜2014 / 後半 2015〜

# ストップ安の代用判定。
# 価格は分割調整済みなので「呼値の円建て制限値幅表」は当てられない
# (昔の3,000円が調整後300円になっている)。そこで
# 「その日に大きく下げて、かつ安値引け(=張り付き)」を張り付きの代用とする。
LIMIT_DROP = -0.15   # 当日下落率がこれ以下、かつ
LIMIT_TOL = 1.002    # 終値が安値のこの倍率以内 = 安値に張り付いている

# 期待Rの分母(損切り幅の置き方)。今回は損切りを入れないので
# 「同じ取引を5通りに見ているだけ」= 偶然でない証拠にはならない。記録用。
STOP_DEFS = ("1.5ATR(3-12%)", "1.5ATR(clip無)", "2.0ATR", "固定5%", "固定8%")


def npy(name: str, src: bool = False) -> Path:
    return (SRC if src else WORK) / f"{name}.npy"


def load(name: str, src: bool = False, mmap: bool = True) -> np.ndarray:
    return np.load(npy(name, src), mmap_mode="r" if mmap else None)


def dates() -> np.ndarray:
    return np.load(SRC / "dates.npy", allow_pickle=True)


def codes() -> list[str]:
    return json.loads((SRC / "codes.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 母集団
# ---------------------------------------------------------------------------

def population(slope_thr: float, liq_thr: float) -> np.ndarray:
    """上昇トレンド中(200日線より上・200日線が上向き)かつ流動性のあるセル。

    下降トレンド中のリバウンド狙いは落ちるナイフになるので、
    長期トレンドが上向きであることを必須にする。
    """
    close = load("close", src=True)
    ma200 = load("ma200", src=True)
    slope = load("ma200slope21")
    tv = load("tv20med")

    m = np.isfinite(close) & np.isfinite(ma200) & (close > ma200)
    m &= np.isfinite(slope) & (slope >= slope_thr)
    m &= np.isfinite(tv) & (tv >= liq_thr)
    return m


def tradable() -> np.ndarray:
    """その日の終値で実際に買えるか。ストップ安張り付きと当日/前日の張り付きを外す。"""
    ld = np.asarray(load("limitdown"), dtype=bool)
    ok = ~ld
    ok[1:] &= ~ld[:-1]   # 前日ストップ安も外す
    return ok


# ---------------------------------------------------------------------------
# 損益
# ---------------------------------------------------------------------------

BAD_JUMP = 0.5  # 1日で±50%超はデータの壊れ(分割の調整漏れ)とみなす


def bad_bar() -> np.ndarray:
    """壊れた行。

    日本株には値幅制限があるので1日で±50%動くことは実質ありえない。
    それが出ているのは株式分割の調整が入っていない行(yfinance由来)。
    24,458,070セル中1,244件(0.005%)、うち8割が2010年より前。
    数は少ないが、+17,598% のような行が1つ混じるだけで平均が壊れるので
    必ず外す。外さないと上位0.1%だけで期待Rが3倍以上変わる(実測済み)。
    """
    close = np.asarray(load("close", src=True), dtype=np.float32)
    prev = np.full_like(close, np.nan)
    prev[1:] = close[:-1]
    with np.errstate(all="ignore"):
        chg = close / prev - 1.0
    return np.isfinite(chg) & (np.abs(chg) > BAD_JUMP)


def forward_return(h: int) -> np.ndarray:
    """引成で買って h 日後の終値で売ったときの手取り。コスト往復込み。

    保有期間中に壊れた行が混じるものは計算しない(nanにする)。
    """
    close = np.asarray(load("close", src=True), dtype=np.float32)
    T = close.shape[0]
    exit_px = np.full_like(close, np.nan)
    exit_px[: T - h] = close[h:]
    buy = close * (1.0 + COST_ONEWAY)
    sell = exit_px * (1.0 - COST_ONEWAY)
    with np.errstate(all="ignore"):
        ret = sell / buy - 1.0
    ret[~np.isfinite(close) | (close <= 0)] = np.nan

    bad = bad_bar()
    win = np.zeros_like(bad)
    for k in range(0, h + 1):
        win[: T - k] |= bad[k:]
    ret[win] = np.nan
    return ret


def stop_size(which: str) -> np.ndarray:
    """期待Rの分母。ATRは既存側の定義(TR14日単純平均)をそのまま借りる。"""
    close = np.asarray(load("close", src=True), dtype=np.float32)
    if which == "固定5%":
        return np.full_like(close, 0.05)
    if which == "固定8%":
        return np.full_like(close, 0.08)
    atr = np.asarray(load("atr", src=True), dtype=np.float32)
    with np.errstate(all="ignore"):
        atrp = atr / close
    if which == "2.0ATR":
        return 2.0 * atrp
    sd = 1.5 * atrp
    if which == "1.5ATR(clip無)":
        return sd
    return np.clip(sd, 0.03, 0.12)  # 1.5ATR(3-12%)


# ---------------------------------------------------------------------------
# 重複エントリーの間引き
# ---------------------------------------------------------------------------

def dedup(mask: np.ndarray, gap: int) -> np.ndarray:
    """同じ銘柄で保有期間が重なるエントリーを落とす。

    間引かないと、同じ値動きを何度も数えて件数だけが水増しされる
    (=±2の幅が実態より狭く出て、差があるように見えてしまう)。
    """
    ts, cs = np.nonzero(mask)
    if ts.size == 0:
        return mask.copy()
    order = np.lexsort((ts, cs))
    ts, cs = ts[order], cs[order]
    keep = np.zeros(ts.size, dtype=bool)
    last_c, last_t = -1, -10**9
    for i in range(ts.size):
        c, t = cs[i], ts[i]
        if c != last_c or t - last_t >= gap:
            keep[i] = True
            last_c, last_t = c, t
    out = np.zeros_like(mask)
    out[ts[keep], cs[keep]] = True
    return out


def thin_baseline(mask: np.ndarray, gap: int) -> np.ndarray:
    """ベースライン(母集団の全セル)用の間引き。

    密なので上の貪欲法だと数千万回まわる。gap日ごとの日付だけ残す。
    どの日を起点にするかで結果が動かないよう、全オフセットの平均を
    取りたいところだが、件数が十分あるので起点0で足りる。
    """
    out = np.zeros_like(mask)
    out[::gap] = mask[::gap]
    return out


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def _mn(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else float("nan")


def _se(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    return float(a.std(ddof=1) / np.sqrt(a.size)) if a.size > 1 else float("nan")


def stats(mask: np.ndarray, ret: np.ndarray, sd: np.ndarray,
          dts: np.ndarray) -> dict:
    """期待R・勝率・平均%と、そのばらつき2つぶん(±2)を返す。

    log.md の判定条件4: 比較相手の期待Rがこの幅の中に入るなら
    「差がある」とは書かない。
    """
    m = mask & np.isfinite(ret) & np.isfinite(sd) & (sd > 0)
    ts, cs = np.nonzero(m)
    if ts.size == 0:
        return dict(n=0, meanR=float("nan"), meanR_trim=float("nan"), seR=float("nan"),
                    meanP=float("nan"), win=float("nan"),
                    R_first=float("nan"), R_late=float("nan"),
                    n_first=0, n_late=0)
    r = ret[ts, cs].astype(np.float64)
    s = sd[ts, cs].astype(np.float64)
    R = r / s
    early = dts[ts] < SPLIT
    # 刈込平均(上下0.1%を落とす)。壊れた行は除いてあるが、
    # それでも極端な1件で平均が動いていないかの確認用。
    srt = np.sort(R[np.isfinite(R)])
    k = int(srt.size * 0.001)
    trim = float(srt[k: srt.size - k].mean()) if srt.size > 2 * k + 1 else float("nan")
    return dict(
        n=int(ts.size),
        meanR=_mn(R),
        meanR_trim=trim,
        seR=_se(R),
        meanP=_mn(r) * 100.0,
        win=float((r > 0).mean()),
        R_first=_mn(R[early]),
        R_late=_mn(R[~early]),
        n_first=int(early.sum()),
        n_late=int((~early).sum()),
    )


def fmt(name: str, s: dict) -> str:
    if not s["n"]:
        return f"{name:<26} 件数0"
    lo = s["meanR"] - 2 * s["seR"]
    hi = s["meanR"] + 2 * s["seR"]
    return (f"{name:<26} n={s['n']:>7,} "
            f"期待R {s['meanR']:+.3f} ±{2*s['seR']:.3f} [{lo:+.3f},{hi:+.3f}] "
            f"刈込 {s['meanR_trim']:+.3f} "
            f"勝率 {s['win']*100:4.1f}% 平均 {s['meanP']:+.2f}% "
            f"前半 {s['R_first']:+.3f} 後半 {s['R_late']:+.3f}")
