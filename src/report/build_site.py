"""Site generation: report.json, breadth.json, per-stock chart JSON, and
copying the static dashboard/detail-page assets into docs/ (design doc 7).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.screener.scoring import combined_score

DOCS_DIR = REPO_ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"
CHARTS_DIR = DOCS_DATA_DIR / "charts"
REPORT_PATH = DOCS_DATA_DIR / "report.json"
BREADTH_PATH = DOCS_DATA_DIR / "breadth.json"

STATUS_ORDER = {
    "BREAKOUT": 0,
    "BREAKOUT_WEAK": 1,
    "WATCH_A": 2,
    "WATCH_B": 3,
    "EXTENDED": 4,
    # Watchlist tier: trend template passed, but VCP hasn't produced an
    # actionable setup yet. Ordered roughly by "how close to a real base".
    "REJECTED": 5,
    "IMMATURE": 6,
    "TOO_RECENT": 7,
    "NO_BASE": 8,
}
TIER_ORDER = {"confirmed": 0, "pool": 1, "watchlist": 2}

# セクター強度(機能B)の複合ソート順: 強 -> 中 -> 弱 -> 不明
SECTOR_STRENGTH_ORDER = {"強": 0, "中": 1, "弱": 2}


# ---------------------------------------------------------------------------
# 7.2 report.json assembly
# ---------------------------------------------------------------------------

def assemble_stock_record(
    code: str,
    name: str,
    latest_row: dict,
    tt_flags: dict,
    vcp_result: dict,
    entry_result: dict,
    fund_info: dict,
    config: dict | None = None,
    tier_override: str | None = None,
) -> dict:
    """Combine the outputs of trend_template/vcp/entry/fundamentals into one
    report.json stock record.

    `tier_override` lets the caller place a stock in the "watchlist" tier
    (trend template passed, but no actionable VCP/entry setup yet) instead
    of the fundamentals-coverage-derived confirmed/pool tier.
    """
    config = config or load_config()

    tier = tier_override or fund_info["tier"]
    phase1_score = fund_info.get("full_score") if tier == "confirmed" and fund_info.get("full_score") is not None else fund_info.get("tech_score")
    vcp_score = vcp_result.get("vcp_score")
    if phase1_score is not None and vcp_score is not None:
        total_score = combined_score(phase1_score, vcp_score, config)
    elif phase1_score is not None:
        # Watchlist stocks (no VCP setup yet): rank by the trend-template
        # score alone rather than leaving total_score empty.
        total_score = round(phase1_score, 1)
    else:
        total_score = None

    return {
        "code": code,
        "name": name,
        "tier": tier,
        "status": entry_result.get("status"),
        "close": latest_row.get("close"),
        "total_score": total_score,
        "tech_score": fund_info.get("tech_score"),
        "full_score": fund_info.get("full_score"),
        "vcp_score": vcp_score,
        "rs": latest_row.get("rs"),
        "footprint": vcp_result.get("footprint"),
        "pivot": entry_result.get("pivot"),
        "buy_stop": entry_result.get("buy_stop"),
        "stop_loss": entry_result.get("stop_loss"),
        "risk_pct": entry_result.get("risk_pct"),
        "dist_to_pivot": entry_result.get("dist_to_pivot"),
        "fund_coverage": fund_info.get("fund_coverage"),
        "fund_strong": fund_info.get("fund_strong"),
        "fund_eps_yoy": fund_info.get("fund_eps_yoy"),
        "fund_rev_yoy": fund_info.get("fund_rev_yoy"),
        "fund_stale": fund_info.get("fund_stale", False),
        "fund_checked_date": fund_info.get("fund_checked_date"),
        "eps_accel_slope": fund_info.get("eps_accel_slope"),
        "must_flags": {"tt": tt_flags, "vcp": vcp_result.get("must_flags")},
    }


def attach_priority(record: dict, priority_eval: dict | None) -> dict:
    """機能A: プライオリティ評価結果をレコードにマージする。"""
    if priority_eval is None:
        return record
    record["priority"] = priority_eval["priority"]
    record["priority_penalty"] = priority_eval["penalty"]
    record["priority_unmet"] = priority_eval["unmet"]
    record["ma_deviation_pct"] = priority_eval["ma_deviation_pct"]
    record["high52w_distance_pct"] = priority_eval["high52w_distance_pct"]
    return record


def assemble_priority_record(
    code: str,
    name: str,
    latest_row: dict,
    priority_eval: dict,
    tt_flags: dict | None = None,
    has_chart: bool = False,
) -> dict:
    """機能A: P2〜P4銘柄(旧ウォッチリスト置き換え)の軽量レコード。

    VCP/エントリー評価は行わないため、ピボット等は持たない。
    """
    record = {
        "code": code,
        "name": name,
        "tier": "watchlist",
        "status": None,
        "close": latest_row.get("close"),
        "rs": latest_row.get("rs"),
        "total_score": None,
        "has_chart": has_chart,
        "must_flags": {"tt": tt_flags, "vcp": None},
    }
    return attach_priority(record, priority_eval)


def _sort_key(stock: dict) -> tuple:
    tier_rank = TIER_ORDER.get(stock["tier"], 99)
    if stock["tier"] == "watchlist":
        # 機能A/B複合ソート: プライオリティ昇順 -> セクター強度 -> RS降順
        return (
            tier_rank,
            stock.get("priority") or 99,
            SECTOR_STRENGTH_ORDER.get(stock.get("sector_strength"), 9),
            -(stock.get("rs") or 0.0),
        )
    status_rank = STATUS_ORDER.get(stock["status"], 99)
    score = stock.get("total_score") or 0.0
    return (tier_rank, status_rank, -score, 0)


def build_report(
    stocks: list[dict],
    universe_size: int,
    template_pass: int,
    data_warnings: dict | None = None,
    generated_at: str | None = None,
    priority_counts: dict | None = None,
    p1_scarce: bool | None = None,
) -> dict:
    ordered = sorted(stocks, key=_sort_key)
    report = {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "universe_size": universe_size,
        "template_pass": template_pass,
        "priority_counts": priority_counts,
        "p1_scarce": p1_scarce,
        "data_warnings": data_warnings or {"failed_tickers": [], "stale_tickers": [], "csv_errors": []},
        "stocks": ordered,
    }
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


# ---------------------------------------------------------------------------
# 6. Breadth meter
# ---------------------------------------------------------------------------

def load_breadth() -> dict:
    if not BREADTH_PATH.exists():
        return {"history": []}
    with open(BREADTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_breakout_success_rate(
    status_history: dict, lookback_days: int = 20, hold_days: int = 5
) -> float | None:
    """Share of BREAKOUT events (in the trailing `lookback_days` entries per
    code) that were still above their pivot `hold_days` trading days later.

    Uses status alone as a proxy: BREAKOUT/BREAKOUT_WEAK/EXTENDED all imply
    close > pivot, while WATCH_A implies the stock fell back below pivot.
    """
    successes = 0
    total = 0
    for entries in status_history.values():
        n = len(entries)
        start = max(0, n - lookback_days - hold_days)
        for i in range(start, n - hold_days):
            if entries[i]["status"] == "BREAKOUT":
                total += 1
                later_status = entries[i + hold_days]["status"]
                if later_status in ("BREAKOUT", "BREAKOUT_WEAK", "EXTENDED"):
                    successes += 1
    if total == 0:
        return None
    return round(successes / total, 3)


def update_breadth(
    date_str: str,
    universe_size: int,
    template_pass: int,
    watch_count: int,
    status_history: dict,
    keep_days: int = 60,
    priority_counts: dict | None = None,
) -> dict:
    breadth = load_breadth()
    entry = {
        "date": date_str,
        "universe_size": universe_size,
        "template_pass": template_pass,
        "template_pass_rate": round(template_pass / universe_size, 4) if universe_size else None,
        "watch_count": watch_count,
        "breakout_success_rate": compute_breakout_success_rate(status_history),
    }
    if priority_counts is not None:
        # 機能A: P1〜P4件数を地合い指標として毎回記録
        entry.update(
            {
                "p1_count": priority_counts.get("p1", 0),
                "p2_count": priority_counts.get("p2", 0),
                "p3_count": priority_counts.get("p3", 0),
                "p4_count": priority_counts.get("p4", 0),
            }
        )
    breadth["history"] = [h for h in breadth["history"] if h.get("date") != date_str]
    breadth["history"].append(entry)
    breadth["history"] = breadth["history"][-keep_days:]
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(BREADTH_PATH, "w", encoding="utf-8") as f:
        json.dump(breadth, f, ensure_ascii=False, indent=2)
    return breadth


# ---------------------------------------------------------------------------
# 7.4 per-stock chart data
# ---------------------------------------------------------------------------

def _series_points(df: pd.DataFrame, col: str) -> list[dict]:
    if col not in df.columns:
        return []
    out = []
    for row in df.itertuples(index=False):
        value = getattr(row, col)
        if pd.isna(value):
            continue
        out.append({"time": row.date.strftime("%Y-%m-%d"), "value": round(float(value), 4)})
    return out


def build_chart_data(code: str, df: pd.DataFrame, vcp_result: dict, entry_result: dict, lookback_days: int = 260) -> dict:
    recent = df.tail(lookback_days).reset_index(drop=True)

    candles = [
        {
            "time": row.date.strftime("%Y-%m-%d"),
            "open": round(float(row.open), 2),
            "high": round(float(row.high), 2),
            "low": round(float(row.low), 2),
            "close": round(float(row.close), 2),
        }
        for row in recent.itertuples(index=False)
    ]
    volume = [
        {"time": row.date.strftime("%Y-%m-%d"), "value": float(row.volume)}
        for row in recent.itertuples(index=False)
    ]

    markers = []
    for c in vcp_result.get("contractions", []) or []:
        markers.append({"type": "swing_high", "price": c["high_price"]})
        markers.append({"type": "swing_low", "price": c["low_price"]})

    return {
        "code": code,
        "candles": candles,
        "volume": volume,
        "ma50": _series_points(recent, "ma50"),
        "ma150": _series_points(recent, "ma150"),
        "ma200": _series_points(recent, "ma200"),
        "rs_line": _series_points(recent, "rs_line"),
        "pivot": entry_result.get("pivot"),
        "stop_loss": entry_result.get("stop_loss"),
        "markers": markers,
    }


def write_chart_data(code: str, chart_data: dict) -> None:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHARTS_DIR / f"{code}.json", "w", encoding="utf-8") as f:
        json.dump(chart_data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Static asset placeholders (index.html/stock.html/assets/* are static files
# maintained directly in the repo; this just guarantees the data/ directory
# tree exists so the pages don't 404 on a clean checkout before the first run)
# ---------------------------------------------------------------------------

def ensure_data_dir_exists() -> None:
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    if not REPORT_PATH.exists():
        build_report(stocks=[], universe_size=0, template_pass=0)
    if not BREADTH_PATH.exists():
        with open(BREADTH_PATH, "w", encoding="utf-8") as f:
            json.dump({"history": []}, f, ensure_ascii=False, indent=2)
