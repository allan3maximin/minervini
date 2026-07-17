"""本番フォワード検証用 枯れ度(DRY-UP)ログ。

日次パイプラインで WATCH_A/WATCH_B 銘柄の枯れ度レイヤーを1行1レコードのJSONLで
``data/dryup_log.jsonl`` に追記し、後日 outcome(ブレイク成否)を解決する。米株由来の
暫定閾値が東証で予測力を持つかを、バックテスト(src/backtest.py)とは独立に本番データで
検証するのが目的。集計は tools/aggregate_dryup_log.py が90日窓で行う。

枯れ度メトリクスは必ず indicators.build_dryup_layer から取得する(再実装ドリフト=
summary.py事故の再発防止)。このモジュールは値を生成しない。ログ形状と outcome 解決
のみを担う。

outcome の状態遷移:
    null -> breakout -> breakout_ok / breakout_failed
    null -> broken
    null -> expired
(breakout は中間状態。追跡日数が足りるまで breakout_ok/failed へは進めない。)

閾値(営業日):
    BREAKOUT_WAIT_DAYS = 20  記録日から何営業日以内のブレイクを待つか
    POST_BREAKOUT_DAYS = 10  ブレイク後、成否を判定するまでの追跡日数

「broken」の代理定義(重要): 本レコードは pivot は持つが、V7 の基準となるベース安値
(stop_ref_low)は持たない(§4でレコード形状が固定されているため)。したがって
"broken(V7即違反相当)" は「pivot を一度も超えないまま、終値が pivot*(1-BROKEN_DROP_PCT)
を下回った」を代理条件とする。真の V7 違反(直近スイング安値の切り下げ)ではなく pivot
基準の代理である点に注意。閾値は config['entry']['stop_loss_pct']*2 を既定とする。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.config import REPO_ROOT, load_config

DRYUP_LOG_PATH = REPO_ROOT / "data" / "dryup_log.jsonl"

BREAKOUT_WAIT_DAYS = 20
POST_BREAKOUT_DAYS = 10

# 記録するレコードのキー順(1行1JSON)。outcome/outcome_date は解決前は None。
RECORD_KEYS = (
    "date",
    "code",
    "status",
    "dryup_med_10_50",
    "dryup_avg_5_50",
    "tightness_10d",
    "is_tightest_in_base",
    "shakeout_detected",
    "dist_to_pivot",
    "pivot",
    # 2026-07-15追加: ブレイク検出時の出来高倍率(volume/vol_ma50)。記録日=WATCH時点では
    # ブレイク前なので None。resolve でブレイクを検出した日に埋める。フォワードで出来高倍率帯
    # (1.4-2.0/2.0-3.0/3.0+)を蓄積し、バックテストのn過小(3.0+はn=5)を補うのが目的。
    "vol_ratio_at_breakout",
    "outcome",
    "outcome_date",
)

TERMINAL_OUTCOMES = {"breakout_ok", "breakout_failed", "broken", "expired"}


def _broken_drop_pct(config: dict) -> float:
    return float(config["entry"]["stop_loss_pct"]) * 2.0


# ---------------------------------------------------------------------------
# レコード生成 / 追記 / 読み込み
# ---------------------------------------------------------------------------

def build_log_record(date_str: str, code: str, status: str, dryup_layer: dict, pivot: float | None) -> dict:
    """1日1銘柄のログレコードを組み立てる。

    dryup_layer は indicators.build_dryup_layer の戻り値(4メトリクス + shakeout_detected
    + dist_to_pivot)。outcome/outcome_date は未解決なので None で初期化する。
    """
    return {
        "date": date_str,
        "code": code,
        "status": status,
        "dryup_med_10_50": dryup_layer.get("dryup_med_10_50"),
        "dryup_avg_5_50": dryup_layer.get("dryup_avg_5_50"),
        "tightness_10d": dryup_layer.get("tightness_10d"),
        "is_tightest_in_base": dryup_layer.get("is_tightest_in_base"),
        "shakeout_detected": dryup_layer.get("shakeout_detected"),
        "dist_to_pivot": dryup_layer.get("dist_to_pivot"),
        "pivot": pivot,
        "vol_ratio_at_breakout": None,
        "outcome": None,
        "outcome_date": None,
    }


def append_records(records: list[dict], path: Path = DRYUP_LOG_PATH) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_records(path: Path = DRYUP_LOG_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                # 破損行(途中クラッシュの書きかけ等)はスキップして継続。
                print(f"WARNING: {path}:{line_no} corrupted JSONL line skipped ({e})")
                continue
            # 2026-07-15追加キーを旧レコードにも補完(writeback時の形状統一)。
            rec.setdefault("vol_ratio_at_breakout", None)
            out.append(rec)
    return out


def write_records(records: list[dict], path: Path = DRYUP_LOG_PATH) -> None:
    """全レコードを書き戻す(outcome 解決後の一括更新に使う)。1行1JSONは不変。

    tmp書き込み→os.replace のアトミック置換(途中クラッシュで全ログ消失しない)。"""
    from src.utils_io import atomic_write_text
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# outcome 解決
# ---------------------------------------------------------------------------

def _dates_and_prices(df: pd.DataFrame) -> tuple[list[str], list[float], list[float]]:
    """df から (dateの文字列YYYY-MM-DD, close, low) を昇順で取り出す。"""
    dates = [
        d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10]
        for d in df["date"].tolist()
    ]
    close = [float(v) for v in df["close"].tolist()]
    low = [float(v) for v in df["low"].tolist()]
    return dates, close, low


def _vol_ratio_by_date(df: pd.DataFrame) -> dict[str, float]:
    """date文字列 -> volume/vol_ma50。vol_ma50 が無い/0 の行は載せない。"""
    if "volume" not in df.columns or "vol_ma50" not in df.columns:
        return {}
    out: dict[str, float] = {}
    for d, vol, vma in zip(df["date"].tolist(), df["volume"].tolist(), df["vol_ma50"].tolist()):
        if vma is None or pd.isna(vma) or vma == 0 or pd.isna(vol):
            continue
        key = d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10]
        out[key] = round(float(vol) / float(vma), 3)
    return out


def resolve_record(record: dict, df: pd.DataFrame, config: dict) -> dict:
    """1レコードの outcome を(可能なら)前進させる。df は当該銘柄のフルhistory。

    冪等: 既に終端(TERMINAL_OUTCOMES)なら何もしない。null/breakout のときだけ、
    利用可能な将来バーの範囲で状態を進める。将来バーが足りなければ据え置き(後日再解決)。
    """
    if record.get("outcome") in TERMINAL_OUTCOMES:
        return record
    pivot = record.get("pivot")
    if pivot is None:
        return record

    dates, close, low = _dates_and_prices(df)
    rec_date = record["date"]
    # 記録日より後のバーのみを対象(記録は当日EODのスナップショット)。
    future = [(d, c, lo) for d, c, lo in zip(dates, close, low) if d > rec_date]

    broken_pct = _broken_drop_pct(config)
    vol_ratio_by_date = _vol_ratio_by_date(df)

    # --- まだブレイクしていない(outcome is None): 待機窓を走査 ---
    if record.get("outcome") is None:
        wait = future[:BREAKOUT_WAIT_DAYS]
        for d, c, _lo in wait:
            if c > pivot:
                record["outcome"] = "breakout"
                record["outcome_date"] = d
                # 2026-07-15: ブレイク日の出来高倍率を記録(フォワードの出来高倍率帯蓄積用)。
                record["vol_ratio_at_breakout"] = vol_ratio_by_date.get(d)
                break
            if c < pivot * (1 - broken_pct):
                record["outcome"] = "broken"
                record["outcome_date"] = d
                return record
        else:
            # 待機窓を最後まで見て決着せず。窓ぶんの将来バーが揃っていれば expired。
            if len(future) >= BREAKOUT_WAIT_DAYS:
                record["outcome"] = "expired"
                record["outcome_date"] = wait[-1][0] if wait else rec_date
            return record  # まだデータ不足 or expired 確定

    # --- ブレイク済み(outcome == 'breakout'): 成否を追跡 ---
    if record.get("outcome") == "breakout":
        bo_date = record["outcome_date"]
        entry_price = None
        for d, c, _lo in zip(dates, close, low):
            if d == bo_date:
                entry_price = c
                break
        if entry_price is None:
            return record
        stop = entry_price * (1 - float(config["entry"]["stop_loss_pct"]))
        post = [(d, c, lo) for d, c, lo in future if d > bo_date][:POST_BREAKOUT_DAYS]
        for d, _c, lo in post:
            if lo <= stop:
                record["outcome"] = "breakout_failed"
                record["outcome_date"] = d
                return record
        if len(post) >= POST_BREAKOUT_DAYS:
            record["outcome"] = "breakout_ok"
            record["outcome_date"] = post[-1][0]
        # 追跡日数が足りなければ 'breakout' のまま据え置き(後日再解決)
    return record


def resolve_outcomes(records: list[dict], frames: dict, config: dict | None = None) -> int:
    """全レコードの outcome を in-place で前進させる。戻り値は更新件数。

    frames: code -> DataFrame(date, close, low を含むフルhistory)。当該銘柄が
    frames に無いレコードはスキップ(据え置き)。
    """
    config = config or load_config()
    changed = 0
    for record in records:
        if record.get("outcome") in TERMINAL_OUTCOMES:
            continue
        df = frames.get(record["code"])
        if df is None or len(df) == 0:
            continue
        before = (record.get("outcome"), record.get("outcome_date"))
        resolve_record(record, df, config)
        if (record.get("outcome"), record.get("outcome_date")) != before:
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# パイプライン統合ヘルパ
# ---------------------------------------------------------------------------

def log_and_resolve(new_records: list[dict], frames: dict, config: dict | None = None,
                    path: Path = DRYUP_LOG_PATH) -> dict:
    """日次: 既存レコードの outcome を解決 → 当日分を追記 → 全書き戻し。

    追記(append)ではなく全書き戻しにするのは、既存レコードの outcome 列を更新する
    ため。1行1JSONのJSONL形状は保たれる。
    """
    config = config or load_config()
    existing = load_records(path)
    resolved = resolve_outcomes(existing, frames, config)
    # 同日再実行ガード: 同じ(date, code)が既にあれば追記しない(パイプラインを
    # 同日に複数回走らせるとフォワードログが重複していく問題の再発防止)。
    seen = {(r.get("date"), r.get("code")) for r in existing}
    appended = [r for r in new_records if (r.get("date"), r.get("code")) not in seen]
    all_records = existing + appended
    write_records(all_records, path)
    return {"resolved": resolved, "appended": len(appended), "total": len(all_records)}
