import pandas as pd
import pytest

from src.screener.vcp import compute_zigzag, extract_contractions


def _make_base_df(prices: list[float]) -> pd.DataFrame:
    # Flat bars (high == low == close) so pivot prices exactly match the
    # intended synthetic swing points, with no intraday-range noise.
    dates = pd.bdate_range("2024-01-01", periods=len(prices))
    return pd.DataFrame(
        {"date": dates, "open": prices, "high": prices, "low": prices, "close": prices}
    )


def test_zigzag_detects_known_swing_points_at_3pct_threshold():
    # Hand-built path with known swing points at idx 0 (H=100), 3 (L=88),
    # 5 (H=96), and a final in-progress low at idx 7 (L=89, provisional).
    prices = [100.0, 99.0, 90.0, 88.0, 93.0, 96.0, 90.0, 89.0, 91.0, 89.5]
    df = _make_base_df(prices)

    pivots = compute_zigzag(df, threshold=0.03)

    assert [p["idx"] for p in pivots] == [0, 3, 5, 7]
    assert [p["type"] for p in pivots] == ["H", "L", "H", "L"]
    assert pivots[0]["price"] == 100.0
    assert pivots[1]["price"] == 88.0
    assert pivots[2]["price"] == 96.0
    assert pivots[3]["price"] == 89.0
    assert pivots[3].get("provisional") is True
    assert "provisional" not in pivots[0]
    assert "provisional" not in pivots[1]
    assert "provisional" not in pivots[2]


def test_extract_contractions_computes_expected_depths():
    prices = [100.0, 99.0, 90.0, 88.0, 93.0, 96.0, 90.0, 89.0, 91.0, 89.5]
    df = _make_base_df(prices)
    pivots = compute_zigzag(df, threshold=0.03)
    contractions = extract_contractions(pivots)

    assert len(contractions) == 2
    assert contractions[0]["depth"] == pytest.approx((100 - 88) / 100)
    assert contractions[1]["depth"] == pytest.approx((96 - 89) / 96)
    assert contractions[1]["provisional"] is True
    assert contractions[0]["provisional"] is False


def test_zigzag_no_reversal_yields_single_pivot():
    # Monotonic decline never confirms a full contraction pair.
    prices = [100.0, 99.5, 99.0, 98.5, 98.0]
    df = _make_base_df(prices)
    pivots = compute_zigzag(df, threshold=0.03)
    contractions = extract_contractions(pivots)
    assert contractions == []
