"""VCP funnel 診断: テンプレ通過→P1→VCP(origin/V1-V7)のどこで銘柄が落ちるか内訳を出す。

しきい値チューニングの効果測定に再利用する常設ツール。ローカルの直近バッチ後
キャッシュ(data/prices/*.parquet)を直読みし、ネットワーク取得なしで pipeline の
funnel を再現する。

VCPしきい値の3バリアントを同一データで比較できる:
  - baseline : マージ/cap/除外 いずれも無効(旧挙動)
  - +b       : min_contraction_depth のみ有効(包絡保存マージ)
  - +b+d     : +b に加え swing_th_cap と atr_exclude_threshold(TOO_VOLATILE)を有効

実行:  python3 scripts/diag_vcp_funnel.py
"""
from __future__ import annotations

import copy
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config
from src.data import prices as prices_mod
from src.indicators import compute_all, rs_percentile_rank
from src.screener import trend_template, vcp as vcp_mod
from src.screener.priority import evaluate_priority
from src.universe import load_universe


# バリアント定義: config["vcp"] に上書きするキーだけを列挙する。
VARIANTS = {
    "baseline": {
        "min_contraction_depth": 0.0,
        "swing_th_cap": None,
        "atr_exclude_threshold": None,
    },
    "+b": {
        "min_contraction_depth": 0.02,
        "swing_th_cap": None,
        "atr_exclude_threshold": None,
    },
    "+b+d": {
        "min_contraction_depth": 0.02,
        "swing_th_cap": 0.08,
        "atr_exclude_threshold": 0.09,
    },
}

V_KEYS = ("V1", "V2", "V3", "V4", "V5", "V6", "V7")
V_LABEL = {
    "V1": "V1(収縮回数 2-6)",
    "V2": "V2(単調タイト化)",
    "V3": "V3(初回深さ≤0.35)",
    "V4": "V4(最終深さ≤0.12)",
    "V5": "V5(出来高枯れ)",
    "V6": "V6(ベース期間15-200日)",
    "V7": "V7(安値切り下げ無し)",
}
ORIGIN_KEYS = ("ok", "IMMATURE", "TOO_RECENT", "TOO_VOLATILE", "NO_BASE")
STATUS_KEYS = ("WATCH_A", "WATCH_B", "REJECTED")


def load_offline():
    config = load_config()
    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    name_by_code = {s["code"]: s["name"] for s in universe["stocks"]}

    frames = {}
    for c in codes:
        df = prices_mod.load_cache(c)
        if df is not None:
            frames[c] = df

    bench_code = config["data"]["topix_proxy_ticker"].split(".")[0]
    bench_df = prices_mod.load_cache(bench_code)
    if bench_df is None:
        raise SystemExit(f"benchmark cache {bench_code} not found")
    benchmark_close = prices_mod.drop_benchmark_outliers(
        bench_df.set_index("date")["close"]
    )

    indicator_by_code = {c: compute_all(df, benchmark_close) for c, df in frames.items()}
    rs_by_code = rs_percentile_rank(
        {c: df.iloc[-1]["rs_raw"] for c, df in indicator_by_code.items()}
    )

    latest_by_code = {}
    for c, df in indicator_by_code.items():
        rs = rs_by_code.get(c)
        if rs is None:
            continue
        latest = df.iloc[-1].to_dict()
        latest["rs"] = rs
        latest_by_code[c] = latest

    return config, name_by_code, indicator_by_code, latest_by_code


def make_variant_config(base_config: dict, overrides: dict) -> dict:
    cfg = copy.deepcopy(base_config)
    cfg["vcp"].update(overrides)
    return cfg


def vcp_breakdown(codes, indicator_by_code, config):
    """codes に対して evaluate_vcp を回し集計を返す。"""
    origin_dist = Counter()
    v_fail = Counter()
    status_dist = Counter()
    rejected_reason = Counter()
    per_code = {}

    for c in codes:
        res = vcp_mod.evaluate_vcp(indicator_by_code[c], config)
        per_code[c] = res
        status = res["status"]
        flags = res.get("must_flags")
        if flags is None:
            # origin 早期リターン (NO_BASE/TOO_RECENT/IMMATURE/TOO_VOLATILE)
            origin_dist[status] += 1
            continue
        origin_dist["ok"] += 1
        for vk in V_KEYS:
            if not flags[vk]:
                v_fail[vk] += 1
        status_dist[status] += 1
        if status == "REJECTED":
            bad_v3 = not flags["V3"]
            bad_v7 = not flags["V7"]
            if bad_v3 and bad_v7:
                rejected_reason["!V3 & !V7"] += 1
            elif bad_v3:
                rejected_reason["!V3のみ"] += 1
            elif bad_v7:
                rejected_reason["!V7のみ"] += 1
            else:
                rejected_reason["V3/V7は真だが不合格(WATCH_B条件未達)"] += 1

    return {
        "origin_dist": origin_dist,
        "v_fail": v_fail,
        "status_dist": status_dist,
        "rejected_reason": rejected_reason,
        "per_code": per_code,
    }


