"""履歴JSONLを DuckDB で分析するCLI (2026-07-27追加)。

    python -m src.analyze --list
    python -m src.analyze --preset status-daily
    python -m src.analyze --sql "SELECT date, count(*) FROM status GROUP BY 1 ORDER BY 1"

data/history/*.jsonl をファイル名そのままのビュー (status / sector) として登録する。
DuckDB の read_json_auto() は JSONL をスキーマ推論込みでそのまま読めるので、
中間のETLもDBファイルも要らない — 追記専用JSONLをそのままクエリできるのが
この構成を選んだ理由(src/history_store.py の docstring 参照)。

注意: JSONL は追記専用で同じキーの行が複数ありうる。素の SELECT は重複を含むので、
「最新の1行だけ」を見たい場合は下の DEDUPED_VIEWS のように QUALIFY で絞ること。
プリセットは全てdedup済みのビュー (status_latest / sector_latest) を使っている。
"""
from __future__ import annotations

import argparse
import sys

from src.config import REPO_ROOT

HISTORY_DIR = REPO_ROOT / "data" / "history"

# ビュー名 -> (JSONLファイル名, dedupキー)
SOURCES = {
    "status": ("status.jsonl", ["code", "date"]),
    "sector": ("sector.jsonl", ["date"]),
    "stage": ("stage.jsonl", ["code", "date"]),
}

PRESETS = {
    "status-daily": (
        "日付ごとのエントリーステータス件数の推移(直近30日)",
        """
        SELECT date, status, count(*) AS n
        FROM status_latest
        WHERE date >= (SELECT max(date) FROM status_latest) - INTERVAL 30 DAY
        GROUP BY 1, 2
        ORDER BY date DESC, n DESC
        """,
    ),
    "status-streak": (
        "同じステータスが続いている銘柄(直近日のステータスと連続日数)",
        """
        WITH latest AS (
            SELECT code, date, status,
                   row_number() OVER (PARTITION BY code ORDER BY date DESC) AS rn
            FROM status_latest
        )
        SELECT l.code, l.status AS current_status,
               count(*) FILTER (WHERE s.status = l.status) AS same_status_days
        FROM latest l
        JOIN status_latest s USING (code)
        WHERE l.rn = 1
        GROUP BY 1, 2
        ORDER BY same_status_days DESC
        LIMIT 30
        """,
    ),
    "stage-daily": (
        "日付ごとの監視バケット件数の推移(直近30日)",
        """
        SELECT date, bucket, count(*) AS n
        FROM stage_latest
        WHERE date >= (SELECT max(date) FROM stage_latest) - INTERVAL 30 DAY
        GROUP BY 1, 2
        ORDER BY date DESC, n DESC
        """,
    ),
    "stage-promotion": (
        "バケット別の10営業日以内の昇格率(→ order/watch/cooled)",
        # 「あと一歩(near)を既定で表示すべきか」を決めるための本命クエリ。
        # 日付は営業日でしか記録されないので、記録された日付そのものを
        # 営業日カレンダーとして使う (di = 通し番号)。
        # 観測日は「10営業日ぶんの追跡窓が取れる日」に限る。直近の日を混ぜると
        # まだ昇格する余地がある分だけ率が過小に出る。
        """
        WITH days AS (
            SELECT date, row_number() OVER (ORDER BY date) AS di
            FROM (SELECT DISTINCT date FROM stage_latest)
        ),
        s AS (
            SELECT st.code, st.date, st.bucket, d.di
            FROM stage_latest st JOIN days d USING (date)
        ),
        src AS (
            SELECT * FROM s
            WHERE bucket IN ('near', 'forming', 'fresh_high', 'rejected')
              AND di <= (SELECT max(di) FROM days) - 10
        ),
        agg AS (
            SELECT src.bucket,
                   count(*) AS observations,
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM s f
                       WHERE f.code = src.code
                         AND f.di > src.di AND f.di <= src.di + 10
                         AND f.bucket IN ('order', 'watch', 'cooled')
                   )) AS promoted
            FROM src GROUP BY 1
        )
        SELECT bucket, observations, promoted,
               round(promoted / nullif(observations, 0), 3) AS promotion_rate
        FROM agg ORDER BY promotion_rate DESC NULLS LAST
        """,
    ),
    "sector-strength": (
        "直近日のセクター相対強度ランキング",
        # sectors は {セクター名: {...}} だが、read_json_auto はキー数が
        # map_inference_threshold (既定200) 未満だと MAP ではなく STRUCT と推論する
        # (33業種なので必ずSTRUCT側になる)。STRUCT は列名が固定なので動的に舐められない。
        # そこで to_json() 経由で MAP へ入れ直してから unnest する。
        """
        SELECT e.key AS sector,
               (e.value->>'rel_strength_pct')::DOUBLE AS rel_strength_pct,
               (e.value->>'d1')::DOUBLE AS d1,
               (e.value->>'p1_count')::BIGINT AS p1_count
        FROM (
            SELECT unnest(map_entries(to_json(sectors)::MAP(VARCHAR, JSON))) AS e
            FROM sector_latest
            WHERE date = (SELECT max(date) FROM sector_latest)
        )
        ORDER BY rel_strength_pct DESC NULLS LAST
        LIMIT 40
        """,
    ),
}


