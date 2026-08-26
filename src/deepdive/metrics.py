"""A レイヤ(自動取得指標)の算出関数。DESIGN_DEEPDIVE.md §4 / §5 を参照。

**純関数のみ。ファイル I/O・ネットワークは一切しない。** データの取得(parquet 読込・
TOPIX 読込等)は呼び出し側(prep.py)の責務。ここでは渡された値からの計算だけを行う。

件数 n を必ず返す(§1.3 の方針: 取れる範囲で出し、件数を必ず併記する)。
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

import pandas as pd

# CurPerType(会計期間種別)の並び順(1Q → 2Q → 3Q → FY → 翌期1Q ...)。
# フィールド名は src/data/jquants.py の実装(J-Quants v2 実仕様に基づく)に合わせる。
# 4Q は本決算を4Qとして出す会社向けの別名(FYと同じ扱い)。
_QUARTER_ORDER = ["1Q", "2Q", "3Q", "FY"]
_QUARTER_ALIASES = {"4Q": "FY"}


def progress_rate(ytd: float | None, plan: float | None) -> float | None:
    """通期予想に対する累計進捗率(%)。plan が無い/0 なら None。"""
    if ytd is None or plan is None or plan == 0:
        return None
    return ytd / plan * 100


def progress_vs_history(cur: float, history: list[float]) -> dict:
    """同時点進捗率(%)の過去実績との差分(pt)。

    {"diff_pt": 過去平均との差(pt) or None, "n": len(history)}
    history が空なら diff_pt は None(比較対象が無い)。
    """
    n = len(history)
    if n == 0:
        return {"diff_pt": None, "n": 0}
    diff_pt = cur - (sum(history) / n)
    return {"diff_pt": diff_pt, "n": n}


def guidance_gap(pairs: list[tuple[float, float]]) -> dict:
    """期初予想→着地の乖離率(%)。

    pairs: [(期初予想, 着地), ...]
    {"values": [乖離率, ...], "median": 中央値 or None, "n": len(values)}
    forecast が 0 の組は乖離率を定義できないため除外する。
    n < 3 なら median は None(中央値を名乗れる件数ではない、§1.3)。
    """
    values = [
        (actual - forecast) / forecast * 100
        for forecast, actual in pairs
        if forecast != 0
    ]
    n = len(values)
    median = statistics.median(values) if n >= 3 else None
    return {"values": values, "median": median, "n": n}


def percentile_in_series(series: pd.Series, current: float) -> dict:
    """`current` が過去の観測 `series` の中で何%タイルに位置するか(§4.3)。

    順位ベース(線形位置ではない)。外れ値1本でレンジが歪むのを避けるため。
    {"pct": ..., "n": ..., "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    series が空なら pct/start/end は None、n=0。
    """
    n = len(series)
    if n == 0:
        return {"pct": None, "n": 0, "start": None, "end": None}
    pct = (series < current).sum() / n * 100
    idx = series.index
    start = pd.Timestamp(idx.min()).date().isoformat()
    end = pd.Timestamp(idx.max()).date().isoformat()
    return {"pct": pct, "n": n, "start": start, "end": end}


def return_pct(close: pd.Series, days: int) -> float | None:
    """直近 `days` 営業日の騰落率(%)。系列が短すぎれば None。"""
    if days <= 0 or len(close) <= days:
        return None
    latest = close.iloc[-1]
    base = close.iloc[-1 - days]
    if base == 0:
        return None
    return (latest / base - 1) * 100


def relative_return(stock: pd.Series, bench: pd.Series, days: int) -> float | None:
    """TOPIX 比 = 個別の同期間リターン − ベンチマークの同期間リターン(単純差)。"""
    stock_ret = return_pct(stock, days)
    bench_ret = return_pct(bench, days)
    if stock_ret is None or bench_ret is None:
        return None
    return stock_ret - bench_ret


def volume_ratio(volume: pd.Series, short: int, long: int) -> float | None:
    """出来高の短期平均/長期平均(枯れ度の簡易指標)。系列が短すぎれば None。"""
    if short <= 0 or long <= 0 or len(volume) < long:
        return None
    long_mean = volume.tail(long).mean()
    if not long_mean:
        return None
    short_mean = volume.tail(short).mean()
    return short_mean / long_mean


def _quarter_of(rec: dict) -> str | None:
    """レコードの CurPerType(会計期間種別)を _QUARTER_ORDER 上のラベルに正規化する。

    決算短信以外(業績予想修正など)は対象外(DocType に "FinancialStatements"
    を含まない)。次回発表日の推定には確定した決算短信の開示日だけを使う。
    """
    doc_type = rec.get("DocType") or ""
    if "FinancialStatements" not in doc_type:
        return None
    q = rec.get("CurPerType")
    q = _QUARTER_ALIASES.get(q, q)
    return q if q in _QUARTER_ORDER else None


def _estimate_from_raw(raw_records: list[dict]) -> str | None:
    """前年同期の開示日 + 365日(曜日補正なし)で次回発表日を推定する。"""
    dated = sorted(
        (r for r in raw_records if r.get("DiscDate") and _quarter_of(r)),
        key=lambda r: r["DiscDate"],
    )
    if not dated:
        return None
    latest_q = _quarter_of(dated[-1])
    if latest_q is None:
        return None
    next_q = _QUARTER_ORDER[(_QUARTER_ORDER.index(latest_q) + 1) % len(_QUARTER_ORDER)]
    candidates = [r for r in dated if _quarter_of(r) == next_q]
    if not candidates:
        return None
    prev_date = date.fromisoformat(candidates[-1]["DiscDate"])
    return (prev_date + timedelta(days=365)).isoformat()


def next_earnings_date(
    code: str,
    calendar: dict | None,
    raw_records: list[dict],
    manual: str | None,
) -> tuple[str | None, str]:
    """次回決算発表日を3段フォールバックで決める(§1.4)。

    カレンダー(data/earnings_calendar.json の by_code) → raw/{code}.jsonl から
    前年同期の開示日+365日で推定 → watchlist の手入力。出典は
    "カレンダー" / "前年同期からの推定" / "手入力" / "不明" のいずれか。
    推定日を確定日と誤読させないため、出典を返り値に必ず含める。
    """
    cal_date = (calendar or {}).get(code)
    if cal_date:
        return cal_date, "カレンダー"

    estimated = _estimate_from_raw(raw_records or [])
    if estimated:
        return estimated, "前年同期からの推定"

    if manual:
        return manual, "手入力"

    return None, "不明"


# ---------------------------------------------------------------------------
# prep.py 向けの追加ヘルパ(§10 手順6)。いずれも純関数(I/O なし)。
# ---------------------------------------------------------------------------

def quarter_of(rec: dict) -> str | None:
    """`_quarter_of` の公開ラッパ。prep.py 等の他モジュールから使う。"""
    return _quarter_of(rec)


def next_quarter_label(raw_records: list[dict]) -> str | None:
    """直近の確定決算短信の次に来る CurPerType ラベル(例: "2Q")を返す。

    レコードが無ければ None。`_estimate_from_raw` と同じ並び替えロジック。
    """
    dated = sorted(
        (r for r in raw_records if r.get("DiscDate") and _quarter_of(r)),
        key=lambda r: r["DiscDate"],
    )
    if not dated:
        return None
    latest_q = _quarter_of(dated[-1])
    if latest_q is None:
        return None
    return _QUARTER_ORDER[(_QUARTER_ORDER.index(latest_q) + 1) % len(_QUARTER_ORDER)]


def fy_eps_points(raw_records: list[dict]) -> list[tuple[str, float]]:
    """確定した本決算(FY)の (DiscDate, EPS) を日付昇順で返す。

    PER の簡易レンジ(§4.1 △: EPS が2年しか無いので5年レンジは作れない代わりに
    取れる範囲の階段関数で代用する)を作るための入力。EPS が欠損/非数値の行は
    除外する。
    """
    pts: list[tuple[str, float]] = []
    for r in raw_records:
        if _quarter_of(r) != "FY":
            continue
        disc = r.get("DiscDate")
        eps = r.get("EPS")
        if not disc or eps in (None, ""):
            continue
        try:
            pts.append((disc, float(eps)))
        except (TypeError, ValueError):
            continue
    return sorted(pts, key=lambda p: p[0])


def per_series(close: pd.Series, eps_points: list[tuple[str, float]]) -> pd.Series:
    """EPS の階段関数(直近の確定 FY EPS を各日に割り当て)で PER の日次系列を作る。

    `close` は日付(Timestamp 相当)をインデックスに持つ Series を想定。最初の FY
    開示日より前(EPS 未確定の期間)は NaN になり、結果から除外される。
    """
    if not eps_points or close.empty:
        return pd.Series(dtype=float)
    eps_s = pd.Series({pd.Timestamp(d): v for d, v in eps_points}).sort_index()
    aligned = eps_s.reindex(close.index, method="ffill")
    per = close / aligned
    return per[aligned.notna() & (aligned != 0)]


def since_date_return(close: pd.Series, since: str) -> float | None:
    """`since`(YYYY-MM-DD)以降で最初に観測される終値から直近終値までの騰落率(%)。

    `close` は日付インデックスを持つ Series を想定(前回決算発表日からの騰落率に使う)。
    """
    if close.empty:
        return None
    try:
        since_ts = pd.Timestamp(since)
    except (TypeError, ValueError):
        return None
    after = close[close.index >= since_ts]
    if after.empty:
        return None
    base = after.iloc[0]
    if base == 0:
        return None
    return (close.iloc[-1] / base - 1) * 100


def sector_relative_return(stock_ret: float | None, peer_returns: list[float]) -> dict:
    """同業(33業種)の中央値との差(pt)。§4.2.2。

    {"value": 差分 or None, "median": 同業中央値 or None, "n": len(peer_returns)}
    """
    n = len(peer_returns)
    if stock_ret is None or n == 0:
        return {"value": None, "median": None, "n": n}
    median = statistics.median(peer_returns)
    return {"value": stock_ret - median, "median": median, "n": n}


def revision_proxy(raw_records: list[dict]) -> dict:
    """「期中修正の回数と方向」のプロキシ(§4.1 △)。

    設計書は `ForecastRevision` タグを参照するが、深掘りの raw スキーマでは
    独立に確認できていない。直近の会計年度(CurFYSt)内で通期営業利益予想
    (FOP)の値が開示のたびにどう変化したかを数える代替実装として採用した
    (解釈上の選択。next_earnings_date の「前年同期からの推定」と同様、
    log.md に理由を明記して運用する)。

    {"count": 変化回数, "direction": "up"/"down"/"mixed"/None, "n": その年度の開示件数}
    """
    by_fy: dict[str, list[tuple[str, float]]] = {}
    for r in raw_records:
        if "FinancialStatements" not in (r.get("DocType") or ""):
            continue
        fy = r.get("CurFYSt")
        disc = r.get("DiscDate")
        fop = r.get("FOP")
        if not fy or not disc or fop in (None, ""):
            continue
        try:
            fop_v = float(fop)
        except (TypeError, ValueError):
            continue
        by_fy.setdefault(fy, []).append((disc, fop_v))

    if not by_fy:
        return {"count": 0, "direction": None, "n": 0}

    latest_fy = max(by_fy)
    points = sorted(by_fy[latest_fy])
    changes = [b - a for (_, a), (_, b) in zip(points, points[1:]) if b != a]
    count = len(changes)
    if count == 0:
        direction = None
    elif all(c > 0 for c in changes):
        direction = "up"
    elif all(c < 0 for c in changes):
        direction = "down"
    else:
        direction = "mixed"
    return {"count": count, "direction": direction, "n": len(points)}
