import json

import numpy as np
import pandas as pd
import pytest

import src.pipeline as pipeline
from src.data.prices import PriceUpdateResult
from src.report import build_site


def _make_df(n=300, price_start=1000.0, seed=0):
    dates = pd.bdate_range("2024-01-01", periods=n)
    close = price_start + np.cumsum(np.random.RandomState(seed).randn(n))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": np.random.RandomState(seed + 1).randint(50_000, 150_000, n),
        }
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Wire the pipeline's I/O and heavy-computation seams to test doubles so
    the orchestration (data flow, report/history writes) can be verified
    without network access or re-testing already-covered algorithms."""
    codes = ["1111", "2222", "3333"]
    monkeypatch.setattr(pipeline, "load_universe", lambda: {"stocks": [{"code": c, "name": f"Stock{c}"} for c in codes]})
    monkeypatch.setattr(pipeline.jpholiday, "is_holiday", lambda d: False)

    # Distinct price scales let the vcp/priority mocks below tell the codes
    # apart from the frame alone without needing the code itself:
    # "1111" ~1000, "2222" ~100, "3333" ~5000.
    frames = {
        "1111": _make_df(seed=0, price_start=1000.0),
        "2222": _make_df(seed=1, price_start=100.0),
        "3333": _make_df(seed=2, price_start=5000.0),
    }
    fake_result = PriceUpdateResult(frames=frames, failed_tickers=[], stale_tickers=[], job_failed=False)
    monkeypatch.setattr(pipeline.prices_mod, "update_prices", lambda codes, config: fake_result)

    bench = pd.Series(np.linspace(2000, 2010, 300), index=frames[codes[0]]["date"])
    monkeypatch.setattr(pipeline.prices_mod, "get_benchmark_close", lambda config: bench)

    def fake_screen_universe(latest_by_code, config):
        # "2222" fails the trend template outright; "1111" and "3333" both
        # pass it but only "1111" has a mature VCP setup (see fake_evaluate_vcp).
        # tech_score isn't set here -- pipeline.py always computes its own via
        # score_stock(), this mock's job is only the pass/fail + must_flags.
        return [{"code": c, "passed": c in ("1111", "3333"), "must_flags": {"cond1": True}} for c in latest_by_code]

    monkeypatch.setattr(pipeline.trend_template, "screen_universe", fake_screen_universe)

    def fake_evaluate_priority(latest, config):
        # Mirrors fake_screen_universe: "2222" (close ~100) fails the hard
        # filters -> None; "1111" and "3333" are both P1 so they flow through
        # the VCP/entry path exactly like the pre-priority pipeline did.
        if latest["close"] < 500:
            return None
        return {
            "penalty": 0,
            "priority": 1,
            "unmet": [],
            "ma_deviation_pct": {"ma50": 1.0, "ma150": 2.0, "ma200": 3.0},
            "high52w_distance_pct": 5.0,
            "rs": latest["rs"],
        }

    monkeypatch.setattr(pipeline.priority_mod, "evaluate_priority", fake_evaluate_priority)

    def fake_evaluate_vcp(df, config):
        if df["close"].iloc[0] > 3000:  # "3333": passed trend template, base still forming
            return {"status": "IMMATURE", "must_flags": None, "vcp_score": None, "footprint": None, "contractions": []}
        return {
            "status": "WATCH_A",
            "must_flags": {"V1": True},
            "vcp_score": 70.0,
            "footprint": "6W 20/10/4 3T",
            "contractions": [
                {"high_idx": 10, "high_price": 1010.0, "low_idx": 12, "low_price": 960.0, "depth": 0.05}
            ],
        }

    monkeypatch.setattr(pipeline.vcp_mod, "evaluate_vcp", fake_evaluate_vcp)

    monkeypatch.setattr(pipeline, "DEBUG_PATH", tmp_path / "debug.json")
    monkeypatch.setattr(pipeline.entry_mod, "STATUS_HISTORY_PATH", tmp_path / "status_history.json")
    monkeypatch.setattr(build_site, "DOCS_DATA_DIR", tmp_path)
    monkeypatch.setattr(build_site, "REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(build_site, "BREADTH_PATH", tmp_path / "breadth.json")
    monkeypatch.setattr(build_site, "CHARTS_DIR", tmp_path / "charts")
    monkeypatch.setattr(pipeline.heatmap_mod, "HEATMAP_PATH", tmp_path / "heatmap.json")
    monkeypatch.setattr(pipeline.heatmap_mod, "SECTOR_HISTORY_PATH", tmp_path / "sector_history.json")
    # 公開版(docs/data/sector_history.json)も実リポジトリを汚さない。
    monkeypatch.setattr(pipeline.heatmap_mod, "SECTOR_HISTORY_PUBLIC_PATH", tmp_path / "sector_history_public.json")
    monkeypatch.setattr(pipeline.heatmap_mod, "SECTOR_MAP_PATH", tmp_path / "sector_map.json")
    # 指数取得はネットワークに出さず、実リポジトリの docs/data/indices.json も書かない。
    monkeypatch.setattr(pipeline.indices_mod, "update_indices", lambda config: {"failed": []})
    # 信用残取得はネットワークに出さず、実リポジトリの data/margin_weekly.json も書かない
    # (log.md (51) の実データ汚染事故の教訓。表示専用レイヤーでも必ずモックする)。
    # pipeline側は update_margin_store の戻り値ではなく MARGIN_STORE_PATH を読み直す
    # 実装のため、モックも同じパスへ実際に書き込む。
    margin_store_path = tmp_path / "margin_weekly.json"
    monkeypatch.setattr(pipeline.margin_mod, "MARGIN_STORE_PATH", margin_store_path)
    fake_margin_store = {
        "updated_at": "2026-07-18T00:00:00+09:00",
        "last_url": "https://example.com/syumatsu2026071700.pdf",
        "warnings": [],
        "history": [{"date": "2026-07-17", "by_code": {"1111": {"buy": 1000, "sell": 200}}}],
    }

    def fake_update_margin_store(config):
        from src.utils_io import atomic_write_json
        atomic_write_json(margin_store_path, fake_margin_store)
        return fake_margin_store

    monkeypatch.setattr(pipeline.margin_mod, "update_margin_store", fake_update_margin_store)
    # 枯れ度フォワードログ: 実リポジトリの data/dryup_log.jsonl に追記させない
    # (log_and_resolveのpathデフォルト引数はdef時に束縛済みのため、定数の
    # 差し替えでは効かない。関数ごと差し替える)。
    monkeypatch.setattr(
        pipeline.dryup_log_mod, "log_and_resolve",
        lambda new_records, frames, config=None, path=None: {"appended": len(new_records), "resolved": 0, "total": len(new_records)},
    )
    # J-Quants自動取得はネットワークに出さない。
    monkeypatch.setattr(pipeline.jquants_mod, "update_fundamentals_auto", lambda codes, config: {})
    # source_freshness用のstate読み取り: 実リポジトリの data/*.json に触れない
    # (conftest.pyのautouseでも差し替え済みだが、この読み取りシームは
    # run_daily が直接依存するためここでも明示しておく)。
    monkeypatch.setattr(pipeline.jquants_mod, "STATE_PATH", tmp_path / "jquants_state.json")
    monkeypatch.setattr(pipeline.edinetdb_mod, "STATE_PATH", tmp_path / "edinetdb_state.json")
    # EDINET DB自動取得もネットワークに出さない。
    monkeypatch.setattr(
        pipeline.edinetdb_mod, "update_fundamentals_auto",
        lambda codes, config, base_store=None, priority_by_code=None: {})
    # 実リポジトリの docs/data/fundamentals_public.json を汚さない。
    monkeypatch.setattr(pipeline, "write_public_json", lambda fundamentals_by_code, path=None: None)
    # ポジション管理: 実リポジトリの manual/positions.csv / docs/data/positions.json に触れない。
    monkeypatch.setattr(pipeline.positions_mod, "load_positions_csv", lambda path=None: ([], []))
    monkeypatch.setattr(pipeline.positions_mod, "write_positions_json", lambda report, path=None: None)
    # 地合いシグナルはTOPIXキャッシュの実ファイル読み込みに触れない固定値を返す。
    # 2026-07-18タスク3でシグネチャに breadth_today/breadth_history が追加されたが、
    # このモックは既存のオーケストレーション検証用なので **kwargs で吸収し詳細指標
    # 自体のロジックは tests/test_market_signal.py 側で単体検証する。
    monkeypatch.setattr(
        pipeline.market_signal_mod, "compute_market_signal",
        lambda latest_by_code, config, **kwargs: {
            "signal": "yellow", "reasons": ["テスト用固定値"],
            "pct_above_ma200": 0.4, "pct_above_ma50": 0.5,
            "new_high_count": 1, "new_low_count": 1,
            "index_above_ma50": True, "index_above_ma200": True, "index_ma200_slope_up": True,
        },
    )

    return tmp_path, codes


def test_run_daily_passes_priority_rank_to_edinetdb(wired, monkeypatch):
    # 2026-07-08追加: EDINET DB呼び出しに、この実行で確定したP1〜P4ランクを
    # priority_by_codeとして渡していることを確認する(前回report.jsonではなく、
    # この実行のpriority_mod.evaluate_priority結果を直接使う設計)。
    tmp_path, codes = wired
    captured = {}

    def fake_edinetdb_update(codes, config, base_store=None, priority_by_code=None):
        captured["priority_by_code"] = priority_by_code
        return {}

    monkeypatch.setattr(pipeline.edinetdb_mod, "update_fundamentals_auto", fake_edinetdb_update)

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0
    # "1111"/"3333" はfake_evaluate_priorityでP1、"2222"は評価対象外(除外)。
    assert captured["priority_by_code"] == {"1111": 1, "3333": 1}


def test_run_daily_wires_pipeline_end_to_end(wired):
    tmp_path, codes = wired

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["universe_size"] == 3
    assert report["template_pass"] == 2

    # 2026-07-17追加: ファンダ乖離warning枠(この構成では空)とデータソース鮮度。
    assert report["data_warnings"]["fundamentals_mismatch"] == []
    # stateファイル無し -> jquants/edinetdb は null、価格は当日取得成功 -> 今日の日付。
    assert report["source_freshness"]["jquants"] == {"last_success": None}
    assert report["source_freshness"]["edinetdb"] == {"last_success": None}
    assert report["source_freshness"]["prices"]["last_success"] is not None
    # "1111" (actionable, confirmed/pool tier) sorts ahead of "3333" (watchlist)
    assert [s["code"] for s in report["stocks"]] == ["1111", "3333"]
    assert report["stocks"][0]["footprint"] == "6W 20/10/4 3T"

    # 信用残(タスク1): "1111" は fake_margin_store の by_code にあるので値が入り、
    # "3333"(history に無いコード)は None に落ちる。総合スコアには一切影響しない表示専用層。
    # days_to_cover は _make_df の乱数出来高由来の vol_ma50 に依存するため値までは固定しない。
    margin_1111 = report["stocks"][0]["margin"]
    assert margin_1111["ratio"] == 5.0
    assert margin_1111["buy"] == 1000
    assert margin_1111["sell"] == 200
    assert margin_1111["date"] == "2026-07-17"
    assert margin_1111["buy_wow_pct"] is None  # 前週データなし
    assert report["stocks"][1]["margin"] is None

    watchlist_stock = report["stocks"][1]
    assert watchlist_stock["tier"] == "watchlist"
    assert watchlist_stock["status"] == "IMMATURE"
    assert watchlist_stock["pivot"] is None
    assert watchlist_stock["vcp_score"] is None
    # no vcp_score to combine with -> total_score falls back to tech_score alone
    assert watchlist_stock["total_score"] == watchlist_stock["tech_score"]

    history = json.loads((tmp_path / "status_history.json").read_text(encoding="utf-8"))
    assert "1111" in history
    assert "2222" not in history
    assert "3333" not in history  # watchlist stocks have no pivot to lock in, so no history entry

    assert (tmp_path / "charts" / "1111.json").exists()
    assert (tmp_path / "charts" / "3333.json").exists()  # watchlist stocks still get a chart
    assert not (tmp_path / "charts" / "2222.json").exists()

    breadth = json.loads((tmp_path / "breadth.json").read_text(encoding="utf-8"))
    assert breadth["history"][-1]["template_pass"] == 2
    # 地合いシグナル: compute_market_signal の結果が breadth entry にそのまま載る
    assert breadth["history"][-1]["signal"] == "yellow"
    assert breadth["history"][-1]["pct_above_ma200"] == 0.4

    # 機能A: priority counts + scarcity flag flow into report and breadth
    assert report["priority_counts"] == {"p1": 2, "p2": 0, "p3": 0, "p4": 0}
    assert report["p1_scarce"] is True  # 2 < p1_warn_threshold(3)
    assert report["stocks"][0]["priority"] == 1
    assert report["stocks"][0]["has_chart"] is True
    assert breadth["history"][-1]["p1_count"] == 2

    # 機能B: heatmap json written and sector attributes attached
    heatmap = json.loads((tmp_path / "heatmap.json").read_text(encoding="utf-8"))
    assert heatmap["sectors"]  # at least the fallback sector
    codes_in_hm = {s["code"] for sec in heatmap["sectors"] for s in sec["stocks"]}
    assert codes_in_hm == {"1111", "2222", "3333"}
    assert "sector33" in report["stocks"][0]
    assert (tmp_path / "sector_history.json").exists()


def test_run_daily_source_freshness_reads_state_files(wired, monkeypatch):
    # jquants/edinetdb の state ファイルが存在する場合、その進捗日付が
    # report.json の source_freshness に載る(2026-07-17追加)。
    tmp_path, codes = wired
    (tmp_path / "jquants_state.json").write_text(
        json.dumps({"last_list_date": "2026-04-20"}), encoding="utf-8")
    (tmp_path / "edinetdb_state.json").write_text(
        json.dumps({"last_events_date": "2026-07-15", "backlog": []}), encoding="utf-8")

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["source_freshness"]["jquants"] == {"last_success": "2026-04-20"}
    assert report["source_freshness"]["edinetdb"] == {"last_success": "2026-07-15"}


def test_run_daily_writes_positions_report(wired, monkeypatch):
    tmp_path, codes = wired
    monkeypatch.setattr(
        pipeline.positions_mod, "load_positions_csv",
        lambda path=None: ([{
            "code": "1111", "entry_date": "2026-01-01", "entry_price": 900.0,
            "shares": 100, "initial_stop": 800.0, "current_stop": 850.0, "memo": "",
        }], []),
    )
    captured = {}
    monkeypatch.setattr(
        pipeline.positions_mod, "write_positions_json",
        lambda report, path=None: captured.setdefault("report", report),
    )

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0
    assert captured["report"]["positions"][0]["code"] == "1111"
    assert captured["report"]["positions"][0]["data_missing"] is False


def test_run_daily_skips_on_holiday(wired, monkeypatch):
    tmp_path, codes = wired
    monkeypatch.setattr(pipeline.jpholiday, "is_holiday", lambda d: True)

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0
    assert not (tmp_path / "report.json").exists()


def test_run_daily_aborts_when_too_many_failed_tickers(wired, monkeypatch):
    tmp_path, codes = wired
    failing_result = PriceUpdateResult(frames={}, failed_tickers=codes, job_failed=True)
    monkeypatch.setattr(pipeline.prices_mod, "update_prices", lambda codes, config: failing_result)

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 1
    assert not (tmp_path / "report.json").exists()

# ---------------------------------------------------------------------------
# cooled ティア改修テスト (2026-07-27)
# ---------------------------------------------------------------------------

def test_run_daily_cooled_tier_for_extended_stale(wired, monkeypatch):
    """EXTENDED/STALE 銘柄が cooled ティアで出力され actionable_count に含まれないこと"""
    tmp_path, codes = wired

    # "1111" を EXTENDED ステータスにする: WATCH_A ベースのピボット(1010)より+8%上
    def fake_evaluate_entry_extended(code, latest_row, vcp_result, history, config):
        if code == "1111":
            return {
                "status": "EXTENDED",
                "pivot": 1010.0,
                "buy_stop": 1011.0,
                "stop_loss": 959.5,
                "risk_pct": 5.0,
                "dist_to_pivot": -7.9,
            }
        # その他はデフォルト(ピボットなし)
        return {"status": vcp_result.get("status", "NO_SETUP"), "pivot": None}

    monkeypatch.setattr(pipeline.entry_mod, "evaluate_entry", fake_evaluate_entry_extended)

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    stocks_by_code = {s["code"]: s for s in report["stocks"]}

    # "1111" は cooled ティアに落ちる
    assert stocks_by_code["1111"]["tier"] == "cooled"
    assert stocks_by_code["1111"]["status"] == "EXTENDED"
    # ピボット・損切りがレコードに載っていること
    assert stocks_by_code["1111"]["pivot"] == 1010.0
    assert stocks_by_code["1111"]["stop_loss"] == 959.5

    # "3333" は watchlist に留まる
    assert stocks_by_code["3333"]["tier"] == "watchlist"


def test_run_daily_actionable_count_excludes_cooled(wired, monkeypatch, capsys):
    """EXTENDED/STALE は actionable_count に含まれず cooled_count で集計されること"""
    tmp_path, codes = wired

    def fake_evaluate_entry_stale(code, latest_row, vcp_result, history, config):
        if code == "1111":
            return {
                "status": "STALE",
                "pivot": 1010.0,
                "buy_stop": 1011.0,
                "stop_loss": 959.5,
                "risk_pct": 5.0,
                "dist_to_pivot": 2.0,
            }
        return {"status": vcp_result.get("status", "NO_SETUP"), "pivot": None}

    monkeypatch.setattr(pipeline.entry_mod, "evaluate_entry", fake_evaluate_entry_stale)

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0

    captured = capsys.readouterr()
    # 出力に "0 actionable" かつ "1 cooled" が含まれること
    assert "0 actionable" in captured.out
    assert "1 cooled" in captured.out


def test_run_daily_cooled_pivot_included_in_record(wired, monkeypatch):
    """cooled 銘柄のレコードにピボット・損切りが載ること (実装内容2の (f))"""
    tmp_path, codes = wired

    def fake_evaluate_entry_ext(code, latest_row, vcp_result, history, config):
        if code == "1111":
            return {
                "status": "EXTENDED",
                "pivot": 1010.0,
                "buy_stop": 1011.0,
                "stop_loss": 960.0,
                "risk_pct": 5.0,
                "dist_to_pivot": -8.0,
                "breakout_age_days": 10,
            }
        return {"status": vcp_result.get("status", "NO_SETUP"), "pivot": None}

    monkeypatch.setattr(pipeline.entry_mod, "evaluate_entry", fake_evaluate_entry_ext)

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    cooled = [s for s in report["stocks"] if s["tier"] == "cooled"]
    assert len(cooled) == 1
    assert cooled[0]["pivot"] is not None
    assert cooled[0]["stop_loss"] is not None
    assert cooled[0]["breakout_age_days"] == 10
