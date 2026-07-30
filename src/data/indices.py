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
- 日足の系列とは別に、実行のたびの「今の値」を data/history/indices_intraday.jsonl
  へ1行ずつ追記する。indices.json は毎回上書きなので、これが無いとザラ場中の
  値動きの形(寄り天/後場V字/だらだら安)が翌日には残らない。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.config import REPO_ROOT, load_config

INDICES_CACHE_DIR = REPO_ROOT / "data" / "indices"
INDICES_JSON_PATH = REPO_ROOT / "docs" / "data" / "indices.json"

JST = timezone(timedelta(hours=9))

# 指数の日中ティック履歴 (2026-07-31 新設)。1実行 = 1行。
INTRADAY_TICKS_PATH = REPO_ROOT / "data" / "history" / "indices_intraday.jsonl"
# 同一実行時刻の行は後勝ちで潰す(ワークフローの再実行対策)。
INTRADAY_TICKS_KEY = ("ts",)
# 保持は当日+数日。「その日の地合いの形」を読むための素材なので、日足系列と違って
# 長期保存する意味がない(長期の形は indices.json の series 側にある)。
INTRADAY_TICKS_KEEP_DAYS = 7
# 15分間隔 × 平日ほぼ終日で1日80行前後。間引き(compaction)は毎回やると
# 全行書き戻しになって git 差分が膨らむので、行数が保持想定を大きく超えた時だけ。
INTRADAY_TICKS_MAX_LINES = 1200

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


# ---------------------------------------------------------------------------
# 日中ティック履歴
# ---------------------------------------------------------------------------

def _last_intraday_tick() -> dict | None:
    """JSONL の最終行を返す(無ければ None)。間引き判定用。"""
    from src.history_store import iter_records

    last = None
    for record in iter_records(INTRADAY_TICKS_PATH):
        last = record
    return last


def append_intraday_tick(entries: list[dict], now: datetime | None = None) -> bool:
    """指数の「今この瞬間の値」を1行 data/history/indices_intraday.jsonl へ追記する。

    なぜ要るか: indices.json は実行のたびにまるごと作り直されるので、ザラ場中に
    どう動いたか(寄ってすぐ天井をつけたのか、後場にV字で戻したのか、じりじり
    安かったのか)が翌日には一切残らない。日足の終値系列だけでは「その日の地合いの
    形」を後から復元できない。15分間隔で走るワークフローの各実行を1行ずつ残す。

    - `date` は JST 基準で入れる。この cron は米国市場の時間帯(JST 22:00〜翌8:45)
      にも走るので、読み手が「東証のザラ場 = JSTの9:00〜15:30 の行」だけを選べる
      ようにするため。記録自体は時間帯を問わず全部残す(ドル円やNASDAQの夜間の
      動きは翌朝の地合いの前提になる)。
    - 直前の行と全指数の値が完全に一致する実行は追記しない。市場が閉じていて値が
      動かない時間帯に同じ行が延々増えるのを防ぐ、単純な間引き。

    追記したら True。
    """
    from src.history_store import append_records, compact, count_lines

    values = {
        entry["key"]: entry.get("last")
        for entry in entries
        if entry.get("key") and entry.get("last") is not None
    }
    if not values:
        return False

    previous = _last_intraday_tick()
    if previous is not None and (previous.get("values") or {}) == values:
        return False

    now = (now or datetime.now(JST)).astimezone(JST)
    date_str = now.date().isoformat()
    append_records(
        INTRADAY_TICKS_PATH,
        [{"ts": now.isoformat(timespec="seconds"), "date": date_str, "values": values}],
    )

    if count_lines(INTRADAY_TICKS_PATH) > INTRADAY_TICKS_MAX_LINES:
        removed = compact(
            INTRADAY_TICKS_PATH,
            INTRADAY_TICKS_KEY,
            keep_days=INTRADAY_TICKS_KEEP_DAYS,
            today=date_str,
        )
        print(f"indices_intraday: compaction で {removed} 行を削減")
    return True


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
    from src.report.secure_io import write_docs_json
    write_docs_json(INDICES_JSON_PATH, payload, indent=1)

    # 日中ティックの蓄積。指数表示そのものは既に書き終わっているので、ここが
    # 失敗しても指数更新は成功扱いのままにする(このファイル全体の「1つの指数が
    # 取れなくてもジョブは落とさない」方針と同じ)。
    try:
        append_intraday_tick(entries)
    except Exception as e:
        print(f"Intraday tick append failed (ignored): {e}")

    return {"updated": updated, "failed": failed}


def main() -> None:
    """CLI entry point (``python -m src.data.indices``).

    Standalone entry used by the intraday-indices workflow to refresh just
    docs/data/indices.json during market hours, independent of the full
    daily pipeline (which also runs the screener, fundamentals, etc.).
    """
    result = update_indices()
    print(f"updated={result['updated']} failed={result['failed']}")


if __name__ == "__main__":
    main()
