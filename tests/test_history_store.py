"""src/history_store.py (追記専用JSONL履歴ストア) のテスト。

重点は「追記専用で、旧方式の『同日再実行は上書き』と同じ結果になること」。
"""
from __future__ import annotations

from src.history_store import (
    append_records,
    calendar_keep_days,
    compact,
    count_lines,
    iter_records,
    load_deduped,
    needs_compaction,
)


def test_append_and_load_roundtrip(tmp_path):
    path = tmp_path / "h" / "status.jsonl"
    append_records(path, [
        {"code": "7203", "date": "2026-07-01", "status": "WATCH_A"},
        {"code": "6758", "date": "2026-07-01", "status": "BREAKOUT"},
    ])
    assert path.exists()  # 親ディレクトリごと作られる
    rows = load_deduped(path, ("code", "date"))
    assert len(rows) == 2
    assert {r["code"] for r in rows} == {"7203", "6758"}


def test_append_empty_is_noop(tmp_path):
    path = tmp_path / "empty.jsonl"
    append_records(path, [])
    assert not path.exists()


def test_last_write_wins(tmp_path):
    """同日再実行 = 同じキーの行を追記 → 後の行が勝つ(旧方式の『上書き』と同義)。"""
    path = tmp_path / "status.jsonl"
    append_records(path, [{"code": "7203", "date": "2026-07-01", "status": "WATCH_A"}])
    append_records(path, [{"code": "7203", "date": "2026-07-01", "status": "BREAKOUT"}])

    rows = load_deduped(path, ("code", "date"))
    assert len(rows) == 1
    assert rows[0]["status"] == "BREAKOUT"
    # ファイル自体には2行残っている(追記専用なので)
    assert count_lines(path) == 2


def test_dedup_order_follows_last_occurrence(tmp_path):
    """並び順は『最終出現位置』。再実行された日が末尾に来て時系列順が保たれる。"""
    path = tmp_path / "sector.jsonl"
    append_records(path, [
        {"date": "2026-07-01", "v": 1},
        {"date": "2026-07-02", "v": 2},
    ])
    append_records(path, [{"date": "2026-07-01", "v": 99}])

    rows = load_deduped(path, ("date",))
    assert [r["date"] for r in rows] == ["2026-07-02", "2026-07-01"]
    assert rows[-1]["v"] == 99


def test_corrupted_lines_are_skipped(tmp_path):
    """1行壊れても残りは読める(全量書き戻しJSONだと全履歴が飛んでいた)。"""
    path = tmp_path / "broken.jsonl"
    append_records(path, [{"date": "2026-07-01", "v": 1}])
    with open(path, "a", encoding="utf-8") as f:
        f.write("{ this is not json\n")
        f.write("[1, 2, 3]\n")          # dict ではないのでスキップ
        f.write("\n")                    # 空行は黙って無視
    append_records(path, [{"date": "2026-07-02", "v": 2}])

    rows = list(iter_records(path))
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02"]


def test_iter_records_missing_file(tmp_path):
    assert list(iter_records(tmp_path / "nope.jsonl")) == []
    assert load_deduped(tmp_path / "nope.jsonl", ("date",)) == []
    assert count_lines(tmp_path / "nope.jsonl") == 0


def test_compact_dedups_and_prunes_old(tmp_path):
    path = tmp_path / "status.jsonl"
    append_records(path, [
        {"code": "7203", "date": "2026-01-01", "status": "OLD"},
        {"code": "7203", "date": "2026-07-01", "status": "WATCH_A"},
        {"code": "7203", "date": "2026-07-01", "status": "BREAKOUT"},  # 重複
    ])
    removed = compact(path, ("code", "date"), keep_days=30, today="2026-07-10")

    rows = load_deduped(path, ("code", "date"))
    assert [r["date"] for r in rows] == ["2026-07-01"]
    assert rows[0]["status"] == "BREAKOUT"   # dedupは後勝ちのまま
    assert removed == 2                       # 重複1行 + 期限切れ1行
    assert count_lines(path) == 1             # 全書き直しされている


def test_compact_without_keep_days_only_dedups(tmp_path):
    path = tmp_path / "s.jsonl"
    append_records(path, [
        {"date": "2020-01-01", "v": 1},
        {"date": "2020-01-01", "v": 2},
    ])
    compact(path, ("date",))
    rows = load_deduped(path, ("date",))
    assert len(rows) == 1 and rows[0]["v"] == 2


def test_compact_keeps_unparseable_dates(tmp_path):
    """日付が読めないという理由で履歴を静かに消さない。"""
    path = tmp_path / "s.jsonl"
    append_records(path, [{"date": None, "v": 1}, {"date": "2026-07-01", "v": 2}])
    compact(path, ("v",), keep_days=1, today="2026-07-10")
    assert any(r["v"] == 1 for r in load_deduped(path, ("v",)))


def test_compact_empty_file(tmp_path):
    assert compact(tmp_path / "nope.jsonl", ("date",)) == 0


def test_needs_compaction_threshold(tmp_path):
    path = tmp_path / "s.jsonl"
    append_records(path, [{"date": f"2026-07-{i:02d}"} for i in range(1, 6)])
    assert needs_compaction(path, max_lines=4) is True
    assert needs_compaction(path, max_lines=5) is False
    assert needs_compaction(path, max_lines=10) is False


def test_calendar_keep_days_has_slack():
    """営業日ベースの件数を暦日へ換算するときは必ず余裕を持たせる。"""
    assert calendar_keep_days(90) > 90
    assert calendar_keep_days(400) > 400


def test_keys_are_sorted_for_stable_diffs(tmp_path):
    """キー順がぶれると内容が同じでも git 差分が出るので sort_keys を固定している。"""
    path = tmp_path / "s.jsonl"
    append_records(path, [{"b": 2, "a": 1}])
    line = path.read_text(encoding="utf-8").strip()
    assert line == '{"a": 1, "b": 2}'


def test_non_ascii_is_not_escaped(tmp_path):
    """セクター名(日本語)がそのまま読めること。"""
    path = tmp_path / "s.jsonl"
    append_records(path, [{"sector": "電気機器"}])
    assert "電気機器" in path.read_text(encoding="utf-8")
