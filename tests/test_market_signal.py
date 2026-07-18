import math

import pandas as pd
import pytest

from src.report import market_signal as market_signal_mod
from src.report.market_signal import (
    compute_breadth_stats,
    compute_index_trend,
    compute_market_signal,
)

CONFIG = {"market_signal": {"green_pct_above_ma200": 0.50, "red_pct_above_ma200": 0.30}}


@pytest.fixture(autouse=True)
def _no_real_index_cache(monkeypatch):
    """indices_mod.load_cache を既定でNoneに固定し、実キャッシュ(data/indices/*.parquet)
    に触れないようにする(read-onlyだが決定性のため)。テストが nikkei_df/growth_df/
    index_df を明示的に渡す場合はそちらが優先されるので影響しない。"""
    monkeypatch.setattr(market_signal_mod.indices_mod, "load_cache", lambda key: None)


def _stock(close, ma50, ma200, high, low, high_52w, low_52w):
    return {
        "close": close, "ma50": ma50, "ma200": ma200,
        "high": high, "low": low, "high_52w": high_52w, "low_52w": low_52w,
    }


def test_compute_breadth_stats_counts_above_ma_and_new_highs_lows():
    latest_by_code = {
        "A": _stock(110, 100, 100, 110, 105, 110, 90),  # above ma50/ma200, new high
        "B": _stock(90, 100, 100, 95, 80, 110, 80),     # below ma50/ma200, new low
        "C": _stock(100, 100, 100, 100, 95, 105, 90),   # exactly at ma -> not "above"
    }
    stats = compute_breadth_stats(latest_by_code)
    assert stats["pct_above_ma200"] == round(1 / 3, 4)
    assert stats["pct_above_ma50"] == round(1 / 3, 4)
    assert stats["new_high_count"] == 1
    assert stats["new_low_count"] == 1


def test_compute_breadth_stats_excludes_nan_ma_from_denominator():
    latest_by_code = {
        "A": _stock(110, 100, 100, 110, 105, 110, 90),
        "B": _stock(90, math.nan, math.nan, 95, 80, math.nan, math.nan),  # no MA/52w data yet
    }
    stats = compute_breadth_stats(latest_by_code)
    assert stats["pct_above_ma200"] == 1.0  # only "A" counted in denominator
    assert stats["pct_above_ma50"] == 1.0
    assert stats["new_high_count"] == 1
    assert stats["new_low_count"] == 0


def test_compute_breadth_stats_empty_returns_none_pct():
    stats = compute_breadth_stats({})
    assert stats["pct_above_ma200"] is None
    assert stats["pct_above_ma50"] is None
    assert stats["new_high_count"] == 0
    assert stats["new_low_count"] == 0


def _index_df(closes):
    dates = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"date": dates, "close": closes})


def test_compute_index_trend_none_when_insufficient_history():
    assert compute_index_trend(None) is None
    assert compute_index_trend(_index_df([100.0] * 50)) is None


def test_compute_index_trend_detects_uptrend():
    # Rising series: MA200 slopes up, close above both MAs.
    closes = [100.0 + i * 0.5 for i in range(230)]
    trend = compute_index_trend(_index_df(closes))
    assert trend is not None
    assert trend["index_above_ma50"] is True
    assert trend["index_above_ma200"] is True
    assert trend["index_ma200_slope_up"] is True


def test_compute_index_trend_detects_downtrend():
    closes = [200.0 - i * 0.5 for i in range(230)]
    trend = compute_index_trend(_index_df(closes))
    assert trend is not None
    assert trend["index_above_ma50"] is False
    assert trend["index_above_ma200"] is False
    assert trend["index_ma200_slope_up"] is False


def _uptrend_index_df():
    closes = [100.0 + i * 0.5 for i in range(230)]
    return _index_df(closes)


def _downtrend_index_df():
    closes = [200.0 - i * 0.5 for i in range(230)]
    return _index_df(closes)


