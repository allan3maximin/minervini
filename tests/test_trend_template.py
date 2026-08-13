import pytest

from src.config import load_config
from src.screener.trend_template import (
    attach_score_percentiles,
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
    "ma200_slope_21d": 0.08,
    "dryup_med_10_50": 0.8,
    "low_52w": 100.0,
    "high_52w": 160.0,
    "rs": 80,
}


def _scored(latest: dict) -> float:
    """単独銘柄でも採点できるよう、自分だけの断面を作って tech_score を出す。"""
    by_code = {"X": dict(latest)}
    attach_score_percentiles(by_code, CONFIG)
    return technical_score(by_code["X"], CONFIG)


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
        ({"ma200_slope_21d": 0.02}, "ma200_uptrend_1m"),
        ({"ma200_slope_21d": None}, "ma200_uptrend_1m"),
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
        "cond3_ma200_slope_too_flat",
        "cond3_ma200_slope_missing",
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


def test_ma200_slope_boundary_is_inclusive():
    """ちょうど下限(+5%)は通す。境界を跨いだ瞬間に落ちる。"""
    floor = CONFIG["trend_template"]["ma200_slope_21d_min"]
    on = dict(BASELINE, ma200_slope_21d=floor)
    under = dict(BASELINE, ma200_slope_21d=floor - 0.001)
    assert check_must_conditions(on, CONFIG)["ma200_uptrend_1m"] is True
    assert check_must_conditions(under, CONFIG)["ma200_uptrend_1m"] is False


def test_technical_score_is_higher_for_stronger_stock():
    """3変数(MA200の21日傾き / 52週安値からの倍率 / 枯れ度)全部が上の銘柄が上に来る。"""
    # weak 側も MUST は通す必要がある(落ちると採点母集団から外れて None になる)。
    # 傾きの下限が 5% になったので 0.005 は使えない。
    weak = dict(BASELINE, ma200_slope_21d=0.052, close=142.0, dryup_med_10_50=1.6)
    strong = dict(BASELINE, ma200_slope_21d=0.09, close=155.0, dryup_med_10_50=0.5)
    by_code = {"WEAK": weak, "STRONG": strong}
    attach_score_percentiles(by_code, CONFIG)
    assert technical_score(weak, CONFIG) < technical_score(strong, CONFIG)
    assert 0 <= technical_score(weak, CONFIG) <= 100
    assert 0 <= technical_score(strong, CONFIG) <= 100


def test_technical_score_ignores_rs_and_near_high():
    """RS と 52週高値への近さは MUST 条件のまま。スコアには一切効かない
    (2026-07-29改定。26年検証で足し戻すと期待Rが下がったため)。"""
    a = dict(BASELINE, rs=70, high_52w=195.0)
    b = dict(BASELINE, rs=99, high_52w=151.0)
    by_code = {"A": a, "B": b}
    attach_score_percentiles(by_code, CONFIG)
    assert technical_score(a, CONFIG) == technical_score(b, CONFIG)


def test_technical_score_is_none_without_cross_section():
    """断面ランクを付けていない latest からは原理的に採点できない。"""
    assert technical_score(dict(BASELINE), CONFIG) is None


def test_attach_score_percentiles_skips_must_failures():
    """母集団はその日のMUST通過銘柄のみ。落ちた銘柄は採点対象に入れない。"""
    passer = dict(BASELINE)
    failer = dict(BASELINE, rs=50)  # RS 70未満でMUST落ち
    by_code = {"PASS": passer, "FAIL": failer}
    attach_score_percentiles(by_code, CONFIG)
    assert "score_pct" in passer
    assert "score_pct" not in failer
    assert technical_score(failer, CONFIG) is None


def test_technical_score_is_equal_weighted():
    """等ウェイト = 3成分パーセンタイルの単純平均。重みづけを持ち込まない。"""
    latest = dict(BASELINE)
    by_code = {"A": latest, "B": dict(BASELINE, ma200_slope_21d=0.9, close=159.0,
                                      dryup_med_10_50=0.1)}
    attach_score_percentiles(by_code, CONFIG)
    pct = latest["score_pct"]
    assert set(pct) == {"ma200_slope", "low52w_ratio", "dryup"}
    assert technical_score(latest, CONFIG) == round(sum(pct.values()) / 3, 1)


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


def _baseline_with_percentiles() -> dict:
    by_code = {"X": dict(BASELINE)}
    attach_score_percentiles(by_code, CONFIG)
    return by_code["X"]


def test_compute_full_score_partial_renormalizes_to_100_scale():
    latest = _baseline_with_percentiles()
    result = compute_full_score(latest, eps_quarters=None, revenue_quarters=None, monthly_yoy=None)
    # only the technical component is available -> renormalized using tech weight only
    assert 0 <= result["full_score"] <= 100
    assert result["eps_accel_slope"] is None
    assert result["rev_accel_slope"] is None


def test_compute_full_score_full_coverage_within_100():
    quarters = _make_quarters([10, 10, 10, 10, 12, 15, 19, 25])
    result = compute_full_score(
        _baseline_with_percentiles(), eps_quarters=quarters, revenue_quarters=quarters,
        monthly_yoy=15.0,
    )
    assert 0 <= result["full_score"] <= 100
    assert result["eps_accel_slope"] > 0


def test_compute_full_score_falls_back_to_fundamentals_without_cross_section():
    """断面が無い(tech_score が None)ときは技術成分を欠測として割り戻す。"""
    quarters = _make_quarters([10, 10, 10, 10, 12, 15, 19, 25])
    result = compute_full_score(
        dict(BASELINE), eps_quarters=quarters, revenue_quarters=quarters, monthly_yoy=15.0
    )
    assert "technical" not in result["component_points"]
    assert 0 <= result["full_score"] <= 100
