"""追記専用(append-only)JSONL 履歴ストア。

なぜこの形にしたか (2026-07-27):

1. **git 差分を「増えた行だけ」に保つため。**
   旧方式は `data/status_history.json` / `data/sector_history.json` を毎回まるごと
   ロードして丸ごと書き戻していた。JSON の indent 付き全量書き戻しは、実質1日ぶんの
   追加でもファイル全行が書き換わったことになり、日次コミットの差分が毎回数千行に
   膨れていた。1レコード1行の JSONL に**追記だけ**すれば、差分は追記行のみになる。

2. **「同日再実行は既存エントリを置換」という現行セマンティクスを壊さないため。**
   追記専用だと同じキーの行が複数できてしまうが、**読み出し時に後勝ち(last-write-wins)
   で dedup** すれば意味論は「置換」と完全に同じになる。書き込み側は何も考えずに
   append するだけでよく、書き込み途中クラッシュで既存行が壊れる余地も無くなる
   (旧方式は全量書き戻しなので、tmp+os.replace が無ければ全履歴消失のリスクがあった)。

3. **分析クエリを打てるようにするため。**
   JSONL は DuckDB の `read_json_auto()` がそのまま読める。`python -m src.analyze`
   から SQL で履歴を集計できる(src/analyze.py)。

追記だけだと当然ファイルは単調増加するので、行数が閾値を超えたときにだけ
`compact()` で dedup + 古いレコードの間引きを行う。毎回 compact すると
append-only の利点(小さい git 差分)が消えるので、`needs_compaction()` で
閾値を超えたときだけ呼ぶ想定。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from src.utils_io import atomic_write_text

# 「営業日ぶん」で数えている保持件数を、暦日ベースの compaction で削らないための
# 余裕係数。90営業日 ≒ 126暦日、400営業日 ≒ 580暦日なので 1.5 倍あれば足りる。
# (呼び出し側が keep_days を「営業日ぶんの件数」として扱っている場合に使う)
CALENDAR_SLACK = 1.5


def calendar_keep_days(trading_days: int) -> int:
    """営業日ベースの保持件数を、暦日ベースの保持日数へ安全側に換算する。

    compact() の keep_days は暦日で効くため、そのまま営業日の数値を渡すと
    「末尾N件」を期待している読み出し側より先にデータが消える。CALENDAR_SLACK 倍して
    必ず N 営業日ぶんが残るようにする。
    """
    return int(trading_days * CALENDAR_SLACK) + 1


# ---------------------------------------------------------------------------
# 書き込み
# ---------------------------------------------------------------------------

def append_records(path, records: list[dict]) -> None:
    """JSONL へ追記する。ファイルが無ければ親ディレクトリごと作成する。

    - 1レコード1行、`ensure_ascii=False`(日本語セクター名をそのまま読めるように)。
    - `sort_keys=True` でキー順を固定する。dict の挿入順に任せると呼び出し箇所ごとに
      キー順がぶれ、内容が同じでも git 差分が出てしまうため。
    """
    if not records:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# 読み出し
# ---------------------------------------------------------------------------

def iter_records(path) -> Iterator[dict]:
    """1行ずつ読む。壊れた行は warning を print してスキップし、例外にしない。

    safe_load_json の「1バイト壊れたら全履歴が消える」問題を JSONL では避けられる
    (壊れるのは書きかけの最終行だけで、それ以前の行は無傷)ので、その利点を潰さない
    ためにパースエラーは握り潰す。dict 以外(配列や数値だけの行)も不正としてスキップ。
    """
    path = Path(path)
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARNING: {path}:{line_no} corrupted JSONL line skipped ({e})")
                continue
            if not isinstance(rec, dict):
                print(f"WARNING: {path}:{line_no} non-object JSONL line skipped")
                continue
            yield rec


def _key_of(rec: dict, key_fields: tuple[str, ...]) -> tuple:
    return tuple(rec.get(f) for f in key_fields)


def load_deduped(path, key_fields: tuple[str, ...]) -> list[dict]:
    """全行読んで `key_fields` のタプルで dedup する。**後勝ち**(last-write-wins)。

    これが「同日再実行は既存エントリを置換」を追記専用で実現している中核。

    並び順はキーのソート順ではなく、**そのキーが最後に出現した位置の順**を保つ。
    append-only のファイルでは「後から書き直された行」= 最新の再実行ぶんが末尾に
    来るので、この順序はそのまま時系列順になる。
    """
    out: dict[tuple, dict] = {}
    for rec in iter_records(path):
        key = _key_of(rec, key_fields)
        # 既存キーを一度消してから入れ直すことで「最終出現位置」の順になる
        # (dict の再代入は挿入位置を保つため、消さないと初出位置のままになる)。
        if key in out:
            del out[key]
        out[key] = rec
    return list(out.values())


# ---------------------------------------------------------------------------
# 間引き (compaction)
# ---------------------------------------------------------------------------

def count_lines(path) -> int:
    """空行を除いた行数。needs_compaction / 移行スクリプトの件数表示に使う。"""
    path = Path(path)
    if not path.exists():
        return 0
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def needs_compaction(path, *, max_lines: int) -> bool:
    """行数が `max_lines` を超えたら True。

    毎回 compact すると全行書き戻しになって append-only の利点(小さい git 差分)が
    消えるので、閾値を超えたときだけ呼ぶ想定。
    """
    return count_lines(path) > max_lines


def _parse_iso(value) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def compact(path, key_fields: tuple[str, ...], *, keep_days: int | None = None,
            date_field: str = "date", today: str | None = None) -> int:
    """dedup + `keep_days` より古いレコードを落として全書き直しする。削除行数を返す。

    - `keep_days` が None なら dedup のみ(日付での間引きはしない)。
    - `today` 未指定なら実行日。カットオフは `today - keep_days` 日で、
      これ**より古い**(<)レコードを落とす。
    - `date_field` がパース不能・欠損のレコードは**残す**。日付が読めないという理由で
      履歴を静かに消すのは危険なため(壊れた行は iter_records 側で既に落ちている)。
    - 書き込みは atomic_write_text(tmp + os.replace)。全書き直しの途中でクラッシュ
      しても既存ファイルは無傷。
    """
    path = Path(path)
    before = count_lines(path)
    if before == 0:
        return 0

    records = load_deduped(path, key_fields)

    if keep_days is not None:
        base = _parse_iso(today) or date.today()
        cutoff = base - timedelta(days=keep_days)
        kept = []
        for rec in records:
            d = _parse_iso(rec.get(date_field))
            if d is None or d >= cutoff:
                kept.append(rec)
        records = kept

    text = "".join(
        json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n" for rec in records
    )
    atomic_write_text(path, text)
    return before - len(records)
