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
# 2026-07-08の実地確認(第6弾): 本番の68フィールド実レコードに基づくケース。
# quarterは整数(FY=4)、fiscal_year_startは無く代わりにfiscal_year_end(期末日)
# のみ存在、disclosure_dateはRFC2822形式。
# ---------------------------------------------------------------------------

def test_record_to_point_real_schema_integer_quarter_and_fiscal_year_end():
    rec = {
        "quarter": 4,
        "eps": 150.93,
        "revenue": 513286,
        "disclosure_date": "Thu, 14 May 2026 00:00:00 GMT",
        "fiscal_year_end": "2026-03-31",
    }
    p = ed.record_to_point(rec, "9024")
    assert p == {
        "code": "9024",
        "fy_start": "2025-04-01",
        "n": 4,
        "label": "2025Q4",
        "eps": 150.93,
        "revenue": 513286 * 1_000_000,
        "disc_date": "2026-05-14",
    }


def test_record_to_point_real_schema_quarter_1_to_3():
    rec = {
        "quarter": 2,
        "eps": 40.0,
        "revenue": 200000,
        "disclosure_date": "Fri, 07 Nov 2025 00:00:00 GMT",
        "fiscal_year_end": "2026-03-31",
    }
    p = ed.record_to_point(rec, "9024")
    assert p["n"] == 2
    assert p["fy_start"] == "2025-04-01"
    assert p["label"] == "2025Q2"
    assert p["disc_date"] == "2025-11-07"


def test_fy_start_from_fy_end_helper():
    assert ed._fy_start_from_fy_end("2026-03-31") == "2025-04-01"
    assert ed._fy_start_from_fy_end("2026-09-30") == "2025-10-01"
    assert ed._fy_start_from_fy_end(None) is None
    assert ed._fy_start_from_fy_end("") is None
    assert ed._fy_start_from_fy_end("not-a-date") is None


def test_parse_disclosure_date_handles_rfc2822_and_iso():
    assert ed._parse_disclosure_date("Thu, 14 May 2026 00:00:00 GMT") == "2026-05-14"
    assert ed._parse_disclosure_date("2026-05-14") == "2026-05-14"
    assert ed._parse_disclosure_date(None) is None
    assert ed._parse_disclosure_date("") is None


def test_resolve_quarter_n_handles_int_and_string():
    assert ed._resolve_quarter_n({"quarter": 4}) == 4
    assert ed._resolve_quarter_n({"quarter": 1}) == 1
    assert ed._resolve_quarter_n({"quarter": "Q3"}) == 3
    assert ed._resolve_quarter_n({"quarter": "FY"}) == 4
    assert ed._resolve_quarter_n({"quarter": 5}) is None
    assert ed._resolve_quarter_n({"quarter": 0}) is None
    assert ed._resolve_quarter_n({"quarter": None}) is None
    assert ed._resolve_quarter_n({}) is None


def test_record_to_point_fiscal_year_end_takes_priority_over_legacy_fy_start_field():
    # fiscal_year_end (実データで確認済み) が有れば、fiscal_year_start (未確認の
    # 旧フォールバック候補) より優先する。
    rec = {
        "quarter": 1,
        "eps": 10.0,
        "revenue": 100.0,
        "disclosure_date": "2026-08-01",
        "fiscal_year_end": "2027-03-31",
        "fiscal_year_start": "1999-01-01",  # 万一存在しても無視されることを確認
    }
    p = ed.record_to_point(rec, "7203")
    assert p["fy_start"] == "2026-04-01"


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


def test_derive_with_base_split_artifact_nulls_eps_keeps_revenue():
    # 6590芝浦の実値: 通期EPS170.28(黒字・増益) − 9M累計674.72 = -504.44。
    # 期中1→5分割で通期は分割後(株数増でEPS縮小)・9Mは分割前 → 捏造の深マイナス。
    # revenueは加法的で正常なので残し、EPSだけNoneに落とす。
    point = {"n": 4, "label": "2026Q4", "eps": 170.28, "revenue": 4000.0}
    base = [_q("2026Q1", 200.0, 800.0), _q("2026Q2", 224.72, 900.0), _q("2026Q3", 250.0, 1000.0)]
    out = ed.derive_with_base(point, base)
    assert out["eps"] is None
    assert out["revenue"] == 1300.0  # 4000 - (800+900+1000)


def test_derive_with_base_modest_negative_quarter_kept():
    # 通常の小幅赤字四半期(分割artifactではない)は破棄せず残す。
    point = {"n": 4, "label": "2026Q4", "eps": 90.0, "revenue": 4000.0}
    base = [_q("2026Q1", 30.0, 800.0), _q("2026Q2", 35.0, 900.0), _q("2026Q3", 30.0, 1000.0)]
    out = ed.derive_with_base(point, base)
    assert out["eps"] == -5.0  # 90 - 95、9M=95*0.5=47.5 > 5 なので通過


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


