"""Universe construction: JPX listed-stock list + liquidity filter (design doc 1.2).

Monthly job: fetch the full JPX listed securities list, keep domestic common
stock only (exclude ETF/ETN/REIT/infra funds/PRO Market/foreign stock/capital
certificates), fetch ~3 months of recent prices for all candidates, and keep
everything whose 20-day average trading value (close * volume) clears
`universe.min_trading_value`. `universe.size`, if set, is an upper cap applied
after the threshold; leave it null to let the threshold alone decide.
Result is cached to data/universe.json; the daily job just reads that file.
"""
from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO

import pandas as pd
import requests

from src.config import REPO_ROOT, load_config
from src.data.prices import fetch_yfinance_chunk

UNIVERSE_PATH = REPO_ROOT / "data" / "universe.json"
SECTOR_MAP_PATH = REPO_ROOT / "data" / "sector_map.json"

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


def select_liquid_codes(ranking: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """流動性ランキングから採用銘柄を切り出す (config.universe)。

    `min_trading_value` (20日平均売買代金の下限) を主、`size` を上限キャップとして
    適用する。size が None/0 ならキャップ無し = 閾値だけで銘柄数が決まる。
    どちらも未設定なら全件を返す。ネットワーク不要なので単体テストできる。
    """
    config = config or load_config()
    uni_cfg = config.get("universe", {})
    min_value = uni_cfg.get("min_trading_value")
    size = uni_cfg.get("size")

    selected = ranking
    if min_value:
        selected = selected[selected["avg_trading_value"] >= float(min_value)]
    if size:
        selected = selected.head(int(size))
    return selected.reset_index(drop=True)


def write_sector_map(candidates: pd.DataFrame) -> dict:
    """機能B: JPX月次Excel由来のTSE33業種 静的マッピング (data/sector_map.json)。

    ユニバース再構築(月次)のたびに再生成される。ヒートマップ生成バッチが参照。
    """
    sectors = {}
    if "sector33" in candidates.columns:
        for row in candidates.itertuples(index=False):
            sector = getattr(row, "sector33", None)
            if sector is not None and not pd.isna(sector) and str(sector) != "-":
                sectors[row.code] = str(sector)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "sectors": sectors,
    }
    SECTOR_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SECTOR_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def fetch_shares_outstanding(codes: list[str], config: dict | None = None) -> dict[str, int | None]:
    """機能B: 発行済株式数を月次ユニバース再構築時にまとめて取得。

    日次の時価総額は「株式数 × 最新終値」でバッチが算出する。
    取得失敗は None 許容(ヒートマップ側で最小面積フォールバック)。

    銘柄あたり1リクエストなので逐次だと銘柄数に比例して伸びる(0.5秒×2000銘柄=17分)。
    `universe.shares_workers` 本のスレッドで並列化する。sleep はワーカーごとに効くので
    実効レートは workers/sleep_sec req/秒。
    """
    import yfinance as yf

    config = config or load_config()
    uni_cfg = config.get("universe", {})
    sleep_sec = uni_cfg.get("shares_sleep_sec", 0.5)
    workers = max(1, int(uni_cfg.get("shares_workers", 1) or 1))

    def _one(code: str) -> tuple[str, int | None]:
        n = None
        try:
            fi = yf.Ticker(f"{code}.T").fast_info
            raw = None
            try:
                raw = fi["shares"]
            except Exception:
                raw = getattr(fi, "shares", None)
            if raw:
                n = int(raw)
        except Exception:
            n = None
        time.sleep(sleep_sec)
        return code, n

    if workers == 1:
        return dict(_one(code) for code in codes)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_one, codes))


def build_universe(config: dict | None = None) -> dict:
    config = config or load_config()

    listed = fetch_jpx_listed_stocks(config)
    candidates = filter_domestic_common_stock(listed, config)
    write_sector_map(candidates)
    ranking = compute_liquidity_ranking(candidates["code"].tolist(), config)

    top = select_liquid_codes(ranking, config)
    name_by_code = dict(zip(candidates["code"], candidates["name"]))
    sector_by_code = (
        dict(zip(candidates["code"], candidates["sector33"])) if "sector33" in candidates.columns else {}
    )
    segment_by_code = (
        dict(zip(candidates["code"], candidates["segment"])) if "segment" in candidates.columns else {}
    )
    top_codes = [row.code for row in top.itertuples(index=False)]
    shares_by_code = fetch_shares_outstanding(top_codes, config)

    stocks = []
    for row in top.itertuples(index=False):
        sector = sector_by_code.get(row.code)
        if sector is None or pd.isna(sector) or str(sector) == "-":
            sector = None
        segment = segment_by_code.get(row.code)
        if segment is None or pd.isna(segment):
            segment = None
        stocks.append(
            {
                "code": row.code,
                "name": name_by_code.get(row.code, ""),
                "avg_trading_value": row.avg_trading_value,
                "sector33": sector,
                "segment": str(segment) if segment else None,
                "shares_outstanding": shares_by_code.get(row.code),
            }
        )

    universe = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "size": len(stocks),
        "candidates_scanned": len(candidates),
        # 採用基準を出力にも残す。銘柄数が市況で変動する方式なので、後から
        # 「この時どの閾値で何銘柄だったか」を追えないと breadth や RS の
        # 履歴の断絶を説明できなくなる。
        "min_trading_value": config["universe"].get("min_trading_value"),
        "size_cap": config["universe"].get("size"),
        "stocks": stocks,
    }
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE_PATH, "w", encoding="utf-8") as f:
        json.dump(universe, f, ensure_ascii=False, indent=2)
    return universe


def load_universe() -> dict:
    with open(UNIVERSE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Universe / sector map builder")
    parser.add_argument(
        "--sector-map-only",
        action="store_true",
        help="JPX一覧からdata/sector_map.jsonのみ再生成(株価取得なし)",
    )
    args = parser.parse_args()
    config = load_config()
    if args.sector_map_only:
        listed = fetch_jpx_listed_stocks(config)
        candidates = filter_domestic_common_stock(listed, config)
        payload = write_sector_map(candidates)
        print(f"sector_map.json written: {len(payload['sectors'])} codes")
    else:
        universe = build_universe(config)
        thr = universe.get("min_trading_value")
        thr_txt = f", min_trading_value={thr / 1e8:.2f}億円" if thr else ""
        print(
            f"universe.json written: {universe['size']} stocks "
            f"(scanned {universe['candidates_scanned']}{thr_txt})"
        )


if __name__ == "__main__":
    main()
