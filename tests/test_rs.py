import numpy as np
import pandas as pd
import pytest

from src.indicators import RS_LOOKBACKS, RS_WEIGHTS, add_rs_line, add_rs_raw, rs_percentile_rank


def _make_close_series(n_days: int, daily_return: float) -> pd.Series:
    dates = pd.bdate_range("2023-01-01", periods=n_days)
    prices = 100.0 * (1.0 + daily_return) ** np.arange(n_days)
    return pd.Series(prices, index=dates)


def test_rs_raw_matches_weighted_formula():
    close = _make_close_series(400, daily_return=0.002)
    df = pd.DataFrame({"close": close})
    result = add_rs_raw(df)

    last = -1
    expected = 0.0
    c_now = close.iloc[last]
    for lookback, weight in zip(RS_LOOKBACKS, RS_WEIGHTS):
        c_then = close.iloc[last - lookback]
        expected += weight * (c_now / c_then - 1.0)

    assert result["rs_raw"].iloc[last] == pytest.approx(expected, rel=1e-9)


def test_rs_raw_nan_when_insufficient_history():
    close = _make_close_series(100, daily_return=0.001)
    df = pd.DataFrame({"close": close})
    result = add_rs_raw(df)
    # 100 days of history cannot look back 252 days
    assert pd.isna(result["rs_raw"].iloc[-1])


def test_rs_percentile_rank_boundaries_large_population():
    # 999 stocks spanning a wide range of rs_raw values
    codes = [f"S{i:04d}" for i in range(999)]
    values = np.linspace(-0.5, 0.5, 999)
    rs_raw_by_code = dict(zip(codes, values))

    rs = rs_percentile_rank(rs_raw_by_code)

    worst_code = codes[0]  # lowest rs_raw
    best_code = codes[-1]  # highest rs_raw

    assert rs[worst_code] == 1
    assert rs[best_code] == 99
    assert all(1 <= v <= 99 for v in rs.values())


def test_rs_percentile_rank_monotonic():
    rs_raw_by_code = {"A": -0.3, "B": -0.1, "C": 0.0, "D": 0.2, "E": 0.5}
    rs = rs_percentile_rank(rs_raw_by_code)
    ordered = [rs[c] for c in ["A", "B", "C", "D", "E"]]
    assert ordered == sorted(ordered)


def test_rs_percentile_rank_drops_nan():
    rs_raw_by_code = {"A": 0.1, "B": float("nan"), "C": 0.3}
    rs = rs_percentile_rank(rs_raw_by_code)
    assert "B" not in rs
    assert set(rs.keys()) == {"A", "C"}


def test_add_rs_line_aligns_on_date_column_not_index():
    # Regression: the stock df keeps dates in a "date" column with a plain
    # positional index, while the benchmark is a date-indexed Series. Aligning
    # on the index instead of the date column silently produced all-NaN.
    dates = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame({"date": dates, "close": [100.0, 102.0, 104.0, 106.0, 108.0]})
    bench = pd.Series([2000.0, 2000.0, 2080.0, 2120.0, 2160.0], index=dates)

    result = add_rs_line(df, bench)

    assert result["rs_line"].notna().all()
    assert result["rs_line"].iloc[0] == pytest.approx(100.0 / 2000.0)
    assert result["rs_line"].iloc[-1] == pytest.approx(108.0 / 2160.0)


def test_add_rs_line_ffills_benchmark_gaps():
    dates = pd.bdate_range("2024-01-01", periods=4)
    df = pd.DataFrame({"date": dates, "close": [100.0, 101.0, 102.0, 103.0]})
    # benchmark missing the 3rd day (e.g. ETF non-trading day)
    bench = pd.Series([2000.0, 2010.0, 2030.0], index=[dates[0], dates[1], dates[3]])

    result = add_rs_line(df, bench)

    assert result["rs_line"].notna().all()
    # gap day uses the previous benchmark value
    assert result["rs_line"].iloc[2] == pytest.approx(102.0 / 2010.0)
