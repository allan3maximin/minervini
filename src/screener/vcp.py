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
            # 熟成中でも形成途中のベースをチャート描画できるよう base_df / t0_date
            # を返す(判定は行わないので呼び出し側は描画用途にのみ使う)。
            return {
                "status": "immature",
                "base_days": base_days,
                "days_from_high": days_from_high,
                "base_df": base_df,
                "t0_date": base_df["date"].iloc[0] if "date" in base_df.columns else None,
            }

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
    raw = max(vcp_cfg["zigzag_min_pct"], atr_pct)
    cap = vcp_cfg.get("swing_th_cap")
    return min(raw, cap) if cap else raw


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
    #
    # 2026-07-25 修正: 判定を idx から **type** に変えた。base_df[0] は探索窓の最高値
    # なので、確定するH ピボットの idx は必ず 0 になる。つまり「先頭が L」のケースでは
    # その L の idx も 0 であり、旧条件 `pivots[0]["idx"] != 0` は一度も発火しなかった。
    # 先頭が L になるのは、ベース初日が広いレンジ足で、その安値が最初のスイング安値と
    # して確定する(= 高値から threshold 下がるより先に安値から threshold 戻す)ケース。
    # ベース最高値の日はしばしば大陽線なので珍しくなく、実測(400営業日/992銘柄)で
    # 全ベースの 23.1% が該当し、ベース最高値そのものが収縮列から消えていた。
    # 消えると T 数・V2(単調性の基準)・V3(第1収縮の深さ)・V7 が、本来より低い
    # 別の高値を基準に計算される(乖離の中央値 1.54% / p90 5.54%)。
    # 先頭が L のときに H@0 を挿す形なので、ピボット列の H/L 交互性は常に保たれる。
    if not pivots or pivots[0]["type"] != "H":
        pivots.insert(0, {"idx": 0, "price": highs[0], "type": "H"})

    return pivots


def _fold_pair(p: list[dict], i: int, low_nb: dict | None, high_nb: dict | None) -> None:
    """Remove pivots p[i], p[i+1] in place, absorbing their extremes into the
    given neighbours so the H/L envelope survives the merge (a merge, not a
    delete).

    Absorption moves a neighbour's `idx` as well as its price. If both sides
    absorb at once (the removed pair holds both a higher high and a lower low
    than its neighbours) the two neighbours can swap positions on the time
    axis, leaving consecutive contractions overlapping in time -- which makes
    the drawn ZigZag run backwards and the chart disagree with the contraction
    list. Any absorption that would break the pivot list's chronological order
    is therefore rolled back; the price envelope is only widened when it can be
    done without reordering time.
    """
    saved = [(nb, nb["price"], nb["idx"]) for nb in (low_nb, high_nb) if nb is not None]
    removed = (p[i], p[i + 1])
    rem_high = max(removed, key=lambda pv: pv["price"])
    rem_low = min(removed, key=lambda pv: pv["price"])
    if low_nb is not None and low_nb["type"] == "L" and rem_low["price"] < low_nb["price"]:
        low_nb["price"], low_nb["idx"] = rem_low["price"], rem_low["idx"]
    if high_nb is not None and high_nb["type"] == "H" and rem_high["price"] > high_nb["price"]:
        high_nb["price"], high_nb["idx"] = rem_high["price"], rem_high["idx"]
    del p[i:i + 2]

    # Roll back (low first -- the base's high envelope is the more important of
    # the two) until the pivot list is chronological again.
    for nb, price, idx in saved:
        if all(p[k]["idx"] <= p[k + 1]["idx"] for k in range(len(p) - 1)):
            break
        nb["price"], nb["idx"] = price, idx


