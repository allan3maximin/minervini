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

    # "3333" gets a distinctly higher price scale so the vcp mock below can
    # tell it apart from "1111" without needing the code itself.
    frames = {
        "1111": _make_df(seed=0, price_start=1000.0),
        "2222": _make_df(seed=1, price_start=1000.0),
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

    return tmp_path, codes


def test_run_daily_wires_pipeline_end_to_end(wired):
    tmp_path, codes = wired

    rc = pipeline.run_daily(config=pipeline.load_config())
    assert rc == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["universe_size"] == 3
    assert report["template_pass"] == 2
    # "1111" (actionable, confirmed/pool tier) sorts ahead of "3333" (watchlist)
    assert [s["code"] for s in report["stocks"]] == ["1111", "3333"]
    assert report["stocks"][0]["footprint"] == "6W 20/10/4 3T"

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
