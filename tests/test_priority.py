import pytest

from src.config import load_config
from src.screener.priority import (
    COND_CLOSE_ABOVE_MA50,
    COND_MA50_ABOVE_MA150,
    COND_NEAR_HIGH52W,
    COND_RS_MIN,
    evaluate_priority,
    passes_hard_filters,
    priority_counts,
    priority_sort_key,
)

CONFIG = load_config()

# 8条件完全一致 (P1) のベースライン
BASELINE = {
    "close": 150.0,
    "ma50": 140.0,
    "ma150": 130.0,
    "ma200": 120.0,
    "ma200_slope_days": 30,
    "ma200_slope_21d": 0.08,
    "low_52w": 100.0,
    "high_52w": 160.0,
    "rs": 80,
}


def make(**overrides):
    latest = dict(BASELINE)
    latest.update(overrides)
    return latest


# ------------------------------------------------------------- ハードフィルタ


def test_baseline_passes_hard_filters():
    assert passes_hard_filters(BASELINE, CONFIG)


@pytest.mark.parametrize(
    "mutation",
    [
        {"ma150": 110.0},          # 150MA <= 200MA
        {"ma200_slope_days": 10},  # 200MA上向き1ヶ月未満
        {"ma200_slope_21d": 0.02},  # 向きはあるが21日上昇率が+5%未満 (2026-08-13 A-2)
        {"ma200_slope_21d": None},  # 21日上昇率が取れない(履歴不足)
        {"low_52w": 125.0},        # 52週安値+25%未満 (150 < 125*1.25=156.25)
    ],
)
def test_hard_filter_failure_returns_none(mutation):
    latest = make(**mutation)
    assert not passes_hard_filters(latest, CONFIG)
    assert evaluate_priority(latest, CONFIG) is None


def test_low52w_margin_boundary_inclusive():
    # ちょうど +25% は通過 (close >= low*1.25)
    latest = make(low_52w=120.0)  # 120*1.25 = 150 = close
    assert passes_hard_filters(latest, CONFIG)


# ------------------------------------------------------------------ ペナルティ


def test_p1_zero_penalty():
    result = evaluate_priority(BASELINE, CONFIG)
    assert result["penalty"] == 0
    assert result["priority"] == 1
    assert result["unmet"] == []
    assert result["rs"] == 80


def test_close_below_ma50_penalty_1():
    result = evaluate_priority(make(close=135.0, high_52w=150.0), CONFIG)
    assert result["penalty"] == 1
    assert result["priority"] == 2
    unmet = {u["condition"]: u for u in result["unmet"]}
    assert unmet[COND_CLOSE_ABOVE_MA50]["penalty"] == 1
    # 距離は負 (50MAより下)
    assert unmet[COND_CLOSE_ABOVE_MA50]["distance_pct"] < 0


def test_high52w_distance_boundaries():
    # ちょうど25% → ペナルティなし
    result = evaluate_priority(make(high_52w=200.0), CONFIG)  # (200-150)/200=25%
    assert result["penalty"] == 0

    # 25%超〜35%以下 → +1
    result = evaluate_priority(make(high_52w=210.0), CONFIG)  # 28.57%
    assert result["penalty"] == 1
    assert result["unmet"][0]["condition"] == COND_NEAR_HIGH52W
    assert result["unmet"][0]["penalty"] == 1

    # ちょうど35% → +1 のまま (50MA割れを避けるためMA群も下げる)
    result = evaluate_priority(
        make(close=130.0, ma50=125.0, ma150=120.0, ma200=110.0, high_52w=200.0), CONFIG
    )  # 35%
    assert result["penalty"] == 1

    # 35%超 → +2
    result = evaluate_priority(
        make(close=126.0, ma50=125.0, ma150=120.0, ma200=110.0, high_52w=210.0), CONFIG
    )  # 40%
    assert result["penalty"] == 2
    assert result["unmet"][0]["penalty"] == 2
    assert result["priority"] == 3


def test_ma50_below_ma150_penalty_2():
    result = evaluate_priority(make(ma50=125.0), CONFIG)
    unmet = {u["condition"]: u for u in result["unmet"]}
    assert unmet[COND_MA50_ABOVE_MA150]["penalty"] == 2
    assert result["priority"] == 3


def test_rs_boundaries():
    # RS=70 ちょうど → ペナルティなし
    assert evaluate_priority(make(rs=70), CONFIG)["penalty"] == 0

    # 60 <= RS < 70 → +1
    result = evaluate_priority(make(rs=65), CONFIG)
    assert result["penalty"] == 1
    unmet = result["unmet"][0]
    assert unmet["condition"] == COND_RS_MIN
    assert unmet["penalty"] == 1
    assert unmet["distance_pct"] == -5  # rs - rs_min

    # RS=60 ちょうど → +1
    assert evaluate_priority(make(rs=60), CONFIG)["penalty"] == 1

    # RS < 60 → +3
    result = evaluate_priority(make(rs=59), CONFIG)
    assert result["penalty"] == 3
    assert result["unmet"][0]["penalty"] == 3
    assert result["priority"] == 3


def test_p4_accumulated_penalties():
    # 50MA以下(+1) + 並び崩れ(+2) + RS<60(+3) = 6 → P4
    result = evaluate_priority(make(close=131.0, ma50=140.0, ma150=135.0, rs=50, high_52w=160.0), CONFIG)
    assert result is not None
    assert result["penalty"] >= 4
    assert result["priority"] == 4


def test_ma_deviation_signs():
    result = evaluate_priority(BASELINE, CONFIG)
    dev = result["ma_deviation_pct"]
    assert dev["ma50"] == pytest.approx(7.14, abs=0.01)
    assert dev["ma150"] > dev["ma50"] > 0
    assert dev["ma200"] > dev["ma150"]


# ------------------------------------------------------------- ソート・集計


def test_priority_sort_key_order():
    records = [
        {"priority": 2, "rs": 90},
        {"priority": 1, "rs": 75},
        {"priority": 1, "rs": 95},
        {"priority": None, "rs": 99},
    ]
    ordered = sorted(records, key=priority_sort_key)
    assert [(r["priority"], r["rs"]) for r in ordered] == [
        (1, 95),
        (1, 75),
        (2, 90),
        (None, 99),
    ]


def test_priority_counts():
    evals = [
        {"priority": 1},
        {"priority": 1},
        {"priority": 2},
        {"priority": 4},
        None,
    ]
    assert priority_counts(evals) == {"p1": 2, "p2": 1, "p3": 0, "p4": 1}
