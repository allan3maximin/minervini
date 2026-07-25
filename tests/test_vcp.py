import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.indicators import add_atr, add_moving_averages
from src.screener.vcp import (
    _check_v5,
    evaluate_vcp,
    extract_contractions,
    merge_shallow_pivots,
    merge_short_contractions,
    merge_short_legs,
    merge_short_rallies,
    vcp_quality_score,
)

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


def _build_synthetic_df(
    control_points: list[tuple[int, float]], runup_days: int = RUNUP_DAYS
) -> pd.DataFrame:
    runup = list(np.linspace(RUNUP_START, RUNUP_END, runup_days, endpoint=False))
    base = _interpolate(control_points)
    closes = runup + base
    n = len(closes)

    dates = pd.bdate_range("2023-01-01", periods=n)

    # Volume: flat during run-up, then declining through the base (dry-up).
    base_len = len(base)
    base_volume = np.linspace(190_000, 60_000, base_len)
    volume = [200_000.0] * runup_days + base_volume.tolist()

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


# Short base: T0 -> one 24% contraction -> partial rally, only 11 base days
# (< base_min_days 15) so evaluate_vcp returns IMMATURE. days_from_high (10)
# stays >= min_days_from_high (5) so it doesn't fall into TOO_RECENT instead.
IMMATURE_CONTROL_POINTS = [
    (0, 100.0),     # T0
    (6, 76.0),      # trough1: depth 24%
    (10, 88.0),     # partial recovery
]


def test_vcp_immature_returns_contractions_for_charting():
    """IMMATURE(ベース熟成中)でも形成途中の収縮を描画用に返す。判定系
    (must_flags/vcp_score/footprint)は従来どおり付けない。"""
    df = _build_synthetic_df(IMMATURE_CONTROL_POINTS, runup_days=150)
    result = evaluate_vcp(df)

    assert result["status"] == "IMMATURE", result
    assert result["must_flags"] is None
    assert result["vcp_score"] is None
    assert "footprint" not in result

    contractions = result["contractions"]
    assert len(contractions) == 1, contractions
    c = contractions[0]
    assert c["high_price"] == pytest.approx(100.0)
    assert c["low_price"] == pytest.approx(76.0)
    # チャート描画に必須の日付が文字列で付与され、時系列順になっている
    assert c["high_date"] < c["low_date"]
    assert result["t0_date"] is not None


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


def _tightness_gate_score(dryup_med: float | None):
    """案Y検証用ヘルパ: dryup_med(=recent10_median/vol_ma50)を与えて
    vcp_quality_score の tightness コンポーネントを返す。depths[-1]=3% は
    last_depth_perfect(5%)未満なので、加点されれば満点(30)になる。"""
    config = load_config()
    contractions = [
        {"depth": 0.24, "high_price": 100.0, "low_price": 76.0},
        {"depth": 0.03, "high_price": 83.5, "low_price": 80.7},
    ]
    base_df = pd.DataFrame({"volume": [150.0] * 30, "vol_ma50": [200.0] * 30})
    if dryup_med is None:
        v5 = {"recent10_median": None, "vol_ma50": None}
    else:
        v5 = {"recent10_median": dryup_med * 200.0, "vol_ma50": 200.0}
    diagnostics = {"v5": v5}
    return vcp_quality_score(contractions, 30, base_df, config, diagnostics)["components"]["tightness"]


def test_tightness_credited_when_dry():
    # dryup_med 0.50 < mild(0.77): 枯れ銘柄なので tightness 満点が付く。
    assert _tightness_gate_score(0.50) == pytest.approx(30.0, abs=0.1)


def test_tightness_zeroed_when_not_dry():
    # dryup_med 0.90 >= mild(0.77): 枯れ不足なので tightness 加点はゼロ(案Y)。
    assert _tightness_gate_score(0.90) == 0.0


def test_tightness_credited_when_dryup_unknown():
    # dryup_med が取れない(None)場合はゲート無効=保守側で従来どおり加点。
    assert _tightness_gate_score(None) == pytest.approx(30.0, abs=0.1)


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


def test_merge_shallow_pivots_keeps_pivots_chronological():
    """両隣が同時に極値を吸収しても、ピボット列の時系列順は壊さない。

    H(0,100.2) と L(15,100.1) が除去ペアの極値を同時に吸収すると、高値が idx10、
    安値が idx5 へ移り前後関係が逆転する(=収縮が時間的に重なりチャートの
    ジグザグが逆走する)。順序を壊す吸収はロールバックされる。
    """
    pivots = [
        {"idx": 0, "price": 100.2, "type": "H"},
        {"idx": 5, "price": 100.0, "type": "L"},
        {"idx": 10, "price": 100.3, "type": "H"},
        {"idx": 15, "price": 100.1, "type": "L"},
    ]
    merged = merge_shallow_pivots([dict(p) for p in pivots], 0.05)

    assert len(merged) == 2
    assert [p["idx"] for p in merged] == sorted(p["idx"] for p in merged)
    assert merged[0]["type"] == "H" and merged[1]["type"] == "L"


