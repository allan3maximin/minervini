"""Phase 1: trend template MUST filter + quality scoring (design doc section 3)."""
from __future__ import annotations

import numpy as np

from src.config import load_config
from src.screener.scoring import linear_score, renormalize_to_100

MUST_CONDITION_NAMES = [
    "close_above_ma150_ma200",
    "ma150_above_ma200",
    "ma200_uptrend_1m",
    "ma_stack_50_150_200",
    "close_above_ma50",
    "above_low52w_margin",
    "within_high52w_margin",
    "rs_above_min",
]


def check_must_conditions(latest: dict, config: dict | None = None) -> dict[str, bool]:
    config = config or load_config()
    tt = config["trend_template"]
    close = latest["close"]
    return {
        "close_above_ma150_ma200": bool(close > latest["ma150"] and close > latest["ma200"]),
        "ma150_above_ma200": bool(latest["ma150"] > latest["ma200"]),
        "ma200_uptrend_1m": bool(latest["ma200_slope_days"] >= tt["ma200_up_days_min"]),
        "ma_stack_50_150_200": bool(latest["ma50"] > latest["ma150"] > latest["ma200"]),
        "close_above_ma50": bool(close > latest["ma50"]),
        "above_low52w_margin": bool(close >= latest["low_52w"] * tt["low52w_margin"]),
        "within_high52w_margin": bool(close >= latest["high_52w"] * tt["high52w_margin"]),
        "rs_above_min": bool(latest["rs"] >= tt["rs_min"]),
    }


def passes_trend_template(flags: dict[str, bool]) -> bool:
    return all(flags.values())


def technical_score_raw(latest: dict, config: dict | None = None) -> dict[str, float]:
    """The 3 technical-quality point components (max rs=30, ma200_days=10, near_high=15)."""
    config = config or load_config()
    w = config["trend_template"]["score_weights"]
    rs = latest["rs"]
    slope_days = latest["ma200_slope_days"]
    high_52w = latest["high_52w"]
    close = latest["close"]

    rs_pts = linear_score(rs, 70, 99, w["rs"])
    ma200_days_pts = linear_score(min(slope_days, 105), 0, 105, w["ma200_days"])
    near_high_shortfall = (high_52w - close) / (high_52w * 0.25) if high_52w else 1.0
    near_high_pts = linear_score(1 - near_high_shortfall, 0, 1, w["near_high"])

    return {"rs": rs_pts, "ma200_days": ma200_days_pts, "near_high": near_high_pts}


def technical_score(latest: dict, config: dict | None = None) -> float:
    """Technical-only score (design doc 3.2 (1)), rescaled from 55 raw points to 100."""
    config = config or load_config()
    w = config["trend_template"]["score_weights"]
    raw = technical_score_raw(latest, config)
    max_pts = w["rs"] + w["ma200_days"] + w["near_high"]
    return round(sum(raw.values()) / max_pts * 100.0, 1)


def quarter_sort_key(fiscal_quarter: str) -> tuple[int, int]:
    year, q = fiscal_quarter.split("Q")
    return int(year), int(q)


def compute_accel_slope(quarters: list[dict], value_col: str) -> float | None:
    """Slope of the trailing-4-quarter YoY growth-rate series (design doc 3.3).

    `quarters` is a list of {"fiscal_quarter": "2026Q1", value_col: 45.2, ...},
    recommended 8 quarters so each of the last 4 has a year-ago counterpart.
    Returns None if fewer than 3 valid YoY growth rates can be computed.
    """
    by_q = {q["fiscal_quarter"]: q.get(value_col) for q in quarters}
    by_q = {k: v for k, v in by_q.items() if v is not None}
    sorted_keys = sorted(by_q.keys(), key=quarter_sort_key, reverse=True)

    growths = []  # (age, growth_pct); age 0 = most recent quarter
    for age in range(4):
        if age >= len(sorted_keys):
            break
        key = sorted_keys[age]
        year, q = quarter_sort_key(key)
        prior_key = f"{year - 1}Q{q}"
        prior_val = by_q.get(prior_key)
        if prior_val is None or prior_val <= 0:
            continue
        growth = (by_q[key] - prior_val) / abs(prior_val) * 100.0
        growths.append((age, growth))

    if len(growths) < 3:
        return None

    xs = [3 - age for age, _ in growths]
    ys = [g for _, g in growths]
    return float(np.polyfit(xs, ys, 1)[0])


