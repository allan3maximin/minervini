"""実績の紐付けと的中判定・成績集計。DESIGN_DEEPDIVE.md §3.5 / §5 / §12 を参照。

`actual` コマンドが実行されたときに `(ticker, quarter)` の予想を**全 model_ver ぶん**
引いて判定結果を `outcomes.jsonl` に書く(predictions.jsonl は書き換えない。R2 と
矛盾させないため、判定結果は別ファイルに独立させる)。

**§12: n は永遠に足りない。** ここが返すのは「当たった/外れた」の記録であって
「統計的に有意」ではない。`score()` の出力・フォーマットに信頼区間や有意差の表現を
**入れないこと**。
"""
from __future__ import annotations

import pandas as pd

from src import history_store
from src.deepdive import prep, store


def dir_hit(my_op: float | None, company_op: float | None, actual_op: float | None) -> bool | None:
    """会社予想に対する上振れ/下振れの向きを当てられたか(§3.5)。

    `sign(my_op - company_op) == sign(actual_op - company_op)`。
    どちらかの差分が 0、またはいずれかの値が欠損なら `None`(判定不能。集計から外す)。
    「前年同期比で増益か減益か」ではなく、会社予想を基準線にすることに注意。
    """
    if my_op is None or company_op is None or actual_op is None:
        return None
    my_diff = my_op - company_op
    actual_diff = actual_op - company_op
    if my_diff == 0 or actual_diff == 0:
        return None
    return (my_diff > 0) == (actual_diff > 0)


def level_err_pct(my_op: float | None, actual_op: float | None) -> float | None:
    """水準の誤差(%)。`(my_op - actual_op) / actual_op * 100`。

    `actual_op` が 0 または欠損なら定義できないため None。
    """
    if my_op is None or actual_op is None or actual_op == 0:
        return None
    return (my_op - actual_op) / actual_op * 100


def _base_index(close: pd.Series, disclosed_at: str, timing: str) -> int | None:
    """timing に応じた起点(base)の位置を `close`(日付インデックス)上で探す(§3.5)。

    - 引け後: 起点は発表日終値そのもの → 発表日ちょうどの位置。発表日が営業日として
      系列に存在しなければ判定不能として None(通常は発表日=営業日のはず)。
    - 寄り前 / 場中: 起点は前営業日終値 → 発表日より前の直近営業日の位置。
    """
    if close.empty:
        return None
    try:
        ts = pd.Timestamp(disclosed_at)
    except (TypeError, ValueError):
        return None
    idx = close.index
    pos = idx.searchsorted(ts)  # 発表日以上の最初の位置
    if timing == "引け後":
        if pos < len(idx) and idx[pos] == ts:
            return pos
        return None
    # 寄り前 / 場中: 発表日より前の直近営業日(割り切って同一ロジック。§3.5)
    if pos == 0:
        return None
    return pos - 1


def _return_between(close: pd.Series, base_idx: int, offset: int) -> float | None:
    target_idx = base_idx + offset
    if target_idx >= len(close):
        return None
    base = close.iloc[base_idx]
    target = close.iloc[target_idx]
    if base == 0:
        return None
    return (target / base - 1) * 100


def disclosure_returns(close: pd.Series, disclosed_at: str, timing: str) -> dict:
    """timing 別の起点から `ret_next_day` / `ret_5d` を計算する(§3.5)。

    起点が特定できなければ両方 None を返す(価格系列不足・非営業日など)。
    """
    base_idx = _base_index(close, disclosed_at, timing)
    if base_idx is None:
        return {"ret_next_day": None, "ret_5d": None}
    return {
        "ret_next_day": _return_between(close, base_idx, 1),
        "ret_5d": _return_between(close, base_idx, 5),
    }


def build_outcomes(code: str, quarter: str) -> list[dict]:
    """`(ticker, quarter)` の予想を全 model_ver ぶん引いて的中判定する(§3.5)。

    - `actuals.jsonl` に該当レコードが無ければ空リスト(まだ実績が無いので判定できない。
      例外にはしない — `actual` を書く前に呼ばれることは正常な状態遷移の一部)。
    - 長期株価が無ければ `ret_next_day` / `ret_5d` は None のまま(§4.2 と同じ方針:
      無ければ黙って取りに行かず、無い値は無いと出す)。
    """
    actuals = store.load_last_wins(store.ACTUALS_PATH, ("ticker", "quarter"))
    actual = next(
        (a for a in actuals if a.get("ticker") == code and a.get("quarter") == quarter),
        None,
    )
    if actual is None:
        return []

    preds = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    preds = [p for p in preds if p.get("ticker") == code and p.get("quarter") == quarter]
    if not preds:
        return []

    actual_op = actual.get("op")

    rets = {"ret_next_day": None, "ret_5d": None}
    price_df = prep.load_long_prices(code)
    disclosed_at = actual.get("disclosed_at")
    timing = actual.get("timing")
    if price_df is not None and disclosed_at and timing:
        close = price_df.set_index("date")["close"]
        rets = disclosure_returns(close, disclosed_at, timing)

    out = []
    for p in preds:
        my_op = p.get("my_op")
        company_op = p.get("company_op")
        out.append({
            "ticker": code,
            "quarter": quarter,
            "model_ver": p.get("model_ver"),
            "actual_op": actual_op,
            "my_op": my_op,
            "company_op": company_op,
            "dir_hit": dir_hit(my_op, company_op, actual_op),
            "level_err_pct": level_err_pct(my_op, actual_op),
            "ret_next_day": rets["ret_next_day"],
            "ret_5d": rets["ret_5d"],
        })
    return out


