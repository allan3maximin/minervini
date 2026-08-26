"""深掘りツールのレコード読み書きと規律ルール(R1〜R4・UC-1)の実装。

DESIGN_DEEPDIVE.md §3.3, §5 を参照。

- `load_first_wins` は `src.history_store.load_deduped`(後勝ち)の**真逆**で、
  初出のレコードを採用する。予想(predictions.jsonl)は「発表後に書き換えない」
  という規律を守るためにこちらを使う。取り違えると規律ルールが丸ごと無効になる。
- 書き込み系の関数(add_watch 等)はすべて `written_at` を `now_iso()` で
  実行時刻に**必ず上書き**する。呼び出し側が偽装できる余地を残さないため、
  レコードに `written_at` が入っていても無視する。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from src import history_store
from src.config import REPO_ROOT

DEEPDIVE_DIR = REPO_ROOT / "data" / "deepdive"
WATCHLIST_PATH = DEEPDIVE_DIR / "watchlist.jsonl"
PREDICTIONS_PATH = DEEPDIVE_DIR / "predictions.jsonl"
ACTUALS_PATH = DEEPDIVE_DIR / "actuals.jsonl"
OUTCOMES_PATH = DEEPDIVE_DIR / "outcomes.jsonl"
NOTES_PATH = DEEPDIVE_DIR / "notes.jsonl"
VERSIONS_PATH = DEEPDIVE_DIR / "model_versions.jsonl"

JST = timezone(timedelta(hours=9))

VALID_CONFIDENCE = {"高", "中", "低"}
VALID_ACTION = {"買う", "買わん", "保有継続"}
VALID_TIMING = {"寄り前", "場中", "引け後"}

_PREDICTION_KEY = ("ticker", "quarter", "model_ver")
_REQUIRED_PREDICTION_FIELDS = (
    "ticker", "quarter", "earnings_date", "company_op", "my_op",
    "confidence", "action", "model_ver", "rationale",
)


def now_iso() -> str:
    """JST の現在時刻を ISO8601 で返す。

    テストからは `monkeypatch.setattr(store, "now_iso", lambda: "...")` で
    差し替える。引数を取らないのは、呼び出し側から偽の時刻を渡せる余地を
    作らないため(R1 の実効的な防御)。
    """
    return datetime.now(JST).isoformat()


# ---------------------------------------------------------------------------
# 読み出し
# ---------------------------------------------------------------------------

def load_first_wins(path, key_fields: tuple[str, ...]) -> list[dict]:
    """`key_fields` で dedup するが、**初出勝ち**(first-write-wins)。

    `history_store.load_deduped`(後勝ち)の逆。同一キーの2行目以降は
    読み捨てる。ファイルを直接エディタで編集されて同じキーが複数行あっても、
    最初の行だけが「記録された予想」として扱われる(R2 の最後の砦)。
    """
    out: dict[tuple, dict] = {}
    for rec in history_store.iter_records(path):
        key = tuple(rec.get(f) for f in key_fields)
        if key not in out:
            out[key] = rec
    return list(out.values())


def load_last_wins(path, key_fields: tuple[str, ...]) -> list[dict]:
    """`history_store.load_deduped` の薄いラッパ。名前で意図(後勝ち)を明示する。"""
    return history_store.load_deduped(path, key_fields)


# ---------------------------------------------------------------------------
# 書き込み
# ---------------------------------------------------------------------------

def add_watch(rec: dict) -> None:
    """ウォッチ銘柄を登録する(後勝ち。マスタは書き換わる前提)。

    `drivers` / `break_conditions` が空なら ValueError(UC-1: ここが書けない
    銘柄は登録しない = 適性判定を兼ねる。バリデーションであってオプションではない)。
    """
    rec = dict(rec)
    if not str(rec.get("ticker") or "").strip():
        raise ValueError("watch add: ticker は必須です")
    if not str(rec.get("drivers") or "").strip():
        raise ValueError(
            "watch add: drivers が空です"
            "(UC-1: 何がこの株を動かすか書けない銘柄は登録しない)"
        )
    if not str(rec.get("break_conditions") or "").strip():
        raise ValueError(
            "watch add: break_conditions が空です"
            "(UC-1: どうなったら前提が崩れたと判断するか書けない銘柄は登録しない)"
        )
    rec.setdefault("status", "active")
    rec.setdefault("next_earnings_date_manual", None)
    rec["written_at"] = now_iso()
    history_store.append_records(WATCHLIST_PATH, [rec])


def _is_valid_prediction(written_at: str, earnings_date: str) -> bool:
    """R1: 発表当日中でも記入日が発表日以降なら無効。

    決算は寄り前/場中/引け後があるため「記入日 > 発表予定日」では粗すぎる。
    `earnings_date` 当日に書けた時点で発表を見た可能性を排除できないので、
    当日ぶんも無効にする(`<` であって `<=` ではないことに注意)。
    """
    w = datetime.fromisoformat(written_at)
    e = date.fromisoformat(earnings_date)
    return w.date() < e


def add_prediction(rec: dict) -> None:
    """予想を追記する(初出勝ち・固定フィールド)。

    - 必須項目の欠落、`confidence` / `action` の値域違反は ValueError。
    - `(ticker, quarter, model_ver)` が既存なら ValueError(R2: 追記そのものを拒否)。
    - `written_at` は必ず実行時刻で上書きし、`earnings_date` と比較して
      `valid` / `invalid_reason` を自動判定する。**無効でも例外にせず保存する**
      (R4: 消したら規律ルールの意味が無くなる。集計側で除外する)。
    """
    rec = dict(rec)
    missing = [f for f in _REQUIRED_PREDICTION_FIELDS if rec.get(f) in (None, "")]
    if missing:
        raise ValueError(f"predict: 必須項目が未入力です: {', '.join(missing)}")
    if rec["confidence"] not in VALID_CONFIDENCE:
        raise ValueError(
            f"predict: confidence は 高|中|低 のいずれかにしてください: {rec['confidence']!r}"
        )
    if rec["action"] not in VALID_ACTION:
        raise ValueError(
            f"predict: action は 買う|買わん|保有継続 のいずれかにしてください: {rec['action']!r}"
        )

    key = tuple(rec.get(f) for f in _PREDICTION_KEY)
    existing = load_first_wins(PREDICTIONS_PATH, _PREDICTION_KEY)
    if any(tuple(e.get(f) for f in _PREDICTION_KEY) == key for e in existing):
        raise ValueError(
            f"predict: {key} は既に記録済みです"
            "(R2: 固定フィールドは書き換え不可。訂正は新しい model_ver で書き直すこと)"
        )

    rec["written_at"] = now_iso()
    if _is_valid_prediction(rec["written_at"], rec["earnings_date"]):
        rec["valid"] = True
        rec["invalid_reason"] = None
    else:
        rec["valid"] = False
        rec["invalid_reason"] = "記入日が発表日以降"

    history_store.append_records(PREDICTIONS_PATH, [rec])


def add_actual(rec: dict) -> None:
    """実績を追記する(後勝ち。訂正短信・入力ミス修正があるため書き換えを禁じない)。

    `timing`(寄り前|場中|引け後)は必須。翌日騰落率の起点(§3.5)が変わるため。
    """
    rec = dict(rec)
    if not str(rec.get("ticker") or "").strip():
        raise ValueError("actual: ticker は必須です")
    if not str(rec.get("quarter") or "").strip():
        raise ValueError("actual: quarter は必須です")
    if rec.get("timing") not in VALID_TIMING:
        raise ValueError(
            f"actual: timing は 寄り前|場中|引け後 のいずれかにしてください: {rec.get('timing')!r}"
        )
    rec["written_at"] = now_iso()
    history_store.append_records(ACTUALS_PATH, [rec])


def add_note(rec: dict) -> None:
    """C層(定性メモ)を追記する(dedupキー無し・追記専用)。

    `ticker` / `quarter` / `text` が必須。訂正は「書き直す」のではなく
    新しい行を積む前提(いつ何を思ったかの記録そのものが価値なので、
    後勝ちで上書きしない)。
    """
    rec = dict(rec)
    if not str(rec.get("ticker") or "").strip():
        raise ValueError("note: ticker は必須です")
    if not str(rec.get("quarter") or "").strip():
        raise ValueError("note: quarter は必須です")
    if not str(rec.get("text") or "").strip():
        raise ValueError("note: text は必須です")
    rec["written_at"] = now_iso()
    history_store.append_records(NOTES_PATH, [rec])


def add_version(rec: dict) -> None:
    """判断ロジックの変更ログを追記する(初出勝ち)。

    既存の `ver` への再追加は ValueError(R3: 遡及禁止。新しい ver でしか作れない)。
    """
    rec = dict(rec)
    ver = rec.get("ver")
    if not str(ver or "").strip():
        raise ValueError("ver add: ver は必須です")
    existing = load_first_wins(VERSIONS_PATH, ("ver",))
    if any(e.get("ver") == ver for e in existing):
        raise ValueError(
            f"ver add: ver={ver!r} は既に存在します"
            "(R3: ロジック変更の遡及は禁止。新しい ver 名で追加すること)"
        )
    rec["written_at"] = now_iso()
    history_store.append_records(VERSIONS_PATH, [rec])
