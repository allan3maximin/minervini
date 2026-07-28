from datetime import date, timedelta
from io import StringIO

import pandas as pd
import pytest

import json

from src.config import load_config
from src.data.fundamentals import (
    build_fundamentals_by_code,
    compute_fund_stale,
    fund_coverage_tier,
    fund_verdict_and_multiplier,
    get_fundamentals_for_code,
    is_split_artifact_eps,
    load_fundamentals_csv,
    merge_fundamentals,
    score_stock,
    write_public_json,
)


# ---------------------------------------------------------------------------
# is_split_artifact_eps -- 期中株式分割による単四半期EPS導出バグの検出
# ---------------------------------------------------------------------------

def test_split_artifact_detects_real_cases():
    # 6590芝浦 (通期170.28 − 9M 674.72 = -504.44)
    assert is_split_artifact_eps(-504.44, 170.28, 674.72, 1300.0)
    # 8393宮崎銀 (通期167.32逆算 − 9M ≈616.85 = -449.53)
    assert is_split_artifact_eps(-449.53, 167.32, 616.85, 5000.0)


def test_split_artifact_passes_modest_negative():
    # 小幅赤字四半期(9Mの半分未満)は artifact ではない
    assert not is_split_artifact_eps(-5.0, 90.0, 95.0, 1300.0)


def test_split_artifact_requires_positive_annual_and_normal_revenue():
    # 通期が赤字なら単Q赤字は自然 -> artifact扱いしない
    assert not is_split_artifact_eps(-504.44, -10.0, 674.72, 1300.0)
    # revenueまで負なら分割の非対称ではない -> artifact扱いしない
    assert not is_split_artifact_eps(-504.44, 170.28, 674.72, -100.0)
    # 正のEPSは対象外
    assert not is_split_artifact_eps(50.0, 170.28, 120.0, 1300.0)

CONFIG = load_config()

BASELINE_LATEST = {
    "close": 150.0,
    "ma50": 140.0,
    "ma150": 130.0,
    "ma200": 120.0,
    "ma200_slope_days": 30,
    "ma200_slope_21d": 0.05,
    "dryup_med_10_50": 0.8,
    "low_52w": 100.0,
    "high_52w": 160.0,
    "rs": 80,
    # tech_score は当日の断面ランクから出るので、pipeline 同様
    # attach_score_percentiles 済みの状態を模す(単独銘柄なので全成分100)。
    "score_pct": {"ma200_slope": 100.0, "low52w_ratio": 100.0, "dryup": 100.0},
}


def _write_csv(tmp_path, content: str):
    path = tmp_path / "fundamentals.csv"
    path.write_text(content, encoding="utf-8")
    return path


def _quarters_csv_rows(code: str, values: list[float], start_year=2024, start_q=1) -> str:
    rows = []
    year, q = start_year, start_q
    for v in values:
        rows.append(f"{code},{year}Q{q},{v},{v*10},,")
        q += 1
        if q > 4:
            q = 1
            year += 1
    return "\n".join(rows)


def test_load_fundamentals_csv_skips_malformed_and_duplicate_rows(tmp_path):
    content = (
        "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n"
        "7134,2026Q1,45.2,12800,8.5,2026-07-03\n"
        "7134,2026Q1,99.9,99999,1.0,2026-07-01\n"  # duplicate quarter -> skipped
        ",2025Q4,38.1,11900,,\n"  # missing code -> skipped
        "7135,BADQ,10.0,100,,\n"  # bad fiscal_quarter -> skipped
    )
    path = _write_csv(tmp_path, content)
    df, warnings = load_fundamentals_csv(path)

    assert len(df) == 1
    assert df.iloc[0]["code"] == "7134"
    assert len(warnings) == 3


def test_load_fundamentals_csv_missing_file_returns_empty(tmp_path):
    df, warnings = load_fundamentals_csv(tmp_path / "does_not_exist.csv")
    assert df.empty
    assert warnings == []


def test_no_csv_rows_yields_pool_tier():
    fundamentals_by_code = build_fundamentals_by_code(pd.DataFrame(columns=["code", "fiscal_quarter"]))
    tier_info = fund_coverage_tier("9999", fundamentals_by_code)
    assert tier_info["fund_coverage"] == "none"
    assert tier_info["tier"] == "pool"
    assert tier_info["fund_strong"] is None