def test_extract_list_finds_nested_list_in_wrapper_dict(capsys):
    # 2026-07-08の実地確認(第2弾): /earnings の実際の形は "data" 直下が単一
    # レコードではなく {"count":N, "edinet_code":..., "earnings":[...]} という
    # ラッパーdictで、本当のリストはさらに1階層下 (data.earnings) にある。
    body = {
        "data": {"count": 3, "edinet_code": "E01542",
                  "earnings": [{"quarter": "Q1", "eps": 10}, {"quarter": "Q2", "eps": 12}]},
        "meta": {"page": 1},
    }
    out = ed._extract_list_of_dicts(body, ("data", "earnings", "results", "items"), "ctx")
    assert out == [{"quarter": "Q1", "eps": 10}, {"quarter": "Q2", "eps": 12}]
    assert "found nested list at 'data.earnings'" in capsys.readouterr().out


def test_fetch_earnings_finds_nested_list_in_wrapper_dict(monkeypatch):
    monkeypatch.setattr(ed, "_get", lambda *a, **kw: {
        "data": {"count": 1, "edinet_code": "E01542",
                  "earnings": [{"quarter": "Q1", "eps": 10, "revenue": 100,
                                 "disclosure_date": "2026-05-10", "fiscal_year_start": "2026-01-01"}]},
        "meta": {"page": 1},
    })
    recs = ed.fetch_earnings("KEY", {}, "E01542")
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


def test_update_priority_by_code_reorders_backlog_by_rank(isolated_paths, monkeypatch):
    # 2026-07-08追加: priority_by_codeで渡したランク(P1=1〜P4=4)の昇順で
    # backlogを並べ替える。P1が無くてもP2→P3→P4の順で優先されることを確認。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001", "6758": "E002", "9024": "E003"},
        "codemap_date": today,
        "last_events_date": today,  # events窓が空なのでevents呼び出しはスキップ
        "backlog": ["9024", "6758", "7203"],  # 検出順(=ランクとは無関係)
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    cfg = {"edinetdb": {"enabled": True, "requests_per_day": 2, "earnings_limit": 8,
                        "codemap_refresh_days": 30, "max_quarters_keep": 12}}
    # P1が存在しない(9024=P3, 6758=P2, 7203=P4)状態でもP2→P3の順で優先されるはず。
    ed.update_fundamentals_auto(
        ["7203", "6758", "9024"], cfg,
        priority_by_code={"9024": 3, "6758": 2, "7203": 4})

    state = json.loads(state_path.read_text())
    # 予算2本: ランク順(6758=P2, 9024=P3)が先に消化され、最下位の7203(P4)が残る。
    assert state["backlog"] == ["7203"]


def test_update_priority_by_code_unlisted_codes_sort_last(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001", "6758": "E002"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": ["7203", "6758"],  # 7203はランク未指定(=99扱い)、6758はP1
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    cfg = {"edinetdb": {"enabled": True, "requests_per_day": 1, "earnings_limit": 8,
                        "codemap_refresh_days": 30, "max_quarters_keep": 12}}
    ed.update_fundamentals_auto(["7203", "6758"], cfg, priority_by_code={"6758": 1})

    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["7203"]  # ランク未指定の7203が後回しになる


def test_update_priority_by_code_none_leaves_backlog_order_unchanged(isolated_paths, monkeypatch):
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001", "6758": "E002"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": ["7203", "6758"],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    cfg = {"edinetdb": {"enabled": True, "requests_per_day": 1, "earnings_limit": 8,
                        "codemap_refresh_days": 30, "max_quarters_keep": 12}}
    ed.update_fundamentals_auto(["7203", "6758"], cfg)  # priority_by_code未指定

    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["6758"]  # 従来通り検出順(=backlog順)で消化


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
    # 2026-07-30追加のオンボード経路(データ皆無の銘柄をbacklogへ積む)で
    # 7203が積まれ /earnings まで進むため、ネットワークに出ないようモックする。
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])
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
    # codemapが空なので再取得判定が立つ。ネットワークに出ないよう空マップを返す
    # (=9999は依然マップに無い)。
    monkeypatch.setattr(ed, "fetch_companies_map", lambda api_key, config: {})
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


