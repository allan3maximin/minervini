import math

import pandas as pd
import pytest

from src.report.market_signal import (
    compute_breadth_stats,
    compute_index_trend,
    compute_market_signal,
)

CONFIG = {"market_signal": {"green_pct_above_ma200": 0.50, "red_pct_above_ma200": 0.30}}


def _stock(close, ma50, ma200, high, low, high_52w, low_52w):
    return {
        "close": close, "ma50": ma50, "ma200": ma200,
        "high": high, "low": low, "high_52w": high_52w, "low_52w": low_52w,
    }


def test_compute_breadth_stats_counts_above_ma_and_new_highs_lows():
    latest_by_code = {
        "A": _stock(110, 100, 100, 110, 105, 110, 90),  # above ma50/ma200, new high
        "B": _stock(90, 100, 100, 95, 80, 110, 80),     # below ma50/ma200, new low
        "C": _stock(100, 100, 100, 100, 95, 105, 90),   # exactly at ma -> not "above"
    }
    stats = compute_breadth_stats(latest_by_code)
    assert stats["pct_above_ma200"] == round(1 / 3, 4)
    assert stats["pct_above_ma50"] == round(1 / 3, 4)
    assert stats["new_high_count"] == 1
    assert stats["new_low_count"] == 1


def test_compute_breadth_stats_excludes_nan_ma_from_denominator():
    latest_by_code = {
        "A": _stock(110, 100, 100, 110, 105, 110, 90),
        "B": _stock(90, math.nan, math.nan, 95, 80, math.nan, math.nan),  # no MA/52w data yet
    }
    stats = compute_breadth_stats(latest_by_code)
    assert stats["pct_above_ma200"] == 1.0  # only "A" counted in denominator
    assert stats["pct_above_ma50"] == 1.0
    assert stats["new_high_count"] == 1
    assert stats["new_low_count"] == 0


def test_compute_breadth_stats_empty_returns_none_pct():
    stats = compute_breadth_stats({})
    assert stats["pct_above_ma200"] is None
    assert stats["pct_above_ma50"] is None
    assert stats["new_high_count"] == 0
    assert stats["new_low_count"] == 0


def _index_df(closes):
    dates = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"date": dates, "close": closes})


def test_compute_index_trend_none_when_insufficient_history():
    assert compute_index_trend(None) is None
    assert compute_index_trend(_index_df([100.0] * 50)) is None


def test_compute_index_trend_detects_uptrend():
    # Rising series: MA200 slopes up, close above both MAs.
    closes = [100.0 + i * 0.5 for i in range(230)]
    trend = compute_index_trend(_index_df(closes))
    assert trend is not None
    assert trend["index_above_ma50"] is True
    assert trend["index_above_ma200"] is True
    assert trend["index_ma200_slope_up"] is True


def test_compute_index_trend_detects_downtrend():
    closes = [200.0 - i * 0.5 for i in range(230)]
    trend = compute_index_trend(_index_df(closes))
    assert trend is not None
    assert trend["index_above_ma50"] is False
    assert trend["index_above_ma200"] is False
    assert trend["index_ma200_slope_up"] is False


def _uptrend_index_df():
    closes = [100.0 + i * 0.5 for i in range(230)]
    return _index_df(closes)


def _downtrend_index_df():
    closes = [200.0 - i * 0.5 for i in range(230)]
    return _index_df(closes)


def test_compute_market_signal_green_when_all_conditions_met():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    latest_by_code["low1"] = _stock(90, 100, 100, 95, 80, 110, 80)  # 1 new low, still fewer than new highs
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["signal"] == "green"
    assert result["pct_above_ma200"] >= 0.50
    assert result["new_high_count"] > result["new_low_count"]
    assert result["reasons"]


def test_compute_market_signal_red_when_index_below_ma200():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_downtrend_index_df())
    assert result["signal"] == "red"
    assert any("200日線を下回っている" in r for r in result["reasons"])


def test_compute_market_signal_red_when_breadth_weak_even_if_index_uptrend():
    # Only 2/10 above MA200 (20%) -- below red threshold 30%, even though index itself is up.
    latest_by_code = {f"S{i}": _stock(90, 100, 100, 95, 80, 110, 80) for i in range(8)}
    latest_by_code.update({f"U{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(2)})
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["signal"] == "red"
    assert any("上抜け銘柄が" in r for r in result["reasons"])


def test_compute_market_signal_yellow_when_neutral():
    # Breadth between red and green thresholds -> neutral.
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(4)}
    latest_by_code.update({f"D{i}": _stock(90, 100, 100, 95, 80, 110, 80) for i in range(6)})
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["signal"] == "yellow"


def test_compute_market_signal_yellow_when_index_data_missing():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    # Too-short series simulates a missing/insufficient TOPIX cache (compute_index_trend
    # returns None) without touching the real data/indices/topix.parquet on disk.
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_index_df([100.0] * 10))
    assert result["signal"] == "yellow"
    assert any("指数データ欠損" in r for r in result["reasons"])
    assert result["index_above_ma50"] is None