def test_eight_quarters_strong_growth_yields_confirmed_full_tier(tmp_path):
    # 直近2025Q4 eps=25 vs 前年2024Q4 eps=10 -> EPS YoY +150%(売上=eps*10で同率)。
    # 強度基準(EPS>=+25% かつ 売上>=+20%)を満たすので confirmed。
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "7134", [10, 10, 10, 10, 12, 15, 19, 25]
    )
    path = _write_csv(tmp_path, csv_text)
    df, warnings = load_fundamentals_csv(path)
    assert warnings == []
    fundamentals_by_code = build_fundamentals_by_code(df)

    tier_info = fund_coverage_tier("7134", fundamentals_by_code)
    assert tier_info["fund_coverage"] == "full"
    assert tier_info["tier"] == "confirmed"
    assert tier_info["fund_strong"] is True
    assert tier_info["fund_eps_yoy"] == 150.0
    assert tier_info["fund_rev_yoy"] == 150.0


def test_weak_growth_yields_pool_despite_full_coverage(tmp_path):
    # 減益トレンド(直近2025Q4 eps=5 vs 2024Q4 eps=10 -> -50%)はデータが
    # 揃っていても本命に昇格させない(2026-07-09基準改定の回帰テスト)。
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "8418", [10, 10, 10, 10, 9, 8, 6, 5]
    )
    path = _write_csv(tmp_path, csv_text)
    df, _ = load_fundamentals_csv(path)
    fundamentals_by_code = build_fundamentals_by_code(df)

    tier_info = fund_coverage_tier("8418", fundamentals_by_code)
    assert tier_info["fund_coverage"] == "full"
    assert tier_info["tier"] == "pool"
    assert tier_info["fund_strong"] is False
    assert tier_info["fund_eps_yoy"] == -50.0


def test_few_quarters_unverifiable_strength_yields_pool_partial_tier(tmp_path):
    # 2四半期のみ -> 前年同期比が計算できない -> 強度未確認として pool 止まり
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "7134", [10, 11]
    )
    path = _write_csv(tmp_path, csv_text)
    df, _ = load_fundamentals_csv(path)
    fundamentals_by_code = build_fundamentals_by_code(df)

    tier_info = fund_coverage_tier("7134", fundamentals_by_code)
    assert tier_info["fund_coverage"] == "partial"
    assert tier_info["tier"] == "pool"
    assert tier_info["fund_strong"] is False
    assert tier_info["fund_eps_yoy"] is None


# ---------------------------------------------------------------------------
# サイズ係数レイヤー fund_verdict_and_multiplier (2026-07-22)
# ---------------------------------------------------------------------------


def test_fund_verdict_pass_full_size():
    v = fund_verdict_and_multiplier(30.0, 25.0, CONFIG)
    assert v == {"fund_verdict": "pass", "fund_multiplier": 1.0}


def test_fund_verdict_fail_only_when_yoy_negative():
    # 2026-07-25改定: fail はYoYマイナス(減益/減収)時のみ。
    # EPS減益
    assert fund_verdict_and_multiplier(-29.8, 25.0, CONFIG) == {
        "fund_verdict": "fail", "fund_multiplier": 0.0}
    # 売上減収
    assert fund_verdict_and_multiplier(30.0, -1.0, CONFIG) == {
        "fund_verdict": "fail", "fund_multiplier": 0.0}
    # 片方計算不能でも、計算できる方がマイナスなら fail
    assert fund_verdict_and_multiplier(-10.0, None, CONFIG) == {
        "fund_verdict": "fail", "fund_multiplier": 0.0}


def test_fund_verdict_positive_below_threshold_is_unknown():
    # プラス成長だが confirmed 閾値未満 -> fail ではなく unknown(半サイズ)
    assert fund_verdict_and_multiplier(24.0, 18.0, CONFIG) == {
        "fund_verdict": "unknown", "fund_multiplier": 0.5}
    # 片方だけ閾値以上でも、他方がプラス未満なら strong ではない -> unknown
    assert fund_verdict_and_multiplier(30.0, 5.0, CONFIG) == {
        "fund_verdict": "unknown", "fund_multiplier": 0.5}


def test_fund_verdict_unknown_half_size():
    # 両方計算不能(データ無し等) -> 不明はハーフ(不明≠悪)
    assert fund_verdict_and_multiplier(None, None, CONFIG) == {
        "fund_verdict": "unknown", "fund_multiplier": 0.5}
    # 片方が合格水準・片方が計算不能 -> pass確定ではないので unknown
    assert fund_verdict_and_multiplier(30.0, None, CONFIG) == {
        "fund_verdict": "unknown", "fund_multiplier": 0.5}


