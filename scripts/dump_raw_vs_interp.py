"""dump_raw_vs_interp.py — ネットワーク不要のオフライン解析ダンプスクリプト。

目的:
  本番パイプライン(src/pipeline.py)の生成関数を直接呼び出し、各銘柄について
  (A) 機械ルールへの入力値(生データ) と (B) ルール出力(解釈済み判定) を
  並べた JSON を生成する。暗号化された docs/data/report.json を使わずに、
  ローカルキャッシュ(data/prices/*.parquet)のみから完全に再計算する。

  LLMが後工程で「生データから機械判定を追跡・検証」できるのが目的。

使い方:
  python scripts/dump_raw_vs_interp.py          # 全銘柄処理
  python scripts/dump_raw_vs_interp.py --codes 7203,9984  # 特定銘柄のみ

制約:
  - ネットワーク不使用。update_prices / get_benchmark_close はモンキーパッチで
    キャッシュ参照専用に差し替える。
  - 既存ソースファイルは一切変更しない。
  - 出力先: data/analysis_dump/{full,sample,meta}.json (docs/data/ には書かない)

忠実性ゲート:
  再計算した8フラグを data/trend_template_debug.json(本番2026-07-23実行結果)と
  突合し、99%以上一致しなければ処理を中断してデバッグを促す。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# パス設定: スクリプトがどこから呼ばれても REPO_ROOT を正しく参照する
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# ネットワーク遮断: update_prices / get_benchmark_close のモンキーパッチ
# ---------------------------------------------------------------------------
# 本番 update_prices は yfinance / stooq に接続する。キャッシュ専用版に差し替える。
import src.data.prices as prices_mod
from src.data.prices import PriceUpdateResult, load_cache

def _cache_only_update_prices(codes: list[str], config: dict | None = None) -> PriceUpdateResult:
    """ネットワーク不使用: ローカル Parquet キャッシュだけを読む。"""
    result = PriceUpdateResult()
    for code in codes:
        df = load_cache(code)
        if df is None:
            result.failed_tickers.append(code)
        else:
            result.frames[code] = df
    return result

# モンキーパッチ適用(インポート後すぐに差し替える。get_benchmark_close は内部で
# update_prices を呼ぶので、パッチ後は自動的にキャッシュ読みになる)。
prices_mod.update_prices = _cache_only_update_prices

# ---------------------------------------------------------------------------
# 本番モジュールのインポート(パッチ後にインポートすることで循環を避ける)
# ---------------------------------------------------------------------------
from src.config import load_config
from src.data.fundamentals import (
    build_fundamentals_by_code,
    fund_coverage_tier,
    load_auto_store,
    load_fundamentals_csv,
    merge_fundamentals,
    score_stock,
)
from src.data import edinetdb as edinetdb_mod
from src.indicators import compute_all, rs_percentile_rank
from src.screener import entry as entry_mod
from src.screener import trend_template as tt
from src.screener import vcp as vcp_mod
from src.universe import load_universe
from src.utils_io import safe_load_json

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
OUTPUT_DIR = REPO_ROOT / "data" / "analysis_dump"
DEBUG_PATH = REPO_ROOT / "data" / "trend_template_debug.json"
STATUS_HISTORY_PATH = REPO_ROOT / "data" / "status_history.json"

# CANDIDATEとみなすエントリーステータス(VCP/エントリーが actionable な状態)。
ACTIONABLE_STATUSES = {"WATCH_A", "WATCH_B", "BREAKOUT", "BREAKOUT_WEAK", "EXTENDED", "STALE"}

RANDOM_SEED = 42
RANDOM_SAMPLE_NON_CANDIDATE = 30  # 非CANDIDATEウォッチリストからランダムサンプリング数


# ---------------------------------------------------------------------------
# ユーティリティ: numpy スカラー・NaN を JSON シリアライズ可能な Python 型へ変換
# ---------------------------------------------------------------------------
def to_python(obj):
    """再帰的に numpy 型 / NaN / Inf を JSON セーフな Python 型へ変換する。"""
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_python(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def round_float(v, digits=4):
    """float を指定桁数で丸める。None / NaN は None として返す。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, digits)


