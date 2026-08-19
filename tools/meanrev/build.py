#!/usr/bin/env python3
"""型1の中間行列を作る。既存 data/audit_cache/ を読むだけで書かない。

出力は data/mr_cache/ に置く(既存と混ざらない)。
始値は使わない。エントリーは引成(判定日の終値)なので不要。
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import core as C


def _t(t0: float) -> str:
    return f"({time.time()-t0:.0f}秒)"


def build_a() -> None:
    """RSI(2) / 連続陰線 / ストップ安張り付き / 直近20日高値からの下落率。"""
    C.WORK.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    close = np.asarray(C.load("close", src=True), dtype=np.float32)
    T, N = close.shape
    print(f"{T} 日 × {N} 銘柄", flush=True)

    prev = np.full_like(close, np.nan)
    prev[1:] = close[:-1]
    with np.errstate(all="ignore"):
        chg = close / prev - 1.0

    # RSI(2)。Wilder平滑(alpha=1/2)。行ごとに回すのでメモリを食わない。
    up = np.where(np.isfinite(chg) & (chg > 0), close - prev, 0.0).astype(np.float32)
    dn = np.where(np.isfinite(chg) & (chg < 0), prev - close, 0.0).astype(np.float32)
    au = np.zeros(N, dtype=np.float64)
    ad = np.zeros(N, dtype=np.float64)
    rsi = np.full((T, N), np.nan, dtype=np.float32)
    seen = np.zeros(N, dtype=np.int32)
    ok = np.isfinite(chg)
    for t in range(1, T):
        o = ok[t]
        au[o] = 0.5 * au[o] + 0.5 * up[t][o]
        ad[o] = 0.5 * ad[o] + 0.5 * dn[t][o]
        seen[o] += 1
        den = au + ad
        good = o & (seen >= 5) & (den > 0)
        rsi[t][good] = (100.0 * au[good] / den[good]).astype(np.float32)
    np.save(C.npy("rsi2"), rsi)
    print(f"  rsi2 ok {_t(t0)}", flush=True)
    del rsi, up, dn, au, ad

    # 連続陰線(前日比マイナスが何日続いたか)
    down = np.isfinite(chg) & (chg < 0)
    streak = np.zeros((T, N), dtype=np.int8)
    run = np.zeros(N, dtype=np.int8)
    for t in range(T):
        run = np.where(down[t], np.minimum(run + 1, 100), 0).astype(np.int8)
        streak[t] = run
    np.save(C.npy("downstreak"), streak)
    print(f"  downstreak ok {_t(t0)}", flush=True)
    del streak, down

    # ストップ安張り付きの代用。価格が分割調整済みで円建ての制限値幅表を
    # 当てられないため、「大きく下げて安値引け」を張り付きとみなす。
    low = np.asarray(C.load("low", src=True), dtype=np.float32)
    high = np.asarray(C.load("high", src=True), dtype=np.float32)
    with np.errstate(all="ignore"):
        stuck = (chg <= C.LIMIT_DROP) & (close <= low * C.LIMIT_TOL)
        stuck |= (high <= low * 1.0001) & (chg <= -0.05)  # 値幅ゼロの完全張り付き
    stuck &= np.isfinite(chg)
    np.save(C.npy("limitdown"), stuck)
    print(f"  limitdown ok {stuck.mean()*100:.3f}% {_t(t0)}", flush=True)
    del stuck, low, high

    # 直近20日高値からの下落率
    h20 = np.asarray(C.load("h20", src=True), dtype=np.float32)
    with np.errstate(all="ignore"):
        dd = close / h20 - 1.0
    np.save(C.npy("dd20"), dd.astype(np.float32))
    print(f"  dd20 ok {_t(t0)}", flush=True)


def build_b() -> None:
    """ボリンジャー下限割れ / 売買代金20日中央値 / 200日線の21日前比。"""
    C.WORK.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    close = np.asarray(C.load("close", src=True), dtype=np.float32)
    df = pd.DataFrame(close)
    r = df.rolling(20, min_periods=20)
    ma20 = r.mean().to_numpy(dtype=np.float32)
    sd20 = r.std(ddof=0).to_numpy(dtype=np.float32)
    below = np.isfinite(sd20) & (close < ma20 - 2.0 * sd20)
    np.save(C.npy("bblow"), below)
    print(f"  bblow ok {below.mean()*100:.2f}% {_t(t0)}", flush=True)
    del df, r, ma20, sd20, below

    vol = np.asarray(C.load("volume", src=True), dtype=np.float32)
    tv = (close.astype(np.float64) * vol.astype(np.float64))
    med = pd.DataFrame(tv).rolling(20, min_periods=15).median().to_numpy(dtype=np.float32)
    np.save(C.npy("tv20med"), med)
    print(f"  tv20med ok {_t(t0)}", flush=True)
    del vol, tv, med

    ma200 = np.asarray(C.load("ma200", src=True), dtype=np.float32)
    sh = np.full_like(ma200, np.nan)
    sh[21:] = ma200[:-21]
    with np.errstate(all="ignore"):
        slope = ma200 / sh - 1.0
    np.save(C.npy("ma200slope21"), slope.astype(np.float32))
    print(f"  ma200slope21 ok {_t(t0)}", flush=True)
