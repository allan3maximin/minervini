"""A レイヤ(自動取得指標)を束ねて1銘柄ぶんの dict を組む。DESIGN_DEEPDIVE.md §4/§10 手順6。

`metrics.py` は純関数のみだが、ここでは実ファイル I/O を行う:
  - `data/prices_long/{code}.parquet`(長期株価。無ければ§4.2の案内 + MissingDataError)
  - `data/prices_asset/jp_1306.parquet`(TOPIX 比較用ベンチマーク。§4.2.1。`get_benchmark_close`
    は絶対に呼ばない — `update_prices` を内部で叩きネットワークに出るため)
  - `data/sector_map.json`(同業比の母集団)
  - `data/deepdive/raw/{code}.jsonl`(jq_raw.py が貯めた生レコード)
  - `data/deepdive/watchlist.jsonl`(銘柄名・next_earnings_date_manual)
  - `data/earnings_calendar.json`(発表予定日カレンダー)

ネットワークには一切出ない。長期株価/ベンチマークが無いときに自動取得しに行かないのは
「準備は30分で終える」という要件を壊さないため(§4.2 のコメント参照)。

★ベンチマークは `idx_topix`(^TPX)ではなく `jp_1306`(TOPIX連動ETF)を使う★
2026-08-25、7611での実地確認で `idx_topix.parquet` が1行(2015-10-20のみ)しか
無いことが発覚。`tools/fetch_long_history.py` 側で ^TPX は「Yahooに無く取れないことがある」
と既知の欠陥として明記されており(ASSET_TICKERS のnote参照)、`--force` 再取得でも
1行のまま。同ファイルの note が代替として案内している `jp_1306`(1306.T、TOPIX連動・
配当込みETF、2001〜)に切り替えた。**注意: `jp_1306` は配当込み(tr=True)なので、
ここで出る「TOPIX比」は厳密な価格指数比ではなく、配当を含むTOPIX連動ETF比になる。**
配当分だけ僅かに有利に出る(年1〜2%程度)が、値段だけの`^TPX`が実質使えない以上、
これが現実的に取れる最善のベンチマークという判断。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import history_store
from src.config import REPO_ROOT, load_config
from src.data.jquants import load_earnings_calendar
from src.deepdive import jq_raw, metrics, store
from src.utils_io import safe_load_json

LONG_DIR = REPO_ROOT / "data" / "prices_long"
ASSET_DIR = REPO_ROOT / "data" / "prices_asset"
SECTOR_MAP_PATH = REPO_ROOT / "data" / "sector_map.json"
PREP_DIR = REPO_ROOT / "data" / "deepdive" / "prep"

DEFAULT_RETURN_WINDOWS = [21, 63]
DEFAULT_VOLUME_WINDOWS = [[5, 20], [5, 60]]

_WINDOW_LABELS = {21: "1M", 63: "3M"}

# §4.1 で「出せない」と確定しているもの。理由は sheet.py の末尾セクションに固定で出す。
OMITTED_ITEMS = [
    {"item": "PBR 5年レンジ", "reason": "純資産(BPS)を取得していないため(恒久)"},
    {"item": "EV/EBITDA", "reason": "有利子負債・現金・減価償却を取得していないため(恒久)"},
    {"item": "配当利回り5年レンジ", "reason": "配当データのキャッシュが無い(要実装)"},
    {"item": "月次 既存店前年比", "reason": "自動化対象外(手入力)"},
]


class MissingDataError(Exception):
    """§4.2/§4.2.1: 長期株価や TOPIX が無いときに送出する。

    cli.py 側でこれを catch し、メッセージを stderr に出して終了コード2で返す。
    """


# ---------------------------------------------------------------------------
# 読み出し(すべて読むだけ。無ければ None を返し、呼び出し側が案内を出す)
# ---------------------------------------------------------------------------

def long_price_path(code: str) -> Path:
    return LONG_DIR / f"{code}.parquet"


def load_long_prices(code: str) -> pd.DataFrame | None:
    """`data/prices_long/{code}.parquet` を読む。無ければ None。自動取得はしない(§4.2)。"""
    path = long_price_path(code)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def require_long_prices(code: str) -> pd.DataFrame:
    df = load_long_prices(code)
    if df is None:
        raise MissingDataError(
            f"{code} の長期株価がありません。先に取得してください:\n"
            f"  .venv/bin/python tools/fetch_long_history.py --only {code}"
        )
    return df


def load_topix() -> pd.DataFrame | None:
    """`data/prices_asset/jp_1306.parquet` を読む(TOPIX連動ETF。§4.2.1)。

    `get_benchmark_close` は使わない。`idx_topix.parquet`(^TPX)はYahoo側で
    ほぼ取れず1行しか作れない既知の欠陥があるため、代替として案内されている
    `jp_1306`(1306.T、配当込み)を使う(本ファイル冒頭docstring参照)。
    """
    path = ASSET_DIR / "jp_1306.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def require_topix() -> pd.DataFrame:
    df = load_topix()
    if df is None:
        raise MissingDataError(
            "TOPIX比較用のベンチマーク(jp_1306)がありません:\n"
            "  .venv/bin/python tools/fetch_long_history.py --assets --tickers jp_1306=1306.T"
        )
    return df


def load_sector_map() -> dict:
    """`{code: 業種名}`。トップレベルが code の辞書ではないので "sectors" を見る(§4.2.2)。"""
    return safe_load_json(SECTOR_MAP_PATH, {}).get("sectors", {})


def load_raw_records(code: str) -> list[dict]:
    """`raw/{code}.jsonl` を読む。無ければ空リスト(fetch し忘れとして prep 側が扱う)。"""
    return list(history_store.iter_records(jq_raw.raw_path(code)))


def load_watch_entry(code: str) -> dict:
    rows = [
        r for r in store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
        if r.get("ticker") == code
    ]
    return rows[0] if rows else {}


def prep_path(code: str, quarter: str) -> Path:
    return PREP_DIR / f"{code}_{quarter}.md"


# ---------------------------------------------------------------------------
# 内部ヘルパ(raw レコードの解釈)
# ---------------------------------------------------------------------------

def _float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _finstmt_records(raw_records: list[dict]) -> list[dict]:
    return [r for r in raw_records if metrics.quarter_of(r)]


def _latest_record(records: list[dict]) -> dict | None:
    dated = [r for r in records if r.get("DiscDate")]
    if not dated:
        return None
    return max(dated, key=lambda r: r["DiscDate"])


def _iso(ts) -> str | None:
    if ts is None or pd.isna(ts):
        return None
    return pd.Timestamp(ts).date().isoformat()


def _window_label(days: int) -> str:
    return _WINDOW_LABELS.get(days, f"{days}d")


def _progress_block(latest: dict | None, ytd_field: str, plan_field: str) -> dict:
    if latest is None:
        return {"value": None, "ytd": None, "plan": None, "period": None}
    ytd = _float(latest.get(ytd_field))
    plan = _float(latest.get(plan_field))
    return {
        "value": metrics.progress_rate(ytd, plan),
        "ytd": ytd,
        "plan": plan,
        "period": latest.get("CurPerType"),
    }


def _progress_history(fin_records: list[dict], latest: dict | None,
                       ytd_field: str, plan_field: str) -> dict:
    if latest is None:
        return {"diff_pt": None, "n": 0}
    cur_pr = metrics.progress_rate(_float(latest.get(ytd_field)), _float(latest.get(plan_field)))
    if cur_pr is None:
        return {"diff_pt": None, "n": 0}
    period = latest.get("CurPerType")
    hist: list[float] = []
    for r in fin_records:
        if r.get("DiscDate") == latest.get("DiscDate"):
            continue
        if r.get("CurPerType") != period:
            continue
        pr = metrics.progress_rate(_float(r.get(ytd_field)), _float(r.get(plan_field)))
        if pr is not None:
            hist.append(pr)
    return metrics.progress_vs_history(cur_pr, hist)


def _guidance_pairs(fin_records: list[dict]) -> list[tuple[float, float]]:
    """期初予想(前期末開示の NxFOP)→着地(当期 FY 実績 OP)のペアを年度順に作る。"""
    fy_records = sorted(
        (r for r in fin_records if metrics.quarter_of(r) == "FY" and r.get("CurFYSt")),
        key=lambda r: r["CurFYSt"],
    )
    pairs = []
    for prev, cur in zip(fy_records, fy_records[1:]):
        forecast = _float(prev.get("NxFOP"))
        actual = _float(cur.get("OP"))
        if forecast is not None and actual is not None:
            pairs.append((forecast, actual))
    return pairs


def _per_block(close: pd.Series, fin_records: list[dict]) -> dict:
    eps_points = metrics.fy_eps_points(fin_records)
    if not eps_points or close.empty:
        return {"pct": None, "n": 0, "start": None, "end": None, "current": None}
    series = metrics.per_series(close, eps_points)
    current_eps = eps_points[-1][1]
    if series.empty or not current_eps:
        return {"pct": None, "n": 0, "start": None, "end": None, "current": None}
    current = close.iloc[-1] / current_eps
    result = metrics.percentile_in_series(series, current)
    result["current"] = current
    return result


def _sector_relative_block(code: str, close: pd.Series, sector: str | None,
                            sector_map: dict, windows: list[int]) -> dict:
    if not sector:
        return {"sector": None, "n_peer_codes": 0, "windows": {}}
    peer_codes = sorted(
        c for c, s in sector_map.items()
        if s == sector and c != code and long_price_path(c).exists()
    )
    peer_closes = []
    for c in peer_codes:
        df = load_long_prices(c)
        if df is not None:
            peer_closes.append(df.set_index("date")["close"])

    windows_out = {}
    for days in windows:
        stock_ret = metrics.return_pct(close, days)
        peer_returns = [
            r for s in peer_closes if (r := metrics.return_pct(s, days)) is not None
        ]
        windows_out[_window_label(days)] = metrics.sector_relative_return(stock_ret, peer_returns)
    return {"sector": sector, "n_peer_codes": len(peer_codes), "windows": windows_out}


def _since_earnings_block(close: pd.Series, latest: dict | None) -> dict:
    if latest is None or not latest.get("DiscDate"):
        return {"value": None, "since": None}
    return {
        "value": metrics.since_date_return(close, latest["DiscDate"]),
        "since": latest["DiscDate"],
    }


def _default_quarter_label(fin_records: list[dict]) -> str:
    """`--quarter` 省略時の便宜ラベル。年度開始年 + 次四半期(例 "2026Q2")。

    正確な会計四半期の呼称ではなく、あくまでファイル名・記録用の目安。
    正確を期したい場合は呼び出し側で `--quarter` を明示すること。
    """
    latest = _latest_record(fin_records)
    next_q = metrics.next_quarter_label(fin_records)
    if latest is None or next_q is None:
        return "unknown"
    fy_year_str = (latest.get("CurFYSt") or "")[:4]
    if not fy_year_str.isdigit():
        return next_q
    fy_year = int(fy_year_str)
    if metrics.quarter_of(latest) == "FY":
        fy_year += 1
    return f"{fy_year}{next_q}"


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def build_a_layer(code: str, quarter: str | None = None, config: dict | None = None) -> dict:
    """1銘柄ぶんの A レイヤ dict を組む。§4 の各指標をここで束ねる。

    長期株価/TOPIX が無ければ `MissingDataError`(§4.2/§4.2.1)。raw/{code}.jsonl が
    無くても(空リストでも)例外にはしない — fetch し忘れは「取れる指標がゼロ」として
    現れるだけで、準備シート自体は生成できたほうが実用上は良いため。
    """
    config = config or load_config()
    dd_cfg = (config.get("deepdive") or {}) if config else {}
    return_windows = dd_cfg.get("return_windows", DEFAULT_RETURN_WINDOWS)
    volume_windows = dd_cfg.get("volume_windows", DEFAULT_VOLUME_WINDOWS)

    price_df = require_long_prices(code)
    topix_df = require_topix()
    close = price_df.set_index("date")["close"]
    volume = price_df.set_index("date")["volume"]
    topix_close = topix_df.set_index("date")["close"]

    raw_records = load_raw_records(code)
    fin_records = _finstmt_records(raw_records)
    latest = _latest_record(fin_records)

    sector_map = load_sector_map()
    sector = sector_map.get(code)

    watch = load_watch_entry(code)
    calendar = (load_earnings_calendar() or {}).get("by_code", {})
    earnings_date, earnings_source = metrics.next_earnings_date(
        code, calendar, raw_records, watch.get("next_earnings_date_manual")
    )

    return {
        "code": code,
        "name": watch.get("name"),
        "quarter": quarter or _default_quarter_label(fin_records),
        "generated_at": store.now_iso(),
        "data_freshness": {
            "price": {
                "path": str(long_price_path(code)),
                "latest": _iso(close.index.max()) if not close.empty else None,
            },
            "raw": {
                "path": str(jq_raw.raw_path(code)),
                "latest_disc_date": latest.get("DiscDate") if latest else None,
            },
            "benchmark": {
                "path": str(ASSET_DIR / "jp_1306.parquet"),
                "latest": _iso(topix_close.index.max()) if not topix_close.empty else None,
            },
        },
        "progress": {
            "sales": _progress_block(latest, "Sales", "FSales"),
            "op": _progress_block(latest, "OP", "FOP"),
        },
        "progress_vs_history": {
            "sales": _progress_history(fin_records, latest, "Sales", "FSales"),
            "op": _progress_history(fin_records, latest, "OP", "FOP"),
        },
        "guidance_gap": metrics.guidance_gap(_guidance_pairs(fin_records)),
        "revision": metrics.revision_proxy(fin_records),
        "per": _per_block(close, fin_records),
        "returns": {
            _window_label(days): {
                "abs": metrics.return_pct(close, days),
                "topix_relative": metrics.relative_return(close, topix_close, days),
            }
            for days in return_windows
        },
        "sector_relative": _sector_relative_block(code, close, sector, sector_map, return_windows),
        "since_earnings_return": _since_earnings_block(close, latest),
        "volume_ratio": {
            f"{s}_{l}": metrics.volume_ratio(volume, s, l) for s, l in volume_windows
        },
        "next_earnings_date": {"date": earnings_date, "source": earnings_source},
        "omitted": OMITTED_ITEMS,
    }
