"""src/deepdive/store.py のテスト。

重点は規律ルール R1〜R4 と UC-1(drivers/break_conditions 必須)。
パス定数は tests/conftest.py の isolate_write_paths で tmp_path に差し替え済みなので、
ここでは実データを一切汚さずに store.WATCHLIST_PATH 等をそのまま呼べる。
"""
from __future__ import annotations

import pytest

from src import history_store
from src.deepdive import store


def _watch_rec(**overrides):
    rec = {
        "ticker": "7134",
        "name": "アップガレージグループ",
        "fy_end_month": 3,
        "drivers": "既存店売上 = 客数 × 客単価。買取台数が先行指標",
        "break_conditions": "既存店が2ヶ月連続マイナス",
    }
    rec.update(overrides)
    return rec


def _pred_rec(**overrides):
    rec = {
        "ticker": "7134",
        "quarter": "2026Q2",
        "earnings_date": "2026-11-07",
        "company_op": 1_200_000_000,
        "my_op": 1_350_000_000,
        "confidence": "中",
        "action": "買う",
        "model_ver": "v1",
        "rationale": "既存店が3ヶ月連続で+5%超",
    }
    rec.update(overrides)
    return rec


# ---------------------------------------------------------------------------
# UC-1: drivers / break_conditions が空なら登録拒否
# ---------------------------------------------------------------------------

def test_add_watch_rejects_empty_drivers():
    with pytest.raises(ValueError):
        store.add_watch(_watch_rec(drivers=""))


def test_add_watch_rejects_empty_break_conditions():
    with pytest.raises(ValueError):
        store.add_watch(_watch_rec(break_conditions="   "))


def test_add_watch_rejects_missing_ticker():
    with pytest.raises(ValueError):
        store.add_watch(_watch_rec(ticker=""))


def test_add_watch_ok_writes_written_at_and_defaults():
    store.add_watch(_watch_rec())
    rows = store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
    assert len(rows) == 1
    assert rows[0]["written_at"]
    assert rows[0]["status"] == "active"
    assert rows[0]["next_earnings_date_manual"] is None


def test_add_watch_ignores_caller_supplied_written_at():
    """written_at を引数で渡しても無視され、必ず now_iso() で上書きされる。"""
    store.add_watch(_watch_rec(written_at="2000-01-01T00:00:00+09:00"))
    rows = store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
    assert not rows[0]["written_at"].startswith("2000-01-01")


def test_add_watch_last_write_wins_for_master_data():
    """watchlist はマスタなので後勝ちでよい(予想と違って書き換えを禁じない)。"""
    store.add_watch(_watch_rec(name="旧社名"))
    store.add_watch(_watch_rec(name="新社名"))
    rows = store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
    assert len(rows) == 1
    assert rows[0]["name"] == "新社名"


# ---------------------------------------------------------------------------
# R2: 固定フィールドは初出勝ち。二重登録は追記そのものを拒否する
# ---------------------------------------------------------------------------

def test_add_prediction_duplicate_key_raises():
    store.add_prediction(_pred_rec())
    with pytest.raises(ValueError):
        store.add_prediction(_pred_rec(my_op=999_999_999))


def test_add_prediction_different_ver_is_allowed():
    """訂正は同じ (ticker, quarter) でも model_ver を変えれば通る(R3の裏側)。"""
    store.add_prediction(_pred_rec(model_ver="v1"))
    store.add_prediction(_pred_rec(model_ver="v2"))
    rows = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert len(rows) == 2


def test_load_first_wins_returns_first_row_on_direct_edit():
    """ファイルを直接編集されて同一キーが2行あっても、初出(1行目)だけを採用する。"""
    history_store.append_records(store.PREDICTIONS_PATH, [
        {"ticker": "7134", "quarter": "2026Q2", "model_ver": "v1", "my_op": 100},
        {"ticker": "7134", "quarter": "2026Q2", "model_ver": "v1", "my_op": 999},
    ])
    rows = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert len(rows) == 1
    assert rows[0]["my_op"] == 100


def test_add_prediction_missing_field_raises():
    rec = _pred_rec()
    del rec["rationale"]
    with pytest.raises(ValueError):
        store.add_prediction(rec)


def test_add_prediction_rejects_invalid_confidence():
    with pytest.raises(ValueError):
        store.add_prediction(_pred_rec(confidence="激高"))


def test_add_prediction_rejects_invalid_action():
    with pytest.raises(ValueError):
        store.add_prediction(_pred_rec(action="様子見"))


# ---------------------------------------------------------------------------
# R1: 発表後に書いた予想は valid: false で保存する(例外にしない)
# ---------------------------------------------------------------------------

def test_add_prediction_before_earnings_date_is_valid():
    store.add_prediction(_pred_rec(earnings_date="2099-01-01"))
    rows = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert rows[0]["valid"] is True
    assert rows[0]["invalid_reason"] is None


def test_add_prediction_after_earnings_date_is_invalid_not_raised(monkeypatch):
    monkeypatch.setattr(store, "now_iso", lambda: "2026-11-08T09:00:00+09:00")
    store.add_prediction(_pred_rec(earnings_date="2026-11-07"))
    rows = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert len(rows) == 1
    assert rows[0]["valid"] is False
    assert rows[0]["invalid_reason"] == "記入日が発表日以降"


def test_add_prediction_on_earnings_date_itself_is_invalid(monkeypatch):
    """発表日当日に書いた予想も無効。寄り前/場中/引け後を区別できないため。"""
    monkeypatch.setattr(store, "now_iso", lambda: "2026-11-07T08:00:00+09:00")
    store.add_prediction(_pred_rec(earnings_date="2026-11-07"))
    rows = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert rows[0]["valid"] is False


# ---------------------------------------------------------------------------
# R3: 既存 ver への add_version は拒否(遡及禁止)
# ---------------------------------------------------------------------------

def test_add_version_duplicate_raises():
    store.add_version({"ver": "v1", "change": "初版", "reason": "設計書どおり"})
    with pytest.raises(ValueError):
        store.add_version({"ver": "v1", "change": "書き換え", "reason": "気が変わった"})


def test_add_version_new_ver_ok():
    store.add_version({"ver": "v1", "change": "初版", "reason": "設計書どおり"})
    store.add_version({"ver": "v2", "change": "confidence の閾値変更", "reason": "v1が甘すぎた"})
    rows = store.load_first_wins(store.VERSIONS_PATH, ("ver",))
    assert {r["ver"] for r in rows} == {"v1", "v2"}


def test_add_version_missing_ver_raises():
    with pytest.raises(ValueError):
        store.add_version({"change": "...", "reason": "..."})


# ---------------------------------------------------------------------------
# actuals: 後勝ち + timing 必須
# ---------------------------------------------------------------------------

def test_add_actual_requires_timing():
    with pytest.raises(ValueError):
        store.add_actual({"ticker": "7134", "quarter": "2026Q2", "timing": "昼休み"})


def test_add_actual_last_write_wins():
    store.add_actual({"ticker": "7134", "quarter": "2026Q2", "timing": "引け後", "op": 100})
    store.add_actual({"ticker": "7134", "quarter": "2026Q2", "timing": "引け後", "op": 101})
    rows = store.load_last_wins(store.ACTUALS_PATH, ("ticker", "quarter"))
    assert len(rows) == 1
    assert rows[0]["op"] == 101