def test_fund_verdict_boundary_equals_threshold_is_pass():
    fcfg = CONFIG["fundamentals"]
    v = fund_verdict_and_multiplier(
        fcfg["confirmed_eps_yoy_min"], fcfg["confirmed_rev_yoy_min"], CONFIG)
    assert v["fund_verdict"] == "pass"


def test_score_stock_carries_verdict_and_multiplier(tmp_path):
    # 減益銘柄(fund_strong False)は verdict=fail / multiplier=0 になり、
    # score_stock の結果まで流れる(build_site 経由で report.json に載る前提)。
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "8418", [10, 10, 10, 10, 9, 8, 6, 5]
    )
    path = _write_csv(tmp_path, csv_text)
    df, _ = load_fundamentals_csv(path)
    fundamentals_by_code = build_fundamentals_by_code(df)

    result = score_stock("8418", BASELINE_LATEST, fundamentals_by_code, today=date(2026, 7, 3), config=CONFIG)
    assert result["fund_verdict"] == "fail"
    assert result["fund_multiplier"] == 0.0

    # データ無し銘柄は unknown/0.5
    result_none = score_stock("no_csv_code", BASELINE_LATEST, fundamentals_by_code={}, today=date(2026, 7, 3), config=CONFIG)
    assert result_none["fund_verdict"] == "unknown"
    assert result_none["fund_multiplier"] == 0.5


def test_fund_stale_true_after_120_days():
    today = date(2026, 7, 3)
    old_date = (today - timedelta(days=121)).isoformat()
    recent_date = (today - timedelta(days=100)).isoformat()

    assert compute_fund_stale(old_date, today, CONFIG) is True
    assert compute_fund_stale(recent_date, today, CONFIG) is False
    assert compute_fund_stale(None, today, CONFIG) is False


def test_partial_full_score_renormalizes_to_100(tmp_path):
    csv_text = "code,fiscal_quarter,eps,revenue,monthly_yoy,checked_date\n" + _quarters_csv_rows(
        "7134", [10, 11]
    )
    path = _write_csv(tmp_path, csv_text)
    df, _ = load_fundamentals_csv(path)
    fundamentals_by_code = build_fundamentals_by_code(df)

    result = score_stock("7134", BASELINE_LATEST, fundamentals_by_code, today=date(2026, 7, 3), config=CONFIG)
    # 2四半期のみ: 強度未確認なので tier は pool 落ちだが、full_score は
    # データがある限り計算される(個別株画面・コピー機能用)
    assert result["tier"] == "pool"
    assert result["fund_coverage"] == "partial"
    assert result["full_score"] is not None
    assert 0 <= result["full_score"] <= 100


def test_pool_tier_stock_has_tech_score_but_no_full_score():
    result = score_stock("no_csv_code", BASELINE_LATEST, fundamentals_by_code={}, today=date(2026, 7, 3), config=CONFIG)
    assert result["tier"] == "pool"
    assert result["full_score"] is None
    assert result["tech_score"] is not None


# ---------------------------------------------------------------------------
# merge_fundamentals (3ソース: manual > auto(jquants) > tanshin(edinetdb))
# ---------------------------------------------------------------------------

def test_merge_fundamentals_tanshin_only_quarter_is_included():
    tanshin_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 5.0, "revenue": 500.0}],
            "checked_date": "2026-07-05",
        }
    }
    merged = merge_fundamentals({}, {}, tanshin_by_code=tanshin_by_code)
    assert merged["1111"]["quarters"] == [{"fiscal_quarter": "2026Q1", "eps": 5.0, "revenue": 500.0}]
    assert merged["1111"]["checked_date"] == "2026-07-05"


def test_merge_fundamentals_auto_beats_tanshin_for_same_label():
    auto_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 10.0, "revenue": 1000.0}],
            "checked_date": "2026-07-06",
        }
    }
    tanshin_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 9.0, "revenue": 900.0}],
            "checked_date": "2026-07-05",
        }
    }
    merged = merge_fundamentals(auto_by_code, {}, tanshin_by_code=tanshin_by_code)
    assert merged["1111"]["quarters"] == [{"fiscal_quarter": "2026Q1", "eps": 10.0, "revenue": 1000.0}]