# ---------------------------------------------------------------------------
# ベンチマーク(TOPIX代替 ETF)のクローズ系列をキャッシュから取得
# ---------------------------------------------------------------------------
def get_benchmark_close_from_cache(config: dict) -> pd.Series:
    """モンキーパッチ済みの get_benchmark_close を呼び出す。

    prices_mod.update_prices は既にキャッシュ専用版に差し替えられているので、
    get_benchmark_close を呼ぶだけでネットワーク不使用になる。
    """
    return prices_mod.get_benchmark_close(config)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------
def main(only_codes: list[str] | None = None) -> None:
    config = load_config()
    today = datetime.now().date()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- ユニバース読み込み ------------------------------------------------
    universe = load_universe()
    all_stocks = universe["stocks"]
    name_by_code = {s["code"]: s["name"] for s in all_stocks}
    all_codes = [s["code"] for s in all_stocks]

    if only_codes:
        # --codes 指定時は指定銘柄のみ処理(フィルタを超えて全計算は行わない)
        process_codes = [c for c in all_codes if c in set(only_codes)]
        print(f"--codes 指定: {len(process_codes)}銘柄を処理")
    else:
        process_codes = all_codes

    # ---- 価格キャッシュ読み込み (ネットワーク不使用) -----------------------
    print("価格キャッシュ読み込み中 ...")
    frames: dict[str, pd.DataFrame] = {}
    for code in process_codes:
        df = load_cache(code)
        if df is not None:
            frames[code] = df
    n_with_cache = len(frames)
    print(f"  キャッシュあり: {n_with_cache}/{len(process_codes)}銘柄")

    # ---- ベンチマーク(TOPIX ETF)取得 ------------------------------------
    print("ベンチマーク(1306)読み込み中 ...")
    try:
        benchmark_close = get_benchmark_close_from_cache(config)
    except RuntimeError as e:
        print(f"ERROR: ベンチマーク取得失敗: {e}")
        sys.exit(1)

    # ---- 指標計算(compute_all) ------------------------------------------
    print("テクニカル指標計算中 ...")
    indicator_by_code: dict[str, pd.DataFrame] = {}
    for code, df in frames.items():
        indicator_by_code[code] = compute_all(df, benchmark_close)

    # ---- RS パーセンタイルランク(全ユニバース横断) ---------------------
    rs_raw_by_code = {
        code: float(df.iloc[-1]["rs_raw"])
        for code, df in indicator_by_code.items()
    }
    rs_by_code = rs_percentile_rank(rs_raw_by_code)

    # ---- latest ビルド(RS注入) ------------------------------------------
    latest_by_code: dict[str, dict] = {}
    for code, df in indicator_by_code.items():
        rs = rs_by_code.get(code)
        if rs is None:
            continue  # 履歴不足でRSが計算できない銘柄は除外
        latest = df.iloc[-1].to_dict()
        latest["rs"] = rs
        latest_by_code[code] = latest

    # tech_score は「その日のMUST通過銘柄内での断面パーセンタイル」なので、
    # 個別銘柄を回す前にユニバース全体で一括付与しておく(pipeline と同順序)。
    tt.attach_score_percentiles(latest_by_code, config)

    # ---- トレンドテンプレート再計算 & 忠実性ゲート -----------------------
    print("トレンドテンプレート再計算 & 忠実性ゲート検証中 ...")

    # 本番デバッグ JSON を読み込む
    debug_list = safe_load_json(DEBUG_PATH, [])
    debug_by_code = {r["code"]: r for r in debug_list}

    matched = 0
    mismatched_codes: list[dict] = []  # 最大 10 件の不一致を記録

    tt_by_code: dict[str, dict] = {}
    for code, latest in latest_by_code.items():
        flags = tt.check_must_conditions(latest, config)
        passed = tt.passes_trend_template(flags)
        tt_by_code[code] = {"flags": flags, "passed": passed}

        # 本番デバッグと突合
        ref = debug_by_code.get(code)
        if ref is None:
            continue  # 本番実行に含まれていなかった銘柄はゲートからは除外
        ref_flags = ref.get("must_flags", {})
        if flags == ref_flags:
            matched += 1
        else:
            if len(mismatched_codes) < 10:
                diff = {k: (flags[k], ref_flags.get(k)) for k in flags if flags[k] != ref_flags.get(k)}
                mismatched_codes.append({"code": code, "diff_flags": diff})

    comparable = sum(1 for c in latest_by_code if c in debug_by_code)
    match_rate = matched / comparable if comparable > 0 else 0.0
    print(f"  忠実性: {matched}/{comparable} = {match_rate:.1%}")

    if match_rate < 0.99:
        if only_codes:
            # --codes 指定時は部分ユニバースで RS パーセンタイルが変わるため
            # ゲート不合格は想定内。警告のみ出してスキップする。
            print("WARNING: --codes 指定時は RS パーセンタイルランクが全ユニバース計算と"
                  " 異なるためゲート不合格は正常。ゲートをスキップして処理を続行します。")
        else:
            print("ERROR: 忠実性ゲート未通過(99%未満)。")
            print("  不一致銘柄(最大10件):")
            for m in mismatched_codes:
                print(f"    {m['code']}: {m['diff_flags']}")
            print("原因候補: latest の RS 未注入 / ベンチマーク不一致 / config 不一致")
            sys.exit(1)

    # ---- ウォッチリスト = トレンドテンプレート通過銘柄 -------------------
    watchlist_codes = [
        code for code, res in tt_by_code.items() if res["passed"]
    ]
    print(f"ウォッチリスト銘柄数: {len(watchlist_codes)}")

    # ---- ファンダメンタルズ読み込み & マージ -----------------------------
    print("ファンダメンタルズ読み込み中 ...")
    csv_df, _ = load_fundamentals_csv()
    manual_by_code = build_fundamentals_by_code(csv_df)
    auto_by_code = load_auto_store()

    # EDINET DB(決算短信)ストアも読む(jquants と同じく既存キャッシュを使う)
    try:
        tanshin_by_code = safe_load_json(REPO_ROOT / "data" / "edinetdb_auto.json", {})
    except Exception:
        tanshin_by_code = {}

    fundamentals_by_code = merge_fundamentals(
        auto_by_code, manual_by_code, tanshin_by_code=tanshin_by_code
    )

    # ---- ステータス履歴読み込み ------------------------------------------
    history = safe_load_json(STATUS_HISTORY_PATH, {})

    # ---- ウォッチリスト銘柄の VCP / エントリー評価 ----------------------
    print("VCP / エントリー評価中 ...")
    candidate_codes: list[str] = []
    non_candidate_watchlist: list[str] = []
    results_by_code: dict[str, dict] = {}

    for code in watchlist_codes:
        df_ind = indicator_by_code[code]
        latest = latest_by_code[code]
        vcp_result = vcp_mod.evaluate_vcp(df_ind, config)
        entry_result = entry_mod.evaluate_entry(code, latest, vcp_result, history, config)
        fund_info = score_stock(code, latest, fundamentals_by_code, today, config)

        # CANDIDATE 判定: pivot が存在 AND actionable ステータス
        is_candidate = (
            entry_result.get("pivot") is not None
            and entry_result.get("status") in ACTIONABLE_STATUSES
        )

        results_by_code[code] = {
            "vcp": vcp_result,
            "entry": entry_result,
            "fund": fund_info,
            "is_candidate": is_candidate,
        }

        if is_candidate:
            candidate_codes.append(code)
        else:
            non_candidate_watchlist.append(code)

    print(f"  CANDIDATE(actionable): {len(candidate_codes)}銘柄")
    print(f"  非CANDIDATE ウォッチリスト: {len(non_candidate_watchlist)}銘柄")

    # ---- サンプル選択: 全CANDIDATE + 非CANDIDATEからランダム30銘柄(seed=42) -
    rng = random.Random(RANDOM_SEED)
    sampled_non_candidate = rng.sample(
        non_candidate_watchlist,
        min(RANDOM_SAMPLE_NON_CANDIDATE, len(non_candidate_watchlist))
    )
    sample_set = set(candidate_codes) | set(sampled_non_candidate)
    n_sample = len(sample_set)
    print(f"  サンプル合計: {n_sample}銘柄")

    # ---- レコード組み立て ------------------------------------------------
    print("レコード組み立て中 ...")
    records: list[dict] = []

    for code in watchlist_codes:
        res = results_by_code[code]
        latest = latest_by_code[code]
        df_ind = indicator_by_code[code]
        vcp_result = res["vcp"]
        entry_result = res["entry"]
        fund_info = res["fund"]
        is_candidate = res["is_candidate"]
        flags = tt_by_code[code]["flags"]

        # ---- (A) 生データ(入力値) ----------------------------------------
        close = round_float(latest.get("close"), 2)
        high_52w = round_float(latest.get("high_52w"), 2)
        low_52w = round_float(latest.get("low_52w"), 2)
        ma50 = round_float(latest.get("ma50"), 2)

        pct_from_50dma = (
            round_float((close - ma50) / ma50 * 100, 2)
            if close and ma50 else None
        )
        pct_from_high_52w = (
            round_float((high_52w - close) / high_52w * 100, 2)
            if high_52w and close else None
        )

        # 直近20本のOHLCV(チャート検証用)
        recent_ohlcv = []
        for _, row in df_ind.tail(20).iterrows():
            recent_ohlcv.append({
                "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                "open": round_float(row.get("open"), 2),
                "high": round_float(row.get("high"), 2),
                "low": round_float(row.get("low"), 2),
                "close": round_float(row.get("close"), 2),
                "volume": int(row["volume"]) if not pd.isna(row.get("volume", float("nan"))) else None,
            })

        # VCP 収縮リスト(high_price/low_price/depth/dates)
        vcp_contractions = [
            {
                "high_price": round_float(c.get("high_price"), 2),
                "low_price": round_float(c.get("low_price"), 2),
                "depth_pct": round_float(c.get("depth", 0) * 100, 1),
                "high_date": c.get("high_date"),
                "low_date": c.get("low_date"),
                "provisional": c.get("provisional", False),
            }
            for c in (vcp_result.get("contractions") or [])
        ]

        # ファンダ四半期データ
        fund_data = fundamentals_by_code.get(code, {})
        fund_quarters = fund_data.get("quarters") or []
        # eps_yoy / rev_yoy は fund_info に既に計算済み
        fund_eps_yoy = round_float(fund_info.get("fund_eps_yoy"), 1)
        fund_rev_yoy = round_float(fund_info.get("fund_rev_yoy"), 1)

        raw = {
            "close": close,
            "ma50": round_float(latest.get("ma50"), 2),
            "ma150": round_float(latest.get("ma150"), 2),
            "ma200": round_float(latest.get("ma200"), 2),
            "ma200_slope_days": round_float(latest.get("ma200_slope_days"), 0),
            # tech_score の3変数の生値(2026-07-29改定)
            "ma200_slope_21d": round_float(latest.get("ma200_slope_21d"), 4),
            "dryup_med_10_50": round_float(latest.get("dryup_med_10_50"), 4),
            "low_52w": low_52w,
            "high_52w": high_52w,
            "rs": latest.get("rs"),
            "rs_raw": round_float(latest.get("rs_raw"), 4),
            "atr": round_float(latest.get("atr20"), 2),
            "pct_from_50dma": pct_from_50dma,
            "pct_from_high_52w": pct_from_high_52w,
            "recent_ohlcv": recent_ohlcv,
            "vcp_contractions": vcp_contractions,
            "fund_quarters": to_python(fund_quarters),
            "fund_eps_yoy": fund_eps_yoy,
            "fund_rev_yoy": fund_rev_yoy,
        }

        # ---- (B) 解釈済み判定(ルール出力) --------------------------------
        tech_score_val = round_float(tt.technical_score(latest, config), 1)

        # full_score は compute_full_score を直接呼ばず、fund_info から取得
        # (score_stock が既に計算済みなので再実装しない)
        full_score_val = round_float(fund_info.get("full_score"), 1)

        interp = {
            "tt_flags": to_python(flags),
            "passed": bool(tt.passes_trend_template(flags)),
            "tech_score": tech_score_val,
            # tech_score の内訳(当日断面パーセンタイル、等ウェイト平均の前の3成分)
            "score_pct": to_python(latest.get("score_pct")),
            "full_score": full_score_val,
            "vcp_status": vcp_result.get("status"),
            "vcp_score": round_float(vcp_result.get("vcp_score"), 1),
            "footprint": vcp_result.get("footprint"),
            "vcp_flags": to_python(vcp_result.get("must_flags")),
            "entry_status": entry_result.get("status"),
            "pivot": round_float(entry_result.get("pivot"), 2),
            "buy_stop": round_float(entry_result.get("buy_stop"), 2),
            "stop_loss": round_float(entry_result.get("stop_loss"), 2),
            "fund_verdict": fund_info.get("fund_verdict"),
            "fund_multiplier": fund_info.get("fund_multiplier"),
        }

        record = {
            "code": code,
            "name": name_by_code.get(code, ""),
            "classification": "candidate" if is_candidate else "watchlist",
            "in_sample": code in sample_set,
            "raw": raw,
            "interp": interp,
        }
        records.append(record)

    # ---- 出力: full.json ------------------------------------------------
    full_path = OUTPUT_DIR / "full.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(to_python(records), f, ensure_ascii=False, indent=2)
    print(f"full.json 書き出し: {full_path} ({len(records)}銘柄)")

    # ---- 出力: sample.json -----------------------------------------------
    sample_records = [r for r in records if r["in_sample"]]
    sample_path = OUTPUT_DIR / "sample.json"
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(to_python(sample_records), f, ensure_ascii=False, indent=2)
    print(f"sample.json 書き出し: {sample_path} ({len(sample_records)}銘柄)")

    # ---- 出力: meta.json ------------------------------------------------
    tt_cfg = config["trend_template"]
    meta = {
        "run_date": today.isoformat(),
        "n_universe_with_cache": n_with_cache,
        "n_watchlist": len(watchlist_codes),
        "n_candidate": len(candidate_codes),
        "n_sample": n_sample,
        "fidelity_match_rate": round(match_rate, 4),
        "fidelity_matched": matched,
        "fidelity_comparable": comparable,
        "mismatched_codes_sample": mismatched_codes[:10],
        "config_thresholds": {
            "rs_min": tt_cfg.get("rs_min"),
            "ma200_up_days_min": tt_cfg.get("ma200_up_days_min"),
            "low52w_margin": tt_cfg.get("low52w_margin"),
            "high52w_margin": tt_cfg.get("high52w_margin"),
            "score_weights": tt_cfg.get("score_weights"),
            "confirmed_eps_yoy_min": config["fundamentals"].get("confirmed_eps_yoy_min"),
            "confirmed_rev_yoy_min": config["fundamentals"].get("confirmed_rev_yoy_min"),
        },
    }
    meta_path = OUTPUT_DIR / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(to_python(meta), f, ensure_ascii=False, indent=2)
    print(f"meta.json 書き出し: {meta_path}")

    # ---- 最終サマリー出力 ------------------------------------------------
    print("\n=== 完了 ===")
    print(f"  忠実性一致率  : {match_rate:.1%} ({matched}/{comparable})")
    print(f"  ユニバース     : {len(all_codes)}銘柄 / キャッシュあり: {n_with_cache}銘柄")
    print(f"  ウォッチリスト : {len(watchlist_codes)}銘柄")
    print(f"  CANDIDATE      : {len(candidate_codes)}銘柄")
    print(f"  サンプル       : {n_sample}銘柄")
    if mismatched_codes:
        print(f"\n  不一致銘柄(最大10件):")
        for m in mismatched_codes:
            print(f"    {m['code']}: {m['diff_flags']}")
    print(f"\n  出力ファイル:")
    print(f"    {full_path}")
    print(f"    {sample_path}")
    print(f"    {meta_path}")
    print("\nネットワーク未使用(全処理はローカルキャッシュから再計算)。")


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ウォッチリスト銘柄の生データ + 機械判定をオフラインでダンプする"
    )
    parser.add_argument(
        "--codes",
        type=str,
        default=None,
        help="処理対象銘柄コードをカンマ区切りで指定 (例: 7203,9984)。省略時は全ウォッチリスト。",
    )
    args = parser.parse_args()

    only_codes = None
    if args.codes:
        only_codes = [c.strip() for c in args.codes.split(",") if c.strip()]

    main(only_codes=only_codes)
