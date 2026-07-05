import numpy as np
import pandas as pd

from src.data.prices import drop_benchmark_outliers


def test_drop_benchmark_outliers_removes_bad_ticks():
    dates = pd.bdate_range("2026-03-16", periods=15)
    values = [382.0] * 15
    # reproduce the observed 1306.T glitch: two isolated days at ~1/10 price
    values[9] = 37.6
    values[10] = 37.1
    close = pd.Series(values, index=dates)

    cleaned = drop_benchmark_outliers(close)

    assert len(cleaned) == 13
    assert dates[9] not in cleaned.index
    assert dates[10] not in cleaned.index
    assert (cleaned == 382.0).all()


def test_drop_benchmark_outliers_keeps_normal_moves():
    dates = pd.bdate_range("2026-03-16", periods=30)
    # a legitimate but sharp market: -8% day inside a mild downtrend
    rng = np.random.RandomState(0)
    values = 2000 * np.cumprod(1 + rng.normal(0, 0.01, 30))
    values[15] *= 0.92
    close = pd.Series(values, index=dates)

    cleaned = drop_benchmark_outliers(close)

    assert len(cleaned) == 30
