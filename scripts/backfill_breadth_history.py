"""地合い(breadth.json)履歴の task3 詳細指標を過去日についてbackfillする。

2026-07-18タスク3で market_signal.py に追加した表示専用フィールド
(advancers/decliners/up_down_ratio_25/breadth_trend_20d/net_new_highs/
nh_nl_cumulative/index_trends/growth_rel_20d/market_score/score_breakdown/
score_trend)は、追加された日以降のエントリにしか値が入らず、蓄積型の指標
(up_down_ratio_25は25エントリ、score_trendは5エントリ必要)はしばらく
グラフ・パネルに表示されない。

このスクリプトは backtest.py と同じ「backward-looking rolling計算は
フルhistoryを一度計算してから任意の過去日でスライスしても同じ値になる
(look-aheadなし)」という性質を使い、data/prices/*.parquet と
data/indices/{topix,nikkei225,growth250}.parquet の既存キャッシュだけで
(追加のネットワーク取得なしに)breadth.json の既存エントリへ上記フィールドを
追加する。

安全策:
- pre-task3フィールド(pct_above_ma200/pct_above_ma50/new_high_count/
  new_low_count/universe_size/template_pass/watch_count/
  breakout_success_rate/p1〜p4_count/signal/reasons/index_above_ma50等)は
  一切読み書きしない。触るのは NEW_FIELDS のみ。
- 各エントリの各フィールドは「現在値がNoneの場合のみ」新しい値で埋める
  (実運用パイプラインが既に計算済みの値を上書きしない)。
- entry.market_score が既にNoneでなければ「そのエントリは計算済み」とみなし
  丸ごとスキップする(再実行しても安全な冪等スクリプト)。

暗号化について: docs/data/breadth.json は env DASHBOARD_DATA_KEY が設定されて
いれば AES-256-GCM封筒として読み書きする(read_docs_json/write_docs_jsonが
自動判別。src/report/secure_io.py参照)。このリポジトリでは鍵はGitHub Secrets
にのみ置かれておりローカルには無いため、鍵が無い環境で実行すると
「暗号化されていますが鍵が未設定です」という RuntimeError になる。
実行する側で `DASHBOARD_DATA_KEY=<鍵> python scripts/backfill_breadth_history.py`
のように鍵を設定して実行すること。

実行: python scripts/backfill_breadth_history.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.config import load_config
from src.data import indices as indices_mod
from src.data import prices as prices_mod
from src.indicators import compute_all
from src.report import build_site
from src.report.market_signal import compute_market_signal
from src.report.secure_io import write_docs_json
from src.universe import load_universe

# task3で新規追加されたフィールドのみを対象にする(pre-existingフィールドは触らない)。
NEW_FIELDS = [
    "advancers", "decliners", "up_down_ratio_25", "breadth_trend_20d",
    "net_new_highs", "nh_nl_cumulative", "index_trends", "growth_rel_20d",
    "market_score", "score_breakdown", "score_trend",
]

# このフィールドが非Noneなら「そのエントリは計算済み」とみなす(冪等判定用)。
# compute_market_signal は market_score を必ず数値で返す(条件付きNoneが無い)ので
# マーカーとして安全。
DONE_MARKER = "market_score"


def load_all_price_frames() -> dict[str, pd.DataFrame]:
    """ユニバース銘柄の指標付き価格フレームを1回だけ計算してキャッシュする。

    backtest.py の load_universe_frames() と同方針: 260日未満のキャッシュは
    指標が安定しないためスキップ。RS(rs_line)はcompute_breadth_statsで使わない
    のでbenchmark_closeは渡さない。
    """
    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    frames: dict[str, pd.DataFrame] = {}
    skipped = 0
    for code in codes:
        df = prices_mod.load_cache(code)
        if df is None or len(df) < 260:
            skipped += 1
            continue
        frames[code] = compute_all(df.reset_index(drop=True))
    print(f"price frames loaded: {len(frames)} codes ({skipped} skipped, insufficient history)")
    return frames


def _slice_latest_by_code(
    frames: dict[str, pd.DataFrame], target_date: pd.Timestamp
) -> tuple[dict, int, int]:
    """target_date以下にスライスした各銘柄の最新行 + advancers/decliners。

    pipeline.py の実運用ロジック(df.iloc[-1]/df.iloc[-2]の終値比較)を、
    「target_date以下の行にスライスしてから同じ比較をする」形で再現する。
    """
    latest_by_code: dict = {}
    advancers = decliners = 0
    for code, df in frames.items():
        sub = df[df["date"] <= target_date]
        if sub.empty:
            continue
        latest_by_code[code] = sub.iloc[-1].to_dict()
        if len(sub) >= 2:
            c0 = sub.iloc[-2]["close"]
            c1 = sub.iloc[-1]["close"]
            if pd.notna(c0) and pd.notna(c1):
                if c1 > c0:
                    advancers += 1
                elif c1 < c0:
                    decliners += 1
    return latest_by_code, advancers, decliners


def _slice(df: pd.DataFrame | None, target_date: pd.Timestamp) -> pd.DataFrame | None:
    """target_date以下にスライスする。

    注意: 空でも(0行でも)Noneにせずそのまま返すこと。compute_market_signal は
    index_df/nikkei_df/growth_df に None を渡すと「省略された」とみなして
    indices_mod.load_cache() で実キャッシュ(=未来日込みのフル履歴)を読みに
    行ってしまう(look-ahead混入)。0行のDataFrameならcompute_index_trendが
    「データ不足」としてNone判定してくれるので、それで安全に代替する。
    df自体がNone(=そもそもキャッシュファイルが存在しない)の場合のみNoneを返す
    (この場合はcompute_market_signal側の再取得も同じくNoneになるだけで無害)。
    """
    if df is None:
        return None
    return df[df["date"] <= target_date]


def backfill_history(
    history: list[dict],
    frames: dict[str, pd.DataFrame],
    config: dict,
    topix_df: pd.DataFrame | None = None,
    nikkei_df: pd.DataFrame | None = None,
    growth_df: pd.DataFrame | None = None,
) -> tuple[list[dict], dict]:
    """I/O を持たない純粋なbackfillロジック(テスト容易性のため分離)。

    history は日付昇順(呼び出し側で保証すること)。エントリは in-place で
    更新され、更新後の同じリストを返す。統計dict: updated/already_done/no_data。
    """
    breadth_history_so_far: list[dict] = []
    updated_count = 0
    already_done_count = 0
    no_data_count = 0

    for entry in history:
        date_str = entry["date"]
        if entry.get(DONE_MARKER) is not None:
            # 既に実運用パイプライン(または過去のbackfill実行)が計算済み。
            already_done_count += 1
            breadth_history_so_far.append(entry)
            continue

        target_date = pd.Timestamp(date_str)
        latest_by_code, advancers, decliners = _slice_latest_by_code(frames, target_date)
        if not latest_by_code:
            no_data_count += 1
            breadth_history_so_far.append(entry)
            continue

        result = compute_market_signal(
            latest_by_code,
            config,
            index_df=_slice(topix_df, target_date),
            breadth_today={"advancers": advancers, "decliners": decliners},
            breadth_history=breadth_history_so_far,
            nikkei_df=_slice(nikkei_df, target_date),
            growth_df=_slice(growth_df, target_date),
        )

        for f in NEW_FIELDS:
            if entry.get(f) is None:
                entry[f] = result.get(f)
        updated_count += 1
        breadth_history_so_far.append(entry)

    stats = {
        "updated": updated_count,
        "already_done": already_done_count,
        "no_data": no_data_count,
        "total": len(history),
    }
    return history, stats


def backfill(dry_run: bool = False) -> None:
    config = load_config()
    breadth = build_site.load_breadth()
    history = sorted(breadth.get("history", []), key=lambda h: h["date"])
    if not history:
        print("breadth.json history is empty; nothing to backfill.")
        return

    frames = load_all_price_frames()
    topix_df = indices_mod.load_cache("topix")
    nikkei_df = indices_mod.load_cache("nikkei225")
    growth_df = indices_mod.load_cache("growth250")
    if topix_df is None:
        print("WARN: data/indices/topix.parquet not found; index trend will be None for all dates.")

    history, stats = backfill_history(history, frames, config, topix_df, nikkei_df, growth_df)
    print(
        f"updated: {stats['updated']} entries / already-done(skipped): {stats['already_done']} "
        f"/ no-data(skipped): {stats['no_data']} / total: {stats['total']}"
    )

    if dry_run:
        print("--dry-run: not writing breadth.json")
        return

    if stats["updated"] == 0:
        print("no changes; not writing breadth.json")
        return

    breadth["history"] = history
    write_docs_json(build_site.BREADTH_PATH, breadth)
    print(f"wrote: {build_site.BREADTH_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="breadth.json の地合い詳細指標(task3分)を過去日について再計算しbackfillする"
    )
    parser.add_argument("--dry-run", action="store_true", help="計算のみ行い書き込まない")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