# --- (c) 期間マージ: merge_short_contractions -------------------------------

def test_merge_short_contractions_folds_single_bar_contraction():
    """1本の足で完結する収縮(high_idx == low_idx)は収縮として数えない。

    Minerviniの収縮は日〜週で形成される調整であって、1本の足の高安レンジは
    収縮ではない。ここでは idx18 の 0日収縮が畳まれ、収縮1本ぶん(ピボット2つ)減る。
    """
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 8, "price": 80.0, "type": "L"},
        {"idx": 14, "price": 92.0, "type": "H"},
        {"idx": 20, "price": 85.0, "type": "L"},
        {"idx": 26, "price": 90.0, "type": "H"},   # 同一足で完結する収縮
        {"idx": 26, "price": 86.0, "type": "L", "provisional": True},
    ]
    merged = merge_short_contractions([dict(p) for p in pivots], 2)

    assert [(p["idx"], p["type"]) for p in merged] == [
        (0, "H"), (8, "L"), (14, "H"), (20, "L")
    ]
    assert all(c["low_idx"] - c["high_idx"] >= 2 for c in extract_contractions(merged))


def test_merge_short_contractions_preserves_envelope_and_order():
    """畳んだ収縮の極値は隣接波へ吸収され、包絡と時系列順は保たれる。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 8, "price": 80.0, "type": "L"},
        {"idx": 14, "price": 95.0, "type": "H"},  # 局所ピーク: 隣接Hへ吸収される
        {"idx": 15, "price": 88.0, "type": "L"},  # 1日収縮
        {"idx": 22, "price": 90.0, "type": "H"},  # 95 より低い隣接H
        {"idx": 30, "price": 84.0, "type": "L"},
    ]
    merged = merge_short_contractions([dict(p) for p in pivots], 2)

    assert len(merged) == len(pivots) - 2
    assert [p["idx"] for p in merged] == sorted(p["idx"] for p in merged)
    assert max(p["price"] for p in merged) == 100.0
    assert min(p["price"] for p in merged) == 80.0
    # 95 のピークは削除ではなく隣接Hへ吸収されている。
    assert any(p["price"] == 95.0 and p["idx"] == 14 for p in merged)


def test_merge_short_contractions_keeps_base_anchored_at_t0():
    """初回収縮が短くても、T0(ベース最高値)は次のHに吸収されアンカーは残る。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},  # T0
        {"idx": 1, "price": 93.0, "type": "L"},   # 1日収縮
        {"idx": 10, "price": 96.0, "type": "H"},
        {"idx": 20, "price": 88.0, "type": "L"},
        {"idx": 28, "price": 94.0, "type": "H"},
        {"idx": 36, "price": 90.0, "type": "L"},
    ]
    merged = merge_short_contractions([dict(p) for p in pivots], 2)

    assert len(merged) == len(pivots) - 2
    assert merged[0]["idx"] == 0 and merged[0]["price"] == 100.0


def test_merge_short_contractions_never_empties_the_base():
    """収縮が1本しか残らない状態では、それ以上畳まない(ベースを空にしない)。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 0, "price": 94.0, "type": "L"},
    ]
    assert merge_short_contractions([dict(p) for p in pivots], 5) == pivots


def test_merge_short_contractions_noop_when_disabled():
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 0, "price": 90.0, "type": "L"},
        {"idx": 10, "price": 95.0, "type": "H"},
        {"idx": 11, "price": 88.0, "type": "L"},
    ]
    assert merge_short_contractions([dict(p) for p in pivots], 0) == pivots


# --- (c-2) 期間マージ: merge_short_rallies ----------------------------------

def test_merge_short_rallies_folds_zero_bar_rally():
    """T(N)の安値とT(N+1)の高値が同じ足に乗る「0日戻し」は戻し脚として数えない。

    VCPは「高値 → 押し → 戻し → より浅い押し」の繰り返しで、戻しにも時間がかかる
    のが前提。0日戻しは1本の広いレンジ足を2つの波に割っているだけなので畳む。
    """
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 8, "price": 80.0, "type": "L"},
        {"idx": 8, "price": 92.0, "type": "H"},   # 0日戻し (L と同じ足)
        {"idx": 16, "price": 85.0, "type": "L"},
        {"idx": 24, "price": 90.0, "type": "H"},
        {"idx": 32, "price": 87.0, "type": "L"},
    ]
    merged = merge_short_rallies([dict(p) for p in pivots], 1)

    assert len(merged) == len(pivots) - 2
    cons = extract_contractions(merged)
    rallies = [b["high_idx"] - a["low_idx"] for a, b in zip(cons, cons[1:])]
    assert all(r >= 1 for r in rallies)


def test_merge_short_rallies_preserves_envelope_and_order():
    """畳んだ戻しの極値は隣接波へ吸収され、包絡と時系列順は保たれる。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 8, "price": 78.0, "type": "L"},   # ベース最安値
        {"idx": 14, "price": 95.0, "type": "H"},
        {"idx": 20, "price": 80.0, "type": "L"},  # 局所ボトム: 隣接Lへ吸収される
        {"idx": 20, "price": 90.0, "type": "H"},  # 0日戻し
        {"idx": 30, "price": 86.0, "type": "L"},
    ]
    merged = merge_short_rallies([dict(p) for p in pivots], 1)

    assert len(merged) == len(pivots) - 2
    assert [p["idx"] for p in merged] == sorted(p["idx"] for p in merged)
    assert max(p["price"] for p in merged) == 100.0
    assert min(p["price"] for p in merged) == 78.0
    # 80.0 のボトムは削除ではなく隣接Lへ吸収されている。
    assert any(p["price"] == 80.0 and p["idx"] == 20 for p in merged)


