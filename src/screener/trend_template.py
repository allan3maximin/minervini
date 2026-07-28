"""Phase 1: trend template MUST filter + quality scoring (design doc section 3)."""
from __future__ import annotations

import numpy as np
import pandas as pd

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


# ---------------------------------------------------------------------------
# tech_score (2026-07-29 全面作り直し)
#
# 旧スコアは RS 54.5% / near_high 27.3% / MA200上向き日数 18.2% の固定重みと
# ハードコードされた境界(70→99, 0→105, 高値25%幅)でできていた。26年(2000-2026)
# のデータで診断したところ、MUST通過集合の中ではこの3変数はいずれも期待Rの幅が
# 0.02〜0.03R しかなく、局面別の符号も 5勝5敗〜7勝4敗 とコイン投げだった。
# near_high に至っては単独で使うと期待R +0.078 と無条件ベースライン +0.126 を
# 下回る(11局面中1勝)。つまり旧スコアは直近2年の局面に合わせて配分された
# カーブフィッティングであり、上位20%(+0.117)が自分の上位50%(+0.140)にも
# 無条件ベースライン(+0.126)にも負けるという、選別力が負の状態にあった。
#
# 新スコアは26年を通して符号が安定していた3変数だけを使う:
#   - ma200_slope_21d      : MA200の21日上昇率(強度)   幅 +0.277R / 8勝1敗
#   - close / low_52w      : 52週安値からの倍率         幅 +0.216R / 6勝4敗
#   - dryup_med_10_50 の反転: 出来高の枯れ度(小さいほど良) 幅 +0.142R / 9勝2敗
#
# 設計上の縛りが2つある。どちらも「26年フィットで2年フィットを置き換えるだけ」に
# ならないための歯止めなので、後から緩めないこと。
#   (1) 等ウェイト。重み探索をしない(Dawes 1979: improper linear model の頑健性)。
#   (2) 正規化は当日の断面パーセンタイル。閾値・境界値を一切持たない。母集団は
#       「その日のMUST通過銘柄」に限る -- 効果量をこの部分集合の中で測ったので、
#       採点も同じ部分集合の中で行わないと測定と運用がズレる。
#
# RS と near_high(52週高値からの距離)は MUST フィルタとしては残す。「通過に
# 必要な条件」と「通過者の順位付けに効く変数」は別物であり、アブレーションでは
# 両者をスコアに足し戻すと期待Rが +0.209 → +0.199 → +0.177 と下がった。
# 根拠の全文は log.md (140)。
# ---------------------------------------------------------------------------

SCORE_COMPONENT_NAMES = ("ma200_slope", "low52w_ratio", "dryup")


def score_variables(latest: dict) -> dict[str, float | None]:
    """スコア3変数の生値。いずれも「大きいほど良い」向きに符号を揃えて返す。

    dryup は小さい(出来高が枯れている)ほど良いので符号を反転させる。値が
    欠けている(履歴不足など)場合は None。
    """
    slope = latest.get("ma200_slope_21d")
    close = latest.get("close")
    low_52w = latest.get("low_52w")
    dryup = latest.get("dryup_med_10_50")

    low_ratio = close / low_52w if close is not None and low_52w else None
    return {
        "ma200_slope": _finite(slope),
        "low52w_ratio": _finite(low_ratio),
        "dryup": (-_finite(dryup)) if _finite(dryup) is not None else None,
    }


def _finite(v) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if f != f else f  # NaN


def attach_score_percentiles(
    latest_by_code: dict[str, dict], config: dict | None = None
) -> dict[str, dict[str, float]]:
    """その日のMUST通過銘柄の中で3変数を断面パーセンタイル(0-100)に変換し、
    各 latest の "score_pct" に書き込む(破壊的)。

    母集団をMUST通過銘柄に限るのが要点。落ちる銘柄まで含めて順位付けすると、
    効果量を測った集合と採点する集合が食い違う。MUSTを落ちた銘柄には
    score_pct を付けない(= tech_score は None)。
    """
    config = config or load_config()
    passers = [
        code
        for code, latest in latest_by_code.items()
        if passes_trend_template(check_must_conditions(latest, config))
    ]
    values = {code: score_variables(latest_by_code[code]) for code in passers}

    pct_by_code: dict[str, dict[str, float]] = {code: {} for code in passers}
    for name in SCORE_COMPONENT_NAMES:
        series = pd.Series(
            {code: values[code][name] for code in passers}, dtype="float64"
        ).dropna()
        if series.empty:
            continue
        ranks = series.rank(pct=True, method="average") * 100.0
        for code, pct in ranks.items():
            pct_by_code[code][name] = round(float(pct), 1)

    for code in passers:
        latest_by_code[code]["score_pct"] = pct_by_code[code]
    return pct_by_code


def technical_score_raw(latest: dict, config: dict | None = None) -> dict[str, float]:
    """attach_score_percentiles が付けた断面パーセンタイル成分。

    等ウェイトなので「成分 = そのままパーセンタイル値」。config の重みは見ない。
    """
    pct = latest.get("score_pct") or {}
    return {k: v for k, v in pct.items() if v is not None}


def technical_score(latest: dict, config: dict | None = None) -> float | None:
    """技術面スコア(0-100)。3変数の断面パーセンタイルの等ウェイト平均。

    attach_score_percentiles を先に通していない latest には計算できないので
    None を返す(断面が要るスコアなので、単独銘柄からは原理的に出せない)。
    """
    raw = technical_score_raw(latest, config)
    if not raw:
        return None
    return round(sum(raw.values()) / len(raw), 1)


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

    # 技術面は tech_score(0-100)を w["technical"] 点満点に写すだけ。断面が無くて
    # tech_score が出せない場合は技術成分を欠測扱いにし、renormalize_to_100 が
    # ファンダ成分だけで100点満点に割り戻す(部分カバレッジと同じ扱い)。
    tech_max = w["technical"]
    tech = technical_score(latest, config)
    tech_points = None if tech is None else tech / 100.0 * tech_max

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
    # tech_score は当日の断面ランクなので、個別判定に入る前に一括で付与する。
    attach_score_percentiles(latest_by_code, config)
    results = []
    for code, latest in latest_by_code.items():
        flags = check_must_conditions(latest, config)
        passed = passes_trend_template(flags)
        entry = {"code": code, "must_flags": flags, "passed": passed}
        if passed:
            entry["tech_score"] = technical_score(latest, config)
        results.append(entry)
    return results
