"""sheet.render() のテスト。

build_a_layer() は呼ばない。build_a_layer が返す dict のスキーマを手で組み立てて
render() に渡し、出力を検証する(sheet.py は純関数フォーマッタなので入力を
固定できる)。「フルデータ」ケースと「ほぼ None(算出不能)」ケースの2種類。
"""
from __future__ import annotations

from src.deepdive import sheet


def _full_a_layer() -> dict:
    return {
        "code": "7134",
        "name": "テスト商事",
        "quarter": "2026Q1",
        "generated_at": "2026-08-25T12:00:00+09:00",
        "progress": {
            "sales": {"value": 48.0, "period": "1Q", "ytd": 480_000_000, "plan": 1_000_000_000},
            "op": {"value": 50.0, "period": "1Q", "ytd": 50_000_000, "plan": 100_000_000},
        },
        "progress_vs_history": {
            "sales": {"diff_pt": 1.5, "n": 2},
            "op": {"diff_pt": -2.0, "n": 2},
        },
        "guidance_gap": {"n": 3, "median": 4.2, "values": [3.0, 4.2, 5.5]},
        "revision": {"count": 2, "direction": "up", "n": 3},
        "per": {"current": 15.23, "pct": 62.0, "start": "2024-08-25", "end": "2026-08-22", "n": 480},
        "returns": {
            "1M": {"abs": 3.2, "topix_relative": 1.1},
            "3M": {"abs": -5.4, "topix_relative": -2.0},
        },
        "sector_relative": {
            "sector": "情報・通信業",
            "n_peer_codes": 5,
            "windows": {
                "1M": {"value": 0.8, "median": 2.4, "n": 4},
                "3M": {"value": -1.0, "median": -4.4, "n": 3},
            },
        },
        "since_earnings_return": {"value": 6.7, "since": "2026-05-14"},
        "volume_ratio": {"5_20": 1.23, "5_60": 0.98},
        "next_earnings_date": {"date": "2026-11-13", "source": "calendar"},
        "omitted": [
            {"item": "PBR / EV/EBITDA", "reason": "純資産・有利子負債を取得していないため(恒久)"},
            {"item": "PER の5年レンジ", "reason": "J-Quants Free が2年しか返さないため 2.3年レンジで代用"},
            {"item": "月次", "reason": "手入力待ち"},
        ],
        "data_freshness": {
            "price": {"path": "data/prices_long/7134.parquet", "latest": "2026-08-22"},
            "raw": {"path": "data/deepdive/raw/7134.jsonl", "latest_disc_date": "2026-05-14"},
            "benchmark": {"path": "data/prices_asset/jp_1306.parquet", "latest": "2026-08-22"},
        },
    }


def _empty_a_layer() -> dict:
    return {
        "code": "9999",
        "name": "",
        "quarter": "2026Q1",
        "generated_at": "2026-08-25T12:00:00+09:00",
        "progress": {
            "sales": {"value": None, "period": None, "ytd": None, "plan": None},
            "op": {"value": None, "period": None, "ytd": None, "plan": None},
        },
        "progress_vs_history": {
            "sales": {"diff_pt": None, "n": 0},
            "op": {"diff_pt": None, "n": 0},
        },
        "guidance_gap": {"n": 0, "median": None, "values": []},
        "revision": {"count": 0, "direction": None, "n": 0},
        "per": {"current": None, "pct": None, "start": None, "end": None, "n": 0},
        "returns": {
            "1M": {"abs": None, "topix_relative": None},
            "3M": {"abs": None, "topix_relative": None},
        },
        "sector_relative": {
            "sector": None,
            "n_peer_codes": 0,
            "windows": {
                "1M": {"value": None, "median": None, "n": 0},
                "3M": {"value": None, "median": None, "n": 0},
            },
        },
        "since_earnings_return": {"value": None, "since": None},
        "volume_ratio": {"5_20": None, "5_60": None},
        "next_earnings_date": {"date": None, "source": "推定不能"},
        "omitted": [
            {"item": "PBR / EV/EBITDA", "reason": "純資産・有利子負債を取得していないため(恒久)"},
            {"item": "PER の5年レンジ", "reason": "J-Quants Free が2年しか返さないため 2.3年レンジで代用"},
            {"item": "月次", "reason": "手入力待ち"},
        ],
        "data_freshness": {
            "price": {"path": "data/prices_long/9999.parquet", "latest": None},
            "raw": {"path": "data/deepdive/raw/9999.jsonl", "latest_disc_date": None},
            "benchmark": {"path": "data/prices_asset/jp_1306.parquet", "latest": None},
        },
    }