def merge_shallow_pivots(pivots: list[dict], min_depth: float) -> list[dict]:
    """Envelope-preserving merge of sub-`min_depth` swings.

    Over-segmentation (a ZigZag that registers minor intra-base wiggles as
    separate contractions) inflates the contraction count and mechanically
    fails V1(count)/V2(monotonic)/V7(no-undercut). This folds away the
    shallowest leg below `min_depth` and repeats: the removed pair's extreme
    high/low are absorbed into the retained neighbours so the H/L envelope is
    preserved (a merge, not a delete). T0 (index 0) and the final -- possibly
    provisional -- pivot are never dropped, so the base's anchor and the
    still-forming last contraction stay intact.
    """
    if not min_depth or len(pivots) < 4:
        return pivots
    p = [dict(pv) for pv in pivots]
    # Only interior legs are eligible (need both i-1 and i+2 to exist), which
    # also protects index 0 (T0) and the last two pivots (final contraction).
    while len(p) >= 4:
        shallow = None
        for i in range(1, len(p) - 2):
            hi = max(p[i]["price"], p[i + 1]["price"])
            if hi <= 0:
                continue
            depth = abs(p[i]["price"] - p[i + 1]["price"]) / hi
            if depth < min_depth and (shallow is None or depth < shallow[1]):
                shallow = (i, depth)
        if shallow is None:
            break
        i = shallow[0]
        nbs = (p[i - 1], p[i + 2])
        low_nb = next((nb for nb in nbs if nb["type"] == "L"), None)
        high_nb = next((nb for nb in nbs if nb["type"] == "H"), None)
        _fold_pair(p, i, low_nb, high_nb)
    return p


def merge_short_contractions(pivots: list[dict], min_bars: int) -> list[dict]:
    """Fold away contractions shorter than `min_bars` trading days.

    Minervini's contractions are corrections that take days to weeks to form;
    a single bar's high-to-low range is not a contraction. ZigZag, however,
    promotes any bar whose own range clears the swing threshold straight into
    an H and an L pivot, producing zero-day "contractions" (high_idx ==
    low_idx). Those inflate the footprint's T count, and feed V1/V2/V4/V7 and
    the entry pivot with what is really one bar of intraday noise.

    Duration is measured on the down-leg only (H -> L, i.e. the contraction
    itself); the rallies between contractions are left alone. Folding is the
    same envelope-preserving merge `merge_shallow_pivots` uses. The first
    contraction can be folded too -- T0 is the base's highest high, so it is
    always re-absorbed by the following high and the base keeps its anchor --
    and so can the last, which is the common case (a still-forming final leg
    that is only a bar or two old is not yet a contraction). Folding stops
    while two pivots remain, so a base is never emptied out.
    """
    if not min_bars or len(pivots) < 4:
        return pivots
    p = [dict(pv) for pv in pivots]
    while len(p) >= 4:
        target = None
        # H→L のペアだけが収縮。ピボット列は必ず交互なので、先頭がL始まり
        # (T0直後に安値ピボットが立つケース)でも1つずつ見れば正しく拾える。
        for j in range(len(p) - 1):
            if p[j]["type"] != "H" or p[j + 1]["type"] != "L":
                continue
            bars = p[j + 1]["idx"] - p[j]["idx"]
            if bars < min_bars and (target is None or bars < target[1]):
                target = (j, bars)
        if target is None:
            break
        j = target[0]
        # Interior contractions sit between the previous low and the next high.
        # At the edges the missing side falls back to the nearest same-type
        # pivot so the extreme still has somewhere to go.
        low_nb = p[j - 1] if j >= 1 else (p[j + 3] if j + 3 < len(p) else None)
        high_nb = p[j + 2] if j + 2 < len(p) else (p[j - 2] if j >= 2 else None)
        _fold_pair(p, j, low_nb, high_nb)
    return p


def merge_short_rallies(pivots: list[dict], min_bars: int) -> list[dict]:
    """Fold away rallies (L -> H) shorter than `min_bars` trading days.

    The mirror image of `merge_short_contractions`. A VCP is "high, pull back,
    rally back to a lower high, pull back to a higher low" -- the rally between
    two contractions takes time just like the pull-back does. When the low of
    contraction N and the high of contraction N+1 land on the same bar the
    rally leg is zero days long, which means one wide bar has been split into
    two waves. On the chart that shows up as T(N) and T(N+1) labelled on the
    same day.

    Folding uses the same envelope-preserving merge, with the neighbours
    mirrored: a rally sits between the previous high and the next low.
    """
    if not min_bars or len(pivots) < 4:
        return pivots
    p = [dict(pv) for pv in pivots]
    while len(p) >= 4:
        target = None
        # L→H のペアだけが戻し脚。ピボット列は必ず交互だが先頭の型は決め打ち
        # できないので、収縮側と同じく1つずつ型を見て拾う。
        for j in range(len(p) - 1):
            if p[j]["type"] != "L" or p[j + 1]["type"] != "H":
                continue
            bars = p[j + 1]["idx"] - p[j]["idx"]
            if bars < min_bars and (target is None or bars < target[1]):
                target = (j, bars)
        if target is None:
            break
        j = target[0]
        # 戻し脚は「前の高値」と「次の安値」に挟まれる(収縮の逆)。端では同型の
        # 最寄りピボットへフォールバックして極値の行き先を確保する。
        high_nb = p[j - 1] if j >= 1 else (p[j + 3] if j + 3 < len(p) else None)
        low_nb = p[j + 2] if j + 2 < len(p) else (p[j - 2] if j >= 2 else None)
        _fold_pair(p, j, low_nb, high_nb)
    return p


