"""src/deepdive/jq_raw.py のテスト。

ネットワークには一切出ない(§9)。requests.get を偽物に差し替え、
tests/fixtures/jq_summary_page*.json を返させる。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.deepdive import jq_raw
from src import history_store

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._body


def test_fetch_summaries_paginates(monkeypatch):
    """pagination_key が付いている間はページを辿り続ける。"""
    page1 = _load_fixture("jq_summary_page1.json")
    page2 = _load_fixture("jq_summary_page2.json")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        if "pagination_key" not in params:
            return FakeResponse(200, page1)
        return FakeResponse(200, page2)

    monkeypatch.setattr(jq_raw.requests, "get", fake_get)

    records = jq_raw.fetch_summaries("dummy-key", None, code="7134")

    assert len(records) == 2
    assert len(calls) == 2
    assert calls[1]["pagination_key"] == "page2token"
    assert calls[0]["code"] == "7134"


def test_fetch_summaries_retries_once_on_429(monkeypatch):
    page1 = _load_fixture("jq_summary_page1.json")
    # pagination_key 抜きにして1ページで終わらせる
    page1_no_next = {"data": page1["data"]}
    responses = [FakeResponse(429, {}), FakeResponse(200, page1_no_next)]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr(jq_raw.requests, "get", fake_get)
    monkeypatch.setattr(jq_raw.time, "sleep", lambda _: None)

    records = jq_raw.fetch_summaries("dummy-key", None, code="7134")

    assert len(calls) == 2
    assert len(records) == 1


def test_fetch_summaries_gives_up_after_one_retry(monkeypatch):
    """429 が2回続いたら2回目の raise_for_status で例外(無限リトライしない)。"""
    responses = [FakeResponse(429, {}), FakeResponse(429, {})]

    def fake_get(url, params=None, headers=None, timeout=None):
        return responses.pop(0)

    monkeypatch.setattr(jq_raw.requests, "get", fake_get)
    monkeypatch.setattr(jq_raw.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError):
        jq_raw.fetch_summaries("dummy-key", None, code="7134")


def test_fetch_and_store_requires_api_key(monkeypatch):
    monkeypatch.delenv(jq_raw.API_KEY_ENV, raising=False)
    with pytest.raises(ValueError):
        jq_raw.fetch_and_store("7134", api_key=None)


def test_fetch_and_store_writes_raw_records(monkeypatch):
    page1 = _load_fixture("jq_summary_page1.json")
    page2 = _load_fixture("jq_summary_page2.json")

    def fake_get(url, params=None, headers=None, timeout=None):
        if "pagination_key" not in params:
            return FakeResponse(200, page1)
        return FakeResponse(200, page2)

    monkeypatch.setattr(jq_raw.requests, "get", fake_get)

    n = jq_raw.fetch_and_store("7134", api_key="dummy-key")

    assert n == 2
    rows = list(history_store.iter_records(jq_raw.raw_path("7134")))
    assert len(rows) == 2
    assert {r["DiscDate"] for r in rows} == {"2025-05-14", "2025-08-13"}
    # 生レコードは加工せずそのまま保存されている(文字列のまま、int化しない)
    assert rows[0]["OP"] == "700000000"


def test_fetch_and_store_skips_already_fetched_discdate(monkeypatch):
    """再実行しても DiscDate が既にあるレコードは重複追記しない。"""
    page1 = _load_fixture("jq_summary_page1.json")
    page2 = _load_fixture("jq_summary_page2.json")

    def fake_get(url, params=None, headers=None, timeout=None):
        if "pagination_key" not in params:
            return FakeResponse(200, page1)
        return FakeResponse(200, page2)

    monkeypatch.setattr(jq_raw.requests, "get", fake_get)

    first = jq_raw.fetch_and_store("7134", api_key="dummy-key")
    second = jq_raw.fetch_and_store("7134", api_key="dummy-key")

    assert first == 2
    assert second == 0
    rows = list(history_store.iter_records(jq_raw.raw_path("7134")))
    assert len(rows) == 2


def test_fetch_and_store_returns_zero_when_no_data(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"data": []})

    monkeypatch.setattr(jq_raw.requests, "get", fake_get)

    n = jq_raw.fetch_and_store("9999", api_key="dummy-key")
    assert n == 0
    assert not jq_raw.raw_path("9999").exists()
