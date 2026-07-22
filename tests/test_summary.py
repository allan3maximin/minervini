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
        "early_violation_allowance": 1, "overall_contraction_ratio": 0.6,
        "first_depth_max": 0.35, "last_depth_max": 0.12, "last_depth_perfect": 0.05,
        "volume_dryup_median_ratio": 0.85, "volume_trend_ratio": 0.75,
        "swing_low_tolerance": 0.99, "shakeout_bonus": 5,
        "vol_trend_bonus_fraction": 0.15,
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
        "momentum": {"chg_5d": 1.2, "chg_20d": 8.5, "chg_60d": 25.0, "vol_ratio_10_50": 0.75,
                     "vol_median_ratio_10_50": 0.8},
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
    assert "12%超" in s["headline"]  # V4 (閾値はconfigから)
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
    assert "80%(ドライアップ水準)" in text
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


# ---------------------------------------------------------------------------
# サイズ係数(fund_verdict)の注意喚起 (2026-07-22)
# ---------------------------------------------------------------------------

def test_fund_verdict_fail_caution_mentions_entry_cancel():
    rec = _record(fund_verdict="fail", fund_multiplier=0.0)
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    text = "\n".join(s["cautions"])
    assert "エントリー取り止め" in text and "サイズ係数0" in text
    # 旧文言(ファンダ弱)との二重出力はしない
    assert not any(c.startswith("ファンダ弱") for c in s["cautions"])


def test_fund_verdict_unknown_caution_mentions_half_size():
    rec = _record(fund_verdict="unknown", fund_multiplier=0.5, fund_strong=False,
                  fund_eps_yoy=None, fund_rev_yoy=None)
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("ハーフサイズ" in c and "0.5" in c for c in s["cautions"])


def test_old_report_without_verdict_falls_back_to_legacy_captions():
    # fund_verdictフィールドが無い旧report.jsonでは従来の「ファンダ弱」文言。
    s = sm.build_stock_summary(_record(), config=_CFG, today=_TODAY)
    assert any("ファンダ弱" in c for c in s["cautions"])


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
    assert m["vol_median_ratio_10_50"] == pytest.approx(500 / 900, abs=0.01)


def test_compute_momentum_short_history():
    df = pd.DataFrame({"close": [100.0, 101.0], "volume": [1.0, 1.0]})
    m = sm.compute_momentum(df)
    assert m["chg_20d"] is None and m["vol_ratio_10_50"] is None
    assert m["vol_median_ratio_10_50"] is None


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


# ---------------------------------------------------------------------------
# derive_guidance_view (会社計画・進捗率・予想PER)
# ---------------------------------------------------------------------------

def _fy_quarters(year, eps_list, rev_list):
    return [
        {"fiscal_quarter": f"{year}Q{n}", "eps": e, "revenue": r}
        for n, (e, r) in enumerate(zip(eps_list, rev_list), start=1)
    ]


def test_guidance_view_quarterly_disclosure_uses_current_fy_plan():
    # 2025年度Q2開示: FEPS=通期120円。前期(2024)実績EPS計100円 -> 計画YoY+20%。
    quarters = (_fy_quarters(2024, [25, 25, 25, 25], [100, 100, 100, 100])
                + _fy_quarters(2025, [30, 33], [110, 115]))
    guidance = {"fy_start": "2025-04-01", "per_n": 2, "disc_date": "2025-11-07",
                "feps": 120.0, "fsales": 460.0, "nx_feps": None, "nx_fsales": None}
    gv = sm.derive_guidance_view(quarters, guidance, close=2400.0)
    assert gv["plan_fy"] == 2025
    assert gv["eps_plan_yoy"] == 20.0
    assert gv["sales_plan_yoy"] == pytest.approx((460 - 400) / 400 * 100, abs=0.1)
    assert gv["eps_progress_pct"] == pytest.approx((30 + 33) / 120 * 100, abs=0.1)
    assert gv["quarters_reported"] == 2
    assert gv["forward_per"] == 20.0  # 2400 / 120


def test_guidance_view_fy_disclosure_uses_next_year_plan():
    # 本決算(per_n=4)開示: NxFEPSが来期計画。実績4Q揃った2025年度EPS計100円と比較。
    quarters = _fy_quarters(2025, [25, 25, 25, 25], [100, 100, 100, 100])
    guidance = {"fy_start": "2025-04-01", "per_n": 4, "disc_date": "2026-05-14",
                "feps": 100.0, "fsales": 400.0, "nx_feps": 130.0, "nx_fsales": 480.0}
    gv = sm.derive_guidance_view(quarters, guidance, close=1300.0)
    assert gv["plan_fy"] == 2026
    assert gv["eps_plan_yoy"] == 30.0
    assert gv["eps_progress_pct"] is None  # 2026年度の実績はまだ無い
    assert gv["forward_per"] == 10.0


