"""Daily pipeline entrypoint.

Usage:
    python -m src.pipeline                  # daily run (uses cached universe)
    python -m src.pipeline --universe-rebuild  # rebuild universe.json first
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import jpholiday

from src.config import REPO_ROOT, load_config
from src.data import indices as indices_mod
from src.data import prices as prices_mod
from src.data.fundamentals import build_fundamentals_by_code, load_fundamentals_csv, score_stock
from src.indicators import compute_all, rs_percentile_rank
from src.report import build_site
from src.report import heatmap as heatmap_mod
from src.screener import entry as entry_mod
from src.screener import priority as priority_mod
from src.screener import trend_template
from src.screener import vcp as vcp_mod
from src.universe import build_universe, load_universe

DEBUG_PATH = REPO_ROOT / "data" / "trend_template_debug.json"

# Statuses worth surfacing on the dashboard: an active setup, or a stock
# that's already broken out of one (tracked via the locked historical pivot).
ACTIONABLE_ENTRY_STATUSES = {"BREAKOUT", "BREAKOUT_WEAK", "WATCH_A", "WATCH_B", "EXTENDED"}


def run_daily(universe_rebuild: bool = False, config: dict | None = None) -> int:
    config = config or load_config()
    today = datetime.now().date()

    if jpholiday.is_holiday(today):
        print(f"{today} is a JP holiday; skipping.")
        return 0

    # Market overview indices (Nikkei/TOPIX/Growth/JGB10y/USDJPY/NASDAQ/SOX).
    # Fully independent of the screener; a failure here must never block it.
    try:
        idx_result = indices_mod.update_indices(config)
        if idx_result["failed"]:
            print(f"Index fetch failed (kept cache if any): {idx_result['failed']}")
    except Exception as e:
        print(f"Index update crashed (ignored): {e}")

    if universe_rebuild:
        build_universe(config)

    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    name_by_code = {s["code"]: s["name"] for s in universe["stocks"]}
    if not codes:
        print("Universe is empty; run with --universe-rebuild first.")
        return 1

    price_result = prices_mod.update_prices(codes, config)
    if price_result.job_failed:
        print(f"Too many failed tickers ({len(price_result.failed_tickers)}/{len(codes)}); aborting.")
        return 1

    benchmark_close = prices_mod.get_benchmark_close(config)
    topix_return = None
    if len(benchmark_close) >= 2:
        topix_return = float(benchmark_close.iloc[-1] / benchmark_close.iloc[-2] - 1.0)

    indicator_by_code = {
        code: compute_all(df, benchmark_close) for code, df in price_result.frames.items()
    }

    rs_raw_by_code = {code: df.iloc[-1]["rs_raw"] for code, df in indicator_by_code.items()}
    rs_by_code = rs_percentile_rank(rs_raw_by_code)

    latest_by_code = {}
    for code, df in indicator_by_code.items():
        rs = rs_by_code.get(code)
        if rs is None:
            continue  # insufficient history for RS -- excluded from screening
        latest = df.iloc[-1].to_dict()
        latest["rs"] = rs
        latest_by_code[code] = latest

    tt_results = trend_template.screen_universe(latest_by_code, config)
    tt_by_code = {r["code"]: r for r in tt_results}
    with open(DEBUG_PATH, "w", encoding="utf-8") as f:
        json.dump(tt_results, f, ensure_ascii=False, indent=2, default=str)

    # 機能A: ハードフィルタ通過銘柄のプライオリティ評価(P1〜P4)。
    # P1 == トレンドテンプレート8条件完全一致。P2〜P4は旧ウォッチリストを置き換える。
    priority_by_code = {}
    for code, latest in latest_by_code.items():
        pr_eval = priority_mod.evaluate_priority(latest, config)
        if pr_eval is not None:
            priority_by_code[code] = pr_eval
    pr_counts = priority_mod.priority_counts(list(priority_by_code.values()))
    p1_scarce = pr_counts["p1"] < config.get("priority", {}).get("p1_warn_threshold", 3)

    csv_df, csv_warnings = load_fundamentals_csv()
    fundamentals_by_code = build_fundamentals_by_code(csv_df)

    history = entry_mod.load_status_history()
    previous_status_by_code = {code: entry_mod.previous_status(history, code) for code in codes}

    today_str = today.isoformat()
    stock_records = []
    watch_count = 0
    actionable_count = 0

    for code, pr_eval in priority_by_code.items():
        tt_result = tt_by_code[code]

        if pr_eval["priority"] != 1:
            # P2〜P4: VCP/エントリー評価なしの軽量レコード(旧ウォッチリスト置き換え)。
            record = build_site.assemble_priority_record(
                code,
                name_by_code.get(code, ""),
                latest_by_code[code],
                pr_eval,
                tt_flags=tt_result["must_flags"],
            )
            stock_records.append(record)
            continue

        df_ind = indicator_by_code[code]
        vcp_result = vcp_mod.evaluate_vcp(df_ind, config)
        entry_result = entry_mod.evaluate_entry(code, latest_by_code[code], vcp_result, history, config)
        fund_info = score_stock(code, latest_by_code[code], fundamentals_by_code, today, config)

        is_actionable = entry_result.get("pivot") is not None and entry_result["status"] in ACTIONABLE_ENTRY_STATUSES

        if is_actionable:
            actionable_count += 1
            stop_ref_low = None
            if vcp_result.get("status") == "WATCH_A" and vcp_result.get("contractions"):
                stop_ref_low = vcp_result["contractions"][-1]["low_price"]
            else:
                locked = entry_mod.locked_pivot(history, code)
                if locked:
                    stop_ref_low = locked.get("stop_ref_low")

            history = entry_mod.record_status(
                history, code, today_str, entry_result["status"], entry_result["pivot"], stop_ref_low, config
            )

            if entry_result["status"] in ("WATCH_A", "WATCH_B"):
                watch_count += 1

            record = build_site.assemble_stock_record(
                code,
                name_by_code.get(code, ""),
                latest_by_code[code],
                tt_result["must_flags"],
                vcp_result,
                entry_result,
                fund_info,
                config,
            )

            if entry_result["status"] == "BREAKOUT":
                record["new_breakout_today"] = previous_status_by_code.get(code) == "WATCH_A"
                if topix_return is not None and entry_mod.market_guard_triggered(topix_return, config):
                    record["market_guard_warning"] = True
        else:
            # Watchlist: passed the trend template, but VCP hasn't produced
            # an actionable base yet (still building, too recent, or the
            # base broke down). No pivot/stop levels, but still worth
            # surfacing as a tier below the pool instead of disappearing
            # entirely once it falls out of an active setup.
            record = build_site.assemble_stock_record(
                code,
                name_by_code.get(code, ""),
                latest_by_code[code],
                tt_result["must_flags"],
                vcp_result,
                entry_result,
                fund_info,
                config,
                tier_override="watchlist",
            )

        build_site.attach_priority(record, pr_eval)
        record["has_chart"] = True
        stock_records.append(record)
        chart_data = build_site.build_chart_data(code, df_ind, vcp_result, entry_result)
        build_site.write_chart_data(code, chart_data)

    entry_mod.save_status_history(history)

    # 機能B: セクターヒートマップ生成 + セクター強度属性の付与。
    # 失敗してもスクリーナー本体は止めない。
    try:
        hm_result = heatmap_mod.build_heatmap(
            universe, price_result.frames, benchmark_close, stock_records, config, today_str
        )
        strength_by_code = hm_result["sector_strength_by_code"]
        for record in stock_records:
            info = strength_by_code.get(record["code"])
            if info:
                record["sector33"] = info["sector"]
                record["sector_strength"] = info["strength"]
                record["sector_direction"] = info["direction"]
    except Exception as e:
        print(f"Heatmap build failed (ignored): {e}")

    template_pass = sum(1 for r in tt_results if r["passed"])
    data_warnings = {
        "failed_tickers": price_result.failed_tickers,
        "stale_tickers": price_result.stale_tickers,
        "csv_errors": csv_warnings,
    }
    build_site.build_report(
        stock_records,
        universe_size=len(codes),
        template_pass=template_pass,
        data_warnings=data_warnings,
        priority_counts=pr_counts,
        p1_scarce=p1_scarce,
    )
    build_site.update_breadth(
        today_str, len(codes), template_pass, watch_count, history, priority_counts=pr_counts
    )

    watchlist_count = len(stock_records) - actionable_count
    print(
        f"Done. {template_pass}/{len(codes)} passed trend template, "
        f"{actionable_count} actionable, {watchlist_count} watchlist "
        f"(P1:{pr_counts['p1']} P2:{pr_counts['p2']} P3:{pr_counts['p3']} P4:{pr_counts['p4']})."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Minervini screener daily pipeline")
    parser.add_argument("--universe-rebuild", action="store_true", help="Rebuild data/universe.json first")
    args = parser.parse_args()
    sys.exit(run_daily(universe_rebuild=args.universe_rebuild))


if __name__ == "__main__":
    main()
