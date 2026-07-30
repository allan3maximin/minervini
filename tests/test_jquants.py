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
        "ni": None,
        "shares": None,
        "disc_date": "2025-08-01",
    }


def test_record_to_point_net_income_and_shares():
    p = jq.record_to_point(_rec(NP="500000000", ShOutFY="10000000", TrShFY="200000"))
    assert p["ni"] == 500000000.0
    assert p["shares"] == 9800000.0  # 発行済 - 自己株式


def test_record_to_point_shares_none_when_nonpositive():
    # 自己株式が発行済以上 -> shares<=0 は None (異常値ガード)
    p = jq.record_to_point(_rec(NP="100", ShOutFY="1000", TrShFY="1000"))
    assert p["shares"] is None
    assert p["ni"] == 100.0


def test_record_to_point_treasury_missing_defaults_zero():
    p = jq.record_to_point(_rec(NP="100", ShOutFY="1000"))
    assert p["shares"] == 1000.0


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

def _point(n, eps, revenue, fy="2025-04-01", disc="2025-08-01", ni=None, shares=None):
    return {
        "code": "7203",
        "fy_start": fy,
        "n": n,
        "label": f"{fy[:4]}Q{n}",
        "eps": eps,
        "revenue": revenue,
        "ni": ni,
        "shares": shares,
        "disc_date": disc,
    }


def test_derive_quarters_diffs_ytd():
    points = [_point(1, 10.0, 100.0), _point(2, 25.0, 220.0), _point(3, 45.0, 360.0)]
    out = jq.derive_quarters(points)
    assert out == [
        {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0, "disc_date": "2025-08-01"},
        {"fiscal_quarter": "2025Q2", "eps": 15.0, "revenue": 120.0, "disc_date": "2025-08-01"},
        {"fiscal_quarter": "2025Q3", "eps": 20.0, "revenue": 140.0, "disc_date": "2025-08-01"},
    ]


def test_derive_quarters_dedup_keeps_latest_disclosure():
    points = [
        _point(1, 10.0, 100.0, disc="2025-08-01"),
        _point(1, 12.0, 110.0, disc="2025-09-15"),  # 訂正短信が勝つ
    ]
    out = jq.derive_quarters(points)
    assert out == [{"fiscal_quarter": "2025Q1", "eps": 12.0, "revenue": 110.0, "disc_date": "2025-09-15"}]


def test_derive_quarters_separates_fiscal_years():
    points = [
        _point(4, 100.0, 1000.0, fy="2024-04-01"),
        _point(1, 10.0, 100.0, fy="2025-04-01"),
    ]
    out = jq.derive_quarters(points)
    labels = {q["fiscal_quarter"] for q in out}
    # 2026-07-17仕様変更: 年度内にFY(n=4)点しか無い場合、通期値をQ4単四半期として
    # 出力しない(YoY歪み防止)。2024Q4は破棄され、2025Q1のみ出力される。
    assert labels == {"2025Q1"}
    # 年度をまたいで差分しない: 2025Q1は0基準
    q1 = next(q for q in out if q["fiscal_quarter"] == "2025Q1")
    assert q1["eps"] == 10.0


def test_derive_quarters_fy_only_year_emits_nothing():
    # 年度内にFY点しか無い -> 単四半期値が導出不能なので出力ゼロ(通期=Q4登録の禁止)
    out = jq.derive_quarters([_point(4, 100.0, 1000.0, fy="2024-04-01")])
    assert out == []


def test_derive_quarters_gap_mid_year_still_derives_next():
    # {Q2, Q3}のみ: Q2は基準(Q1)欠落で破棄されるが、Q3はQ2との差分で正しく導出できる
    points = [
        _point(2, 20.0, 200.0),
        _point(3, 35.0, 320.0),
    ]
    out = jq.derive_quarters(points)
    assert [q["fiscal_quarter"] for q in out] == ["2025Q3"]
    assert out[0]["eps"] == 15.0
    assert out[0]["revenue"] == 120.0


def test_derive_quarters_none_values_skip_key():
    points = [_point(1, None, 100.0), _point(2, 25.0, None)]
    out = jq.derive_quarters(points)
    assert out[0] == {"fiscal_quarter": "2025Q1", "eps": None, "revenue": 100.0, "disc_date": "2025-08-01"}
    # 前Q点のepsがNoneなら0基準ではなくそのまま(base None -> 0扱い)
    assert out[1]["eps"] == 25.0
    assert out[1]["revenue"] is None


