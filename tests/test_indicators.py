"""指標レイヤーの回帰テスト。

主眼は「同じ概念の二重実装がドリフトしていないこと」の常時保証。
枯れ度(dryup_med_10_50)は point-in-time の dryup_metrics() が正準実装で、
add_dryup_series() は全行一括版。両者が食い違ったら summary.py 事故の再来なので
ここで必ず落とす。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators import add_dryup_series, add_ma200_slope_21d, compute_all, dryup_metrics


def _synthetic_frame(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0012, 0.015, n))
    high = close * (1.0 + rng.uniform(0.0, 0.02, n))
    low = close * (1.0 - rng.uniform(0.0, 0.02, n))
    volume = rng.integers(50_000, 500_000, n).astype("float64")
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2023-01-02", periods=n),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_dryup_series_matches_pointwise_canonical_implementation():
    df = compute_all(_synthetic_frame())
    # 末尾だけでなく複数の位置で突合する(末尾だけだと窓の端の扱い違いを見逃す)。
    for idx in (-1, -2, -50, -120):
        expected = dryup_metrics(df, idx)["dryup_med_10_50"]
        actual = df["dryup_med_10_50"].to_numpy()[idx]
        assert expected is not None
        assert round(float(actual), 4) == expected


def test_ma200_slope_21d_is_positive_for_rising_ma200():
    df = pd.DataFrame({"ma200": pd.Series(np.linspace(100.0, 200.0, 300))})
    out = add_ma200_slope_21d(df)
    assert out["ma200_slope_21d"].iloc[-1] > 0
    # 最初の21行は前値が無いので欠測。ルックアヘッドしていないことの確認。
    assert out["ma200_slope_21d"].iloc[:21].isna().all()


def test_ma200_slope_21d_is_negative_for_falling_ma200():
    df = pd.DataFrame({"ma200": pd.Series(np.linspace(200.0, 100.0, 300))})
    assert add_ma200_slope_21d(df)["ma200_slope_21d"].iloc[-1] < 0


def test_dryup_series_is_nan_when_vol_ma50_is_zero():
    df = _synthetic_frame(120)
    df["vol_ma50"] = 0.0
    assert add_dryup_series(df)["dryup_med_10_50"].isna().all()


def test_compute_all_exposes_the_three_score_variables():
    df = compute_all(_synthetic_frame())
    latest = df.iloc[-1]
    for col in ("ma200_slope_21d", "dryup_med_10_50", "low_52w", "close"):
        assert col in df.columns
        assert not pd.isna(latest[col])
