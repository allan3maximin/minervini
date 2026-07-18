"""scripts/backfill_breadth_history.py の純粋ロジック(backfill_history)のテスト。

I/O(価格キャッシュ読み込み・breadth.json読み書き)は行わず、合成した frames/
history だけを使う(実データ・暗号化breadth.jsonには一切触れない)。
"""
import math

import pandas as pd
import pytest

from scripts.backfill_breadth_history import (
    DONE_MARKER,
    NEW_FIELDS,
    backfill_history,
)
from src.report import market_signal as market_signal_mod

CONFIG = {"market_signal": {"green_pct_above_ma200": 0.50, "red_pct_above_ma200": 0.30}}


@pytest.fixture(autouse=True)
def _no_real_index_cache(monkeypatch):
    """market_signal.compute_market_signal が index_df=None 時に実キャッシュ
    (data/indices/*.parquet)へフォールバックしないよう固定する。backfill_history
    は _slice() 経由で常に非Noneの(空もありうる)DataFrameを渡す設計だが、
    念のためテストでも実データに触れない安全策として固定しておく。"""
    monkeypatch.setattr(market_signal_mod.indices_mod, "load_cache", lambda key: None)


def _price_frame(code: str, dates: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    """compute_breadth_stats/_slice_latest_by_codeが読む最小限の列だけを持つ合成フレーム。

    ma50/ma200/high_52w/low_52w はNaN(=breadth比率計算の対象外)にして、
    テストの主眼(advancers/decliners/up_down_ratio_25等の蓄積ロジック)を
    ma系の副作用から独立させる。
    """
    return pd.DataFrame(
        {
            "date": dates,
            "close": closes,
            "high": closes,
            "low": closes,
            "ma50": [math.nan] * len(dates),
            "ma200": [math.nan] * len(dates),
            "high_52w": [math.nan] * len(dates),
            "low_52w": [math.nan] * len(dates),
        }
    )


def _make_history(dates: pd.DatetimeIndex, extra: dict | None = None) -> list[dict]:
    return [{"date": d.strftime("%Y-%m-%d"), **(extra or {})} for d in dates]


def test_skips_entries_already_marked_done():
    dates = pd.bdate_range("2026-06-01", periods=3)
    frames = {"A": _price_frame("A", dates, [100, 101, 102])}
    history = _make_history(dates)
    # 1件目だけ「計算済み」を装う(DONE_MARKER + 1つのフィールドにセンチネル値)。
    history[0][DONE_MARKER] = 42.0
    history[0]["advancers"] = 999  # backfillされたら困る値
    updated, stats = backfill_history(history, frames, CONFIG)
    assert updated[0]["advancers"] == 999  # 上書きされていない
    assert stats["already_done"] == 1
    assert stats["updated"] == 2


def test_never_overwrites_pre_existing_non_task3_fields():
    dates = pd.bdate_range("2026-06-01", periods=3)
    frames = {"A": _price_frame("A", dates, [100, 101, 102])}
    history = _make_history(dates, extra={"universe_size": 1000, "watch_count": 7, "signal": "yellow"})
    updated, _ = backfill_history(history, frames, CONFIG)
    for entry in updated:
        assert entry["universe_size"] == 1000
        assert entry["watch_count"] == 7
        assert entry["signal"] == "yellow"


def test_does_not_overwrite_existing_task3_field_value():
    dates = pd.bdate_range("2026-06-01", periods=2)
    frames = {"A": _price_frame("A", dates, [100, 101])}
    history = _make_history(dates)
    # 2件目は一部フィールドだけ既に値がある(実運用パイプラインが計算済みの想定)が
    # market_score(DONE_MARKER)は無いので「未計算」扱いになり他フィールドは埋まる。
    history[1]["advancers"] = -1  # センチネル: backfillされたら困る
    updated, _ = backfill_history(history, frames, CONFIG)
    assert updated[1]["advancers"] == -1
    assert updated[1]["market_score"] is not None  # 他のNEW_FIELDSは埋まる


def test_no_data_before_series_start_is_skipped_without_crashing():
    dates = pd.bdate_range("2026-06-10", periods=2)
    frames = {"A": _price_frame("A", dates, [100, 101])}
    early_dates = pd.bdate_range("2026-06-01", periods=1)  # 価格データより前の日付
    history = _make_history(early_dates) + _make_history(dates)
    updated, stats = backfill_history(history, frames, CONFIG)
    assert stats["no_data"] == 1
    assert updated[0].get("market_score") is None
    assert updated[1]["market_score"] is not None


def test_advancers_decliners_accumulate_and_up_down_ratio_25_fills_in_after_25_entries():
    dates = pd.bdate_range("2026-01-01", periods=26)
    # A: 毎日上昇(常にadvancer)。B: 毎日下落(常にdecliner)。初日は前日が無いので0/0。
    a_closes = [100 + i for i in range(26)]
    b_closes = [200 - i for i in range(26)]
    frames = {
        "A": _price_frame("A", dates, a_closes),
        "B": _price_frame("B", dates, b_closes),
    }
    history = _make_history(dates)
    updated, stats = backfill_history(history, frames, CONFIG)

    assert stats["updated"] == 26
    assert updated[0]["advancers"] == 0 and updated[0]["decliners"] == 0  # 初日
    assert updated[1]["advancers"] == 1 and updated[1]["decliners"] == 1
    # up_down_ratio_25 は history 24件+当日=25エントリ必要。25番目(index 24)で初めて非None。
    assert updated[23]["up_down_ratio_25"] is None
    assert updated[24]["up_down_ratio_25"] is not None
    assert updated[25]["up_down_ratio_25"] is not None


def test_market_score_always_present_as_done_marker():
    dates = pd.bdate_range("2026-06-01", periods=1)
    frames = {"A": _price_frame("A", dates, [100])}
    history = _make_history(dates)
    updated, _ = backfill_history(history, frames, CONFIG)
    assert updated[0][DONE_MARKER] is not None
    for f in NEW_FIELDS:
        assert f in updated[0]