# 収縮マージと戻しマージは互いに新しい短脚を生み得るので、変化が止まるまで
# 交互に回す。上限は暴走時の保険(1ベースのピボットは高々数十本)。
_MAX_MERGE_PASSES = 20


def merge_short_legs(pivots: list[dict], min_bars: int, min_rally_bars: int) -> list[dict]:
    """Apply the contraction / rally duration floors until the pivot list settles."""
    for _ in range(_MAX_MERGE_PASSES):
        before = len(pivots)
        pivots = merge_short_contractions(pivots, min_bars)
        pivots = merge_short_rallies(pivots, min_rally_bars)
        if len(pivots) == before:
            break
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

def _is_first_half(idx: int, total: int) -> bool:
    """Shared "base前半" convention: position idx (0-based, out of total) sits
    in the first half of the base iff idx/total < 0.5. Used by V2's
    early-violation allowance and V5(b)'s volume-trend sub-condition so both
    conditions agree on what "前半" means.
    """
    return bool(total) and (idx / total) < 0.5


def _volume_regression_slope(base_df: pd.DataFrame) -> float | None:
    """Linear-regression slope of base_df's volume series. Shared by
    _volume_trend_score (SCORE) and V5(b) (MUST) so both read the same trend.
    """
    volume = base_df["volume"].to_numpy(dtype="float64")
    if len(volume) < 2:
        return None
    xs = np.arange(len(volume))
    return float(np.polyfit(xs, volume, 1)[0])


def _check_v2(contractions: list[dict], base_days: int, config: dict) -> tuple[bool, dict]:
    """V2: contraction depths must be non-increasing (within tolerance).

    Up to `early_violation_allowance` tolerance-exceeding reversals are
    forgiven, but only if they occur in the base's first half (by the
    reversal's low-pivot position) -- a reversal in the second half, or a 2nd+
    reversal anywhere, still fails V2. Regardless of the above, an
    `overall_contraction_ratio` backstop requires the final contraction to be
    materially tighter than the first.
    """
    vcp_cfg = config["vcp"]
    depths = [c["depth"] for c in contractions]
    n = len(depths)
    tol = vcp_cfg["monotonic_tolerance"]
    allowance = vcp_cfg["early_violation_allowance"]

    violations = []
    for i in range(1, n):
        if depths[i - 1] > 0 and depths[i] > depths[i - 1] * tol:
            first_half = _is_first_half(contractions[i]["low_idx"], base_days)
            violations.append({"index": i, "first_half": first_half})

    second_half_violations = [v for v in violations if not v["first_half"]]
    first_half_violations = [v for v in violations if v["first_half"]]

    if n < 2:
        step_pass = n >= 1
    elif second_half_violations:
        step_pass = False
    else:
        step_pass = len(first_half_violations) <= allowance

    overall_ratio = (depths[-1] / depths[0]) if (n >= 1 and depths[0] > 0) else None
    overall_ratio_threshold = vcp_cfg["overall_contraction_ratio"]
    backstop_failed = overall_ratio is not None and overall_ratio >= overall_ratio_threshold

    v2 = bool(step_pass and not backstop_failed)
    diag = {
        "violation_count": len(violations),
        "first_half_violation_count": len(first_half_violations),
        "second_half_violation_count": len(second_half_violations),
        "overall_ratio": overall_ratio,
        "overall_ratio_threshold": overall_ratio_threshold,
        "backstop_failed": backstop_failed,
    }
    return v2, diag


