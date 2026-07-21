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

from datetime import date, datetime

from src.config import REPO_ROOT, load_config
from src.utils_io import atomic_write_json, safe_load_json

STATUS_HISTORY_PATH = REPO_ROOT / "data" / "status_history.json"

# 「終値がピボットより上」を意味するステータス群。ブレイク鮮度(breakout_age_days)と
# クールダウン(extended_cooldown_ready)の判定で共有する。
POST_BREAKOUT_STATUSES = {"BREAKOUT", "BREAKOUT_WEAK", "EXTENDED", "STALE"}


def _parse_date(value) -> date | None:
    """history/latest_row の日付表現(ISO文字列 / date / datetime / pandas Timestamp)を
    date に正規化する。パース不能なら None(呼び出し側は鮮度チェックをスキップ)。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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


def _current_pivot_streak(history: dict, code: str) -> list[dict]:
    """現在ロック中のピボット値を共有する末尾連続エントリ群(古い順)。
    ピボット値が変わった/無い所でストリークは切れる。"""
    entries = history.get(code, [])
    locked = None
    for e in reversed(entries):
        if e.get("pivot") is not None:
            locked = e["pivot"]
            break
    if locked is None:
        return []
    streak: list[dict] = []
    for e in reversed(entries):
        if e.get("pivot") != locked:
            break
        streak.append(e)
    streak.reverse()
    return streak


def breakout_age_days(history: dict, code: str, today: date | None) -> int | None:
    """現在のピボットストリーク内で「最初にピボットを上抜けた日」から today までの
    経過日数(暦日)。当該ピボットで一度もブレイクしていなければ None。

    ブレイク鮮度チェック(2026-07-21): locked_pivot は status_history_days(90日)
    生き続けるため、これが無いと何週間も前のブレイクの残骸ピボットに対して毎日
    BREAKOUT/BREAKOUT_WEAK を出し続ける(ゾンビピボット問題)。ミネルヴィニの
    買いチャンスはピボット突破から数日以内であり、それを逃したら次のベース待ち。"""
    if today is None:
        return None
    for e in _current_pivot_streak(history, code):
        if e.get("status") in POST_BREAKOUT_STATUSES:
            first = _parse_date(e.get("date"))
            if first is not None:
                return (today - first).days
    return None


def extended_cooldown_ready(history: dict, code: str, config: dict | None = None) -> bool:
    """True once a stock has been continuously EXTENDED/STALE for >= cooldown
    days, signaling the locked pivot should be dropped so the stock is
    re-scanned from Phase 2 for a new base. STALE(ブレイク鮮度切れ)もEXTENDEDと
    同じ「追いかけ禁止・新ベース待ち」状態なのでカウントに含める。"""
    config = config or load_config()
    cooldown = config["entry"]["extended_cooldown_days"]
    count = 0
    for entry in reversed(history.get(code, [])):
        if entry["status"] in ("EXTENDED", "STALE"):
            count += 1
        else:
            break
    return count >= cooldown


def lock_drop_reason(
    history: dict, code: str, close: float, locked: dict, today: date | None,
    config: dict | None = None,
) -> str | None:
    """ロック済みピボットを無効化すべきなら理由文字列を返す(有効なら None)。

    - "cooldown":    EXTENDED/STALE が extended_cooldown_days 日連続 → 新ベース待ち。
      (2026-07-21まで extended_cooldown_ready はどこからも呼ばれない死にコードで、
      ロックが90日間無条件に生き続けていた)
    - "base_failed": 終値がロック時の水準から計算した損切りラインを下回った。
      ブレイク失敗/ベース崩壊であり「WATCH_A(突破待ち)」を出し続けるのは誤り。
    - "gap":         最後の記録から pivot_lock_max_gap_days 暦日超の空白。P1落ち等で
      追跡が途切れた古いピボットの資源化(復活)を防ぐ。"""
    e = (config or load_config())["entry"]

    if extended_cooldown_ready(history, code, config):
        return "cooldown"

    levels = compute_pivot_levels(locked["pivot"], locked.get("stop_ref_low"), config)
    stop = levels.get("stop_loss")
    if stop is None:
        stop = locked["pivot"] * (1 - e["stop_loss_pct"])
    if close < stop:
        return "base_failed"

    max_gap = e.get("pivot_lock_max_gap_days")
    if max_gap is not None and today is not None:
        entries = history.get(code, [])
        last_date = _parse_date(entries[-1].get("date")) if entries else None
        if last_date is not None and (today - last_date).days > max_gap:
            return "gap"

    return None


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
    today = _parse_date(latest_row.get("date"))

    pivot = None
    stop_ref_low = None
    from_lock = False

    if vcp_result.get("status") == "WATCH_A" and vcp_result.get("contractions"):
        last_c = vcp_result["contractions"][-1]
        pivot = last_c["high_price"]
        stop_ref_low = last_c["low_price"]
    else:
        locked = locked_pivot(history, code)
        if locked:
            reason = lock_drop_reason(history, code, close, locked, today, config)
            if reason:
                return {
                    "status": vcp_result.get("status", "NO_SETUP"),
                    "pivot": None,
                    "lock_dropped": reason,
                }
            pivot = locked["pivot"]
            stop_ref_low = locked["stop_ref_low"]
            from_lock = True

    if pivot is None:
        return {"status": vcp_result.get("status", "NO_SETUP"), "pivot": None}

    status_info = determine_entry_status(close, volume, vol_ma50, pivot, config)
    levels = compute_pivot_levels(pivot, stop_ref_low, config)

    # ブレイク鮮度チェック(ゾンビピボット対策): ロック済みピボットの初回ブレイクから
    # breakout_stale_days 暦日を超えたら、BREAKOUT/BREAKOUT_WEAK/WATCH_A を STALE に
    # 落とす(EXTENDED は価格ベースの追いかけ禁止としてそのまま)。STALE は
    # extended_cooldown_ready のカウント対象なので、cooldown 日数後にロック自体が
    # 破棄され、新しいベース形成(WATCH_A)を待つ状態に戻る。フレッシュなVCPスキャンが
    # WATCH_A を返した場合(from_lock=False)は新ベースなので対象外。
    if from_lock and status_info["status"] != "EXTENDED":
        stale_days = config["entry"].get("breakout_stale_days")
        age = breakout_age_days(history, code, today)
        if stale_days is not None and age is not None and age >= stale_days:
            status_info = {**status_info, "status": "STALE", "breakout_age_days": age}

    return {
        **status_info,
        **levels,
        "dist_to_pivot": dist_to_pivot_pct(pivot, close),
    }
