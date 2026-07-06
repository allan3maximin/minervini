"""EDINET API v2 automatic fundamentals (EPS / revenue) fetcher.

Flow (incremental, runs inside the daily pipeline):
  1. List disclosure documents day-by-day since the last processed date
     (``GET /documents.json?date=YYYY-MM-DD&type=2``).
  2. Keep 有価証券報告書(120) / 四半期報告書(140, 2024年4月廃止) /
     半期報告書(160) whose secCode maps into our universe and csvFlag == "1".
  3. Download the CSV bundle (``GET /documents/{docID}?type=5`` -> zip of
     UTF-16LE TSVs) and extract the current-period YTD EPS / revenue.
  4. Derive per-label values by diffing YTD points within a fiscal year:
     value(Qn) = ytd(Qn) - ytd(prev point). Post-2024 (no Q1/Q3 filings) this
     yields H1 under the Q2 label and H2 under the Q4 label; YoY comparisons
     in compute_accel_slope stay aligned because the same regime applies to
     the prior year too.
  5. Persist to data/fundamentals_auto.json (+ data/edinet_state.json).

No API key (env EDINET_API_KEY) -> no network access at all; the previously
stored auto data is still loaded and used. Manual CSV rows always win over
auto rows for the same (code, fiscal_quarter) -- see
fundamentals.merge_fundamentals.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
import zipfile
from datetime import date, datetime, timedelta

import requests

from src.config import REPO_ROOT, load_config

AUTO_PATH = REPO_ROOT / "data" / "fundamentals_auto.json"
STATE_PATH = REPO_ROOT / "data" / "edinet_state.json"

API_KEY_ENV = "EDINET_API_KEY"
DEFAULT_API_URL = "https://api.edinet-fsa.go.jp/api/v2"

# 当期(YTD)の主要コンテキストID。メンバー無し(=連結優先)を先に試し、
# 単体しか無い提出者向けに NonConsolidatedMember をフォールバックにする。
_CONTEXTS_CONSOLIDATED = ["CurrentYTDDuration", "CurrentYearDuration", "InterimDuration"]
_CONTEXTS_NONCONSOLIDATED = [c + "_NonConsolidatedMember" for c in _CONTEXTS_CONSOLIDATED]

# 要素IDの部分一致候補(優先順)。会計基準・業種で名称が揺れるため部分一致。
_EPS_CANDIDATES = ["BasicEarningsLossPerShare", "BasicEarningsPerShare"]
_REVENUE_CANDIDATES = [
    "NetSales",
    "RevenueIFRS",
    "Revenue",
    "OperatingRevenue",
    "GrossOperatingRevenue",
    "OperatingIncomeINS",
]
# 経営指標サマリー(5年分の表)由来の要素は別コンテキストなので、
# コンテキストIDフィルタで自然に除外される。


def _edinet_cfg(config: dict) -> dict:
    return config.get("edinet", {}) or {}


def load_auto_store(path=None) -> dict:
    path = path or AUTO_PATH
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_auto_store(store: dict, path=None) -> None:
    path = path or AUTO_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1, sort_keys=True)


def load_state(path=None) -> dict:
    path = path or STATE_PATH
    if not path.exists():
        return {"last_list_date": None, "processed_doc_ids": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, path=None) -> None:
    path = path or STATE_PATH
    # docID履歴は直近分だけ保持(再処理防止には日付境界+直近分で十分)。
    state["processed_doc_ids"] = state.get("processed_doc_ids", [])[-5000:]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

def _get(url: str, api_key: str, params: dict | None = None, timeout: int = 60) -> requests.Response:
    headers = {"Subscription-Key": api_key}
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


def list_documents(day: date, api_key: str, config: dict) -> list[dict]:
    """その日の提出書類一覧(type=2: メタデータ+結果)を返す。"""
    api_url = _edinet_cfg(config).get("api_url", DEFAULT_API_URL)
    resp = _get(f"{api_url}/documents.json", api_key, params={"date": day.isoformat(), "type": 2})
    body = resp.json()
    return body.get("results") or []


def fetch_document_csv(doc_id: str, api_key: str, config: dict) -> bytes:
    """CSVバンドル(zip)のバイト列を返す。"""
    api_url = _edinet_cfg(config).get("api_url", DEFAULT_API_URL)
    resp = _get(f"{api_url}/documents/{doc_id}", api_key, params={"type": 5}, timeout=120)
    return resp.content


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_number(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", "")
    if not s or s in {"－", "-", "―"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rows_from_zip(zip_bytes: bytes) -> list[dict]:
    """zip内の全TSV(UTF-16LE)を行dictのリストに展開する。"""
    rows: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as f:
                text = f.read().decode("utf-16", errors="replace")
            reader = csv.DictReader(io.StringIO(text), delimiter="\t")
            for row in reader:
                rows.append(row)
    return rows


def _pick_value(rows: list[dict], element_candidates: list[str]) -> float | None:
    """候補要素×コンテキスト優先順で当期YTD値を1つ選ぶ。"""
    for contexts in (_CONTEXTS_CONSOLIDATED, _CONTEXTS_NONCONSOLIDATED):
        for cand in element_candidates:
            for row in rows:
                elem = row.get("要素ID") or ""
                ctx = row.get("コンテキストID") or ""
                if cand in elem and ctx in contexts:
                    val = _parse_number(row.get("値") or "")
                    if val is not None:
                        return val
    return None


def extract_ytd_point(zip_bytes: bytes) -> dict:
    """CSVバンドルから当期YTDのEPS/売上を抽出する。"""
    rows = _rows_from_zip(zip_bytes)
    return {
        "eps": _pick_value(rows, _EPS_CANDIDATES),
        "revenue": _pick_value(rows, _REVENUE_CANDIDATES),
    }


# ---------------------------------------------------------------------------
# Quarter derivation
# ---------------------------------------------------------------------------

def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) + (1 if end.day >= start.day - 5 else 0)


def quarter_label(period_start: date, period_end: date) -> tuple[str, int] | None:
    """YTD期間 -> ("YYYYQn", n)。fy=期首の年。nは期間月数/3の丸め。"""
    months = _months_between(period_start, period_end)
    n = round(months / 3)
    if n < 1 or n > 4:
        return None
    return f"{period_start.year}Q{n}", n


def derive_quarters(ytd_points: list[dict]) -> list[dict]:
    """会計年度ごとにYTD点を昇順に並べ、隣接差分でラベル値を導出する。

    ytd_points: [{"fy_start": "YYYY-MM-DD", "n": int, "label": "YYYYQn",
                  "eps": float|None, "revenue": float|None}]
    戻り値: [{"fiscal_quarter", "eps", "revenue"}] (差分済み)
    """
    by_fy: dict[str, list[dict]] = {}
    for p in ytd_points:
        by_fy.setdefault(p["fy_start"], []).append(p)

    out: list[dict] = []
    for fy_points in by_fy.values():
        fy_points = sorted(fy_points, key=lambda p: p["n"])
        prev: dict | None = None
        for p in fy_points:
            if prev is not None and p["n"] == prev["n"]:
                continue  # 同一四半期の重複(訂正報告書等)は先勝ち
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


# ---------------------------------------------------------------------------
# Store update
# ---------------------------------------------------------------------------

def _merge_into_store(store: dict, code: str, quarters: list[dict], checked_date: str, max_keep: int) -> None:
    entry = store.setdefault(code, {"quarters": [], "checked_date": None, "source": "edinet"})
    by_label = {q["fiscal_quarter"]: q for q in entry["quarters"]}
    for q in quarters:
        by_label[q["fiscal_quarter"]] = q  # 新しい提出が同ラベルを上書き
    merged = sorted(by_label.values(), key=lambda q: (q["fiscal_quarter"][:4], q["fiscal_quarter"][-1]))
    entry["quarters"] = merged[-max_keep:]
    if entry["checked_date"] is None or checked_date > entry["checked_date"]:
        entry["checked_date"] = checked_date


def update_fundamentals_auto(codes: list[str], config: dict | None = None, backfill_days: int | None = None) -> dict:
    """日次インクリメンタル取得。APIキーが無ければ既存ストアを返すだけ。

    戻り値は fundamentals_auto.json と同形式の {code: {...}}。
    """
    config = config or load_config()
    cfg = _edinet_cfg(config)
    store = load_auto_store()

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key or not cfg.get("enabled", True):
        return store

    state = load_state()
    today = datetime.now().date()
    lookback = backfill_days if backfill_days is not None else cfg.get("lookback_days", 7)
    start = today - timedelta(days=lookback)
    if backfill_days is None and state.get("last_list_date"):
        resume = date.fromisoformat(state["last_list_date"]) + timedelta(days=1)
        start = max(start, min(resume, today))

    doc_types = set(cfg.get("doc_type_codes", ["120", "140", "160"]))
    sleep_sec = cfg.get("sleep_sec", 0.3)
    max_keep = cfg.get("max_quarters_keep", 12)
    code_set = set(codes)
    processed = set(state.get("processed_doc_ids", []))

    # code(4桁) ごとにYTD点を貯めてから差分導出する。
    ytd_by_code: dict[str, list[dict]] = {}
    checked_by_code: dict[str, str] = {}
    n_docs = 0

    day = start
    while day <= today:
        try:
            docs = list_documents(day, api_key, config)
        except Exception as e:
            print(f"EDINET list {day} failed (skipped): {e}")
            day += timedelta(days=1)
            continue
        for doc in docs:
            doc_id = doc.get("docID")
            sec = (doc.get("secCode") or "").strip()
            if (
                not doc_id
                or doc_id in processed
                or doc.get("docTypeCode") not in doc_types
                or doc.get("csvFlag") != "1"
                or len(sec) < 4
            ):
                continue
            code = sec[:4]
            if code not in code_set:
                continue
            ps, pe = doc.get("periodStart"), doc.get("periodEnd")
            if not ps or not pe:
                continue
            label_n = quarter_label(date.fromisoformat(ps), date.fromisoformat(pe))
            if label_n is None:
                continue
            label, n = label_n
            try:
                point = extract_ytd_point(fetch_document_csv(doc_id, api_key, config))
            except Exception as e:
                print(f"EDINET doc {doc_id} ({code}) failed (skipped): {e}")
                continue
            time.sleep(sleep_sec)
            if point["eps"] is None and point["revenue"] is None:
                processed.add(doc_id)
                continue
            ytd_by_code.setdefault(code, []).append({"fy_start": ps, "n": n, "label": label, **point})
            submit = (doc.get("submitDateTime") or "")[:10] or day.isoformat()
            if code not in checked_by_code or submit > checked_by_code[code]:
                checked_by_code[code] = submit
            processed.add(doc_id)
            n_docs += 1
        time.sleep(sleep_sec)
        day += timedelta(days=1)

    for code, points in ytd_by_code.items():
        # 既存ストアのYTD差分と混ぜないため、既存ラベルはそのまま、
        # 新規点は同一年度内の既知YTD点のみで差分する(年度先頭が欠けている
        # 場合、最初の点はYTDそのままになる点に注意 -> 有報/半期の全点が
        # 揃う運用(バックフィル)を推奨)。
        quarters = derive_quarters(points)
        _merge_into_store(store, code, quarters, checked_by_code.get(code, today.isoformat()), max_keep)

    save_auto_store(store)
    state["last_list_date"] = today.isoformat()
    state["processed_doc_ids"] = sorted(processed)
    save_state(state)
    print(f"EDINET: {n_docs} documents processed, {len(ytd_by_code)} codes updated.")
    return store


def main() -> None:
    parser = argparse.ArgumentParser(description="EDINET fundamentals fetcher")
    parser.add_argument("--backfill-days", type=int, default=None, help="遡って取得する日数(例: 730)")
    args = parser.parse_args()
    from src.universe import load_universe

    config = load_config()
    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    update_fundamentals_auto(codes, config, backfill_days=args.backfill_days)


if __name__ == "__main__":
    main()
