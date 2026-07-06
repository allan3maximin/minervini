"""機能B: セクターヒートマップ用データ生成 (docs/data/heatmap.json).

日次バッチで全計算を済ませ、フロント(heatmap.html)は描画のみ行う:
  - 銘柄別 1/5/20/60営業日リターン (価格キャッシュ由来、追加リクエストなし)
  - 時価総額 = 発行済株式数(月次取得) × 最新終値。取得不可は None (最小面積フォールバック)
  - TSE33業種で集計 (data/sector_map.json / universe.json 由来の静的マッピング)
  - セクターRS(対TOPIX): 強/中/弱 + 方向(↑/→/↓)。銘柄の独立属性でプライオリティには不参入
  - セクター集計履歴を data/sector_history.json に蓄積 (グラフ化は対象外)
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from src.config import REPO_ROOT, load_config

HEATMAP_PATH = REPO_ROOT / "docs" / "data" / "heatmap.json"
SECTOR_HISTORY_PATH = REPO_ROOT / "data" / "sector_history.json"
SECTOR_MAP_PATH = REPO_ROOT / "data" / "sector_map.json"

UNKNOWN_SECTOR = "その他"

DEFAULTS = {
    "periods": [1, 5, 20, 60],
    "strength_window": 20,
    "direction_window": 5,
    "sector_strong_rel_pct": 2.0,
    "sector_weak_rel_pct": -2.0,
    "direction_up_rel_pct": 0.5,
    "direction_down_rel_pct": -0.5,
    "history_keep_days": 400,
}


def _cfg(config: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update(config.get("heatmap", {}) or {})
    return merged


def compute_returns(close: pd.Series, periods: list[int]) -> dict[str, float | None]:
    """終値系列から各期間の騰落率(%)。データ不足期間は None。"""
    out: dict[str, float | None] = {}
    n = len(close)
    for p in periods:
        key = f"d{p}"
        if n >= p + 1:
            base = float(close.iloc[-(p + 1)])
            out[key] = round((float(close.iloc[-1]) / base - 1.0) * 100.0, 2) if base else None
        else:
            out[key] = None
    return out


def load_sector_map() -> dict[str, str]:
    if not SECTOR_MAP_PATH.exists():
        return {}
    with open(SECTOR_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("sectors", {})


def sector_rs(
    sector_returns: dict[str, float | None],
    topix_returns: dict[str, float | None],
    cfg: dict,
) -> dict:
    """セクターRS(対TOPIX相対): 強/中/弱 + 方向(↑/→/↓)。

    強弱:  strength_window(既定20日)の相対リターンで判定
    方向:  direction_window(既定5日)の相対リターンで判定
    """
    sk = f"d{cfg['strength_window']}"
    dk = f"d{cfg['direction_window']}"

    rel_strength = None
    if sector_returns.get(sk) is not None and topix_returns.get(sk) is not None:
        rel_strength = round(sector_returns[sk] - topix_returns[sk], 2)
    rel_direction = None
    if sector_returns.get(dk) is not None and topix_returns.get(dk) is not None:
        rel_direction = round(sector_returns[dk] - topix_returns[dk], 2)

    if rel_strength is None:
        strength = None
    elif rel_strength >= cfg["sector_strong_rel_pct"]:
        strength = "強"
    elif rel_strength <= cfg["sector_weak_rel_pct"]:
        strength = "弱"
    else:
        strength = "中"

    if rel_direction is None:
        direction = None
    elif rel_direction >= cfg["direction_up_rel_pct"]:
        direction = "↑"
    elif rel_direction <= cfg["direction_down_rel_pct"]:
        direction = "↓"
    else:
        direction = "→"

    return {
        "strength": strength,
        "direction": direction,
        "rel_strength_pct": rel_strength,
        "rel_direction_pct": rel_direction,
    }


def _weighted_returns(stocks: list[dict], periods: list[int]) -> dict[str, float | None]:
    """セクター集計リターン: 時価総額加重平均(不明銘柄は等ウェイト扱いの中央値的簡略化として除外、
    全銘柄不明なら単純平均)。"""
    out: dict[str, float | None] = {}
    for p in periods:
        key = f"d{p}"
        pairs = [(s["returns"].get(key), s.get("mcap")) for s in stocks if s["returns"].get(key) is not None]
        if not pairs:
            out[key] = None
            continue
        weighted = [(r, m) for r, m in pairs if m]
        if weighted:
            total = sum(m for _, m in weighted)
            out[key] = round(sum(r * m for r, m in weighted) / total, 2)
        else:
            out[key] = round(sum(r for r, _ in pairs) / len(pairs), 2)
    return out


def build_heatmap(
    universe: dict,
    frames: dict[str, pd.DataFrame],
    benchmark_close: pd.Series,
    stock_records: list[dict],
    config: dict | None = None,
    today_str: str | None = None,
) -> dict:
    """heatmap.json + sector_history.json を生成し、レポート付与用の
    sector_strength_by_code を返す。"""
    config = config or load_config()
    cfg = _cfg(config)
    periods = cfg["periods"]
    today_str = today_str or datetime.now().date().isoformat()

    sector_map = load_sector_map()
    record_by_code = {r["code"]: r for r in stock_records}

    topix_returns = (
        compute_returns(benchmark_close, periods)
        if benchmark_close is not None and len(benchmark_close) > 0
        else {f"d{p}": None for p in periods}
    )

    # --- 銘柄タイル ---
    stocks_by_sector: dict[str, list[dict]] = {}
    for stock in universe.get("stocks", []):
        code = stock["code"]
        df = frames.get(code)
        if df is None or df.empty:
            continue
        close = df["close"]
        last_close = float(close.iloc[-1])
        shares = stock.get("shares_outstanding")
        mcap = round(shares * last_close) if shares else None
        sector = stock.get("sector33") or sector_map.get(code) or UNKNOWN_SECTOR

        record = record_by_code.get(code)
        tile = {
            "code": code,
            "name": stock.get("name", ""),
            "close": round(last_close, 2),
            "mcap": mcap,
            "returns": compute_returns(close, periods),
            "priority": record.get("priority") if record else None,
            "rs": record.get("rs") if record else None,
        }
        if record and record.get("priority") is not None:
            # タップ時ポップアップ用の機能A詳細
            tile["detail"] = {
                "tier": record.get("tier"),
                "status": record.get("status"),
                "priority_penalty": record.get("priority_penalty"),
                "priority_unmet": record.get("priority_unmet"),
                "ma_deviation_pct": record.get("ma_deviation_pct"),
                "high52w_distance_pct": record.get("high52w_distance_pct"),
                "has_chart": record.get("has_chart", False),
            }
        stocks_by_sector.setdefault(sector, []).append(tile)

    # --- セクター集計 ---
    sector_strength_by_code: dict[str, dict] = {}
    sectors = []
    for sector, tiles in stocks_by_sector.items():
        agg_returns = _weighted_returns(tiles, periods)
        rs_info = sector_rs(agg_returns, topix_returns, cfg)
        p1 = sum(1 for t in tiles if t.get("priority") == 1)
        p2 = sum(1 for t in tiles if t.get("priority") == 2)
        mcap_total = sum(t["mcap"] for t in tiles if t.get("mcap"))
        tiles_sorted = sorted(tiles, key=lambda t: -(t.get("mcap") or 0))
        sectors.append(
            {
                "sector": sector,
                "returns": agg_returns,
                "rs": rs_info,
                "p1_count": p1,
                "p2_count": p2,
                "stock_count": len(tiles),
                "mcap_total": mcap_total or None,
                "stocks": tiles_sorted,
            }
        )
        for t in tiles:
            sector_strength_by_code[t["code"]] = {
                "sector": sector,
                "strength": rs_info["strength"],
                "direction": rs_info["direction"],
            }

    sectors.sort(key=lambda s: -(s["mcap_total"] or 0))

    heatmap = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "date": today_str,
        "periods": periods,
        "topix_returns": topix_returns,
        "sectors": sectors,
    }
    HEATMAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HEATMAP_PATH, "w", encoding="utf-8") as f:
        json.dump(heatmap, f, ensure_ascii=False, indent=2)

    update_sector_history(today_str, sectors, topix_returns, cfg)

    return {"heatmap": heatmap, "sector_strength_by_code": sector_strength_by_code}


def update_sector_history(
    date_str: str, sectors: list[dict], topix_returns: dict, cfg: dict
) -> dict:
    """セクター集計値の日次履歴 (data/sector_history.json)。同日再実行は上書き。"""
    if SECTOR_HISTORY_PATH.exists():
        with open(SECTOR_HISTORY_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {"history": []}

    entry = {
        "date": date_str,
        "topix_d1": topix_returns.get("d1"),
        "sectors": {
            s["sector"]: {
                "d1": s["returns"].get("d1"),
                "rel_strength_pct": s["rs"].get("rel_strength_pct"),
                "strength": s["rs"].get("strength"),
                "mcap_total": s["mcap_total"],
                "p1_count": s["p1_count"],
            }
            for s in sectors
        },
    }
    history["history"] = [e for e in history["history"] if e.get("date") != date_str]
    history["history"].append(entry)
    history["history"] = history["history"][-cfg["history_keep_days"]:]

    SECTOR_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SECTOR_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return history
