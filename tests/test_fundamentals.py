from datetime import date, timedelta
from io import StringIO

import pandas as pd
import pytest

import json

from src.config import load_config
from src.data.fundamentals import (
    build_fundamentals_by_code,
    compute_fund_stale,
    fund_coverage_tier,
    get_fundamentals_for_code,
    load_fundamentals_csv,
    merge_fundamentals,
    score_stock,
    write_public_json,
)

CONFIG = load_config()

BASELINE_LATEST = {
    "close": 150.0,
    "ma50": 140.0,
    "ma150": 130.0,
    "ma200": 120.0,
    "ma200_slope_days": 30,
    "low_52w": 100.0,
    "high_52w": 160.0,
    "rs": 80,
}


def _write_csv(tmp_path, content: str):
    path = tmp_path / "fundamentals.csv"
    path.write_text(content, encoding="utf-8")
    return path


def _quarters_csv_rows(code: str, values: list[float], start_year=2024, start_q=1) -> str:
    rows = []
    year, q = start_year, start_q
    for v in values:
        rows.append(f"{code},{year}Q{q},{v},{v*10},,")
        q += 1
        if q > 4:
            q = 1
            year += 1
    return "\n".join(rows)


def test_load_fundamentals_csv_skips_malformed_and_duplicate_rows(tmp_path):
    content = (
        "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n"
        "7134,2026Q1,45.2,12800,8.5,2026-07-03\n"
        "7134,2026Q1,99.9,99999,1.0,2026-07-01\n"  # duplicate quarter -> skipped
        ",2025Q4,38.1,11900,,\n"  # missing code -> skipped
        "7135,BADQ,10.0,100,,\n"  # bad fiscal_quarter -> skipped
    )
    path = _write_csv(tmp_path, content)
    df, warnings = load_fundamentals_csv(path)

    assert len(df) == 1
    assert df.iloc[0]["code"] == "7134"
    assert len(warnings) == 3


def test_load_fundamentals_csv_missing_file_returns_empty(tmp_path):
    df, warnings = load_fundamentals_csv(tmp_path / "does_not_exist.csv")
    assert df.empty
    assert warnings == []


def test_no_csv_rows_yields_pool_tier():
    fundamentals_by_code = build_fundamentals_by_code(pd.DataFrame(columns=["code", "fiscal_quarter"]))
    tier_info = fund_coverage_tier("9999", fundamentals_by_code)
    assert tier_info == {"fund_coverage": "none", "tier": "pool"}


def test_eight_quarters_yields_confirmed_full_tier(tmp_path):
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "7134", [10, 10, 10, 10, 12, 15, 19, 25]
    )
    path = _write_csv(tmp_path, csv_text)
    df, warnings = load_fundamentals_csv(path)
    assert warnings == []
    fundamentals_by_code = build_fundamentals_by_code(df)

    tier_info = fund_coverage_tier("7134", fundamentals_by_code)
    assert tier_info == {"fund_coverage": "full", "tier": "confirmed"}


def test_few_quarters_yields_confirmed_partial_tier(tmp_path):
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "7134", [10, 11]
    )
    path = _write_csv(tmp_path, csv_text)
    df, _ = load_fundamentals_csv(path)
    fundamentals_by_code = build_fundamentals_by_code(df)

    tier_info = fund_coverage_tier("7134", fundamentals_by_code)
    assert tier_info == {"fund_coverage": "partial", "tier": "confirmed"}


def test_fund_stale_true_after_120_days():
    today = date(2026, 7, 3)
    old_date = (today - timedelta(days=121)).isoformat()
    recent_date = (today - timedelta(days=100)).isoformat()

    assert compute_fund_stale(old_date, today, CONFIG) is True
    assert compute_fund_stale(recent_date, today, CONFIG) is False
    assert compute_fund_stale(None, today, CONFIG) is False


def test_partial_full_score_renormalizes_to_100(tmp_path):
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "7134", [10, 11]
    )
    path = _write_csv(tmp_path, csv_text)
    df, _ = load_fundamentals_csv(path)
    fundamentals_by_code = build_fundamentals_by_code(df)

    result = score_stock("7134", BASELINE_LATEST, fundamentals_by_code, today=date(2026, 7, 3), config=CONFIG)
    assert result["tier"] == "confirmed"
    assert result["fund_coverage"] == "partial"
    assert result["full_score"] is not None
    assert 0 <= result["full_score"] <= 100


def test_pool_tier_stock_has_tech_score_but_no_full_score():
    result = score_stock("no_csv_code", BASELINE_LATEST, fundamentals_by_code={}, today=date(2026, 7, 3), config=CONFIG)
    assert result["tier"] == "pool"
    assert result["full_score"] is None
    assert result["tech_score"] is not None


def test_write_public_json_includes_only_codes_with_quarters(tmp_path):
    # code "1111" has real J-Quants-sourced quarters and should be exported;
    # code "2222" has an entry but no quarters (e.g. a failed/empty auto
    # fetch) and should be skipped so the dashboard's fundamentals modal
    # doesn't get a code entry with nothing usable to prefill.
    auto_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2025Q1", "eps": 10.5, "revenue": 1000.0}],
            "monthly_yoy": None,
            "checked_date": "2026-06-01",
        },
        "2222": {"quarters": [], "monthly_yoy": None, "checked_date": None},
    }
    merged = merge_fundamentals(auto_by_code, {})

    out_path = tmp_path / "fundamentals_public.json"
    write_public_json(merged, path=out_path)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["1111"]
    assert data["1111"]["quarters"] == [{"fiscal_quarter": "2025Q1", "eps": 10.5, "revenue": 1000.0}]
    assert data["1111"]["checked_date"] == "2026-06-01"


def test_write_public_json_manual_overrides_take_precedence(tmp_path):
    # merge_fundamentals already applies "manual wins" per quarter; this just
    # confirms write_public_json passes that merged result through as-is
    # rather than re-deriving from the raw auto store.
    auto_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2025Q1", "eps": 10.5, "revenue": 1000.0}],
            "monthly_yoy": None,
            "checked_date": "2026-06-01",
        }
    }
    manual_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2025Q1", "eps": 11.0, "revenue": 1010.0}],
            "monthly_yoy": 5.0,
            "checked_date": "2026-07-01",
        }
    }
    merged = merge_fundamentals(auto_by_code, manual_by_code)

    out_path = tmp_path / "fundamentals_public.json"
    write_public_json(merged, path=out_path)
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["1111"]["quarters"][0]["eps"] == 11.0
    assert data["1111"]["checked_date"] == "2026-07-01"
