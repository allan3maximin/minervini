"""Common scoring helpers shared by trend_template.py and vcp.py.

Weighted linear mapping of a raw metric to a point value, plus renormalization
across a set of scoring components when some are unavailable (e.g. a stock
with only partial fundamentals data).
"""
from __future__ import annotations


def linear_score(value: float | None, low: float, high: float, weight: float) -> float:
    """Map `value` linearly from [low, high] -> [0, weight], clipped to that range.

    Works whether `high` > `low` (higher value = better) or `high` < `low`
    (lower value = better).
    """
    if value is None:
        return 0.0
    span = high - low
    if span == 0:
        return weight
    frac = (value - low) / span
    frac = max(0.0, min(1.0, frac))
    return frac * weight


def renormalize_to_100(
    components: dict[str, tuple[float | None, float]]
) -> tuple[float, dict[str, float]]:
    """Given {name: (points_or_None, max_points)}, sum the available points and
    rescale to a 100-point scale using only the weights of available
    components (design doc 3.2: partial fundamentals coverage).

    Returns (total_score_rounded, {name: points} for components actually used).
    """
    available = {k: v for k, v in components.items() if v[0] is not None}
    if not available:
        return 0.0, {}
    points_sum = sum(p for p, _ in available.values())
    weight_sum = sum(w for _, w in available.values())
    if weight_sum == 0:
        return 0.0, {k: v[0] for k, v in available.items()}
    total = points_sum / weight_sum * 100.0
    return round(total, 1), {k: v[0] for k, v in available.items()}


def combined_score(
    phase1_score: float, vcp_score: float | None, config: dict | None = None
) -> float:
    """Overall score = phase1_score * 0.5 + vcp_score * 0.5 (design doc 4.4).

    `phase1_score` は全ティア共通で tech_score(2026-07-22改定)。

    **vcp_score が None のときは 0 として扱う** (2026-07-29改定)。以前はこの場合
    total_score = phase1_score にフォールバックしていたが、それだと「テクニカル70・
    VCP70」の銘柄と「テクニカル70・VCPセットアップ未成立」の銘柄が同じ70になり、
    同じ列で並べられなかった(だからティアで隔離するしかなかった)。欠損を0に倒すと
    セットアップ未成立の銘柄は上限50に沈み、順序そのものが両者を分離する。

    注意: この配点は設計判断であって実測ではない。log.md (140) の「スコアに入れる
    変数は実測エッジのあるものだけ」は *変数の採否* のルールで、欠損値の扱いはその
    外側にある、という整理。詳細は log.md (143)。
    """
    from src.config import load_config

    config = config or load_config()
    w = config["scoring"]
    total = phase1_score * w["phase1_weight"] + (vcp_score or 0.0) * w["vcp_weight"]
    return round(total, 1)
