import numpy as np
import pandas as pd

from src.indicators import add_atr, add_moving_averages
from src.screener.vcp import evaluate_vcp

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
