import json
from datetime import date

import pytest

import src.data.edinetdb as ed


def _rec(**kw):
    base = {
        "quarter": "Q1",
        "eps": 50.5,
        "revenue": 1000.0,
        "disclosure_date": "2025-08-01",
        "fiscal_year_start": "2025-04-01",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# record_to_point
# ---------------------------------------------------------------------------

def test_record_to_point_basic():
    p = ed.record_to_point(_rec(), "7203")
    assert p == {
        "code": "7203",
        "fy_start": "2025-04-01",
        "n": 1,
        "label": "2025Q1",
        "eps": 50.5,
        "revenue": 1000.0 * 1_000_000,
        "disc_date": "2025-08-01",
    }


def test_record_to_point_rejects_unknown_quarter():
    assert ed.record_to_point(_rec(quarter="Q5"), "7203") is None
    assert ed.record_to_point(_rec(quarter=""), "7203") is None


def test_record_to_point_rejects_when_no_values():
    assert ed.record_to_point(_rec(eps=None, revenue=None), "7203") is None


def test_record_to_point_revenue_million_to_yen():
    p = ed.record_to_point(_rec(eps=None, revenue=12.5), "7203")
    assert p["revenue"] == 12.5 * 1_000_000
    assert p["eps"] is None


def test_record_to_point_fy_start_from_explicit_field():
    p = ed.record_to_point(_rec(quarter="Q3", fiscal_year_start="2025-04-01"), "7203")
    assert p["fy_start"] == "2025-04-01"
    assert p["label"] == "2025Q3"


def test_record_to_point_fy_start_estimated_when_field_missing():
    # 3月決算(fy_start月=4)のQ3: 期末2025-12-01の約45日後に開示。
    rec = {
        "quarter": "Q3",
        "eps": 100.0,
        "revenue": 200.0,
        "disclosure_date": "2026-01-15",
    }
    p = ed.record_to_point(rec, "7203", fiscal_year_end_month=3)
    assert p["fy_start"] == "2025-04-01"
    assert p["label"] == "2025Q3"


def test_record_to_point_none_when_fy_start_unresolvable():
    rec = {"quarter": "Q3", "eps": 100.0, "revenue": 200.0, "disclosure_date": "2026-01-15"}
    assert ed.record_to_point(rec, "7203") is None  # fiscal_year_end_month無し、fy fieldも無し


# ---------------------------------------------------------------------------
# derive_with_base
# ---------------------------------------------------------------------------

def _q(label, eps, revenue):
    return {"fiscal_quarter": label, "eps": eps, "revenue": revenue}


def test_derive_with_base_q1_is_ytd_as_is():
    point = {"n": 1, "label": "2025Q1", "eps": 10.0, "revenue": 100.0}
    out = ed.derive_with_base(point, [])
    assert out == {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0}


def test_derive_with_base_q3_diffs_against_complete_base():
    point = {"n": 3, "label": "2025Q3", "eps": 45.0, "revenue": 450.0}
    base = [_q("2025Q1", 10.0, 100.0), _q("2025Q2", 15.0, 150.0)]
    out = ed.derive_with_base(point, base)
    assert out == {"fiscal_quarter": "2025Q3", "eps": 20.0, "revenue": 200.0}


def test_derive_with_base_missing_prior_quarter_drops_record():
    point = {"n": 3, "label": "2025Q3", "eps": 45.0, "revenue": 450.0}
    base = [_q("2025Q1", 10.0, 100.0)]  # Q2が丸ごと欠けている
    assert ed.derive_with_base(point, base) is None


def test_derive_with_base_partial_field_completeness():
    point = {"n": 3, "label": "2025Q3", "eps": 45.0, "revenue": 450.0}
    base = [
        _q("2025Q1", 10.0, 100.0),
        {"fiscal_quarter": "2025Q2", "eps": 15.0, "revenue": None},  # revenueだけ欠け
    ]
    out = ed.derive_with_base(point, base)
    assert out == {"fiscal_quarter": "2025Q3", "eps": 20.0, "revenue": None}


# ---------------------------------------------------------------------------
# _extract_list_of_dicts / fetch_companies_map / fetch_events -- 未知スキーマ耐性
# (2026-07-08の初回稼働で既知キー名が外れて0件になった不具合の再発防止)
# ---------------------------------------------------------------------------

def test_extract_list_known_key():
    body = {"data": [{"a": 1}]}
    assert ed._extract_list_of_dicts(body, ("data", "events"), "ctx") == [{"a": 1}]


def test_extract_list_bare_list_body():
    body = [{"a": 1}]
    assert ed._extract_list_of_dicts(body, ("data",), "ctx") == [{"a": 1}]


def test_extract_list_falls_back_to_auto_detected_key(capsys):
    body = {"meta": {"count": 1}, "results": [{"a": 1}]}
    out = ed._extract_list_of_dicts(body, ("data", "events"), "ctx")
    assert out == [{"a": 1}]
    assert "auto-detected key 'results'" in capsys.readouterr().out


def test_extract_list_returns_empty_when_nothing_matches(capsys):
    body = {"meta": {"count": 0}}
    out = ed._extract_list_of_dicts(body, ("data", "events"), "ctx")
    assert out == []
    assert "no list field at all" in capsys.readouterr().out


def test_fetch_companies_map_uses_fallback_key(monkeypatch):
    # /companies の実レスポンスがdata/companies以外のキーで返ってきても拾える。
    monkeypatch.setattr(ed, "_get", lambda *a, **kw: {
        "results": [{"security_code": "72030", "edinet_code": "E02144"}]
    })
    result = ed.fetch_companies_map("KEY", {})
    assert result == {"7203": "E02144"}


def test_fetch_events_uses_fallback_key(monkeypatch):
    monkeypatch.setattr(ed, "_get", lambda *a, **kw: {
        "disclosures": [{"security_code": "72030"}]
    })
    events = ed.fetch_events("KEY", {}, date(2026, 1, 1), date(2026, 7, 8))
    assert events == [{"security_code": "72030"}]


def test_extract_list_wraps_single_dict_record(capsys):
    # 2026-07-08の実地確認で判明: /earnings は "data" 直下がlistではなく単一
    # レコードのdictで返ってくることがある。
    body = {"data": {"quarter": "Q1", "eps": 10}, "meta": {"page": 1}}
    out = ed._extract_list_of_dicts(body, ("data", "earnings"), "ctx")
    assert out == [{"quarter": "Q1", "eps": 10}]
    assert "was a single dict, not a list" in capsys.readouterr().out


def test_fetch_earnings_wraps_single_dict_record(monkeypatch):
    monkeypatch.setattr(ed, "_get", lambda *a, **kw: {
        "data": {"quarter": "Q1", "eps": 10, "revenue": 100,
                  "disclosure_date": "2026-05-10", "fiscal_year_start": "2026-01-01"},
        "meta": {"page": 1},
    })
    recs = ed.fetch_earnings("KEY", {}, "E02144")
    assert recs == [{"quarter": "Q1", "eps": 10, "revenue": 100,
                      "disclosure_date": "2026-05-10", "fiscal_year_start": "2026-01-01"}]


# ---------------------------------------------------------------------------
# update_fundamentals_auto
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    store_path = tmp_path / "edinetdb_auto.json"
    state_path = tmp_path / "edinetdb_state.json"
    monkeypatch.setattr(ed, "STORE_PATH", store_path)
    monkeypatch.setattr(ed, "STATE_PATH", state_path)
    monkeypatch.setattr(ed.time, "sleep", lambda s: None)
    return store_path, state_path


_CFG = {"edinetdb": {"enabled": True, "requests_per_day": 90, "earnings_limit": 8,
                     "codemap_refresh_days": 30, "max_quarters_keep": 12}}


def test_update_without_key_returns_existing_store(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    store_path.write_text(json.dumps({"7203": {"quarters": [], "checked_date": None, "source": "edinetdb"}}))
    monkeypatch.delenv(ed.API_KEY_ENV, raising=False)

    store = ed.update_fundamentals_auto(["7203"], _CFG)
    assert "7203" in store
    assert not state_path.exists()  # ネットワークに出ていない


def test_update_disabled_returns_existing_store_even_with_key(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    cfg = {"edinetdb": {"enabled": False}}

    store = ed.update_fundamentals_auto(["7203"], cfg)
    assert store == {}
    assert not state_path.exists()


def test_update_budget_exceeded_leaves_backlog(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001", "6758": "E002"},
        "codemap_date": today,
        "last_events_date": today,  # events窓が空なのでevents呼び出しはスキップ
        "backlog": ["7203", "6758"],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    def boom(*a, **kw):
        raise AssertionError("should not be called")

    monkeypatch.setattr(ed, "fetch_companies_map", boom)
    monkeypatch.setattr(ed, "fetch_events", boom)
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    cfg = {"edinetdb": {"enabled": True, "requests_per_day": 1, "earnings_limit": 8,
                        "codemap_refresh_days": 30, "max_quarters_keep": 12}}
    ed.update_fundamentals_auto(["7203", "6758"], cfg)

    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["6758"]  # budget1本目で消化、2本目は持ち越し


def test_update_events_all_fail_does_not_advance_last_events_date(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001"},
        "codemap_date": today,
        "backlog": [],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    def boom_events(*a, **kw):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(ed, "fetch_events", boom_events)
    ed.update_fundamentals_auto(["7203"], _CFG)

    state = json.loads(state_path.read_text())
    assert "last_events_date" not in state or state.get("last_events_date") is None


def test_update_code_missing_from_codemap_stays_in_backlog(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {},  # 9999はマップに無い(新規上場等)
        "codemap_date": today,
        "last_events_date": today,
        "backlog": ["9999"],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    def boom(*a, **kw):
        raise AssertionError("fetch_earnings should not be called for unmapped code")

    monkeypatch.setattr(ed, "fetch_earnings", boom)
    ed.update_fundamentals_auto(["9999"], _CFG)

    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["9999"]  # 次回codemap更新まで持ち越し


def test_update_success_stores_derived_quarter(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E02144"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": ["7203"],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    def fake_earnings(api_key, config, edinet_code):
        assert edinet_code == "E02144"
        return [{
            "quarter": "Q1",
            "eps": 10.0,
            "revenue": 100.0,
            "disclosure_date": "2026-02-06",
            "fiscal_year_start": "2025-04-01",
        }]

    monkeypatch.setattr(ed, "fetch_earnings", fake_earnings)
    base_store = {}  # Q1はbase不要
    store = ed.update_fundamentals_auto(["7203"], _CFG, base_store=base_store)

    quarters = store["7203"]["quarters"]
    assert {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0 * 1_000_000} in quarters
    assert store["7203"]["source"] == "edinetdb"
    assert store["7203"]["checked_date"] == "2026-02-06"
    assert json.loads(store_path.read_text())["7203"]["quarters"] == quarters


# ---------------------------------------------------------------------------
# state / store persistence round-trip
# ---------------------------------------------------------------------------

def test_state_store_roundtrip(tmp_path):
    state_path = tmp_path / "state.json"
    store_path = tmp_path / "store.json"

    state = {"last_events_date": "2026-07-04", "codemap": {"7203": "E02144"}, "backlog": []}
    ed.save_state(state, path=state_path)
    assert ed.load_state(path=state_path) == state

    store = {"7203": {"quarters": [{"fiscal_quarter": "2025Q1", "eps": 1.0, "revenue": 2.0}],
                       "checked_date": "2026-02-06", "source": "edinetdb"}}
    ed.save_store(store, path=store_path)
    assert ed.load_store(path=store_path) == store


def test_load_state_missing_file_returns_empty(tmp_path):
    assert ed.load_state(path=tmp_path / "missing.json") == {}


def test_load_store_missing_file_returns_empty(tmp_path):
    assert ed.load_store(path=tmp_path / "missing.json") == {}
