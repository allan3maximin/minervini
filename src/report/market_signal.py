"""地合いシグナル(市場ブレッドス指標 + 攻め/中立/守りの3段階表示)。

日次バッチで計算し、build_site.update_breadth() 経由で breadth.json の history
エントリへ格納する。失敗しても本体を止めない設計(呼び出し元のpipeline.pyがtry/except)。

2026-07-18(タスク3): 既存の green/yellow/red 判定ロジック(compute_market_signal内の
if/elif連鎖)は完全に維持したまま、多観点の詳細指標(騰落レシオ・NH-NL累積・
日経/グロース250含む指数マルチトレンド・グロース-TOPIX相対・0-100合成スコア
market_score・score_trend)を「追加」する。スコア合成はあくまで表示用の補助指標で、
green/yellow/red判定自体には一切使わない(既存挙動は不変)。
"""
from __future__ import annotations

import pandas as pd

from src.data import indices as indices_mod

DEFAULTS = {
    "green_pct_above_ma200": 0.50,
    "red_pct_above_ma200": 0.30,
    # 機能: market_score(0-100)のサブスコア配点。合計は100想定だが、正規化して
    # 使うので厳密に100でなくても動く。
    "detail_weights": {"breadth": 40, "index_trend": 30, "momentum": 20, "risk_appetite": 10},
    # 各サブスコアの線形クリップ境界(lo→0点 / hi→満点)。
    "detail_scale": {
        "breadth_lo": 0.2, "breadth_hi": 0.6,
        "up_down_ratio_lo": 0.7, "up_down_ratio_hi": 1.3,
        "breadth_trend_lo": -0.10, "breadth_trend_hi": 0.10,
        "growth_rel_lo": -5.0, "growth_rel_hi": 5.0,
    },
}

# TOPIXトレンド判定に必要な最小営業日数 (MA200 + 21営業日前比較分)
_MIN_INDEX_DAYS = 221

_N_DAY_RETURN = 20  # growth_rel_20d / breadth_trend_20d の窓
_UP_DOWN_WINDOW = 25  # up_down_ratio_25 の窓(history 24件 + 当日1件)
_TREND_LOOKBACK = 5  # score_trend の比較先(N エントリ前)


def _cfg(config: dict) -> dict:
    raw = config.get("market_signal", {}) or {}
    merged = dict(DEFAULTS)
    merged.update(raw)
    # ネストした辞書(detail_weights/detail_scale)は部分上書きをマージする
    # (config側で一部キーだけ指定してもデフォルトの残りが消えないように)。
    for key in ("detail_weights", "detail_scale"):
        merged[key] = {**DEFAULTS[key], **(raw.get(key) or {})}
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


def _n_day_return_pct(index_df: pd.DataFrame | None, days: int = _N_DAY_RETURN) -> float | None:
    """終値の直近N営業日リターン(%)。データ不足/欠損はNone(例外は投げない)。"""
    if index_df is None or "close" not in index_df or len(index_df) <= days:
        return None
    try:
        c0 = float(index_df["close"].iloc[-days - 1])
        c1 = float(index_df["close"].iloc[-1])
    except (ValueError, TypeError):
        return None
    if pd.isna(c0) or pd.isna(c1) or c0 == 0:
        return None
    return round((c1 / c0 - 1.0) * 100.0, 3)


def _up_down_ratio_25(breadth_today: dict | None, breadth_history: list | None) -> float | None:
    """直近25エントリ(history 24件 + 当日)の sum(advancers)/sum(decliners)。

    蓄積が25エントリ未満、当日値なし、decliners合計が0のいずれかならNone。
    """
    if not breadth_today or breadth_today.get("advancers") is None:
        return None
    entries = list(breadth_history or [])[-(_UP_DOWN_WINDOW - 1):] + [breadth_today]
    if len(entries) < _UP_DOWN_WINDOW:
        return None
    total_adv = sum(e.get("advancers") or 0 for e in entries)
    total_dec = sum(e.get("decliners") or 0 for e in entries)
    if not total_dec:
        return None
    return round(total_adv / total_dec, 3)


