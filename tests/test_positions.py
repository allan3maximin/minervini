from datetime import date

import pandas as pd
import pytest

from src.report.positions import (
    build_positions_report,
    load_positions_csv,
)


def _df(close, ma50, ma200):
    return pd.DataFrame({"close": [close], "ma50": [ma50], "ma200": [ma200]})


def test_load_positions_csv_missing_file_returns_empty(tmp_path):
    positions, warnings = load_positions_csv(tmp_path / "nope.csv")
    assert positions == []
    assert warnings == []


def test_load_positions_csv_header_only_returns_empty(tmp_path):
    path = tmp_path / "positions.csv"
    path.write_text("code,entry_date,entry_price,shares,initial_stop,current_stop,memo\n", encoding="utf-8")
    positions, warnings = load_positions_csv(path)
    assert positions == []
    assert warnings == []


def test_load_positions_csv_parses_valid_row(tmp_path):
    path = tmp_path / "positions.csv"
    path.write_text(
        "code,entry_date,entry_price,shares,initial_stop,current_stop,memo\n"
        "7203,2026-07-15,3500,100,3325,3325,VCPブレイク\n",
        encoding="utf-8",
    )
    positions, warnings = load_positions_csv(path)
    assert warnings == []
    assert positions == [
        {
            "code": "7203",
            "entry_date": "2026-07-15",
            "entry_price": 3500.0,
            "shares": 100,
            "initial_stop": 3325.0,
            "current_stop": 3325.0,
            "memo": "VCPブレイク",
        }
    ]


def test_load_positions_csv_skips_malformed_rows(tmp_path):
    path = tmp_path / "positions.csv"
    path.write_text(
        "code,entry_date,entry_price,shares,initial_stop,current_stop,memo\n"
        ",2026-07-15,3500,100,3325,3325,missing code\n"
        "7203,not-a-date,3500,100,3325,3325,bad date\n"
        "7203,2026-07-15,notanumber,100,3325,3325,bad price\n"
        "1111,2026-07-16,1000,50,900,900,ok row\n",
        encoding="utf-8",
    )
    positions, warnings = load_positions_csv(path)
    assert len(positions) == 1
    assert positions[0]["code"] == "1111"
    assert len(warnings) == 3


def test_load_positions_csv_blank_memo_becomes_empty_string(tmp_path):
    path = tmp_path / "positions.csv"
    path.write_text(
        "code,entry_date,entry_price,shares,initial_stop,current_stop,memo\n"
        "7203,2026-07-15,3500,100,3325,3325,\n",
        encoding="utf-8",
    )
    positions, _ = load_positions_csv(path)
    assert positions[0]["memo"] == ""


def test_build_positions_report_computes_pl_and_r_multiple():
    positions = [
        {
            "code": "7203", "entry_date": "2026-06-01", "entry_price": 1000.0,
            "shares": 100, "initial_stop": 900.0, "current_stop": 950.0, "memo": "",
        }
    ]
    indicator_by_code = {"7203": _df(close=1100.0, ma50=1000.0, ma200=900.0)}
    report = build_positions_report(positions, indicator_by_code, {"7203": "トヨタ"}, today=date(2026, 6, 11))

    rec = report["positions"][0]
    assert rec["name"] == "トヨタ"
    assert rec["days_held"] == 10
    assert rec["close"] == 1100.0
    assert rec["pl_pct"] == 10.0
    assert rec["pl_jpy"] == 10000.0
    assert rec["r_multiple"] == pytest.approx(1.0)  # (1100-1000)/(1000-900)
    assert rec["dist_to_stop_pct"] == pytest.approx((1100 - 950) / 1100 * 100, abs=0.01)
    assert rec["data_missing"] is False


def test_build_positions_report_data_missing_when_code_absent():
    positions = [
        {
            "code": "9999", "entry_date": "2026-06-01", "entry_price": 1000.0,
            "shares": 100, "initial_stop": 900.0, "current_stop": 950.0, "memo": "",
        }
    ]
    report = build_positions_report(positions, {}, {}, today=date(2026, 6, 11))
    rec = report["positions"][0]
    assert rec["data_missing"] is True
    assert rec["close"] is None
    assert rec["r_multiple"] is None
    assert rec["sell_signals"] == []