def store_outcomes(code: str, quarter: str) -> list[dict]:
    """`build_outcomes` の結果を `outcomes.jsonl` に追記する(`actual` コマンドから呼ぶ)。

    実績は後勝ちで訂正されうるので、再実行すれば新しい行が積まれる。集計側
    (`score`)は `load_last_wins` で最新の判定を読む。
    """
    recs = build_outcomes(code, quarter)
    if not recs:
        return []
    now = store.now_iso()
    for r in recs:
        r["written_at"] = now
    history_store.append_records(store.OUTCOMES_PATH, recs)
    return recs


def score(by: str = "ver") -> dict:
    """成績サマリを作る(§6 の表)。

    `valid: false`(R1 で無効判定された予想)は集計から除外し、除外件数を
    必ず返す(§6: 「除外件数を隠さないこと。無効票が増えているのは規律が
    緩んでいる兆候」)。

    **§12: 信頼区間・有意差の表現はここに一切持ち込まない。** n は常に小さく、
    このツールが記録するのは「当たった/外れた」であって「有意に当たる」ではない。

    Returns:
        {"rows": [{"group": ver または ticker, "n": 判定済み件数,
                    "hit": dir_hit=True の件数, "hit_n": dir_hit が None でない件数,
                    "avg_level_err_pct": 平均誤差(%) or None,
                    "hit_rate_pct": hit/hit_n*100 or None}, ...],
         "excluded": 発表日以降に記入され除外された件数}
    """
    if by not in ("ver", "ticker"):
        raise ValueError(f"score: by は ver|ticker のいずれかにしてください: {by!r}")
    key_field = "model_ver" if by == "ver" else "ticker"

    all_preds = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    valid_preds = [p for p in all_preds if p.get("valid")]
    excluded = len(all_preds) - len(valid_preds)

    outcomes = store.load_last_wins(store.OUTCOMES_PATH, ("ticker", "quarter", "model_ver"))
    by_key = {(o.get("ticker"), o.get("quarter"), o.get("model_ver")): o for o in outcomes}

    groups: dict[str, list[dict]] = {}
    for p in valid_preds:
        key = (p.get("ticker"), p.get("quarter"), p.get("model_ver"))
        o = by_key.get(key)
        if o is None:
            continue  # まだ actual が来ていない = 判定不能。除外件数(R1)とは別枠
        groups.setdefault(p.get(key_field), []).append(o)

    rows = []
    for group_key in sorted(groups):
        items = groups[group_key]
        n = len(items)
        dir_hits = [o.get("dir_hit") for o in items if o.get("dir_hit") is not None]
        hit_n = len(dir_hits)
        hit = sum(1 for h in dir_hits if h)
        errs = [o.get("level_err_pct") for o in items if o.get("level_err_pct") is not None]
        avg_err = sum(errs) / len(errs) if errs else None
        hit_rate = (hit / hit_n * 100) if hit_n else None
        rows.append({
            "group": group_key,
            "n": n,
            "hit": hit,
            "hit_n": hit_n,
            "avg_level_err_pct": avg_err,
            "hit_rate_pct": hit_rate,
        })
    return {"rows": rows, "excluded": excluded}


def format_score(result: dict) -> str:
    """`score()` の戻り値を §6 の表形式テキストにする。信頼区間等は付けない(§12)。"""
    lines = []
    for row in result["rows"]:
        avg = "n/a" if row["avg_level_err_pct"] is None else f"{row['avg_level_err_pct']:+.0f}%"
        rate = "n/a" if row["hit_rate_pct"] is None else f"{row['hit_rate_pct']:.0f}%"
        lines.append(
            f"{row['group']:<6}{row['n']:<4}{row['hit']}/{row['hit_n']:<4}{avg:>6}{rate:>6}"
        )
    if not lines:
        lines.append("判定済みの実績なし")
    if result["excluded"]:
        lines.append(f"（発表日以降に記入され除外: {result['excluded']}件）")
    return "\n".join(lines)
