"""Price data acquisition: yfinance primary, stooq fallback, Parquet cache.

See design doc section 1.1 / 1.4. Key behaviors implemented here:
- 50-ticker chunked yfinance downloads with randomized sleep between chunks
- exponential backoff retry (5s -> 15s -> 45s) per failed chunk
- incremental fetch (only new days) using the local Parquet cache, with the
  most recent 30 days always re-fetched and overwritten to absorb splits /
  corrections
- stooq fallback (direct CSV endpoint, custom User-Agent, 2s/ticker sleep)
  for tickers that fail all yfinance retries
- job-level failure if more than `max_fail_ratio` of the universe has no data
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.config import REPO_ROOT, load_config

PRICE_CACHE_DIR = REPO_ROOT / "data" / "prices"
OHLCV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
STOOQ_URL = "https://stooq.com/q/d/l/?s={ticker}&i=d"
STOOQ_USER_AGENT = "Mozilla/5.0 (compatible; minervini-screener/1.0)"
RECHECK_DAYS = 30  # re-fetch + overwrite this many recent trading days each run


@dataclass
class PriceUpdateResult:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    failed_tickers: list[str] = field(default_factory=list)
    stale_tickers: list[str] = field(default_factory=list)
    illiquid_tickers: list[str] = field(default_factory=list)
    insufficient_history_count: int = 0
    job_failed: bool = False


def cache_path(code: str) -> Path:
    return PRICE_CACHE_DIR / f"{code}.parquet"


def load_cache(code: str) -> pd.DataFrame | None:
    path = cache_path(code)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def save_cache(code: str, df: pd.DataFrame, history_days: int) -> None:
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df = df.tail(history_days).reset_index(drop=True)
    df = df.copy()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype("float32")
    PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path(code), index=False)


def _clean_ohlcv(sub: pd.DataFrame) -> pd.DataFrame | None:
    sub = sub.reset_index()
    sub.columns = [str(c).lower() for c in sub.columns]
    keep = [c for c in OHLCV_COLUMNS if c in sub.columns]
    if "date" not in keep or "close" not in keep:
        return None
    sub = sub[keep].dropna(subset=["close"])
    if sub.empty:
        return None
    sub["date"] = pd.to_datetime(sub["date"])
    return sub


def _split_multi_ticker(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame | None]:
    result: dict[str, pd.DataFrame | None] = {}
    if raw is None or raw.empty:
        return {t: None for t in tickers}
    if isinstance(raw.columns, pd.MultiIndex):
        top_level = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t not in top_level:
                result[t] = None
                continue
            sub = raw[t].dropna(how="all")
            result[t] = _clean_ohlcv(sub) if not sub.empty else None
    else:
        result[tickers[0]] = _clean_ohlcv(raw) if not raw.empty else None
    return result


def fetch_yfinance_chunk(
    tickers: list[str], start: str | None, period: str | None, config: dict
) -> dict[str, pd.DataFrame | None]:
    import yfinance as yf

    backoff = config["data"]["backoff_sec"]
    kwargs = dict(
        tickers=tickers,
        group_by="ticker",
        threads=False,
        auto_adjust=True,
        progress=False,
    )
    if start:
        kwargs["start"] = start
    else:
        kwargs["period"] = period or "2y"

    for wait in [0] + list(backoff):
        if wait:
            time.sleep(wait)
        try:
            raw = yf.download(**kwargs)
            return _split_multi_ticker(raw, tickers)
        except Exception:
            continue
    return {t: None for t in tickers}


def fetch_stooq(code: str) -> pd.DataFrame | None:
    ticker = f"{code}.jp"
    url = STOOQ_URL.format(ticker=ticker)
    try:
        resp = requests.get(url, headers={"User-Agent": STOOQ_USER_AGENT}, timeout=15)
        resp.raise_for_status()
    except Exception:
        return None
    text = resp.text
    if not text or "<html" in text.lower() or "Exceeded" in text:
        return None
    try:
        df = pd.read_csv(StringIO(text))
    except Exception:
        return None
    if df.empty or "Date" not in df.columns:
        return None
    df.columns = [c.lower() for c in df.columns]
    keep = [c for c in OHLCV_COLUMNS if c in df.columns]
    if "date" not in keep or "close" not in keep:
        return None
    df = df[keep].dropna(subset=["close"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _merge_new_data(cached: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if cached is None or cached.empty:
        return new
    combined = pd.concat([cached, new], ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates(subset="date", keep="last")
    return combined.reset_index(drop=True)


def _quality_guard(code: str, df: pd.DataFrame, result: PriceUpdateResult) -> pd.DataFrame | None:
    if len(df) < 252:
        result.insufficient_history_count += 1
        return None
    recent20 = df.tail(20)
    zero_vol_days = int((recent20["volume"] <= 0).sum())
    if zero_vol_days >= 3:
        result.illiquid_tickers.append(code)
        return None
    return df


def update_prices(codes: list[str], config: dict | None = None) -> PriceUpdateResult:
    config = config or load_config()
    data_cfg = config["data"]
    chunk_size = data_cfg["chunk_size"]
    sleep_lo, sleep_hi = data_cfg["sleep_range"]
    history_days = data_cfg["history_days"]

    result = PriceUpdateResult()

    caches: dict[str, pd.DataFrame | None] = {code: load_cache(code) for code in codes}
    full_codes = [c for c in codes if caches[c] is None]
    incr_codes = [c for c in codes if caches[c] is not None]

    incr_start = (datetime.today() - timedelta(days=RECHECK_DAYS + 10)).strftime("%Y-%m-%d")

    fetched: dict[str, pd.DataFrame | None] = {}

    for group_codes, period, start in (
        (full_codes, "2y", None),
        (incr_codes, None, incr_start),
    ):
        for i in range(0, len(group_codes), chunk_size):
            chunk = group_codes[i : i + chunk_size]
            if not chunk:
                continue
            tickers = [f"{c}.T" for c in chunk]
            chunk_result = fetch_yfinance_chunk(tickers, start, period, config)
            for code, ticker in zip(chunk, tickers):
                fetched[code] = chunk_result.get(ticker)
            if i + chunk_size < len(group_codes):
                time.sleep(random.uniform(sleep_lo, sleep_hi))

    stooq_needed = [c for c in codes if fetched.get(c) is None]
    for code in stooq_needed:
        fetched[code] = fetch_stooq(code)
        time.sleep(data_cfg.get("stooq_sleep_sec", 2.0))

    for code in codes:
        new_data = fetched.get(code)
        cached = caches.get(code)
        if new_data is None:
            if cached is not None:
                result.stale_tickers.append(code)
                merged = cached
            else:
                result.failed_tickers.append(code)
                continue
        else:
            merged = _merge_new_data(cached, new_data)
            save_cache(code, merged, history_days)

        merged = _quality_guard(code, merged, result)
        if merged is not None:
            result.frames[code] = merged

    max_fail_ratio = data_cfg["max_fail_ratio"]
    if codes and len(result.failed_tickers) / len(codes) > max_fail_ratio:
        result.job_failed = True

    return result


def drop_benchmark_outliers(close: pd.Series, max_dev: float = 0.3) -> pd.Series:
    """Drop obviously bad ticks from an index-ETF close series.

    Yahoo occasionally serves isolated rows off by ~10x (seen on 1306.T);
    a TOPIX ETF never legitimately deviates 30% from its 11-day median, so
    such rows are data glitches. Dropped days are later forward-filled by
    consumers (add_rs_line) or simply absent from the return calc.
    """
    med = close.rolling(11, center=True, min_periods=1).median()
    bad = (close / med - 1).abs() > max_dev
    return close[~bad]


def get_benchmark_close(config: dict | None = None) -> pd.Series:
    """Fetch/cache the TOPIX proxy ETF and return its close series indexed by date."""
    config = config or load_config()
    ticker_full = config["data"]["topix_proxy_ticker"]  # e.g. "1306.T"
    code = ticker_full.split(".")[0]
    res = update_prices([code], config)
    df = res.frames.get(code)
    if df is None:
        raise RuntimeError(f"Failed to fetch benchmark ticker {ticker_full}")
    return drop_benchmark_outliers(df.set_index("date")["close"])