def test_build_positions_report_entry_price_leq_initial_stop_warns_and_nulls_r():
    positions = [
        {
            "code": "7203", "entry_date": "2026-06-01", "entry_price": 900.0,
            "shares": 100, "initial_stop": 900.0, "current_stop": 850.0, "memo": "",
        }
    ]
    indicator_by_code = {"7203": _df(close=950.0, ma50=900.0, ma200=800.0)}
    report = build_positions_report(positions, indicator_by_code, {}, today=date(2026, 6, 11))
    rec = report["positions"][0]
    assert rec["r_multiple"] is None
    assert len(report["warnings"]) == 1
    assert "7203" in report["warnings"][0]


@pytest.mark.parametrize(
    "close,ma50,ma200,current_stop,entry_price,expected_signal",
    [
        (940.0, 1000.0, 900.0, 950.0, 1000.0, "STOP_BREACH"),  # close below current_stop
        (950.0, 1000.0, 900.0, 900.0, 1000.0, "MA50_BREAK"),   # close below ma50
        (850.0, 800.0, 900.0, 800.0, 1000.0, "MA200_BREAK"),   # close below ma200
    ],
)
def test_build_positions_report_sell_signal_triggers(close, ma50, ma200, current_stop, entry_price, expected_signal):
    positions = [
        {
            "code": "7203", "entry_date": "2026-06-01", "entry_price": entry_price,
            "shares": 100, "initial_stop": entry_price - 100, "current_stop": current_stop, "memo": "",
        }
    ]
    indicator_by_code = {"7203": _df(close=close, ma50=ma50, ma200=ma200)}
    report = build_positions_report(positions, indicator_by_code, {}, today=date(2026, 6, 11))
    assert expected_signal in report["positions"][0]["sell_signals"]


def test_build_positions_report_take_profit_zone_at_2r():
    positions = [
        {
            "code": "7203", "entry_date": "2026-06-01", "entry_price": 1000.0,
            "shares": 100, "initial_stop": 900.0, "current_stop": 950.0, "memo": "",
        }
    ]
    # r_multiple = (1200-1000)/(1000-900) = 2.0
    indicator_by_code = {"7203": _df(close=1200.0, ma50=1000.0, ma200=900.0)}
    report = build_positions_report(positions, indicator_by_code, {}, today=date(2026, 6, 11))
    assert "TAKE_PROFIT_ZONE" in report["positions"][0]["sell_signals"]


def test_build_positions_report_breakeven_ready_at_1r_with_stop_below_entry():
    positions = [
        {
            "code": "7203", "entry_date": "2026-06-01", "entry_price": 1000.0,
            "shares": 100, "initial_stop": 900.0, "current_stop": 950.0, "memo": "",  # stop still below entry
        }
    ]
    # r_multiple = (1100-1000)/(1000-900) = 1.0
    indicator_by_code = {"7203": _df(close=1100.0, ma50=1000.0, ma200=900.0)}
    report = build_positions_report(positions, indicator_by_code, {}, today=date(2026, 6, 11))
    assert "BREAKEVEN_READY" in report["positions"][0]["sell_signals"]


def test_build_positions_report_breakeven_not_ready_once_stop_at_or_above_entry():
    positions = [
        {
            "code": "7203", "entry_date": "2026-06-01", "entry_price": 1000.0,
            "shares": 100, "initial_stop": 900.0, "current_stop": 1000.0, "memo": "",  # already at breakeven
        }
    ]
    indicator_by_code = {"7203": _df(close=1100.0, ma50=1000.0, ma200=900.0)}
    report = build_positions_report(positions, indicator_by_code, {}, today=date(2026, 6, 11))
    assert "BREAKEVEN_READY" not in report["positions"][0]["sell_signals"]
