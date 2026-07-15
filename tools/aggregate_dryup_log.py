#!/usr/bin/env python3
"""本番フォワード検証: data/dryup_log.jsonl を90日窓で集計する。

米株由来の暫定閾値(枯れ度中央値・タイトネス等)が東証で予測力を持つかを、
バックテスト(src/backtest.py)とは独立に本番ログで検証するための集計スクリプト。
バケット定義はバックテスト §2 と揃える(median版/average版/tightness/shakeout/合成)。

使い方:
    python tools/aggregate_dryup_log.py                # 直近90日、data/dryup_log.jsonl
    python tools/aggregate_dryup_log.py --days 180
    python tools/aggregate_dryup_log.py --path path/to/log.jsonl

§6 の多重比較ディシプリン: n<10 のバケットは「参考値」と明示し、結論には使わない。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.report import dryup_log as dryup_log_mod  # noqa: E402

MIN_BUCKET_N = 10
TERMINAL = dryup_log_mod.TERMINAL_OUTCOMES
# 「ブレイク到達」= 一度でも pivot を上抜けた(成功/失敗/中間の別を問わない)
REACHED = {"breakout", "breakout_ok", "breakout_failed"}


def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _within_window(record: dict, cutoff: date) -> bool:
    try:
        return _parse_date(record["date"]) >= cutoff
    except (KeyError, ValueError):
        return False


def _bucket_med(v: float | None) -> str | None:
    if v is None:
        return None
    if v < 0.4:
        return "<0.4"
    if v < 0.6:
        return "0.4-0.6"
    if v < 0.8:
        return "0.6-0.8"
    return ">=0.8"


def _bucket_tight(v: float | None) -> str | None:
    if v is None:
        return None
    if v <= 0.05:
        return "<=0.05"
    if v <= 0.08:
        return "0.05-0.08"
    return ">0.08"


def _stats(records: list[dict]) -> dict:
    """バケット内レコードの outcome 分布を集計する。"""
    n = len(records)
    resolved = [r for r in records if r.get("outcome") in TERMINAL]
    n_res = len(resolved)
    reached = sum(1 for r in records if r.get("outcome") in REACHED)
    ok = sum(1 for r in records if r.get("outcome") == "breakout_ok")
    failed = sum(1 for r in records if r.get("outcome") == "breakout_failed")
    broken = sum(1 for r in records if r.get("outcome") == "broken")
    expired = sum(1 for r in records if r.get("outcome") == "expired")
    pending = sum(1 for r in records if r.get("outcome") not in TERMINAL and r.get("outcome") != "breakout")
    return {
        "n": n,
        "n_resolved": n_res,
        "reached_rate": (reached / n) if n else None,
        "ok_rate": (ok / reached) if reached else None,
        "failed_rate": (failed / reached) if reached else None,
        "broken_rate": (broken / n_res) if n_res else None,
        "expired_rate": (expired / n_res) if n_res else None,
        "pending": pending,
    }


def _fmt_pct(v: float | None) -> str:
    return f"{v * 100:.0f}%" if v is not None else "-"


def _render_group(title: str, buckets: dict[str, list[dict]], order: list[str]) -> list[str]:
    lines = [f"### {title}", ""]
    lines.append("| バケット | n | 解決済 | ブレイク到達率 | 成功率(到達比) | 失敗率(到達比) | broken率 | expired率 | 保留 | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for key in order:
        recs = buckets.get(key, [])
        s = _stats(recs)
        note = "参考値(n<10)" if s["n"] < MIN_BUCKET_N else ""
        lines.append(
            f"| {key} | {s['n']} | {s['n_resolved']} | {_fmt_pct(s['reached_rate'])} | "
            f"{_fmt_pct(s['ok_rate'])} | {_fmt_pct(s['failed_rate'])} | {_fmt_pct(s['broken_rate'])} | "
            f"{_fmt_pct(s['expired_rate'])} | {s['pending']} | {note} |"
        )
    lines.append("")
    return lines


def build_report(records: list[dict], days: int) -> str:
    cutoff = date.today() - timedelta(days=days)
    window = [r for r in records if _within_window(r, cutoff)]

    lines: list[str] = []
    lines.append(f"# 枯れ度(DRY-UP)本番フォワード集計 — 直近{days}日")
    lines.append("")
    lines.append(f"- 生成: {date.today().isoformat()}")
    lines.append(f"- 対象レコード: {len(window)} / 全 {len(records)}")
    n_res = sum(1 for r in window if r.get("outcome") in TERMINAL)
    lines.append(f"- うち解決済(terminal): {n_res}、保留(未解決/追跡中): {len(window) - n_res}")
    lines.append("")
    lines.append(
        "「ブレイク到達率」= pivot上抜け割合(n基準)。「成功率/失敗率」= 到達した中での "
        "breakout_ok/breakout_failed。broken/expired率は解決済み基準。n<10 は参考値(§6)。"
    )
    lines.append("")

    # (a) dryup_med_10_50
    med_order = ["<0.4", "0.4-0.6", "0.6-0.8", ">=0.8"]
    med_buckets: dict[str, list[dict]] = {k: [] for k in med_order}
    for r in window:
        b = _bucket_med(r.get("dryup_med_10_50"))
        if b:
            med_buckets[b].append(r)
    lines += _render_group("(a) dryup_med_10_50(中央値版)", med_buckets, med_order)

    # (b) dryup_avg_5_50
    avg_buckets: dict[str, list[dict]] = {k: [] for k in med_order}
    for r in window:
        b = _bucket_med(r.get("dryup_avg_5_50"))
        if b:
            avg_buckets[b].append(r)
    lines += _render_group("(b) dryup_avg_5_50(平均版)", avg_buckets, med_order)

    # (c) tightness_10d
    tight_order = ["<=0.05", "0.05-0.08", ">0.08"]
    tight_buckets: dict[str, list[dict]] = {k: [] for k in tight_order}
    for r in window:
        b = _bucket_tight(r.get("tightness_10d"))
        if b:
            tight_buckets[b].append(r)
    lines += _render_group("(c) tightness_10d", tight_buckets, tight_order)

    # (d) shakeout_detected
    sh_order = ["true", "false"]
    sh_buckets: dict[str, list[dict]] = {k: [] for k in sh_order}
    for r in window:
        sh_buckets["true" if r.get("shakeout_detected") else "false"].append(r)
    lines += _render_group("(d) shakeout_detected", sh_buckets, sh_order)

    # (e) 合成: dryup_med<0.6 かつ tightness<=0.08 vs その他
    comp_order = ["枯れ&タイト", "その他"]
    comp_buckets: dict[str, list[dict]] = {k: [] for k in comp_order}
    for r in window:
        med, tight = r.get("dryup_med_10_50"), r.get("tightness_10d")
        hit = med is not None and tight is not None and med < 0.6 and tight <= 0.08
        comp_buckets["枯れ&タイト" if hit else "その他"].append(r)
    lines += _render_group("(e) 合成(dryup_med<0.6 × tightness<=0.08)", comp_buckets, comp_order)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate dryup_log.jsonl (forward validation)")
    parser.add_argument("--days", type=int, default=90, help="集計窓(日、既定90)")
    parser.add_argument("--path", type=str, default=str(dryup_log_mod.DRYUP_LOG_PATH),
                        help="ログファイルパス")
    parser.add_argument("--out", type=str, default=None, help="出力先(省略時 stdout)")
    args = parser.parse_args()

    records = dryup_log_mod.load_records(Path(args.path))
    report = build_report(records, args.days)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
