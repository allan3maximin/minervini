import pandas as pd

from src.backtest import find_breakout_index, is_strong_breakout, measure_performance


def _df(closes, opens=None, volumes=None, vol_ma50=None):
    n = len(closes)
    dates = pd.bdate_range("2024-01-01", periods=n)
    opens = opens if opens is not None else closes
    volumes = volumes if volumes is not None else [100_000] * n
    vol_ma50 = vol_ma50 if vol_ma50 is not None else [100_000] * n
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": volumes,
            "vol_ma50": vol_ma50,
        }
    )


def test_find_breakout_index_detects_first_close_above_pivot():
    closes = [100, 100, 99, 98, 105, 106]  # breaks above pivot=101 at index 4
    df = _df(closes)
    idx = find_breakout_index(df, setup_idx=0, pivot=101)
    assert idx == 4


def test_find_breakout_index_returns_none_when_no_breakout_within_window():
    closes = [100] * 10  # never exceeds pivot
    df = _df(closes)
    idx = find_breakout_index(df, setup_idx=0, pivot=101, max_wait_days=5)
    assert idx is None


def test_find_breakout_index_respects_max_wait_days():
    closes = [100, 100, 100, 100, 100, 100, 105]  # breaks at idx 6, beyond a 3-day window
    df = _df(closes)
    idx = find_breakout_index(df, setup_idx=0, pivot=101, max_wait_days=3)
    assert idx is None


def test_is_strong_breakout_true_when_volume_meets_multiple():
    df = _df([100, 105], volumes=[100_000, 150_000], vol_ma50=[100_000, 100_000])
    assert is_strong_breakout(df, breakout_idx=1, vol_mult=1.4) is True


def test_is_strong_breakout_false_when_volume_below_multiple():
    df = _df([100, 105], volumes=[100_000, 110_000], vol_ma50=[100_000, 100_000])
    assert is_strong_breakout(df, breakout_idx=1, vol_mult=1.4) is False


def test_measure_performance_uses_next_day_open_as_entry_and_computes_returns():
    # breakout at idx 0 (close=105); entry uses idx1's open (110); prices then rise steadily.
    # Need at least entry_idx(1) + max_horizon(20) + 1 = 22 rows for the +20 return to resolve.
    n = 25
    closes = [105.0] + [110.0 + i for i in range(n - 1)]
    opens = [105.0] + [110.0 + i for i in range(n - 1)]
    df = _df(closes, opens=opens)

    result = measure_performance(df, breakout_idx=0, stop_loss_pct=0.05)
    assert result["entry_price"] == 110.0
    assert result["stop_hit"] is False
    # entry_idx=1, +5 -> idx6 close
    assert result["returns"][5] == round((df.iloc[6]["close"] / 110.0 - 1) * 100, 2)
    assert result["returns"][20] == round((df.iloc[21]["close"] / 110.0 - 1) * 100, 2)
    assert result["r_multiple"] is not None


def test_measure_performance_detects_stop_hit_and_freezes_later_returns():
    # entry_price = open of idx1 = 100. stop_loss_pct=0.05 -> stop_price=95.
    # Close drops below 95 on idx3 (entry_idx+2), then would have recovered -- but position is closed.
    closes = [105, 99, 97, 94, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270, 280]
    opens = [105, 100, 99, 97, 94, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270]
    df = _df(closes, opens=opens)

    result = measure_performance(df, breakout_idx=0, stop_loss_pct=0.05)
    assert result["entry_price"] == 100.0
    assert result["stop_price"] == 95.0
    assert result["stop_hit"] is True
    stop_return = round((94 / 100 - 1) * 100, 2)  # close at stop-hit index (idx3) = 94
    assert result["returns"][5] == stop_return
    assert result["returns"][10] == stop_return
    assert result["returns"][20] == stop_return
    assert result["r_multiple"] == round((94 - 100) / (100 - 95), 2)


def test_measure_performance_falls_back_to_breakout_close_when_no_next_day():
    closes = [105]
    df = _df(closes)
    result = measure_performance(df, breakout_idx=0, stop_loss_pct=0.05)
    assert result["entry_price"] == 105.0
    assert result["returns"][5] is None