def test_derive_quarters_split_artifact_nulls_eps_keeps_revenue():
    # 期中株式分割の artifact: 通期YTD EPS(分割後) < 9M YTD EPS(分割前) で
    # Q4単独が捏造の深マイナスになる。EPSだけNoneに落とし、revenueは残す。
    points = [
        _point(1, 200.0, 800.0),
        _point(2, 424.72, 1700.0),
        _point(3, 674.72, 2700.0),  # 9M累計EPS(分割前)
        _point(4, 170.28, 4000.0),  # 通期EPS(分割後・黒字)。170.28-674.72=-504.44
    ]
    out = jq.derive_quarters(points)
    q4 = next(q for q in out if q["fiscal_quarter"] == "2025Q4")
    assert q4["eps"] is None
    assert q4["revenue"] == 1300.0  # 4000 - 2700、revenueは正常に導出


def test_derive_quarters_modest_negative_quarter_kept():
    # 通常の小幅赤字四半期は分割artifactではないので残す。
    points = [
        _point(1, 30.0, 800.0),
        _point(2, 65.0, 1700.0),
        _point(3, 95.0, 2700.0),
        _point(4, 90.0, 4000.0),  # 90-95=-5 の小幅赤字。9M=95*0.5=47.5 > 5 で通過
    ]
    out = jq.derive_quarters(points)
    q4 = next(q for q in out if q["fiscal_quarter"] == "2025Q4")
    assert q4["eps"] == -5.0


# ---------------------------------------------------------------------------
# derive_quarters -- 純利益(総額)ベースEPS (分割根治)
# ---------------------------------------------------------------------------

def test_derive_quarters_net_income_based_eps_split_safe():
    # 期中2:1分割。純利益(総額)は連続、会社報告のYTD EPSは分割で歪む。
    # ni/最新株式数(200万)で割り直すと単QEPSは滑らかで捏造マイナスが出ない。
    points = [
        _point(1, 100.0, 800.0, ni=100e6, shares=1_000_000, disc="2025-08-01"),
        _point(2, 210.0, 1700.0, ni=210e6, shares=1_000_000, disc="2025-11-01"),
        _point(3, 330.0, 2700.0, ni=330e6, shares=1_000_000, disc="2026-02-01"),
        # 分割後の通期。会社報告EPS=460M/2M=230 → 差分だと230-330=-100の捏造マイナス
        _point(4, 230.0, 4000.0, ni=460e6, shares=2_000_000, disc="2026-05-01"),
    ]
    out = jq.derive_quarters(points)
    eps_by_q = {q["fiscal_quarter"]: q["eps"] for q in out}
    # 最新株式数200万で全四半期を割り直し: Q1=100M/2M=50 ... Q4=130M/2M=65
    assert eps_by_q == {"2025Q1": 50.0, "2025Q2": 55.0, "2025Q3": 60.0, "2025Q4": 65.0}
    # 単Q純利益(総額)も保存される(merge時の再計算基準)
    ni_by_q = {q["fiscal_quarter"]: q["ni"] for q in out}
    assert ni_by_q["2025Q4"] == 130e6


def test_derive_quarters_falls_back_to_legacy_when_no_ni():
    # NP/株式数が無い銘柄は従来のEPS差分+対症ガードのまま。
    points = [
        _point(1, 200.0, 800.0),
        _point(2, 424.72, 1700.0),
        _point(3, 674.72, 2700.0),
        _point(4, 170.28, 4000.0),  # 分割artifact → ガードでeps=None
    ]
    out = jq.derive_quarters(points)
    q4 = next(q for q in out if q["fiscal_quarter"] == "2025Q4")
    assert q4["eps"] is None
    assert q4["revenue"] == 1300.0


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


def test_merge_into_store_recomputes_eps_on_share_change():
    # 分割で株式数が2倍になると、過去四半期(ni保存済み)のEPSも最新株式数で
    # 割り直される → 分割前後でEPSの基準が揃う。
    store = {}
    early = [
        {"fiscal_quarter": "2025Q1", "eps": 100.0, "revenue": 800.0, "ni": 100e6},
        {"fiscal_quarter": "2025Q2", "eps": 110.0, "revenue": 900.0, "ni": 110e6},
    ]
    jq._merge_into_store(store, "7203", early, "2025-11-01", max_keep=8, shares=1_000_000)
    assert store["7203"]["quarters"][0]["eps"] == 100.0  # 100M / 1M

    # 分割後(株式数200万)の新四半期が到着 → 全四半期を200万で割り直す
    late = [{"fiscal_quarter": "2025Q3", "eps": 60.0, "revenue": 1000.0, "ni": 120e6}]
    jq._merge_into_store(store, "7203", late, "2026-02-01", max_keep=8, shares=2_000_000)
    eps_by_q = {q["fiscal_quarter"]: q["eps"] for q in store["7203"]["quarters"]}
    assert eps_by_q == {"2025Q1": 50.0, "2025Q2": 55.0, "2025Q3": 60.0}
    assert store["7203"]["shares"] == 2_000_000


# ---------------------------------------------------------------------------
# select_missing_codes -- 未取得分だけに絞ってバックフィルする
# ---------------------------------------------------------------------------