def test_compute_market_signal_green_when_all_conditions_met():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    latest_by_code["low1"] = _stock(90, 100, 100, 95, 80, 110, 80)  # 1 new low, still fewer than new highs
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["signal"] == "green"
    assert result["pct_above_ma200"] >= 0.50
    assert result["new_high_count"] > result["new_low_count"]
    assert result["reasons"]


def test_compute_market_signal_red_when_index_below_ma200():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_downtrend_index_df())
    assert result["signal"] == "red"
    assert any("200日線を下回っている" in r for r in result["reasons"])


def test_compute_market_signal_red_when_breadth_weak_even_if_index_uptrend():
    # Only 2/10 above MA200 (20%) -- below red threshold 30%, even though index itself is up.
    latest_by_code = {f"S{i}": _stock(90, 100, 100, 95, 80, 110, 80) for i in range(8)}
    latest_by_code.update({f"U{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(2)})
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["signal"] == "red"
    assert any("上抜け銘柄が" in r for r in result["reasons"])


def test_compute_market_signal_yellow_when_neutral():
    # Breadth between red and green thresholds -> neutral.
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(4)}
    latest_by_code.update({f"D{i}": _stock(90, 100, 100, 95, 80, 110, 80) for i in range(6)})
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["signal"] == "yellow"


def test_compute_market_signal_yellow_when_index_data_missing():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    # Too-short series simulates a missing/insufficient TOPIX cache (compute_index_trend
    # returns None) without touching the real data/indices/topix.parquet on disk.
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_index_df([100.0] * 10))
    assert result["signal"] == "yellow"
    assert any("指数データ欠損" in r for r in result["reasons"])
    assert result["index_above_ma50"] is None


# ---------------------------------------------------------------------------
# 2026-07-18 タスク3: 地合い詳細化(騰落レシオ/NH-NL累積/マルチ指数/market_score)
# 既存の green/yellow/red 判定ロジックには一切影響しない表示専用の追加指標。
# ---------------------------------------------------------------------------


def test_compute_market_signal_defaults_index_kwargs_none_when_all_missing():
    # 全ての省略可能引数(nikkei_df/growth_df 含む)を渡さない場合の挙動。
    # autouse の _no_real_index_cache フィクスチャにより実キャッシュには触れない。
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    result = compute_market_signal(latest_by_code, CONFIG)
    assert result["signal"] == "yellow"
    assert result["index_trends"] == {"topix": {"index_above_ma50": None, "index_above_ma200": None, "index_ma200_slope_up": None}, "nikkei225": None, "growth250": None}
    assert result["growth_rel_20d"] is None
    assert result["up_down_ratio_25"] is None
    assert result["breadth_trend_20d"] is None
    assert result["advancers"] is None and result["decliners"] is None
    # pct_above_ma200 は latest_by_code から計算できる(1.0, 6銘柄全て above ma200)ので
    # breadthサブスコアだけは満点。指数/騰落/相対の3つはデータ欠損で中立50。
    assert result["score_breakdown"] == {"breadth": 100.0, "index_trend": 50.0, "momentum": 50.0, "risk_appetite": 50.0}
    assert result["market_score"] == round((100.0 * 40 + 50.0 * 30 + 50.0 * 20 + 50.0 * 10) / 100, 2)
    assert result["score_trend"] is None


def test_up_down_ratio_25_none_until_full_window_then_computes():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    breadth_today = {"advancers": 30, "decliners": 10}
    short_history = [{"advancers": 10, "decliners": 5}] * 10  # 10 + 当日1 = 11 < 25
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(),
        breadth_today=breadth_today, breadth_history=short_history,
    )
    assert result["up_down_ratio_25"] is None

    full_history = [{"advancers": 10, "decliners": 5}] * 24  # 24 + 当日1 = 25
    result2 = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(),
        breadth_today=breadth_today, breadth_history=full_history,
    )
    # sum(advancers) = 10*24 + 30 = 270, sum(decliners) = 5*24 + 10 = 130
    assert result2["up_down_ratio_25"] == round(270 / 130, 3)


