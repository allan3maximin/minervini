"""EDINET DB (edinetdb.jp) 決算短信フェッチャー -- J-Quants Freeプランの12週間
遅延窓を、開示後ほぼ即日〜翌日で提供される決算短信データで補完する。

役割分担 (DESIGN_EDINETDB.md 0節、案A採用):
  manual/fundamentals.csv (最強) > data/fundamentals_auto.json (J-Quants, 中) >
  data/edinetdb_auto.json (本モジュール, 最弱)。同一(code, fiscal_quarter)は
  J-Quantsの遅延が追いつけば自然に上書きされる(EDINET DB値は暫定速報扱い)。

仕組み:
  1. codemap: 証券コード -> EDINETコード のマップを `/companies` から構築
     (config.edinetdb.codemap_refresh_days ごとに再取得)。
  2. events: `/events?event_type=earnings_summary` で当日開示銘柄を検出し、
     backlog キューに積む (全銘柄ポーリング不要)。
  3. backlog消化: 1日あたり requests_per_day 予算の範囲で `/earnings` を叩き、
     YTD点に変換 -> J-Quantsストア(base_store)+自ストアの確定四半期を基準に
     単四半期値へ差分導出 -> ストアへマージ。

Free プランはデータが2026年1月以降の開示しか無い(バックフィル不可)。決算
集中日は backlog が積み上がり、数日〜数週かけて消化される設計。

Auth: 環境変数 EDINETDB_API_KEY を X-API-Key ヘッダで送る。キーが無い、または
config.edinetdb.enabled が false ならネットワークに一切触れず既存ストアを返す
だけ (jquants.py と同じ設計)。

**実地確認ステータス** (2026-07-08、本番運用で段階的に検証・修正済み):
  - `/companies` の証券コード/EDINETコードのフィールド名 -> 確認済み
    (`security_code`/`edinet_code`)。
  - `/earnings` のフルスキーマ(68フィールド) -> 確認済み。会計年度は
    `fiscal_year_end`(期末日)のみ実在、fy_start相当のフィールドは無いため
    期末日から1年分逆算する(`_fy_start_from_fy_end`)。`quarter`は整数1〜4
    (FY=4相当)。`disclosure_date`はRFC2822形式。
  - revenue の単位換算(百万円->円の ×1_000_000) -> 確認済み(9024の通期
    revenue=513286と実際の決算短信の整合を確認)。
  - `/events` のレスポンス形式 -> 確認済み(`_extract_list_of_dicts`の
    フォールバックで自動吸収)。
  - 429/枠超過時の挙動 -> 本番で429発生を確認、既存の30秒待ち1回リトライで
    運用上問題なし(バックログに残して翌日に持ち越す設計のため取りこぼしなし)。
詳細な経緯は log.md および HANDOFF.md「実地確認について」参照。
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import requests

from src.config import REPO_ROOT, load_config
from src.data.fundamentals import load_auto_store

STATE_PATH = REPO_ROOT / "data" / "edinetdb_state.json"
STORE_PATH = REPO_ROOT / "data" / "edinetdb_auto.json"

API_KEY_ENV = "EDINETDB_API_KEY"
DEFAULT_API_URL = "https://edinetdb.jp/v1"
EARLIEST_DATA_DATE = "2026-01-01"  # 決算短信データの提供開始日。初回runのevents開始日

# 2026-07-08の実地確認(第6弾、初めて68フィールド全件の実データを確認)で判明:
# `quarter` は既に整数 1〜4 (FY=4相当) で入っており、"Q1"/"FY"のような文字列
# ラベルではなかった。文字列で来る変種にも後方互換で対応できるよう両対応にする。
_QUARTER_TO_N = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 4}

# /companies レスポンスの想定フィールド名候補 (実地確認1で確認済み: security_code/edinet_code)。
_COMPANY_CODE_KEYS = ("security_code", "securities_code", "code", "stock_code", "sec_code")
_COMPANY_EDINET_KEYS = ("edinet_code", "edinetCode", "edinet_cd")

# /events 内の各イベントの証券コードフィールド候補 (要実地確認4)。
_EVENT_CODE_KEYS = ("security_code", "securities_code", "code", "stock_code", "sec_code")

# /earnings レスポンスの会計年度末フィールド候補。2026-07-08の実地確認(第6弾)で
# `fiscal_year_end` (例 '2026-03-31'、その決算短信が属する会計年度の期末日) の
# 実在を確認した。fy_startそのものを返すフィールドは存在しないため、期末日から
# 1年分逆算する (_fy_start_from_fy_end)。
_FY_END_FIELD_CANDIDATES = ("fiscal_year_end", "fy_end", "period_end", "current_period_end")
# 旧フォールバック: fy_startを直接持つフィールドがもし存在すればそれを優先する
# (現行APIでは未確認だが、将来の互換性のため残す)。
_FY_START_FIELD_CANDIDATES = ("fiscal_year_start", "fy_start", "period_start", "current_period_start")


def _ed_cfg(config: dict) -> dict:
    return config.get("edinetdb", {}) or {}


# ---------------------------------------------------------------------------
# State / store persistence
# ---------------------------------------------------------------------------

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


def load_store(path=None) -> dict:
    return load_auto_store(path or STORE_PATH)


def save_store(store: dict, path=None) -> None:
    path = path or STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1, sort_keys=True)


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

def _get(api_key: str, config: dict, path: str, params: dict | None = None) -> dict:
    """GET {api_url}{path}。X-API-Keyヘッダ。429は30秒待って1回だけ再試行
    (jquants.fetch_summaries と同じ粘り方)。"""
    api_url = _ed_cfg(config).get("api_url", DEFAULT_API_URL)
    headers = {"X-API-Key": api_key}
    resp = requests.get(f"{api_url}{path}", params=params, headers=headers, timeout=60)
    if resp.status_code == 429:
        time.sleep(30)
        resp = requests.get(f"{api_url}{path}", params=params, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _normalize_code(raw) -> str:
    """証券コードを4桁に正規化する (5桁なら末尾を落とす。jquants.record_to_point
    の code5[:4] と同じ規約)。"""
    s = str(raw).strip()
    return s[:4] if len(s) >= 5 else s


def _extract_list_of_dicts(body, known_keys: tuple[str, ...], context: str) -> list[dict]:
    """レスポンスbodyから配列部分を取り出す。

    2026-07-08の初回稼働で `/companies`・`/events` とも既知キー名 (要実地確認1/4)
    が外れて0件になる不具合を確認したため、既知キーが尽きたらトップレベル
    dictの値からlist[dict]を自動検出するフォールバックを追加した。これにより
    キー名の予想さえ外れなければ実データを取りこぼさない。それでも見つから
    ない場合は原因追跡用にトップレベルのキー名をprintする。
    """
    if isinstance(body, list):
        return body
    if not isinstance(body, dict):
        print(f"EDINET DB: {context} response is neither dict nor list "
              f"(type={type(body).__name__}); treating as empty")
        return []

    for key in known_keys:
        val = body.get(key)
        if isinstance(val, list):
            return val

    for key, val in body.items():
        if isinstance(val, list) and (not val or isinstance(val[0], dict)):
            print(f"EDINET DB: {context} used auto-detected key '{key}' "
                  f"(none of {known_keys} matched; response top-level keys: {list(body.keys())})")
            return val

    # 2026-07-08の実地確認で判明(第2弾): /companies/{edinet_code}/earnings は
    # "data" 直下が単一レコードではなく {"count":N, "earnings":[...], "edinet_code":...}
    # という「ラッパーdict」で、実際のリストはさらに1階層下 (data.earnings) にある。
    # known_keysのいずれかがdictなら、その中を既知キー→自動検出の順で再探索する。
    for key in known_keys:
        val = body.get(key)
        if isinstance(val, dict) and val:
            for inner_key in known_keys:
                inner_val = val.get(inner_key)
                if isinstance(inner_val, list):
                    print(f"EDINET DB: {context} found nested list at '{key}.{inner_key}' "
                          f"(wrapper keys: {list(val.keys())})")
                    return inner_val
            for inner_key, inner_val in val.items():
                if isinstance(inner_val, list) and (not inner_val or isinstance(inner_val[0], dict)):
                    print(f"EDINET DB: {context} found nested list at auto-detected '{key}.{inner_key}' "
                          f"(wrapper keys: {list(val.keys())})")
                    return inner_val

    # ネストしたリストが見つからない場合のみ、単一レコードのdictそのものを
    # [dict]でラップして救う (真にフラットな1件だけのレスポンス用の最終フォールバック)。
    for key in known_keys:
        val = body.get(key)
        if isinstance(val, dict) and val:
            print(f"EDINET DB: {context} '{key}' was a single dict, not a list "
                  f"(wrapping as one record; keys: {list(val.keys())})")
            return [val]

    print(f"EDINET DB: {context} response had no list field at all; top-level keys: {list(body.keys())}")
    return []


def fetch_companies_map(api_key: str, config: dict) -> dict[str, str]:
    """GET /companies?per_page=5000 (1リクエスト) -> {証券コード4桁: edinet_code}。
    ユニバース外も含め全社分を返す (呼び出し側でユニバースにfilterする)。"""
    body = _get(api_key, config, "/companies", {"per_page": 5000})
    rows = _extract_list_of_dicts(body, ("data", "companies", "results", "items", "list"), "/companies")

    result: dict[str, str] = {}
    unmatched_sample: dict | None = None
    for row in rows:
        raw_code = next((row.get(k) for k in _COMPANY_CODE_KEYS if row.get(k)), None)
        edinet_code = next((row.get(k) for k in _COMPANY_EDINET_KEYS if row.get(k)), None)
        if not raw_code or not edinet_code:
            if unmatched_sample is None:
                unmatched_sample = row
            continue
        result[_normalize_code(raw_code)] = str(edinet_code).strip()

    if rows:
        print(f"EDINET DB: /companies returned {len(rows)} rows, matched {len(result)} "
              f"to known field names {_COMPANY_CODE_KEYS + _COMPANY_EDINET_KEYS}.")
        if not result:
            print(f"EDINET DB: /companies sample unmatched row keys: "
                  f"{list(unmatched_sample.keys()) if unmatched_sample else '?'}")
    return result


def fetch_events(api_key: str, config: dict, since: date, until: date) -> list[dict]:
    """GET /events?event_type=earnings_summary&since&until。
    limit=1000 + offset でページング (バッチがlimit未満になるまで続行)。"""
    limit = 1000
    offset = 0
    events: list[dict] = []
    while True:
        body = _get(api_key, config, "/events", {
            "event_type": "earnings_summary",
            "since": since.isoformat(),
            "until": until.isoformat(),
            "limit": limit,
            "offset": offset,
        })
        batch = _extract_list_of_dicts(body, ("data", "events", "results", "items", "disclosures"), "/events")
        events.extend(batch)
        if len(batch) < limit:
            return events
        offset += limit


def fetch_earnings(api_key: str, config: dict, edinet_code: str) -> list[dict]:
    """GET /companies/{edinet_code}/earnings?limit={cfg}&include_nulls=true"""
    limit = _ed_cfg(config).get("earnings_limit", 8)
    body = _get(api_key, config, f"/companies/{edinet_code}/earnings",
                {"limit": limit, "include_nulls": "true"})
    return _extract_list_of_dicts(
        body, ("data", "earnings", "results", "items"), f"/companies/{edinet_code}/earnings")


# ---------------------------------------------------------------------------
# Record -> YTD point
# ---------------------------------------------------------------------------

def _num(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    if not s or s in {"-", "－", "―"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_disclosure_date(raw) -> str | None:
    """disclosure_date をISO日付文字列(YYYY-MM-DD)に正規化する。

    2026-07-08の実地確認(第6弾)で実データがRFC2822形式
    (例 'Thu, 14 May 2026 00:00:00 GMT') であることを確認した(ISO形式という
    当初の推測は誤り)。旧来のISO形式で来るケースにも後方互換で対応する。
    """
    if not raw:
        return None
    s = str(raw).strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _fy_start_from_fy_end(fy_end: str) -> str | None:
    """fiscal_year_end (例 '2026-03-31') から fy_start (例 '2025-04-01') を算出する。

    2026-07-08の実地確認(第6弾)で `fiscal_year_end` フィールドの実在を確認した。
    日本の決算短信は原則12ヶ月決算のため、期末日の1年前の翌日がfy_startになる。
    """
    if not fy_end:
        return None
    try:
        end = date.fromisoformat(str(fy_end).strip()[:10])
    except ValueError:
        return None
    try:
        prev_end = end.replace(year=end.year - 1)
    except ValueError:
        # 2/29(うるう日)を含む期末日で、1年前が非うるう年の場合のフォールバック。
        prev_end = end.replace(year=end.year - 1, day=28)
    return (prev_end + timedelta(days=1)).isoformat()


def _estimate_fy_start(disc_date: str, n: int, fiscal_year_end_month: int) -> str | None:
    """会計年度フィールドが無い場合のfy_start推定 (DESIGN_EDINETDB.md 3.3(b))。

    四半期nの期末月 = fy_start月 + 3n - 1 (mod 12)。開示日は期末のおよそ
    25〜65日後という前提で、それを満たす直近の期末候補が一意に決まる場合のみ
    fy_startを返す。決まらなければNone(安全側 -- 誤登録より取りこぼしを選ぶ)。
    """
    if not disc_date or n not in (1, 2, 3, 4):
        return None
    try:
        d = datetime.fromisoformat(disc_date[:10]).date()
    except ValueError:
        return None

    fy_start_month = (fiscal_year_end_month % 12) + 1
    end_month = ((fy_start_month - 1 + 3 * n - 1) % 12) + 1

    candidates = []
    for year_offset in (-1, 0, 1):
        end_year = d.year + year_offset
        try:
            end_date = date(end_year, end_month, 1)
        except ValueError:
            continue
        delta_days = (d - end_date).days
        if 25 <= delta_days <= 65:
            candidates.append(end_date)

    if len(candidates) != 1:
        return None

    end_date = candidates[0]
    fy_start_year = end_date.year
    fy_start_month_actual = end_date.month - (3 * n - 1)
    while fy_start_month_actual <= 0:
        fy_start_month_actual += 12
        fy_start_year -= 1
    return f"{fy_start_year:04d}-{fy_start_month_actual:02d}-01"


def _resolve_quarter_n(rec: dict) -> int | None:
    """quarter フィールドから四半期番号(1〜4、FY=4)を取り出す。

    2026-07-08の実地確認(第6弾)で実データは整数 (例 `'quarter': 4`) だと確認
    した。旧来の文字列ラベル("Q1"/"FY"等)で来る変種にも後方互換で対応する。
    """
    raw = rec.get("quarter")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        n = int(raw)
        return n if n in (1, 2, 3, 4) else None
    s = str(raw or "").strip().upper()
    return _QUARTER_TO_N.get(s)


def _resolve_fy_start(rec: dict, disc_date: str, n: int, fiscal_year_end_month: int | None) -> str | None:
    # 第一候補: fiscal_year_end (実地確認第6弾で実在確認済み) から1年分逆算。
    for key in _FY_END_FIELD_CANDIDATES:
        raw = rec.get(key)
        if raw:
            fy_start = _fy_start_from_fy_end(str(raw).strip())
            if fy_start:
                return fy_start
    # 第二候補: fy_startを直接持つフィールドがもしあれば(未確認だが将来の保険)。
    for key in _FY_START_FIELD_CANDIDATES:
        raw = rec.get(key)
        if raw:
            s = str(raw).strip()
            if len(s) >= 10:
                return s[:10]
    # 最終フォールバック: 開示日からの逆算推定(fiscal_year_end_monthが渡された場合のみ)。
    if fiscal_year_end_month is not None:
        return _estimate_fy_start(disc_date, n, fiscal_year_end_month)
    return None


def record_to_point(rec: dict, code: str, fiscal_year_end_month: int | None = None) -> dict | None:
    """earnings 1レコードを jquants.record_to_point と同じ点形式に変換する。

    戻り値: {"code", "fy_start", "n", "label", "eps", "revenue", "disc_date"}
    (eps/revenueはYTD累計)。対象外・fy_start不明ならNone。
    """
    n = _resolve_quarter_n(rec)
    if n is None:
        return None

    eps = _num(rec.get("eps"))
    revenue = _num(rec.get("revenue"))
    if revenue is not None:
        revenue *= 1_000_000  # 百万円 -> 円 (実地確認3で確認: 9024の通期revenue=513286はこの単位で整合)
    if eps is None and revenue is None:
        return None

    disc_date = _parse_disclosure_date(rec.get("disclosure_date")) or ""
    fy_start = _resolve_fy_start(rec, disc_date, n, fiscal_year_end_month)
    if fy_start is None:
        return None

    return {
        "code": code,
        "fy_start": fy_start,
        "n": n,
        "label": f"{fy_start[:4]}Q{n}",
        "eps": eps,
        "revenue": revenue,
        "disc_date": disc_date,
    }


# ---------------------------------------------------------------------------
# YTD差分の四半期化 -- J-Quantsストアを基準に使う
# ---------------------------------------------------------------------------

def derive_with_base(point: dict, base_quarters: list[dict]) -> dict | None:
    """point: record_to_point のYTD点。base_quarters: 同一銘柄の確定四半期
    リスト(J-Quantsストア + 既存edinetdbストアをlabelでマージしたもの。値は
    単四半期値)。

    n==1: 値 = YTDそのまま。
    n>=2: 同一年度のQ1..Q(n-1)がbase_quartersに全て揃っている場合のみ
          値 = ytd - sum(prior)。1つでも欠ければその項目はNone(スキップ)。
    eps/revenueは独立に判定する。両方Noneならレコード全体をNoneで返す。
    """
    n = point["n"]
    label = point["label"]
    fy_prefix = label[:4]
    by_label = {q["fiscal_quarter"]: q for q in base_quarters if q.get("fiscal_quarter")}

    result = {"fiscal_quarter": label, "eps": None, "revenue": None}
    for key in ("eps", "revenue"):
        ytd = point.get(key)
        if ytd is None:
            continue
        if n == 1:
            result[key] = round(ytd, 4)
            continue
        prior_total = 0.0
        complete = True
        for m in range(1, n):
            prior = by_label.get(f"{fy_prefix}Q{m}")
            if prior is None or prior.get(key) is None:
                complete = False
                break
            prior_total += prior[key]
        if complete:
            result[key] = round(ytd - prior_total, 4)

    if result["eps"] is None and result["revenue"] is None:
        return None
    return result


def _merge_into_store(store: dict, code: str, quarters: list[dict], checked_date: str, max_keep: int) -> None:
    """jquants._merge_into_store と同型 (source="edinetdb")。"""
    entry = store.setdefault(code, {"quarters": [], "checked_date": None, "source": "edinetdb"})
    entry["source"] = "edinetdb"
    by_label = {q["fiscal_quarter"]: q for q in entry["quarters"]}
    for q in quarters:
        by_label[q["fiscal_quarter"]] = q  # 新しい開示が同ラベルを上書き
    merged = sorted(by_label.values(), key=lambda q: (q["fiscal_quarter"][:4], q["fiscal_quarter"][-1]))
    entry["quarters"] = merged[-max_keep:]
    if entry["checked_date"] is None or (checked_date and checked_date > entry["checked_date"]):
        entry["checked_date"] = checked_date


# ---------------------------------------------------------------------------
# Update entrypoint
# ---------------------------------------------------------------------------

def update_fundamentals_auto(codes: list[str], config: dict | None = None,
                              base_store: dict | None = None,
                              priority_by_code: dict[str, int] | None = None) -> dict:
    """日次インクリメンタル。pipeline.py から J-Quants ストアを base_store と
    して受け取り、YTD差分の基準に使う。APIキー無し or enabled: false なら
    既存ストアを返すだけ(ネットワーク不使用)。

    priority_by_code: {code: 優先度(P1=1〜P4=4)} を渡すと、backlog消化を
    その優先度の昇順(1が最優先)で行う。2026-07-08追加 -- P1〜P4のプライオリティ
    評価は技術指標のみで決まりファンダメンタルに依存しないため、この呼び出し
    時点で当日分が確定済み。「P1が無くてもP2→P3→P4の順で優先的に」という
    要件を満たすため、二値(優先/非優先)ではなくランクそのものでソートする。
    未指定(dictに無い)コードはrank=99扱いで最後に回る。list.sort()は安定
    ソートなので、同ランク内の相対順序(events検出順)は保持される。
    """
    config = config or load_config()
    cfg = _ed_cfg(config)
    store = load_store()

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key or not cfg.get("enabled", False):
        return store

    base_store = base_store or {}
    state = load_state()
    budget = cfg.get("requests_per_day", 90)
    max_keep = cfg.get("max_quarters_keep", 12)
    code_set = set(codes)
    today = datetime.now().date()

    # 1. codemap更新判定: 空 or codemap_refresh_daysより古ければ再取得 (1req)。
    codemap: dict[str, str] = dict(state.get("codemap") or {})
    codemap_date = state.get("codemap_date")
    refresh_days = cfg.get("codemap_refresh_days", 30)
    needs_refresh = not codemap or not codemap_date or (
        today - date.fromisoformat(codemap_date)
    ).days >= refresh_days
    if needs_refresh and budget > 0:
        try:
            full_map = fetch_companies_map(api_key, config)
            budget -= 1
            codemap = {c: full_map[c] for c in codes if c in full_map}
            state["codemap"] = codemap
            state["codemap_date"] = today.isoformat()
        except Exception as e:
            print(f"EDINET DB codemap refresh failed (kept old): {e}")

    # 2. events取得: 前回last_events_dateの翌日〜今日。開示銘柄をbacklogへ追加。
    backlog: list[str] = list(dict.fromkeys(state.get("backlog") or []))
    backlog_set = set(backlog)
    last_events_date = state.get("last_events_date")
    since = (date.fromisoformat(last_events_date) + timedelta(days=1)
             if last_events_date else date.fromisoformat(EARLIEST_DATA_DATE))
    if since <= today and budget > 0:
        try:
            events = fetch_events(api_key, config, since, today)
            budget -= 1
            extracted = 0
            matched = 0
            unmatched_sample: dict | None = None
            for ev in events:
                raw_code = next((ev.get(k) for k in _EVENT_CODE_KEYS if ev.get(k)), None)
                if raw_code is None:
                    if unmatched_sample is None:
                        unmatched_sample = ev
                    continue
                extracted += 1
                code = _normalize_code(raw_code)
                if code in code_set and code not in backlog_set:
                    backlog.append(code)
                    backlog_set.add(code)
                    matched += 1
            if events:
                print(f"EDINET DB: /events returned {len(events)} events ({since}〜{today}), "
                      f"{extracted} had a recognizable code field, {matched} matched our universe "
                      f"and were queued.")
                if extracted == 0:
                    print(f"EDINET DB: /events sample unmatched event keys: "
                          f"{list(unmatched_sample.keys()) if unmatched_sample else '?'} "
                          f"(known code fields tried: {_EVENT_CODE_KEYS})")
            state["last_events_date"] = today.isoformat()
        except Exception as e:
            print(f"EDINET DB events fetch failed (state not advanced): {e}")

    # 2.5 優先順位付け: priority_by_codeが指定されていれば、backlogをそのランク
    # (P1=1〜P4=4、未指定コードは99)の昇順に並べ替える。安定ソートなので
    # 同ランク内の相対順序(検出順)は維持される。P1が無くてもP2→P3→P4の順で
    # 優先されるよう、二値ではなくランクそのもので比較する。
    if priority_by_code:
        backlog.sort(key=lambda c: priority_by_code.get(c, 99))

    # 3. backlog消化: 残り予算の範囲で /earnings を叩き、ストアへ反映。
    processed = 0
    remaining_backlog: list[str] = []
    # 2026-07-08の実地確認(第3〜6弾)でrecord_to_pointの個別フィールド名
    # (quarter/eps/revenue/disclosure_date/fiscal_year_end)を全て実データと
    # 突き合わせて修正済み。ただし将来APIが変わって再び噛み合わなくなった場合に
    # 気付けるよう、レコードは取れたのに1件も採用できなかった銘柄がいた場合は
    # 最初の1件だけキー一覧を軽くprintする(以前のような全フィールド1行ずつの
    # ダンプは調査完了に伴い撤去)。
    printed_empty_sample = False
    for code in backlog:
        if budget <= 0:
            remaining_backlog.append(code)
            continue
        edinet_code = codemap.get(code)
        if edinet_code is None:
            print(f"EDINET DB: code {code} not in codemap (kept in backlog, will retry after next refresh)")
            remaining_backlog.append(code)
            continue

        try:
            recs = fetch_earnings(api_key, config, edinet_code)
            budget -= 1
        except Exception as e:
            print(f"EDINET DB earnings fetch {code} failed (kept in backlog): {e}")
            remaining_backlog.append(code)
            continue

        own_quarters = store.get(code, {}).get("quarters", [])
        base_quarters = list(base_store.get(code, {}).get("quarters", [])) + list(own_quarters)

        derived: list[dict] = []
        checked_dates: list[str] = []
        for rec in recs:
            point = record_to_point(rec, code)
            if point is None:
                continue
            d = derive_with_base(point, base_quarters)
            if d is None:
                continue
            derived.append(d)
            if point.get("disc_date"):
                checked_dates.append(point["disc_date"])

        if derived:
            checked = max(checked_dates) if checked_dates else today.isoformat()
            _merge_into_store(store, code, derived, checked, max_keep)
        elif recs and not printed_empty_sample:
            printed_empty_sample = True
            sample = recs[0]
            print(f"EDINET DB: {code} fetched {len(recs)} earnings record(s) but 0 were usable "
                  f"after record_to_point/derive_with_base -- possible causes: quarter not in "
                  f"1-4, both eps/revenue missing, or fy_start unresolved (no {_FY_END_FIELD_CANDIDATES} "
                  f"field). sample record keys: {sorted(sample.keys())}")
        processed += 1

    state["backlog"] = remaining_backlog
    save_store(store)
    save_state(state)
    print(f"EDINET DB: {processed} codes processed, {len(remaining_backlog)} left in backlog.")
    return store


def main() -> None:
    from src.universe import load_universe

    config = load_config()
    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    # 単独実行時はJ-Quantsストア(data/fundamentals_auto.json)をbaseに使う
    # (pipeline実行時にjquants_mod.update_fundamentals_autoの戻り値を渡すのと同じ基準)。
    base_store = load_auto_store()
    update_fundamentals_auto(codes, config, base_store=base_store)


if __name__ == "__main__":
    main()
