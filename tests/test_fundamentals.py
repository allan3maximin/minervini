from datetime import date, timedelta
from io import StringIO

import pandas as pd
import pytest

from src.config import load_config
from src.data.fundamentals import (
    build_fundamentals_by_code,
    compute_fund_stale,
    fund_coverage_tier,
    get_fundamentals_for_code,
    load_fundamentals_csv,
    score_stock,
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
