import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.indicators import add_atr, add_moving_averages
from src.screener.vcp import _check_v5, evaluate_vcp, merge_shallow_pivots

# T0 (base origin) sits after a 100-day gradual run-up from 70 -> ~100.
RUNUP_DAYS = 100
RUNUP_START = 70.0
RUNUP_END = 100.0  # T0 price

# Base-region control points: (offset_from_T0, price). offset 0 = T0 itself.
# Each rally between contractions is kept >= the 3% zigzag threshold so it is
# confirmed as its own swing low, rather than merging into the next leg.
CLASSIC_CONTROL_POINTS = [
    (0, 100.0),     # T0 (peak)
    (6, 76.0),      # trough1: depth 24%
    (12, 90.0),     # peak2 (rally +18.4%)
    (18, 79.2),     # trough2: depth 12%
    (24, 85.0),     # peak3 (rally +7.3%)
    (30, 79.9),     # trough3: depth 6%
    (36, 83.5),     # peak4 (rally +4.5%)
    (42, 80.7245),  # trough4 (final, in progress): depth 3.3%
]

# Same shape, but trough3 is reached via a much higher peak3 so the third
# contraction is *deeper* than the second (18% vs 12%), breaking the
# monotonic non-increasing depth requirement (V2), while trough3 stays close
# enough to trough2 to keep V7 (no meaningful lower low) satisfied.
REVERSED_CONTROL_POINTS = [
    (0, 100.0),
    (6, 76.0),      # depth 24%
    (12, 90.0),
    (18, 79.2),     # depth 12%
    (24, 97.439),   # peak3 raised so the next drop is 18%, not 6%
    (30, 79.9),     # depth 18% (79.9 = 97.439 * (1-0.18))
    (36, 83.5),
    (42, 80.7245),  # depth 3.3%
]

# Same skeleton as REVERSED_CONTROL_POINTS (the depth reversal at trough3 vs.
# trough2 is unchanged: 18% > 12%*1.2), but with a flat price extension added
# after trough4 (same price repeated) that adds no new pivot -- compute_zigzag
# never confirms a new swing on a flat run, so the final provisional pivot
# stays anchored at trough4 (idx 42) regardless. This only inflates base_days
# (43 -> 64), which pushes trough3's *relative* position from 30/43 (~0.70,
# back half -- where REVERSED_CONTROL_POINTS' test expects V2 to fail) to
# 30/64 (~0.47, front half), so the exact same reversal is now forgivable
# under V2's early_violation_allowance.
#
# Note peak2/peak3/peak4 must all stay below T0 (100.0) for find_base_origin
# to keep anchoring the base at T0 -- a peak that exceeds T0 gets picked as
# the new origin instead, silently discarding everything before it. Combined
# with V7's requirement that lows never meaningfully fall, this means a
# depth reversal can never occur at the *very first* contraction step (its
# preceding low has no room below it to exceed T0 from), which is why the
# reversal here, like in REVERSED_CONTROL_POINTS, sits at step 3.
FRONT_HALF_VIOLATION_CONTROL_POINTS = [
    (0, 100.0),
    (6, 76.0),      # trough1: depth 24%
    (12, 90.0),
    (18, 79.2),     # trough2: depth 12%
    (24, 97.439),   # peak3 raised so the next drop is 18%, not 6%
    (30, 79.9),     # trough3: depth 18% (reversal vs. trough2's 12%, front half)
    (36, 83.5),
    (42, 80.7245),  # trough4 (final): depth ~3.3%
    (63, 80.7245),  # flat extension: no new pivot, only inflates base_days
]

# Same skeleton again, but the final contraction's depth is 11% -- inside the
# relaxed V4 ceiling (12%) but above the old, tighter one (10%) -- while the
# preceding contraction is loosened to 10% so no V2 step exceeds the 1.2x
# tolerance. Also used to check that the tightness SCORE (not just the MUST
# gate) still tells 11% apart from a "perfect" sub-5% base.
LOOSE_FINAL_DEPTH_CONTROL_POINTS = [
    (0, 100.0),     # T0
    (6, 76.0),      # trough1: depth 24%
    (12, 90.0),     # peak2
    (18, 79.2),     # trough2: depth 12%
    (24, 90.0),     # peak3
    (30, 81.0),     # trough3: depth 10%
    (36, 95.0),     # peak4
    (42, 84.55),    # trough4 (final): depth 11%
]

