"""src/report/stage_log.py (監視バケットの日次記録) のテスト。

重点は「docs/assets/app.js のフィルタ分岐と同じ結論になること」。ここがずれると
計測値と画面の見え方が食い違い、線引きの根拠として使えなくなる。
"""
from __future__ import annotations

from src.report import stage_log


def _rec(code, status=None, pivot=None, stage=None, near=False, score=None):
    setup = None if stage is None else {"stage": stage, "near": near}
    return {
        "code": code, "status": status, "pivot": pivot,
        "setup_stage": setup, "total_score": score,
    }


def test_entry_status_with_pivot_is_order():
    assert stage_log.classify_bucket(_rec("1", "WATCH_A", pivot=1200.0)) == "order"
    assert stage_log.classify_bucket(_rec("2", "BREAKOUT", pivot=900.0)) == "order"


def test_entry_status_without_pivot_is_watch():
    # ロックが切れた直後などピボットが解決できていない待機。発注はできない。
    assert stage_log.classify_bucket(_rec("3", "WATCH_A")) == "watch"


def test_extended_and_stale_are_cooled():
    # 追撃禁止。ピボットが残っていても order には入れない。
    assert stage_log.classify_bucket(_rec("4", "EXTENDED", pivot=100.0)) == "cooled"
    assert stage_log.classify_bucket(_rec("5", "STALE")) == "cooled"


def test_status_takes_precedence_over_setup_stage():
    # app.js の cardBadgeKey は status が付いていれば setup_stage を見ない。
    rec = _rec("6", "WATCH_A", pivot=10.0, stage="forming", near=True)
    assert stage_log.classify_bucket(rec) == "order"


def test_near_beats_stage_name():
    for stage in ("forming", "rejected", "fresh_high", "volatile"):
        rec = _rec("7", stage=stage, near=True)
        assert stage_log.classify_bucket(rec) == "near", stage


def test_stage_names_pass_through():
    assert stage_log.classify_bucket(_rec("8", stage="forming")) == "forming"
    assert stage_log.classify_bucket(_rec("9", stage="fresh_high")) == "fresh_high"
    assert stage_log.classify_bucket(_rec("10", stage="rejected")) == "rejected"


def test_volatile_and_no_base_are_inactive():
    assert stage_log.classify_bucket(_rec("11", stage="volatile")) == "inactive"
    assert stage_log.classify_bucket(_rec("12", stage="no_base")) == "inactive"
    assert stage_log.classify_bucket(_rec("13", stage="???")) == "inactive"


def test_no_status_and_no_setup_stage_is_unknown():
    # 異常系。0件でない日は分類漏れなので気付けるようにバケットを残す。
    assert stage_log.classify_bucket(_rec("14")) == "unknown"


def test_funnel_keeps_zero_buckets():
    funnel = stage_log.build_stage_funnel([_rec("1", "WATCH_A", pivot=1.0)])
    assert set(funnel) == set(stage_log.BUCKETS)
    assert funnel["order"] == 1
    assert funnel["near"] == 0  # 0でもキーが消えない


def test_funnel_total_equals_input():
    recs = [
        _rec("1", "WATCH_A", pivot=1.0), _rec("2", "WATCH_A"),
        _rec("3", "STALE"), _rec("4", stage="forming", near=True),
        _rec("5", stage="fresh_high"), _rec("6"),
    ]
    assert sum(stage_log.build_stage_funnel(recs).values()) == len(recs)


def test_build_stage_records_shape():
    rows = stage_log.build_stage_records(
        "2026-07-29", [_rec("7203", stage="forming", near=True, score=61.2)])
    assert rows == [{
        "date": "2026-07-29", "code": "7203", "bucket": "near",
        "status": None, "stage": "forming", "near": True,
        "total_score": 61.2, "has_pivot": False,
    }]


def test_update_stage_history_appends(tmp_path, monkeypatch):
    path = tmp_path / "history" / "stage.jsonl"
    monkeypatch.setattr(stage_log, "STAGE_HISTORY_JSONL", path)
    recs = [_rec("1", "WATCH_A", pivot=1.0), _rec("2", stage="rejected")]
    n = stage_log.update_stage_history("2026-07-29", recs, {"history_keep_days": 90})
    assert n == 2

    from src.history_store import load_deduped
    # 同日再実行は後勝ちで上書き相当 (追記専用の想定どおり)
    stage_log.update_stage_history("2026-07-29", recs, {"history_keep_days": 90})
    rows = load_deduped(path, ("code", "date"))
    assert len(rows) == 2
