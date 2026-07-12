"""J-Quants API v2 automatic fundamentals (EPS / revenue) fetcher.

Flow:
  - Daily incremental (runs inside the daily pipeline): fetch 決算短信サマリー
    (``GET /v2/fins/summary?date=YYYY-MM-DD``) day-by-day since the last
    processed date, keep universe codes, and update the auto store.
  - Backfill (``python -m src.data.jquants --backfill``): one request per
    universe code (``?code=XXXX`` returns the code's full history) to build
    the store from scratch. ~1000 codes at 60 req/min ≒ 17 min.

Values in /fins/summary are YTD cumulative (決算短信サマリーそのまま), so
per-quarter values are derived by diffing YTD points within a fiscal year --
the same regime the EDINET fetcher used. Post-2024 tanshin still comes out
quarterly (1Q/3Q included), which is exactly why J-Quants replaced EDINET here.

Auth: env JQUANTS_API_KEY sent as the ``x-api-key`` header. No key -> no
network access at all; the previously stored auto data is still used.
Manual CSV rows always win over auto rows for the same (code,
fiscal_quarter) -- see fundamentals.merge_fundamentals.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta

import requests

from src.config import REPO_ROOT, load_config
from src.data.fundamentals import AUTO_PATH, load_auto_store

STATE_PATH = REPO_ROOT / "data" / "jquants_state.json"
CALENDAR_PATH = REPO_ROOT / "data" / "earnings_calendar.json"

API_KEY_ENV = "JQUANTS_API_KEY"
DEFAULT_API_URL = "https://api.jquants.com/v2"

# 当会計期間種類 -> 年度内の四半期番号。5Q(変則決算)は対象外。
_PERIOD_TO_N = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4, "FY": 4}


def _jq_cfg(config: dict) -> dict:
    return config.get("jquants", {}) or {}


def save_auto_store(store: dict, path=None) -> None:
    path = path or AUTO_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1, sort_keys=True)


def load_state(path=None) -> dict:
    path = path or STATE_PATH
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, path=None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

def fetch_summaries(api_key: str, config: dict, *, code: str | None = None,
                    day: date | None = None) -> list[dict]:
    """/fins/summary を叩いて全ページ分のレコードを返す。

    code指定: その銘柄の全期間分。day指定: その開示日の全銘柄分。
    """
    api_url = _jq_cfg(config).get("api_url", DEFAULT_API_URL)
    params: dict = {}
    if code is not None:
        params["code"] = code
    if day is not None:
        params["date"] = day.isoformat()

    records: list[dict] = []
    while True:
        resp = requests.get(
            f"{api_url}/fins/summary",
            params=params,
            headers={"x-api-key": api_key},
            timeout=60,
        )
        if resp.status_code == 429:
            # レートリミット。少し待って同じページを再試行(1回だけ粘る)。
            time.sleep(30)
            resp = requests.get(
                f"{api_url}/fins/summary",
                params=params,
                headers={"x-api-key": api_key},
                timeout=60,
            )
        resp.raise_for_status()
        body = resp.json()
        records.extend(body.get("data") or [])
        pk = body.get("pagination_key")
        if not pk:
            return records
        params["pagination_key"] = pk


# ---------------------------------------------------------------------------
# Record -> YTD point
# ---------------------------------------------------------------------------

def _num(raw) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s in {"-", "－", "―"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def record_to_point(rec: dict) -> dict | None:
    """/fins/summary の1レコードをYTD点に変換する。対象外はNone。

    戻り値: {"code", "fy_start", "n", "label", "eps", "revenue", "disc_date"}
    (eps/revenueはYTD累計。連結が空なら非連結にフォールバック。)
    """
    doc_type = rec.get("DocType") or ""
    if "FinancialStatements" not in doc_type:
        return None  # 業績予想修正・配当予想修正などは対象外
    n = _PERIOD_TO_N.get(rec.get("CurPerType") or "")
    if n is None:
        return None
    code5 = (rec.get("Code") or "").strip()
    fy_start = (rec.get("CurFYSt") or "").strip()
    if len(code5) < 4 or len(fy_start) < 10:
        return None

    eps = _num(rec.get("EPS"))
    if eps is None:
        eps = _num(rec.get("NCEPS"))
    revenue = _num(rec.get("Sales"))
    if revenue is None:
        revenue = _num(rec.get("NCSales"))
    if eps is None and revenue is None:
        return None

    fy_year = fy_start[:4]
    return {
        "code": code5[:4],
        "fy_start": fy_start,
        "n": n,
        "label": f"{fy_year}Q{n}",
        "eps": eps,
        "revenue": revenue,
        "disc_date": (rec.get("DiscDate") or "").strip(),
    }


def record_to_guidance(rec: dict) -> dict | None:
    """/fins/summary の1レコードから会社予想(ガイダンス)を取り出す。

    決算短信(FinancialStatements)に加えて業績予想修正(ForecastRevision)も
    対象にする(四半期の間の上方/下方修正を取りこぼさないため)。
    フィールドはJ-Quants v2仕様 (jpx-jquants.com/ja/spec/fin-summary):
      FEPS/FSales   = 当期の通期予想EPS/売上
      NxFEPS/NxFSales = 翌事業年度の通期予想 (本決算短信に載る来期計画)
      ShOutFY       = 期末発行済株式数
    予想値が1つも無ければNone。
    """
    doc_type = rec.get("DocType") or ""
    is_statement = "FinancialStatements" in doc_type
    # 配当予想修正(DividendForecastRevision)は業績予想を含まないため除外する
    # ("ForecastRevision"の部分一致だけだと誤って通ってしまう)。
    is_earn_revision = "ForecastRevision" in doc_type and "Dividend" not in doc_type
    if not is_statement and not is_earn_revision:
        return None
    code5 = (rec.get("Code") or "").strip()
    fy_start = (rec.get("CurFYSt") or "").strip()
    if len(code5) < 4 or len(fy_start) < 10:
        return None

    values = {
        "feps": _num(rec.get("FEPS")),
        "fsales": _num(rec.get("FSales")),
        "nx_feps": _num(rec.get("NxFEPS")),
        "nx_fsales": _num(rec.get("NxFSales")),
    }
    if all(v is None for v in values.values()):
        return None

    return {
        "code": code5[:4],
        "fy_start": fy_start,
        "per_n": _PERIOD_TO_N.get(rec.get("CurPerType") or ""),  # 修正開示ではNoneあり得る
        "disc_date": (rec.get("DiscDate") or "").strip(),
        "shares_fy": _num(rec.get("ShOutFY")),
        **values,
    }


# ---------------------------------------------------------------------------
# Quarter derivation (YTD差分) -- EDINET版と同一ロジック
# ---------------------------------------------------------------------------

def derive_quarters(ytd_points: list[dict]) -> list[dict]:
    """会計年度ごとにYTD点を昇順に並べ、隣接差分でラベル値を導出する。"""
    by_fy: dict[str, list[dict]] = {}
    for p in ytd_points:
        by_fy.setdefault(p["fy_start"], []).append(p)

    out: list[dict] = []
    for fy_points in by_fy.values():
        # 同一四半期が複数ある場合(訂正短信等)は開示日が新しい方を採用。
        fy_points = sorted(fy_points, key=lambda p: (p["n"], p.get("disc_date") or ""))
        dedup: dict[int, dict] = {}
        for p in fy_points:
            dedup[p["n"]] = p
        prev: dict | None = None
        for n in sorted(dedup):
            p = dedup[n]
            rec = {"fiscal_quarter": p["label"], "eps": None, "revenue": None}
            for key in ("eps", "revenue"):
                cur = p.get(key)
                if cur is None:
                    continue
                base = prev.get(key) if prev is not None else 0.0
                rec[key] = round(cur - (base if base is not None else 0.0), 4)
            if rec["eps"] is not None or rec["revenue"] is not None:
                out.append(rec)
            prev = p
    return out


def _merge_into_store(store: dict, code: str, quarters: list[dict], checked_date: str, max_keep: int) -> None:
    entry = store.setdefault(code, {"quarters": [], "checked_date": None, "source": "jquants"})
    entry["source"] = "jquants"
    by_label = {q["fiscal_quarter"]: q for q in entry["quarters"]}
    for q in quarters:
        by_label[q["fiscal_quarter"]] = q  # 新しい開示が同ラベルを上書き
    merged = sorted(by_label.values(), key=lambda q: (q["fiscal_quarter"][:4], q["fiscal_quarter"][-1]))
    entry["quarters"] = merged[-max_keep:]
    if entry["checked_date"] is None or (checked_date and checked_date > entry["checked_date"]):
        entry["checked_date"] = checked_date


def _apply_points(store: dict, points_by_code: dict[str, list[dict]], max_keep: int) -> None:
    for code, points in points_by_code.items():
        quarters = derive_quarters(points)
        if not quarters:
            continue
        checked = max((p.get("disc_date") or "" for p in points), default="")
        _merge_into_store(store, code, quarters, checked, max_keep)


def _apply_guidance(store: dict, guidance_by_code: dict[str, list[dict]]) -> None:
    """銘柄ごとに開示日が最新のガイダンスをストアentryの"guidance"に格納する。

    既存guidanceより古い開示は上書きしない(増分取得の日順は保証されないため)。
    """
    for code, cands in guidance_by_code.items():
        latest = max(cands, key=lambda g: g.get("disc_date") or "")
        entry = store.setdefault(code, {"quarters": [], "checked_date": None, "source": "jquants"})
        cur = entry.get("guidance")
        if cur and (cur.get("disc_date") or "") >= (latest.get("disc_date") or ""):
            continue
        entry["guidance"] = {k: v for k, v in latest.items() if k != "code"}


# ---------------------------------------------------------------------------
# 決算発表予定日 (/equities/earnings-calendar)
# ---------------------------------------------------------------------------

def fetch_earnings_calendar(api_key: str, config: dict) -> list[dict]:
    """GET /equities/earnings-calendar (全ページ)。Freeプランで利用可。

    レスポンスは {Date(発表予定日), Code, CoName, FY, FQ, Section, ...} の配列
    (jpx-jquants.com/ja/spec/eq-earnings-cal)。3月期・9月期決算企業のみ対象、
    という提供側の制約があるため、載っていない銘柄は呼び出し側で
    「前回開示からの経過日数」推定にフォールバックする。
    """
    api_url = _jq_cfg(config).get("api_url", DEFAULT_API_URL)
    params: dict = {}
    records: list[dict] = []
    while True:
        resp = requests.get(
            f"{api_url}/equities/earnings-calendar",
            params=params,
            headers={"x-api-key": api_key},
            timeout=60,
        )
        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.get(
                f"{api_url}/equities/earnings-calendar",
                params=params,
                headers={"x-api-key": api_key},
                timeout=60,
            )
        resp.raise_for_status()
        body = resp.json()
        records.extend(body.get("data") or [])
        pk = body.get("pagination_key")
        if not pk:
            return records
        params["pagination_key"] = pk


def next_dates_from_calendar(records: list[dict], code_set: set[str], today: date) -> dict[str, str]:
    """カレンダーレコードから {4桁コード: 直近の今日以降の発表予定日} を作る。"""
    by_code: dict[str, str] = {}
    for rec in records:
        code5 = str(rec.get("Code") or "").strip()
        code = code5[:4] if len(code5) >= 4 else code5
        d = str(rec.get("Date") or "").strip()[:10]
        if code not in code_set or len(d) != 10:
            continue
        try:
            if date.fromisoformat(d) < today:
                continue
        except ValueError:
            continue
        if code not in by_code or d < by_code[code]:
            by_code[code] = d
    return by_code


def load_earnings_calendar(path=None) -> dict:
    path = path or CALENDAR_PATH
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def update_earnings_calendar(codes: list[str], config: dict | None = None) -> dict[str, str]:
    """決算発表予定日を取得して data/earnings_calendar.json に保存する(日次1〜数req)。

    APIキー無し・取得失敗時は前回保存分をそのまま返す(フェイルセーフ)。
    戻り値: {code: "YYYY-MM-DD"} (今日以降の直近予定のみ)。
    """
    config = config or load_config()
    cached = load_earnings_calendar()
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key or not _jq_cfg(config).get("enabled", True):
        return cached.get("by_code") or {}

    today = datetime.now().date()
    try:
        records = fetch_earnings_calendar(api_key, config)
    except Exception as e:
        print(f"J-Quants earnings calendar fetch failed (kept cache): {e}")
        return cached.get("by_code") or {}

    by_code = next_dates_from_calendar(records, set(codes), today)
    payload = {"fetched_at": today.isoformat(), "by_code": by_code}
    CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CALENDAR_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"J-Quants earnings calendar: {len(by_code)} upcoming dates for universe codes.")
    return by_code


# ---------------------------------------------------------------------------
# Update entrypoints
# ---------------------------------------------------------------------------

def update_fundamentals_auto(codes: list[str], config: dict | None = None) -> dict:
    """日次インクリメンタル取得。APIキーが無ければ既存ストアを返すだけ。"""
    config = config or load_config()
    cfg = _jq_cfg(config)
    store = load_auto_store()

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key or not cfg.get("enabled", True):
        return store

    state = load_state()
    # Freeプランはデータが12週間遅延で提供される。遅延分より新しい日付を
    # 問い合わせても空が返るだけなので、取得対象は end_day までに留めて
    # state もそこまでしか進めない(でないと遅延データを永久に取りこぼす)。
    delay = cfg.get("data_delay_days", 0)
    end_day = datetime.now().date() - timedelta(days=delay)
    lookback = cfg.get("lookback_days", 7)
    start = end_day - timedelta(days=lookback)
    if state.get("last_list_date"):
        resume = date.fromisoformat(state["last_list_date"]) + timedelta(days=1)
        start = max(start, min(resume, end_day))
    if start > end_day:
        return store  # 進める日が無い(前回実行から1日未満など)

    sleep_sec = cfg.get("sleep_sec", 1.1)
    max_keep = cfg.get("max_quarters_keep", 12)
    code_set = set(codes)

    points_by_code: dict[str, list[dict]] = {}
    guidance_by_code: dict[str, list[dict]] = {}
    ok = fail = n_recs = 0
    day = start
    while day <= end_day:
        try:
            recs = fetch_summaries(api_key, config, day=day)
            ok += 1
        except Exception as e:
            print(f"J-Quants summary {day} failed (skipped): {e}")
            fail += 1
            day += timedelta(days=1)
            continue
        for rec in recs:
            g = record_to_guidance(rec)
            if g is not None and g["code"] in code_set:
                guidance_by_code.setdefault(g["code"], []).append(g)
            point = record_to_point(rec)
            if point is None or point["code"] not in code_set:
                continue
            points_by_code.setdefault(point["code"], []).append(point)
            n_recs += 1
        time.sleep(sleep_sec)
        day += timedelta(days=1)

    # 差分導出はYTDの前Q点が必要なので、既存ストアに無い年度途中の点は
    # code単位で全期間を取り直して整合させる(発生時のみ、少数)。
    _refetch_incomplete(points_by_code, store, api_key, config, sleep_sec)

    _apply_points(store, points_by_code, max_keep)
    _apply_guidance(store, guidance_by_code)
    save_auto_store(store)

    if fail > 0 and ok == 0:
        # 全日failed(キー不正など)ならstateを進めない。進めると次回実行が
        # その期間を永久にスキップしてしまうため。
        print(f"J-Quants: all {fail} requests failed; state not advanced (check {API_KEY_ENV}).")
        return store
    state["last_list_date"] = end_day.isoformat()
    save_state(state)
    print(f"J-Quants: {n_recs} records processed, {len(points_by_code)} codes updated.")
    return store


def _refetch_incomplete(points_by_code: dict[str, list[dict]], store: dict,
                        api_key: str, config: dict, sleep_sec: float) -> None:
    """2Q以降の点だけ拾った銘柄は、年度前半のYTD点をcode指定で取り直す。

    直近開示だけだとYTD差分の基準(前Q累計)が無く、値がYTDのまま
    誤登録されるのを防ぐ。失敗しても該当銘柄をスキップするだけ。
    """
    for code, points in list(points_by_code.items()):
        needs = any(
            p["n"] >= 2 and not any(
                q["n"] == p["n"] - 1 and q["fy_start"] == p["fy_start"] for q in points
            )
            for p in points
        )
        if not needs:
            continue
        try:
            recs = fetch_summaries(api_key, config, code=code)
        except Exception as e:
            print(f"J-Quants refetch {code} failed (skipped): {e}")
            del points_by_code[code]
            continue
        full = [p for rec in recs if (p := record_to_point(rec)) is not None and p["code"] == code]
        if full:
            points_by_code[code] = full
        time.sleep(sleep_sec)


def backfill_all(codes: list[str], config: dict | None = None) -> dict:
    """全銘柄をcode指定で1件ずつ全期間取得してストアを構築する。"""
    config = config or load_config()
    cfg = _jq_cfg(config)
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise SystemExit(f"env {API_KEY_ENV} is required for backfill")

    sleep_sec = cfg.get("sleep_sec", 1.1)
    max_keep = cfg.get("max_quarters_keep", 12)
    store = load_auto_store()

    n_ok = n_fail = 0
    for i, code in enumerate(codes, 1):
        try:
            recs = fetch_summaries(api_key, config, code=code)
        except Exception as e:
            print(f"J-Quants backfill {code} failed (skipped): {e}")
            n_fail += 1
            time.sleep(sleep_sec)
            continue
        guidance = [g for rec in recs if (g := record_to_guidance(rec)) is not None and g["code"] == code]
        if guidance:
            _apply_guidance(store, {code: guidance})
        points = [p for rec in recs if (p := record_to_point(rec)) is not None]
        if points:
            _apply_points(store, {code: points}, max_keep)
            n_ok += 1
        if i % 50 == 0:
            save_auto_store(store)  # 中間セーブ(途中失敗しても積み上げは残す)
            print(f"J-Quants backfill progress: {i}/{len(codes)}")
        time.sleep(sleep_sec)

    save_auto_store(store)
    if n_ok > 0:
        state = load_state()
        delay = cfg.get("data_delay_days", 0)
        state["last_list_date"] = (datetime.now().date() - timedelta(days=delay)).isoformat()
        save_state(state)
    print(f"J-Quants backfill done: {n_ok} codes stored, {n_fail} failed.")
    return store


def main() -> None:
    parser = argparse.ArgumentParser(description="J-Quants fundamentals fetcher")
    parser.add_argument("--backfill", action="store_true", help="全銘柄の全期間を取得してストアを構築")
    args = parser.parse_args()
    from src.universe import load_universe

    config = load_config()
    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    if args.backfill:
        backfill_all(codes, config)
    else:
        update_fundamentals_auto(codes, config)


if __name__ == "__main__":
    main()
