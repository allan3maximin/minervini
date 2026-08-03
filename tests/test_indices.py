"""Tests for src/data/indices.py using synthetic data (no network)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.data import indices as indices_mod
from src.data.indices import (
    INDEX_SPECS,
    IndexSpec,
    _merge,
    append_intraday_tick,
    build_index_entry,
    update_indices,
)
from src.history_store import iter_records


def make_frame(start: str, days: int, base: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=days)
    closes = [base + i for i in range(days)]
    return pd.DataFrame({"date": dates, "close": closes})


def test_merge_dedupes_and_sorts():
    a = make_frame("2026-01-05", 5)
    b = make_frame("2026-01-08", 5, base=200.0)  # overlaps last 2 days of a
    merged = _merge(a, b)
    assert merged["date"].is_monotonic_increasing
    assert not merged["date"].duplicated().any()
    # overlap rows must take the NEW value (keep="last")
    overlap_date = b["date"].iloc[0]
    val = merged.loc[merged["date"] == overlap_date, "close"].iloc[0]
    assert val == b["close"].iloc[0]


def test_merge_with_empty_cache():
    fresh = make_frame("2026-01-05", 3)
    assert _merge(None, fresh) is fresh
    assert len(_merge(pd.DataFrame(columns=["date", "close"]), fresh)) == 3


def test_build_index_entry_shape():
    spec = IndexSpec("nikkei225", "日経225", "", (("yahoo", "^N225"),))
    df = make_frame("2025-01-01", 300, base=30000.0)
    entry = build_index_entry(spec, df)
    assert entry["key"] == "nikkei225"
    assert entry["name"] == "日経225"
    assert entry["unit"] == ""
    assert entry["last"] == pytest.approx(30299.0)
    assert entry["prev"] == pytest.approx(30298.0)
    assert entry["change"] == pytest.approx(1.0)
    assert entry["change_pct"] == pytest.approx(round(1.0 / 30298.0 * 100, 2))
    # series capped at SERIES_DAYS
    assert len(entry["series"]) == indices_mod.SERIES_DAYS
    point = entry["series"][-1]
    assert set(point.keys()) == {"t", "v"}
    assert point["v"] == entry["last"]
    assert entry["last_date"] == point["t"]


def test_build_index_entry_yield_decimals():
    spec = IndexSpec("jgb10y", "日本10年金利", "%", (("stooq", "10jpy.b"),), decimals=3)
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-06-01", periods=3),
            "close": [1.0512, 1.0621, 1.0755],
        }
    )
    entry = build_index_entry(spec, df)
    assert entry["last"] == round(1.0755, 3)
    assert entry["change"] == round(1.0755 - 1.0621, 3)


def test_update_indices_writes_json_and_survives_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(indices_mod, "INDICES_CACHE_DIR", tmp_path / "cache")
    json_path = tmp_path / "docs" / "indices.json"
    monkeypatch.setattr(indices_mod, "INDICES_JSON_PATH", json_path)
    # 日中ティック履歴も tmp へ逃がす(テストで実リポジトリの data/history を汚さない)。
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", tmp_path / "indices_intraday.jsonl")
    monkeypatch.setattr(indices_mod.time, "sleep", lambda *_: None)

    fail_keys = {"sox", "jgb10y"}

    def fake_fetch(spec, sleep_sec=1.0):
        if spec.key in fail_keys:
            return None
        return make_frame("2026-01-05", 30, base=1000.0)

    monkeypatch.setattr(indices_mod, "fetch_index", fake_fetch)

    result = update_indices(config={})
    assert set(result["failed"]) == fail_keys
    assert set(result["updated"]) == {s.key for s in INDEX_SPECS} - fail_keys

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "generated_at" in payload
    keys = [e["key"] for e in payload["indices"]]
    # failed keys with no cache are omitted entirely
    assert fail_keys.isdisjoint(keys)
    assert payload["stale_keys"] == []

    # caches written for successes
    for k in keys:
        assert (tmp_path / "cache" / f"{k}.parquet").exists()

    # second run: everything fails but caches exist -> served stale
    monkeypatch.setattr(indices_mod, "fetch_index", lambda spec, sleep_sec=1.0: None)
    result2 = update_indices(config={})
    assert set(result2["updated"]) == set()
    payload2 = json.loads(json_path.read_text(encoding="utf-8"))
    keys2 = [e["key"] for e in payload2["indices"]]
    assert set(keys2) == set(keys)
    assert set(payload2["stale_keys"]) == set(keys)

    # 日中ティックは1回目の実行で1行できる。2回目は値が変わっていないので増えない。
    ticks = list(iter_records(tmp_path / "indices_intraday.jsonl"))
    assert len(ticks) == 1
    assert set(ticks[0]["values"]) == set(keys)


# ------------------------------------------------------- append_intraday_tick


def _tick_entries(last: float) -> list[dict]:
    return [
        {"key": "topix", "last": last},
        {"key": "nikkei225", "last": last * 10},
    ]


def test_append_intraday_tick_writes_jst_date_and_values(tmp_path, monkeypatch):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)

    # JST 09:15 = UTC 00:15。date は JST 基準で入ること(東証のザラ場の行だけを
    # 後段が選べるようにするため)。
    now = datetime(2026, 7, 31, 9, 15, tzinfo=indices_mod.JST)
    assert append_intraday_tick(_tick_entries(2850.1), now=now) is True

    rows = list(iter_records(path))
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-07-31"
    assert rows[0]["ts"].startswith("2026-07-31T09:15:00")
    assert rows[0]["values"] == {"topix": 2850.1, "nikkei225": 28501.0}


def test_append_intraday_tick_uses_jst_date_for_us_session(tmp_path, monkeypatch):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)

    # UTC 23:30 (= JST 翌日08:30)。UTC日付ではなくJST日付が入る。
    now = datetime(2026, 7, 30, 23, 30, tzinfo=timezone.utc)
    append_intraday_tick(_tick_entries(2850.1), now=now)
    assert list(iter_records(path))[0]["date"] == "2026-07-31"


def test_append_intraday_tick_skips_unchanged_values(tmp_path, monkeypatch):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)

    base = datetime(2026, 7, 31, 22, 0, tzinfo=indices_mod.JST)
    assert append_intraday_tick(_tick_entries(2850.1), now=base) is True
    # 市場が閉じていて値が動かない実行は行を増やさない。
    assert append_intraday_tick(_tick_entries(2850.1), now=base + timedelta(minutes=15)) is False
    assert append_intraday_tick(_tick_entries(2851.0), now=base + timedelta(minutes=30)) is True
    assert len(list(iter_records(path))) == 2


def test_append_intraday_tick_ignores_entries_without_value(tmp_path, monkeypatch):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)

    assert append_intraday_tick([{"key": "topix", "last": None}]) is False
    assert not path.exists()


# --------------------------------------------------- backfill_intraday_bars
# 15分間隔のワークフローだけでは点が足りない(GitHub Actions が cron どおりに
# 起動しない)ので、大引後に5分足でまとめて埋める経路のテスト。


def _bars(day: str, times: list[str], closes: list[float]) -> pd.DataFrame:
    stamps = pd.to_datetime([f"{day} {t}" for t in times]).tz_localize(indices_mod.JST)
    return pd.DataFrame({"ts": stamps, "close": closes})


def test_backfill_intraday_bars_appends_marked_rows(tmp_path, monkeypatch):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)
    monkeypatch.setattr(
        indices_mod, "_fetch_yahoo_intraday",
        lambda symbol, interval=indices_mod.INTRADAY_BARS_INTERVAL: _bars(
            "2026-08-03", ["09:00", "09:05", "09:10"], [2850.0, 2852.5, 2849.0]
        ),
    )

    assert indices_mod.backfill_intraday_bars("2026-08-03") == 3
    rows = list(iter_records(path))
    assert [r["ts"] for r in rows] == [
        "2026-08-03T09:00:00+09:00",
        "2026-08-03T09:05:00+09:00",
        "2026-08-03T09:10:00+09:00",
    ]
    # 後段(review.classify_shape)が cron の1点ものと選り分けられるよう印が要る。
    assert all(r["src"] == indices_mod.INTRADAY_BARS_SOURCE for r in rows)
    assert all(r["date"] == "2026-08-03" for r in rows)
    assert rows[1]["values"] == {"topix": 2852.5}


def test_backfill_intraday_bars_is_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)
    monkeypatch.setattr(
        indices_mod, "_fetch_yahoo_intraday",
        lambda symbol, interval=indices_mod.INTRADAY_BARS_INTERVAL: _bars(
            "2026-08-03", ["09:00", "09:05"], [2850.0, 2852.5]
        ),
    )

    assert indices_mod.backfill_intraday_bars("2026-08-03") == 2
    # 同じ日に回し直しても同じ時刻の点は積まない(日次バッチの再実行対策)。
    assert indices_mod.backfill_intraday_bars("2026-08-03") == 0
    assert len(list(iter_records(path))) == 2


def test_backfill_intraday_bars_skips_other_days(tmp_path, monkeypatch):
    """休場日に回すと直近営業日の足が返ってくるので、当日ぶん以外は捨てる。"""
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)
    monkeypatch.setattr(
        indices_mod, "_fetch_yahoo_intraday",
        lambda symbol, interval=indices_mod.INTRADAY_BARS_INTERVAL: _bars(
            "2026-07-31", ["09:00", "09:05"], [2850.0, 2852.5]
        ),
    )

    assert indices_mod.backfill_intraday_bars("2026-08-03") == 0
    assert not path.exists()


def test_backfill_intraday_bars_survives_fetch_failure(tmp_path, monkeypatch, capsys):
    path = tmp_path / "indices_intraday.jsonl"
    monkeypatch.setattr(indices_mod, "INTRADAY_TICKS_PATH", path)
    monkeypatch.setattr(
        indices_mod, "_fetch_yahoo_intraday",
        lambda symbol, interval=indices_mod.INTRADAY_BARS_INTERVAL: None,
    )

    # 取れない日があってもレビュー本体は出したいので、例外にはしない。
    assert indices_mod.backfill_intraday_bars("2026-08-03") == 0
    assert "日中足が取れませんでした" in capsys.readouterr().out
