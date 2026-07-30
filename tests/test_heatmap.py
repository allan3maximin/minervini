import json

import pandas as pd
import pytest

from src.report import heatmap as hm

CFG = dict(hm.DEFAULTS)


def linear_series(start: float, step: float, n: int) -> pd.Series:
    return pd.Series([start + step * i for i in range(n)])


def make_frame(start: float, step: float, n: int = 70) -> pd.DataFrame:
    close = linear_series(start, step, n)
    return pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n), "close": close})


# ------------------------------------------------------------ compute_returns


def test_compute_returns_basic():
    close = pd.Series([100.0, 110.0])
    out = hm.compute_returns(close, [1, 5])
    assert out["d1"] == 10.0
    assert out["d5"] is None  # データ不足


def test_compute_returns_needs_p_plus_1_rows():
    close = pd.Series([100.0] * 5 + [105.0])  # 6行 → d5はOK, d6はNG
    out = hm.compute_returns(close, [5, 6])
    assert out["d5"] == 5.0
    assert out["d6"] is None


def test_compute_returns_zero_base():
    close = pd.Series([0.0, 100.0])
    assert hm.compute_returns(close, [1])["d1"] is None


# ----------------------------------------------------------------- sector_rs


@pytest.mark.parametrize(
    "rel20,expected_strength",
    [(2.0, "強"), (1.99, "中"), (-1.99, "中"), (-2.0, "弱")],
)
def test_sector_rs_strength_thresholds(rel20, expected_strength):
    sec = {"d20": 5.0 + rel20, "d5": 1.0}
    top = {"d20": 5.0, "d5": 1.0}
    assert hm.sector_rs(sec, top, CFG)["strength"] == expected_strength


@pytest.mark.parametrize(
    "rel5,expected_dir",
    [(0.5, "↑"), (0.49, "→"), (-0.49, "→"), (-0.5, "↓")],
)
def test_sector_rs_direction_thresholds(rel5, expected_dir):
    sec = {"d20": 1.0, "d5": 1.0 + rel5}
    top = {"d20": 1.0, "d5": 1.0}
    assert hm.sector_rs(sec, top, CFG)["direction"] == expected_dir


def test_sector_rs_none_when_missing():
    result = hm.sector_rs({"d20": None, "d5": None}, {"d20": 1.0, "d5": 1.0}, CFG)
    assert result["strength"] is None
    assert result["direction"] is None


# ---------------------------------------------------------- _weighted_returns


def test_weighted_returns_mcap_weighting():
    stocks = [
        {"returns": {"d1": 10.0}, "mcap": 300},
        {"returns": {"d1": 0.0}, "mcap": 100},
    ]
    out = hm._weighted_returns(stocks, [1])
    assert out["d1"] == 7.5  # (10*300 + 0*100)/400


def test_weighted_returns_fallback_simple_mean():
    stocks = [
        {"returns": {"d1": 10.0}, "mcap": None},
        {"returns": {"d1": 0.0}, "mcap": None},
    ]
    assert hm._weighted_returns(stocks, [1])["d1"] == 5.0


def test_weighted_returns_all_none():
    assert hm._weighted_returns([{"returns": {"d1": None}, "mcap": 1}], [1])["d1"] is None


# --------------------------------------------------------------- build_heatmap


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "HEATMAP_PATH", tmp_path / "heatmap.json")
    monkeypatch.setattr(hm, "SECTOR_HISTORY_PATH", tmp_path / "sector_history.json")
    # 追記専用JSONL (2026-07-27〜)。実体はこちらで、上の旧JSONは移行用フォールバック。
    monkeypatch.setattr(hm, "SECTOR_HISTORY_JSONL", tmp_path / "history" / "sector.jsonl")
    # 公開版(docs/data/sector_history.json)も実リポジトリを汚さない。
    monkeypatch.setattr(hm, "SECTOR_HISTORY_PUBLIC_PATH", tmp_path / "sector_history_public.json")
    monkeypatch.setattr(hm, "SECTOR_MAP_PATH", tmp_path / "sector_map.json")
    return tmp_path


def build_fixture_inputs():
    universe = {
        "stocks": [
            {"code": "1001", "name": "アルファ", "sector33": "電気機器", "shares_outstanding": 1000},
            {"code": "1002", "name": "ベータ", "sector33": "電気機器", "shares_outstanding": None},
            {"code": "2001", "name": "ガンマ", "sector33": None, "shares_outstanding": 500},
        ]
    }
    frames = {
        "1001": make_frame(100.0, 1.0),   # 上昇
        "1002": make_frame(200.0, -0.5),  # 下落
        "2001": make_frame(50.0, 0.2),
    }
    benchmark_close = linear_series(1000.0, 2.0, 70)
    stock_records = [
        {
            "code": "1001",
            "tier": "confirmed",
            "status": "WATCH_A",
            "priority": 1,
            "priority_penalty": 0,
            "priority_unmet": [],
            "ma_deviation_pct": {"ma50": 5.0, "ma150": 10.0, "ma200": 15.0},
            "high52w_distance_pct": 3.0,
            "rs": 90,
            "has_chart": True,
        },
        {
            "code": "1002",
            "tier": "watchlist",
            "priority": 2,
            "priority_penalty": 1,
            "priority_unmet": [{"condition": "rs_above_min", "penalty": 1, "distance_pct": -5}],
            "ma_deviation_pct": {"ma50": -1.0, "ma150": 2.0, "ma200": 4.0},
            "high52w_distance_pct": 10.0,
            "rs": 65,
        },
    ]
    config = {"heatmap": {}}
    return universe, frames, benchmark_close, stock_records, config


