import json

import pytest

import src.data.jquants as jq


def _rec(**kw):
    base = {
        "DiscDate": "2025-08-01",
        "Code": "72030",
        "DocType": "1QFinancialStatements_Consolidated_JP",
        "CurPerType": "1Q",
        "CurFYSt": "2025-04-01",
        "CurFYEn": "2026-03-31",
        "Sales": "1000000",
        "EPS": "50.5",
        "NCSales": "",
        "NCEPS": "",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# record_to_point
# ---------------------------------------------------------------------------

def test_record_to_point_basic():
    p = jq.record_to_point(_rec())
    assert p == {
        "code": "7203",
        "fy_start": "2025-04-01",
        "n": 1,
        "label": "2025Q1",
        "eps": 50.5,
        "revenue": 1000000.0,
        "disc_date": "2025-08-01",
    }


def test_record_to_point_fy_maps_to_q4():
    p = jq.record_to_point(_rec(CurPerType="FY"))
    assert p["n"] == 4
    assert p["label"] == "2025Q4"


def test_record_to_point_rejects_non_financial_statements():
    assert jq.record_to_point(_rec(DocType="EarnForecastRevision")) is None
    assert jq.record_to_point(_rec(DocType="")) is None


def test_record_to_point_rejects_5q_and_unknown_period():
    assert jq.record_to_point(_rec(CurPerType="5Q")) is None
    assert jq.record_to_point(_rec(CurPerType="")) is None


def test_record_to_point_nonconsolidated_fallback():
    p = jq.record_to_point(_rec(Sales="", EPS="", NCSales="500", NCEPS="7.2"))
    assert p["eps"] == 7.2
    assert p["revenue"] == 500.0


def test_record_to_point_rejects_when_no_values():
    assert jq.record_to_point(_rec(Sales="", EPS="", NCSales="", NCEPS="")) is None
    assert jq.record_to_point(_rec(Sales="-", EPS="-", NCSales="-", NCEPS="-")) is None


# ---------------------------------------------------------------------------
# derive_quarters (YTD差分)
# ---------------------------------------------------------------------------

def _point(n, eps, revenue, fy="2025-04-01", disc="2025-08-01"):
    return {
        "code": "7203",
        "fy_start": fy,
        "n": n,
        "label": f"{fy[:4]}Q{n}",
        "eps": eps,
        "revenue": revenue,
        "disc_date": disc,
    }


def test_derive_quarters_diffs_ytd():
    points = [_point(1, 10.0, 100.0), _point(2, 25.0, 220.0), _point(3, 45.0, 360.0)]
    out = jq.derive_quarters(points)
    assert out == [
        {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0},
        {"fiscal_quarter": "2025Q2", "eps": 15.0, "revenue": 120.0},
        {"fiscal_quarter": "2025Q3", "eps": 20.0, "revenue": 140.0},
    ]


def test_derive_quarters_dedup_keeps_latest_disclosure():
    points = [
        _point(1, 10.0, 100.0, disc="2025-08-01"),
        _point(1, 12.0, 110.0, disc="2025-09-15"),  # 訂正短信が勝つ
    ]
    out = jq.derive_quarters(points)
    assert out == [{"fiscal_quarter": "2025Q1", "eps": 12.0, "revenue": 110.0}]


def test_derive_quarters_separates_fiscal_years():
    points = [
        _point(4, 100.0, 1000.0, fy="2024-04-01"),
        _point(1, 10.0, 100.0, fy="2025-04-01"),
    ]
    out = jq.derive_quarters(points)
    labels = {q["fiscal_quarter"] for q in out}
    assert labels == {"2024Q4", "2025Q1"}
    # 年度をまたいで差分しない: 2025Q1は0基準
    q1 = next(q for q in out if q["fiscal_quarter"] == "2025Q1")
    assert q1["eps"] == 10.0


def test_derive_quarters_none_values_skip_key():
    points = [_point(1, None, 100.0), _point(2, 25.0, None)]
    out = jq.derive_quarters(points)
    assert out[0] == {"fiscal_quarter": "2025Q1", "eps": None, "revenue": 100.0}
    # 前Q点のepsがNoneなら0基準ではなくそのまま(base None -> 0扱い)
    assert out[1]["eps"] == 25.0
    assert out[1]["revenue"] is None


# ---------------------------------------------------------------------------
# _merge_into_store
# ---------------------------------------------------------------------------

def test_merge_into_store_overwrites_and_caps():
    store = {}
    q1 = [{"fiscal_quarter": "2025Q1", "eps": 1.0, "revenue": 10.0}]
    jq._merge_into_store(store, "7203", q1, "2025-08-01", max_keep=2)
    assert store["7203"]["source"] == "jquants"
    assert store["7203"]["checked_date"] == "2025-08-01"

    q_more = [
        {"fiscal_quarter": "2025Q1", "eps": 2.0, "revenue": 20.0},  # 上書き
        {"fiscal_quarter": "2025Q2", "eps": 3.0, "revenue": 30.0},
        {"fiscal_quarter": "2025Q3", "eps": 4.0, "revenue": 40.0},
    ]
    jq._merge_into_store(store, "7203", q_more, "2025-11-01", max_keep=2)
    entry = store["7203"]
    assert entry["checked_date"] == "2025-11-01"
    assert [q["fiscal_quarter"] for q in entry["quarters"]] == ["2025Q2", "2025Q3"]  # max_keep=2


# ---------------------------------------------------------------------------
# fetch_summaries (pagination + 429 retry)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def test_fetch_summaries_pagination(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        assert headers == {"x-api-key": "KEY"}
        if "pagination_key" not in params:
            return _Resp(body={"data": [{"a": 1}], "pagination_key": "pk1"})
        return _Resp(body={"data": [{"a": 2}]})

    monkeypatch.setattr(jq.requests, "get", fake_get)
    recs = jq.fetch_summaries("KEY", {}, code="7203")
    assert recs == [{"a": 1}, {"a": 2}]
    assert calls[0]["code"] == "7203"
    assert calls[1]["pagination_key"] == "pk1"


def test_fetch_summaries_retries_once_on_429(monkeypatch):
    monkeypatch.setattr(jq.time, "sleep", lambda s: None)
    seq = [_Resp(status_code=429), _Resp(body={"data": [{"a": 1}]})]

    def fake_get(url, params=None, headers=None, timeout=None):
        return seq.pop(0)

    monkeypatch.setattr(jq.requests, "get", fake_get)
    assert jq.fetch_summaries("KEY", {}, code="7203") == [{"a": 1}]


# ---------------------------------------------------------------------------
# update_fundamentals_auto (state guard / no-key passthrough)
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    auto_path = tmp_path / "fundamentals_auto.json"
    state_path = tmp_path / "jquants_state.json"
    monkeypatch.setattr(jq, "STATE_PATH", state_path)
    monkeypatch.setattr(jq, "AUTO_PATH", auto_path)
    monkeypatch.setattr(jq, "load_auto_store", lambda path=None: json.loads(auto_path.read_text()) if auto_path.exists() else {})
    monkeypatch.setattr(jq.time, "sleep", lambda s: None)
    return auto_path, state_path


_CFG = {"jquants": {"enabled": True, "lookback_days": 2, "sleep_sec": 0, "max_quarters_keep": 12}}


def test_update_without_key_returns_existing_store(isolated_paths, monkeypatch):
    auto_path, state_path = isolated_paths
    auto_path.write_text(json.dumps({"7203": {"quarters": [], "checked_date": None, "source": "jquants"}}))
    monkeypatch.delenv(jq.API_KEY_ENV, raising=False)

    store = jq.update_fundamentals_auto(["7203"], _CFG)
    assert "7203" in store
    assert not state_path.exists()  # ネットワークに出ていない


def test_update_all_failures_does_not_advance_state(isolated_paths, monkeypatch):
    auto_path, state_path = isolated_paths
    monkeypatch.setenv(jq.API_KEY_ENV, "BADKEY")

    def boom(api_key, config, *, code=None, day=None):
        raise RuntimeError("HTTP 401")

    monkeypatch.setattr(jq, "fetch_summaries", boom)
    jq.update_fundamentals_auto(["7203"], _CFG)
    assert not state_path.exists()  # 全fail -> stateを進めない


def test_update_respects_data_delay(isolated_paths, monkeypatch):
    """Freeプラン: 遅延分より新しい日付は問い合わせず、stateもそこまで。"""
    auto_path, state_path = isolated_paths
    monkeypatch.setenv(jq.API_KEY_ENV, "KEY")
    from datetime import datetime, timedelta

    queried = []

    def fake_fetch(api_key, config, *, code=None, day=None):
        queried.append(day)
        return []

    monkeypatch.setattr(jq, "fetch_summaries", fake_fetch)
    cfg = {"jquants": {"enabled": True, "lookback_days": 2, "sleep_sec": 0,
                       "max_quarters_keep": 12, "data_delay_days": 85}}
    jq.update_fundamentals_auto(["7203"], cfg)

    end_day = datetime.now().date() - timedelta(days=85)
    assert max(queried) == end_day
    state = json.loads(state_path.read_text())
    assert state["last_list_date"] == end_day.isoformat()


def test_update_success_stores_quarters_and_state(isolated_paths, monkeypatch):
    auto_path, state_path = isolated_paths
    monkeypatch.setenv(jq.API_KEY_ENV, "KEY")

    def fake_fetch(api_key, config, *, code=None, day=None):
        if code is not None:  # _refetch_incomplete用: 全期間
            return [_rec(CurPerType="1Q", Sales="100", EPS="10", DiscDate="2025-08-01"),
                    _rec(CurPerType="2Q", Sales="220", EPS="25", DiscDate="2025-11-01")]
        # 日次: 2Q点だけ開示された日がある想定
        return [_rec(CurPerType="2Q", Sales="220", EPS="25", DiscDate="2025-11-01")]

    monkeypatch.setattr(jq, "fetch_summaries", fake_fetch)
    store = jq.update_fundamentals_auto(["7203"], _CFG)

    quarters = store["7203"]["quarters"]
    # _refetch_incompleteが1Q点を取り直しているのでQ2はYTD差分になっている
    assert {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0} in quarters
    assert {"fiscal_quarter": "2025Q2", "eps": 15.0, "revenue": 120.0} in quarters
    assert state_path.exists()
    assert json.loads(auto_path.read_text())["7203"]["quarters"] == quarters
