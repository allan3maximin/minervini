"""src/report/summary.py -- ルールベース日本語サマリー生成のテスト。

サマリーは既存判定(status/must_flags/fund_*等)の言語化なので、テストは
「どの状態でどの要素が文面に現れるか」を代表ケースで確認する。
"""
from datetime import date

import pandas as pd
import pytest

from src.report import summary as sm

_CFG = {
    "vcp": {
        "scan_days": 130, "scan_days_extended": 200,
        "base_min_days": 15, "base_max_days": 200, "min_days_from_high": 5,
        "contraction_count": [2, 6], "monotonic_tolerance": 1.2,
        "first_depth_max": 0.35, "last_depth_max": 0.1,
        "volume_dryup_ratio": 0.8, "swing_low_tolerance": 0.99,
    },
    "entry": {"breakout_vol_mult": 1.4, "extended_pct": 0.05},
    "fundamentals": {"confirmed_eps_yoy_min": 25, "confirmed_rev_yoy_min": 20,
                     "stale_days": 120},
}

_TODAY = date(2026, 7, 12)


def _record(**kw):
    base = {
        "code": "9247",
        "status": "WATCH_A",
        "close": 2144.0,
        "rs": 72,
        "pivot": 2192.0,
        "buy_stop": 2197.0,
        "stop_loss": 2082.4,
        "risk_pct": 5.22,
        "dist_to_pivot": 2.19,
        "high52w_distance_pct": 2.9,
        "fund_coverage": "full",
        "fund_strong": False,
        "fund_eps_yoy": -37.0,
        "fund_rev_yoy": -11.8,
        "fund_stale": False,
        "fund_checked_date": "2026-05-13",
        "must_flags": {"tt": {}, "vcp": {"V1": True, "V2": True, "V3": True,
                                          "V4": True, "V5": True, "V6": True, "V7": True}},
        "ma_deviation_pct": {"ma50": 19.04, "ma150": 26.8, "ma200": 30.08},
        "vcp_detail": {"base_days": 17, "days_from_high": 17, "t0_date": "2026-06-16",
                       "depths_pct": [6.0, 7.0, 7.0]},
        "momentum": {"chg_5d": 1.2, "chg_20d": 8.5, "chg_60d": 25.0, "vol_ratio_10_50": 0.75},
        "sector33": "サービス業",
        "sector_strength": "強",
        "sector_direction": "↑",
    }
    base.update(kw)
    return base


def _text(s: dict) -> str:
    return s["headline"] + "\n" + "\n".join(s["points"]) + "\n" + "\n".join(s["cautions"])


# ---------------------------------------------------------------------------
# headline: 状態ごとの文面
# ---------------------------------------------------------------------------

def test_watch_a_headline_has_pivot_and_risk():
    s = sm.build_stock_summary(_record(), config=_CFG, today=_TODAY)
    assert "ピボット2,192円" in s["headline"]
    assert "あと+2.2%" in s["headline"]
    assert "リスク5.2%" in s["headline"]


def test_too_recent_headline_counts_days():
    rec = _record(status="TOO_RECENT", pivot=None, buy_stop=None, stop_loss=None,
                  vcp_detail={"base_days": None, "days_from_high": 3, "t0_date": None, "depths_pct": []})
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert "3営業日" in s["headline"]
    assert "5日必要" in s["headline"]


def test_immature_headline_counts_remaining_days():
    rec = _record(status="IMMATURE", pivot=None,
                  vcp_detail={"base_days": 10, "days_from_high": 10, "t0_date": None, "depths_pct": []})
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert "ベース形成10営業日目" in s["headline"]
    assert "あと5日" in s["headline"]


def test_rejected_headline_lists_failed_conditions_in_japanese():
    flags = {"V1": True, "V2": False, "V3": True, "V4": False, "V5": True, "V6": True, "V7": True}
    rec = _record(status="REJECTED", must_flags={"tt": {}, "vcp": flags})
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert "段階的に減っていない" in s["headline"]  # V2
    assert "10%超" in s["headline"]  # V4 (閾値はconfigから)
    assert "V2" not in s["headline"]  # 生のコード名は出さない


def test_breakout_weak_headline_mentions_volume():
    rec = _record(status="BREAKOUT_WEAK", pivot=2932.0)
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert "出来高" in s["headline"]
    assert "×1.4" in s["headline"]


# ---------------------------------------------------------------------------
# points / cautions の要素
# ---------------------------------------------------------------------------

def test_points_include_base_and_momentum_and_sector():
    s = sm.build_stock_summary(_record(), config=_CFG, today=_TODAY)
    text = "\n".join(s["points"])
    assert "8条件すべて合格" in text and "RS 72" in text
    assert "ベース17営業日" in text and "6% → 7% → 7%" in text
    assert "20日+8.5%" in text
    assert "75%(ドライアップ水準)" in text
    assert "サービス業" in text


