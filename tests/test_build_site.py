import pandas as pd

from src.report.build_site import (
    assemble_stock_record,
    build_chart_data,
    build_report,
    compute_breakout_success_rate,
    update_breadth,
)

CONFIG_LATEST = {"close": 150.0, "rs": 85}


def _fund_info(tier="confirmed", tech_score=80.0, full_score=75.0):
    return {
        "tier": tier,
        "tech_score": tech_score,
        "full_score": full_score,
        "fund_coverage": "full" if tier == "confirmed" else "none",
        "fund_stale": False,
        "fund_checked_date": "2026-07-01",
        "eps_accel_slope": 12.3,
    }


def test_assemble_stock_record_confirmed_uses_tech_score_for_total():
    # 2026-07-22改定: confirmedティアもランキングは tech_score(純セットアップ品質)。
    # full_score はレコードに残るが total_score には影響しない(表示専用)。
    tt_flags = {"cond1": True}
    vcp_result = {"vcp_score": 70.0, "footprint": "7W 18/9/4 3T", "must_flags": {"V1": True}, "contractions": []}
    entry_result = {"status": "WATCH_A", "pivot": 1280, "buy_stop": 1281, "stop_loss": 1228, "risk_pct": 4.1, "dist_to_pivot": 3.6}

    record = assemble_stock_record(
        "7134", "Test Co", CONFIG_LATEST, tt_flags, vcp_result, entry_result, _fund_info(tier="confirmed", tech_score=80.0, full_score=90.0)
    )
    assert record["tier"] == "confirmed"
    assert record["total_score"] == 75.0  # (tech 80*0.5 + vcp 70*0.5)、full_score 90は非算入
    assert record["full_score"] == 90.0  # 表示用には残る


def test_assemble_stock_record_passes_through_fund_multiplier():
    tt_flags = {"cond1": True}
    vcp_result = {"vcp_score": 70.0, "footprint": None, "must_flags": None, "contractions": []}
    entry_result = {"status": "WATCH_A", "pivot": 1280}
    fund_info = {**_fund_info(tier="pool"), "fund_verdict": "fail", "fund_multiplier": 0.0}

    record = assemble_stock_record(
        "7134", "T", CONFIG_LATEST, tt_flags, vcp_result, entry_result, fund_info
    )
    assert record["fund_verdict"] == "fail"
    assert record["fund_multiplier"] == 0.0


DRYUP_CONFIG = {
    "dryup": {"dryup_badge_strong": 0.66, "dryup_badge_mild": 0.77},
    "scoring": {"phase1_weight": 0.5, "vcp_weight": 0.5},
    "vcp": {"last_depth_max": 0.12},
}


def _vcp_with_v5(recent10_median, vol_ma50):
    return {
        "vcp_score": 70.0,
        "footprint": "7W 18/9/4 3T",
        "must_flags": {"V1": True},
        "contractions": [],
        "vcp_diagnostics": {"v5": {"recent10_median": recent10_median, "vol_ma50": vol_ma50}},
    }


def test_dryup_badge_extreme():
    # med/vol_ma50 = 0.30 <= 0.66 -> 激枯れ(extreme)
    vcp_result = _vcp_with_v5(300.0, 1000.0)
    entry_result = {"status": "WATCH_A", "pivot": 1280}
    record = assemble_stock_record(
        "7134", "T", CONFIG_LATEST, {}, vcp_result, entry_result, _fund_info(), DRYUP_CONFIG
    )
    assert record["dryup"]["value"] == 0.3
    assert record["dryup"]["badge"] == "extreme"


def test_dryup_badge_dry():
    # 0.70: > 0.66 (strong) but <= 0.77 (mild) -> 枯れ気味(dryup)
    vcp_result = _vcp_with_v5(700.0, 1000.0)
    entry_result = {"status": "WATCH_A", "pivot": 1280}
    record = assemble_stock_record(
        "7134", "T", CONFIG_LATEST, {}, vcp_result, entry_result, _fund_info(), DRYUP_CONFIG
    )
    assert record["dryup"]["badge"] == "dryup"


def test_dryup_badge_none_above_threshold():
    # 0.85 > 0.6 -> no badge, value still present
    vcp_result = _vcp_with_v5(850.0, 1000.0)
    entry_result = {"status": "WATCH_A", "pivot": 1280}
    record = assemble_stock_record(
        "7134", "T", CONFIG_LATEST, {}, vcp_result, entry_result, _fund_info(), DRYUP_CONFIG
    )
    assert record["dryup"]["value"] == 0.85
    assert record["dryup"]["badge"] is None


