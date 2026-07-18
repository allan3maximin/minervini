"""ポジション管理: 保有銘柄ビュー + 売りシグナル (manual/positions.csv を読み、
docs/data/positions.json を生成)。

manual/fundamentals.csv (src/data/fundamentals.py) と同じ「手で編集するCSVを
パイプラインが読む」パターンを踏襲する。書き込みUIは作らない
(passkeyAuthEnabled: false のため、GitHub web編集/ローカル編集での運用を想定)。
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

from src.config import REPO_ROOT

DEFAULT_CSV_PATH = REPO_ROOT / "manual" / "positions.csv"
POSITIONS_JSON_PATH = REPO_ROOT / "docs" / "data" / "positions.json"
CSV_COLUMNS = ["code", "entry_date", "entry_price", "shares", "initial_stop", "current_stop", "memo"]


def load_positions_csv(path=None) -> tuple[list[dict], list[str]]:
    """manual/positions.csv を読む。無い/空なら ([], []) を返す。

    パース不能行(必須列の欠損・型不正)は警告に載せてスキップする。
    """
    path = path or DEFAULT_CSV_PATH
    warnings: list[str] = []
    if not path.exists():
        return [], warnings

    raw = pd.read_csv(path, dtype={"code": str})
    if raw.empty:
        return [], warnings

    positions: list[dict] = []
    for i, row in raw.iterrows():
        line_no = i + 2  # header is line 1
        code = str(row.get("code", "")).strip()
        if not code or code == "nan":
            warnings.append(f"行{line_no}: codeが空のためスキップ")
            continue

        try:
            entry_date_str = str(row["entry_date"]).strip()
            datetime.strptime(entry_date_str, "%Y-%m-%d")
            entry_price = float(row["entry_price"])
            shares = int(row["shares"])
            initial_stop = float(row["initial_stop"])
            current_stop = float(row["current_stop"])
        except (KeyError, ValueError, TypeError):
            warnings.append(f"行{line_no}: 不正な形式のためスキップ (code={code!r})")
            continue

        memo = row.get("memo", "")
        memo = "" if pd.isna(memo) else str(memo).strip()

        positions.append(
            {
                "code": code,
                "entry_date": entry_date_str,
                "entry_price": entry_price,
                "shares": shares,
                "initial_stop": initial_stop,
                "current_stop": current_stop,
                "memo": memo,
            }
        )

    return positions, warnings


def _compute_sell_signals(close: float, ma50, ma200, current_stop: float, entry_price: float, r_multiple: float | None) -> list[str]:
    signals: list[str] = []
    if close < current_stop:
        signals.append("STOP_BREACH")
    if not pd.isna(ma50) and close < ma50:
        signals.append("MA50_BREAK")
    if not pd.isna(ma200) and close < ma200:
        signals.append("MA200_BREAK")
    if r_multiple is not None and r_multiple >= 2.0:
        signals.append("TAKE_PROFIT_ZONE")
    if r_multiple is not None and r_multiple >= 1.0 and current_stop < entry_price:
        signals.append("BREAKEVEN_READY")
    return signals


def _margin_for(code: str, latest_row: dict | None, margin_store: dict | None) -> dict | None:
    """表示専用の信用残メトリクス。総合スコアには一切使わない。失敗してもNoneに落とす。"""
    try:
        from src.data.margin import build_margin_metrics

        return build_margin_metrics(code, latest_row, store=margin_store)
    except Exception:
        return None


def build_positions_report(
    positions: list[dict],
    indicator_by_code: dict,
    name_by_code: dict,
    today: date | None = None,
    margin_store: dict | None = None,
) -> dict:
    """各ポジションの現在値・R倍数・売りシグナルを計算する。

    indicator_by_code[code] は日足指標付きDataFrame(close/ma50/ma200等を含む)を
    想定。ユニバース外/上場廃止でcodeが無ければ data_missing=True の数値null行を出す。

    margin_store は data/margin_weekly.json の辞書(呼び出し側で1回だけ読み込んで
    渡す想定)。信用残(需給)は表示専用レイヤーでスコアには使わない。
    """
    today = today or date.today()
    warnings: list[str] = []
    out: list[dict] = []

    for pos in positions:
        code = pos["code"]
        entry_price = pos["entry_price"]
        shares = pos["shares"]
        initial_stop = pos["initial_stop"]
        current_stop = pos["current_stop"]
        entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d").date()

        record = {
            "code": code,
            "name": name_by_code.get(code, ""),
            "entry_date": pos["entry_date"],
            "entry_price": entry_price,
            "shares": shares,
            "initial_stop": initial_stop,
            "current_stop": current_stop,
            "memo": pos.get("memo", ""),
            "days_held": (today - entry_dt).days,
        }

        df = indicator_by_code.get(code)
        if df is None or df.empty:
            record.update(
                {
                    "data_missing": True,
                    "close": None,
                    "pl_pct": None,
                    "pl_jpy": None,
                    "r_multiple": None,
                    "dist_to_stop_pct": None,
                    "sell_signals": [],
                    "margin": _margin_for(code, None, margin_store),
                }
            )
            out.append(record)
            continue

        latest = df.iloc[-1]
        close = float(latest["close"])
        ma50 = latest.get("ma50")
        ma200 = latest.get("ma200")

        if entry_price <= initial_stop:
            r_multiple = None
            warnings.append(
                f"{code}: entry_price({entry_price}) <= initial_stop({initial_stop}) のためR倍数を計算できません"
            )
        else:
            r_multiple = round((close - entry_price) / (entry_price - initial_stop), 3)

        record.update(
            {
                "data_missing": False,
                "close": close,
                "pl_pct": round((close / entry_price - 1) * 100, 2),
                "pl_jpy": round((close - entry_price) * shares, 2),
                "r_multiple": r_multiple,
                "dist_to_stop_pct": round((close - current_stop) / close * 100, 2),
                "sell_signals": _compute_sell_signals(close, ma50, ma200, current_stop, entry_price, r_multiple),
                "margin": _margin_for(code, latest.to_dict(), margin_store),
            }
        )
        out.append(record)

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "warnings": warnings,
        "positions": out,
    }


def write_positions_json(report: dict, path=None) -> None:
    from src.report.secure_io import write_docs_json
    write_docs_json(path or POSITIONS_JSON_PATH, report)
