"""V1(収縮数 T)の算出パイプラインを段階ごとに計測する診断スクリプト。

    python -m scripts.diag_v1_count [--days 400] [--step 5] [--limit N]

T は ZigZag → 浅いピボット統合 → 短期収縮統合 → H/L ペアリング の4段で決まる。
どの段が何をしているか、V1([2,6])が何を落としているかを数える。

出力する項目:
  1. T0アンカー: 第1収縮の高値がベース最高値と一致しているか
     (compute_zigzag の先頭ピボット挿入判定のバグ検出用。2026-07-25に修正済み)
  2. merge_shallow_pivots / merge_short_contractions の発火率と減少量
     (減少量の分布が「0日/1日収縮の数」の分布と一致していれば暴走していない)
  3. マージ前後の T 分布と V1 の合格率・不合格の内訳
  4. min_contraction_depth の感度(この値は swing閾値未満だと構造的に不活性になる)
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import Counter

import pandas as pd

from src.backtest import build_rs_by_date, load_universe_frames
from src.config import load_config
from src.screener import trend_template as TT
from src.screener import vcp as V

DEPTH_GRID = (0.02, 0.04, 0.05, 0.06, 0.08)


def iter_bases(frames, rs_by_date, config, rs_min, days, step):
    """トレンドテンプレート+RSを通過し、ベースが成立した (base_df, latest) を返す。"""
    for code, df in frames.items():
        n = len(df)
        rs_series = rs_by_date[code] if code in rs_by_date.columns else pd.Series(dtype="float64")
        for i in range(max(0, n - days), n, step):
            row = df.iloc[i]
            if pd.isna(row.get("ma200")) or pd.isna(row.get("ma50")) or pd.isna(row.get("high_52w")):
                continue
            rs = rs_series.get(row["date"])
            if rs is None or pd.isna(rs) or rs < rs_min:
                continue
            latest = row.to_dict()
            latest["rs"] = rs
            if not TT.passes_trend_template(TT.check_must_conditions(latest, config)):
                continue
            origin = V.find_base_origin(df.iloc[: i + 1], config)
            if origin["status"] != "ok":
                continue
            yield origin["base_df"], latest


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--step", type=int, default=5)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rs-min", type=float, default=None)
    args = p.parse_args()

    config = load_config()
    vcp_cfg = config["vcp"]
    rs_min = args.rs_min if args.rs_min is not None else config["trend_template"]["rs_min"]
    min_bars = vcp_cfg["min_contraction_bars"]
    min_depth = vcp_cfg["min_contraction_depth"]

    frames = load_universe_frames(args.limit)
    rs_by_date = build_rs_by_date(frames)

    total = 0
    anchor_bad = 0
    anchor_gap: list[float] = []
    shallow_fired = 0
    short_fired = 0
    zero_bar = 0
    one_bar = 0
    short_per_base: Counter = Counter()
    reduction: Counter = Counter()
    t_before: Counter = Counter()
    t_after: Counter = Counter()
    v1_fail: Counter = Counter()
    grid = {d: {"v1": 0, "T": Counter()} for d in DEPTH_GRID}

    for base_df, latest in iter_bases(frames, rs_by_date, config, rs_min, args.days, args.step):
        total += 1
        t0_high = float(base_df["high"].iloc[0])
        threshold = V.zigzag_swing_threshold(latest, config)

        raw = V.compute_zigzag(base_df, threshold)
        shallow = V.merge_shallow_pivots(raw, min_depth)
        if len(shallow) != len(raw):
            shallow_fired += 1
        merged = V.merge_short_contractions(shallow, min_bars)
        if len(merged) != len(shallow):
            short_fired += 1

        c_raw = V.extract_contractions(shallow)
        c_out = V.extract_contractions(merged)

        z = sum(1 for c in c_raw if c["low_idx"] - c["high_idx"] == 0)
        o = sum(1 for c in c_raw if c["low_idx"] - c["high_idx"] == 1)
        zero_bar += z
        one_bar += o
        short_per_base[z + o] += 1
        reduction[len(c_raw) - len(c_out)] += 1
        t_before[len(c_raw)] += 1
        t_after[len(c_out)] += 1

        lo, hi = vcp_cfg["contraction_count"]
        if not (lo <= len(c_out) <= hi):
            v1_fail[len(c_out)] += 1

        if not c_out or c_out[0]["high_price"] < t0_high * 0.9995:
            anchor_bad += 1
            if c_out:
                anchor_gap.append((t0_high - c_out[0]["high_price"]) / t0_high)

        for d, acc in grid.items():
            c = V.extract_contractions(
                V.merge_short_contractions(V.merge_shallow_pivots(raw, d), min_bars)
            )
            acc["T"][min(len(c), 9)] += 1
            if lo <= len(c) <= hi:
                acc["v1"] += 1

    if not total:
        print("no bases found")
        return

    def pct(x):
        return f"{x} ({x / total * 100:.1f}%)"

    print(f"銘柄数 {len(frames)} / 直近{args.days}営業日 / スキャン間隔 {args.step}日")
    print(f"ベース成立(status=ok)日数: {total}")
    print()
    print("[1] T0アンカー")
    print(f"  第1収縮の高値がベース最高値でない: {pct(anchor_bad)}")
    if anchor_gap:
        print(f"    乖離幅 中央値 {st.median(anchor_gap) * 100:.2f}% / "
              f"p90 {sorted(anchor_gap)[int(len(anchor_gap) * 0.9)] * 100:.2f}%")
    print()
    print("[2] マージ段")
    print(f"  merge_shallow_pivots 発火: {pct(shallow_fired)}  (min_contraction_depth={min_depth})")
    print(f"  merge_short_contractions 発火: {pct(short_fired)}  (min_contraction_bars={min_bars})")
    print(f"  0日収縮 {zero_bar}件 / 1日収縮 {one_bar}件 (マージ前, 全ベース合計)")
    print(f"  1ベースあたり短収縮数の分布: {dict(sorted(short_per_base.items())[:10])}")
    print(f"  merge_shortでのT減少量の分布: {dict(sorted(reduction.items()))}")
    print("    ↑ 短収縮数の分布とほぼ一致していれば連鎖マージの暴走は起きていない")
    print()
    print("[3] T分布とV1")
    print(f"  マージ前: {dict(sorted(t_before.items()))}")
    print(f"  マージ後: {dict(sorted(t_after.items()))}")
    lo, hi = vcp_cfg["contraction_count"]
    passed = total - sum(v1_fail.values())
    print(f"  V1 [{lo},{hi}] 合格: {pct(passed)}")
    low_side = sum(v for k, v in v1_fail.items() if k < lo)
    high_side = sum(v for k, v in v1_fail.items() if k > hi)
    print(f"    不合格 少なすぎ(T<{lo}): {pct(low_side)} / 多すぎ(T>{hi}): {pct(high_side)}")
    print()
    print("[4] min_contraction_depth 感度")
    for d, acc in grid.items():
        mark = " ←現行" if abs(d - min_depth) < 1e-9 else ""
        print(f"  {d:.2f}: V1合格 {acc['v1']} ({acc['v1'] / total * 100:.1f}%)  "
              f"T分布(9=9以上) {dict(sorted(acc['T'].items()))}{mark}")


if __name__ == "__main__":
    main()
