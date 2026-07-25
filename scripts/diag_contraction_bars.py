"""収縮の最小期間 (vcp.min_contraction_bars) の感度を測るドライラン集計。

Minervini の収縮は日〜週で形成される調整であって、1本の足の高安レンジは収縮では
ない。ZigZag は自足のレンジだけで閾値を満たした足を H/L 両方のピボットにするため
0日収縮 (high_idx == low_idx) が生まれる。この閾値を入れると footprint の T 数・
V1/V2/V4/V7・エントリーのピボットがどう動くかを、採用前に全銘柄で確認する。

    python3 scripts/diag_contraction_bars.py [--bars 0 2 3 5] [--limit N]

config は書き換えず、メモリ上のコピーに値を差し込んで評価するだけ。
"""
from __future__ import annotations

import argparse
import copy
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.data.prices import load_cache  # noqa: E402
from src.indicators import compute_all  # noqa: E402
from src.screener.vcp import evaluate_vcp  # noqa: E402

WATCH = ("WATCH_A", "WATCH_B")


def _summarize(result: dict) -> dict:
    cons = result.get("contractions") or []
    return {
        "status": result.get("status"),
        "n": len(cons),
        "footprint": result.get("footprint"),
        "score": result.get("vcp_score"),
        "zero_bar": sum(1 for c in cons if c["high_idx"] == c["low_idx"]),
        "min_bars": min((c["low_idx"] - c["high_idx"] for c in cons), default=None),
        "flags": result.get("must_flags"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, nargs="+", default=[0, 2, 3, 5])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    base_cfg = load_config()
    codes = sorted(os.path.basename(p)[:-8] for p in glob.glob("data/prices/*.parquet"))
    if args.limit:
        codes = codes[: args.limit]

    cfgs = {}
    for b in args.bars:
        c = copy.deepcopy(base_cfg)
        c["vcp"]["min_contraction_bars"] = b
        cfgs[b] = c

    per_bars: dict[int, dict[str, dict]] = {b: {} for b in args.bars}
    for code in codes:
        df = load_cache(code)
        if df is None or len(df) < 220:
            continue
        try:
            df = compute_all(df)
        except Exception:
            continue
        for b in args.bars:
            try:
                per_bars[b][code] = _summarize(evaluate_vcp(df, cfgs[b]))
            except Exception as e:  # noqa: BLE001
                per_bars[b][code] = {"status": f"ERROR:{e}", "n": 0}

    baseline = args.bars[0]
    base = per_bars[baseline]
    print(f"対象銘柄: {len(base)}\n")

    for b in args.bars:
        cur = per_bars[b]
        st: dict[str, int] = {}
        zero_bar_total = 0
        for s in cur.values():
            st[s["status"]] = st.get(s["status"], 0) + 1
            zero_bar_total += s.get("zero_bar") or 0
        order = ["WATCH_A", "WATCH_B", "IMMATURE", "REJECTED", "TOO_VOLATILE",
                 "TOO_RECENT", "NO_BASE"]
        line = " ".join(f"{k}={st[k]}" for k in order if k in st)
        others = " ".join(f"{k}={v}" for k, v in st.items() if k not in order)
        n_changed = sum(1 for c, s in cur.items() if s["n"] != base[c]["n"])
        st_changed = sum(1 for c, s in cur.items() if s["status"] != base[c]["status"])
        print(f"--- min_contraction_bars = {b} ---")
        print(f"  {line} {others}".rstrip())
        print(f"  0日収縮の総数: {zero_bar_total}")
        if b != baseline:
            print(f"  収縮数が変わった銘柄: {n_changed} / status が変わった銘柄: {st_changed}")
        print()

    # WATCH に出入りした銘柄は個別に出す(採用可否の判断材料)。
    for b in args.bars[1:]:
        cur = per_bars[b]
        entered = [c for c in cur if cur[c]["status"] in WATCH and base[c]["status"] not in WATCH]
        left = [c for c in cur if base[c]["status"] in WATCH and cur[c]["status"] not in WATCH]
        stayed = [c for c in cur if base[c]["status"] in WATCH and cur[c]["status"] in WATCH]
        print(f"=== bars={b}: WATCH の増減 (基準 bars={baseline}) ===")
        for code in left:
            print(f"  - 脱落 {code}: {base[code]['status']} {base[code]['footprint']} "
                  f"score={base[code]['score']} -> {cur[code]['status']} "
                  f"n={cur[code]['n']} flags={cur[code]['flags']}")
        for code in entered:
            print(f"  + 新規 {code}: {base[code]['status']} n={base[code]['n']} -> "
                  f"{cur[code]['status']} {cur[code]['footprint']} score={cur[code]['score']}")
        for code in stayed:
            if base[code] != cur[code]:
                print(f"  ~ 残留 {code}: {base[code]['status']} {base[code]['footprint']} "
                      f"score={base[code]['score']} -> {cur[code]['status']} "
                      f"{cur[code]['footprint']} score={cur[code]['score']}")
        print()


if __name__ == "__main__":
    main()