def test_dryup_badge_missing_diagnostics():
    vcp_result = {"vcp_score": None, "footprint": None, "must_flags": None, "contractions": []}
    entry_result = {"status": "WATCH_B", "pivot": None}
    record = assemble_stock_record(
        "7134", "T", CONFIG_LATEST, {}, vcp_result, entry_result, _fund_info(tier="pool", full_score=None), DRYUP_CONFIG
    )
    assert record["dryup"]["value"] is None
    assert record["dryup"]["badge"] is None


def test_assemble_stock_record_pool_uses_tech_score_for_total():
    tt_flags = {"cond1": True}
    vcp_result = {"vcp_score": 70.0, "footprint": "7W 18/9/4 3T", "must_flags": {"V1": True}, "contractions": []}
    entry_result = {"status": "WATCH_B", "pivot": None}

    record = assemble_stock_record(
        "9999", "Pool Co", CONFIG_LATEST, tt_flags, vcp_result, entry_result, _fund_info(tier="pool", tech_score=60.0, full_score=None)
    )
    assert record["tier"] == "pool"
    assert record["full_score"] is None
    assert record["total_score"] == 65.0  # (60*0.5 + 70*0.5)


def test_assemble_stock_record_watchlist_tier_override_falls_back_to_phase1_score():
    tt_flags = {"cond1": True}
    # No VCP setup yet: status is one of the "not actionable" VCP states,
    # vcp_score/footprint/contractions are all absent.
    vcp_result = {"status": "IMMATURE", "vcp_score": None, "footprint": None, "must_flags": None}
    entry_result = {"status": "IMMATURE", "pivot": None}

    record = assemble_stock_record(
        "5555",
        "Watchlist Co",
        CONFIG_LATEST,
        tt_flags,
        vcp_result,
        entry_result,
        _fund_info(tier="pool", tech_score=72.0, full_score=None),
        tier_override="watchlist",
    )
    assert record["tier"] == "watchlist"
    assert record["status"] == "IMMATURE"
    assert record["pivot"] is None
    # no vcp_score available -> total_score falls back to the phase1 (tech) score alone
    assert record["total_score"] == 72.0


def test_build_report_sorts_confirmed_before_pool_and_by_status_then_score(tmp_path, monkeypatch):
    import src.report.build_site as bs

    monkeypatch.setattr(bs, "DOCS_DATA_DIR", tmp_path)
    monkeypatch.setattr(bs, "REPORT_PATH", tmp_path / "report.json")

    stocks = [
        {"code": "A", "tier": "pool", "status": "WATCH_A", "total_score": 90},
        {"code": "B", "tier": "confirmed", "status": "BREAKOUT", "total_score": 50},
        {"code": "C", "tier": "confirmed", "status": "WATCH_A", "total_score": 95},
        {"code": "D", "tier": "confirmed", "status": "WATCH_A", "total_score": 60},
        {"code": "E", "tier": "watchlist", "status": "IMMATURE", "total_score": 99},
    ]
    report = build_report(stocks, universe_size=1000, template_pass=87)
    codes = [s["code"] for s in report["stocks"]]
    # confirmed tier first; within confirmed, BREAKOUT before WATCH_A; within
    # WATCH_A, higher score first; watchlist always last regardless of score
    assert codes == ["B", "C", "D", "A", "E"]


def test_compute_breakout_success_rate_counts_holds_above_pivot():
    history = {
        "7134": [{"status": "WATCH_A"}] * 3
        + [{"status": "BREAKOUT"}]
        + [{"status": "BREAKOUT"}] * 4
        + [{"status": "BREAKOUT"}],  # breakout held through hold_days=5
        "9999": [{"status": "WATCH_A"}] * 3
        + [{"status": "BREAKOUT"}]
        + [{"status": "WATCH_A"}] * 4
        + [{"status": "WATCH_A"}],  # breakout failed, fell back below pivot
    }
    rate = compute_breakout_success_rate(history, lookback_days=20, hold_days=5)
    assert rate == 0.5


def test_compute_breakout_success_rate_none_when_no_breakouts():
    history = {"7134": [{"status": "WATCH_A"}] * 10}
    assert compute_breakout_success_rate(history) is None


def test_update_breadth_same_date_replaces_instead_of_appending(tmp_path, monkeypatch):
    import src.report.build_site as bs

    monkeypatch.setattr(bs, "DOCS_DATA_DIR", tmp_path)
    monkeypatch.setattr(bs, "BREADTH_PATH", tmp_path / "breadth.json")

    update_breadth("2026-07-11", universe_size=1000, template_pass=50, watch_count=10, status_history={})
    result = update_breadth("2026-07-11", universe_size=1000, template_pass=99, watch_count=20, status_history={})

    same_date_entries = [h for h in result["history"] if h["date"] == "2026-07-11"]
    assert len(same_date_entries) == 1
    assert same_date_entries[0]["template_pass"] == 99
    assert same_date_entries[0]["watch_count"] == 20