# A slight (<1%) undercut of trough1 at trough2 -- within the unchanged
# swing_low_tolerance (0.99) -- followed by peak3 exceeding peak2: a textbook
# shakeout (V7 stays satisfied; the undercut/recovery is a score-only bonus).
SHAKEOUT_CONTROL_POINTS = [
    (0, 100.0),     # T0
    (6, 80.0),      # trough1: depth 20%
    (12, 92.0),     # peak2
    (18, 79.6),     # trough2: 0.5% undercut of trough1 (79.6 / 80.0 = 0.995)
    (24, 98.0),     # peak3, higher than peak2 (92.0) -- shakeout confirmation
    (30, 92.12),    # trough3: depth 6%
    (36, 96.0),     # peak4
    (42, 93.12),    # trough4 (final): depth ~3%
]


def _interpolate(control_points: list[tuple[int, float]]) -> list[float]:
    closes = []
    for (o1, p1), (o2, p2) in zip(control_points, control_points[1:]):
        n = o2 - o1
        seg = np.linspace(p1, p2, n + 1)[:-1]
        closes.extend(seg.tolist())
    closes.append(control_points[-1][1])
    return closes


def _build_synthetic_df(control_points: list[tuple[int, float]]) -> pd.DataFrame:
    runup = list(np.linspace(RUNUP_START, RUNUP_END, RUNUP_DAYS, endpoint=False))
    base = _interpolate(control_points)
    closes = runup + base
    n = len(closes)

    dates = pd.bdate_range("2023-01-01", periods=n)

    # Volume: flat during run-up, then declining through the base (dry-up).
    base_len = len(base)
    base_volume = np.linspace(190_000, 60_000, base_len)
    volume = [200_000.0] * RUNUP_DAYS + base_volume.tolist()

    df = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": volume,
        }
    )
    df = add_moving_averages(df)  # gives vol_ma50
    df = add_atr(df, 20)
    return df


def test_vcp_watch_a_on_classic_four_contraction_pattern():
    df = _build_synthetic_df(CLASSIC_CONTROL_POINTS)
    result = evaluate_vcp(df)

    assert result["status"] == "WATCH_A", result
    assert all(result["must_flags"].values()), result["must_flags"]

    depths = [round(c["depth"] * 100) for c in result["contractions"]]
    assert depths == [24, 12, 6, 3], result["contractions"]
    assert result["vcp_score"] > 0
    assert result["footprint"].endswith("4T")


def test_vcp_v2_fails_on_depth_reversal():
    df = _build_synthetic_df(REVERSED_CONTROL_POINTS)
    result = evaluate_vcp(df)

    assert result["must_flags"]["V2"] is False
    assert result["status"] != "WATCH_A"
    # the reversal shouldn't trip V7 (lows aren't meaningfully undercut)
    assert result["must_flags"]["V7"] is True


def test_vcp_v2_allows_single_front_half_reversal():
    df = _build_synthetic_df(FRONT_HALF_VIOLATION_CONTROL_POINTS)
    result = evaluate_vcp(df)

    depths = [round(c["depth"] * 100) for c in result["contractions"]]
    assert depths == [24, 12, 18, 3], result["contractions"]
    low_idx = result["contractions"][2]["low_idx"]
    base_days = result["base_days"]
    assert low_idx / base_days < 0.5  # the reversal sits in the base's front half
    assert result["must_flags"]["V2"] is True, result["must_flags"]
    assert result["status"] == "WATCH_A", result


def test_v5_median_based_dryup_survives_single_day_spike():
    """A single 3x spike day at the tail of the base pulls the mean above the
    old dryup threshold (0.8) but leaves the median-based gate (0.85)
    unaffected -- this is exactly the robustness V5(a)'s rewrite is for.
    """
    config = load_config()
    normal_vol = 150.0
    spike_vol = normal_vol * 3
    volume = [normal_vol] * 49 + [spike_vol]
    base_df = pd.DataFrame({"volume": volume, "vol_ma50": [200.0] * 50})

    v5, diag = _check_v5(base_df, config)

    mean_ratio = (sum(volume[-10:]) / 10) / 200.0
    assert mean_ratio > 0.8  # a mean-based gate would have rejected this base
    assert diag["recent10_median"] == pytest.approx(normal_vol)
    assert diag["sub_a_pass"] is True
    assert v5 is True


def test_vcp_v7_shakeout_detected_and_scored():
    df = _build_synthetic_df(SHAKEOUT_CONTROL_POINTS)
    result = evaluate_vcp(df)

    assert result["must_flags"]["V7"] is True, result["must_flags"]
    assert result["status"] == "WATCH_A", result
    assert result["shakeout_detected"] is True
    assert result["vcp_diagnostics"]["v7"]["shakeout_detected"] is True
    assert result["components"]["shakeout_bonus"] == pytest.approx(5.0, abs=0.1)


