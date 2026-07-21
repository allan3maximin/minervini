"""Manual-CSV fundamentals loader, validation, tier determination, and staleness
guard (design doc section 1.3).

Sources: the auto-fetch store (data/fundamentals_auto.json — populated by an
external fetcher when one is wired up) merged with the human-maintained
manual/fundamentals.csv via merge_fundamentals (manual rows win per quarter).
Absence of any row is not an error, just "pool" tier.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.screener.trend_template import (
    compute_accel_slope,
    compute_full_score,
    latest_yoy_growth,
    quarter_sort_key,
    technical_score,
)

DEFAULT_CSV_PATH = REPO_ROOT / "manual" / "fundamentals.csv"
AUTO_PATH = REPO_ROOT / "data" / "fundamentals_auto.json"
# Public, Pages-served mirror of the merged (auto + manual) fundamentals data.
# data/fundamentals_auto.json lives outside docs/ so GitHub Pages never serves
# it; the dashboard's fundamentals input modal needs *some* pre-fetched
# baseline to prefill from, so we write a trimmed copy here every run.
PUBLIC_JSON_PATH = REPO_ROOT / "docs" / "data" / "fundamentals_public.json"
CSV_COLUMNS = ["code", "fiscal_quarter", "eps", "revenue", "monthly_yoy", "checked_date"]
_QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")


def load_auto_store(path=None) -> dict:
    """data/fundamentals_auto.json を読む。無い・壊れている場合は空dict
    (=手動CSVのみで動作。破損時はwarningをprintして空から再構築)。"""
    from src.utils_io import safe_load_json
    return safe_load_json(path or AUTO_PATH, {})


def load_fundamentals_csv(path=None) -> tuple[pd.DataFrame, list[str]]:
    """Parse and validate manual/fundamentals.csv.

    Malformed rows (bad code/fiscal_quarter format) and duplicate
    (code, fiscal_quarter) pairs are skipped with a warning rather than
    failing the job (design doc 1.3).
    """
    path = path or DEFAULT_CSV_PATH
    warnings: list[str] = []
    empty = pd.DataFrame(columns=CSV_COLUMNS)

    if not path.exists():
        return empty, warnings

    raw = pd.read_csv(path, dtype={"code": str})
    if raw.empty:
        return empty, warnings

    valid_rows = []
    seen: set[tuple[str, str]] = set()
    for i, row in raw.iterrows():
        line_no = i + 2  # header is line 1
        code = str(row.get("code", "")).strip()
        fq = str(row.get("fiscal_quarter", "")).strip()

        if not code or code == "nan" or not _QUARTER_RE.match(fq):
            warnings.append(f"行{line_no}: 不正な形式のためスキップ (code={code!r}, fiscal_quarter={fq!r})")
            continue

        key = (code, fq)
        if key in seen:
            warnings.append(f"行{line_no}: 四半期重複のためスキップ (code={code}, fiscal_quarter={fq})")
            continue
        seen.add(key)
        valid_rows.append(dict(row))

    if not valid_rows:
        return empty, warnings

    df = pd.DataFrame(valid_rows)
    df["code"] = df["code"].astype(str)
    for col in ("eps", "revenue", "monthly_yoy"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df, warnings


def build_fundamentals_by_code(df: pd.DataFrame) -> dict[str, dict]:
    """Group parsed CSV rows by code into {quarters, monthly_yoy, checked_date}."""
    result: dict[str, dict] = {}
    if df.empty:
        return result

    for code, group in df.groupby("code"):
        quarters = [
            {k: (None if pd.isna(v) else v) for k, v in row.items() if k != "code"}
            for row in group.to_dict("records")
        ]
        quarters_sorted = sorted(quarters, key=lambda q: quarter_sort_key(q["fiscal_quarter"]))
        latest = quarters_sorted[-1]
        result[code] = {
            "quarters": quarters,
            "monthly_yoy": latest.get("monthly_yoy"),
            "checked_date": latest.get("checked_date"),
        }
    return result


MISMATCH_REL_THRESHOLD = 0.20  # auto/tanshin間の乖離警告閾値(相対20%超)


def _relative_mismatch(a: float | None, b: float | None, threshold: float) -> bool:
    """2値の相対乖離が threshold を超えるか。None・分母ゼロは比較不能=False。"""
    if a is None or b is None:
        return False
    denom = max(abs(a), abs(b))
    if denom == 0:
        return False
    return abs(a - b) / denom > threshold


def merge_fundamentals(auto_by_code: dict, manual_by_code: dict,
                       tanshin_by_code: dict | None = None,
                       warnings_out: list | None = None) -> dict:
    """自動取得ストア(J-Quants) + EDINET DB決算短信補完 + 手動CSVを統合する。

    優先度: manual > auto(jquants) > tanshin(edinetdb) -- 同一(code,
    fiscal_quarter)は投入順(tanshin -> auto -> manual)で後勝ちさせるだけ。
    J-Quantsの遅延が追いつけば同ラベルを自動的に上書きするので、tanshin値は
    暫定速報の扱いになる(DESIGN_EDINETDB.md 0節)。
    monthly_yoy は従来どおり手動のみ。checked_date は manual があれば manual、
    無ければ auto と tanshin の新しい方 (ISO日付文字列は辞書順比較で正しく比較可)。

    warnings_out (2026-07-17追加): list を渡すと、auto と tanshin の両方が同一
    (code, fiscal_quarter) を持ち eps または revenue の相対乖離が
    MISMATCH_REL_THRESHOLD(20%)を超える場合に警告文字列を追加する。従来は
    黙って上書きされソース間の食い違いが検知不能だった。マージ結果自体は
    従来と同一(後方互換)。
    """
    tanshin_by_code = tanshin_by_code or {}
    result: dict[str, dict] = {}
    for code in set(auto_by_code) | set(manual_by_code) | set(tanshin_by_code):
        tanshin = tanshin_by_code.get(code) or {}
        auto = auto_by_code.get(code) or {}
        manual = manual_by_code.get(code) or {}

        if warnings_out is not None:
            tanshin_by_label = {
                q.get("fiscal_quarter"): q for q in tanshin.get("quarters", [])
                if q.get("fiscal_quarter")
            }
            for q in auto.get("quarters", []):
                fq = q.get("fiscal_quarter")
                t = tanshin_by_label.get(fq)
                if t is None:
                    continue
                for key in ("eps", "revenue"):
                    a_val, t_val = q.get(key), t.get(key)
                    if _relative_mismatch(a_val, t_val, MISMATCH_REL_THRESHOLD):
                        warnings_out.append(
                            f"{code} {fq} {key}: jquants={a_val} と edinetdb={t_val} "
                            f"が20%超乖離 (jquants値を採用)"
                        )

        def _slim(q: dict) -> dict:
            # eps/revenueに加え、決算開示日(disc_date)があればチャートの決算
            # マーカー用に持ち越す。無い場合はキー自体を付けない(後方互換のため
            # 従来の3キー辞書と一致させる)。
            r = {"fiscal_quarter": q.get("fiscal_quarter"), "eps": q.get("eps"), "revenue": q.get("revenue")}
            dd = q.get("disc_date")
            if dd:
                r["disc_date"] = dd
            return r

        by_label: dict[str, dict] = {}
        for q in tanshin.get("quarters", []):
            fq = q.get("fiscal_quarter")
            if fq:
                by_label[fq] = _slim(q)
        for q in auto.get("quarters", []):
            fq = q.get("fiscal_quarter")
            if fq:
                by_label[fq] = _slim(q)
        for q in manual.get("quarters", []):
            fq = q.get("fiscal_quarter")
            if fq:
                by_label[fq] = dict(q)  # manual wins

        quarters = sorted(by_label.values(), key=lambda q: quarter_sort_key(q["fiscal_quarter"]))
        checked_date = manual.get("checked_date") or max(
            (d for d in (auto.get("checked_date"), tanshin.get("checked_date")) if d), default=None
        )
        result[code] = {
            "quarters": quarters,
            "monthly_yoy": manual.get("monthly_yoy") if manual else None,
            "checked_date": checked_date,
            # 会社予想(ガイダンス)はJ-Quants自動取得のみが持つ(2026-07-12追加)。
            "guidance": auto.get("guidance"),
        }
    return result


def write_public_json(fundamentals_by_code: dict, path=None) -> None:
    """Write a trimmed, Pages-servable copy of the merged fundamentals data.

    Only codes with at least one quarter are included (skips empty/None
    entries) so the file doesn't balloon with universe-wide null rows. This
    is what docs/assets/fundamentals-modal.js fetches to prefill the
    "ファンダ入力/編集" form with whatever the J-Quants batch (or a prior
    manual commit) already found -- without it, the input modal only ever
    saw manual/fundamentals.csv and appeared blank even after a successful
    auto-fetch.
    """
    path = path or PUBLIC_JSON_PATH
    out: dict[str, dict] = {}
    for code, data in fundamentals_by_code.items():
        quarters = data.get("quarters") or []
        if not quarters:
            continue
        out[code] = {
            "quarters": [
                {
                    "fiscal_quarter": q.get("fiscal_quarter"),
                    "eps": q.get("eps"),
                    "revenue": q.get("revenue"),
                }
                for q in quarters
            ],
            "monthly_yoy": data.get("monthly_yoy"),
            "checked_date": data.get("checked_date"),
            "guidance": data.get("guidance"),
        }
    from src.report.secure_io import write_docs_json
    write_docs_json(path, out, indent=1)


def fund_coverage_tier(code: str, fundamentals_by_code: dict, config: dict | None = None) -> dict:
    """Coverage ("full"=EPS acceleration computable / "partial"=rows exist but
    not enough / "none"=no rows) + strength-based tier determination.

    〔本命〕(confirmed) はデータの存在だけでは不十分で、Minervini Code 33 準拠の
    強度基準を要求する: 直近EPS YoY >= confirmed_eps_yoy_min かつ 売上YoY >=
    confirmed_rev_yoy_min (config.yaml fundamentals節)。YoYが計算不能(前年の
    比較対象なし・前年値<=0)な場合は強度未確認として pool 止まり。

    J-Quants自動取得で全銘柄に quarters が入るようになり「データあり=confirmed」
    では減益銘柄まで〔本命〕に上がってしまったための改定 (2026-07-09)。
    """
    config = config or load_config()
    data = fundamentals_by_code.get(code)
    if not data or not data.get("quarters"):
        return {"fund_coverage": "none", "tier": "pool", "fund_strong": None,
                "fund_eps_yoy": None, "fund_rev_yoy": None}

    quarters = data["quarters"]
    eps_slope = compute_accel_slope(quarters, "eps")
    coverage = "full" if eps_slope is not None else "partial"

    fcfg = config["fundamentals"]
    eps_yoy = latest_yoy_growth(quarters, "eps")
    rev_yoy = latest_yoy_growth(quarters, "revenue")
    strong = (
        eps_yoy is not None and rev_yoy is not None
        and eps_yoy >= fcfg["confirmed_eps_yoy_min"]
        and rev_yoy >= fcfg["confirmed_rev_yoy_min"]
    )
    return {
        "fund_coverage": coverage,
        "tier": "confirmed" if strong else "pool",
        "fund_strong": strong,
        "fund_eps_yoy": round(eps_yoy, 1) if eps_yoy is not None else None,
        "fund_rev_yoy": round(rev_yoy, 1) if rev_yoy is not None else None,
    }


def compute_fund_stale(checked_date: str | None, today: date, config: dict | None = None) -> bool:
    config = config or load_config()
    if not checked_date:
        return False
    stale_days = config["fundamentals"]["stale_days"]
    checked = pd.to_datetime(checked_date).date()
    return (today - checked).days > stale_days


def get_fundamentals_for_code(
    code: str, fundamentals_by_code: dict, today: date | None = None, config: dict | None = None
) -> dict:
    config = config or load_config()
    today = today or datetime.now().date()
    tier_info = fund_coverage_tier(code, fundamentals_by_code, config)
    data = fundamentals_by_code.get(code)

    if not data:
        return {**tier_info, "fund_stale": False, "fund_checked_date": None, "monthly_yoy": None, "quarters": []}

    return {
        **tier_info,
        "fund_stale": compute_fund_stale(data.get("checked_date"), today, config),
        "fund_checked_date": data.get("checked_date"),
        "monthly_yoy": data.get("monthly_yoy"),
        "quarters": data["quarters"],
    }


def score_stock(
    code: str,
    latest_row: dict,
    fundamentals_by_code: dict,
    today: date | None = None,
    config: dict | None = None,
) -> dict:
    """The two-axis scoring tie-in (design doc 3.2): every stock that passes
    the trend template gets a tech_score (pool-tier ranking key); stocks with
    a "confirmed" fundamentals tier additionally get a full_score (used to
    rank the confirmed tier instead)."""
    config = config or load_config()
    info = get_fundamentals_for_code(code, fundamentals_by_code, today, config)

    result = {
        "tech_score": technical_score(latest_row, config),
        "tier": info["tier"],
        "fund_coverage": info["fund_coverage"],
        "fund_strong": info.get("fund_strong"),
        "fund_eps_yoy": info.get("fund_eps_yoy"),
        "fund_rev_yoy": info.get("fund_rev_yoy"),
        "fund_stale": info["fund_stale"],
        "fund_checked_date": info["fund_checked_date"],
        "full_score": None,
        "eps_accel_slope": None,
        "rev_accel_slope": None,
    }

    # full_score/加速slopeはファンダデータがあれば tier に関係なく計算する
    # (強度基準未達で pool に落ちた銘柄でも個別株画面・コピー機能で見たいため)。
    # confirmed のランキングにだけ full_score が使われる点は従来どおり
    # (build_site.assemble_stock_record 側の分岐)。
    if info["fund_coverage"] != "none":
        full = compute_full_score(
            latest_row,
            eps_quarters=info["quarters"],
            revenue_quarters=info["quarters"],
            monthly_yoy=info.get("monthly_yoy"),
            config=config,
        )
        result["full_score"] = full["full_score"]
        result["eps_accel_slope"] = full["eps_accel_slope"]
        result["rev_accel_slope"] = full["rev_accel_slope"]

    return result