def test_breadth_trend_20d_compares_to_20_entries_ago():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    history = [{"pct_above_ma200": 0.30}] + [{"pct_above_ma200": 0.5}] * 19  # len==20, [-20] is first
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(), breadth_history=history,
    )
    # pct_above_ma200(today) is 1.0 (all 6 stocks above ma200) - 0.30 = 0.70
    assert result["breadth_trend_20d"] == round(result["pct_above_ma200"] - 0.30, 4)


def test_breadth_trend_20d_none_when_history_too_short():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(), breadth_history=[{"pct_above_ma200": 0.3}],
    )
    assert result["breadth_trend_20d"] is None


def test_nh_nl_cumulative_restarts_from_today_when_no_history():
    latest_by_code = {
        "A": _stock(110, 100, 100, 110, 105, 110, 90),  # new high
        "B": _stock(90, 100, 100, 95, 80, 110, 80),      # new low
    }
    result = compute_market_signal(latest_by_code, CONFIG, index_df=_uptrend_index_df())
    assert result["net_new_highs"] == 0  # 1 high - 1 low
    assert result["nh_nl_cumulative"] == 0


def test_nh_nl_cumulative_accumulates_across_history():
    latest_by_code = {"A": _stock(110, 100, 100, 110, 105, 110, 90)}  # 1 new high, net=1
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(),
        breadth_history=[{"nh_nl_cumulative": 5}],
    )
    assert result["net_new_highs"] == 1
    assert result["nh_nl_cumulative"] == 6


def test_nh_nl_cumulative_restarts_when_legacy_entry_missing_field():
    latest_by_code = {"A": _stock(110, 100, 100, 110, 105, 110, 90)}
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(),
        breadth_history=[{"date": "2026-01-01"}],  # 旧フィールド無しエントリ
    )
    assert result["net_new_highs"] == 1
    assert result["nh_nl_cumulative"] == 1  # 当日netから再スタート


def test_multi_index_trend_and_growth_rel_20d():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    growth_closes = [100.0 + i * 1.0 for i in range(230)]  # steeper uptrend than topix
    result = compute_market_signal(
        latest_by_code, CONFIG,
        index_df=_uptrend_index_df(), nikkei_df=_uptrend_index_df(), growth_df=_index_df(growth_closes),
    )
    assert result["index_trends"]["topix"]["index_above_ma200"] is True
    assert result["index_trends"]["nikkei225"]["index_above_ma200"] is True
    assert result["index_trends"]["growth250"]["index_above_ma200"] is True
    assert result["growth_rel_20d"] is not None and result["growth_rel_20d"] > 0  # growth outpaced topix
    # 全指数トレンド判定可能 -> index_trendサブスコアは満点(9/9合格、全て上昇トレンド)。
    assert result["score_breakdown"]["index_trend"] == 100.0


def test_market_score_clips_and_score_trend_labels():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    history = [{"market_score": 40.0}] * 4 + [{"market_score": 40.0}]  # len 5, [-5] == first == 40.0
    # nikkei_df/growth_df は明示的に短い系列を渡し、実キャッシュ(data/indices/*.parquet)に
    # 触れず決定的にNone判定(データ不足)にする。
    short_df = _index_df([100.0] * 10)
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(), nikkei_df=short_df, growth_df=short_df,
        breadth_history=history,
    )
    assert 0.0 <= result["market_score"] <= 100.0
    if result["market_score"] - 40.0 > 3:
        assert result["score_trend"] == "improving"
    elif result["market_score"] - 40.0 < -3:
        assert result["score_trend"] == "deteriorating"
    else:
        assert result["score_trend"] == "flat"


def test_score_trend_none_when_history_shorter_than_5():
    latest_by_code = {f"S{i}": _stock(110, 100, 100, 110, 105, 110, 90) for i in range(6)}
    result = compute_market_signal(
        latest_by_code, CONFIG, index_df=_uptrend_index_df(), breadth_history=[{"market_score": 10.0}] * 4,
    )
    assert result["score_trend"] is None