def test_weak_fundamentals_goes_to_cautions():
    s = sm.build_stock_summary(_record(), config=_CFG, today=_TODAY)
    text = "\n".join(s["cautions"])
    assert "ファンダ弱" in text and "-37.0%" in text and "+25%" in text


def test_strong_fundamentals_goes_to_points():
    rec = _record(fund_strong=True, fund_eps_yoy=41.0, fund_rev_yoy=22.0)
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("本命基準クリア" in p and "+41.0%" in p for p in s["points"])
    assert not any("ファンダ弱" in c for c in s["cautions"])


def test_no_fund_data_caution():
    rec = _record(fund_coverage="none", fund_strong=None, fund_eps_yoy=None,
                  fund_rev_yoy=None, fund_checked_date=None)
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("ファンダデータなし" in c for c in s["cautions"])


def test_stale_fundamentals_caution():
    rec = _record(fund_stale=True, fund_checked_date="2026-02-13")
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("2026-02-13" in c and "未反映" in c for c in s["cautions"])


def test_earnings_proximity_caution_when_checked_date_old_but_not_stale():
    # 前回確認から75日以上・stale(120日)未満 -> 決算接近の注意のみ
    rec = _record(fund_checked_date="2026-04-20")  # 83日前
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("発表跨ぎ" in c for c in s["cautions"])
    assert not any("未反映" in c for c in s["cautions"])


def test_extended_ma50_caution():
    s = sm.build_stock_summary(_record(), config=_CFG, today=_TODAY)  # ma50 +19%
    assert any("MA50乖離+19.0%" in c for c in s["cautions"])


def test_market_signal_red_caution_and_green_point():
    red = {"signal": "red", "reasons": ["TOPIXが200日線を下回っている"]}
    s = sm.build_stock_summary(_record(), market_signal=red, config=_CFG, today=_TODAY)
    assert any("🔴" in c and "200日線" in c for c in s["cautions"])

    green = {"signal": "green", "reasons": []}
    s2 = sm.build_stock_summary(_record(), market_signal=green, config=_CFG, today=_TODAY)
    assert any("🟢" in p for p in s2["points"])


def test_eps_yoy_series_from_quarters():
    quarters = [
        {"fiscal_quarter": "2024Q1", "eps": 10.0, "revenue": 100.0},
        {"fiscal_quarter": "2024Q2", "eps": 10.0, "revenue": 100.0},
        {"fiscal_quarter": "2024Q3", "eps": 10.0, "revenue": 100.0},
        {"fiscal_quarter": "2025Q1", "eps": 11.0, "revenue": 100.0},  # +10%
        {"fiscal_quarter": "2025Q2", "eps": 12.5, "revenue": 100.0},  # +25%
        {"fiscal_quarter": "2025Q3", "eps": 14.0, "revenue": 100.0},  # +40%
    ]
    s = sm.build_stock_summary(_record(), quarters=quarters, config=_CFG, today=_TODAY)
    assert any("+10.0% → +25.0% → +40.0%" in p and "加速中" in p for p in s["points"])


def test_minimal_record_does_not_crash():
    rec = {"code": "0000", "status": "NO_BASE"}
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert "ベース起点が見つからない" in s["headline"]
    assert isinstance(s["points"], list) and isinstance(s["cautions"], list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_compute_momentum():
    n = 80
    close = [100.0 + i for i in range(n)]           # 単調増加
    volume = [1000.0] * (n - 10) + [500.0] * 10     # 直近10日は半減
    df = pd.DataFrame({"close": close, "volume": volume})
    m = sm.compute_momentum(df)
    assert m["chg_5d"] == pytest.approx((179 / 174 - 1) * 100, abs=0.05)
    assert m["chg_20d"] == pytest.approx((179 / 159 - 1) * 100, abs=0.05)
    assert m["vol_ratio_10_50"] == pytest.approx(500 / 900, abs=0.01)


def test_compute_momentum_short_history():
    df = pd.DataFrame({"close": [100.0, 101.0], "volume": [1.0, 1.0]})
    m = sm.compute_momentum(df)
    assert m["chg_20d"] is None and m["vol_ratio_10_50"] is None


def test_yoy_series_skips_nonpositive_and_missing_base():
    quarters = [
        {"fiscal_quarter": "2024Q1", "eps": -5.0},   # 前年値<=0 -> 2025Q1はスキップ
        {"fiscal_quarter": "2024Q2", "eps": 10.0},
        {"fiscal_quarter": "2025Q1", "eps": 8.0},
        {"fiscal_quarter": "2025Q2", "eps": 12.0},   # +20%
        {"fiscal_quarter": "2025Q3", "eps": 15.0},   # 前年Q3なし -> スキップ
    ]
    out = sm.yoy_series(quarters, "eps")
    assert out == [("2025Q2", 20.0)]
