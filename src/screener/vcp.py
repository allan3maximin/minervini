"""Phase 2: VCP (Volatility Contraction Pattern) detection (design doc section 4).

Pipeline: base-area identification -> ZigZag contraction extraction -> V1-V7
MUST filter -> setup-quality SCORE -> footprint string.

`df` throughout is expected to be a single stock's OHLCV+indicator history,
sorted ascending by date, with columns: date, open, high, low, close, volume,
atr20, vol_ma50 (see src/indicators.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import load_config
from src.screener.scoring import linear_score


# ---------------------------------------------------------------------------
# 4.1 Base area identification
# ---------------------------------------------------------------------------

def find_base_origin(df: pd.DataFrame, config: dict | None = None) -> dict:
    """Locate the base origin T0 (the scan window's highest high).

    Returns a dict with either {"status": "no_base"} / {"status": "too_recent"}
    / {"status": "immature", "base_days": int} or
    {"status": "ok", "base_df": DataFrame, "base_days": int, "t0_date": ...}.
    """
    config = config or load_config()
    vcp_cfg = config["vcp"]
    windows = [vcp_cfg["scan_days"], vcp_cfg["scan_days_extended"]]
    min_days_from_high = vcp_cfg["min_days_from_high"]
    base_min_days = vcp_cfg["base_min_days"]

    for i, window in enumerate(windows):
        is_last_window = i == len(windows) - 1
        if len(df) < window:
            if is_last_window:
                return {"status": "no_base"}
            continue

        window_df = df.tail(window).reset_index(drop=True)
        t0_idx = int(np.argmax(window_df["high"].to_numpy()))

        if t0_idx == 0:
            # peak sits at the window's edge; the true peak may lie further
            # back in history than this window covers.
            if is_last_window:
                return {"status": "no_base"}
            continue

        days_from_high = len(window_df) - 1 - t0_idx
        if days_from_high < min_days_from_high:
            return {"status": "too_recent", "days_from_high": days_from_high}

        base_df = window_df.iloc[t0_idx:].reset_index(drop=True)
        base_days = len(base_df)
        if base_days < base_min_days:
            return {"status": "immature", "base_days": base_days, "days_from_high": days_from_high}

        return {
            "status": "ok",
            "base_df": base_df,
            "base_days": base_days,
            "days_from_high": days_from_high,
            "t0_date": base_df["date"].iloc[0] if "date" in base_df.columns else None,
        }

    return {"status": "no_base"}


# ---------------------------------------------------------------------------
# 4.2 ZigZag contraction extraction
# ---------------------------------------------------------------------------

def zigzag_swing_threshold(latest_row: dict, config: dict | None = None) -> float:
    config = config or load_config()
    vcp_cfg = config["vcp"]
    atr_pct = vcp_cfg["zigzag_atr_mult"] * latest_row["atr20"] / latest_row["close"]
    return max(vcp_cfg["zigzag_min_pct"], atr_pct)


def compute_zigzag(base_df: pd.DataFrame, threshold: float) -> list[dict]:
    """Standard 2-state ZigZag over the base region.

    Because `base_df` starts at T0 (the highest high in the scan window by
    construction), the first confirmed pivot is always that high. The final
    in-progress leg is appended as a provisional pivot only when the stock is
    currently pulling back (trend == 'down') and hasn't yet reversed by
    `threshold` -- this is the common "still forming" final contraction that a
    VCP scanner needs to catch before the breakout happens, per design doc
    4.2.3. If instead the trend is 'up' at the end, the last confirmed low is
    already a complete, unambiguous contraction and needs no adjustment.
    """
    # Plain Python floats, not numpy scalars: comparisons on numpy scalars
    # return numpy.bool_, which json.dump cannot serialize downstream.
    highs = [float(v) for v in base_df["high"].to_numpy()]
    lows = [float(v) for v in base_df["low"].to_numpy()]
    n = len(base_df)
    if n < 2:
        return []

    pivots: list[dict] = []
    trend: str | None = None
    last_high_idx, last_high = 0, highs[0]
    last_low_idx, last_low = 0, lows[0]

    for i in range(1, n):
        if trend is None:
            if highs[i] >= last_low * (1 + threshold):
                pivots.append({"idx": last_low_idx, "price": last_low, "type": "L"})
                trend = "up"
                last_high_idx, last_high = i, highs[i]
            elif lows[i] <= last_high * (1 - threshold):
                pivots.append({"idx": last_high_idx, "price": last_high, "type": "H"})
                trend = "down"
                last_low_idx, last_low = i, lows[i]
            else:
                if highs[i] > last_high:
                    last_high_idx, last_high = i, highs[i]
                if lows[i] < last_low:
                    last_low_idx, last_low = i, lows[i]
        elif trend == "up":
            if highs[i] > last_high:
                last_high_idx, last_high = i, highs[i]
            if lows[i] <= last_high * (1 - threshold):
                pivots.append({"idx": last_high_idx, "price": last_high, "type": "H"})
                trend = "down"
                last_low_idx, last_low = i, lows[i]
        elif trend == "down":
            if lows[i] < last_low:
                last_low_idx, last_low = i, lows[i]
            if highs[i] >= last_low * (1 + threshold):
                pivots.append({"idx": last_low_idx, "price": last_low, "type": "L"})
                trend = "up"
                last_high_idx, last_high = i, highs[i]

    if trend == "down":
        pivots.append({"idx": last_low_idx, "price": last_low, "type": "L", "provisional": True})

    # Always ensure T0 (index 0) is represented as the first pivot.
    if not pivots or pivots[0]["idx"] != 0:
        pivots.insert(0, {"idx": 0, "price": highs[0], "type": "H"})

    return pivots


def extract_contractions(pivots: list[dict]) -> list[dict]:
    """Pair adjacent (H, L) pivots into contractions, in chronological order."""
    contractions = []
    i = 0
    while i + 1 < len(pivots):
        h, low = pivots[i], pivots[i + 1]
        if h["type"] != "H" or low["type"] != "L":
            i += 1
            continue
        depth = (h["price"] - low["price"]) / h["price"] if h["price"] else 0.0
        contractions.append(
            {
                "high_idx": h["idx"],
                "high_price": h["price"],
                "low_idx": low["idx"],
                "low_price": low["price"],
                "depth": depth,
                "provisional": bool(low.get("provisional", False)),
            }
        )
        i += 2
    return contractions


# ---------------------------------------------------------------------------
# 4.3 MUST conditions (V1-V7)
# ---------------------------------------------------------------------------

def check_vcp_must_conditions(
    contractions: list[dict],
    base_days: int,
    latest_row: dict,
    config: dict | None = None,
) -> dict[str, bool]:
    config = config or load_config()
    vcp_cfg = config["vcp"]
    depths = [c["depth"] for c in contractions]
    n = len(depths)

    v1 = vcp_cfg["contraction_count"][0] <= n <= vcp_cfg["contraction_count"][1]

    tol = vcp_cfg["monotonic_tolerance"]
    v2 = all(depths[i] <= depths[i - 1] * tol for i in range(1, n)) if n >= 2 else n >= 1

    v3 = bool(depths[0] <= vcp_cfg["first_depth_max"]) if n >= 1 else False
    v4 = bool(depths[-1] <= vcp_cfg["last_depth_max"]) if n >= 1 else False

    recent_vol = latest_row.get("recent10_vol_avg")
    vol_ma50 = latest_row.get("vol_ma50")
    v5 = bool(recent_vol is not None and vol_ma50 and recent_vol <= vol_ma50 * vcp_cfg["volume_dryup_ratio"])

    v6 = vcp_cfg["base_min_days"] <= base_days <= vcp_cfg["base_max_days"]

    low_tol = vcp_cfg["swing_low_tolerance"]
    lows = [c["low_price"] for c in contractions]
    v7 = all(lows[i] >= lows[i - 1] * low_tol for i in range(1, len(lows))) if len(lows) >= 2 else True

    return {"V1": v1, "V2": v2, "V3": v3, "V4": v4, "V5": v5, "V6": v6, "V7": v7}


def vcp_status(flags: dict[str, bool]) -> str:
    if not flags["V3"] or not flags["V7"]:
        return "REJECTED"
    if all(flags.values()):
        return "WATCH_A"
    if flags["V1"] and flags["V3"] and flags["V6"] and flags["V7"]:
        return "WATCH_B"
    return "REJECTED"


# ---------------------------------------------------------------------------
# 4.4 SCORE (setup quality, 100 pts)
# ---------------------------------------------------------------------------

def _duration_score(base_days: int, weight: float) -> float:
    if 25 <= base_days <= 60:
        return weight
    if base_days < 25:
        return linear_score(base_days, 15, 25, weight)
    return linear_score(base_days, 200, 60, weight)


def _volume_trend_score(base_df: pd.DataFrame, weight: float, config: dict) -> float:
    volume = base_df["volume"].to_numpy(dtype="float64")
    if len(volume) < 2 or volume.mean() == 0:
        return 0.0
    xs = np.arange(len(volume))
    slope = float(np.polyfit(xs, volume, 1)[0])
    norm_slope = slope / volume.mean()
    full_score_norm_slope = config["vcp"].get("vol_trend_full_score_norm_slope", -0.008)
    return linear_score(norm_slope, 0.0, full_score_norm_slope, weight)


def vcp_quality_score(
    contractions: list[dict], base_days: int, base_df: pd.DataFrame, config: dict | None = None
) -> dict:
    config = config or load_config()
    w = config["vcp"]["score_weights"]
    depths = [c["depth"] for c in contractions]

    tightness = linear_score(depths[-1], 0.10, 0.03, w["tightness"]) if depths else 0.0

    if len(depths) >= 2:
        ratios = [depths[i] / depths[i - 1] for i in range(1, len(depths)) if depths[i - 1] > 0]
        avg_ratio = sum(ratios) / len(ratios) if ratios else 1.0
    else:
        avg_ratio = 1.0
    halving = linear_score(avg_ratio, 1.0, 0.5, w["halving"])

    vol_trend = _volume_trend_score(base_df, w["vol_trend"], config)

    first_depth = linear_score(depths[0], 0.35, 0.15, w["first_depth"]) if depths else 0.0

    duration = _duration_score(base_days, w["duration"])

    total = round(tightness + halving + vol_trend + first_depth + duration, 1)
    return {
        "vcp_score": total,
        "components": {
            "tightness": round(tightness, 2),
            "halving": round(halving, 2),
            "vol_trend": round(vol_trend, 2),
            "first_depth": round(first_depth, 2),
            "duration": round(duration, 2),
        },
    }


# ---------------------------------------------------------------------------
# 4.5 Footprint string
# ---------------------------------------------------------------------------

def footprint_string(contractions: list[dict], base_days: int) -> str:
    weeks = round(base_days / 5)
    depth_pcts = "/".join(str(round(c["depth"] * 100)) for c in contractions)
    return f"{weeks}W {depth_pcts} {len(contractions)}T"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def evaluate_vcp(df: pd.DataFrame, config: dict | None = None) -> dict:
    """Run the full VCP pipeline for one stock's price history.

    `df` must already contain atr20 and vol_ma50 columns (see indicators.py).
    """
    config = config or load_config()

    origin = find_base_origin(df, config)
    if origin["status"] != "ok":
        return {
            "status": origin["status"].upper(),
            "must_flags": None,
            "vcp_score": None,
            # サマリー生成用の文脈: IMMATUREなら形成中のベース日数、TOO_RECENT/
            # IMMATUREなら高値からの経過日数が入る(該当しない場合はNone)。
            "base_days": origin.get("base_days"),
            "days_from_high": origin.get("days_from_high"),
        }

    base_df = origin["base_df"]
    base_days = origin["base_days"]

    latest = df.iloc[-1].to_dict()
    latest["recent10_vol_avg"] = float(df["volume"].tail(10).mean())

    threshold = zigzag_swing_threshold(latest, config)
    pivots = compute_zigzag(base_df, threshold)
    contractions = extract_contractions(pivots)

    flags = check_vcp_must_conditions(contractions, base_days, latest, config)
    status = vcp_status(flags)

    result = {
        "status": status,
        "must_flags": flags,
        "base_days": base_days,
        "days_from_high": origin.get("days_from_high"),
        "t0_date": origin.get("t0_date"),
        "contractions": contractions,
        "footprint": footprint_string(contractions, base_days),
    }

    if status in ("WATCH_A", "WATCH_B"):
        score = vcp_quality_score(contractions, base_days, base_df, config)
        result.update(score)
    else:
        result["vcp_score"] = None

    return result