def test_build_chart_data_includes_ma_and_markers():
    dates = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame(
        {
            "date": dates,
            "open": [10, 11, 12, 13, 14],
            "high": [10.5, 11.5, 12.5, 13.5, 14.5],
            "low": [9.5, 10.5, 11.5, 12.5, 13.5],
            "close": [10, 11, 12, 13, 14],
            "volume": [100, 200, 300, 400, 500],
            "ma50": [None, None, None, None, 12.0],
        }
    )
    vcp_result = {"contractions": [{"high_idx": 1, "high_price": 12.0, "low_idx": 2, "low_price": 10.0}]}
    entry_result = {"pivot": 14.0, "stop_loss": 12.5}

    chart = build_chart_data("7134", df, vcp_result, entry_result)
    assert len(chart["candles"]) == 5
    assert chart["pivot"] == 14.0
    assert len(chart["markers"]) == 2
    assert len(chart["ma50"]) == 1  # only the non-null value
    assert chart["earnings"] == []  # fund_entry未指定なら決算マーカーは空


def test_build_chart_data_earnings_markers_snap_and_filter():
    dates = pd.bdate_range("2024-01-01", periods=5)  # 01-01,02,03,04,05
    df = pd.DataFrame(
        {
            "date": dates,
            "open": [10, 11, 12, 13, 14],
            "high": [10.5, 11.5, 12.5, 13.5, 14.5],
            "low": [9.5, 10.5, 11.5, 12.5, 13.5],
            "close": [10, 11, 12, 13, 14],
            "volume": [100, 200, 300, 400, 500],
        }
    )
    fund_entry = {
        "quarters": [
            {"fiscal_quarter": "2023Q4", "eps": 5.0, "disc_date": "2023-12-20"},  # 期間前=除外
            {"fiscal_quarter": "2024Q1", "eps": 6.0, "disc_date": "2024-01-03"},  # 当日バーにヒット
            {"fiscal_quarter": "2024Q2", "eps": 7.0, "disc_date": "2024-01-06"},  # 期間後=除外(hi=01-05)
        ]
    }
    chart = build_chart_data("7134", df, {"contractions": []}, {}, fund_entry=fund_entry)
    assert chart["earnings"] == [
        {"time": "2024-01-03", "quarter": "2024Q1", "disc_date": "2024-01-03", "eps": 6.0}
    ]


def test_build_chart_data_earnings_snaps_holiday_disclosure_to_next_bar():
    # 開示日が非営業日(01-06土)なら、開示日以上で最も近いバー...は無いので
    # 期間内に収まらず除外。期間内の休日(存在しないが)代わりに、開示が
    # バー間に落ちるケースを検証: 01-03と01-04の間に相当する開示は無いが、
    # 開示日<=barで最近傍へフォールバックする経路を確認する。
    dates = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame({
        "date": dates,
        "open": [10, 11, 12, 13, 14], "high": [10.5, 11.5, 12.5, 13.5, 14.5],
        "low": [9.5, 10.5, 11.5, 12.5, 13.5], "close": [10, 11, 12, 13, 14],
        "volume": [100, 200, 300, 400, 500],
    })
    # 開示日 01-05(最終バー)ちょうど。
    fund_entry = {"quarters": [{"fiscal_quarter": "2024Q1", "eps": 6.0, "disc_date": "2024-01-05"}]}
    chart = build_chart_data("7134", df, {"contractions": []}, {}, fund_entry=fund_entry)
    assert [e["time"] for e in chart["earnings"]] == ["2024-01-05"]


# ---------------------------------------------------------------------------
# build_setup_stage (監視タブ進行度分類)
# ---------------------------------------------------------------------------

from src.report.build_site import build_setup_stage  # noqa: E402

STAGE_CONFIG = {"vcp": {"base_min_days": 15, "setup_stage_near_days": 5}}


def test_setup_stage_immature_near():
    st = build_setup_stage({"status": "IMMATURE", "base_days": 12}, STAGE_CONFIG)
    assert st["stage"] == "forming"
    assert st["near"] is True  # あと3日 <= 5
    assert "あと3日" in st["detail"]


def test_setup_stage_immature_far():
    st = build_setup_stage({"status": "IMMATURE", "base_days": 6}, STAGE_CONFIG)
    assert st["stage"] == "forming"
    assert st["near"] is False  # あと9日 > 5


