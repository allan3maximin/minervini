import numpy as np
import pandas as pd

import src.data.prices as prices_mod
from src.data.prices import detect_cache_divergence, drop_benchmark_outliers, save_cache


def test_drop_benchmark_outliers_removes_bad_ticks():
    dates = pd.bdate_range("2026-03-16", periods=15)
    values = [382.0] * 15
    # reproduce the observed 1306.T glitch: two isolated days at ~1/10 price
    values[9] = 37.6
    values[10] = 37.1
    close = pd.Series(values, index=dates)

    cleaned = drop_benchmark_outliers(close)

    assert len(cleaned) == 13
    assert dates[9] not in cleaned.index
    assert dates[10] not in cleaned.index
    assert (cleaned == 382.0).all()


def test_drop_benchmark_outliers_keeps_normal_moves():
    dates = pd.bdate_range("2026-03-16", periods=30)
    # a legitimate but sharp market: -8% day inside a mild downtrend
    rng = np.random.RandomState(0)
    values = 2000 * np.cumprod(1 + rng.normal(0, 0.01, 30))
    values[15] *= 0.92
    close = pd.Series(values, index=dates)

    cleaned = drop_benchmark_outliers(close)

    assert len(cleaned) == 30


# ---------------------------------------------------------------------------
# 株式分割・調整基準変更の検知 (2026-07-17追加)
# ---------------------------------------------------------------------------

_SPLIT_TEST_CONFIG = {
    "data": {
        "chunk_size": 50,
        "sleep_range": [0.0, 0.0],
        "history_days": 500,
        "backoff_sec": [],
        "max_fail_ratio": 0.2,
        "split_check_tolerance": 0.01,
        "stooq_sleep_sec": 0.0,
    }
}


def _ohlcv(dates, close_value, volume=100_000):
    close = np.full(len(dates), float(close_value))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(len(dates), float(volume)),
        }
    )


def test_detect_cache_divergence_flags_split_and_ignores_matching_data():
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=30)
    cached = _ohlcv(dates, 1000.0)
    same = _ohlcv(dates[-10:], 1000.0)
    halved = _ohlcv(dates[-10:], 500.0)  # 1:2分割後の遡及調整済み価格

    assert detect_cache_divergence(cached, same, tolerance=0.01) is False
    assert detect_cache_divergence(cached, halved, tolerance=0.01) is True
    # 重複日が無い(キャッシュより未来のみ)場合は比較不能 -> False
    future = _ohlcv(pd.bdate_range(dates[-1] + pd.Timedelta(days=1), periods=5), 500.0)
    assert detect_cache_divergence(cached, future, tolerance=0.01) is False


def test_update_prices_refetches_full_history_on_split(monkeypatch, tmp_path):
    """増分取得とキャッシュの重複期間で終値が閾値超でズレたら、全履歴を
    再取得してキャッシュを置き換える(古い調整基準の行を残さない)。"""
    monkeypatch.setattr(prices_mod, "PRICE_CACHE_DIR", tmp_path / "prices")

    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    # キャッシュ: 分割前基準 (close=1000) の300営業日
    save_cache("1111", _ohlcv(dates, 1000.0), history_days=500)

    calls = []

    def fake_fetch(tickers, start, period, config):
        calls.append({"tickers": list(tickers), "start": start, "period": period})
        assert tickers == ["1111.T"]
        if start is not None:
            # 増分取得: 直近20営業日が分割後基準 (close=500) で返る
            return {"1111.T": _ohlcv(dates[-20:], 500.0)}
        # 全履歴再取得 (start=None, period指定): 全期間が新基準で返る
        assert period == "2y"
        return {"1111.T": _ohlcv(dates, 500.0)}

    monkeypatch.setattr(prices_mod, "fetch_yfinance_chunk", fake_fetch)

    def no_stooq(code):
        raise AssertionError("stooq fallback must not be used")

    monkeypatch.setattr(prices_mod, "fetch_stooq", no_stooq)

    result = prices_mod.update_prices(["1111"], _SPLIT_TEST_CONFIG)

    # 1回目=増分、2回目=全履歴再取得 の2回だけ呼ばれる
    assert [c["start"] is None for c in calls] == [False, True]
    assert result.split_refetched_tickers == ["1111"]
    assert result.failed_tickers == [] and result.stale_tickers == []

    # フレームもキャッシュも旧基準 (1000) の行が一切残っていない
    frame = result.frames["1111"]
    assert (frame["close"] == 500.0).all()
    reloaded = prices_mod.load_cache("1111")
    assert (reloaded["close"] == 500.0).all()
    assert len(reloaded) == 300


def test_update_prices_no_refetch_when_prices_match(monkeypatch, tmp_path):
    """重複期間の終値が一致していれば全履歴再取得パスには入らない。"""
    monkeypatch.setattr(prices_mod, "PRICE_CACHE_DIR", tmp_path / "prices")

    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=300)
    save_cache("1111", _ohlcv(dates, 1000.0), history_days=500)

    calls = []

    def fake_fetch(tickers, start, period, config):
        calls.append({"start": start, "period": period})
        assert start is not None, "full re-fetch must not happen when closes match"
        return {"1111.T": _ohlcv(dates[-20:], 1000.0)}

    monkeypatch.setattr(prices_mod, "fetch_yfinance_chunk", fake_fetch)

    result = prices_mod.update_prices(["1111"], _SPLIT_TEST_CONFIG)

    assert len(calls) == 1  # 増分取得のみ
    assert result.split_refetched_tickers == []
    assert (result.frames["1111"]["close"] == 1000.0).all()
