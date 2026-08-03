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
# fetch_all_available
# ---------------------------------------------------------------------------

_MULTI_LINK_HTML = (
    '<a href="/markets/.../syumatsu2026061200.pdf">a</a>'
    '<a href="/markets/.../syumatsu2026061900.pdf">b</a>'
    '<a href="/markets/.../syumatsu2026062600.pdf">c</a>'
)


def test_fetch_all_available_returns_all_missing_dates_ascending(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)

    def fake_get(url, timeout=30):
        if url.endswith(".html") or "05.html" in url:
            return FakeResp(text=_MULTI_LINK_HTML)
        return FakeResp(content=f"pdf-for-{url}".encode())

    monkeypatch.setattr(margin_mod.requests, "get", fake_get)
    config = {"margin": {"enabled": True, "page_url": "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"}}
    results = margin_mod.fetch_all_available(config)
    dates = [margin_mod._extract_ymd(url) for url, _content in results]
    assert dates == ["20260612", "20260619", "20260626"]


def test_fetch_all_available_skips_dates_already_in_store(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    store_path.write_text(
        json.dumps({"history": [{"date": "2026-06-12", "by_code": {}}, {"date": "2026-06-19", "by_code": {}}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)

    def fake_get(url, timeout=30):
        if url.endswith(".html") or "05.html" in url:
            return FakeResp(text=_MULTI_LINK_HTML)
        return FakeResp(content=b"dummy")

    monkeypatch.setattr(margin_mod.requests, "get", fake_get)
    config = {"margin": {"enabled": True, "page_url": "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"}}
    results = margin_mod.fetch_all_available(config)
    assert len(results) == 1
    assert margin_mod._extract_ymd(results[0][0]) == "20260626"


def test_fetch_all_available_disabled_returns_empty():
    assert margin_mod.fetch_all_available({"margin": {"enabled": False}}) == []


def test_fetch_all_available_page_failure_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", tmp_path / "margin_weekly.json")

    def boom(url, timeout=30):
        raise RuntimeError("network down")

    monkeypatch.setattr(margin_mod.requests, "get", boom)
    result = margin_mod.fetch_all_available({"margin": {"enabled": True, "page_url": "https://example.com/05.html"}})
    assert result == []


# ---------------------------------------------------------------------------
# backfill_margin_history
# ---------------------------------------------------------------------------

def test_backfill_margin_history_adds_multiple_weeks(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(
        universe_mod, "load_universe", lambda: {"stocks": [{"code": "1301"}, {"code": "166A"}]}
    )

    fetched = [
        ("https://www.jpx.co.jp/.../syumatsu2026061200.pdf", b"pdf-0612"),
        ("https://www.jpx.co.jp/.../syumatsu2026061900.pdf", b"pdf-0619"),
        ("https://www.jpx.co.jp/.../syumatsu2026062600.pdf", b"pdf-0626"),
    ]
    monkeypatch.setattr(margin_mod, "fetch_all_available", lambda config, include_existing=False: fetched)

    def fake_parse(content, universe_codes=None):
        return {"1301": {"buy": len(content), "sell": 1}}, []

    monkeypatch.setattr(margin_mod, "parse_margin_pdf", fake_parse)

    store = margin_mod.backfill_margin_history({"margin": {"keep_weeks": 13}})
    dates = [h["date"] for h in store["history"]]
    assert dates == ["2026-06-12", "2026-06-19", "2026-06-26"]
    assert store["last_url"] == fetched[-1][0]

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert [h["date"] for h in on_disk["history"]] == dates


def test_backfill_margin_history_respects_keep_weeks_trim(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(universe_mod, "load_universe", lambda: {"stocks": []})

    fetched = [
        ("https://www.jpx.co.jp/.../syumatsu2026061200.pdf", b"a"),
        ("https://www.jpx.co.jp/.../syumatsu2026061900.pdf", b"b"),
        ("https://www.jpx.co.jp/.../syumatsu2026062600.pdf", b"c"),
    ]
    monkeypatch.setattr(margin_mod, "fetch_all_available", lambda config, include_existing=False: fetched)
    # 1行もパースできなかった週(空dict)は追加しないので、中身のある週を返す
    monkeypatch.setattr(margin_mod, "parse_margin_pdf",
                        lambda content, universe_codes=None: ({"1301": {"buy": 1, "sell": 1}}, []))

    store = margin_mod.backfill_margin_history({"margin": {"keep_weeks": 2}})
    dates = [h["date"] for h in store["history"]]
    assert dates == ["2026-06-19", "2026-06-26"]


def test_backfill_margin_history_does_not_duplicate_existing_dates(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    store_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-01-01T00:00:00+09:00",
                "last_url": "https://old",
                "warnings": [],
                "history": [{"date": "2026-06-26", "by_code": {"1301": {"buy": 999, "sell": 1}}}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(universe_mod, "load_universe", lambda: {"stocks": []})
    # 同一日付を取り直しても週が二重にならず、既存の値も上書きされないこと。
    fetched = [("https://www.jpx.co.jp/.../syumatsu2026062600.pdf", b"new-content")]
    monkeypatch.setattr(margin_mod, "fetch_all_available", lambda config, include_existing=False: fetched)
    monkeypatch.setattr(margin_mod, "parse_margin_pdf", lambda content, universe_codes=None: ({"1301": {"buy": 1, "sell": 1}}, []))

    store = margin_mod.backfill_margin_history({"margin": {"keep_weeks": 13}})
    assert len(store["history"]) == 1
    assert store["history"][0]["by_code"]["1301"]["buy"] == 999  # 既存値のまま(上書きされない)


def test_backfill_margin_history_widen_fills_missing_codes(tmp_path, monkeypatch):
    """候補リストが増える前に保存した週へ、後から足りない銘柄だけを足せること。

    2026-07-28に候補リストが1000→1579銘柄へ増えた際、保存済みの週は古いリストぶん
    しか入っておらず新しく入った銘柄の需給が全週で空になった。widen=True の取り直しで
    その穴を埋める(既存銘柄の値は触らない)。
    """
    store_path = tmp_path / "margin_weekly.json"
    store_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-07-28T09:33:44+09:00",
                "last_url": "https://old",
                "warnings": [],
                # 旧リストぶん(1301だけ)しか入っていない既存週
                "history": [{"date": "2026-07-24", "by_code": {"1301": {"buy": 999, "sell": 1}}}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)

    seen = {}

    def fake_fetch_all(config, include_existing=False):
        seen["include_existing"] = include_existing
        return [("https://www.jpx.co.jp/.../syumatsu2026072400.pdf", b"pdf")]

    monkeypatch.setattr(margin_mod, "fetch_all_available", fake_fetch_all)
    monkeypatch.setattr(
        margin_mod,
        "parse_margin_pdf",
        lambda content, universe_codes=None: (
            {"1301": {"buy": 1, "sell": 1}, "146A": {"buy": 500, "sell": 20}}, []
        ),
    )

    store = margin_mod.backfill_margin_history({"margin": {"keep_weeks": 26}})
    assert seen["include_existing"] is True  # 既存週も取り直す
    assert len(store["history"]) == 1
    by_code = store["history"][0]["by_code"]
    assert by_code["1301"]["buy"] == 999  # 既存値は温存
    assert by_code["146A"] == {"buy": 500, "sell": 20}  # 足りなかった銘柄が入る

    on_disk = json.loads(store_path.read_text(encoding="utf-8"))
    assert "146A" in on_disk["history"][0]["by_code"]


def test_backfill_margin_history_no_widen_skips_existing_dates(tmp_path, monkeypatch):
    store_path = tmp_path / "margin_weekly.json"
    store_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-07-28T09:33:44+09:00",
                "last_url": "https://old",
                "warnings": [],
                "history": [{"date": "2026-07-24", "by_code": {"1301": {"buy": 999, "sell": 1}}}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", store_path)
    monkeypatch.setattr(
        margin_mod, "fetch_all_available",
        lambda config, include_existing=False: [
            ("https://www.jpx.co.jp/.../syumatsu2026072400.pdf", b"pdf")
        ],
    )
    monkeypatch.setattr(
        margin_mod, "parse_margin_pdf",
        lambda content, universe_codes=None: ({"146A": {"buy": 500, "sell": 20}}, []),
    )

    store = margin_mod.backfill_margin_history({"margin": {"keep_weeks": 26}}, widen=False)
    assert list(store["history"][0]["by_code"]) == ["1301"]  # 何も足されない


def test_backfill_margin_history_no_fetch_keeps_existing(tmp_path, monkeypatch):
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
    monkeypatch.setattr(margin_mod, "fetch_all_available", lambda config, include_existing=False: [])

    result = margin_mod.backfill_margin_history({"margin": {}})
    assert result["history"] == existing["history"]
    assert result["last_url"] == "https://old"


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
        lambda content, universe_codes=None: ({"1301": {"buy": 100 + fetch_calls["n"], "sell": 50}}, []),
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
    monkeypatch.setattr(margin_mod, "parse_margin_pdf", lambda content, universe_codes=None: ({"1301": {"buy": 999, "sell": 1}}, []))

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


# ---------------------------------------------------------------------------
# J-Quants バックフィル(過去週の一括取得)
# ---------------------------------------------------------------------------

def test_jq_records_to_by_code_normalizes_and_filters():
    records = [
        {"Code": "13010", "LongVol": "20000", "ShrtVol": "10000"},  # 5桁→1301
        {"Code": "166A0", "LongVol": 5000, "ShrtVol": 0},            # 英数字→166A
        {"Code": "99990", "LongVol": 100, "ShrtVol": 100},          # 指定集合外→捨てる
    ]
    by_code = margin_mod._jq_records_to_by_code(records, {"1301", "166A"})
    assert by_code == {
        "1301": {"buy": 20000, "sell": 10000},
        "166A": {"buy": 5000, "sell": 0},
    }


def test_jq_records_to_by_code_keeps_all_codes_by_default():
    """既定では絞らず全銘柄を保存する(2026-07-30改定)。"""
    records = [
        {"Code": "13010", "LongVol": 1, "ShrtVol": 2},
        {"Code": "99990", "LongVol": 3, "ShrtVol": 4},
    ]
    by_code = margin_mod._jq_records_to_by_code(records)
    assert set(by_code) == {"1301", "9999"}


def test_jq_records_to_by_code_skips_missing_or_bad_values():
    records = [
        {"Code": "13010", "LongVol": None, "ShrtVol": 100},   # buy欠損→skip
        {"Code": "43070", "LongVol": 100, "ShrtVol": "x"},    # 変換不能→skip
        {"LongVol": 1, "ShrtVol": 1},                          # Code欠損→skip
    ]
    assert margin_mod._jq_records_to_by_code(records, set()) == {}


def test_recent_week_end_dates_from_a_friday():
    from datetime import date
    # 2026-07-17 は金曜。3週分を新しい順に。
    got = margin_mod._recent_week_end_dates(3, today=date(2026, 7, 17))
    assert got == ["2026-07-17", "2026-07-10", "2026-07-03"]


def test_recent_week_end_dates_from_midweek_backs_to_friday():
    from datetime import date
    # 2026-07-21 は火曜 → 直近金曜は 2026-07-17
    got = margin_mod._recent_week_end_dates(2, today=date(2026, 7, 21))
    assert got == ["2026-07-17", "2026-07-10"]


def test_backfill_jquants_no_api_key_is_noop(monkeypatch):
    monkeypatch.delenv(margin_mod.API_KEY_ENV, raising=False)
    called = {"n": 0}
    monkeypatch.setattr(margin_mod, "fetch_weekly_margin_by_date",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    margin_mod.backfill_margin_jquants(config={"margin": {"keep_weeks": 4}}, weeks=2)
    assert called["n"] == 0  # 鍵なしなら一切叩かない


def test_backfill_jquants_populates_history(monkeypatch, tmp_path):
    monkeypatch.setenv(margin_mod.API_KEY_ENV, "dummy")
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", str(store_path))
    # universe を固定
    monkeypatch.setattr("src.universe.load_universe",
                        lambda: {"stocks": [{"code": "1301"}]})
    # 各金曜で1銘柄ぶん返す
    def fake_fetch(api_key, date_str, config):
        return [{"Code": "13010", "LongVol": 100 + len(date_str), "ShrtVol": 50}]
    monkeypatch.setattr(margin_mod, "fetch_weekly_margin_by_date", fake_fetch)

    from datetime import date
    monkeypatch.setattr(margin_mod, "_recent_week_end_dates",
                        lambda weeks, today=None: ["2026-07-17", "2026-07-10"])
    store = margin_mod.backfill_margin_jquants(config={"margin": {"keep_weeks": 26}}, weeks=2)
    dates = [h["date"] for h in store["history"]]
    assert dates == ["2026-07-10", "2026-07-17"]  # 昇順ソート
    assert store["history"][-1]["by_code"]["1301"]["sell"] == 50


def test_backfill_jquants_widen_fills_missing_codes(monkeypatch, tmp_path):
    """保存済みの週に足りない銘柄だけを足す(既定 widen=True)。

    候補リストで絞って保存していた時代の週には、後から候補リストに入った銘柄が
    抜けている。取り直して穴を埋めるが、既に入っている値は上書きしない。
    """
    monkeypatch.setenv(margin_mod.API_KEY_ENV, "dummy")
    store_path = tmp_path / "margin_weekly.json"
    import json as _json
    store_path.write_text(_json.dumps({
        "updated_at": None, "last_url": None, "warnings": [],
        "history": [{"date": "2026-07-17", "by_code": {"1301": {"buy": 1, "sell": 1}}}],
    }), encoding="utf-8")
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", str(store_path))

    def fake_fetch(api_key, date_str, config):
        return [
            {"Code": "13010", "LongVol": 999, "ShrtVol": 9},
            {"Code": "146A0", "LongVol": 500, "ShrtVol": 20},
        ]

    monkeypatch.setattr(margin_mod, "fetch_weekly_margin_by_date", fake_fetch)
    monkeypatch.setattr(margin_mod, "_recent_week_end_dates",
                        lambda weeks, today=None: ["2026-07-17"])
    store = margin_mod.backfill_margin_jquants(config={"margin": {"keep_weeks": 26}}, weeks=1)
    assert len(store["history"]) == 1
    by_code = store["history"][0]["by_code"]
    assert by_code["1301"]["buy"] == 1  # 既存値は温存
    assert by_code["146A"] == {"buy": 500, "sell": 20}  # 足りなかった銘柄が入る


def test_backfill_jquants_skips_existing_dates(monkeypatch, tmp_path):
    monkeypatch.setenv(margin_mod.API_KEY_ENV, "dummy")
    store_path = tmp_path / "margin_weekly.json"
    import json as _json
    store_path.write_text(_json.dumps({
        "updated_at": None, "last_url": None, "warnings": [],
        "history": [{"date": "2026-07-17", "by_code": {"1301": {"buy": 1, "sell": 1}}}],
    }), encoding="utf-8")
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", str(store_path))
    monkeypatch.setattr("src.universe.load_universe", lambda: {"stocks": [{"code": "1301"}]})
    seen = []
    def fake_fetch(api_key, date_str, config):
        seen.append(date_str)
        return [{"Code": "13010", "LongVol": 999, "ShrtVol": 9}]
    monkeypatch.setattr(margin_mod, "fetch_weekly_margin_by_date", fake_fetch)
    monkeypatch.setattr(margin_mod, "_recent_week_end_dates",
                        lambda weeks, today=None: ["2026-07-17", "2026-07-10"])
    store = margin_mod.backfill_margin_jquants(
        config={"margin": {"keep_weeks": 26}}, weeks=2, widen=False)
    # widen=False なら既存の 07-17 は取り直さず(buy=1のまま)、07-10 だけ取得
    assert "2026-07-17" not in seen
    by = {h["date"]: h["by_code"]["1301"]["buy"] for h in store["history"]}
    assert by["2026-07-17"] == 1
    assert by["2026-07-10"] == 999


# ---------------------------------------------------------------------------
# 401/403 (鍵が無効 / プラン不足) の扱い
# ---------------------------------------------------------------------------
# /markets/margin-interest は Standard プラン以上でしか配信されない。Free の鍵で
# 叩くと 403 が返るが、以前はこれを「その週はデータが無い」と同じ空リストにして
# 握り潰していたため、全週が空振りしても "nothing added" と出て正常終了に見えていた。


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.parametrize("status", [401, 403])
def test_fetch_weekly_margin_raises_on_auth_or_plan_error(monkeypatch, status):
    monkeypatch.setattr(margin_mod.requests, "get",
                        lambda *a, **k: _FakeResp(status))
    with pytest.raises(margin_mod.JQuantsAccessError) as excinfo:
        margin_mod.fetch_weekly_margin_by_date("dummy", "2026-07-17", {})
    assert str(status) in str(excinfo.value)


def test_fetch_weekly_margin_other_http_error_still_returns_empty(monkeypatch):
    """500 などは従来どおり空リスト。1週分の失敗で全体を止めない方針は維持する。"""
    monkeypatch.setattr(margin_mod.requests, "get",
                        lambda *a, **k: _FakeResp(500))
    assert margin_mod.fetch_weekly_margin_by_date("dummy", "2026-07-17", {}) == []


def test_backfill_jquants_aborts_immediately_on_access_error(monkeypatch, tmp_path, capsys):
    """403 が出たら残りの週を叩かずに打ち切り、既存の store も書き換えない。"""
    monkeypatch.setenv(margin_mod.API_KEY_ENV, "dummy")
    store_path = tmp_path / "margin_weekly.json"
    import json as _json
    store_path.write_text(_json.dumps({
        "updated_at": "before", "last_url": None, "warnings": [],
        "history": [{"date": "2026-07-03", "by_code": {"1301": {"buy": 7, "sell": 3}}}],
    }), encoding="utf-8")
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", str(store_path))

    calls = []

    def fake_fetch(api_key, date_str, config):
        calls.append(date_str)
        raise margin_mod.JQuantsAccessError("403 だよ")

    monkeypatch.setattr(margin_mod, "fetch_weekly_margin_by_date", fake_fetch)
    monkeypatch.setattr(margin_mod, "_recent_week_end_dates",
                        lambda weeks, today=None: ["2026-07-17", "2026-07-10"])

    store = margin_mod.backfill_margin_jquants(
        config={"margin": {"keep_weeks": 26}}, weeks=2)

    assert len(calls) == 1  # 1回目で打ち切る(Thu/Wed も次の週も叩かない)
    assert "ERROR" in capsys.readouterr().out
    # 既存の履歴はそのまま。ファイルも書き換わっていない。
    assert [h["date"] for h in store["history"]] == ["2026-07-03"]
    assert _json.loads(store_path.read_text(encoding="utf-8"))["updated_at"] == "before"


def test_backfill_jquants_warns_when_every_request_returns_nothing(monkeypatch, tmp_path, capsys):
    """全リクエストが空だったときは「全部保存済み」と同じ文言にしない。"""
    monkeypatch.setenv(margin_mod.API_KEY_ENV, "dummy")
    store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(margin_mod, "MARGIN_STORE_PATH", str(store_path))
    monkeypatch.setattr(margin_mod, "fetch_weekly_margin_by_date",
                        lambda api_key, date_str, config: [])
    monkeypatch.setattr(margin_mod, "_recent_week_end_dates",
                        lambda weeks, today=None: ["2026-07-17"])

    margin_mod.backfill_margin_jquants(config={"margin": {"keep_weeks": 26}}, weeks=1)

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "all returned" in out