def _breadth_trend_20d(pct200_today: float | None, breadth_history: list | None) -> float | None:
    """pct_above_ma200(当日) − 同(20エントリ前)。履歴不足/欠損はNone。"""
    if pct200_today is None:
        return None
    hist = breadth_history or []
    if len(hist) < _N_DAY_RETURN:
        return None
    prev = hist[-_N_DAY_RETURN].get("pct_above_ma200")
    if prev is None:
        return None
    return round(pct200_today - prev, 4)


def _nh_nl_cumulative(net_today: int, breadth_history: list | None) -> int:
    """前回エントリのnh_nl_cumulative + 当日net。旧エントリ(フィールド無し)or
    履歴なしの場合は当日netから再スタート(null安全)。"""
    hist = breadth_history or []
    if not hist:
        return net_today
    prev_cum = hist[-1].get("nh_nl_cumulative")
    if prev_cum is None:
        return net_today
    return prev_cum + net_today


def _linear_score(value: float | None, lo: float, hi: float) -> float | None:
    """value を [lo, hi] -> [0, 100] に線形クリップ。value=Noneならスコア自体None
    (呼び出し側で中立50%へフォールバックするかどうかを判断する)。"""
    if value is None:
        return None
    if hi == lo:
        return 100.0 if value >= hi else 0.0
    frac = (value - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return round(frac * 100.0, 2)


def _index_trend_score(trends: list[dict | None]) -> float:
    """topix/nikkei225/growth250 の (above_ma50+above_ma200+slope_up) 計9判定の
    合格割合(%)。None判定(指数トレンド自体が計算不能)は分母から除外。
    全None(=1件も判定できない)なら中立50%。"""
    total = passed = 0
    for t in trends:
        if t is None:
            continue
        for key in ("index_above_ma50", "index_above_ma200", "index_ma200_slope_up"):
            v = t.get(key)
            if v is None:
                continue
            total += 1
            if v:
                passed += 1
    if total == 0:
        return 50.0
    return round(passed / total * 100.0, 2)


def _score_trend(market_score: float, breadth_history: list | None) -> str | None:
    """market_score − 5エントリ前のscore → improving/flat/deteriorating。
    履歴不足・比較先にmarket_scoreが無い(旧エントリ)場合はNone。"""
    hist = breadth_history or []
    if len(hist) < _TREND_LOOKBACK:
        return None
    prev_score = hist[-_TREND_LOOKBACK].get("market_score")
    if prev_score is None:
        return None
    diff = market_score - prev_score
    if diff > 3:
        return "improving"
    if diff < -3:
        return "deteriorating"
    return "flat"


def compute_market_signal(
    latest_by_code: dict,
    config: dict,
    index_df: pd.DataFrame | None = None,
    breadth_today: dict | None = None,
    breadth_history: list | None = None,
    nikkei_df: pd.DataFrame | None = None,
    growth_df: pd.DataFrame | None = None,
) -> dict:
    """機能: 市場ブレッドス + 指数トレンドを合成した攻め/中立/守りシグナル。

    index_df を省略した場合は indices_mod.load_cache("topix") を読む(テスト時は
    合成データを直接渡せるようにするための引数)。nikkei_df/growth_df も同様に
    省略時は data/indices/{nikkei225,growth250}.parquet のキャッシュを読む
    (2026-07-18タスク3で追加。多観点の指数トレンド用、追加取得なし)。

    breadth_today: {"advancers": int, "decliners": int} (当日値、pipeline.py が
    indicator_by_code のdf末尾2行終値比較でカウントして渡す。省略時はNone扱い)。
    breadth_history: build_site.load_breadth()["history"](当日エントリ追記前の
    既存履歴)。up_down_ratio_25/breadth_trend_20d/nh_nl_cumulative/score_trend の
    計算に使う。I/O(ファイル読み込み)はこの関数に持たせず、呼び出し元(pipeline.py)
    が読んで渡す設計(テスト容易性のため)。

    ここに追加する詳細指標・market_score はあくまで表示用の補助情報であり、
    既存の green/yellow/red 判定ロジック自体には一切影響しない(変更禁止の方針)。
    """
    cfg = _cfg(config)
    stats = compute_breadth_stats(latest_by_code)
    if index_df is None:
        index_df = indices_mod.load_cache("topix")
    if nikkei_df is None:
        nikkei_df = indices_mod.load_cache("nikkei225")
    if growth_df is None:
        growth_df = indices_mod.load_cache("growth250")
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

    # ---- ここから2026-07-18タスク3追加分(表示専用の詳細指標) ----
    advancers = (breadth_today or {}).get("advancers")
    decliners = (breadth_today or {}).get("decliners")
    up_down_ratio_25 = _up_down_ratio_25(breadth_today, breadth_history)
    breadth_trend_20d = _breadth_trend_20d(pct200, breadth_history)

    net_new_highs = stats["new_high_count"] - stats["new_low_count"]
    nh_nl_cumulative = _nh_nl_cumulative(net_new_highs, breadth_history)

    trend_nikkei = compute_index_trend(nikkei_df)
    trend_growth = compute_index_trend(growth_df)
    growth_return_20d = _n_day_return_pct(growth_df)
    topix_return_20d = _n_day_return_pct(index_df)
    growth_rel_20d = (
        round(growth_return_20d - topix_return_20d, 3)
        if growth_return_20d is not None and topix_return_20d is not None
        else None
    )

    scale = cfg["detail_scale"]
    weights = cfg["detail_weights"]

    breadth_score = _linear_score(pct200, scale["breadth_lo"], scale["breadth_hi"])
    if breadth_score is None:
        breadth_score = 50.0

    index_trend_score = _index_trend_score([trend, trend_nikkei, trend_growth])

    momentum_parts = []
    ud_score = _linear_score(up_down_ratio_25, scale["up_down_ratio_lo"], scale["up_down_ratio_hi"])
    if ud_score is not None:
        momentum_parts.append(ud_score)
    bt_score = _linear_score(breadth_trend_20d, scale["breadth_trend_lo"], scale["breadth_trend_hi"])
    if bt_score is not None:
        momentum_parts.append(bt_score)
    momentum_score = round(sum(momentum_parts) / len(momentum_parts), 2) if momentum_parts else 50.0

    risk_score = _linear_score(growth_rel_20d, scale["growth_rel_lo"], scale["growth_rel_hi"])
    if risk_score is None:
        risk_score = 50.0

    weight_total = sum(weights.values()) or 1
    market_score = round(
        (
            breadth_score * weights["breadth"]
            + index_trend_score * weights["index_trend"]
            + momentum_score * weights["momentum"]
            + risk_score * weights["risk_appetite"]
        )
        / weight_total,
        2,
    )
    score_trend = _score_trend(market_score, breadth_history)

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
        # --- タスク3追加(表示専用) ---
        "advancers": advancers,
        "decliners": decliners,
        "up_down_ratio_25": up_down_ratio_25,
        "breadth_trend_20d": breadth_trend_20d,
        "net_new_highs": net_new_highs,
        "nh_nl_cumulative": nh_nl_cumulative,
        "index_trends": {
            "topix": trend,
            "nikkei225": trend_nikkei,
            "growth250": trend_growth,
        },
        "growth_rel_20d": growth_rel_20d,
        "market_score": market_score,
        "score_breakdown": {
            "breadth": breadth_score,
            "index_trend": index_trend_score,
            "momentum": momentum_score,
            "risk_appetite": risk_score,
        },
        "score_trend": score_trend,
    }
