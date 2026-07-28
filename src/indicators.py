"""Technical indicator calculations: moving averages, 52w high/low, RS, ATR.

All calculations are vectorized (pandas/numpy) per design doc section 2.
Input `df` is expected to be a single-symbol OHLCV DataFrame, sorted ascending
by date, with columns: open, high, low, close, volume.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RS_LOOKBACKS = (63, 126, 189, 252)
RS_WEIGHTS = (2.0, 1.0, 1.0, 1.0)


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma150"] = df["close"].rolling(150).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["vol_ma50"] = df["volume"].rolling(50).mean()
    return df


def add_ma200_slope_days(df: pd.DataFrame) -> pd.DataFrame:
    """Consecutive trading days (ending at each row) that MA200 has been rising."""
    df = df.copy()
    up = (df["ma200"].diff() > 0).astype(int)
    reset_groups = (up == 0).cumsum()
    df["ma200_slope_days"] = up.groupby(reset_groups).cumsum()
    return df


def add_ma200_slope_21d(df: pd.DataFrame) -> pd.DataFrame:
    """MA200 の21営業日(≒1ヶ月)あたりの上昇率。tech_score の3変数のひとつ。

    MUST条件で使う ma200_slope_days が「何日連続で上向きか(持続)」なのに対し、
    こちらは「どれくらいの角度で上がっているか(強度)」。26年検証(log.md 140)で
    期待Rの幅が持続の10倍以上あり局面安定性も8勝1敗だったのはこちらの側。
    両者は別物なので、片方をもう片方の代用にしないこと。
    """
    df = df.copy()
    prev = df["ma200"].shift(21)
    df["ma200_slope_21d"] = df["ma200"] / prev - 1.0
    return df


def add_dryup_series(df: pd.DataFrame) -> pd.DataFrame:
    """dryup_med_10_50 のベクトル化版(全行ぶん)。

    dryup_metrics() と同一定義(直近10日出来高の中央値 / vol_ma50)。あちらが
    point-in-time の正準実装で、こちらは全行を一括で欲しい場面(tech_score の
    断面ランク・バックテスト)専用の同値実装。定義がドリフトしていないことは
    tests/test_indicators.py で最終行同士を突合して常時保証する。
    """
    df = df.copy()
    med10 = df["volume"].rolling(10).median()
    df["dryup_med_10_50"] = med10 / df["vol_ma50"].replace(0, np.nan)
    return df


def add_52w_high_low(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["high_52w"] = df["high"].rolling(252).max()
    df["low_52w"] = df["low"].rolling(252).min()
    return df


def add_atr(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df[f"atr{n}"] = tr.rolling(n).mean()
    return df


def add_rs_raw(df: pd.DataFrame) -> pd.DataFrame:
    """IBD-style reverse-engineered RS raw score (3-month return weighted x2)."""
    df = df.copy()
    close = df["close"]
    total = pd.Series(0.0, index=df.index)
    for lookback, weight in zip(RS_LOOKBACKS, RS_WEIGHTS):
        shifted = close.shift(lookback)
        total = total + weight * (close / shifted - 1.0)
    df["rs_raw"] = total
    return df


def add_rs_line(df: pd.DataFrame, benchmark_close: pd.Series) -> pd.DataFrame:
    """Price relative to a benchmark (e.g. TOPIX proxy), for chart display.

    `df` carries its dates in a "date" column (its index is positional), while
    `benchmark_close` is indexed by date -- so align via the date column, not
    the index, then forward-fill benchmark gaps (e.g. ETF non-trading days).
    """
    df = df.copy()
    bench = df["date"].map(benchmark_close).ffill()
    df["rs_line"] = df["close"] / bench
    return df


def compute_all(df: pd.DataFrame, benchmark_close: pd.Series | None = None) -> pd.DataFrame:
    """Apply all per-symbol indicator calculations in one pass."""
    df = add_moving_averages(df)
    df = add_ma200_slope_days(df)
    df = add_ma200_slope_21d(df)
    df = add_dryup_series(df)
    df = add_52w_high_low(df)
    df = add_atr(df, 20)
    df = add_rs_raw(df)
    if benchmark_close is not None:
        df = add_rs_line(df, benchmark_close)
    return df


# ---------------------------------------------------------------------------
# 枯れ度(DRY-UP)レイヤー: VCP MUST判定(V1〜V7)とも vcp_score とも独立した
# バッジ/ソート用メトリクス。閾値の予測力検証(バックテスト)と本番フォワード
# ログの両方が、必ずこの共通関数から値を取得する(summary.py事故=再実装ドリフト
# の再発防止。バックテスト側・pipeline側で決して再実装しないこと)。
# ---------------------------------------------------------------------------

def _window(arr: np.ndarray, end_idx: int, k: int) -> np.ndarray:
    lo = max(0, end_idx - k + 1)
    return arr[lo : end_idx + 1]


def dryup_metrics(
    df: pd.DataFrame,
    idx: int = -1,
    base_start_idx: int | None = None,
    vol_ma50: float | None = None,
) -> dict:
    """指定行 idx 時点の枯れ度/タイトネス素メトリクスを算出する(point-in-time)。

    - dryup_avg_5_50 : 直近5日平均出来高 / vol_ma50
    - dryup_med_10_50: 直近10日出来高中央値 / vol_ma50  (V5(a)と同一系列)
    - tightness_10d  : 直近10日の (max(high)-min(low)) / close
    - is_tightest_in_base: tightness_10d が base_start_idx〜idx のベース内10日窓で
      最小か(bool)。base_start_idx=None なら None。

    `df` は単一銘柄の指標付きフレーム(vol_ma50 列を持つ)。idx は位置インデックス
    (負値は末尾からの相対)。全て backward-looking なので、フルhistoryに対して
    任意の idx を渡してもルックアヘッドは起きない。
    """
    n = len(df)
    empty = {
        "dryup_avg_5_50": None,
        "dryup_med_10_50": None,
        "tightness_10d": None,
        "is_tightest_in_base": None,
    }
    if n == 0:
        return empty
    i = idx if idx >= 0 else n + idx
    if i < 0 or i >= n:
        return empty

    vol = df["volume"].to_numpy(dtype="float64")
    high = df["high"].to_numpy(dtype="float64")
    low = df["low"].to_numpy(dtype="float64")
    close = df["close"].to_numpy(dtype="float64")

    if vol_ma50 is None and "vol_ma50" in df.columns:
        vm = float(df["vol_ma50"].to_numpy(dtype="float64")[i])
        vol_ma50 = vm if not np.isnan(vm) else None

    last5 = _window(vol, i, 5)
    last10 = _window(vol, i, 10)
    dryup_avg_5_50 = float(last5.mean()) / vol_ma50 if vol_ma50 else None
    dryup_med_10_50 = float(np.median(last10)) / vol_ma50 if vol_ma50 else None

    w_high = float(_window(high, i, 10).max())
    w_low = float(_window(low, i, 10).min())
    tightness_10d = (w_high - w_low) / close[i] if close[i] else None

    is_tightest_in_base = None
    if base_start_idx is not None and tightness_10d is not None:
        # ベース内の各10日窓(末尾 j)を走査し、現在窓が最小タイトネスか判定する。
        # 10日ぶんのバーが必要なので窓末尾は max(base_start_idx+9, 9) から。
        start = max(base_start_idx + 9, 9)
        is_tightest_in_base = True
        for j in range(start, i + 1):
            jh = float(_window(high, j, 10).max())
            jl = float(_window(low, j, 10).min())
            if close[j]:
                t = (jh - jl) / close[j]
                if t < tightness_10d - 1e-9:
                    is_tightest_in_base = False
                    break

    return {
        "dryup_avg_5_50": round(float(dryup_avg_5_50), 4) if dryup_avg_5_50 is not None else None,
        "dryup_med_10_50": round(float(dryup_med_10_50), 4) if dryup_med_10_50 is not None else None,
        "tightness_10d": round(float(tightness_10d), 4) if tightness_10d is not None else None,
        "is_tightest_in_base": is_tightest_in_base,
    }


def build_dryup_layer(
    df: pd.DataFrame,
    idx: int,
    base_start_idx: int | None,
    pivot: float | None,
    shakeout_detected: bool,
    vol_ma50: float | None = None,
) -> dict:
    """枯れ度レイヤーの完全レコード(バックテスト・pipeline共通の唯一の生成点)。

    dryup_metrics の4指標に、既存の正準ソースから取る shakeout_detected(vcp)と
    dist_to_pivot(entry)を束ねる。dist_to_pivot は entry.dist_to_pivot_pct を再利用
    (再実装しない)。
    """
    from src.screener.entry import dist_to_pivot_pct  # 遅延import(循環回避)

    m = dryup_metrics(df, idx, base_start_idx, vol_ma50)
    close = float(df["close"].to_numpy(dtype="float64")[idx if idx >= 0 else len(df) + idx])
    m["shakeout_detected"] = bool(shakeout_detected)
    m["dist_to_pivot"] = dist_to_pivot_pct(pivot, close) if pivot else None
    return m


def rs_percentile_rank(rs_raw_by_code: dict[str, float]) -> dict[str, int]:
    """Cross-sectional percentile rank -> integer RS 1-99, per design doc 2.3.

    RS = clip(round(percentile_rank * 98 + 1), 1, 99)
    Population is expected to be the trading universe (~1000 stocks), not all
    listed stocks.
    """
    s = pd.Series(rs_raw_by_code, dtype="float64").dropna()
    if s.empty:
        return {}
    pct = s.rank(pct=True, method="average")
    rs = (pct * 98 + 1).round().clip(1, 99).astype(int)
    return rs.to_dict()
