"""Market index / macro series collection for the dashboard's market overview.

Collects daily closes for a fixed set of indices (Nikkei 225, TOPIX, TSE
Growth 250, JGB 10y yield, USD/JPY, NASDAQ Composite, SOX) via yfinance with
a stooq fallback, caches each series to data/indices/{key}.parquet, and
emits docs/data/indices.json for the dashboard.

Design notes:
- Each index has an ordered list of (source, symbol) candidates; the first
  one that returns data wins. This absorbs source-side symbol availability
  differences (e.g. TOPIX itself isn't on Yahoo, so an ETF proxy follows).
- A per-index failure never fails the job: the index is simply reported
  with its cached history (or omitted when there is no cache at all).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.config import REPO_ROOT, load_config

INDICES_CACHE_DIR = REPO_ROOT / "data" / "indices"
INDICES_JSON_PATH = REPO_ROOT / "docs" / "data" / "indices.json"

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
STOOQ_USER_AGENT = "Mozilla/5.0 (compatible; minervini-screener/1.0)"

HISTORY_DAYS = 520  # cache depth (trading days)
SERIES_DAYS = 260  # depth exported to indices.json (enough for a 1y sparkline/chart)


@dataclass(frozen=True)
class IndexSpec:
    key: str
    name: str
    # unit: "" = index points (change shown in %), "%" = a yield (change
    # shown as a point diff), "JPY" = currency rate (change shown in %).
    unit: str
    candidates: tuple[tuple[str, str], ...]  # ordered (source, symbol)
    decimals: int = 2


INDEX_SPECS: tuple[IndexSpec, ...] = (
    IndexSpec("nikkei225", "日経225", "", (("yahoo", "^N225"), ("stooq", "^nkx"))),
    # TOPIX itself is not on Yahoo; ^TPX is tried first in case it appears,
    # then stooq's index symbol, then the 1306 ETF as a tracking proxy.
    IndexSpec("topix", "TOPIX", "", (("yahoo", "^TPX"), ("stooq", "^tpx"), ("yahoo", "1306.T"))),
    # TSE Growth Market 250 index: the most reliable freely available proxy
    # is the 2516 ETF (iFreeETF 東証グロース市場250指数).
    IndexSpec("growth250", "グロース250", "", (("yahoo", "2516.T"),)),
    IndexSpec("jgb10y", "日本10年金利", "%", (("stooq", "10jpy.b"), ("stooq", "10yjpy.b")), decimals=3),
    IndexSpec("usdjpy", "ドル円", "JPY", (("yahoo", "JPY=X"), ("stooq", "usdjpy")), decimals=2),
    IndexSpec("nasdaq", "NASDAQ", "", (("yahoo", "^IXIC"), ("stooq", "^ndq"))),
    IndexSpec("sox", "SOX", "", (("yahoo", "^SOX"), ("stooq", "^sox"))),
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def cache_path(key: str) -> Path:
    return INDICES_CACHE_DIR / f"{key}.parquet"


def load_cache(key: str) -> pd.DataFrame | None:
    path = cache_path(key)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def save_cache(key: str, df: pd.DataFrame) -> None:
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last")
    df = df.tail(HISTORY_DAYS).reset_index(drop=True)
    INDICES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path(key), index=False)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_yahoo(symbol: str) -> pd.DataFrame | None:
    import yfinance as yf

    try:
        raw = yf.download(symbol, period="2y", progress=False, auto_adjust=True, threads=False)
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    df = raw.reset_index()
    # yfinance may return single-level or (field, ticker) MultiIndex columns.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    if "date" not in df.columns or "close" not in df.columns:
        return None
    df = df[["date", "close"]].dropna(subset=["close"])
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df


def _fetch_stooq(symbol: str) -> pd.DataFrame | None:
    url = STOOQ_URL.format(symbol=symbol)
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
    if df.empty or "Date" not in df.columns or "Close" not in df.columns:
        return None
    df.columns = [c.lower() for c in df.columns]
    df = df[["date", "close"]].dropna(subset=["close"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_index(spec: IndexSpec, sleep_sec: float = 1.0) -> pd.DataFrame | None:
    """Try each candidate source in order; return the first non-empty frame."""
    for i, (source, symbol) in enumerate(spec.candidates):
        if i > 0:
            time.sleep(sleep_sec)
        df = _fetch_yahoo(symbol) if source == "yahoo" else _fetch_stooq(symbol)
        if df is not None and len(df) >= 2:
            return df
    return None


# ---------------------------------------------------------------------------
# Update + JSON output
# ---------------------------------------------------------------------------

def _merge(cached: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if cached is None or cached.empty:
        return new
    combined = pd.concat([cached, new], ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates(subset="date", keep="last")
    return combined.reset_index(drop=True)


def build_index_entry(spec: IndexSpec, df: pd.DataFrame) -> dict:
    df = df.sort_values("date").reset_index(drop=True)
    tail = df.tail(SERIES_DAYS)
    last = float(tail["close"].iloc[-1])
    prev = float(tail["close"].iloc[-2]) if len(tail) >= 2 else None

    change = None
    change_pct = None
    if prev is not None:
        change = round(last - prev, spec.decimals)
        if prev != 0:
            change_pct = round((last / prev - 1.0) * 100.0, 2)

    return {
        "key": spec.key,
        "name": spec.name,
        "unit": spec.unit,
        "last": round(last, spec.decimals),
        "prev": round(prev, spec.decimals) if prev is not None else None,
        "change": change,
        "change_pct": change_pct,
        "last_date": tail["date"].iloc[-1].strftime("%Y-%m-%d"),
        "series": [
            {"t": d.strftime("%Y-%m-%d"), "v": round(float(v), spec.decimals)}
            for d, v in zip(tail["date"], tail["close"])
        ],
    }


def update_indices(config: dict | None = None) -> dict:
    """Fetch/refresh every index, update caches, and write indices.json.

    Returns {"updated": [keys], "failed": [keys]}; never raises for
    per-index failures.
    """
    config = config or load_config()  # noqa: F841 (kept for future config knobs)
    updated: list[str] = []
    failed: list[str] = []
    entries: list[dict] = []

    for spec in INDEX_SPECS:
        cached = load_cache(spec.key)
        try:
            fresh = fetch_index(spec)
        except Exception:
            fresh = None

        if fresh is not None:
            merged = _merge(cached, fresh)
            save_cache(spec.key, merged)
            updated.append(spec.key)
        elif cached is not None:
            merged = cached
            failed.append(spec.key)
        else:
            failed.append(spec.key)
            continue

        entries.append(build_index_entry(spec, merged))
        time.sleep(1.0)

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "stale_keys": [k for k in failed if any(e["key"] == k for e in entries)],
        "indices": entries,
    }
    INDICES_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(INDICES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    return {"updated": updated, "failed": failed}
