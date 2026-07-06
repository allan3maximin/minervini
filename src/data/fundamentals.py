"""Manual-CSV fundamentals loader, validation, tier determination, and staleness
guard (design doc section 1.3).

Sources: EDINET API auto-fetch (src/data/edinet.py, data/fundamentals_auto.json)
merged with the human-maintained manual/fundamentals.csv via merge_fundamentals
(manual rows win per quarter). Absence of any row is not an error, just "pool"
tier.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.screener.trend_template import compute_accel_slope, compute_full_score, quarter_sort_key, technical_score

DEFAULT_CSV_PATH = REPO_ROOT / "manual" / "fundamentals.csv"
CSV_COLUMNS = ["code", "fiscal_quarter", "eps", "revenue", "monthly_yoy", "checked_date"]
_QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")


def load_fundamentals_csv(path=None) -> tuple[pd.DataFrame, list[str]]:
    """Parse and validate manual/fundamentals.csv.

    Malformed rows (bad code/fiscal_quarter format) and duplicate
    (code, fiscal_quarter) pairs are skipped with a warning rather than
    failing the job (design doc 1.3).
    """
    path = path or DEFAULT_CSV_PATH
    warnings: list[str] = []
    empty = pd.DataFrame(columns=CSV_COLUMNS)

    if not path.exists():
        return empty, warnings

    raw = pd.read_csv(path, dtype={"code": str})
    if raw.empty:
        return empty, warnings

    valid_rows = []
    seen: set[tuple[str, str]] = set()
    for i, row in raw.iterrows():
        line_no = i + 2  # header is line 1
        code = str(row.get("code", "")).strip()
        fq = str(row.get("fiscal_quarter", "")).strip()

        if not code or code == "nan" or not _QUARTER_RE.match(fq):
            warnings.append(f"行{line_no}: 不正な形式のためスキップ (code={code!r}, fiscal_quarter={fq!r})")
            continue

        key = (code, fq)
        if key in seen:
            warnings.append(f"行{line_no}: 四半期重複のためスキップ (code={code}, fiscal_quarter={fq})")
            continue
        seen.add(key)
        valid_rows.append(dict(row))

    if not valid_rows:
        return empty, warnings

    df = pd.DataFrame(valid_rows)
    df["code"] = df["code"].astype(str)
    for col in ("eps", "revenue", "monthly_yoy"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df, warnings


def build_fundamentals_by_code(df: pd.DataFrame) -> dict[str, dict]:
    """Group parsed CSV rows by code into {quarters, monthly_yoy, checked_date}."""
    result: dict[str, dict] = {}
    if df.empty:
        return result

    for code, group in df.groupby("code"):
        quarters = [
            {k: (None if pd.isna(v) else v) for k, v in row.items() if k != "code"}
            for row in group.to_dict("records")
        ]
        quarters_sorted = sorted(quarters, key=lambda q: quarter_sort_key(q["fiscal_quarter"]))
        latest = quarters_sorted[-1]
        result[code] = {
            "quarters": quarters,
            "monthly_yoy": latest.get("monthly_yoy"),
            "checked_date": latest.get("checked_date"),
        }
    return result


def merge_fundamentals(auto_by_code: dict, manual_by_code: dict) -> dict:
    """EDINET自動取得と手動CSVを統合する。同一(code, fiscal_quarter)は手動が勝ち、
    monthly_yoy / checked_date も手動があれば手動を採用する。"""
    result: dict[str, dict] = {}
    for code in set(auto_by_code) | set(manual_by_code):
        auto = auto_by_code.get(code) or {}
        manual = manual_by_code.get(code) or {}

        by_label: dict[str, dict] = {}
        for q in auto.get("quarters", []):
            fq = q.get("fiscal_quarter")
            if fq:
                by_label[fq] = {"fiscal_quarter": fq, "eps": q.get("eps"), "revenue": q.get("revenue")}
        for q in manual.get("quarters", []):
            fq = q.get("fiscal_quarter")
            if fq:
                by_label[fq] = dict(q)  # manual wins

        quarters = sorted(by_label.values(), key=lambda q: quarter_sort_key(q["fiscal_quarter"]))
        result[code] = {
            "quarters": quarters,
            "monthly_yoy": manual.get("monthly_yoy") if manual else None,
            "checked_date": manual.get("checked_date") or auto.get("checked_date"),
        }
    return result


def fund_coverage_tier(code: str, fundamentals_by_code: dict) -> dict:
    """"full" (EPS acceleration computable) / "partial" (rows exist but not
    enough for acceleration) / "none" (no rows) -> confirmed/confirmed/pool."""
    data = fundamentals_by_code.get(code)
    if not data or not data.get("quarters"):
        return {"fund_coverage": "none", "tier": "pool"}
    eps_slope = compute_accel_slope(data["quarters"], "eps")
    coverage = "full" if eps_slope is not None else "partial"
    return {"fund_coverage": coverage, "tier": "confirmed"}


def compute_fund_stale(checked_date: str | None, today: date, config: dict | None = None) -> bool:
    config = config or load_config()
    if not checked_date:
        return False
    stale_days = config["fundamentals"]["stale_days"]
    checked = pd.to_datetime(checked_date).date()
    return (today - checked).days > stale_days


def get_fundamentals_for_code(
    code: str, fundamentals_by_code: dict, today: date | None = None, config: dict | None = None
) -> dict:
    config = config or load_config()
    today = today or datetime.now().date()
    tier_info = fund_coverage_tier(code, fundamentals_by_code)
    data = fundamentals_by_code.get(code)

    if not data:
        return {**tier_info, "fund_stale": False, "fund_checked_date": None, "monthly_yoy": None, "quarters": []}

    return {
        **tier_info,
        "fund_stale": compute_fund_stale(data.get("checked_date"), today, config),
        "fund_checked_date": data.get("checked_date"),
        "monthly_yoy": data.get("monthly_yoy"),
        "quarters": data["quarters"],
    }


def score_stock(
    code: str,
    latest_row: dict,
    fundamentals_by_code: dict,
    today: date | None = None,
    config: dict | None = None,
) -> dict:
    """The two-axis scoring tie-in (design doc 3.2): every stock that passes
    the trend template gets a tech_score (pool-tier ranking key); stocks with
    a "confirmed" fundamentals tier additionally get a full_score (used to
    rank the confirmed tier instead)."""
    config = config or load_config()
    info = get_fundamentals_for_code(code, fundamentals_by_code, today, config)

    result = {
        "tech_score": technical_score(latest_row, config),
        "tier": info["tier"],
        "fund_coverage": info["fund_coverage"],
        "fund_stale": info["fund_stale"],
        "fund_checked_date": info["fund_checked_date"],
        "full_score": None,
        "eps_accel_slope": None,
        "rev_accel_slope": None,
    }

    if info["tier"] == "confirmed":
        full = compute_full_score(
            latest_row,
            eps_quarters=info["quarters"],
            revenue_quarters=info["quarters"],
            monthly_yoy=info.get("monthly_yoy"),
            config=config,
        )
        result["full_score"] = full["full_score"]
        result["eps_accel_slope"] = full["eps_accel_slope"]
        result["rev_accel_slope"] = full["rev_accel_slope"]

    return result
