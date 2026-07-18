"""Tests for src/data/margin.py using synthetic PDF-line text (no network, no real PDF)."""
from __future__ import annotations

import json

import pytest

from src.data import margin as margin_mod
from src import universe as universe_mod


def make_row_line(name: str, code5: str, isin: str, nums: list[int], b: bool = True) -> str:
    """1行分の合成PDFテキストを組み立てる(実測フォーマットを模した合成データ)。"""
    prefix = "B " if b else ""

    def fmt(n: int) -> str:
        if n < 0:
            return f"▲ {abs(n)}"
        return str(n)

    nums_str = " ".join(fmt(n) for n in nums)
    return f"{prefix}{name}普通株式{code5} {isin} {nums_str}"


# 12個: sell_total, sell_total_wow, buy_total, buy_total_wow,
#       general_sell, general_sell_wow, standard_sell, standard_sell_wow,
#       general_buy, general_buy_wow, standard_buy, standard_buy_wow
ROW_A = make_row_line("テスト商事", "13010", "JP3382200001", [10000, -300, 20000, 1200, 4000, -100, 6000, -200, 8000, 500, 12000, 700])
ROW_B = make_row_line("新コード工業", "166A0", "JP1234567890", [0, 0, 5000, 0, 0, 0, 0, 0, 2000, 0, 3000, 0], b=False)
ROW_C_NOT_IN_UNIVERSE = make_row_line("対象外株式", "99990", "JP9999999999", [100, 0, 200, 0, 50, 0, 50, 0, 100, 0, 100, 0])

UNIVERSE_CODES = {"1301", "166A"}


def test_normalize_code():
    assert margin_mod._normalize_code("13010") == "1301"
    assert margin_mod._normalize_code("166A0") == "166A"
    assert margin_mod._normalize_code("285A") == "285A"  # already 4-char, untouched


def test_parse_numbers_handles_negative_marker():
    nums = margin_mod._parse_numbers("10,000 ▲ 300 20000 1200 4000 ▲ 100 6000 ▲ 200 8000 500 12000 700")
    assert nums == [10000, -300, 20000, 1200, 4000, -100, 6000, -200, 8000, 500, 12000, 700]


def test_parse_row_extracts_code_and_totals():
    row = margin_mod._parse_row(ROW_A)
    assert row is not None
    assert row["code"] == "1301"
    assert row["sell_total"] == 10000
    assert row["buy_total"] == 20000


def test_parse_margin_pdf_filters_to_universe(monkeypatch):
    lines = [ROW_A, ROW_B, ROW_C_NOT_IN_UNIVERSE, "ヘッダ行など無関係な文字列"]
    monkeypatch.setattr(margin_mod, "_extract_lines", lambda content: lines)

    by_code, warnings = margin_mod.parse_margin_pdf(b"dummy", UNIVERSE_CODES)
    assert warnings == []
    assert set(by_code.keys()) == {"1301", "166A"}
    assert by_code["1301"] == {"buy": 20000, "sell": 10000}
    assert by_code["166A"] == {"buy": 5000, "sell": 0}


def test_parse_margin_pdf_empty_universe_keeps_all(monkeypatch):
    lines = [ROW_A, ROW_B]
    monkeypatch.setattr(margin_mod, "_extract_lines", lambda content: lines)
    by_code, warnings = margin_mod.parse_margin_pdf(b"dummy", set())
    assert warnings == []
    assert set(by_code.keys()) == {"1301", "166A"}


def test_parse_margin_pdf_format_broken_returns_empty_with_warning(monkeypatch):
    monkeypatch.setattr(margin_mod, "_extract_lines", lambda content: ["何も一致しない行", "another junk line"])
    by_code, warnings = margin_mod.parse_margin_pdf(b"dummy", UNIVERSE_CODES)
    assert by_code == {}
    assert len(warnings) == 1
    assert "0 rows" in warnings[0]


