import pandas as pd

from src.universe import (
    DOMESTIC_STOCK_SEGMENTS,
    filter_domestic_common_stock,
    select_liquid_codes,
)


def _sample_listed_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"code": "1301", "name": "Stock A", "segment": "プライム（内国株式）", "sector33": "水産・農林業", "sector17": "食品"},
            {"code": "1305", "name": "ETF A", "segment": "ETF・ETN", "sector33": "-", "sector17": "-"},
            {"code": "1306", "name": "ETF B", "segment": "ETF・ETN", "sector33": "-", "sector17": "-"},
            {"code": "130A", "name": "Growth Co", "segment": "グロース（内国株式）", "sector33": "医薬品", "sector17": "医薬品"},
            {"code": "131A", "name": "PRO Co", "segment": "PRO Market", "sector33": "情報・通信業", "sector17": "情報通信・サービスその他"},
            {"code": "1330", "name": "REIT A", "segment": "REIT・ベンチャーファンド・カントリーファンド・インフラファンド", "sector33": "-", "sector17": "-"},
            {"code": "1340", "name": "Foreign Std", "segment": "スタンダード（外国株式）", "sector33": "-", "sector17": "-"},
            {"code": "1350", "name": "Standard Co", "segment": "スタンダード（内国株式）", "sector33": "建設業", "sector17": "建設・資材"},
        ]
    )


def test_filter_keeps_only_domestic_common_stock():
    df = _sample_listed_df()
    result = filter_domestic_common_stock(df, config={"universe": {}})
    codes = set(result["code"])
    assert codes == {"1301", "130A", "1350"}
    assert set(result["segment"]) <= DOMESTIC_STOCK_SEGMENTS


def test_filter_excludes_manual_exclude_codes():
    df = _sample_listed_df()
    result = filter_domestic_common_stock(df, config={"universe": {"manual_exclude_codes": ["130A"]}})
    codes = set(result["code"])
    assert "130A" not in codes
    assert codes == {"1301", "1350"}


def _sample_ranking() -> pd.DataFrame:
    """降順の流動性ランキング (単位: 円)。"""
    return pd.DataFrame(
        [
            {"code": "1001", "avg_trading_value": 5_000_000_000.0},  # 50億
            {"code": "1002", "avg_trading_value": 300_000_000.0},    # 3億
            {"code": "1003", "avg_trading_value": 100_000_000.0},    # 1億 ちょうど(境界=採用)
            {"code": "1004", "avg_trading_value": 99_999_999.0},     # 1億弱
            {"code": "1005", "avg_trading_value": 3_000_000.0},      # 300万
        ]
    )


def test_select_liquid_codes_applies_min_trading_value():
    result = select_liquid_codes(
        _sample_ranking(), config={"universe": {"min_trading_value": 100_000_000, "size": None}}
    )
    # 閾値ちょうどは採用 (>=)。1億未満は落とす。
    assert list(result["code"]) == ["1001", "1002", "1003"]


def test_select_liquid_codes_size_is_an_upper_cap_not_the_selector():
    result = select_liquid_codes(
        _sample_ranking(), config={"universe": {"min_trading_value": 100_000_000, "size": 2}}
    )
    assert list(result["code"]) == ["1001", "1002"]


def test_select_liquid_codes_without_threshold_falls_back_to_size():
    result = select_liquid_codes(_sample_ranking(), config={"universe": {"size": 3}})
    assert list(result["code"]) == ["1001", "1002", "1003"]


def test_select_liquid_codes_with_neither_keeps_everything():
    result = select_liquid_codes(_sample_ranking(), config={"universe": {}})
    assert len(result) == 5
    # 後続が itertuples で回すので index は詰め直されている必要がある
    assert list(result.index) == [0, 1, 2, 3, 4]