def test_update_prints_sample_when_records_fetched_but_none_usable(isolated_paths, monkeypatch, capsys):
    # 2026-07-08の実地確認(第3弾): /earningsのリスト取り出しは直ったのに
    # data/edinetdb_auto.jsonが0件のまま、という不具合が発生した。record_to_point
    # 内の個別フィールド名(quarter/eps/revenue等)が実レコードと噛み合わないと
    # 黙って0件になる。原因追跡用の診断printが出ることを確認する。
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
        # 未知のフィールド名 (quarterではなくperiod) -- record_to_pointがNoneを返す。
        return [{"period": "Q1", "eps_value": 10.0}]

    monkeypatch.setattr(ed, "fetch_earnings", fake_earnings)
    store = ed.update_fundamentals_auto(["7203"], _CFG, base_store={})

    assert store.get("7203") is None
    out = capsys.readouterr().out
    assert "fetched 1 earnings record(s) but 0 were usable" in out
    # 第6弾でrecord_to_pointの実データ対応は完了したので、診断printは軽量な
    # キー一覧のみに戻した(全フィールドダンプは調査完了に伴い撤去)。
    assert "sample record keys: ['eps_value', 'period']" in out


# ---------------------------------------------------------------------------
# 2026-07-30追加: 候補リストが増えたとき新しい銘柄を取りこぼさない仕組み
#   (1) 証券コード→EDINETコードの対応表を「載っていない銘柄がある」ときに取り直す
#   (2) データが1件も無い銘柄を初回取得の待ち行列に積む
# ---------------------------------------------------------------------------

def test_update_refreshes_codemap_when_universe_code_uncovered(isolated_paths, monkeypatch, capsys):
    # 対応表の取得日が今日(=期限切れではない)でも、候補リストに載っているのに
    # 対応表に無い銘柄があれば取り直す。これが無いと候補リストが増えた週に
    # 追加された銘柄は最大30日間ずっとEDINETコードが引けないままになる。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001"},  # 146Aが無い
        "codemap_date": today,
        "last_events_date": today,
        "backlog": [],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    monkeypatch.setattr(ed, "fetch_companies_map",
                        lambda api_key, config: {"7203": "E001", "146A": "E999"})
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    ed.update_fundamentals_auto(["7203", "146A"], _CFG, base_store={})

    state = json.loads(state_path.read_text())
    assert state["codemap"]["146A"] == "E999"
    assert state["codemap_unmapped"] == []
    assert "missing from codemap" in capsys.readouterr().out


def test_update_does_not_refresh_codemap_for_known_unmapped_codes(isolated_paths, monkeypatch):
    # EDINETに元々存在しない銘柄(ETF等)のために毎日1リクエストを焼き続けないよう、
    # 一度取り直して見つからなかった銘柄は記録しておき、未カバー扱いにしない。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001"},
        "codemap_date": today,
        "codemap_unmapped": ["9999"],  # 前回取り直して見つからなかった
        "last_events_date": today,
        "backlog": [],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    def boom(*a, **kw):
        raise AssertionError("codemap should not be refreshed")

    monkeypatch.setattr(ed, "fetch_companies_map", boom)
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    ed.update_fundamentals_auto(["7203", "9999"], _CFG, base_store={})

    state = json.loads(state_path.read_text())
    assert state["codemap_unmapped"] == ["9999"]  # 記録はそのまま残る


def test_update_codemap_refresh_does_not_record_unmapped_when_map_empty(isolated_paths, monkeypatch):
    # 対応表の取得が0件で返ってきた(APIのフィールド名変更等)ときに
    # 「全銘柄がEDINETに無い」と誤記録すると、以後永久に取り直さなくなる。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": [],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    monkeypatch.setattr(ed, "fetch_companies_map", lambda api_key, config: {})
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])

    ed.update_fundamentals_auto(["7203", "146A"], _CFG, base_store={})

    state = json.loads(state_path.read_text())
    assert "codemap_unmapped" not in state


def test_update_onboards_codes_with_no_fundamentals_at_all(isolated_paths, monkeypatch, capsys):
    # 開示イベント経由でしか待ち行列に積まれないと、既に決算発表を終えている
    # 新規追加銘柄は永久に取得されない。データが皆無の銘柄をここで積む。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"7203": "E001", "6758": "E002"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": [],
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    requested = []

    def fake_earnings(api_key, config, edinet_code):
        requested.append(edinet_code)
        return []

    monkeypatch.setattr(ed, "fetch_earnings", fake_earnings)
    # 7203はJ-Quants側に四半期データがある -> 積まない。6758は皆無 -> 積む。
    base_store = {"7203": {"quarters": [{"fiscal_quarter": "2025Q1", "eps": 1.0}]}}

    ed.update_fundamentals_auto(["7203", "6758"], _CFG, base_store=base_store)

    assert requested == ["E002"]
    state = json.loads(state_path.read_text())
    assert state["onboard_attempts"] == {"6758": 1}
    assert "queued for onboarding" in capsys.readouterr().out


