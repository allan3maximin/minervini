import pandas as pd

from src.universe import DOMESTIC_STOCK_SEGMENTS, filter_domestic_common_stock


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