def print_variant_comparison(title, n, bd_by_variant):
    print(f"\n=== {title} (対象 {n}銘柄) ===")
    names = list(bd_by_variant.keys())
    col = "  {:26s}" + "{:>10s}" * len(names)
    row = "  {:26s}" + "{:>10d}" * len(names)

    print("[origin ステータス分布]")
    print(col.format("", *names))
    for k in ORIGIN_KEYS:
        vals = [bd_by_variant[v]["origin_dist"].get(k, 0) for v in names]
        print(row.format(k, *vals))

    print("[origin=ok の V1-V7 不合格件数(延べ)]")
    print(col.format("", *names))
    for vk in V_KEYS:
        vals = [bd_by_variant[v]["v_fail"].get(vk, 0) for v in names]
        print(row.format(V_LABEL[vk], *vals))

    print("[vcp_status 分布(origin=ok内)]")
    print(col.format("", *names))
    for k in STATUS_KEYS:
        vals = [bd_by_variant[v]["status_dist"].get(k, 0) for v in names]
        print(row.format(k, *vals))
    actionable = [
        bd_by_variant[v]["status_dist"].get("WATCH_A", 0)
        + bd_by_variant[v]["status_dist"].get("WATCH_B", 0)
        for v in names
    ]
    print(row.format("→ WATCH_A/B(actionable)", *actionable))


def print_v1_samples(codes, name_by_code, indicator_by_code, bd, config):
    print("\n" + "=" * 60)
    print("(D) V1不合格サンプル(origin=ok・V1偽) — ZigZag実態[+b+d]")
    print("=" * 60)
    shown = 0
    for c in codes:
        res = bd["per_code"][c]
        flags = res.get("must_flags")
        if flags is None or flags["V1"]:
            continue
        latest = indicator_by_code[c].iloc[-1].to_dict()
        th = vcp_mod.zigzag_swing_threshold(latest, config)
        atr_ratio = latest["atr20"] / latest["close"] * 100
        contractions = res.get("contractions", [])
        print(f"\n  {c} {name_by_code.get(c, '')}  base_days={res.get('base_days')} "
              f"t0={res.get('t0_date')}  収縮数={len(contractions)}")
        print(f"    zigzag閾値={th*100:.2f}% (ATR/close={atr_ratio:.2f}%)")
        depths = "/".join(f"{cc['depth']*100:.0f}%" for cc in contractions)
        print(f"    収縮深さ列: {depths or '(なし)'}")
        shown += 1
        if shown >= 6:
            break
    if shown == 0:
        print("  該当なし(V1不合格のorigin=ok銘柄が存在しない)")


def main():
    config, name_by_code, indicator_by_code, latest_by_code = load_offline()

    tt_results = trend_template.screen_universe(latest_by_code, config)
    template_pass = [r["code"] for r in tt_results if r["passed"]]

    priority_by_code = {}
    for c, latest in latest_by_code.items():
        ev = evaluate_priority(latest, config)
        if ev is not None:
            priority_by_code[c] = ev

    # ---- (A) Funnel ----
    print("=" * 60)
    print("(A) Funnel")
    print("=" * 60)
    print(f"universe(RS算出可): {len(latest_by_code)}件")
    print(f" └ テンプレ通過(8条件): {len(template_pass)}件")

    pr_of_tp = Counter()
    hardfilter_drop = 0
    for c in template_pass:
        ev = priority_by_code.get(c)
        if ev is None:
            hardfilter_drop += 1
        else:
            pr_of_tp[ev["priority"]] += 1
    print("     └ テンプレ通過銘柄の priority 内訳:")
    for p in (1, 2, 3, 4):
        print(f"         P{p}: {pr_of_tp.get(p, 0)}件")
    print(f"         ハードフィルタ落ち(None): {hardfilter_drop}件")

    p1_all = [c for c, ev in priority_by_code.items() if ev["priority"] == 1]
    print(f" (参考) 全universe中 P1: {len(p1_all)}件")

    # ---- バリアント別 VCP breakdown ----
    variant_configs = {name: make_variant_config(config, ov) for name, ov in VARIANTS.items()}

    bd_tp = {name: vcp_breakdown(template_pass, indicator_by_code, cfg)
             for name, cfg in variant_configs.items()}
    print_variant_comparison("(B) VCP: テンプレ通過 全銘柄 バリアント比較", len(template_pass), bd_tp)

    bd_p1 = {name: vcp_breakdown(p1_all, indicator_by_code, cfg)
             for name, cfg in variant_configs.items()}
    print_variant_comparison("(C) VCP: P1銘柄のみ(本番の実評価対象) バリアント比較", len(p1_all), bd_p1)

    # ---- (D) V1不合格サンプル(+b+d) ----
    print_v1_samples(template_pass, name_by_code, indicator_by_code,
                     bd_tp["+b+d"], variant_configs["+b+d"])


if __name__ == "__main__":
    main()