def test_render_full_smoke():
    text = sheet.render(_full_a_layer())
    assert text.startswith("# 7134 テスト商事 — 2026Q1 準備シート")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_render_full_progress_section():
    text = sheet.render(_full_a_layer())
    assert "進捗率 +48.0%" in text
    assert "1Q時点、YTD 480,000,000 / 計画 1,000,000,000" in text
    assert "過去同時点との差分: +1.5% pt (n=2) (参考値: n少)" in text


def test_render_full_guidance_and_revision():
    text = sheet.render(_full_a_layer())
    assert "期初予想→着地 乖離率: 中央値 +4.2% (n=3)" in text
    assert "期中修正" in text and "2回" in text and "上方" in text


def test_render_full_per_section():
    text = sheet.render(_full_a_layer())
    assert "PER 15.23倍" in text
    assert "62%タイル" in text
    assert "2024-08-25〜2026-08-22" in text
    assert "n=480日" in text


def test_render_full_price_action():
    text = sheet.render(_full_a_layer())
    assert "1M騰落率: +3.2%(TOPIX比・1306で代用 +1.1%)" in text
    assert "情報・通信業" in text
    assert "母集団 n=5" in text
    assert "同業中央値比 +0.8% pt" in text


def test_render_full_since_earnings_and_volume():
    text = sheet.render(_full_a_layer())
    assert "前回決算発表日(2026-05-14)からの騰落率: +6.7%" in text
    assert "5日/20日 1.23倍" in text
    assert "5日/60日 0.98倍" in text


def test_render_full_earnings_date():
    text = sheet.render(_full_a_layer())
    assert "## 次回決算発表予定" in text
    assert "2026-11-13(出典: calendar)" in text


def test_render_full_mandatory_footer_sections_present():
    text = sheet.render(_full_a_layer())
    assert "## この期に出せなかったもの" in text
    assert "PBR / EV/EBITDA: 純資産・有利子負債を取得していないため(恒久)" in text
    assert "## 使ったデータの鮮度" in text
    assert "株価: data/prices_long/7134.parquet 2026-08-22 まで" in text
    assert "財務: data/deepdive/raw/7134.jsonl 最終開示 2026-05-14(J-Quants Free は12週遅延)" in text
    assert "ベンチマーク: data/prices_asset/jp_1306.parquet 2026-08-22 まで" in text
    assert "TOPIX指数^TPXが実質取得不能なため、TOPIX連動ETF・配当込みの1306で代用" in text


def test_render_empty_does_not_crash():
    text = sheet.render(_empty_a_layer())
    assert text.startswith("# 9999")
    assert "データ不足で算出不能" in text


def test_render_empty_progress_fallback():
    text = sheet.render(_empty_a_layer())
    assert "売上: データ不足で算出不能" in text
    assert "営業利益: データ不足で算出不能" in text
    assert "期初予想→着地 乖離率: データ不足で算出不能" in text


def test_render_empty_guidance_and_revision_fallback():
    text = sheet.render(_empty_a_layer())
    assert "期初予想→着地 乖離率: データ不足で算出不能" in text
    assert "期中修正: データ不足で算出不能" in text


def test_render_empty_per_fallback():
    text = sheet.render(_empty_a_layer())
    assert "PER: データ不足で算出不能" in text


def test_render_empty_sector_and_earnings_date_fallback():
    text = sheet.render(_empty_a_layer())
    assert "同業比: 業種不明のため算出不能" in text
    assert "不明(出典: 推定不能)" in text


def test_render_empty_freshness_fallback():
    text = sheet.render(_empty_a_layer())
    assert "株価: data/prices_long/9999.parquet(日付取得不能)" in text
    assert "財務: data/deepdive/raw/9999.jsonl(データなし。fetch 未実行の可能性)" in text
    assert "ベンチマーク: data/prices_asset/jp_1306.parquet(日付取得不能)" in text


def test_render_freshness_omits_benchmark_line_when_key_absent():
    a = _full_a_layer()
    del a["data_freshness"]["benchmark"]
    text = sheet.render(a)
    assert "ベンチマーク:" not in text