def test_guidance_view_missing_prior_year_gives_none_yoy():
    quarters = _fy_quarters(2025, [25, 25], [100, 100])  # 前期(2024)実績なし
    guidance = {"fy_start": "2025-04-01", "per_n": 2, "disc_date": "2025-11-07",
                "feps": 120.0, "fsales": None, "nx_feps": None, "nx_fsales": None}
    gv = sm.derive_guidance_view(quarters, guidance, close=None)
    assert gv["eps_plan_yoy"] is None
    assert gv["eps_progress_pct"] == pytest.approx(50 / 120 * 100, abs=0.1)
    assert gv["forward_per"] is None


def test_guidance_view_none_when_no_plan_values():
    assert sm.derive_guidance_view([], None) is None
    g = {"fy_start": "2025-04-01", "per_n": 2, "feps": None, "fsales": None,
         "nx_feps": None, "nx_fsales": None}
    assert sm.derive_guidance_view([], g) is None


# ---------------------------------------------------------------------------
# サマリーへのガイダンス・時価総額・発表予定日の反映
# ---------------------------------------------------------------------------

def _guidance_ok():
    return {"fy_start": "2025-04-01", "per_n": 2, "disc_date": "2025-11-07",
            "feps": 120.0, "fsales": 460.0, "nx_feps": None, "nx_fsales": None}


def _quarters_for_guidance():
    return (_fy_quarters(2024, [25, 25, 25, 25], [100, 100, 100, 100])
            + _fy_quarters(2025, [30, 33], [110, 115]))


def test_summary_includes_guidance_and_per():
    rec = _record(close=2400.0)
    s = sm.build_stock_summary(rec, quarters=_quarters_for_guidance(),
                               guidance=_guidance_ok(), config=_CFG, today=_TODAY)
    text = "\n".join(s["points"])
    assert "会社計画(2025年度): 前期比 EPS +20.0%" in text
    assert "進捗率 EPS 52%(Q2時点)" in text
    assert "予想PER 20.0倍" in text


def test_summary_flags_plan_vs_latest_divergence():
    # 直近四半期EPS YoYがマイナス(-37%)なのに会社計画が増益(+20%) -> 明示のpoint
    rec = _record(close=2400.0)
    s = sm.build_stock_summary(rec, quarters=_quarters_for_guidance(),
                               guidance=_guidance_ok(), config=_CFG, today=_TODAY)
    assert any("減益だが会社計画は通期増益(+20.0%)" in p for p in s["points"])


def test_summary_low_progress_caution():
    # Q3時点で進捗40% (目安75%) -> 低調caution
    quarters = (_fy_quarters(2024, [25, 25, 25, 25], [100, 100, 100, 100])
                + _fy_quarters(2025, [16, 16, 16], [100, 100, 100]))
    g = {"fy_start": "2025-04-01", "per_n": 3, "disc_date": "2026-02-07",
         "feps": 120.0, "fsales": None, "nx_feps": None, "nx_fsales": None}
    s = sm.build_stock_summary(_record(), quarters=quarters, guidance=g,
                               config=_CFG, today=_TODAY)
    assert any("進捗が低調" in c and "40%" in c for c in s["cautions"])


def test_summary_market_cap_point():
    rec = _record(market_cap_oku=350, market_segment="グロース")
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("時価総額 約350億円(小型株・グロース)" in p for p in s["points"])


def test_summary_next_earnings_within_14_days_is_caution():
    rec = _record(next_earnings_date="2026-07-20")  # 8日後
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("決算発表予定 2026-07-20(あと8日)" in c for c in s["cautions"])
    # 正確な予定日がある場合は75日推定は出さない
    assert not any("次回決算発表が近い可能性" in c for c in s["cautions"])


def test_summary_next_earnings_far_is_point():
    rec = _record(next_earnings_date="2026-08-30")
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("次回決算発表予定: 2026-08-30" in p for p in s["points"])


# ---------------------------------------------------------------------------
# 需給(信用取引週末残高) -- タスク2。表示専用、総合スコアには一切使わない。
# ---------------------------------------------------------------------------

def test_summary_no_margin_field_produces_no_line():
    s = sm.build_stock_summary(_record(), config=_CFG, today=_TODAY)
    assert not any("需給" in p for p in s["points"] + s["cautions"])


def test_summary_margin_heavy_buy_is_caution():
    rec = _record(margin={"ratio": 6.0, "buy": 600, "sell": 100, "date": "2026-07-17",
                          "buy_wow_pct": 5.0, "days_to_cover": 6.0, "badge": "heavy_buy"})
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("需給" in c and "信用倍率6倍" in c and "買残が重く" in c for c in s["cautions"])
    assert not any("需給" in p for p in s["points"])


def test_summary_margin_short_is_point():
    rec = _record(margin={"ratio": 0.5, "buy": 50, "sell": 100, "date": "2026-07-17",
                          "buy_wow_pct": None, "days_to_cover": None, "badge": "short"})
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("需給" in p and "信用倍率0.5倍" in p and "踏み上げ余地" in p for p in s["points"])


def test_summary_margin_no_sell_shows_no_ratio():
    rec = _record(margin={"ratio": None, "buy": 300, "sell": 0, "date": "2026-07-17",
                          "buy_wow_pct": None, "days_to_cover": None, "badge": None})
    s = sm.build_stock_summary(rec, config=_CFG, today=_TODAY)
    assert any("需給" in p and "売残なし" in p for p in s["points"])
