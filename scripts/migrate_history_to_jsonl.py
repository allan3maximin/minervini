#!/usr/bin/env python3
"""旧「全量書き戻しJSON」の履歴を、追記専用JSONLへ一括変換するCLI (2026-07-27)。

    python scripts/migrate_history_to_jsonl.py --dry-run
    python scripts/migrate_history_to_jsonl.py

対象:
    data/status_history.json  -> data/history/status.jsonl   (キー: code + date)
    data/sector_history.json  -> data/history/sector.jsonl   (キー: date)

冪等性: 変換後に dedup (後勝ち) をかけてから書き出すので、2回流しても行は増えない。
旧JSONは**削除しない**。移行後に問題が無いことを確認してから手で消すこと
(消さなくても、JSONLが存在する限りコード側は旧JSONを読まない)。

なぜ移行するか:
    旧方式は1日ぶんの追記でもファイル全行が書き換わるため、日次コミットの差分が
    毎回数千行に膨れていた。JSONLへ追記だけすれば差分は追記行のみになり、
    さらに DuckDB の read_json_auto() でそのまま SQL 分析できる
    (python -m src.analyze)。設計の詳細は src/history_store.py の docstring。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import REPO_ROOT  # noqa: E402
from src.history_store import (  # noqa: E402
    append_records,
    compact,
    count_lines,
    load_deduped,
)
from src.utils_io import safe_load_json  # noqa: E402

STATUS_JSON = REPO_ROOT / "data" / "status_history.json"
STATUS_JSONL = REPO_ROOT / "data" / "history" / "status.jsonl"
SECTOR_JSON = REPO_ROOT / "data" / "sector_history.json"
SECTOR_JSONL = REPO_ROOT / "data" / "history" / "sector.jsonl"


def _status_rows() -> list[dict]:
    """{code: [entry, ...]} を {code, date, status, pivot, stop_ref_low} の行へ展開。"""
    legacy = safe_load_json(STATUS_JSON, {})
    if not isinstance(legacy, dict):
        return []
    rows = []
    for code, entries in legacy.items():
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            rows.append({
                "code": str(code),
                "date": entry.get("date"),
                "status": entry.get("status"),
                "pivot": entry.get("pivot"),
                "stop_ref_low": entry.get("stop_ref_low"),
            })
    return rows


def _sector_rows() -> list[dict]:
    """{"history": [entry, ...]} の entry をそのまま1行1エントリで展開。"""
    legacy = safe_load_json(SECTOR_JSON, {"history": []})
    if not isinstance(legacy, dict):
        return []
    return [e for e in legacy.get("history", []) if isinstance(e, dict)]


def _migrate(name: str, src: Path, dst: Path, rows: list[dict],
             key_fields: tuple[str, ...], dry_run: bool) -> None:
    before = count_lines(dst)
    if not src.exists():
        print(f"[{name}] 旧ファイルが無い ({src}) — スキップ")
        return
    if not rows:
        print(f"[{name}] 旧ファイルに有効なレコードが無い — スキップ")
        return

    if dry_run:
        # 既存JSONLと合わせた場合の最終件数を、書き込まずに見積もる。
        merged = {tuple(r.get(f) for f in key_fields): r
                  for r in load_deduped(dst, key_fields) + rows}
        print(f"[{name}] dry-run: 旧JSON {len(rows)} 行 + 既存JSONL {before} 行 "
              f"→ dedup後 {len(merged)} 行 ({dst})")
        return

    append_records(dst, rows)
    # 2回流しても増えないよう、追記後に必ず dedup (keep_days は指定せず間引きしない)。
    compact(dst, key_fields)
    after = count_lines(dst)
    print(f"[{name}] 旧JSON {len(rows)} 行を取り込み: {before} 行 → {after} 行 ({dst})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="書き込まずに変換後の件数だけ表示する")
    args = parser.parse_args(argv)

    _migrate("status_history", STATUS_JSON, STATUS_JSONL, _status_rows(),
             ("code", "date"), args.dry_run)
    _migrate("sector_history", SECTOR_JSON, SECTOR_JSONL, _sector_rows(),
             ("date",), args.dry_run)

    if not args.dry_run:
        print("\n移行完了。旧JSON (data/status_history.json / data/sector_history.json) は")
        print("念のため残してある。動作確認後に手で削除してよい。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
