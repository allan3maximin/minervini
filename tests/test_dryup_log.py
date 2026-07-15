import pandas as pd

from src.report import dryup_log as dl

CONFIG = {"entry": {"stop_loss_pct": 0.05}}

DRYUP_LAYER = {
    "dryup_med_10_50": 0.55,
    "dryup_avg_5_50": 0.5,
    "tightness_10d": 0.04,
    "is_tightest_in_base": True,
    "shakeout_detected": False,
    "dist_to_pivot": 1.2,
}


def _mkdf(closes, start="2026-01-01"):
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "close": closes,
            "low": [c * 0.99 for c in closes],
            "high": [c * 1.01 for c in closes],
        }
    )


def test_build_log_record_shape():
    rec = dl.build_log_record("2026-01-01", "7203", "WATCH_A", DRYUP_LAYER, 100.0)
    assert set(rec.keys()) == set(dl.RECORD_KEYS)
    assert rec["code"] == "7203"
    assert rec["status"] == "WATCH_A"
    assert rec["dryup_med_10_50"] == 0.55
    assert rec["pivot"] == 100.0
    assert rec["outcome"] is None and rec["outcome_date"] is None


def test_resolve_breakout_ok():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    df = _mkdf([99] * 3 + [101] * 15)
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] == "breakout_ok"


def test_resolve_fills_vol_ratio_at_breakout():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    df = _mkdf([99] * 3 + [101] * 15)
    # ブレイク日(index 3, close 101)の出来高倍率 = 3000/1000 = 3.0
    df["volume"] = [1000] * len(df)
    df["vol_ma50"] = [1000.0] * len(df)
    df.loc[3, "volume"] = 3000
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] in ("breakout", "breakout_ok")
    assert rec["vol_ratio_at_breakout"] == 3.0


def test_resolve_breakout_failed():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    # breaks out to 101 then collapses below entry*(1-0.05) low
    df = _mkdf([99] * 2 + [101] + [90] * 12)
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] == "breakout_failed"


def test_resolve_broken_proxy():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    # never exceeds pivot; drops below pivot*(1-0.10) = 90 -> broken proxy
    df = _mkdf([99, 95, 89] + [89] * 12)
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] == "broken"


def test_resolve_expired():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    df = _mkdf([99] * 25)  # 24 future bars, none break/broke
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] == "expired"


def test_resolve_pending_insufficient_data():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    df = _mkdf([99, 99, 99])  # only 2 future bars -> undecided
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] is None


def test_resolve_idempotent_on_terminal():
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    rec["outcome"] = "broken"
    rec["outcome_date"] = "2026-01-05"
    df = _mkdf([101] * 30)  # would be breakout, but terminal is preserved
    dl.resolve_record(rec, df, CONFIG)
    assert rec["outcome"] == "broken"


def test_log_and_resolve_roundtrip(tmp_path):
    path = tmp_path / "dryup_log.jsonl"
    # day 1: append a WATCH record, no future data yet
    rec = dl.build_log_record("2026-01-01", "A", "WATCH_A", DRYUP_LAYER, 100.0)
    frames = {"A": _mkdf([99])}
    stat = dl.log_and_resolve([rec], frames, CONFIG, path=path)
    assert stat["appended"] == 1 and stat["total"] == 1

    # later run: full history now available -> resolves to breakout_ok
    frames = {"A": _mkdf([99] * 3 + [101] * 15)}
    stat = dl.log_and_resolve([], frames, CONFIG, path=path)
    assert stat["resolved"] == 1
    records = dl.load_records(path)
    assert records[0]["outcome"] == "breakout_ok"
