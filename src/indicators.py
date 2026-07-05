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
    df = add_52w_high_low(df)
    df = add_atr(df, 20)
    df = add_rs_raw(df)
    if benchmark_close is not None:
        df = add_rs_line(df, benchmark_close)
    return df


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