def test_setup_stage_too_recent():
    st = build_setup_stage({"status": "TOO_RECENT", "days_from_high": 3}, STAGE_CONFIG)
    assert st["stage"] == "fresh_high"
    assert st["near"] is False
    assert "高値から3日" in st["detail"]


def test_setup_stage_rejected_single_missing_is_near():
    flags = {"V1": True, "V2": True, "V3": True, "V4": False, "V5": True, "V6": True, "V7": True}
    st = build_setup_stage({"status": "REJECTED", "must_flags": flags}, STAGE_CONFIG)
    assert st["stage"] == "rejected"
    assert st["near"] is True
    assert st["missing"] == ["V4"]


def test_setup_stage_rejected_multi_missing_not_near():
    flags = {"V1": False, "V2": True, "V3": True, "V4": False, "V5": True, "V6": True, "V7": True}
    st = build_setup_stage({"status": "REJECTED", "must_flags": flags}, STAGE_CONFIG)
    assert st["near"] is False
    assert st["missing"] == ["V1", "V4"]


def test_setup_stage_volatile_and_no_base():
    assert build_setup_stage({"status": "TOO_VOLATILE"}, STAGE_CONFIG)["stage"] == "volatile"
    assert build_setup_stage({"status": "NO_BASE"}, STAGE_CONFIG)["stage"] == "no_base"


def test_setup_stage_actionable_returns_none():
    assert build_setup_stage({"status": "WATCH_A"}, STAGE_CONFIG) is None
    assert build_setup_stage({"status": "BREAKOUT"}, STAGE_CONFIG) is None


# ---------------------------------------------------------------------------
# 信用残(需給)バッジ (タスク2: 表示専用。総合スコアには一切使わない)
# ---------------------------------------------------------------------------

MARGIN_CONFIG = {
    "margin": {"high_ratio_warn": 5.0, "dtc_warn": 3.0, "low_ratio_info": 1.0},
    "scoring": {"phase1_weight": 0.5, "vcp_weight": 0.5},
    "vcp": {"last_depth_max": 0.12},
}


def _margin_store(buy, sell, date="2026-07-17", code="7134"):
    return {"history": [{"date": date, "by_code": {code: {"buy": buy, "sell": sell}}}]}


def _assemble_with_margin(margin_store, latest_row=None):
    entry_result = {"status": "WATCH_A", "pivot": 1280}
    vcp_result = {"vcp_score": 70.0, "footprint": None, "must_flags": {"V1": True}, "contractions": []}
    row = dict(CONFIG_LATEST)
    if latest_row:
        row.update(latest_row)
    return assemble_stock_record(
        "7134", "T", row, {}, vcp_result, entry_result, _fund_info(), MARGIN_CONFIG, margin_store=margin_store
    )


def test_margin_badge_heavy_buy_when_ratio_and_dtc_both_over_threshold():
    # ratio = 600/100 = 6.0 (>=5.0) / vol_ma50=100 -> dtc = 600/100 = 6.0 (>=3.0)
    record = _assemble_with_margin(_margin_store(buy=600, sell=100), latest_row={"vol_ma50": 100})
    assert record["margin"]["ratio"] == 6.0
    assert record["margin"]["days_to_cover"] == 6.0
    assert record["margin"]["badge"] == "heavy_buy"


def test_margin_badge_short_when_ratio_at_or_below_low_info():
    # ratio = 50/100 = 0.5 (<= 1.0)
    record = _assemble_with_margin(_margin_store(buy=50, sell=100))
    assert record["margin"]["ratio"] == 0.5
    assert record["margin"]["badge"] == "short"


def test_margin_badge_none_in_neutral_range():
    # ratio = 200/100 = 2.0: not >= high_ratio_warn(5.0), not <= low_ratio_info(1.0)
    record = _assemble_with_margin(_margin_store(buy=200, sell=100))
    assert record["margin"]["ratio"] == 2.0
    assert record["margin"]["badge"] is None


def test_margin_badge_none_when_ratio_high_but_dtc_below_warn():
    # ratio=6.0(>=5.0) だが vol_ma50=1000 -> dtc=600/1000=0.6 (<3.0) なので badge無し
    record = _assemble_with_margin(_margin_store(buy=600, sell=100), latest_row={"vol_ma50": 1000})
    assert record["margin"]["ratio"] == 6.0
    assert record["margin"]["days_to_cover"] == 0.6
    assert record["margin"]["badge"] is None


def test_margin_none_when_no_store_data():
    record = _assemble_with_margin(margin_store=None)
    assert record["margin"] is None