def test_build_heatmap_output(patched_paths):
    universe, frames, bench, records, config = build_fixture_inputs()
    result = hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")

    data = json.loads((patched_paths / "heatmap.json").read_text(encoding="utf-8"))
    assert data["date"] == "2026-07-06"
    assert data["topix_returns"]["d1"] is not None

    sectors = {s["sector"]: s for s in data["sectors"]}
    assert set(sectors) == {"電気機器", hm.UNKNOWN_SECTOR}

    elec = sectors["電気機器"]
    assert elec["stock_count"] == 2
    assert elec["p1_count"] == 1
    assert elec["p2_count"] == 1
    tiles = {t["code"]: t for t in elec["stocks"]}
    # mcap = 発行済株数 × 最新終値 (1001: 1000株 × 169)
    assert tiles["1001"]["mcap"] == 169000
    assert tiles["1002"]["mcap"] is None
    assert tiles["1001"]["detail"]["has_chart"] is True
    assert tiles["1002"]["detail"]["priority_unmet"][0]["condition"] == "rs_above_min"
    # mcap降順ソート (Noneは最後)
    assert elec["stocks"][0]["code"] == "1001"

    # sector_strength_by_code は全銘柄分
    sbc = result["sector_strength_by_code"]
    assert set(sbc) == {"1001", "1002", "2001"}
    assert sbc["1001"]["sector"] == "電気機器"
    assert sbc["2001"]["sector"] == hm.UNKNOWN_SECTOR

    # 履歴も書かれる
    history = hm.load_sector_history()
    assert len(history["history"]) == 1
    assert history["history"][0]["date"] == "2026-07-06"
    assert "電気機器" in history["history"][0]["sectors"]


def test_build_heatmap_skips_missing_frames(patched_paths):
    universe, frames, bench, records, config = build_fixture_inputs()
    frames.pop("2001")
    result = hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")
    assert "2001" not in result["sector_strength_by_code"]


def test_sector_map_fallback(patched_paths):
    universe, frames, bench, records, config = build_fixture_inputs()
    (patched_paths / "sector_map.json").write_text(
        json.dumps({"sectors": {"2001": "サービス業"}}, ensure_ascii=False), encoding="utf-8"
    )
    result = hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")
    assert result["sector_strength_by_code"]["2001"]["sector"] == "サービス業"


def test_sector_history_same_date_overwrite(patched_paths):
    universe, frames, bench, records, config = build_fixture_inputs()
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-07")
    # JSONLには3行追記されているが、読み出し時に後勝ちdedupされるので日付は2つ。
    history = hm.load_sector_history()
    dates = [e["date"] for e in history["history"]]
    assert dates == ["2026-07-06", "2026-07-07"]


def test_snapshot_run_writes_suffixed_json_and_no_history(patched_paths):
    """前場断面: heatmap_maezyou.json だけを書き、大引JSONも履歴も触らない。"""
    universe, frames, bench, records, config = build_fixture_inputs()
    result = hm.build_heatmap(
        universe, frames, bench, records, config, "2026-07-06", snapshot_suffix="_maezyou"
    )

    assert (patched_paths / "heatmap_maezyou.json").exists()
    # 大引の heatmap.json は前場ランでは生成すらしない(上書き以前に触らない)。
    assert not (patched_paths / "heatmap.json").exists()
    # 日次バッチが落ちた日に前場の値が確定値になってしまわないよう履歴は書かない。
    assert hm.load_sector_history()["history"] == []
    assert not (patched_paths / "sector_history_public.json").exists()
    # レコードに載せる表示属性(所属セクター・強弱)は前場でも返す。
    assert result["sector_strength_by_code"]["1001"]["sector"] == "電気機器"


def test_snapshot_run_does_not_overwrite_existing_eod_heatmap(patched_paths):
    """大引ランのあとに前場ランが走っても、大引JSON・履歴は前場の値で潰れない。"""
    universe, frames, bench, records, config = build_fixture_inputs()
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")
    eod_before = (patched_paths / "heatmap.json").read_text(encoding="utf-8")

    hm.build_heatmap(
        universe, frames, bench, records, config, "2026-07-07", snapshot_suffix="_maezyou"
    )
    assert (patched_paths / "heatmap.json").read_text(encoding="utf-8") == eod_before
    dates = [e["date"] for e in hm.load_sector_history()["history"]]
    assert dates == ["2026-07-06"]


def test_sector_history_keep_days(patched_paths):
    universe, frames, bench, records, config = build_fixture_inputs()
    config["heatmap"] = {"history_keep_days": 2}
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-04")
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-05")
    hm.build_heatmap(universe, frames, bench, records, config, "2026-07-06")
    # JSONLの compaction は遅延実行なのでディスク上には古い行が残りうる。
    # 公開データ側が history_keep_days で間引かれていることを確認する。
    published = json.loads(
        (patched_paths / "sector_history_public.json").read_text(encoding="utf-8"))
    assert published["dates"] == ["2026-07-05", "2026-07-06"]
