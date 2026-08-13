"""P1銘柄の「今どの段階にいるか」を毎日1銘柄1行で記録する (2026-07-29追加)。

## なぜ要るか

フロントのフィルタは status と setup_stage を組み合わせて銘柄を出し分けているが、
その線引き(例: 「あと一歩」を既定で出すか隠すか)を決める根拠が無い。既存の
履歴では判断できない:

- data/history/status.jsonl は「エントリー評価が付いた銘柄」だけ(実測55行/12銘柄)。
  非アクショナブル側 = 監視タブの母集団がまるごと入っていない。
- breadth.json の vcp_funnel は日次の集計値しか持たない。集計だけでは
  「今日のあと一歩21銘柄のうち何銘柄が後日 待機A/B に上がったか」が出せない。
  昇格率は銘柄を跨いだ追跡なので、銘柄別スナップショットが要る。

そこで P1 (トレンドテンプレート通過) 全銘柄のバケットを毎日追記する。2〜3週
貯めれば `python -m src.analyze --preset stage-promotion` で昇格率が出るので、
「熟成待ちをどこで切るか」を感覚ではなく数字で決められる。UIはまだ変えない。

## バケット定義

docs/assets/app.js の statusVisible / cardBadgeKey / setupStageGroupKey と
**同じ分岐をサーバ側で再現している**。フロントと定義がずれたら計測の意味が無い
ので、app.js 側を変えたらここも変えること。

    order    ピボットのある BREAKOUT/BREAKOUT_WEAK/WATCH_A (=発注可能)
    watch    上記だがピボット未確定
    cooled   EXTENDED / STALE (追撃禁止)
    near     setup_stage.near = true (あと一歩)
    forming / fresh_high / rejected   setup_stage.stage そのまま
    inactive volatile / no_base
    unknown  status も setup_stage も無い(異常。0でない日は要調査)

分類の入力は vcp_result ではなく **stock_records** (フロントが実際に食う確定
レコード) にする。EXTENDED/STALE の上書きは entry 評価の後に載るので、
vcp_result から数えると breadth.vcp_funnel のように report.json と件数がずれる。
"""
from __future__ import annotations

from src.config import REPO_ROOT

STAGE_HISTORY_JSONL = REPO_ROOT / "data" / "history" / "stage.jsonl"
STAGE_HISTORY_KEY = ("code", "date")

# app.js の cardBadgeKey が status だけで決める集合。ここに入る status は
# setup_stage を見ない。
ENTRY_STATUSES = ("BREAKOUT", "BREAKOUT_WEAK", "WATCH_A")
COOLED_STATUSES = ("EXTENDED", "STALE")

BUCKETS = (
    "order", "watch", "cooled",
    "near", "forming", "fresh_high", "rejected", "inactive",
    "unknown",
)


def classify_bucket(record: dict) -> str:
    """stock_record 1件をバケット名へ分類する (app.js のミラー)。"""
    status = record.get("status")
    if status in COOLED_STATUSES:
        return "cooled"
    if status in ENTRY_STATUSES:
        return "order" if record.get("pivot") is not None else "watch"

    stage_info = record.get("setup_stage")
    if not stage_info:
        return "unknown"
    if stage_info.get("near"):
        return "near"
    stage = stage_info.get("stage")
    if stage in ("forming", "fresh_high", "rejected"):
        return stage
    return "inactive"  # volatile / no_base / 未知のstage


def build_stage_records(date_str: str, stock_records: list[dict]) -> list[dict]:
    """1日分の銘柄別スナップショットを組み立てる。

    列は昇格率の追跡に要る最小限に絞る。価格や指標は report.json / charts 側に
    あるので重複させない(1日200行×数百日が git 差分として積まれるため)。
    """
    rows = []
    for rec in stock_records:
        stage_info = rec.get("setup_stage") or {}
        rows.append({
            "date": date_str,
            "code": rec.get("code"),
            "bucket": classify_bucket(rec),
            "status": rec.get("status"),
            "stage": stage_info.get("stage"),
            "near": bool(stage_info.get("near")),
            "total_score": rec.get("total_score"),
            "has_pivot": rec.get("pivot") is not None,
        })
    return rows


def build_stage_funnel(stock_records: list[dict]) -> dict:
    """バケット別件数。breadth.json に載せて日次推移を軽く見るため。

    0件のバケットもキーを立てる。欠けていると「その日は集計されなかった」のか
    「本当に0だったのか」が後から区別できない。
    """
    counts = {b: 0 for b in BUCKETS}
    for rec in stock_records:
        counts[classify_bucket(rec)] += 1
    return counts


def update_stage_history(date_str: str, stock_records: list[dict], cfg: dict) -> int:
    """stage.jsonl へ当日分を追記する。同日再実行は後勝ちで上書き相当になる。

    戻り値は追記した行数。compaction の方針は update_sector_history と同じで、
    重複が溜まってきたときだけ走らせる(毎回やると追記専用の利点が消える)。
    """
    from src.history_store import (
        append_records, calendar_keep_days, compact, count_lines,
    )

    rows = build_stage_records(date_str, stock_records)
    append_records(STAGE_HISTORY_JSONL, rows)

    keep_days = cfg.get("history_keep_days", 90)
    # 1日あたり P1 の銘柄数(実測200前後)ぶん行が増えるので、閾値も銘柄数を掛ける。
    per_day = max(len(rows), 1)
    if count_lines(STAGE_HISTORY_JSONL) > max(keep_days * per_day * 2, 1000):
        removed = compact(
            STAGE_HISTORY_JSONL,
            STAGE_HISTORY_KEY,
            keep_days=calendar_keep_days(keep_days),
            today=date_str,
        )
        print(f"stage_history: compaction で {removed} 行を削減")
    return len(rows)
