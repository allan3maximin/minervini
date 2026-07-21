import pytest

from src.config import load_config
from src.screener.entry import (
    breakout_age_days,
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
from datetime import date

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


# ---------------------------------------------------------------------------
# ゾンビピボット対策 (2026-07-21): ブレイク鮮度 / ロック破棄
# ---------------------------------------------------------------------------

REJECTED_VCP = {"status": "REJECTED", "contractions": []}


def _daily_history(code, statuses_by_date, pivot=1000.0, stop_ref=950.0):
    history: dict = {}
    for date_str, status in statuses_by_date:
        history = record_status(history, code, date_str, status, pivot, stop_ref, CONFIG)
    return history


def test_breakout_age_days_counts_from_first_breakout_in_streak():
    history = _daily_history("8418", [
        ("2026-07-10", "WATCH_A"),
        ("2026-07-13", "BREAKOUT_WEAK"),  # 初回ブレイク
        ("2026-07-14", "BREAKOUT_WEAK"),
        ("2026-07-17", "WATCH_A"),        # 一時的にピボット割れ(ストリークは継続)
        ("2026-07-20", "BREAKOUT_WEAK"),
    ])
    assert breakout_age_days(history, "8418", date(2026, 7, 21)) == 8


def test_breakout_age_none_when_never_broken_out_on_this_pivot():
    history = _daily_history("7777", [("2026-07-20", "WATCH_A")])
    assert breakout_age_days(history, "7777", date(2026, 7, 21)) is None


def test_breakout_age_resets_when_pivot_changes():
    history: dict = {}
    history = record_status(history, "6666", "2026-07-01", "BREAKOUT", 1000.0, 950.0, CONFIG)
    # 新しいベースで別ピボットに更新 → 旧ブレイクはストリーク外
    history = record_status(history, "6666", "2026-07-20", "WATCH_A", 1100.0, 1040.0, CONFIG)
    assert breakout_age_days(history, "6666", date(2026, 7, 21)) is None


def test_evaluate_entry_stale_when_breakout_is_old():
    # 8418パターン: 初回ブレイクから3週間、ピボット+5%以内でウロウロ
    history = _daily_history("8418", [
        ("2026-07-13", "BREAKOUT_WEAK"),
        ("2026-07-15", "BREAKOUT_WEAK"),
        ("2026-07-17", "WATCH_A"),
        ("2026-07-20", "BREAKOUT_WEAK"),
    ])
    latest_row = {"close": 1040.0, "volume": 110_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("8418", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["status"] == "STALE"
    assert result["breakout_age_days"] == 8
    assert result["pivot"] == 1000.0  # レベル表示は維持


def test_evaluate_entry_watch_a_dip_in_stale_streak_is_stale():
    # ブレイク済みストリーク内でピボットを割った日も「突破待ちWATCH_A」ではなくSTALE
    history = _daily_history("8418", [
        ("2026-07-10", "BREAKOUT_WEAK"),
        ("2026-07-20", "BREAKOUT_WEAK"),
    ])
    latest_row = {"close": 980.0, "volume": 100_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("8418", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["status"] == "STALE"


def test_evaluate_entry_fresh_breakout_not_stale():
    history = _daily_history("8418", [("2026-07-20", "WATCH_A")])
    latest_row = {"close": 1010.0, "volume": 145_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("8418", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["status"] == "BREAKOUT"


def test_evaluate_entry_extended_stays_extended_even_if_old():
    history = _daily_history("8418", [
        ("2026-07-10", "BREAKOUT"),
        ("2026-07-20", "EXTENDED"),
    ])
    latest_row = {"close": 1080.0, "volume": 100_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("8418", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["status"] == "EXTENDED"


def test_evaluate_entry_drops_lock_on_tracking_gap():
    # P1落ち等で追跡が途切れた古いピボットは復活させない
    history = _daily_history("5555", [("2026-06-01", "WATCH_A")])
    latest_row = {"close": 1010.0, "volume": 145_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("5555", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["pivot"] is None
    assert result["lock_dropped"] == "gap"
    assert result["status"] == "REJECTED"


def test_evaluate_entry_drops_lock_when_base_failed():
    # 終値がロック時水準の損切りライン(max(pivot*0.95, ref*0.995)=950)割れ → ゾンビWATCH_A防止
    history = _daily_history("4444", [("2026-07-20", "WATCH_A")])
    latest_row = {"close": 940.0, "volume": 100_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("4444", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["pivot"] is None
    assert result["lock_dropped"] == "base_failed"


def test_evaluate_entry_drops_lock_after_cooldown_of_extended_and_stale():
    days = CONFIG["entry"]["extended_cooldown_days"]
    entries = [(f"2026-07-{i+1:02d}", "EXTENDED" if i % 2 else "STALE") for i in range(days)]
    history = _daily_history("3333", entries)
    latest_row = {"close": 1040.0, "volume": 100_000, "vol_ma50": 100_000,
                  "date": f"2026-07-{days+1:02d}"}
    result = evaluate_entry("3333", latest_row, REJECTED_VCP, history, CONFIG)
    assert result["pivot"] is None
    assert result["lock_dropped"] == "cooldown"


def test_extended_cooldown_counts_stale_days():
    history: dict = {}
    for i in range(CONFIG["entry"]["extended_cooldown_days"]):
        history = record_status(history, "2222", f"day{i}", "STALE", 1000.0, 950.0, CONFIG)
    assert extended_cooldown_ready(history, "2222", CONFIG) is True


def test_evaluate_entry_fresh_vcp_watch_a_ignores_stale_streak():
    # フレッシュなVCPスキャンが同値ピボットのWATCH_Aを返した場合は新ベース扱い(STALEにしない)
    history = _daily_history("1111", [
        ("2026-07-01", "BREAKOUT"),
        ("2026-07-20", "BREAKOUT_WEAK"),
    ])
    vcp_result = {
        "status": "WATCH_A",
        "contractions": [{"high_price": 1000.0, "low_price": 950.0, "depth": 0.05}],
    }
    latest_row = {"close": 990.0, "volume": 80_000, "vol_ma50": 100_000, "date": "2026-07-21"}
    result = evaluate_entry("1111", latest_row, vcp_result, history, CONFIG)
    assert result["status"] == "WATCH_A"
