import pytest

from src.config import load_config
from src.screener.trend_template import (
    check_must_conditions,
    compute_accel_slope,
    compute_full_score,
    passes_trend_template,
    technical_score,
)

CONFIG = load_config()

BASELINE = {
    "close": 150.0,
    "ma50": 140.0,
    "ma150": 130.0,
    "ma200": 120.0,
    "ma200_slope_days": 30,
    "low_52w": 100.0,
    "high_52w": 160.0,
    "rs": 80,
}


def test_all_conditions_pass_on_baseline():
    flags = check_must_conditions(BASELINE, CONFIG)
    assert all(flags.values())
    assert passes_trend_template(flags)


@pytest.mark.parametrize(
    "mutation,expected_false_key",
    [
        ({"close": 100.0}, "close_above_ma150_ma200"),
        ({"ma150": 110.0}, "ma150_above_ma200"),
        ({"ma200_slope_days": 10}, "ma200_uptrend_1m"),
        ({"ma50": 115.0}, "ma_stack_50_150_200"),
        ({"close": 135.0}, "close_above_ma50"),
        ({"low_52w": 200.0}, "above_low52w_margin"),
        ({"high_52w": 400.0}, "within_high52w_margin"),
        ({"rs": 50}, "rs_above_min"),
    ],
    ids=[
        "cond1_close_below_ma150_ma200",
        "cond2_ma150_below_ma200",
        "cond3_ma200_not_uptrend",
        "cond4_ma_stack_broken",
        "cond5_close_below_ma50",
        "cond6_below_low52w_margin",
        "cond7_outside_high52w_margin",
        "cond8_rs_below_min",
    ],
)
def test_each_condition_fails_independently(mutation, expected_false_key):
    latest = dict(BASELINE)
    latest.update(mutation)
    flags = check_must_conditions(latest, CONFIG)
    assert flags[expected_false_key] is False
    assert passes_trend_template(flags) is False


def test_technical_score_is_higher_for_stronger_stock():
    weak = dict(BASELINE, rs=70, ma200_slope_days=0, high_52w=200.0)
    strong = dict(BASELINE, rs=99, ma200_slope_days=105, high_52w=150.0)
    assert technical_score(weak, CONFIG) < technical_score(strong, CONFIG)
    assert 0 <= technical_score(weak, CONFIG) <= 100
    assert 0 <= technical_score(strong, CONFIG) <= 100


def _make_quarters(values: list[float], start_year: int = 2024, start_q: int = 1) -> list[dict]:
    quarters = []
    year, q = start_year, start_q
    for v in values:
        quarters.append({"fiscal_quarter": f"{year}Q{q}", "eps": v, "revenue": v * 10})
        q += 1
        if q > 4:
            q = 1
            year += 1
    return quarters


def test_compute_accel_slope_positive_for_accelerating_growth():
    # 8 quarters: prior year flat at 10, current year accelerating 11,12,14,17
    quarters = _make_quarters([10, 10, 10, 10, 11, 12, 14, 17])
    slope = compute_accel_slope(quarters, "eps")
    assert slope is not None
    assert slope > 0


def test_compute_accel_slope_none_when_insufficient_quarters():
    quarters = _make_quarters([10, 11])
    assert compute_accel_slope(quarters, "eps") is None


def test_compute_full_score_partial_renormalizes_to_100_scale():
    result = compute_full_score(BASELINE, eps_quarters=None, revenue_quarters=None, monthly_yoy=None)
    # only the technical component is available -> renormalized using tech weight only
    assert 0 <= result["full_score"] <= 100
    assert result["eps_accel_slope"] is None
    assert result["rev_accel_slope"] is None


def test_compute_full_score_full_coverage_within_100():
    quarters = _make_quarters([10, 10, 10, 10, 12, 15, 19, 25])
    result = compute_full_score(
        BASELINE, eps_quarters=quarters, revenue_quarters=quarters, monthly_yoy=15.0
    )
    assert 0 <= result["full_score"] <= 100
    assert result["eps_accel_slope"] > 0