def test_merge_fundamentals_manual_beats_auto_and_tanshin():
    auto_by_code = {
        "1111": {"quarters": [{"fiscal_quarter": "2026Q1", "eps": 10.0, "revenue": 1000.0}],
                 "checked_date": "2026-07-06"}
    }
    tanshin_by_code = {
        "1111": {"quarters": [{"fiscal_quarter": "2026Q1", "eps": 9.0, "revenue": 900.0}],
                 "checked_date": "2026-07-05"}
    }
    manual_by_code = {
        "1111": {"quarters": [{"fiscal_quarter": "2026Q1", "eps": 11.0, "revenue": 1010.0,
                               "monthly_yoy": 3.0, "checked_date": "2026-07-07"}],
                 "monthly_yoy": 3.0, "checked_date": "2026-07-07"}
    }
    merged = merge_fundamentals(auto_by_code, manual_by_code, tanshin_by_code=tanshin_by_code)
    assert merged["1111"]["quarters"][0]["eps"] == 11.0
    assert merged["1111"]["checked_date"] == "2026-07-07"


def test_merge_fundamentals_checked_date_uses_max_of_auto_and_tanshin_when_no_manual():
    auto_by_code = {"1111": {"quarters": [], "checked_date": "2026-07-01"}}
    tanshin_by_code = {"1111": {"quarters": [], "checked_date": "2026-07-05"}}
    merged = merge_fundamentals(auto_by_code, {}, tanshin_by_code=tanshin_by_code)
    assert merged["1111"]["checked_date"] == "2026-07-05"  # tanshinの方が新しい


def test_merge_fundamentals_mismatch_over_20pct_emits_warning():
    auto_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 10.0, "revenue": 1000.0}],
            "checked_date": "2026-07-06",
        }
    }
    tanshin_by_code = {
        "1111": {
            # eps 10.0 vs 5.0 -> 相対乖離50% (>20%)。revenueは一致。
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 5.0, "revenue": 1000.0}],
            "checked_date": "2026-07-05",
        }
    }
    warnings_out: list[str] = []
    merged = merge_fundamentals(auto_by_code, {}, tanshin_by_code=tanshin_by_code,
                                warnings_out=warnings_out)

    assert len(warnings_out) == 1
    assert "1111 2026Q1 eps" in warnings_out[0]
    assert "jquants=10.0" in warnings_out[0]
    # マージ結果は従来どおり auto(jquants) 値が勝つ
    assert merged["1111"]["quarters"][0]["eps"] == 10.0


def test_merge_fundamentals_small_mismatch_emits_no_warning():
    auto_by_code = {
        "1111": {
            # eps 10.0 vs 9.0 -> 相対乖離10% (<20%)
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 10.0, "revenue": 1000.0}],
            "checked_date": "2026-07-06",
        }
    }
    tanshin_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2026Q1", "eps": 9.0, "revenue": 950.0}],
            "checked_date": "2026-07-05",
        }
    }
    warnings_out: list[str] = []
    merge_fundamentals(auto_by_code, {}, tanshin_by_code=tanshin_by_code,
                       warnings_out=warnings_out)
    assert warnings_out == []


def test_write_public_json_includes_only_codes_with_quarters(tmp_path):
    # code "1111" has real J-Quants-sourced quarters and should be exported;
    # code "2222" has an entry but no quarters (e.g. a failed/empty auto
    # fetch) and should be skipped so the dashboard's fundamentals modal
    # doesn't get a code entry with nothing usable to prefill.
    auto_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2025Q1", "eps": 10.5, "revenue": 1000.0}],
            "monthly_yoy": None,
            "checked_date": "2026-06-01",
        },
        "2222": {"quarters": [], "monthly_yoy": None, "checked_date": None},
    }
    merged = merge_fundamentals(auto_by_code, {})

    out_path = tmp_path / "fundamentals_public.json"
    write_public_json(merged, path=out_path)

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["1111"]
    assert data["1111"]["quarters"] == [{"fiscal_quarter": "2025Q1", "eps": 10.5, "revenue": 1000.0}]
    assert data["1111"]["checked_date"] == "2026-06-01"


def test_write_public_json_manual_overrides_take_precedence(tmp_path):
    # merge_fundamentals already applies "manual wins" per quarter; this just
    # confirms write_public_json passes that merged result through as-is
    # rather than re-deriving from the raw auto store.
    auto_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2025Q1", "eps": 10.5, "revenue": 1000.0}],
            "monthly_yoy": None,
            "checked_date": "2026-06-01",
        }
    }
    manual_by_code = {
        "1111": {
            "quarters": [{"fiscal_quarter": "2025Q1", "eps": 11.0, "revenue": 1010.0}],
            "monthly_yoy": 5.0,
            "checked_date": "2026-07-01",
        }
    }
    merged = merge_fundamentals(auto_by_code, manual_by_code)

    out_path = tmp_path / "fundamentals_public.json"
    write_public_json(merged, path=out_path)
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["1111"]["quarters"][0]["eps"] == 11.0
    assert data["1111"]["checked_date"] == "2026-07-01"