def test_update_onboard_stops_after_max_attempts(isolated_paths, monkeypatch):
    # 取得できても1件も採用できない銘柄を無条件に積み直すと、成果ゼロのまま
    # 毎日1日分の取得枠を焼き続ける。試行回数の上限で打ち切る。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"6758": "E002"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": [],
        "onboard_attempts": {"6758": 3},  # 上限(既定3)に到達済み
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")

    def boom(*a, **kw):
        raise AssertionError("should not retry a code that hit the attempt cap")

    monkeypatch.setattr(ed, "fetch_earnings", boom)

    ed.update_fundamentals_auto(["6758"], _CFG, base_store={})

    state = json.loads(state_path.read_text())
    assert state["backlog"] == []
    assert state["onboard_attempts"] == {"6758": 3}  # 増えない


def test_update_onboard_attempts_cleared_once_data_arrives(isolated_paths, monkeypatch):
    # データが入った銘柄は試行回数を消す(将来また消えたら改めて試行される)。
    store_path, state_path = isolated_paths
    today = date.today().isoformat()
    state_path.write_text(json.dumps({
        "codemap": {"6758": "E002"},
        "codemap_date": today,
        "last_events_date": today,
        "backlog": [],
        "onboard_attempts": {"6758": 2},
    }))
    monkeypatch.setenv(ed.API_KEY_ENV, "KEY")
    monkeypatch.setattr(ed, "fetch_earnings", lambda api_key, config, edinet_code: [])
    base_store = {"6758": {"quarters": [{"fiscal_quarter": "2025Q1", "eps": 1.0}]}}

    ed.update_fundamentals_auto(["6758"], _CFG, base_store=base_store)

    state = json.loads(state_path.read_text())
    assert state["onboard_attempts"] == {}


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


# ---------------------------------------------------------------------------
# requeue_stale -- 成果ゼロのままbacklogから落ちた銘柄のリペア再投入
# (2026-07-08の初回稼働でパース不具合期間に約450銘柄が消化され穴になった件)
# ---------------------------------------------------------------------------

_REQUEUE_CFG = {
    "edinetdb": {"enabled": True},
    "fundamentals": {"stale_days": 120},
}


def test_requeue_stale_queues_old_and_missing_codes(isolated_paths):
    store_path, state_path = isolated_paths
    today = date(2026, 7, 12)
    # 7203: J-Quantsのchecked_dateが2月 (>120日) -> stale
    # 6758: EDINET DB側が5月に取得済み (<120日) -> fresh
    # 9984: どちらのストアにも無い -> stale扱い
    base_store = {
        "7203": {"quarters": [], "checked_date": "2026-02-06"},
        "6758": {"quarters": [], "checked_date": "2026-02-05"},
    }
    store_path.write_text(json.dumps({
        "6758": {"quarters": [], "checked_date": "2026-05-11", "source": "edinetdb"},
    }))
    state_path.write_text(json.dumps({"backlog": [], "last_events_date": "2026-07-11"}))

    requeued = ed.requeue_stale(["7203", "6758", "9984"], _REQUEUE_CFG,
                                base_store=base_store, today=today)
    assert requeued == ["7203", "9984"]
    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["7203", "9984"]
    assert state["last_events_date"] == "2026-07-11"  # 他のstateは保持


def test_requeue_stale_keeps_existing_backlog_and_dedups(isolated_paths):
    store_path, state_path = isolated_paths
    today = date(2026, 7, 12)
    state_path.write_text(json.dumps({"backlog": ["7203"]}))

    requeued = ed.requeue_stale(["7203", "9984"], _REQUEUE_CFG, base_store={}, today=today)
    assert requeued == ["9984"]  # 7203は既にbacklogに居るので二重投入しない
    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["7203", "9984"]


def test_requeue_stale_uses_newest_checked_date_across_stores(isolated_paths):
    store_path, state_path = isolated_paths
    today = date(2026, 7, 12)
    # J-Quants側は古いがEDINET DB側が新しい -> fresh (最新の方で判定)
    base_store = {"7203": {"quarters": [], "checked_date": "2026-02-06"}}
    store_path.write_text(json.dumps({
        "7203": {"quarters": [], "checked_date": "2026-06-30", "source": "edinetdb"},
    }))

    assert ed.requeue_stale(["7203"], _REQUEUE_CFG, base_store=base_store, today=today) == []


def test_requeue_stale_threshold_override(isolated_paths):
    store_path, state_path = isolated_paths
    today = date(2026, 7, 12)
    base_store = {"7203": {"quarters": [], "checked_date": "2026-06-30"}}  # 12日前

    assert ed.requeue_stale(["7203"], _REQUEUE_CFG, base_store=base_store,
                            stale_days=10, today=today) == ["7203"]
    state = json.loads(state_path.read_text())
    assert state["backlog"] == ["7203"]
