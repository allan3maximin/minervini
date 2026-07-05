"""Tests for src/data/indices.py using synthetic data (no network)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.data import indices as indices_mod
from src.data.indices import INDEX_SPECS, IndexSpec, _merge, build_index_entry, update_indices


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