def _check_v5(base_df: pd.DataFrame, config: dict) -> tuple[bool, dict]:
    """V5: volume dry-up, OR of two sub-conditions.

    (a) the recent-10-day volume MEDIAN (robust to a single spike day) is at
    or below vol_ma50 * volume_dryup_median_ratio.
    (b) the base's overall volume trend is declining (negative regression
    slope) and the recent-10-day median is at or below the base's first-half
    volume median * volume_trend_ratio (i.e. a clear declining trend, even if
    the absolute level hasn't reached (a)'s bar yet).

    `base_df`'s last row is always the same as the full history's last row
    (see find_base_origin), so recent-10-day figures can be read directly off
    base_df without needing the full df.
    """
    vcp_cfg = config["vcp"]
    vol_ma50 = float(base_df["vol_ma50"].iloc[-1]) if len(base_df) and "vol_ma50" in base_df else None
    recent10 = base_df["volume"].tail(10)
    recent10_median = float(recent10.median()) if len(recent10) else None

    median_ratio_threshold = vcp_cfg["volume_dryup_median_ratio"]
    sub_a_pass = bool(recent10_median is not None and vol_ma50 and recent10_median <= vol_ma50 * median_ratio_threshold)

    n = len(base_df)
    first_half_mask = [_is_first_half(i, n) for i in range(n)]
    first_half_volume = base_df["volume"][first_half_mask]
    first_half_median = float(first_half_volume.median()) if len(first_half_volume) else None
    slope = _volume_regression_slope(base_df)
    trend_ratio_threshold = vcp_cfg["volume_trend_ratio"]
    sub_b_pass = bool(
        slope is not None and slope < 0
        and recent10_median is not None and first_half_median is not None
        and recent10_median <= first_half_median * trend_ratio_threshold
    )

    v5 = sub_a_pass or sub_b_pass
    diag = {
        "recent10_median": recent10_median,
        "vol_ma50": vol_ma50,
        "median_ratio_threshold": median_ratio_threshold,
        "sub_a_pass": sub_a_pass,
        "volume_slope": slope,
        "first_half_median": first_half_median,
        "trend_ratio_threshold": trend_ratio_threshold,
        "sub_b_pass": sub_b_pass,
        "bonus_eligible": bool(sub_a_pass and sub_b_pass),
    }
    return v5, diag


def _check_v7(contractions: list[dict], config: dict) -> tuple[bool, dict]:
    """V7: swing lows must not undercut. Tolerance is intentionally
    unchanged (0.99) -- a prior backtest showed relaxing it hurt setup
    quality. Two additions on top of the unchanged pairwise check:

    - an absolute floor: the final swing low can never be the base's new
      minimum, even via compounding of the per-step tolerance across many
      contractions.
    - a score-only "shakeout" detection (does not affect V7 itself): a tiny
      undercut that still satisfies the unchanged tolerance, followed by a
      rally past the pre-dip high, is a healthy shakeout rather than a
      breakdown.
    """
    low_tol = config["vcp"]["swing_low_tolerance"]
    lows = [c["low_price"] for c in contractions]
    n = len(lows)

    pairwise_pass = all(lows[i] >= lows[i - 1] * low_tol for i in range(1, n)) if n >= 2 else True
    floor_pass = (lows[-1] >= min(lows[:-1])) if n >= 2 else True
    v7 = bool(pairwise_pass and floor_pass)

    shakeout_detected = False
    shakeout_index = None
    for i in range(1, n - 1):
        if lows[i - 1] and low_tol <= (lows[i] / lows[i - 1]) < 1.0:
            if contractions[i + 1]["high_price"] > contractions[i]["high_price"]:
                shakeout_detected = True
                shakeout_index = i
                break

    diag = {
        "pairwise_pass": pairwise_pass,
        "floor_pass": floor_pass,
        "shakeout_detected": shakeout_detected,
        "shakeout_index": shakeout_index,
    }
    return v7, diag