def test_merge_short_rallies_keeps_base_anchored_at_t0():
    """戻しを畳んでも T0(ベース最高値)のアンカーは先頭に残る。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},  # T0
        {"idx": 10, "price": 88.0, "type": "L"},
        {"idx": 11, "price": 96.0, "type": "H"},  # 1日戻し
        {"idx": 20, "price": 90.0, "type": "L"},
        {"idx": 28, "price": 94.0, "type": "H"},
        {"idx": 36, "price": 92.0, "type": "L"},
    ]
    merged = merge_short_rallies([dict(p) for p in pivots], 3)

    assert merged[0]["idx"] == 0 and merged[0]["price"] == 100.0
    assert [p["idx"] for p in merged] == sorted(p["idx"] for p in merged)


def test_merge_short_rallies_never_empties_the_base():
    """収縮が1本しか残らない状態では、それ以上畳まない(ベースを空にしない)。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 5, "price": 94.0, "type": "L"},
        {"idx": 5, "price": 98.0, "type": "H"},
        {"idx": 12, "price": 95.0, "type": "L"},
    ]
    merged = merge_short_rallies([dict(p) for p in pivots], 5)

    assert len(merged) == 2
    assert merged[0]["type"] == "H" and merged[1]["type"] == "L"


def test_merge_short_rallies_noop_when_disabled():
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 10, "price": 90.0, "type": "L"},
        {"idx": 10, "price": 95.0, "type": "H"},
        {"idx": 20, "price": 92.0, "type": "L"},
    ]
    assert merge_short_rallies([dict(p) for p in pivots], 0) == pivots


def test_merge_short_legs_applies_both_floors_until_settled():
    """収縮マージと戻しマージを交互に回し、どちらの床も満たすまで畳む。

    片方のマージが新しい短脚を生むことがあるので、変化が止まるまで往復する。
    """
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 10, "price": 80.0, "type": "L"},
        {"idx": 10, "price": 92.0, "type": "H"},   # 0日戻し
        {"idx": 11, "price": 86.0, "type": "L"},   # 1日収縮
        {"idx": 20, "price": 90.0, "type": "H"},
        {"idx": 30, "price": 88.0, "type": "L"},
    ]
    merged = merge_short_legs([dict(p) for p in pivots], 2, 1)

    cons = extract_contractions(merged)
    assert all(c["low_idx"] - c["high_idx"] >= 2 for c in cons)
    rallies = [b["high_idx"] - a["low_idx"] for a, b in zip(cons, cons[1:])]
    assert all(r >= 1 for r in rallies)
    assert [p["idx"] for p in merged] == sorted(p["idx"] for p in merged)


def test_merge_short_legs_is_idempotent():
    """一度収束した列をもう一度通しても変化しない。"""
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 10, "price": 80.0, "type": "L"},
        {"idx": 10, "price": 92.0, "type": "H"},
        {"idx": 18, "price": 85.0, "type": "L"},
        {"idx": 26, "price": 90.0, "type": "H"},
        {"idx": 34, "price": 88.0, "type": "L"},
    ]
    once = merge_short_legs([dict(p) for p in pivots], 2, 1)
    twice = merge_short_legs([dict(p) for p in once], 2, 1)
    assert once == twice


def test_merge_short_legs_noop_when_both_disabled():
    pivots = [
        {"idx": 0, "price": 100.0, "type": "H"},
        {"idx": 10, "price": 90.0, "type": "L"},
        {"idx": 10, "price": 95.0, "type": "H"},
        {"idx": 11, "price": 92.0, "type": "L"},
    ]
    assert merge_short_legs([dict(p) for p in pivots], 0, 0) == pivots


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
