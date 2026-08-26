"""src/deepdive/prep.py のテスト。

長期株価/TOPIX/sector_map は実際に tmp_path 上へ parquet/JSON を書いて読ませる
(§4.2/§4.2.1 の「無ければ MissingDataError」を含めて実I/Oの挙動ごと確認するため)。
raw レコード・watchlist・earnings_calendar は prep.py 側の読み出し関数を直接
monkeypatch する(history_store/store 経由のI/Oは jq_raw.py 側で既にテスト済みのため、
ここでは prep.build_a_layer の集計ロジックに焦点を当てる)。ネットワークは一切使わない。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src.deepdive import prep


def _price_df(start: str, periods: int, base: float, step: float,
              volume: int = 10_000, bump_last: int = 0, bump_volume: int = 0) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    close = [base + i * step for i in range(periods)]
    vols = [volume] * periods
    if bump_last:
        for i in range(1, bump_last + 1):
            vols[-i] = bump_volume
    return pd.DataFrame({
        "date": dates, "open": close, "high": close, "low": close,
        "close": close, "volume": vols,
    })


def _write_parquet(path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


# ---------------------------------------------------------------------------
# 個別の読み出しヘルパ
# ---------------------------------------------------------------------------

def test_load_long_prices_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    assert prep.load_long_prices("9999") is None


def test_require_long_prices_raises_with_code_and_command(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    with pytest.raises(prep.MissingDataError) as exc:
        prep.require_long_prices("7134")
    msg = str(exc.value)
    assert "7134" in msg
    assert "tools/fetch_long_history.py --only 7134" in msg


def test_load_long_prices_reads_real_parquet(monkeypatch, tmp_path):
    long_dir = tmp_path / "prices_long"
    monkeypatch.setattr(prep, "LONG_DIR", long_dir)
    _write_parquet(long_dir / "7134.parquet", _price_df("2024-01-02", 5, 1000.0, 1.0))
    df = prep.load_long_prices("7134")
    assert df is not None
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(df) == 5


def test_require_topix_raises_with_asset_command(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "ASSET_DIR", tmp_path / "prices_asset")
    with pytest.raises(prep.MissingDataError) as exc:
        prep.require_topix()
    assert "--tickers jp_1306=1306.T" in str(exc.value)


def test_load_sector_map_missing_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "SECTOR_MAP_PATH", tmp_path / "sector_map.json")
    assert prep.load_sector_map() == {}


def test_load_sector_map_unwraps_sectors_key(monkeypatch, tmp_path):
    path = tmp_path / "sector_map.json"
    path.write_text(
        json.dumps({"generated_at": "x", "sectors": {"7134": "小売業"}}), encoding="utf-8"
    )
    monkeypatch.setattr(prep, "SECTOR_MAP_PATH", path)
    assert prep.load_sector_map() == {"7134": "小売業"}


def test_prep_path_uses_prep_dir():
    p = prep.prep_path("7134", "2026Q2")
    assert p.name == "7134_2026Q2.md"
    assert p.parent == prep.PREP_DIR


# ---------------------------------------------------------------------------
# build_a_layer: 統合テスト
# ---------------------------------------------------------------------------

RAW_RECORDS = [
    # 前々年度FY: 期初予想(NxFOP)の起点
    {
        "DiscDate": "2024-05-14", "DocType": "FinancialStatements_Consolidated_JP",
        "CurPerType": "FY", "CurFYSt": "2023-04-01",
        "Sales": "11000000000", "OP": "650000000", "OdP": "670000000", "NP": "460000000",
        "EPS": "41.0", "FOP": "700000000", "NxFOP": "750000000",
        "FSales": "11500000000", "NxFSales": "12000000000",
    },
    # 前年度1Q・2Q: 過去同時点比較用
    {
        "DiscDate": "2024-08-13", "DocType": "FinancialStatements_Consolidated_JP",
        "CurPerType": "1Q", "CurFYSt": "2024-04-01",
        "Sales": "2800000000", "OP": "150000000", "FOP": "750000000",
        "FSales": "12000000000", "EPS": "13.0",
    },
    {
        "DiscDate": "2024-11-12", "DocType": "FinancialStatements_Consolidated_JP",
        "CurPerType": "2Q", "CurFYSt": "2024-04-01",
        "Sales": "5800000000", "OP": "360000000", "FOP": "750000000",
        "FSales": "12000000000", "EPS": "20.0",
    },
    # 前年度FY実績: 前々年度NxFOP(750M)に対する着地(700M)
    {
        "DiscDate": "2025-05-14", "DocType": "FinancialStatements_Consolidated_JP",
        "CurPerType": "FY", "CurFYSt": "2024-04-01",
        "Sales": "12000000000", "OP": "700000000", "OdP": "720000000", "NP": "500000000",
        "EPS": "45.2", "FOP": "800000000", "NxFOP": "850000000",
        "FSales": "12500000000", "NxFSales": "13000000000",
    },
    # 当年度1Q
    {
        "DiscDate": "2025-08-13", "DocType": "FinancialStatements_Consolidated_JP",
        "CurPerType": "1Q", "CurFYSt": "2025-04-01",
        "Sales": "3100000000", "OP": "190000000", "FOP": "800000000",
        "FSales": "12800000000", "EPS": "11.7",
    },
    # 当年度2Q(最新開示。FOPが800M→820Mに変化=修正1回・上方)
    {
        "DiscDate": "2025-11-12", "DocType": "FinancialStatements_Consolidated_JP",
        "CurPerType": "2Q", "CurFYSt": "2025-04-01",
        "Sales": "6300000000", "OP": "410000000", "FOP": "820000000",
        "FSales": "13000000000", "EPS": "24.5",
    },
    # DocType対象外(業績予想修正) — revision_proxy/next_earnings_date から除外されること
    {
        "DiscDate": "2025-09-01", "DocType": "ForecastRevision_Consolidated_JP",
        "CurPerType": None,
    },
]


@pytest.fixture
def wired_prep(monkeypatch, tmp_path):
    """build_a_layer が読む全ファイルを tmp_path 上に用意し、read系関数を差し替える。"""
    long_dir = tmp_path / "prices_long"
    asset_dir = tmp_path / "prices_asset"
    sector_map_path = tmp_path / "sector_map.json"
    monkeypatch.setattr(prep, "LONG_DIR", long_dir)
    monkeypatch.setattr(prep, "ASSET_DIR", asset_dir)
    monkeypatch.setattr(prep, "SECTOR_MAP_PATH", sector_map_path)
    monkeypatch.setattr(prep, "PREP_DIR", tmp_path / "prep")

    # 700営業日(約2.7年)ぶん。全 RAW_RECORDS の DiscDate をカバーする。
    stock_df = _price_df("2023-06-01", 700, base=1000.0, step=1.0,
                          volume=10_000, bump_last=5, bump_volume=40_000)
    _write_parquet(long_dir / "7134.parquet", stock_df)

    topix_df = _price_df("2023-06-01", 700, base=1500.0, step=0.2)
    _write_parquet(asset_dir / "jp_1306.parquet", topix_df)

    # 同業ピア: 9999は価格ファイルあり(母集団に入る)、8888は無い(黙って外れる)
    peer_df = _price_df("2023-06-01", 700, base=800.0, step=0.3)
    _write_parquet(long_dir / "9999.parquet", peer_df)

    sector_map_path.write_text(
        json.dumps({
            "generated_at": "x",
            "sectors": {"7134": "小売業", "9999": "小売業", "8888": "小売業"},
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(prep, "load_raw_records", lambda code: list(RAW_RECORDS))
    monkeypatch.setattr(
        prep, "load_watch_entry",
        lambda code: {"ticker": code, "name": "アップガレージグループ",
                      "next_earnings_date_manual": None},
    )
    monkeypatch.setattr(
        prep, "load_earnings_calendar", lambda: {"by_code": {"7134": "2026-11-07"}}
    )
    return tmp_path


def test_build_a_layer_missing_price_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(prep, "LONG_DIR", tmp_path / "prices_long")
    monkeypatch.setattr(prep, "ASSET_DIR", tmp_path / "prices_asset")
    with pytest.raises(prep.MissingDataError):
        prep.build_a_layer("7134")


def test_build_a_layer_progress_and_history(wired_prep):
    a = prep.build_a_layer("7134", quarter="2026Q2")
    # 最新開示(2Q, 2025-11-12): OP 410M / FOP 820M = 50%
    assert a["progress"]["op"]["value"] == pytest.approx(50.0)
    assert a["progress"]["op"]["period"] == "2Q"
    # 過去の2Q(360M/750M=48%)との差分 = +2.0pt, n=1
    hist = a["progress_vs_history"]["op"]
    assert hist["n"] == 1
    assert hist["diff_pt"] == pytest.approx(2.0)


def test_build_a_layer_guidance_gap_n_below_3(wired_prep):
    a = prep.build_a_layer("7134")
    gg = a["guidance_gap"]
    # 期初予想750M(前々年度NxFOP)→着地700M(前年度OP): (700-750)/750*100
    assert gg["n"] == 1
    assert gg["median"] is None  # §1.3: n<3 は中央値を出さない
    assert gg["values"] == pytest.approx([(700_000_000 - 750_000_000) / 750_000_000 * 100])


def test_build_a_layer_revision_proxy_counts_fop_change(wired_prep):
    a = prep.build_a_layer("7134")
    rev = a["revision"]
    assert rev["count"] == 1
    assert rev["direction"] == "up"
    assert rev["n"] == 2  # 当年度(CurFYSt=2025-04-01)の開示件数


def test_build_a_layer_excludes_forecast_revision_doctype(wired_prep):
    """DocType が FinancialStatements を含まないレコードは進捗・修正回数の対象外。"""
    a = prep.build_a_layer("7134")
    # ForecastRevision行が混ざっていても2Qの開示件数は2のまま(3にならない)
    assert a["revision"]["n"] == 2


def test_build_a_layer_next_earnings_date_from_calendar(wired_prep):
    a = prep.build_a_layer("7134")
    assert a["next_earnings_date"] == {"date": "2026-11-07", "source": "カレンダー"}


def test_build_a_layer_sector_relative_excludes_missing_peer(wired_prep):
    a = prep.build_a_layer("7134")
    sr = a["sector_relative"]
    assert sr["sector"] == "小売業"
    assert sr["n_peer_codes"] == 1  # 9999のみ。8888は価格ファイルが無いので除外
    assert sr["windows"]["1M"]["n"] == 1


def test_build_a_layer_volume_ratio_reflects_recent_bump(wired_prep):
    a = prep.build_a_layer("7134")
    assert a["volume_ratio"]["5_20"] > 1.0
    assert a["volume_ratio"]["5_60"] > 1.0


def test_build_a_layer_since_earnings_return_uses_latest_disc_date(wired_prep):
    a = prep.build_a_layer("7134")
    since = a["since_earnings_return"]
    assert since["since"] == "2025-11-12"  # 最新開示(2Q)の DiscDate
    assert since["value"] is not None


def test_build_a_layer_per_uses_latest_fy_eps(wired_prep):
    a = prep.build_a_layer("7134")
    per = a["per"]
    assert per["n"] > 0
    assert per["current"] is not None
    # 最新EPSは45.2(前年度FY)。株価は右肩上がりに作ってあるので current PER は正。
    assert per["current"] > 0


def test_build_a_layer_omitted_items_listed(wired_prep):
    a = prep.build_a_layer("7134")
    items = {o["item"] for o in a["omitted"]}
    assert "配当利回り5年レンジ" in items
    assert "PBR 5年レンジ" in items
    assert "EV/EBITDA" in items
    assert "月次 既存店前年比" in items


def test_build_a_layer_data_freshness_paths(wired_prep):
    a = prep.build_a_layer("7134")
    fresh = a["data_freshness"]
    assert fresh["price"]["latest"] is not None
    assert fresh["raw"]["latest_disc_date"] == "2025-11-12"
    assert fresh["benchmark"]["path"].endswith("jp_1306.parquet")
    assert fresh["benchmark"]["latest"] is not None


def test_build_a_layer_default_quarter_label_when_omitted(wired_prep):
    a = prep.build_a_layer("7134")
    # 最新は CurFYSt=2025-04-01 の 2Q → 次は 3Q、年度は据え置き
    assert a["quarter"] == "20253Q"


def test_build_a_layer_explicit_quarter_overrides_default(wired_prep):
    a = prep.build_a_layer("7134", quarter="2026Q2")
    assert a["quarter"] == "2026Q2"
