"""src/deepdive/metrics.py のテスト。純関数のみなので fixture だけで完結する(§9)。"""
from __future__ import annotations

import pandas as pd
import pytest

from src.deepdive import metrics


# ---------------------------------------------------------------------------
# progress_rate
# ---------------------------------------------------------------------------

def test_progress_rate_basic():
    assert metrics.progress_rate(400, 800) == 50.0


def test_progress_rate_none_when_plan_missing():
    assert metrics.progress_rate(400, None) is None


def test_progress_rate_none_when_plan_zero():
    assert metrics.progress_rate(400, 0) is None


def test_progress_rate_none_when_ytd_missing():
    assert metrics.progress_rate(None, 800) is None


# ---------------------------------------------------------------------------
# progress_vs_history
# ---------------------------------------------------------------------------

def test_progress_vs_history_computes_diff_and_n():
    result = metrics.progress_vs_history(55.0, [50.0, 52.0])
    assert result["n"] == 2
    assert result["diff_pt"] == pytest.approx(4.0)


def test_progress_vs_history_empty_history_returns_none_diff():
    result = metrics.progress_vs_history(55.0, [])
    assert result == {"diff_pt": None, "n": 0}


# ---------------------------------------------------------------------------
# guidance_gap (§1.3: n<3 なら median は None)
# ---------------------------------------------------------------------------

def test_guidance_gap_median_none_when_n_below_3():
    result = metrics.guidance_gap([(100.0, 110.0), (100.0, 90.0)])
    assert result["n"] == 2
    assert result["median"] is None
    assert result["values"] == pytest.approx([10.0, -10.0])


def test_guidance_gap_median_when_n_at_least_3():
    result = metrics.guidance_gap([(100.0, 110.0), (100.0, 90.0), (100.0, 100.0)])
    assert result["n"] == 3
    assert result["median"] == pytest.approx(0.0)


def test_guidance_gap_excludes_zero_forecast():
    result = metrics.guidance_gap([(0.0, 10.0), (100.0, 110.0)])
    assert result["n"] == 1
    assert result["values"] == pytest.approx([10.0])


# ---------------------------------------------------------------------------
# percentile_in_series (§4.3: start/end/n を返す)
# ---------------------------------------------------------------------------

def test_percentile_in_series_returns_pct_n_start_end():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    series = pd.Series([10, 20, 30, 40, 50], index=idx)
    result = metrics.percentile_in_series(series, 35)
    assert result["n"] == 5
    assert result["pct"] == pytest.approx(3 / 5 * 100)
    assert result["start"] == "2024-01-01"
    assert result["end"] == "2024-01-05"


def test_percentile_in_series_empty_series():
    series = pd.Series([], dtype=float)
    result = metrics.percentile_in_series(series, 10)
    assert result == {"pct": None, "n": 0, "start": None, "end": None}


# ---------------------------------------------------------------------------
# return_pct / relative_return
# ---------------------------------------------------------------------------

def test_return_pct_basic():
    close = pd.Series([100.0] * 20 + [110.0])
    assert metrics.return_pct(close, 20) == pytest.approx(10.0)


def test_return_pct_none_when_series_too_short():
    close = pd.Series([100.0, 105.0])
    assert metrics.return_pct(close, 21) is None


def test_relative_return_is_simple_diff():
    stock = pd.Series([100.0] * 5 + [110.0])
    bench = pd.Series([100.0] * 5 + [105.0])
    assert metrics.relative_return(stock, bench, 5) == pytest.approx(5.0)


def test_relative_return_none_when_either_missing():
    stock = pd.Series([100.0, 105.0])
    bench = pd.Series([100.0] * 10)
    assert metrics.relative_return(stock, bench, 5) is None


# ---------------------------------------------------------------------------
# volume_ratio
# ---------------------------------------------------------------------------

def test_volume_ratio_basic():
    volume = pd.Series([100] * 15 + [200] * 5)  # 直近5日=200, 直近20日平均=125
    ratio = metrics.volume_ratio(volume, 5, 20)
    assert ratio == pytest.approx(200 / 125)


def test_volume_ratio_none_when_series_too_short():
    volume = pd.Series([100] * 3)
    assert metrics.volume_ratio(volume, 5, 20) is None


# ---------------------------------------------------------------------------
# next_earnings_date (§1.4: 3段フォールバック)
# ---------------------------------------------------------------------------

def test_next_earnings_date_uses_calendar_first():
    date_, source = metrics.next_earnings_date(
        "7134", {"7134": "2026-11-07"}, [], None,
    )
    assert date_ == "2026-11-07"
    assert source == "カレンダー"


def test_next_earnings_date_estimates_from_raw_when_no_calendar():
    raw_records = [
        {"DiscDate": "2025-05-14", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "FY"},
        {"DiscDate": "2025-08-13", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "1Q"},
        {"DiscDate": "2024-08-09", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "1Q"},
    ]
    # 直近は1Qなので、次は2Q。だが2Qの過去実績が無いため推定不能 → None
    date_, source = metrics.next_earnings_date("7134", {}, raw_records, None)
    assert date_ is None


def test_next_earnings_date_estimates_next_quarter_from_prior_year():
    raw_records = [
        {"DiscDate": "2024-08-13", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "1Q"},
        {"DiscDate": "2024-11-12", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "2Q"},
        {"DiscDate": "2025-08-13", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "1Q"},
    ]
    # 直近は2025-08-13の1Q。次は2Q。前年の2Qは2024-11-12 → +365日
    date_, source = metrics.next_earnings_date("7134", {}, raw_records, None)
    assert date_ == "2025-11-12"
    assert source == "前年同期からの推定"


def test_next_earnings_date_ignores_forecast_revision_records():
    """DocType に FinancialStatements を含まないレコード(業績予想修正等)は対象外。"""
    raw_records = [
        {"DiscDate": "2024-08-13", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "1Q"},
        {"DiscDate": "2024-11-12", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "2Q"},
        {"DiscDate": "2025-09-01", "DocType": "ForecastRevision_Consolidated_JP", "CurPerType": None},
        {"DiscDate": "2025-08-13", "DocType": "FinancialStatements_Consolidated_JP", "CurPerType": "1Q"},
    ]
    date_, source = metrics.next_earnings_date("7134", {}, raw_records, None)
    assert date_ == "2025-11-12"
    assert source == "前年同期からの推定"


def test_next_earnings_date_falls_back_to_manual():
    date_, source = metrics.next_earnings_date("7611", {}, [], "2026-12-01")
    assert date_ == "2026-12-01"
    assert source == "手入力"


def test_next_earnings_date_unknown_when_nothing_available():
    date_, source = metrics.next_earnings_date("7611", {}, [], None)
    assert date_ is None
    assert source == "不明"
