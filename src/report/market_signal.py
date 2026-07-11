"""地合いシグナル(市場ブレッドス指標 + 攻め/中立/守りの3段階表示)。

日次バッチで計算し、build_site.update_breadth() 経由で breadth.json の history
エントリへ格納する。失敗しても本体を止めない設計(呼び出し元のpipeline.pyがtry/except)。
"""
from __future__ import annotations

import pandas as pd

from src.data import indices as indices_mod

DEFAULTS = {
    "green_pct_above_ma200": 0.50,
    "red_pct_above_ma200": 0.30,
}

# TOPIXトレンド判定に必要な最小営業日数 (MA200 + 21営業日前比較分)
_MIN_INDEX_DAYS = 221


def _cfg(config: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update(config.get("market_signal", {}) or {})
    return merged


def compute_breadth_stats(latest_by_code: dict) -> dict:
    """全銘柄の最新行(close/ma50/ma200/high/low/high_52w/low_52w)からブレッドス指標を計算。

    各列がNaN/欠損の銘柄は該当する分母・カウントから除外する。
    """
    above_ma200 = above_ma50 = total_ma200 = total_ma50 = 0
    new_high_count = new_low_count = 0

    for latest in latest_by_code.values():
        close = latest.get("close")
        ma200 = latest.get("ma200")
        ma50 = latest.get("ma50")
        high = latest.get("high")
        low = latest.get("low")
        high_52w = latest.get("high_52w")
        low_52w = latest.get("low_52w")

        if not pd.isna(ma200) and not pd.isna(close):
            total_ma200 += 1
            if close > ma200:
                above_ma200 += 1
        if not pd.isna(ma50) and not pd.isna(close):
            total_ma50 += 1
            if close > ma50:
                above_ma50 += 1
        if not pd.isna(high_52w) and not pd.isna(high) and high >= high_52w:
            new_high_count += 1
        if not pd.isna(low_52w) and not pd.isna(low) and low <= low_52w:
            new_low_count += 1

    return {
        "pct_above_ma200": round(above_ma200 / total_ma200, 4) if total_ma200 else None,
        "pct_above_ma50": round(above_ma50 / total_ma50, 4) if total_ma50 else None,
        "new_high_count": new_high_count,
        "new_low_count": new_low_count,
    }


def compute_index_trend(index_df: pd.DataFrame | None) -> dict | None:
    """TOPIX日足終値からトレンド判定。データ不足(221営業日未満)ならNone(判定不能)。"""
    if index_df is None or len(index_df) < _MIN_INDEX_DAYS:
        return None
    close = index_df["close"]
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    if pd.isna(ma200.iloc[-1]) or pd.isna(ma200.iloc[-22]):
        return None
    return {
        "index_above_ma50": bool(close.iloc[-1] > ma50.iloc[-1]),
        "index_above_ma200": bool(close.iloc[-1] > ma200.iloc[-1]),
        "index_ma200_slope_up": bool(ma200.iloc[-1] > ma200.iloc[-22]),
    }


def compute_market_signal(latest_by_code: dict, config: dict, index_df: pd.DataFrame | None = None) -> dict:
    """機能: 市場ブレッドス + 指数トレンドを合成した攻め/中立/守りシグナル。

    index_df を省略した場合は indices_mod.load_cache("topix") を読む(テスト時は
    合成データを直接渡せるようにするための引数)。
    """
    cfg = _cfg(config)
    stats = compute_breadth_stats(latest_by_code)
    if index_df is None:
        index_df = indices_mod.load_cache("topix")
    trend = compute_index_trend(index_df)

    pct200 = stats["pct_above_ma200"]
    reasons: list[str] = []

    if trend is None:
        signal = "yellow"
        reasons.append("指数データ欠損のため判定不能")
        trend = {"index_above_ma50": None, "index_above_ma200": None, "index_ma200_slope_up": None}
    else:
        red_pct = cfg["red_pct_above_ma200"]
        green_pct = cfg["green_pct_above_ma200"]
        index_below_ma200 = trend["index_above_ma200"] is False
        breadth_weak = pct200 is not None and pct200 < red_pct

        if index_below_ma200 or breadth_weak:
            signal = "red"
            if index_below_ma200:
                reasons.append("TOPIXが200日線を下回っている")
            if breadth_weak:
                reasons.append(f"200日線上抜け銘柄が{pct200 * 100:.1f}%と少ない(赤閾値{red_pct * 100:.0f}%未満)")
        elif (
            trend["index_above_ma50"]
            and trend["index_above_ma200"]
            and trend["index_ma200_slope_up"]
            and pct200 is not None
            and pct200 >= green_pct
            and stats["new_high_count"] > stats["new_low_count"]
        ):
            signal = "green"
            reasons.append("TOPIXが50日線・200日線を上回り、200日線も上向き")
            reasons.append(f"200日線上抜け銘柄が{pct200 * 100:.1f}%(緑閾値{green_pct * 100:.0f}%以上)")
            reasons.append(f"新高値{stats['new_high_count']}件 > 新安値{stats['new_low_count']}件")
        else:
            signal = "yellow"
            reasons.append("明確な攻め/守りの条件を満たさず中立")

    return {
        "signal": signal,
        "reasons": reasons,
        "pct_above_ma200": stats["pct_above_ma200"],
        "pct_above_ma50": stats["pct_above_ma50"],
        "new_high_count": stats["new_high_count"],
        "new_low_count": stats["new_low_count"],
        "index_above_ma50": trend["index_above_ma50"],
        "index_above_ma200": trend["index_above_ma200"],
        "index_ma200_slope_up": trend["index_ma200_slope_up"],
    }
