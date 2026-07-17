"""Site generation: report.json, breadth.json, per-stock chart JSON, and
copying the static dashboard/detail-page assets into docs/ (design doc 7).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.report.secure_io import read_docs_json, write_docs_json
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
    "TOO_VOLATILE": 8,
    "NO_BASE": 9,
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
        # サマリー生成(summary.py)・個別銘柄画面用のVCP文脈。footprint文字列
        # より一段細かい素の数値(ベース日数・高値からの日数・各収縮の深さ%)。
        "vcp_detail": _build_vcp_detail(vcp_result, config),
        # 枯れ度(DRY-UP)バッジ/ソート用。VCP MUST・vcp_scoreとは独立(表示専用、
        # スコア融合なし)。バッジ値=dryup_med_10_50。
        "dryup": _build_dryup_badge(vcp_result, config),
        # 監視タブ分類用(actionableならNone)。stage/near/missing/detail。
        "setup_stage": build_setup_stage(vcp_result, config),
    }


def _build_dryup_badge(vcp_result: dict, config: dict) -> dict:
    """DRY-UPバッジ用レコード。

    バッジ値 dryup_med_10_50 は V5(a)診断の recent10_median/vol_ma50 と同一系列
    (indicators.dryup_metrics の定義と一致。base_df 末尾=full df 末尾のため tail(10)
    も一致する)。config.dryup の2段閾値で badge 種別(激枯れ/枯れ気味)を決める。
    VCP MUST・vcp_score には一切融合しない(「スコアは順位付け、フラグは事実」)。
    """
    diagnostics = vcp_result.get("vcp_diagnostics") or {}
    v5 = diagnostics.get("v5") or {}
    med = v5.get("recent10_median")
    vol_ma50 = v5.get("vol_ma50")
    value = round(med / vol_ma50, 4) if (med is not None and vol_ma50) else None

    d = config.get("dryup", {})
    strong_th = d.get("dryup_badge_strong", 0.66)  # ≒p25
    mild_th = d.get("dryup_badge_mild", 0.77)      # ≒p50
    if value is None:
        badge = None
    elif value <= strong_th:
        badge = "extreme"   # 激枯れ
    elif value <= mild_th:
        badge = "dryup"     # 枯れ気味
    else:
        badge = None
    return {"value": value, "badge": badge}


def _build_vcp_detail(vcp_result: dict, config: dict) -> dict:
    depths_pct = [round(c["depth"] * 100, 1) for c in vcp_result.get("contractions") or []]
    diagnostics = vcp_result.get("vcp_diagnostics") or {}
    v5_diag = diagnostics.get("v5") or {}
    return {
        "base_days": vcp_result.get("base_days"),
        "days_from_high": vcp_result.get("days_from_high"),
        "t0_date": str(vcp_result["t0_date"])[:10] if vcp_result.get("t0_date") is not None else None,
        "depths_pct": depths_pct,
        "depth_last_pct": depths_pct[-1] if depths_pct else None,
        "last_depth_max_pct": round(config["vcp"]["last_depth_max"] * 100, 1),
        "volume_dryup": {
            "recent10_median": v5_diag.get("recent10_median"),
            "vol_ma50": v5_diag.get("vol_ma50"),
            "median_ratio_threshold": v5_diag.get("median_ratio_threshold"),
            "sub_a_pass": v5_diag.get("sub_a_pass"),
            "sub_b_pass": v5_diag.get("sub_b_pass"),
        },
        "shakeout_detected": vcp_result.get("shakeout_detected", False),
    }


# ---------------------------------------------------------------------------
# 監視(watchlist)分類: セットアップ進行度 (2026-07-17 新設)
# ---------------------------------------------------------------------------
# 監視タブ100件超を毎日全部見るのは不可能なので、VCP評価の非アクショナブル
# ステータス+診断値から「今どの段階で、何が足りないか」を機械分類する。
# stage:
#   forming    = IMMATURE (ベース形成中。base_min_daysまでの残日数を出す)
#   fresh_high = TOO_RECENT (高値更新直後でベース自体が未開始)
#   rejected   = REJECTED (ベースはあるがV1〜V7のどれかで不合格。missingに列挙)
#   volatile   = TOO_VOLATILE (ATR過大で評価対象外)
#   no_base    = NO_BASE (スキャン窓に基準高値なし)
# near: 「あと一歩」フラグ。forming で残日数<=near_days、rejected で未達フラグが
#       ちょうど1個のときTrue。フロントはこのグループだけ最上段に出す。

SETUP_STAGE_NEAR_DAYS_DEFAULT = 5


def build_setup_stage(vcp_result: dict, config: dict | None = None) -> dict | None:
    """非アクショナブルVCP結果を進行度ステージへ分類する。actionableならNone。"""
    config = config or load_config()
    status = vcp_result.get("status")
    vcp_cfg = config.get("vcp", {})

    if status == "IMMATURE":
        base_min = vcp_cfg.get("base_min_days", 15)
        near_days = vcp_cfg.get("setup_stage_near_days", SETUP_STAGE_NEAR_DAYS_DEFAULT)
        bd = vcp_result.get("base_days") or 0
        remain = max(0, base_min - bd)
        return {
            "stage": "forming",
            "near": remain <= near_days,
            "missing": [],
            "detail": f"ベース{bd}日目 (最短{base_min}日まであと{remain}日)",
        }
    if status == "TOO_RECENT":
        dfh = vcp_result.get("days_from_high")
        suffix = f" (高値から{dfh}日)" if dfh is not None else ""
        return {
            "stage": "fresh_high",
            "near": False,
            "missing": [],
            "detail": f"高値更新直後・押し待ち{suffix}",
        }
    if status == "REJECTED":
        flags = vcp_result.get("must_flags") or {}
        missing = [k for k, v in flags.items() if not v]
        return {
            "stage": "rejected",
            "near": len(missing) == 1,
            "missing": missing,
            "detail": "VCP未達: " + "/".join(missing) if missing else "VCP未達",
        }
    if status == "TOO_VOLATILE":
        return {
            "stage": "volatile",
            "near": False,
            "missing": [],
            "detail": "ボラティリティ過大 (評価対象外)",
        }
    if status == "NO_BASE":
        return {
            "stage": "no_base",
            "near": False,
            "missing": [],
            "detail": "基準となる高値/ベースなし",
        }
    return None  # WATCH_A/B・BREAKOUT等のactionableステータス


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
    source_freshness: dict | None = None,
) -> dict:
    """report.json を組み立てて書き出す。

    source_freshness (2026-07-17追加、省略可=後方互換): データソースごとの
    最終成功日 {"jquants": {"last_success": ...}, "edinetdb": {...}, "prices": {...}}。
    パイプライン側 (pipeline.run_daily) が state ファイルから組み立てて渡す。
    """
    ordered = sorted(stocks, key=_sort_key)
    report = {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "universe_size": universe_size,
        "template_pass": template_pass,
        "priority_counts": priority_counts,
        "p1_scarce": p1_scarce,
        "data_warnings": data_warnings or {
            "failed_tickers": [], "stale_tickers": [], "csv_errors": [],
            "fundamentals_mismatch": [],
        },
        "source_freshness": source_freshness,
        "stocks": ordered,
    }
    write_docs_json(REPORT_PATH, report)
    return report


# ---------------------------------------------------------------------------
# 6. Breadth meter
# ---------------------------------------------------------------------------

def load_breadth() -> dict:
    return read_docs_json(BREADTH_PATH, default={"history": []})


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
    market_signal: dict | None = None,
    vcp_funnel: dict | None = None,
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
    if market_signal is not None:
        entry.update(market_signal)
    if vcp_funnel is not None:
        # VCP評価対象(P1)の origin/status 分布を地合い観測用に記録。
        # 二段目リーダーが高値更新中(TOO_RECENT)で土俵に乗らない比率などを追う。
        entry["vcp_funnel"] = vcp_funnel
    breadth["history"] = [h for h in breadth["history"] if h.get("date") != date_str]
    breadth["history"].append(entry)
    breadth["history"] = breadth["history"][-keep_days:]
    write_docs_json(BREADTH_PATH, breadth)
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
    write_docs_json(CHARTS_DIR / f"{code}.json", chart_data)


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
        write_docs_json(BREADTH_PATH, {"history": []})
