import numpy as np
import pandas as pd
import pytest

from src.indicators import RS_LOOKBACKS, RS_WEIGHTS, add_rs_raw, rs_percentile_rank


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
