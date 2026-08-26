"""src/deepdive/outcome.py のテスト。DESIGN_DEEPDIVE.md §3.5 / §9 / §12。

`store.py` の書き込み先パスは `tests/conftest.py` の autouse フィクスチャで
tmp_path へ差し替え済み(手動 monkeypatch 不要)。長期株価だけは prep.py 側で
読み取り専用のため、個々のテストで `prep.LONG_DIR` を monkeypatch して
tmp_path 上に parquet を書く(test_deepdive_prep.py と同じ流儀)。
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.deepdive import outcome, prep, store


def _price_df(start: str, periods: int, base: float, step: float) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    close = [base + i * step for i in range(periods)]
    return pd.DataFrame({
        "date": dates, "open": close, "high": close, "low": close,
        "close": close, "volume": [10_000] * periods,
    })


def _write_parquet(path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _pred_rec(**overrides) -> dict:
    rec = {
        "ticker": "7134", "quarter": "2026Q2", "earnings_date": "2099-01-01",
        "company_op": 1200.0, "my_op": 1350.0, "confidence": "中", "action": "買う",
        "model_ver": "v1", "rationale": "テスト",
    }
    rec.update(overrides)
    return rec


def _actual_rec(**overrides) -> dict:
    rec = {
        "ticker": "7134", "quarter": "2026Q2",
        "disclosed_at": "2026-11-06", "timing": "引け後", "op": 1380.0,
    }
    rec.update(overrides)
    return rec


# ---------------------------------------------------------------------------
# dir_hit / level_err_pct
# ---------------------------------------------------------------------------

def test_dir_hit_true_when_both_sides_up():
    assert outcome.dir_hit(my_op=1350, company_op=1200, actual_op=1380) is True


def test_dir_hit_true_when_both_sides_down():
    assert outcome.dir_hit(my_op=1100, company_op=1200, actual_op=1050) is True


def test_dir_hit_false_when_directions_mismatch():
    assert outcome.dir_hit(my_op=1100, company_op=1200, actual_op=1250) is False


def test_dir_hit_none_when_my_diff_is_zero():
    assert outcome.dir_hit(my_op=1200, company_op=1200, actual_op=1300) is None


def test_dir_hit_none_when_actual_diff_is_zero():
    assert outcome.dir_hit(my_op=1300, company_op=1200, actual_op=1200) is None


def test_dir_hit_none_when_value_missing():
    assert outcome.dir_hit(my_op=None, company_op=1200, actual_op=1300) is None


def test_level_err_pct_basic():
    v = outcome.level_err_pct(my_op=1350, actual_op=1380)
    assert v == pytest.approx((1350 - 1380) / 1380 * 100)


def test_level_err_pct_none_when_actual_zero():
    assert outcome.level_err_pct(my_op=100, actual_op=0) is None


def test_level_err_pct_none_when_missing():
    assert outcome.level_err_pct(my_op=None, actual_op=100) is None


# ---------------------------------------------------------------------------
# disclosure_returns: timing 別の起点(§3.5)
# ---------------------------------------------------------------------------

def _close_series() -> pd.Series:
    # 2026-11-02(Mon)〜2026-11-13(Fri) の10営業日。close = 100, 101, ..., 109
    df = _price_df("2026-11-02", 10, 100.0, 1.0)
    return df.set_index("date")["close"]


def test_disclosure_returns_after_close_base_is_disclosure_day():
    close = _close_series()
    r = outcome.disclosure_returns(close, "2026-11-06", "引け後")
    # 2026-11-06 は index=4 (close=104)。next_day=idx5(105), +5営業日=idx9(109)
    assert r["ret_next_day"] == pytest.approx((105 / 104 - 1) * 100)
    assert r["ret_5d"] == pytest.approx((109 / 104 - 1) * 100)


def test_disclosure_returns_before_open_base_is_previous_day():
    close = _close_series()
    r = outcome.disclosure_returns(close, "2026-11-06", "寄り前")
    # 起点は前営業日 idx=3(close=103)。next_day=idx4(104), +5営業日=idx8(108)
    assert r["ret_next_day"] == pytest.approx((104 / 103 - 1) * 100)
    assert r["ret_5d"] == pytest.approx((108 / 103 - 1) * 100)


def test_disclosure_returns_intraday_same_as_before_open():
    close = _close_series()
    before_open = outcome.disclosure_returns(close, "2026-11-06", "寄り前")
    intraday = outcome.disclosure_returns(close, "2026-11-06", "場中")
    assert intraday == before_open


def test_disclosure_returns_none_when_disclosure_day_not_a_trading_day_after_close():
    close = _close_series()
    # 2026-11-07(土)は非営業日。引け後は発表日ちょうどの終値が起点なので判定不能
    r = outcome.disclosure_returns(close, "2026-11-07", "引け後")
    assert r == {"ret_next_day": None, "ret_5d": None}


def test_disclosure_returns_none_when_target_beyond_series():
    close = _close_series()
    # 系列末尾(idx9)を起点にすると next_day/5d とも範囲外
    r = outcome.disclosure_returns(close, "2026-11-13", "引け後")
    assert r == {"ret_next_day": None, "ret_5d": None}


def test_disclosure_returns_empty_series():
    empty = pd.Series(dtype=float)
    r = outcome.disclosure_returns(empty, "2026-11-06", "引け後")
    assert r == {"ret_next_day": None, "ret_5d": None}


# ---------------------------------------------------------------------------
# build_outcomes / store_outcomes
# ---------------------------------------------------------------------------

def test_build_outcomes_empty_when_no_actual(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    store.add_prediction(_pred_rec())
    assert outcome.build_outcomes("7134", "2026Q2") == []


def test_build_outcomes_empty_when_no_prediction(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    store.add_actual(_actual_rec())
    assert outcome.build_outcomes("7134", "2026Q2") == []


def test_build_outcomes_without_price_data_still_judges_direction(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")  # 空のまま = 価格無し
    store.add_prediction(_pred_rec())
    store.add_actual(_actual_rec())
    out = outcome.build_outcomes("7134", "2026Q2")
    assert len(out) == 1
    rec = out[0]
    assert rec["dir_hit"] is True
    assert rec["level_err_pct"] == pytest.approx((1350 - 1380) / 1380 * 100)
    assert rec["ret_next_day"] is None
    assert rec["ret_5d"] is None


def test_build_outcomes_with_price_data_fills_returns(monkeypatch, tmp_path):
    long_dir = tmp_path / "prices_long"
    monkeypatch.setattr(prep, "LONG_DIR", long_dir)
    _write_parquet(long_dir / "7134.parquet", _price_df("2026-11-02", 10, 100.0, 1.0))
    store.add_prediction(_pred_rec())
    store.add_actual(_actual_rec())  # disclosed_at=2026-11-06, timing=引け後
    out = outcome.build_outcomes("7134", "2026Q2")
    assert len(out) == 1
    rec = out[0]
    assert rec["ret_next_day"] == pytest.approx((105 / 104 - 1) * 100)
    assert rec["ret_5d"] == pytest.approx((109 / 104 - 1) * 100)


def test_build_outcomes_covers_all_model_vers(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    store.add_prediction(_pred_rec(model_ver="v1", my_op=1350))
    store.add_prediction(_pred_rec(model_ver="v2", my_op=1250))
    store.add_actual(_actual_rec())
    out = outcome.build_outcomes("7134", "2026Q2")
    assert {r["model_ver"] for r in out} == {"v1", "v2"}


def test_store_outcomes_appends_and_is_readable_back(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    store.add_prediction(_pred_rec())
    store.add_actual(_actual_rec())
    written = outcome.store_outcomes("7134", "2026Q2")
    assert len(written) == 1
    assert written[0]["written_at"]
    loaded = store.load_last_wins(store.OUTCOMES_PATH, ("ticker", "quarter", "model_ver"))
    assert len(loaded) == 1
    assert loaded[0]["ticker"] == "7134"


def test_store_outcomes_noop_when_nothing_to_judge(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    assert outcome.store_outcomes("7134", "2026Q2") == []
    assert not store.OUTCOMES_PATH.exists()


# ---------------------------------------------------------------------------
# score: valid:false の除外と除外件数報告(§6/§9 必須ケース)
# ---------------------------------------------------------------------------

def _setup_scored_prediction(monkeypatch, ticker, quarter, ver, *, valid, my_op, company_op, actual_op):
    """predict + actual + store_outcomes を1セットぶん流し込む。

    `valid=False` にしたいケースは earnings_date を過去日にして R1 の無効判定を発生させる。
    """
    earnings_date = "2020-01-01" if not valid else "2099-01-01"
    store.add_prediction(_pred_rec(
        ticker=ticker, quarter=quarter, model_ver=ver,
        earnings_date=earnings_date, my_op=my_op, company_op=company_op,
    ))
    store.add_actual(_actual_rec(ticker=ticker, quarter=quarter, op=actual_op))
    outcome.store_outcomes(ticker, quarter)


def test_score_excludes_invalid_predictions_and_reports_excluded_count(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    # 有効: 上振れ的中
    _setup_scored_prediction(
        monkeypatch, "7134", "2026Q1", "v1",
        valid=True, my_op=1300, company_op=1200, actual_op=1380,
    )
    # 無効(記入日が発表日以降扱い): 集計から外れるはず
    _setup_scored_prediction(
        monkeypatch, "7134", "2026Q2", "v1",
        valid=False, my_op=1300, company_op=1200, actual_op=1380,
    )
    result = outcome.score(by="ver")
    assert result["excluded"] == 1
    rows = {r["group"]: r for r in result["rows"]}
    assert rows["v1"]["n"] == 1  # 無効ぶんはカウントされない


def test_score_groups_by_ver_and_computes_hit_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    _setup_scored_prediction(
        monkeypatch, "7134", "2026Q1", "v1",
        valid=True, my_op=1300, company_op=1200, actual_op=1380,  # 的中(上振れ)
    )
    _setup_scored_prediction(
        monkeypatch, "7611", "2026Q1", "v1",
        valid=True, my_op=1100, company_op=1200, actual_op=1380,  # 外れ(下振れ予想→実際は上振れ)
    )
    result = outcome.score(by="ver")
    row = next(r for r in result["rows"] if r["group"] == "v1")
    assert row["n"] == 2
    assert row["hit"] == 1
    assert row["hit_n"] == 2
    assert row["hit_rate_pct"] == pytest.approx(50.0)


def test_score_groups_by_ticker(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    _setup_scored_prediction(
        monkeypatch, "7134", "2026Q1", "v1",
        valid=True, my_op=1300, company_op=1200, actual_op=1380,
    )
    result = outcome.score(by="ticker")
    assert {r["group"] for r in result["rows"]} == {"7134"}


def test_score_invalid_by_raises():
    with pytest.raises(ValueError):
        outcome.score(by="unknown")


def test_format_score_never_mentions_statistical_significance(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    _setup_scored_prediction(
        monkeypatch, "7134", "2026Q1", "v1",
        valid=True, my_op=1300, company_op=1200, actual_op=1380,
    )
    text = outcome.format_score(outcome.score(by="ver"))
    for banned in ("信頼区間", "有意", "統計的"):
        assert banned not in text


def test_format_score_reports_excluded_count_line(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    _setup_scored_prediction(
        monkeypatch, "7134", "2026Q2", "v1",
        valid=False, my_op=1300, company_op=1200, actual_op=1380,
    )
    text = outcome.format_score(outcome.score(by="ver"))
    assert "除外: 1件" in text
