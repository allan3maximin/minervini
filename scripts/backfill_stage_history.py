"""data/history/stage.jsonl を過去日ぶん一気に埋める (2026-07-30追加)。

## なぜ要るか

src/report/stage_log.py は 2026-07-29 から動き始めたので、
`python -m src.analyze --preset stage-promotion` は追跡窓(10営業日)が埋まるまで
0行を返す。「あと一歩(near)をどこで切るか」の線引きは待たずに決めたい。

過去の価格は data/prices_long/*.parquet (2001年〜、3716銘柄) に全部あるので、
当時のスクリーナーを日付順に再実行して同じ形のレコードを作れる。根拠は
scripts/backfill_breadth_history.py / src/backtest.py と同じ:

    ma / atr20 / 52週高安 / rs_raw はすべて後ろ向きの rolling 計算なので、
    フル履歴に compute_all() を一度かけてから `df[df["date"] <= D]` で
    スライスした値は、D までのデータだけで計算した値と一致する(look-aheadなし)。

## 逐次リプレイが必須な理由

stage の order/watch/cooled は entry ステータス由来で、これは**経路依存**:

- ピボットは WATCH_A の日に確定して locked_pivot として繰り越される
- EXTENDED/STALE が extended_cooldown_days 連続するとロックが破棄される
- breakout_age_days は「現在のピボットストリークで最初に抜けた日」から数える

つまり D 日の status は D-1 日までの status_history に依存する。単発で D だけ
計算しても order/watch は再現できない。よって古い日から順に合成 history を
育てながら回す(実運用の status_history には一切触らない)。

その帰結として、リプレイ開始直後は history が空でピボットロックが無いため
order/watch が過少になる。そこで報告範囲より `--warmup` 日ぶん手前から回し、
warmup 期間の出力は捨てる。

## 既知の制約(必ず読むこと)

1. **total_score は None**。ファンダ(EDINET/J-Quants)は「現在のスナップショット」
   しか無く、過去日に当時のEPS/売上を当てると look-ahead になる。スコアを
   backfill 行で使ってはいけない。
2. **ユニバースは今日のもの**。data/universe.json は直近の売買代金で作られるので、
   当時は条件を満たさなかった銘柄が入り、上場廃止銘柄は入らない
   (src/backtest.py と同じ survivorship bias)。
3. **各行に `backfilled: true` が付く**。実運用行にはこのキーが無い(=NULL)。
   混ぜて集計すると 1〜2 の歪みが見えなくなるので、analyze 側で必ず区別する。
4. ベンチマーク(1306)は data/prices/ 側にしか無く 2024-07 以降。それより前の
   日付では rs_raw が計算できず銘柄が落ちるので backfill 範囲に注意。

## 使い方 (bash 45秒制限があるので3フェーズに分割・再実行可能)

    python scripts/backfill_stage_history.py prepare      # 指標フレーム構築 (繰り返し呼ぶ)
    python scripts/backfill_stage_history.py replay       # 日付を古い順にリプレイ (繰り返し呼ぶ)
    python scripts/backfill_stage_history.py commit       # stage.jsonl へ追記
    python scripts/backfill_stage_history.py status       # 進捗確認

prepare / replay は途中で終了しても data/backfill_cache/ の状態から再開する。
全部やり直したいときは `--reset` を付けるか data/backfill_cache/ を消す。
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.data.prices import drop_benchmark_outliers
from src.history_store import append_records, load_deduped
from src.indicators import compute_all, rs_percentile_rank
from src.pipeline import ACTIONABLE_ENTRY_STATUSES, COOLED_ENTRY_STATUSES
from src.report import build_site, stage_log
from src.screener import entry as entry_mod
from src.screener import priority as priority_mod
from src.screener import trend_template
from src.screener import vcp as vcp_mod
from src.universe import load_universe

LONG_DIR = REPO_ROOT / "data" / "prices_long"
SHORT_DIR = REPO_ROOT / "data" / "prices"
CACHE_DIR = REPO_ROOT / "data" / "backfill_cache"
ROWS_PATH = CACHE_DIR / "stage_rows.jsonl"
STATE_PATH = CACHE_DIR / "state.pkl"

# compute_all に食わせる本数。ma200 / 52週高安 / rs_raw(約252本) が安定する分 +
# VCP の scan_days_extended(200) + backfill 期間ぶんの余裕。
LOAD_BARS = 900
# 1銘柄あたりメモリに残す指標行数。VCP は最大 scan_days_extended=200 本しか
# 遡らないので、リプレイ期間 + 250 本あれば足りる。
KEEP_BARS = 460

ENTRY_TIER_STATUSES = ACTIONABLE_ENTRY_STATUSES | COOLED_ENTRY_STATUSES


# ---------------------------------------------------------------------------
# 状態管理
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, "rb") as f:
            return pickle.load(f)
    return {}


def _save_state(state: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(STATE_PATH)


def _frames_path(i: int) -> Path:
    return CACHE_DIR / f"frames_{i:03d}.pkl"


# ---------------------------------------------------------------------------
# phase 1: prepare -- 指標付きフレームを作って分割保存する
# ---------------------------------------------------------------------------

def load_benchmark() -> pd.Series:
    """TOPIX プロキシ(1306)の終値。ネットワークには行かない。

    src.data.prices.get_benchmark_close は update_prices 経由で取りに行くので
    backfill では使えない。キャッシュを直読みして同じ外れ値除去だけ通す。
    """
    for d in (LONG_DIR, SHORT_DIR):
        p = d / "1306.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date")
            return drop_benchmark_outliers(df.set_index("date")["close"])
    raise RuntimeError("ベンチマーク 1306.parquet が data/prices_long/ にも data/prices/ にも無い")


def price_path(code: str) -> Path | None:
    """長期履歴を優先し、無ければ通常キャッシュを使う。"""
    for d in (LONG_DIR, SHORT_DIR):
        p = d / f"{code}.parquet"
        if p.exists():
            return p
    return None


def prepare(chunk_codes: int = 400, max_chunks: int = 1) -> bool:
    """指標付きフレームを chunk_codes 銘柄ずつ作って pickle へ落とす。

    戻り値は「全チャンク完了したか」。1回の呼び出しで max_chunks 個だけ進めて
    抜けるので、bash の実行時間制限に当たっても再実行すれば続きから進む。
    """
    config = load_config()
    state = _load_state()
    codes = state.get("codes")
    if codes is None:
        codes = [s["code"] for s in load_universe()["stocks"]]
        state["codes"] = codes
        state["prepare_done"] = 0
        state["n_chunks"] = (len(codes) + chunk_codes - 1) // chunk_codes
        state["chunk_codes"] = chunk_codes
        _save_state(state)
        print(f"universe: {len(codes)} codes -> {state['n_chunks']} chunks")

    chunk_codes = state["chunk_codes"]
    benchmark = load_benchmark()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    for _ in range(max_chunks):
        i = state["prepare_done"]
        if i >= state["n_chunks"]:
            print("prepare: 完了済み")
            return True
        part = codes[i * chunk_codes : (i + 1) * chunk_codes]
        t0 = time.time()
        frames: dict[str, pd.DataFrame] = {}
        skipped = 0
        for code in part:
            p = price_path(code)
            if p is None:
                skipped += 1
                continue
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").tail(LOAD_BARS).reset_index(drop=True)
            # backfill_breadth_history と同じ基準: 260本未満は指標が安定しない。
            if len(df) < 260:
                skipped += 1
                continue
            ind = compute_all(df, benchmark)
            frames[code] = ind.tail(KEEP_BARS).reset_index(drop=True)
        with open(_frames_path(i), "wb") as f:
            pickle.dump(frames, f, protocol=pickle.HIGHEST_PROTOCOL)
        state["prepare_done"] = i + 1
        _save_state(state)
        print(
            f"prepare chunk {i + 1}/{state['n_chunks']}: {len(frames)} frames "
            f"({skipped} skipped) {round(time.time() - t0, 1)}s"
        )
        _ = config  # config は現状 prepare では未使用(将来の指標切替用に受けておく)
    return state["prepare_done"] >= state["n_chunks"]


def load_frames() -> dict[str, pd.DataFrame]:
    state = _load_state()
    frames: dict[str, pd.DataFrame] = {}
    for i in range(state.get("n_chunks", 0)):
        p = _frames_path(i)
        if not p.exists():
            raise RuntimeError(f"{p} が無い。先に prepare を完走させること")
        with open(p, "rb") as f:
            frames.update(pickle.load(f))
    return frames


# ---------------------------------------------------------------------------
# phase 2: replay -- 古い日から順に1日ぶんのスクリーナーを再実行する
# ---------------------------------------------------------------------------

def slice_day(frames: dict[str, pd.DataFrame], target: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """target 日で終わるスライスを銘柄ごとに返す。

    「target 日に値が付いている銘柄」だけを残す。最終行が target より古い銘柄
    (未上場・売買停止・データ欠損) を残すと、実運用の df.iloc[-1] が当日行である
    という前提が崩れ、古い行を当日として扱ってしまう。
    """
    out: dict[str, pd.DataFrame] = {}
    for code, df in frames.items():
        sub = df[df["date"] <= target]
        if sub.empty or sub.iloc[-1]["date"] != target:
            continue
        out[code] = sub
    return out


def replay_one_day(
    date_str: str,
    sliced: dict[str, pd.DataFrame],
    history: dict,
    config: dict,
) -> tuple[list[dict], dict]:
    """1営業日ぶんの stock_record 相当(stage 分類に必要な最小フィールド)を返す。

    pipeline.run_daily の P1 ループを、stage_log.classify_bucket が見る
    status / pivot / setup_stage だけに絞って再現する。history は in-place で
    更新されるので呼び出し側は古い日から順に渡すこと。
    """
    latest_by_code: dict[str, dict] = {}
    for code, sub in sliced.items():
        latest_by_code[code] = sub.iloc[-1].to_dict()

    rs_by_code = rs_percentile_rank({c: r["rs_raw"] for c, r in latest_by_code.items()})
    usable: dict[str, dict] = {}
    for code, latest in latest_by_code.items():
        rs = rs_by_code.get(code)
        if rs is None:
            continue  # RS が出ない = 履歴不足。実運用も同じ理由で除外している。
        latest["rs"] = rs
        usable[code] = latest

    # attach_score_percentiles(断面ランク)がここで latest へ書き込まれる。
    # evaluate_priority より先に呼ぶ順序は pipeline.run_daily と同じ。
    trend_template.screen_universe(usable, config)

    records: list[dict] = []
    for code, latest in usable.items():
        pr_eval = priority_mod.evaluate_priority(latest, config)
        if pr_eval is None or pr_eval["priority"] != 1:
            continue  # P1 以外は report.json に載らない = stage の母集団外。

        df_ind = sliced[code].reset_index(drop=True)
        vcp_result = vcp_mod.evaluate_vcp(df_ind, config)
        entry_result = entry_mod.evaluate_entry(code, latest, vcp_result, history, config)

        status = entry_result.get("status")
        pivot = entry_result.get("pivot")
        if pivot is not None and status in ENTRY_TIER_STATUSES:
            # pipeline の _resolve_stop_ref_low と同じ解決順。
            if vcp_result.get("status") == "WATCH_A" and vcp_result.get("contractions"):
                stop_ref_low = vcp_result["contractions"][-1]["low_price"]
            else:
                locked = entry_mod.locked_pivot(history, code)
                stop_ref_low = locked.get("stop_ref_low") if locked else None
            history = entry_mod.record_status(
                history, code, date_str, status, pivot, stop_ref_low, config
            )

        records.append({
            "code": code,
            "status": status,
            "pivot": pivot,
            "setup_stage": build_site.build_setup_stage(vcp_result, config),
            # ファンダは現在スナップショットしか無いので過去日には当てない(冒頭の制約1)。
            "total_score": None,
        })
    return records, history


def replay(days: int = 60, warmup: int = 30, max_dates: int = 5) -> bool:
    """報告対象 days 日 + 手前 warmup 日を古い順にリプレイする。

    1回の呼び出しで max_dates 日だけ進めて抜ける(bash の時間制限対策)。
    戻り値は「全日程を消化したか」。
    """
    config = load_config()
    state = _load_state()
    frames = load_frames()

    if "replay_dates" not in state:
        all_dates = sorted({d for df in frames.values() for d in df["date"].unique()})
        want = days + warmup
        if len(all_dates) < want:
            print(f"WARN: 利用可能な営業日が {len(all_dates)} 日しか無い (要求 {want} 日)")
        picked = [pd.Timestamp(d) for d in all_dates[-want:]]
        state["replay_dates"] = [d.isoformat()[:10] for d in picked]
        # warmup ぶんの先頭は commit で捨てる(ピボットロックが育っていない)。
        state["warmup_dates"] = state["replay_dates"][:warmup]
        state["replay_done"] = 0
        state["history"] = {}
        ROWS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ROWS_PATH.write_text("", encoding="utf-8")
        _save_state(state)
        print(
            f"replay: {len(state['replay_dates'])} 営業日 "
            f"({state['replay_dates'][0]} 〜 {state['replay_dates'][-1]}), "
            f"うち先頭 {warmup} 日は warmup"
        )

    dates = state["replay_dates"]
    history = state["history"]
    for _ in range(max_dates):
        i = state["replay_done"]
        if i >= len(dates):
            print("replay: 完了済み")
            return True
        date_str = dates[i]
        t0 = time.time()
        sliced = slice_day(frames, pd.Timestamp(date_str))
        records, history = replay_one_day(date_str, sliced, history, config)
        rows = stage_log.build_stage_records(date_str, records)
        for r in rows:
            r["backfilled"] = True  # 実運用行と混ざらないための必須マーカー(制約3)。
        with open(ROWS_PATH, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        state["replay_done"] = i + 1
        state["history"] = history
        _save_state(state)
        funnel = stage_log.build_stage_funnel(records)
        tag = " (warmup)" if date_str in state["warmup_dates"] else ""
        print(
            f"replay {i + 1}/{len(dates)} {date_str}{tag}: P1={len(records)} "
            f"order={funnel['order']} watch={funnel['watch']} near={funnel['near']} "
            f"{round(time.time() - t0, 1)}s"
        )
    return state["replay_done"] >= len(dates)


# ---------------------------------------------------------------------------
# phase 3: commit -- stage.jsonl へ追記
# ---------------------------------------------------------------------------

def commit(dry_run: bool = False) -> int:
    """warmup と既存日を除いて stage.jsonl へ追記する。追記行数を返す。

    既に stage.jsonl にある日付はスキップする(実運用行を backfill 行で
    上書きしない。history_store は後勝ちなので順序に頼らず日付で弾く)。
    """
    state = _load_state()
    warmup = set(state.get("warmup_dates") or [])
    if not ROWS_PATH.exists():
        print("replay 出力が無い。先に prepare/replay を回すこと")
        return 0

    existing_dates = {
        r.get("date")
        for r in load_deduped(stage_log.STAGE_HISTORY_JSONL, stage_log.STAGE_HISTORY_KEY)
    }

    rows, skipped_warmup, skipped_existing = [], 0, 0
    with open(ROWS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["date"] in warmup:
                skipped_warmup += 1
                continue
            if r["date"] in existing_dates:
                skipped_existing += 1
                continue
            rows.append(r)

    dates = sorted({r["date"] for r in rows})
    print(
        f"追記対象: {len(rows)} 行 / {len(dates)} 営業日 "
        f"({dates[0]} 〜 {dates[-1]})" if dates else "追記対象: 0 行"
    )
    print(f"スキップ: warmup {skipped_warmup} 行, 既存日 {skipped_existing} 行")
    if dry_run:
        print("--dry-run: stage.jsonl は書き換えない")
        return 0
    if not rows:
        return 0
    append_records(stage_log.STAGE_HISTORY_JSONL, rows)
    print(f"wrote: {stage_log.STAGE_HISTORY_JSONL}")
    return len(rows)


def show_status() -> None:
    state = _load_state()
    if not state:
        print("状態なし (未実行)")
        return
    print(f"prepare: {state.get('prepare_done', 0)}/{state.get('n_chunks', '?')} chunks")
    dates = state.get("replay_dates") or []
    print(f"replay:  {state.get('replay_done', 0)}/{len(dates)} dates")
    if dates:
        print(f"  範囲: {dates[0]} 〜 {dates[-1]} (warmup {len(state.get('warmup_dates') or [])} 日)")
    if ROWS_PATH.exists():
        n = sum(1 for _ in open(ROWS_PATH, encoding="utf-8"))
        print(f"  出力行: {n}")


def main() -> None:
    p = argparse.ArgumentParser(description="stage.jsonl の過去日を一括backfillする")
    p.add_argument("phase", choices=["prepare", "replay", "commit", "status"])
    p.add_argument("--days", type=int, default=60, help="報告対象の営業日数")
    p.add_argument("--warmup", type=int, default=30, help="捨てる助走営業日数")
    p.add_argument("--chunk-codes", type=int, default=400, help="prepare の1チャンク銘柄数")
    p.add_argument("--max-chunks", type=int, default=1, help="prepare で1回に進めるチャンク数")
    p.add_argument("--max-dates", type=int, default=5, help="replay で1回に進める営業日数")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reset", action="store_true", help="キャッシュを破棄して最初から")
    args = p.parse_args()

    if args.reset:
        for f in CACHE_DIR.glob("*"):
            f.unlink()
        print("キャッシュを破棄した")

    if args.phase == "prepare":
        done = prepare(chunk_codes=args.chunk_codes, max_chunks=args.max_chunks)
        print("prepare 完了" if done else "prepare 未完 -- もう一度実行すること")
    elif args.phase == "replay":
        done = replay(days=args.days, warmup=args.warmup, max_dates=args.max_dates)
        print("replay 完了" if done else "replay 未完 -- もう一度実行すること")
    elif args.phase == "commit":
        commit(dry_run=args.dry_run)
    else:
        show_status()


if __name__ == "__main__":
    main()
