"""Daily pipeline entrypoint.

Usage:
    python -m src.pipeline                  # daily run (uses cached universe)
    python -m src.pipeline --universe-rebuild  # rebuild universe.json first
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime

import jpholiday
import pandas as pd

from src.config import REPO_ROOT, load_config
from src.data import indices as indices_mod
from src.data import margin as margin_mod
from src.data import prices as prices_mod
from src.data import edinetdb as edinetdb_mod
from src.data import jquants as jquants_mod
from src.data.fundamentals import (
    build_fundamentals_by_code,
    load_fundamentals_csv,
    merge_fundamentals,
    score_stock,
    write_public_json,
)
from src.indicators import build_dryup_layer, compute_all, rs_percentile_rank
from src.report import build_site
from src.report import dryup_log as dryup_log_mod
from src.report import heatmap as heatmap_mod
from src.report import market_signal as market_signal_mod
from src.report import positions as positions_mod
from src.report import regime_stats as regime_stats_mod
from src.report import stage_log as stage_log_mod
from src.report import summary as summary_mod
from src.report.secure_io import preflight_data_key
from src.screener import entry as entry_mod
from src.screener import priority as priority_mod
from src.screener import trend_template
from src.screener import vcp as vcp_mod
from src.universe import build_universe, load_universe
from src.utils_io import safe_load_json

DEBUG_PATH = REPO_ROOT / "data" / "trend_template_debug.json"

# 今日エントリー判断ができるステータス(セットアップが生きていて追いかけ禁止でない)。
# EXTENDED(伸びすぎ)と STALE(ブレイク鮮度切れ)は両方とも追いかけ禁止なので
# ここには含めず、別途 COOLED_ENTRY_STATUSES で管理し cooled ティアへ隔離する。
ACTIONABLE_ENTRY_STATUSES = {"BREAKOUT", "BREAKOUT_WEAK", "WATCH_A", "WATCH_B"}

# 追いかけ禁止ステータス: ブレイク済みで既に手遅れ。ピボット情報はあるが
# エントリー不可。watchlist(セットアップ形成待ち)とは意味が違うため別ティア。
COOLED_ENTRY_STATUSES = {"EXTENDED", "STALE"}


def run_daily(universe_rebuild: bool = False, config: dict | None = None) -> int:
    config = config or load_config()
    today = datetime.now().date()

    # スナップショット方式(前場終了バッチ用、2026-07-21): env SCREENER_SNAPSHOT に
    # ラベル(例 "maezyou")が入っていると、report/breadth/positions/indices/heatmap を
    # _<label>.json へ書き出し、canonical(EOD)ファイルとフォワード検証の永続状態
    # (status_history / dryup_log / stage.jsonl / sector.jsonl / per-stock charts)を
    # 一切上書きしない。これで前場断面が EOD の後場ボタン用データと独立に残る。
    # 価格ストアの途中足だけは夕方の日次バッチ(RECHECK_DAYS=30)が自己修復する。
    # (2026-07-31: ヒートマップは自己修復頼みだったが、大引バッチが落ちた日に前場の
    #  セクター値がその日の履歴として確定してしまうため、書かない側へ倒した)
    snapshot_label = (os.environ.get("SCREENER_SNAPSHOT") or "").strip()
    snapshot_suffix = f"_{snapshot_label}" if snapshot_label else ""
    is_snapshot = bool(snapshot_suffix)

    if jpholiday.is_holiday(today):
        print(f"{today} is a JP holiday; skipping.")
        return 0

    # 鍵の配線ミスを「取りに行く前」に落とす。docs/data が暗号化済みなのに
    # DASHBOARD_DATA_KEY が無いと update_breadth (終盤) で必ず落ちるので、
    # 全銘柄の価格取得を終えてから死ぬのを避ける。詳細は preflight_data_key。
    preflight_data_key(build_site.DOCS_DATA_DIR)

    # Market overview indices (Nikkei/TOPIX/Growth/JGB10y/USDJPY/NASDAQ/SOX).
    # Fully independent of the screener; a failure here must never block it.
    try:
        idx_result = indices_mod.update_indices(config)
        if idx_result["failed"]:
            print(f"Index fetch failed (kept cache if any): {idx_result['failed']}")
    except Exception as e:
        print(f"Index update crashed (ignored): {e}")

    # 信用残(週次、火曜16:30頃公表)。表示専用レイヤーで総合スコアには使わない
    # (config margin セクション参照)。失敗しても本体は止めない(indices/heatmapと同方針)。
    try:
        margin_result = margin_mod.update_margin_store(config)
        if margin_result.get("warnings"):
            print(f"Margin update warnings: {margin_result['warnings']}")
    except Exception as e:
        print(f"Margin update crashed (ignored): {e}")
    margin_store = safe_load_json(margin_mod.MARGIN_STORE_PATH, {})

    if universe_rebuild:
        build_universe(config)

    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    name_by_code = {s["code"]: s["name"] for s in universe["stocks"]}
    # 時価総額(株式数×終値)と市場区分。segmentは2026-07-12以降のユニバース再構築で
    # 入る(それ以前のuniverse.jsonではNone → 表示側でスキップ)。
    shares_by_code = {s["code"]: s.get("shares_outstanding") for s in universe["stocks"]}
    segment_by_code = {
        s["code"]: (str(s["segment"]).split("（")[0] if s.get("segment") else None)
        for s in universe["stocks"]
    }
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

    # 地合い詳細化(タスク3): 全ユニバース銘柄の騰落(前日比終値)をここでまとめて
    # カウントする。df がまだ手元にあるこのループが最も安く済む場所(RS計算前で
    # rs_by_code に無い銘柄も含め、全上場銘柄ベースでカウントする)。
    advancers = decliners = 0
    latest_by_code = {}
    for code, df in indicator_by_code.items():
        if len(df) >= 2:
            prev_close = df["close"].iloc[-2]
            curr_close = df["close"].iloc[-1]
            if not pd.isna(prev_close) and not pd.isna(curr_close):
                if curr_close > prev_close:
                    advancers += 1
                elif curr_close < prev_close:
                    decliners += 1

        rs = rs_by_code.get(code)
        if rs is None:
            continue  # insufficient history for RS -- excluded from screening
        latest = df.iloc[-1].to_dict()
        latest["rs"] = rs
        latest_by_code[code] = latest
    breadth_today = {"advancers": advancers, "decliners": decliners}

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

    # J-Quantsでファンダメンタル自動取得(増分)→手動CSVとマージ。
    # APIキー未設定なら既存ストアを読むだけ。失敗しても本体は止めない。
    try:
        auto_by_code = jquants_mod.update_fundamentals_auto(codes, config)
    except Exception as e:
        print(f"J-Quants fundamentals update failed (ignored): {e}")
        auto_by_code = {}

    # EDINET DB (決算短信) で J-Quants 12週遅延窓の直近四半期を補完。
    # APIキー未設定/enabled:false なら既存ストアを読むだけ。失敗しても本体は止めない。
    # P1〜P4優先度(上のpriority_by_code、技術指標のみで決まりファンダに依存しない
    # ためこの時点で確定済み)でbacklogを並べ替え、P1が無くてもP2→P3→P4の順で
    # 優先的にファンダを取得する(2026-07-08追加)。
    priority_rank_by_code = {code: ev["priority"] for code, ev in priority_by_code.items()}
    try:
        tanshin_by_code = edinetdb_mod.update_fundamentals_auto(
            codes, config, base_store=auto_by_code, priority_by_code=priority_rank_by_code)
    except Exception as e:
        print(f"EDINET DB fundamentals update failed (ignored): {e}")
        tanshin_by_code = {}

    # jquants(auto)とedinetdb(tanshin)が同一四半期で20%超乖離した場合の警告を
    # 受け取り、report.json の data_warnings.fundamentals_mismatch に載せる。
    fundamentals_mismatch_warnings: list[str] = []
    fundamentals_by_code = merge_fundamentals(
        auto_by_code, build_fundamentals_by_code(csv_df), tanshin_by_code=tanshin_by_code,
        warnings_out=fundamentals_mismatch_warnings)
    write_public_json(fundamentals_by_code)

    # 決算発表予定日カレンダー(J-Quants /equities/earnings-calendar、日次1〜数req)。
    # 3月期・9月期決算企業のみ提供。失敗時は前回キャッシュ、キー無しなら空。
    try:
        next_earnings_by_code = jquants_mod.update_earnings_calendar(codes, config)
    except Exception as e:
        print(f"Earnings calendar update failed (ignored): {e}")
        next_earnings_by_code = {}

    # ポジション管理: manual/positions.csv (保有銘柄) の現在値・R倍数・売りシグナルを計算。
    # 失敗しても本体は止めない。ユニバース外の保有銘柄は indicator_by_code に無いだけなので安全
    # (data_missing扱いになる。既知の制約 -- HANDOFF §12参照)。
    try:
        positions, positions_csv_warnings = positions_mod.load_positions_csv()
        positions_report = positions_mod.build_positions_report(
            positions, indicator_by_code, name_by_code, margin_store=margin_store, config=config
        )
        positions_report["warnings"] = positions_csv_warnings + positions_report["warnings"]
        positions_path = (
            build_site.DOCS_DATA_DIR / f"positions{snapshot_suffix}.json"
            if is_snapshot else None
        )
        positions_mod.write_positions_json(positions_report, path=positions_path)
    except Exception as e:
        print(f"Positions report update failed (ignored): {e}")

    history = entry_mod.load_status_history()
    previous_status_by_code = {code: entry_mod.previous_status(history, code) for code in codes}

    today_str = today.isoformat()
    stock_records = []
    dryup_records = []  # 本番フォワード検証: WATCH_A/B の枯れ度レイヤーを毎日追記
    watch_count = 0
    actionable_count = 0
    cooled_count = 0
    # VCP評価対象(P1)の origin/status 分布を地合い観測用に集計。
    vcp_status_counts = Counter()

    for code, pr_eval in priority_by_code.items():
        tt_result = tt_by_code[code]

        if pr_eval["priority"] != 1:
            # P2〜P4: フロントは priority===1||null のP1銘柄しか表示しないため、
            # 2026-07-11以降 report.json への出力自体を止める(転送量削減)。
            # priority_counts(pr_counts)はここより上の priority_by_code から独立に
            # 計算済みなので、breadth.jsonのp1_count〜p4_count記録には影響しない。
            continue

        df_ind = indicator_by_code[code]
        vcp_result = vcp_mod.evaluate_vcp(df_ind, config)
        vcp_status_counts[vcp_result["status"]] += 1
        entry_result = entry_mod.evaluate_entry(code, latest_by_code[code], vcp_result, history, config)
        fund_info = score_stock(code, latest_by_code[code], fundamentals_by_code, today, config)

        has_pivot = entry_result.get("pivot") is not None
        is_actionable = has_pivot and entry_result["status"] in ACTIONABLE_ENTRY_STATUSES
        is_cooled = has_pivot and entry_result["status"] in COOLED_ENTRY_STATUSES

        # stop_ref_low の解決(actionable と cooled で共通)。
        def _resolve_stop_ref_low():
            if vcp_result.get("status") == "WATCH_A" and vcp_result.get("contractions"):
                return vcp_result["contractions"][-1]["low_price"]
            locked = entry_mod.locked_pivot(history, code)
            return locked.get("stop_ref_low") if locked else None

        if is_actionable:
            actionable_count += 1
            stop_ref_low = _resolve_stop_ref_low()

            history = entry_mod.record_status(
                history, code, today_str, entry_result["status"], entry_result["pivot"], stop_ref_low, config
            )

            if entry_result["status"] in ("WATCH_A", "WATCH_B"):
                watch_count += 1
                # 本番フォワード検証用: 枯れ度レイヤー(indicators.build_dryup_layer が
                # 唯一の生成点。ここで再実装しない)を1行1レコードで蓄積する。
                base_days = vcp_result.get("base_days")
                base_start_idx = (len(df_ind) - base_days) if base_days else None
                dryup_layer = build_dryup_layer(
                    df_ind,
                    -1,
                    base_start_idx,
                    entry_result.get("pivot"),
                    vcp_result.get("shakeout_detected", False),
                    latest_by_code[code].get("vol_ma50"),
                )
                dryup_records.append(
                    dryup_log_mod.build_log_record(
                        today_str, code, entry_result["status"], dryup_layer,
                        entry_result.get("pivot"),
                    )
                )

            record = build_site.assemble_stock_record(
                code,
                name_by_code.get(code, ""),
                latest_by_code[code],
                tt_result["must_flags"],
                vcp_result,
                entry_result,
                fund_info,
                config,
                margin_store=margin_store,
            )

            if entry_result["status"] == "BREAKOUT":
                record["new_breakout_today"] = previous_status_by_code.get(code) == "WATCH_A"
                if topix_return is not None and entry_mod.market_guard_triggered(topix_return, config):
                    record["market_guard_warning"] = True

        elif is_cooled:
            # Cooled tier: ブレイク済みで追いかけ禁止(EXTENDED/STALE)。ピボット情報は
            # あるが新規エントリーは不可。watchlist(セットアップ形成待ち)とは意味が
            # 違うため別ティアに隔離する。チャートJSONは actionable と同様に出力する。
            cooled_count += 1
            stop_ref_low = _resolve_stop_ref_low()

            history = entry_mod.record_status(
                history, code, today_str, entry_result["status"], entry_result["pivot"], stop_ref_low, config
            )

            record = build_site.assemble_stock_record(
                code,
                name_by_code.get(code, ""),
                latest_by_code[code],
                tt_result["must_flags"],
                vcp_result,
                entry_result,
                fund_info,
                config,
                tier_override="cooled",
                margin_store=margin_store,
            )

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
                margin_store=margin_store,
            )

        # スナップショット(前場)は canonical チャートを途中足で上書きしないよう
        # per-stock chart 書き出しをスキップ(前場ボタンの市場サマリーはチャート不使用)。
        build_site.attach_priority(record, pr_eval)
        record["momentum"] = summary_mod.compute_momentum(df_ind)
        # リスト画面カード用の前日比%。終値2本から素直に計算(旧データ・上場直後
        # 等で前日終値が取れない場合はNone=フロントは空表示)。
        closes = df_ind["close"]
        record["change_pct"] = (
            round(float(closes.iloc[-1] / closes.iloc[-2] - 1.0) * 100.0, 2)
            if len(closes) >= 2 and closes.iloc[-2] else None
        )
        shares = shares_by_code.get(code)
        close = latest_by_code[code].get("close")
        record["market_cap_oku"] = (
            round(shares * close / 1e8) if shares and close else None
        )
        record["market_segment"] = segment_by_code.get(code)
        record["next_earnings_date"] = next_earnings_by_code.get(code)
        record["has_chart"] = True
        stock_records.append(record)
        if not is_snapshot:
            chart_data = build_site.build_chart_data(
                code, df_ind, vcp_result, entry_result,
                fund_entry=fundamentals_by_code.get(code),
            )
            build_site.write_chart_data(code, chart_data)

    # スナップショット(前場)は status_history / dryup_log を永続化しない。途中足の
    # ピボットロックやフォワード検証レコードで EOD の確定データを汚さないため。
    if not is_snapshot:
        entry_mod.save_status_history(history)

        # 本番フォワード検証: 既存レコードの outcome を解決 → 当日の WATCH_A/B を追記。
        # 失敗してもスクリーナー本体は止めない(検証用の副次成果物)。
        try:
            dryup_stat = dryup_log_mod.log_and_resolve(dryup_records, indicator_by_code, config)
            print(
                f"Dry-up log: appended {dryup_stat['appended']}, "
                f"resolved {dryup_stat['resolved']}, total {dryup_stat['total']}."
            )
        except Exception as e:
            print(f"Dry-up log update failed (ignored): {e}")

    # 機能B: セクターヒートマップ生成 + セクター強度属性の付与。
    # 失敗してもスクリーナー本体は止めない。
    # スナップショット(前場)は heatmap_maezyou.json へ書き、大引の heatmap.json と
    # セクターの日次履歴には触らない(判断は build_heatmap 側の説明を参照)。
    # 返ってくる sector_strength_by_code はレコードの表示属性なので前場でも付ける。
    try:
        hm_result = heatmap_mod.build_heatmap(
            universe, price_result.frames, benchmark_close, stock_records, config, today_str,
            snapshot_suffix=snapshot_suffix,
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

    # 地合いシグナル(市場ブレッドス + 多観点指数トレンド合成)。失敗してもスクリーナー本体は
    # 止めない。breadth_history は当日エントリ追記前の既存履歴(update_breadthより先に
    # 読む。I/Oはpipeline側で行い、market_signal.py側はテスト容易性のため純関数に保つ)。
    try:
        breadth_history = build_site.load_breadth().get("history", [])
        signal_result = market_signal_mod.compute_market_signal(
            latest_by_code, config, breadth_today=breadth_today, breadth_history=breadth_history,
        )
    except Exception as e:
        print(f"Market signal computation failed (ignored): {e}")
        signal_result = None

    # ルールベース日本語サマリー生成。セクター強度・地合いシグナルまで確定した
    # あとに全レコードへ付与する(summary.pyは既存判定の言語化のみで新判断はしない)。
    for record in stock_records:
        fund_entry = fundamentals_by_code.get(record["code"]) or {}
        try:
            record["summary"] = summary_mod.build_stock_summary(
                record,
                quarters=fund_entry.get("quarters"),
                guidance=fund_entry.get("guidance"),
                market_signal=signal_result,
                config=config,
                today=today,
            )
            # 個別銘柄画面・分析用コピーが数値として参照できる解釈済みガイダンス。
            record["guidance_view"] = summary_mod.derive_guidance_view(
                fund_entry.get("quarters") or [], fund_entry.get("guidance"),
                close=record.get("close"))
        except Exception as e:
            print(f"Summary build failed for {record['code']} (ignored): {e}")

    template_pass = sum(1 for r in tt_results if r["passed"])
    data_warnings = {
        "failed_tickers": price_result.failed_tickers,
        "stale_tickers": price_result.stale_tickers,
        "csv_errors": csv_warnings,
        "fundamentals_mismatch": fundamentals_mismatch_warnings,
    }
    # データソースの鮮度: 各ソースの「最後に成功した日」をダッシュボードへ渡す。
    # jquants/edinetdb は state ファイルの進捗日付(壊れていれば safe_load_json が
    # 空dictを返すので null)、prices は当日 update_prices が1銘柄でも取れたか。
    jq_state = safe_load_json(jquants_mod.STATE_PATH, {})
    ed_state = safe_load_json(edinetdb_mod.STATE_PATH, {})
    source_freshness = {
        "jquants": {"last_success": jq_state.get("last_list_date")},
        "edinetdb": {"last_success": ed_state.get("last_events_date")},
        "prices": {"last_success": today_str if price_result.frames else None},
    }
    build_site.build_report(
        stock_records,
        universe_size=len(codes),
        template_pass=template_pass,
        data_warnings=data_warnings,
        priority_counts=pr_counts,
        p1_scarce=p1_scarce,
        source_freshness=source_freshness,
        snapshot_suffix=snapshot_suffix,
    )
    # 監視タブのバケット別内訳。vcp_funnel と違い stock_records (フロントが実際に
    # 食う確定レコード) から数えるので、EXTENDED/STALE 上書き後の件数と一致する。
    stage_funnel = stage_log_mod.build_stage_funnel(stock_records)
    build_site.update_breadth(
        today_str, len(codes), template_pass, watch_count, history,
        priority_counts=pr_counts, market_signal=signal_result,
        vcp_funnel=dict(vcp_status_counts),
        stage_funnel=stage_funnel,
        snapshot_suffix=snapshot_suffix,
    )
    # 「あと一歩」がどれだけ待機A/Bへ昇格するかを後日測るための銘柄別スナップショット
    # (src/report/stage_log.py 参照)。集計値だけでは銘柄を跨いだ追跡ができない。
    # スナップショット(前場)は EOD の確定履歴を汚さないので記録しない。
    if not is_snapshot:
        try:
            n_stage = stage_log_mod.update_stage_history(today_str, stock_records, config)
            print(f"Stage history: appended {n_stage} rows.")
        except Exception as e:
            print(f"Stage history update failed (ignored): {e}")

        # 本番で毎日出している候補の「その後」を貯めて docs/data/stats.json を書く
        # (src/report/regime_stats.py)。stage.jsonl へ当日分を追記したあと、かつ
        # update_breadth が breadth.json に当日の地合いスコアを載せたあとでないと
        # 当日の候補行が作れないので、この位置より前には置けない。
        # 前場ラン(is_snapshot)では呼ばない: 途中足の値を「その日の確定値」として
        # 履歴に焼き込んでしまうため(dryup_log / stage_log / review と同じ理由)。
        # 価格は既に指標まで載せた indicator_by_code を渡す(parquet を読み直さない)。
        # 失敗してもスクリーナー本体は止めない(検証用の副次成果物)。
        try:
            outcome_stat = regime_stats_mod.update_candidate_outcomes(indicator_by_code)
            print(
                f"Candidate outcomes: new {outcome_stat['new']}, "
                f"advanced {outcome_stat['advanced']}, "
                f"appended {outcome_stat['appended']}, "
                f"settled {outcome_stat['settled']}/{outcome_stat['total']}."
            )
        except Exception as e:
            print(f"Candidate outcomes update failed (ignored): {e}")
    # スナップショットは indices.json を直接生成しない(intraday-indices.yml が
    # 15分間隔で更新している canonical をそのまま断面として複製する)。
    if is_snapshot:
        build_site.snapshot_docs_json("indices", snapshot_suffix)

    # 日次レビュー(src/report/review.py)。前場断面と大引断面を突き合わせて
    # 「前場に出ていた候補が引けまでにどうなったか」を1枚にまとめる。大引ランでしか
    # 作らない: 前場ランで書くと途中の値が「その日の確定レビュー」の顔をして半日
    # 公開されてしまう。読むファイルは docs/data 配下なので、report/breadth を
    # 書き終えたこの位置より前には置けない。失敗しても本体は止めない。
    if not is_snapshot:
        # レビューの「一日の値動きの形」は指数の日中の点から出す。15分間隔の
        # ワークフローだけに任せると点が3〜4個しか残らず一度も判定できていなかったので、
        # 大引後にその日の5分足をまとめて取りに行って埋める(indices.py 側に理由あり)。
        try:
            added = indices_mod.backfill_intraday_bars(today_str)
            if added:
                print(f"Intraday bars: {today_str} 分を {added} 点補完しました。")
        except Exception as e:
            print(f"Intraday bars backfill failed (ignored): {e}")

        try:
            from src.report import review as review_mod
            result = review_mod.update_review(today_str)
            if result is not None:
                compared = "あり" if result.get("has_maezyou") else "なし"
                print(f"Review: {today_str} 分を生成しました (前場との比較: {compared})。")
        except Exception as e:
            print(f"Review build failed (ignored): {e}")

    watchlist_count = len(stock_records) - actionable_count - cooled_count
    print(
        f"Done. {template_pass}/{len(codes)} passed trend template, "
        f"{actionable_count} actionable, {cooled_count} cooled, {watchlist_count} watchlist "
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