def latest_yoy_growth(quarters: list[dict], value_col: str) -> float | None:
    by_q = {q["fiscal_quarter"]: q.get(value_col) for q in quarters}
    by_q = {k: v for k, v in by_q.items() if v is not None}
    if not by_q:
        return None
    latest_key = max(by_q.keys(), key=quarter_sort_key)
    year, q = quarter_sort_key(latest_key)
    prior_key = f"{year - 1}Q{q}"
    prior_val = by_q.get(prior_key)
    if prior_val is None or prior_val <= 0:
        return None
    return (by_q[latest_key] - prior_val) / abs(prior_val) * 100.0


def accel_score(slope: float | None, weight: float, latest_growth: float | None = None) -> float | None:
    if slope is None:
        return None
    score_frac = linear_score(slope, 0, 15, 1.0)
    if latest_growth is not None and latest_growth >= 30:
        score_frac = min(1.0, score_frac + 0.2)
    return round(score_frac * weight, 2)


def monthly_yoy_score(monthly_yoy: float | None, weight: float) -> float | None:
    if monthly_yoy is None:
        return None
    return round(linear_score(monthly_yoy, 0, 20, weight), 2)


def compute_full_score(
    latest: dict,
    eps_quarters: list[dict] | None,
    revenue_quarters: list[dict] | None,
    monthly_yoy: float | None,
    config: dict | None = None,
) -> dict:
    """Full score (design doc 3.2 (2)): technical 55pt + EPS/revenue accel + monthly YoY,
    renormalized to 100 when some fundamental inputs are unavailable ("partial")."""
    config = config or load_config()
    w = config["trend_template"]["score_weights"]

    tech_raw = technical_score_raw(latest, config)
    tech_points = sum(tech_raw.values())
    tech_max = w["rs"] + w["ma200_days"] + w["near_high"]

    eps_slope = compute_accel_slope(eps_quarters, "eps") if eps_quarters else None
    eps_latest_growth = latest_yoy_growth(eps_quarters, "eps") if eps_quarters else None
    eps_pts = accel_score(eps_slope, w["eps_accel"], eps_latest_growth)

    rev_slope = compute_accel_slope(revenue_quarters, "revenue") if revenue_quarters else None
    rev_pts = accel_score(rev_slope, w["rev_accel"])

    monthly_pts = monthly_yoy_score(monthly_yoy, w["monthly"])

    components = {
        "technical": (tech_points, tech_max),
        "eps_accel": (eps_pts, w["eps_accel"]),
        "rev_accel": (rev_pts, w["rev_accel"]),
        "monthly": (monthly_pts, w["monthly"]),
    }
    total, used_points = renormalize_to_100(components)
    return {
        "full_score": total,
        "eps_accel_slope": eps_slope,
        "rev_accel_slope": rev_slope,
        "component_points": used_points,
    }


def screen_universe(latest_by_code: dict[str, dict], config: dict | None = None) -> list[dict]:
    """Run the MUST filter over every stock's latest indicator row.

    Returns one entry per stock, including failed ones with their per-condition
    flags (design doc 3.1: needed for debugging/tuning), and the technical
    score for stocks that pass.
    """
    config = config or load_config()
    results = []
    for code, latest in latest_by_code.items():
        flags = check_must_conditions(latest, config)
        passed = passes_trend_template(flags)
        entry = {"code": code, "must_flags": flags, "passed": passed}
        if passed:
            entry["tech_score"] = technical_score(latest, config)
        results.append(entry)
    return results
