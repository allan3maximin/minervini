import pytest

from src.config import load_config
from src.screener.entry import (
    compute_pivot_levels,
    determine_entry_status,
    dist_to_pivot_pct,
    evaluate_entry,
    extended_cooldown_ready,
    is_new_breakout,
    market_guard_triggered,
    record_status,
    tick_size,
)

CONFIG = load_config()


@pytest.mark.parametrize(
    "price,expected_tick",
    [
        (500, 1),
        (1000, 1),
        (1500, 5),
        (4000, 10),
        (8000, 50),
        (20000, 100),
        (40000, 500),
    ],
)
def test_tick_size_brackets(price, expected_tick):
    assert tick_size(price, CONFIG) == expected_tick


def test_compute_pivot_levels_uses_shallower_stop():
    # pivot*0.95 = 1216, stop_ref_low*0.995 = 1210*0.995 = 1203.95 -> shallower (higher) wins
    levels = compute_pivot_levels(pivot=1280.0, stop_ref_low=1210.0, config=CONFIG)
    assert levels["buy_stop"] == 1280.0 + tick_size(1280.0, CONFIG)
    assert levels["stop_loss"] == pytest.approx(max(1280.0 * 0.95, 1210.0 * 0.995), abs=0.01)
    assert levels["risk_pct"] > 0


def test_compute_pivot_levels_without_stop_ref_is_none():
    levels = compute_pivot_levels(pivot=1000.0, stop_ref_low=None, config=CONFIG)
    assert levels["stop_loss"] is None
    assert levels["risk_pct"] is None


def test_dist_to_pivot_pct():
    assert dist_to_pivot_pct(pivot=1000.0, close=970.0) == pytest.approx(3.0, abs=0.01)


@pytest.mark.parametrize(
    "close,volume,vol_ma50,expected_status",
    [
        (990.0, 100_000, 100_000, "WATCH_A"),      # below pivot
        (1010.0, 145_000, 100_000, "BREAKOUT"),     # >pivot, vol 1.45x, not extended
        (1010.0, 110_000, 100_000, "BREAKOUT_WEAK"),  # >pivot, vol 1.1x, not extended
        (1080.0, 300_000, 100_000, "EXTENDED"),      # +8% past pivot, even with huge volume
        (1080.0, 50_000, 100_000, "EXTENDED"),       # +8% past pivot, weak volume too
    ],
)
def test_determine_entry_status(close, volume, vol_ma50, expected_status):
    result = determine_entry_status(close, volume, vol_ma50, pivot=1000.0, config=CONFIG)
    assert result["status"] == expected_status


def test_market_guard_triggered():
    assert market_guard_triggered(-0.02, CONFIG) is True
    assert market_guard_triggered(-0.01, CONFIG) is False


def test_status_history_locked_pivot_and_new_breakout_detection():
    history: dict = {}
    history = record_status(history, "7134", "2026-06-25", "WATCH_B", None, None, CONFIG)
    history = record_status(history, "7134", "2026-06-26", "WATCH_A", 1280.0, 1210.0, CONFIG)

    # Before appending today's entry: yesterday was WATCH_A, today is BREAKOUT
    assert is_new_breakout(history, "7134", "BREAKOUT") is True

    history = record_status(history, "7134", "2026-06-29", "BREAKOUT", 1280.0, 1210.0, CONFIG)
    assert is_new_breakout(history, "7134", "BREAKOUT") is False  # yesterday already BREAKOUT


def test_record_status_same_date_replaces_instead_of_appending():
    history: dict = {}
    history = record_status(history, "7134", "2026-06-25", "WATCH_A", 1000.0, 950.0, CONFIG)
    history = record_status(history, "7134", "2026-06-25", "BREAKOUT", 1000.0, 950.0, CONFIG)

    entries = history["7134"]
    assert len(entries) == 1
    assert entries[0]["status"] == "BREAKOUT"


def test_extended_cooldown_ready_after_14_days():
    history: dict = {}
    for i in range(13):
        history = record_status(history, "9999", f"day{i}", "EXTENDED", 1000.0, 950.0, CONFIG)
    assert extended_cooldown_ready(history, "9999", CONFIG) is False

    history = record_status(history, "9999", "day13", "EXTENDED", 1000.0, 950.0, CONFIG)
    assert extended_cooldown_ready(history, "9999", CONFIG) is True


def test_extended_cooldown_resets_on_non_extended_day():
    history: dict = {}
    for i in range(20):
        history = record_status(history, "8888", f"day{i}", "EXTENDED", 1000.0, 950.0, CONFIG)
    history = record_status(history, "8888", "day20", "WATCH_A", 1000.0, 950.0, CONFIG)
    assert extended_cooldown_ready(history, "8888", CONFIG) is False


def test_evaluate_entry_fresh_watch_a():
    vcp_result = {
        "status": "WATCH_A",
        "contractions": [{"high_price": 1280.0, "low_price": 1210.0, "depth": 0.05}],
    }
    latest_row = {"close": 1250.0, "volume": 80_000, "vol_ma50": 100_000}
    result = evaluate_entry("7134", latest_row, vcp_result, history={}, config=CONFIG)
    assert result["status"] == "WATCH_A"
    assert result["pivot"] == 1280.0
    assert result["stop_loss"] is not None


def test_evaluate_entry_uses_locked_pivot_after_breakout():
    # today's fresh VCP scan no longer finds the old base (status REJECTED),
    # but the stock was WATCH_A yesterday with a locked pivot.
    history: dict = {}
    history = record_status(history, "7134", "2026-06-26", "WATCH_A", 1280.0, 1210.0, CONFIG)

    vcp_result_today = {"status": "REJECTED", "contractions": []}
    latest_row = {"close": 1300.0, "volume": 200_000, "vol_ma50": 100_000}
    result = evaluate_entry("7134", latest_row, vcp_result_today, history, CONFIG)

    assert result["pivot"] == 1280.0
    assert result["status"] == "BREAKOUT"


def test_evaluate_entry_no_setup_when_no_history_and_no_watch_a():
    vcp_result = {"status": "REJECTED", "contractions": []}
    latest_row = {"close": 500.0, "volume": 1000, "vol_ma50": 1000}
    result = evaluate_entry("1234", latest_row, vcp_result, history={}, config=CONFIG)
    assert result["pivot"] is None