def check_vcp_must_conditions(
    contractions: list[dict],
    base_days: int,
    base_df: pd.DataFrame,
    config: dict | None = None,
) -> tuple[dict[str, bool], dict]:
    config = config or load_config()
    vcp_cfg = config["vcp"]
    depths = [c["depth"] for c in contractions]
    n = len(depths)

    v1 = vcp_cfg["contraction_count"][0] <= n <= vcp_cfg["contraction_count"][1]
    v2, v2_diag = _check_v2(contractions, base_days, config)
    v3 = bool(depths[0] <= vcp_cfg["first_depth_max"]) if n >= 1 else False
    v4 = bool(depths[-1] <= vcp_cfg["last_depth_max"]) if n >= 1 else False
    v5, v5_diag = _check_v5(base_df, config)
    v6 = vcp_cfg["base_min_days"] <= base_days <= vcp_cfg["base_max_days"]
    v7, v7_diag = _check_v7(contractions, config)

    flags = {"V1": v1, "V2": v2, "V3": v3, "V4": v4, "V5": v5, "V6": v6, "V7": v7}
    diagnostics = {
        "v2": v2_diag,
        "v4": {"depth_last": depths[-1] if n >= 1 else None, "threshold": vcp_cfg["last_depth_max"]},
        "v5": v5_diag,
        "v7": v7_diag,
    }
    return flags, diagnostics


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


def _volume_trend_score(
    base_df: pd.DataFrame, weight: float, config: dict, bonus_eligible: bool = False
) -> float:
    volume = base_df["volume"].to_numpy(dtype="float64")
    if len(volume) < 2 or volume.mean() == 0:
        return 0.0
    slope = _volume_regression_slope(base_df)
    norm_slope = slope / volume.mean()
    full_score_norm_slope = config["vcp"].get("vol_trend_full_score_norm_slope", -0.008)
    raw = linear_score(norm_slope, 0.0, full_score_norm_slope, weight)
    if not bonus_eligible or weight <= 0:
        return raw
    bonus_fraction = config["vcp"].get("vol_trend_bonus_fraction", 0.0)
    fraction = min(1.0, raw / weight + bonus_fraction)
    return fraction * weight


