"""Universe construction: JPX listed-stock list + liquidity filter (design doc 1.2).

Monthly job: fetch the full JPX listed securities list, keep domestic common
stock only (exclude ETF/ETN/REIT/infra funds/PRO Market/foreign stock/capital
certificates), fetch ~3 months of recent prices for all candidates, and keep
the top `universe.size` by 20-day average trading value (close * volume).
Result is cached to data/universe.json; the daily job just reads that file.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import requests

from src.config import REPO_ROOT, load_config
from src.data.prices import fetch_yfinance_chunk

UNIVERSE_PATH = REPO_ROOT / "data" / "universe.json"

DOMESTIC_STOCK_SEGMENTS = {
    "プライム（内国株式）",
    "スタンダード（内国株式）",
    "グロース（内国株式）",
}

JPX_COLUMN_MAP = {
    "日付": "date",
    "コード": "code",
    "銘柄名": "name",
    "市場・商品区分": "segment",
    "33業種区分": "sector33",
    "17業種区分": "sector17",
}


def fetch_jpx_listed_stocks(config: dict | None = None) -> pd.DataFrame:
    config = config or load_config()
    url = config["universe"]["jpx_list_url"]
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    df = pd.read_excel(BytesIO(resp.content), engine="xlrd")
    df = df.rename(columns=JPX_COLUMN_MAP)
    df["code"] = df["code"].astype(str)
    return df


def filter_domestic_common_stock(df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """Keep domestic Prime/Standard/Growth common stock only.

    Note: JPX's supervision/liquidation ("監理・整理銘柄") flags are not present
    in this file; they must be cross-referenced separately if needed. As a
    stopgap, an optional manual exclude list can be set in config.yaml under
    universe.manual_exclude_codes.
    """
    config = config or load_config()
    exclude_codes = set(str(c) for c in config["universe"].get("manual_exclude_codes", []))
    filtered = df[df["segment"].isin(DOMESTIC_STOCK_SEGMENTS)].copy()
    filtered = filtered[~filtered["code"].isin(exclude_codes)]
    keep_cols = [c for c in ["code", "name", "segment", "sector33", "sector17"] if c in filtered.columns]
    return filtered[keep_cols].reset_index(drop=True)


def _fetch_recent_for_liquidity(codes: list[str], config: dict) -> dict[str, pd.DataFrame]:
    chunk_size = config["data"]["chunk_size"]
    sleep_lo, sleep_hi = config["data"]["sleep_range"]
    frames: dict[str, pd.DataFrame] = {}
    for i in range(0, len(codes), chunk_size):
        chunk = codes[i : i + chunk_size]
        tickers = [f"{c}.T" for c in chunk]
        chunk_result = fetch_yfinance_chunk(tickers, start=None, period="3mo", config=config)
        for code, ticker in zip(chunk, tickers):
            df = chunk_result.get(ticker)
            if df is not None:
                frames[code] = df
        if i + chunk_size < len(codes):
            time.sleep(random.uniform(sleep_lo, sleep_hi))
    return frames


def compute_liquidity_ranking(codes: list[str], config: dict | None = None) -> pd.DataFrame:
    """Rank candidate codes by 20-day average trading value (close * volume)."""
    config = config or load_config()
    window = config["universe"]["liquidity_window"]
    frames = _fetch_recent_for_liquidity(codes, config)

    rows = []
    for code, df in frames.items():
        if len(df) < window:
            continue
        recent = df.tail(window)
        avg_value = float((recent["close"] * recent["volume"]).mean())
        rows.append({"code": code, "avg_trading_value": avg_value})

    if not rows:
        return pd.DataFrame(columns=["code", "avg_trading_value"])
    return (
        pd.DataFrame(rows)
        .sort_values("avg_trading_value", ascending=False)
        .reset_index(drop=True)
    )


def build_universe(config: dict | None = None) -> dict:
    config = config or load_config()
    size = config["universe"]["size"]

    listed = fetch_jpx_listed_stocks(config)
    candidates = filter_domestic_common_stock(listed, config)
    ranking = compute_liquidity_ranking(candidates["code"].tolist(), config)

    top = ranking.head(size)
    name_by_code = dict(zip(candidates["code"], candidates["name"]))
    stocks = [
        {
            "code": row.code,
            "name": name_by_code.get(row.code, ""),
            "avg_trading_value": row.avg_trading_value,
        }
        for row in top.itertuples(index=False)
    ]

    universe = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "size": len(stocks),
        "candidates_scanned": len(candidates),
        "stocks": stocks,
    }
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE_PATH, "w", encoding="utf-8") as f:
        json.dump(universe, f, ensure_ascii=False, indent=2)
    return universe


def load_universe() -> dict:
    with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