def test_parse_margin_pdf_extract_raises_returns_empty_with_warning(monkeypatch):
    def boom(content):
        raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(margin_mod, "_extract_lines", boom)
    by_code, warnings = margin_mod.parse_margin_pdf(b"dummy", UNIVERSE_CODES)
    assert by_code == {}
    assert "PDF open/extract failed" in warnings[0]


# ---------------------------------------------------------------------------
# fetch_latest
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def test_fetch_latest_skips_when_same_url(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    html = '<a href="/markets/statistics-equities/margin/data/syumatsu2026062600.pdf">latest</a>'
    monkeypatch.setattr(margin_mod.requests, "get", lambda url, timeout=30: FakeResp(text=html))
    store_path.write_text(
        json.dumps({"last_url": "https://www.jpx.co.jp/markets/statistics-equities/margin/data/syumatsu2026062600.pdf"}),
        encoding="utf-8",
    )
    result = margin_mod.fetch_latest({"margin": {"enabled": True, "page_url": "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"}})
    assert result is None


def test_fetch_latest_downloads_new_pdf(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    html = '<a href="/markets/statistics-equities/margin/data/syumatsu2026070300.pdf">latest</a>'

    calls = {"n": 0}

    def fake_get(url, timeout=30):
        calls["n"] += 1
        if url.endswith(".html") or "05.html" in url:
            return FakeResp(text=html)
        return FakeResp(content=b"%PDF-fake-bytes")

    monkeypatch.setattr(margin_mod.requests, "get", fake_get)
    result = margin_mod.fetch_latest({"margin": {"enabled": True, "page_url": "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"}})
    assert result is not None
    url, content = result
    assert url.endswith("syumatsu2026070300.pdf")
    assert content == b"%PDF-fake-bytes"


def test_fetch_latest_disabled_returns_none():
    assert margin_mod.fetch_latest({"margin": {"enabled": False}}) is None


def test_fetch_latest_request_failure_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", tmp_path / "margin_weekly.json")

    def boom(url, timeout=30):
        raise RuntimeError("network down")

    monkeypatch.setattr(margin_mod.requests, "get", boom)
    result = margin_mod.fetch_latest({"margin": {"enabled": True, "page_url": "https://example.com/05.html"}})
    assert result is None


def test_fetch_latest_no_link_found_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", tmp_path / "margin_weekly.json")
    monkeypatch.setattr(margin_mod.requests, "get", lambda url, timeout=30: FakeResp(text="<html>no links here</html>"))
    result = margin_mod.fetch_latest({"margin": {"enabled": True, "page_url": "https://example.com/05.html"}})
    assert result is None


# ---------------------------------------------------------------------------
# update_margin_store
# ---------------------------------------------------------------------------

def test_update_margin_store_writes_history_and_trims(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(
        universe_mod, "load_universe", lambda: {"stocks": [{"code": "1301"}, {"code": "166A"}]}
    )

    fetch_calls = {"n": 0}
    urls = [
        "https://www.jpx.co.jp/.../syumatsu2026061200.pdf",
        "https://www.jpx.co.jp/.../syumatsu2026061900.pdf",
        "https://www.jpx.co.jp/.../syumatsu2026062600.pdf",
    ]

    def fake_fetch_latest(config):
        idx = fetch_calls["n"]
        fetch_calls["n"] += 1
        if idx >= len(urls):
            return None
        return urls[idx], b"dummy"

    monkeypatch.setattr(margin_mod, "fetch_latest", fake_fetch_latest)
    monkeypatch.setattr(
        margin_mod,
        "parse_margin_pdf",
        lambda content, universe_codes: ({"1301": {"buy": 100 + fetch_calls["n"], "sell": 50}}, []),
    )

    config = {"margin": {"keep_weeks": 2}}
    for _ in range(3):
        margin_mod.update_margin_store(config)

    store = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(store["history"]) == 2  # keep_weeks=2 trims the oldest
    dates = [h["date"] for h in store["history"]]
    assert dates == sorted(dates)
    assert dates[-1] == "2026-06-26"


def test_update_margin_store_replaces_same_date(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(universe_mod, "load_universe", lambda: {"stocks": []})

    url = "https://www.jpx.co.jp/.../syumatsu2026062600.pdf"
    monkeypatch.setattr(margin_mod, "fetch_latest", lambda config: (url, b"dummy"))
    monkeypatch.setattr(margin_mod, "parse_margin_pdf", lambda content, universe_codes: ({"1301": {"buy": 999, "sell": 1}}, []))

    margin_mod.update_margin_store({"margin": {"keep_weeks": 13}})
    margin_mod.update_margin_store({"margin": {"keep_weeks": 13}})  # same URL/date twice

    store = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(store["history"]) == 1
    assert store["history"][0]["by_code"]["1301"]["buy"] == 999


def test_update_margin_store_no_fetch_keeps_existing(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    existing = {
        "updated_at": "2026-01-01T00:00:00+09:00",
        "last_url": "https://old",
        "warnings": [],
        "history": [{"date": "2026-06-19", "by_code": {"1301": {"buy": 1, "sell": 2}}}],
    }
    store_path.write_text(json.dumps(existing), encoding="utf-8")
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(universe_mod, "load_universe", lambda: {"stocks": []})
    monkeypatch.setattr(margin_mod, "fetch_latest", lambda config: None)

    result = margin_mod.update_margin_store({"margin": {}})
    assert result["history"] == existing["history"]
    assert result["last_url"] == "https://old"


# ---------------------------------------------------------------------------
# build_margin_metrics
# ---------------------------------------------------------------------------

def test_build_margin_metrics_full():
    store = {
        "history": [
            {"date": "2026-06-19", "by_code": {"1301": {"buy": 10000, "sell": 5000}}},
            {"date": "2026-06-26", "by_code": {"1301": {"buy": 12000, "sell": 4000}}},
        ]
    }
    m = margin_mod.build_margin_metrics("1301", {"vol_ma50": 3000.0}, store=store)
    assert m["ratio"] == round(12000 / 4000, 3)
    assert m["buy"] == 12000
    assert m["sell"] == 4000
    assert m["date"] == "2026-06-26"
    assert m["buy_wow_pct"] == round((12000 / 10000 - 1.0) * 100.0, 2)
    assert m["days_to_cover"] == round(12000 / 3000.0, 2)


def test_build_margin_metrics_sell_zero_ratio_none():
    store = {"history": [{"date": "2026-06-26", "by_code": {"1301": {"buy": 500, "sell": 0}}}]}
    m = margin_mod.build_margin_metrics("1301", {}, store=store)
    assert m["ratio"] is None
    assert m["days_to_cover"] is None  # no vol_ma50 in latest_row


def test_build_margin_metrics_no_prior_week():
    store = {"history": [{"date": "2026-06-26", "by_code": {"1301": {"buy": 500, "sell": 100}}}]}
    m = margin_mod.build_margin_metrics("1301", None, store=store)
    assert m["buy_wow_pct"] is None
    assert m["days_to_cover"] is None


def test_build_margin_metrics_missing_code_returns_none():
    store = {"history": [{"date": "2026-06-26", "by_code": {"1301": {"buy": 500, "sell": 100}}}]}
    assert margin_mod.build_margin_metrics("9999", {}, store=store) is None


def test_build_margin_metrics_no_history_returns_none():
    assert margin_mod.build_margin_metrics("1301", {}, store={"history": []}) is None


def test_build_margin_metrics_legacy_entry_missing_code_in_prior_week():
    # 前週エントリはあるが対象コードが無い(その週は非対象/フィルタ落ち)場合も安全に None 扱い
    store = {
        "history": [
            {"date": "2026-06-19", "by_code": {}},
            {"date": "2026-06-26", "by_code": {"1301": {"buy": 500, "sell": 100}}},
        ]
    }
    m = margin_mod.build_margin_metrics("1301", {}, store=store)
    assert m["buy_wow_pct"] is None
