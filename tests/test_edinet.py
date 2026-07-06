"""Tests for EDINET auto-fundamentals (src/data/edinet.py) and the
auto+manual merge (fundamentals.merge_fundamentals)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date

import pytest

from src.data import edinet
from src.data.fundamentals import merge_fundamentals

CSV_HEADER = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]


def _make_zip(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t")
    writer.writerow(CSV_HEADER)
    writer.writerows(rows)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("XBRL_TO_CSV/jpcrp040300-q2r-001_E00000-000.csv", buf.getvalue().encode("utf-16"))
    return zbuf.getvalue()


class TestQuarterLabel:
    def test_q1_ytd(self):
        assert edinet.quarter_label(date(2025, 4, 1), date(2025, 6, 30)) == ("2025Q1", 1)

    def test_half_year(self):
        assert edinet.quarter_label(date(2025, 4, 1), date(2025, 9, 30)) == ("2025Q2", 2)

    def test_full_year(self):
        assert edinet.quarter_label(date(2024, 4, 1), date(2025, 3, 31)) == ("2024Q4", 4)

    def test_calendar_year_company(self):
        assert edinet.quarter_label(date(2025, 1, 1), date(2025, 12, 31)) == ("2025Q4", 4)

    def test_bogus_period(self):
        assert edinet.quarter_label(date(2025, 4, 1), date(2027, 3, 31)) is None


class TestExtract:
    def test_extract_consolidated_ytd(self):
        z = _make_zip(
            [
                ["jpcrp_cor:BasicEarningsLossPerShare", "1株当たり利益", "CurrentYTDDuration", "当期", "連結", "期間", "u1", "円", "123.45"],
                ["jppfs_cor:NetSales", "売上高", "CurrentYTDDuration", "当期", "連結", "期間", "u2", "円", "1000000"],
                # 前期の値は拾わないこと
                ["jppfs_cor:NetSales", "売上高", "Prior1YTDDuration", "前期", "連結", "期間", "u2", "円", "999"],
            ]
        )
        point = edinet.extract_ytd_point(z)
        assert point == {"eps": 123.45, "revenue": 1000000.0}

    def test_fallback_to_nonconsolidated(self):
        z = _make_zip(
            [
                ["jppfs_cor:NetSales", "売上高", "CurrentYearDuration_NonConsolidatedMember", "当期", "個別", "期間", "u2", "円", "500"],
            ]
        )
        point = edinet.extract_ytd_point(z)
        assert point["revenue"] == 500.0
        assert point["eps"] is None

    def test_missing_values(self):
        z = _make_zip(
            [
                ["jppfs_cor:NetSales", "売上高", "CurrentYTDDuration", "当期", "連結", "期間", "u2", "円", "－"],
            ]
        )
        point = edinet.extract_ytd_point(z)
        assert point == {"eps": None, "revenue": None}


class TestDeriveQuarters:
    def test_ytd_diff_within_fiscal_year(self):
        points = [
            {"fy_start": "2023-04-01", "n": 1, "label": "2023Q1", "eps": 10.0, "revenue": 100.0},
            {"fy_start": "2023-04-01", "n": 2, "label": "2023Q2", "eps": 25.0, "revenue": 220.0},
            {"fy_start": "2023-04-01", "n": 4, "label": "2023Q4", "eps": 60.0, "revenue": 500.0},
        ]
        out = edinet.derive_quarters(points)
        by_label = {q["fiscal_quarter"]: q for q in out}
        assert by_label["2023Q1"] == {"fiscal_quarter": "2023Q1", "eps": 10.0, "revenue": 100.0}
        assert by_label["2023Q2"] == {"fiscal_quarter": "2023Q2", "eps": 15.0, "revenue": 120.0}
        # Q3欠落(半期報告書体制)でも Q4 = 通期 - 上期 のスパン値になる
        assert by_label["2023Q4"] == {"fiscal_quarter": "2023Q4", "eps": 35.0, "revenue": 280.0}

    def test_post_2024_half_year_regime(self):
        points = [
            {"fy_start": "2024-04-01", "n": 2, "label": "2024Q2", "eps": 30.0, "revenue": 300.0},
            {"fy_start": "2024-04-01", "n": 4, "label": "2024Q4", "eps": 70.0, "revenue": 650.0},
        ]
        out = edinet.derive_quarters(points)
        by_label = {q["fiscal_quarter"]: q for q in out}
        assert by_label["2024Q2"]["eps"] == 30.0  # 上期そのまま
        assert by_label["2024Q4"]["eps"] == 40.0  # 下期 = 通期 - 上期

    def test_duplicate_quarter_first_wins(self):
        points = [
            {"fy_start": "2024-04-01", "n": 2, "label": "2024Q2", "eps": 30.0, "revenue": 300.0},
            {"fy_start": "2024-04-01", "n": 2, "label": "2024Q2", "eps": 31.0, "revenue": 301.0},
        ]
        out = edinet.derive_quarters(points)
        assert len(out) == 1
        assert out[0]["eps"] == 30.0

    def test_separate_fiscal_years_not_diffed(self):
        points = [
            {"fy_start": "2023-04-01", "n": 4, "label": "2023Q4", "eps": 60.0, "revenue": 500.0},
            {"fy_start": "2024-04-01", "n": 2, "label": "2024Q2", "eps": 30.0, "revenue": 300.0},
        ]
        out = edinet.derive_quarters(points)
        by_label = {q["fiscal_quarter"]: q for q in out}
        assert by_label["2024Q2"]["eps"] == 30.0  # 前年度と差分しない


class TestStore:
    def test_merge_into_store_overwrites_same_label_and_caps(self):
        store = {}
        qs = [{"fiscal_quarter": f"202{i}Q1", "eps": float(i), "revenue": 1.0} for i in range(4)]
        edinet._merge_into_store(store, "7203", qs, "2026-07-01", max_keep=3)
        assert len(store["7203"]["quarters"]) == 3
        edinet._merge_into_store(
            store, "7203", [{"fiscal_quarter": "2023Q1", "eps": 99.0, "revenue": 9.0}], "2026-07-02", max_keep=3
        )
        latest = {q["fiscal_quarter"]: q for q in store["7203"]["quarters"]}
        assert latest["2023Q1"]["eps"] == 99.0
        assert store["7203"]["checked_date"] == "2026-07-02"

    def test_update_without_api_key_returns_existing_store(self, tmp_path, monkeypatch):
        auto_path = tmp_path / "fundamentals_auto.json"
        auto_path.write_text(json.dumps({"7203": {"quarters": [], "checked_date": "2026-01-01"}}), encoding="utf-8")
        monkeypatch.setattr(edinet, "AUTO_PATH", auto_path)
        monkeypatch.setattr(edinet, "STATE_PATH", tmp_path / "state.json")
        monkeypatch.delenv(edinet.API_KEY_ENV, raising=False)
        store = edinet.update_fundamentals_auto(["7203"], config={"edinet": {"enabled": True}})
        assert "7203" in store
        assert not (tmp_path / "state.json").exists()  # ネットワークにも状態にも触れない


class TestMergeFundamentals:
    def test_manual_wins_per_quarter(self):
        auto = {
            "7203": {
                "quarters": [
                    {"fiscal_quarter": "2025Q1", "eps": 10.0, "revenue": 100.0},
                    {"fiscal_quarter": "2025Q2", "eps": 12.0, "revenue": 110.0},
                ],
                "checked_date": "2026-06-01",
            }
        }
        manual = {
            "7203": {
                "quarters": [{"fiscal_quarter": "2025Q2", "eps": 99.0, "revenue": 999.0, "monthly_yoy": 5.0}],
                "monthly_yoy": 5.0,
                "checked_date": "2026-07-01",
            }
        }
        merged = merge_fundamentals(auto, manual)
        by_label = {q["fiscal_quarter"]: q for q in merged["7203"]["quarters"]}
        assert by_label["2025Q1"]["eps"] == 10.0  # autoのみの四半期は残る
        assert by_label["2025Q2"]["eps"] == 99.0  # manualが勝つ
        assert merged["7203"]["monthly_yoy"] == 5.0
        assert merged["7203"]["checked_date"] == "2026-07-01"

    def test_auto_only_code(self):
        auto = {"6758": {"quarters": [{"fiscal_quarter": "2025Q1", "eps": 1.0, "revenue": 10.0}], "checked_date": "2026-06-01"}}
        merged = merge_fundamentals(auto, {})
        assert merged["6758"]["checked_date"] == "2026-06-01"
        assert merged["6758"]["monthly_yoy"] is None
        assert len(merged["6758"]["quarters"]) == 1

    def test_manual_only_code(self):
        manual = {"9984": {"quarters": [{"fiscal_quarter": "2025Q1", "eps": 2.0, "revenue": 20.0}], "monthly_yoy": None, "checked_date": "2026-05-01"}}
        merged = merge_fundamentals({}, manual)
        assert merged["9984"]["quarters"][0]["eps"] == 2.0