def test_select_missing_codes_keeps_only_codes_without_quarters():
    store = {
        "7203": {"quarters": [{"fiscal_quarter": "2025Q1", "eps": 1.0}]},  # 取得済み
        "6758": {"quarters": []},   # 登録はあるが中身が空 -> 未取得扱い
        "9984": {},                 # quartersキー自体が無い -> 未取得扱い
    }
    # 146Aはストアに未登録 -> 未取得扱い。引数の順序は保たれる。
    assert jq.select_missing_codes(["7203", "6758", "146A", "9984"], store) == \
        ["6758", "146A", "9984"]


def test_select_missing_codes_empty_store_returns_all():
    assert jq.select_missing_codes(["7203", "6758"], {}) == ["7203", "6758"]


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
    assert {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0, "disc_date": "2025-08-01"} in quarters
    assert {"fiscal_quarter": "2025Q2", "eps": 15.0, "revenue": 120.0, "disc_date": "2025-11-01"} in quarters
    assert state_path.exists()
    assert json.loads(auto_path.read_text())["7203"]["quarters"] == quarters


# ---------------------------------------------------------------------------
# 会社予想(ガイダンス) -- record_to_guidance / _apply_guidance (2026-07-12追加)
# ---------------------------------------------------------------------------

def _summary_rec(**kw):
    base = {
        "DocType": "2QFinancialStatements_Consolidated_JP",
        "Code": "72030",
        "CurFYSt": "2025-04-01",
        "CurPerType": "2Q",
        "DiscDate": "2025-11-07",
        "EPS": "50.5",
        "Sales": "1000000",
        "FEPS": "120.5",
        "FSales": "2500000",
        "NxFEPS": "",
        "NxFSales": None,
        "ShOutFY": "1000000",
    }
    base.update(kw)
    return base


def test_record_to_guidance_quarterly_statement():
    g = jq.record_to_guidance(_summary_rec())
    assert g == {
        "code": "7203",
        "fy_start": "2025-04-01",
        "per_n": 2,
        "disc_date": "2025-11-07",
        "shares_fy": 1000000.0,
        "feps": 120.5,
        "fsales": 2500000.0,
        "nx_feps": None,
        "nx_fsales": None,
    }


def test_record_to_guidance_accepts_forecast_revision():
    g = jq.record_to_guidance(_summary_rec(
        DocType="EarnForecastRevision", CurPerType="", FEPS="99.0"))
    assert g is not None
    assert g["per_n"] is None
    assert g["feps"] == 99.0


def test_record_to_guidance_rejects_other_doc_types_and_empty_forecasts():
    assert jq.record_to_guidance(_summary_rec(DocType="DividendForecastRevision")) is None
    assert jq.record_to_guidance(_summary_rec(FEPS="", FSales="-", NxFEPS=None, NxFSales="")) is None


def test_apply_guidance_keeps_newest_disclosure():
    store = {}
    jq._apply_guidance(store, {"7203": [
        {"code": "7203", "fy_start": "2025-04-01", "per_n": 1, "disc_date": "2025-08-01",
         "feps": 100.0, "fsales": None, "nx_feps": None, "nx_fsales": None, "shares_fy": None},
        {"code": "7203", "fy_start": "2025-04-01", "per_n": 2, "disc_date": "2025-11-07",
         "feps": 110.0, "fsales": None, "nx_feps": None, "nx_fsales": None, "shares_fy": None},
    ]})
    assert store["7203"]["guidance"]["feps"] == 110.0
    assert "code" not in store["7203"]["guidance"]

    # 古い開示で上書きしない
    jq._apply_guidance(store, {"7203": [
        {"code": "7203", "fy_start": "2025-04-01", "per_n": 1, "disc_date": "2025-08-01",
         "feps": 100.0, "fsales": None, "nx_feps": None, "nx_fsales": None, "shares_fy": None},
    ]})
    assert store["7203"]["guidance"]["feps"] == 110.0


# ---------------------------------------------------------------------------
# 決算発表予定日カレンダー (2026-07-12追加)
# ---------------------------------------------------------------------------

def test_next_dates_from_calendar_filters_and_picks_nearest():
    from datetime import date as _date
    records = [
        {"Code": "72030", "Date": "2026-08-05"},
        {"Code": "72030", "Date": "2026-07-30"},   # 近い方を採用
        {"Code": "72030", "Date": "2026-05-08"},   # 過去 -> 無視
        {"Code": "99999", "Date": "2026-08-01"},   # ユニバース外 -> 無視
        {"Code": "67580", "Date": "bad-date"},     # 不正日付 -> 無視
    ]
    out = jq.next_dates_from_calendar(records, {"7203", "6758"}, _date(2026, 7, 12))
    assert out == {"7203": "2026-07-30"}
