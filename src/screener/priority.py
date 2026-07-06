"""機能A: トレンドテンプレート距離ベースのプライオリティ判定 (P1〜P4).

ハードフィルタ(3条件)を1つでも落とした銘柄は対象外(None)。
残りはソフト条件のペナルティ合計でP1〜P4に分類する:

    P1 = 0点  (8条件完全一致相当)
    P2 = 1点
    P3 = 2〜3点
    P4 = 4点以上

ペナルティ:
    現在値 <= 50日線                 +1
    52週高値からの距離 25〜35%圏      +1
    52週高値からの距離 35%超          +2
    50日線 <= 150日線 (並び崩れ)      +2
    RS < 70 (60以上 +1 / 60未満 +3)

単純な合格数カウントによるスコアリングは行わない。
"""
from __future__ import annotations

from src.config import load_config

# 未達条件キー (フロント側で日本語ラベルにマップする)
COND_CLOSE_ABOVE_MA50 = "close_above_ma50"
COND_NEAR_HIGH52W = "near_high52w"
COND_MA50_ABOVE_MA150 = "ma50_above_ma150"
COND_RS_MIN = "rs_above_min"


def _pct(value: float, base: float) -> float | None:
    """base に対する value の乖離率(%). base が無効なら None."""
    if base is None or base == 0:
        return None
    return round((value / base - 1.0) * 100.0, 2)


def passes_hard_filters(latest: dict, config: dict | None = None) -> bool:
    """ハードフィルタ3条件。1つでも未達なら対象外。

    1. 150日線 > 200日線
    2. 200日線が1ヶ月以上上向き
    3. 現在値が52週安値から +25% 以上 (config trend_template.low52w_margin)
    """
    config = config or load_config()
    tt = config["trend_template"]
    close = latest["close"]
    return bool(
        latest["ma150"] > latest["ma200"]
        and latest["ma200_slope_days"] >= tt["ma200_up_days_min"]
        and close >= latest["low_52w"] * tt["low52w_margin"]
    )


def evaluate_priority(latest: dict, config: dict | None = None) -> dict | None:
    """1銘柄のプライオリティ評価。

    ハードフィルタ未達なら None。
    通過銘柄は以下を返す:
        penalty:       ペナルティ合計
        priority:      1〜4
        unmet:         未達ソフト条件のリスト [{condition, penalty, distance_pct}]
        ma_deviation_pct: {ma50, ma150, ma200} 現在値の各MA乖離率(%)
        high52w_distance_pct: 52週高値からの距離(%)
        rs: RSパーセンタイル
    """
    config = config or load_config()
    tt = config["trend_template"]
    pr = config.get("priority", {})
    mid_pct = pr.get("high_dist_mid_pct", 25)
    bad_pct = pr.get("high_dist_bad_pct", 35)
    rs_soft_min = pr.get("rs_soft_min", 60)

    if not passes_hard_filters(latest, config):
        return None

    close = latest["close"]
    ma50 = latest["ma50"]
    ma150 = latest["ma150"]
    high52 = latest["high_52w"]
    rs = latest["rs"]

    penalty = 0
    unmet: list[dict] = []

    # (1) 現在値 <= 50日線: +1
    if close <= ma50:
        penalty += 1
        unmet.append(
            {"condition": COND_CLOSE_ABOVE_MA50, "penalty": 1, "distance_pct": _pct(close, ma50)}
        )

    # (2) 52週高値からの距離: 25〜35%圏 +1 / 35%超 +2
    high_dist = round((high52 - close) / high52 * 100.0, 2) if high52 else None
    if high_dist is not None and high_dist > mid_pct:
        p = 2 if high_dist > bad_pct else 1
        penalty += p
        unmet.append({"condition": COND_NEAR_HIGH52W, "penalty": p, "distance_pct": high_dist})

    # (3) 50日線 <= 150日線 (並び崩れ): +2
    if ma50 <= ma150:
        penalty += 2
        unmet.append(
            {"condition": COND_MA50_ABOVE_MA150, "penalty": 2, "distance_pct": _pct(ma50, ma150)}
        )

    # (4) RS < 70: 60以上 +1 / 60未満 +3
    if rs < tt["rs_min"]:
        p = 1 if rs >= rs_soft_min else 3
        penalty += p
        unmet.append(
            {"condition": COND_RS_MIN, "penalty": p, "distance_pct": round(rs - tt["rs_min"], 2)}
        )

    if penalty == 0:
        priority = 1
    elif penalty == 1:
        priority = 2
    elif penalty <= 3:
        priority = 3
    else:
        priority = 4

    return {
        "penalty": penalty,
        "priority": priority,
        "unmet": unmet,
        "ma_deviation_pct": {
            "ma50": _pct(close, ma50),
            "ma150": _pct(close, ma150),
            "ma200": _pct(close, latest["ma200"]),
        },
        "high52w_distance_pct": high_dist,
        "rs": rs,
    }


def priority_sort_key(record: dict) -> tuple:
    """並び順: プライオリティ昇順 -> RS降順."""
    return (record.get("priority") or 99, -(record.get("rs") or 0.0))


def priority_counts(evaluations: list[dict | None]) -> dict[str, int]:
    """地合い指標用: 実行ごとのP1〜P4件数集計."""
    counts = {"p1": 0, "p2": 0, "p3": 0, "p4": 0}
    for ev in evaluations:
        if ev is None:
            continue
        counts[f"p{ev['priority']}"] += 1
    return counts
