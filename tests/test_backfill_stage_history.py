"""scripts/backfill_stage_history.py のテスト。

重点は「backfill 行が実運用行を汚さないこと」の3点:
  1. slice_day が当日値の無い銘柄を落とす(古い行を当日として扱わない)
  2. 出力行に backfilled マーカーが必ず付く
  3. commit が warmup 日と既存日をスキップする

リプレイ本体 (replay_one_day) は VCP/entry/trend_template を丸ごと呼ぶので
ここでは触らない(それぞれのテストが担保している)。ここで担保するのは
「過去データを流し込む配管が実運用の履歴を壊さないこと」だけ。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bf():
    spec = importlib.util.spec_from_file_location(
        "backfill_stage_history", REPO_ROOT / "scripts" / "backfill_stage_history.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _frame(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates), "close": range(len(dates))})


def test_slice_day_keeps_only_codes_trading_that_day(bf):
    frames = {
        "1111": _frame(["2026-07-01", "2026-07-02", "2026-07-03"]),
        # 07-02 が最終日 = 07-03 は値が付いていない(売買停止/上場前)。
        "2222": _frame(["2026-07-01", "2026-07-02"]),
    }
    out = bf.slice_day(frames, pd.Timestamp("2026-07-03"))
    assert set(out) == {"1111"}
    assert len(out["1111"]) == 3


def test_slice_day_cuts_future_rows(bf):
    frames = {"1111": _frame(["2026-07-01", "2026-07-02", "2026-07-03"])}
    out = bf.slice_day(frames, pd.Timestamp("2026-07-02"))
    assert out["1111"]["date"].max() == pd.Timestamp("2026-07-02")


def test_slice_day_drops_code_with_no_history_yet(bf):
    frames = {"1111": _frame(["2026-07-05"])}
    assert bf.slice_day(frames, pd.Timestamp("2026-07-01")) == {}


def test_commit_skips_warmup_and_existing_dates(bf, tmp_path, monkeypatch):
    from src.report import stage_log

    live_path = tmp_path / "stage.jsonl"
    # 実運用行(backfilled キーを持たない)が既にある日 = 上書きしてはいけない。
    live = {"date": "2026-07-29", "code": "7203", "bucket": "order",
            "status": "WATCH_A", "stage": None, "near": False,
            "total_score": 61.2, "has_pivot": True}
    live_path.write_text(json.dumps(live, ensure_ascii=False, sort_keys=True) + "\n",
                         encoding="utf-8")
    monkeypatch.setattr(stage_log, "STAGE_HISTORY_JSONL", live_path)

    rows_path = tmp_path / "stage_rows.jsonl"
    rows = [
        {"date": "2026-03-01", "code": "1", "bucket": "near", "backfilled": True},   # warmup
        {"date": "2026-03-02", "code": "2", "bucket": "near", "backfilled": True},   # 採用
        {"date": "2026-07-29", "code": "3", "bucket": "near", "backfilled": True},   # 既存日
    ]
    rows_path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(bf, "ROWS_PATH", rows_path)
    monkeypatch.setattr(bf, "_load_state", lambda: {"warmup_dates": ["2026-03-01"]})

    assert bf.commit() == 1
    written = [json.loads(l) for l in live_path.read_text(encoding="utf-8").splitlines()]
    assert len(written) == 2
    # 実運用行はそのまま、追記されたのは 03-02 の1行だけ。
    assert written[0]["code"] == "7203" and "backfilled" not in written[0]
    assert written[1] == {"date": "2026-03-02", "code": "2", "bucket": "near",
                          "backfilled": True}


def test_commit_dry_run_writes_nothing(bf, tmp_path, monkeypatch):
    from src.report import stage_log

    live_path = tmp_path / "stage.jsonl"
    live_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(stage_log, "STAGE_HISTORY_JSONL", live_path)
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text(
        json.dumps({"date": "2026-03-02", "code": "2", "bucket": "near",
                    "backfilled": True}, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(bf, "ROWS_PATH", rows_path)
    monkeypatch.setattr(bf, "_load_state", lambda: {"warmup_dates": []})

    assert bf.commit(dry_run=True) == 0
    assert live_path.read_text(encoding="utf-8") == ""


def test_backfilled_rows_classify_identically_to_live(bf):
    """backfill 行は stage_log.build_stage_records の出力そのもの + マーカーだけ。

    列を増やしたり分類を変えたりしていないことを固定する(ずれると live 行と
    同じクエリで扱えなくなる)。
    """
    from src.report import stage_log

    rec = {"code": "7203", "status": None, "pivot": None,
           "setup_stage": {"stage": "forming", "near": True}, "total_score": None}
    rows = stage_log.build_stage_records("2026-07-28", [rec])
    marked = [{**r, "backfilled": True} for r in rows]
    assert set(marked[0]) - set(rows[0]) == {"backfilled"}
    assert marked[0]["bucket"] == "near"
    # ファンダ由来のスコアは過去日に当てられないので必ず None(look-ahead防止)。
    assert marked[0]["total_score"] is None