def test_vcp_v4_relaxed_ceiling_but_tightness_score_not_perfect():
    df = _build_synthetic_df(LOOSE_FINAL_DEPTH_CONTROL_POINTS)
    result = evaluate_vcp(df)

    depths = [round(c["depth"] * 100) for c in result["contractions"]]
    assert depths == [24, 12, 10, 11], result["contractions"]
    assert result["must_flags"]["V4"] is True  # 11% <= new 12% ceiling
    assert result["status"] == "WATCH_A", result
    # 11% is well short of last_depth_perfect (5%): tightness shouldn't be maxed.
    assert 0 < result["components"]["tightness"] < 10


# --- (b) 包絡保存マージ: merge_shallow_pivots -------------------------------

def test_merge_shallow_pivots_folds_subthreshold_interior_leg():
    """min_depth 未満の内側スイングは独立収縮とせず隣接波へ畳み込む。

    ここでは 90H->89.1L(深さ~1%)が min_depth(2%)未満なので除去され、
    ピボット数が2つ減る(=収縮1本ぶん)。T0(idx0)と最終ピボットは保護される。
    """
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 5, "price": 80.0, "type": "L"},   # depth 20%
        {"idx": 10, "price": 90.0, "type": "H"},
        {"idx": 12, "price": 89.1, "type": "L"},  # 90->89.1 = 1% shallow
        {"idx": 18, "price": 95.0, "type": "H"},
        {"idx": 24, "price": 85.0, "type": "L", "provisional": True},
    ]
    merged = merge_shallow_pivots([dict(p) for p in pivots], 0.02)

    assert len(merged) == len(pivots) - 2
    assert merged[0]["idx"] == 0 and merged[0]["price"] == 100.0  # T0 kept
    assert merged[-1]["idx"] == 24  # final (provisional) pivot kept
    # 包絡(全体の高値・安値の輪郭)は不変。
    assert max(p["price"] for p in merged) == 100.0
    assert min(p["price"] for p in merged) == 80.0


def test_merge_shallow_pivots_absorbs_extreme_into_neighbor():
    """浅い脚が局所ピークを含む場合、そのピークは隣接H波へ吸収され消えない。

    95H を含む 95H->94L の脚が min_depth 未満で除去されるが、95 は隣の 90H より
    高いので隣接Hに吸収される(削除ではなくマージ=包絡の保存)。
    """
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 5, "price": 80.0, "type": "L"},
        {"idx": 10, "price": 95.0, "type": "H"},  # local peak, to be absorbed
        {"idx": 12, "price": 94.0, "type": "L"},  # 95->94 = ~1% shallow
        {"idx": 18, "price": 90.0, "type": "H"},  # neighbor H (lower than 95)
        {"idx": 24, "price": 82.0, "type": "L", "provisional": True},
    ]
    merged = merge_shallow_pivots([dict(p) for p in pivots], 0.02)

    assert len(merged) == len(pivots) - 2
    # 95 のピークは消えず、隣接Hに idx ごと吸収されている。
    assert any(p["price"] == 95.0 and p["idx"] == 10 for p in merged)


def test_merge_shallow_pivots_noop_when_disabled():
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 5, "price": 80.0, "type": "L"},
        {"idx": 10, "price": 90.0, "type": "H"},
        {"idx": 12, "price": 89.1, "type": "L"},
        {"idx": 18, "price": 95.0, "type": "H"},
        {"idx": 24, "price": 85.0, "type": "L"},
    ]
    assert merge_shallow_pivots([dict(p) for p in pivots], 0.0) == pivots


# --- (d) ボラ過大除外: TOO_VOLATILE ----------------------------------------

def test_vcp_too_volatile_excluded_before_v_checks():
    """ATR/close が atr_exclude_threshold(9%)を超える銘柄は V判定前に除外。"""
    n = 160
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = np.linspace(70.0, 100.0, n)
    # 日々の高安レンジを終値の約14%に設定 → ATR20/close ≈ 0.13 > 0.09。
    high = close * 1.07
    low = close * 0.93
    df = pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": [150_000.0] * n,
        }
    )
    df = add_moving_averages(df)
    df = add_atr(df, 20)

    result = evaluate_vcp(df)

    assert result["status"] == "TOO_VOLATILE", result
    assert result["must_flags"] is None
    assert result["atr_ratio"] > 0.09
