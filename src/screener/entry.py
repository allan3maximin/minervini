"""Phase 3: entry support -- pivot/stop levels, status classification, status
history (design doc section 5).

Status classification note: the design doc lists BREAKOUT, BREAKOUT_WEAK,
EXTENDED, WATCH_A in that numbered order, but a literal first-match-wins
reading of that order makes EXTENDED unreachable (BREAKOUT/BREAKOUT_WEAK
already cover every close > pivot case). We check EXTENDED first among the
close > pivot cases instead: once a stock has run more than `extended_pct`
past the pivot it's a "don't chase" regardless of volume, which matches the
doc's own description of EXTENDED as an automatic downgrade.
"""
from __future__ import annotations

from src.config import REPO_ROOT, load_config
from src.utils_io import atomic_write_json, safe_load_json

STATUS_HISTORY_PATH = REPO_ROOT / "data" / "status_history.json"


# ---------------------------------------------------------------------------
# 5.1 Pivot / stop levels
# ---------------------------------------------------------------------------

def tick_size(price: float, config: dict | None = None) -> float:
    config = config or load_config()
    table = config["entry"]["tick_table"]
    for max_price, tick in table:
        if price <= max_price:
            return tick
    return table[-1][1]


def compute_pivot_levels(pivot: float, stop_ref_low: float | None, config: dict | None = None) -> dict:
    config = config or load_config()
    e = config["entry"]
    buy_stop = pivot + tick_size(pivot, config)

    if stop_ref_low is not None:
        stop_loss = max(pivot * (1 - e["stop_loss_pct"]), stop_ref_low * 0.995)
        risk_pct = round((buy_stop - stop_loss) / buy_stop * 100, 2)
        stop_loss = round(stop_loss, 2)
    else:
        stop_loss = None
        risk_pct = None

    return {
        "pivot": pivot,
        "buy_stop": round(buy_stop, 2),
        "stop_loss": stop_loss,
        "risk_pct": risk_pct,
    }


def dist_to_pivot_pct(pivot: float, close: float) -> float:
    return round((pivot - close) / pivot * 100, 2)


# ---------------------------------------------------------------------------
# 5.2 Status classification
# ---------------------------------------------------------------------------

def determine_entry_status(
    close: float, volume: float, vol_ma50: float | None, pivot: float, config: dict | None = None
) -> dict:
    config = config or load_config()
    e = config["entry"]
    vol_mult = (volume / vol_ma50) if vol_ma50 else 0.0

    if close <= pivot:
        return {"status": "WATCH_A", "volume_multiple": round(vol_mult, 2)}

    if close > pivot * (1 + e["extended_pct"]):
        return {"status": "EXTENDED", "volume_multiple": round(vol_mult, 2)}
    if vol_mult >= e["breakout_vol_mult"]:
        return {"status": "BREAKOUT", "volume_multiple": round(vol_mult, 2)}
    return {"status": "BREAKOUT_WEAK", "volume_multiple": round(vol_mult, 2)}


def market_guard_triggered(topix_return: float, config: dict | None = None) -> bool:
    """`topix_return` is a fraction (e.g. -0.02 for -2%)."""
    config = config or load_config()
    return topix_return <= config["entry"]["market_guard_pct"]


# ---------------------------------------------------------------------------
# 5.3 Status history
# ---------------------------------------------------------------------------

def load_status_history() -> dict:
    # 破損時は空dictから再構築(warningはsafe_load_json側でprintされる)。
    return safe_load_json(STATUS_HISTORY_PATH, {})


def save_status_history(history: dict) -> None:
    atomic_write_json(STATUS_HISTORY_PATH, history, indent=2)


def record_status(
    history: dict,
    code: str,
    date_str: str,
    status: str,
    pivot: float | None,
    stop_ref_low: float | None,
    config: dict | None = None,
) -> dict:
    config = config or load_config()
    keep_days = config["entry"]["status_history_days"]
    entries = [e for e in history.get(code, []) if e.get("date") != date_str]
    entries.append({"date": date_str, "status": status, "pivot": pivot, "stop_ref_low": stop_ref_low})
    history[code] = entries[-keep_days:]
    return history


def previous_status(history: dict, code: str) -> str | None:
    entries = history.get(code, [])
    return entries[-1]["status"] if entries else None


def locked_pivot(history: dict, code: str) -> dict | None:
    """Most recently recorded {pivot, stop_ref_low}, carried forward from the
    last day the stock was WATCH_A, so post-breakout days can still compute
    entry metrics even though a fresh VCP scan no longer sees that base."""
    for entry in reversed(history.get(code, [])):
        if entry.get("pivot") is not None:
            return {"pivot": entry["pivot"], "stop_ref_low": entry.get("stop_ref_low")}
    return None


def is_new_breakout(history: dict, code: str, today_status: str) -> bool:
    """True if the stock was WATCH_A as of the last recorded day and is
    BREAKOUT today. Must be called with `history` *before* today's entry is
    appended via record_status."""
    return previous_status(history, code) == "WATCH_A" and today_status == "BREAKOUT"


def extended_cooldown_ready(history: dict, code: str, config: dict | None = None) -> bool:
    """True once a stock has been continuously EXTENDED for >= cooldown days,
    signaling it's eligible to be re-scanned from Phase 2 for a new base."""
    config = config or load_config()
    cooldown = config["entry"]["extended_cooldown_days"]
    count = 0
    for entry in reversed(history.get(code, [])):
        if entry["status"] == "EXTENDED":
            count += 1
        else:
            break
    return count >= cooldown


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def evaluate_entry(
    code: str,
    latest_row: dict,
    vcp_result: dict,
    history: dict,
    config: dict | None = None,
) -> dict:
    config = config or load_config()
    close = latest_row["close"]
    volume = latest_row["volume"]
    vol_ma50 = latest_row.get("vol_ma50")

    pivot = None
    stop_ref_low = None

    if vcp_result.get("status") == "WATCH_A" and vcp_result.get("contractions"):
        last_c = vcp_result["contractions"][-1]
        pivot = last_c["high_price"]
        stop_ref_low = last_c["low_price"]
    else:
        locked = locked_pivot(history, code)
        if locked:
            pivot = locked["pivot"]
            stop_ref_low = locked["stop_ref_low"]

    if pivot is None:
        return {"status": vcp_result.get("status", "NO_SETUP"), "pivot": None}

    status_info = determine_entry_status(close, volume, vol_ma50, pivot, config)
    levels = compute_pivot_levels(pivot, stop_ref_low, config)

    return {
        **status_info,
        **levels,
        "dist_to_pivot": dist_to_pivot_pct(pivot, close),
    }