def vcp_quality_score(
    contractions: list[dict],
    base_days: int,
    base_df: pd.DataFrame,
    config: dict | None = None,
    diagnostics: dict | None = None,
) -> dict:
    config = config or load_config()
    vcp_cfg = config["vcp"]
    w = vcp_cfg["score_weights"]
    depths = [c["depth"] for c in contractions]
    diagnostics = diagnostics or {}

    tightness = (
        linear_score(depths[-1], vcp_cfg["last_depth_max"], vcp_cfg["last_depth_perfect"], w["tightness"])
        if depths
        else 0.0
    )
    # 案Y(2026-07-15採用): tightness加点は「枯れ」銘柄にのみ与える。dryup_med_10_50
    # (=V5(a)の recent10_median/vol_ma50。再算出せずV5診断から取得し系列ドリフトを回避)が
    # dryup.dryup_badge_mild 以上(=枯れ不足)なら加点をゼロにする。足切りではなく加点の条件化。
    # dryup_med が取れない場合は保守側(従来どおり加点)。
    if tightness > 0 and vcp_cfg.get("tightness_requires_dryup"):
        v5d = diagnostics.get("v5", {})
        r10, vma = v5d.get("recent10_median"), v5d.get("vol_ma50")
        dryup_med = (r10 / vma) if (r10 is not None and vma) else None
        mild = config.get("dryup", {}).get("dryup_badge_mild", 0.77)
        if dryup_med is not None and dryup_med >= mild:
            tightness = 0.0

    if len(depths) >= 2:
        ratios = [depths[i] / depths[i - 1] for i in range(1, len(depths)) if depths[i - 1] > 0]
        avg_ratio = sum(ratios) / len(ratios) if ratios else 1.0
    else:
        avg_ratio = 1.0
    halving = linear_score(avg_ratio, 1.0, 0.5, w["halving"])

    bonus_eligible = bool(diagnostics.get("v5", {}).get("bonus_eligible"))
    vol_trend = _volume_trend_score(base_df, w["vol_trend"], config, bonus_eligible=bonus_eligible)

    first_depth = linear_score(depths[0], 0.35, 0.15, w["first_depth"]) if depths else 0.0

    duration = _duration_score(base_days, w["duration"])

    shakeout_detected = bool(diagnostics.get("v7", {}).get("shakeout_detected"))
    shakeout_bonus_points = vcp_cfg.get("shakeout_bonus", 0) if shakeout_detected else 0.0

    raw_total = tightness + halving + vol_trend + first_depth + duration
    total = round(min(100.0, raw_total + shakeout_bonus_points), 1)
    applied_shakeout_bonus = round(total - round(raw_total, 1), 2)
    return {
        "vcp_score": total,
        "shakeout_detected": shakeout_detected,
        "components": {
            "tightness": round(tightness, 2),
            "halving": round(halving, 2),
            "vol_trend": round(vol_trend, 2),
            "first_depth": round(first_depth, 2),
            "duration": round(duration, 2),
            "shakeout_bonus": applied_shakeout_bonus,
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

def _compute_dated_contractions(
    base_df: pd.DataFrame, latest: dict, config: dict
) -> list[dict]:
    """ZigZag→浅いピボット統合→収縮抽出→日付付与、をまとめて実行する。

    通常評価(status ok)とIMMATURE(描画専用)の両方から使う共通処理。日付は
    report/charts JSON にそのまま乗せられるよう文字列化する。
    """
    threshold = zigzag_swing_threshold(latest, config)
    pivots = compute_zigzag(base_df, threshold)
    pivots = merge_shallow_pivots(pivots, config["vcp"].get("min_contraction_depth", 0.0))
    pivots = merge_short_legs(
        pivots,
        config["vcp"].get("min_contraction_bars", 0),
        config["vcp"].get("min_rally_bars", 0),
    )
    contractions = extract_contractions(pivots)
    if "date" in base_df.columns:
        dates = base_df["date"]
        for c in contractions:
            c["high_date"] = pd.Timestamp(dates.iloc[c["high_idx"]]).strftime("%Y-%m-%d")
            c["low_date"] = pd.Timestamp(dates.iloc[c["low_idx"]]).strftime("%Y-%m-%d")
    return contractions


def evaluate_vcp(df: pd.DataFrame, config: dict | None = None) -> dict:
    """Run the full VCP pipeline for one stock's price history.

    `df` must already contain atr20 and vol_ma50 columns (see indicators.py).
    """
    config = config or load_config()

    latest = df.iloc[-1].to_dict()

    # ボラ過大チェック(V判定より前): ATR/close が閾値超の銘柄はZigZagがノイズを
    # 収縮として拾いすぎ V1/V2/V7 を機械的に落とすため、土俵から外す。
    excl = config["vcp"].get("atr_exclude_threshold")
    close = latest.get("close")
    atr_ratio = (latest["atr20"] / close) if (close and latest.get("atr20") is not None) else None
    if excl and atr_ratio is not None and atr_ratio > excl:
        return {
            "status": "TOO_VOLATILE",
            "must_flags": None,
            "vcp_score": None,
            "base_days": None,
            "days_from_high": None,
            "atr_ratio": round(atr_ratio, 4),
        }

    origin = find_base_origin(df, config)
    if origin["status"] != "ok":
        result = {
            "status": origin["status"].upper(),
            "must_flags": None,
            "vcp_score": None,
            # サマリー生成用の文脈: IMMATUREなら形成中のベース日数、TOO_RECENT/
            # IMMATUREなら高値からの経過日数が入る(該当しない場合はNone)。
            "base_days": origin.get("base_days"),
            "days_from_high": origin.get("days_from_high"),
        }
        # IMMATURE(ベース熟成中)は形成途中の収縮をチャートに描画できるよう
        # contractions を付与する。V1-V7判定・スコア・footprint・エントリー計算
        # には一切使わない(それらは status=="WATCH_A" 等でガード済み)。
        if origin["status"] == "immature" and origin.get("base_df") is not None:
            result["t0_date"] = origin.get("t0_date")
            result["contractions"] = _compute_dated_contractions(
                origin["base_df"], latest, config
            )
        return result

    base_df = origin["base_df"]
    base_days = origin["base_days"]

    contractions = _compute_dated_contractions(base_df, latest, config)

    flags, diagnostics = check_vcp_must_conditions(contractions, base_days, base_df, config)
    status = vcp_status(flags)

    result = {
        "status": status,
        "must_flags": flags,
        "base_days": base_days,
        "days_from_high": origin.get("days_from_high"),
        "t0_date": origin.get("t0_date"),
        "contractions": contractions,
        "footprint": footprint_string(contractions, base_days),
        "shakeout_detected": diagnostics["v7"]["shakeout_detected"],
        "vcp_diagnostics": diagnostics,
    }

    if status in ("WATCH_A", "WATCH_B"):
        score = vcp_quality_score(contractions, base_days, base_df, config, diagnostics=diagnostics)
        result.update(score)
    else:
        result["vcp_score"] = None

    return result