def _connect():
    try:
        import duckdb
    except ImportError:
        print("duckdb が入っていません。次を実行してください:\n"
              "    pip install duckdb", file=sys.stderr)
        raise SystemExit(1)

    con = duckdb.connect()
    registered = []
    for view, (filename, key) in SOURCES.items():
        path = HISTORY_DIR / filename
        if not path.exists():
            continue
        con.execute(
            f"CREATE VIEW {view} AS SELECT * FROM read_json_auto('{path.as_posix()}')"
        )
        # 追記専用なので同じキーの行が複数ありうる。rowid の大きい方 = 後に
        # 書かれた行を採用する(load_deduped と同じ last-write-wins)。
        partition = ", ".join(key)
        con.execute(
            f"CREATE VIEW {view}_latest AS "
            f"SELECT * EXCLUDE (_rn) FROM ("
            f"  SELECT *, row_number() OVER ("
            f"    PARTITION BY {partition} ORDER BY rowid DESC) AS _rn"
            f"  FROM (SELECT *, row_number() OVER () AS rowid FROM {view})"
            f") WHERE _rn = 1"
        )
        registered.append(view)

    if not registered:
        print(f"{HISTORY_DIR} に JSONL がありません。先に次を実行してください:\n"
              "    python scripts/migrate_history_to_jsonl.py", file=sys.stderr)
        raise SystemExit(1)
    return con, registered


def _print_table(result) -> None:
    cols = [d[0] for d in result.description]
    rows = result.fetchall()
    widths = [len(c) for c in cols]
    text_rows = []
    for row in rows:
        cells = ["" if v is None else str(v) for v in row]
        widths = [max(w, len(c)) for w, c in zip(widths, cells)]
        text_rows.append(cells)
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("-+-".join("-" * w for w in widths))
    for cells in text_rows:
        print(" | ".join(c.ljust(w) for c, w in zip(cells, widths)))
    print(f"\n({len(rows)} rows)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="使えるビューとプリセットを表示")
    group.add_argument("--sql", help="任意のSQLを実行")
    group.add_argument("--preset", choices=sorted(PRESETS), help="定型クエリを実行")
    args = parser.parse_args(argv)

    con, registered = _connect()

    if args.list:
        print("ビュー:")
        for view in registered:
            n = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
            uniq = con.execute(f"SELECT count(*) FROM {view}_latest").fetchone()[0]
            cols = [r[0] for r in con.execute(f"DESCRIBE {view}").fetchall()]
            print(f"  {view:<8} {n:>7} 行 (dedup後 {uniq} 行 = {view}_latest)")
            print(f"           列: {', '.join(cols)}")
        print("\nプリセット:")
        for name, (desc, _) in sorted(PRESETS.items()):
            print(f"  {name:<16} {desc}")
        return 0

    sql = args.sql if args.sql else PRESETS[args.preset][1]
    if args.preset:
        print(f"-- {PRESETS[args.preset][0]}\n")
    _print_table(con.execute(sql))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
