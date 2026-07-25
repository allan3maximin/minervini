"""現行V1〜V7 vs 「素直版」(公開実装の主流寄り) の成績比較。

    python -m scripts.compare_plain_vcp --stage scan --variant no_atr_gate
    python -m scripts.compare_plain_vcp --stage report

【比較の切り分け】
トレンドテンプレート(MUST)・RSしきい値・ブレイク判定・エントリー価格・損切り・
EXTENDED除外・成績集計はすべてのアームで完全に同一。**config["vcp"] の値だけ**を
差し替えて evaluate_vcp を回す(vcp.py には一切手を入れない)。

アームを分けている理由:
- V2 / V4 の緩和は「日本市場ではMinerviniの数値そのままだとエントリーが出ない」
  という判断による**意図的な**緩和。参考情報として個別に見る。
- ATR除外(atr_exclude_threshold)と V5 の比率は由来が不明。**この2つを単独で外した
  アーム**を作り、それぞれが検出数と成績にどう効いているかを切り分ける。

bash sandbox は1コール45秒制限のため --stage scan は1アームずつ実行し、
結果を /tmp/plain_<variant>.pkl にチェックポイントする。
"""
from __future__ import annotations

import argparse
import pickle
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.backtest import (
    BACKTEST_DIR,
    MAX_BREAKOUT_WAIT_DAYS,
    PIVOT_DEDUPE_TOLERANCE,
    SCAN_STEP,
    _entry_price,
    _perf_stats,
    build_rs_by_date,
    find_breakout_index,
    is_strong_breakout,
    load_universe_frames,
    measure_performance,
)
from src.config import load_config
from src.indicators import build_dryup_layer
from src.screener import trend_template
from src.screener import vcp as vcp_mod

HORIZONS = (5, 10, 20)
CKPT_DIR = Path("/tmp")

# ---------------------------------------------------------------------------
# アーム定義: config["vcp"] への上書き
# ---------------------------------------------------------------------------
VARIANTS: dict[str, dict] = {
    # 現行のまま(ベースライン)
    "current": {},

    # --- 由来が不明な2つ。単独で外す ---
    # ATR20/close > 9% の銘柄を base 探索前に問答無用で切る門。
    # 調べた範囲で、同じことをしている公開VCP実装は見つからなかった
    # (ATRを使う実装はむしろ「下限」= 動かなすぎる銘柄を弾く側で使う)。
    "no_atr_gate": {"atr_exclude_threshold": None},
    # V5(出来高ドライアップ)を「直近10日の出来高中央値 < vol_ma50」だけに戻す。
    # 0.85 / 0.75 / 回帰スロープの複合条件は出典不明。
    # volume_trend_ratio=0 で sub-(b) は成立不能になる(中央値<=0 が要るため)。
    "plain_v5": {"volume_dryup_median_ratio": 1.0, "volume_trend_ratio": 0.0},

    # --- 意図的に緩めた2つ。参考として単独で締める ---
    # V2: 収縮の単調減少。tolerance/前半例外/全体比率をすべて素直に。
    "strict_v2": {
        "monotonic_tolerance": 1.0,
        "early_violation_allowance": 0,
        "overall_contraction_ratio": 0.5,
    },
    # V4: 最終収縮の深さ上限。主流は 3〜10%、現行は 12%。
    "strict_v4": {"last_depth_max": 0.10},

    # --- 全部素直版 ---
    "plain_all": {
        "atr_exclude_threshold": None,
        "volume_dryup_median_ratio": 1.0,
        "volume_trend_ratio": 0.0,
        "monotonic_tolerance": 1.0,
        "early_violation_allowance": 0,
        "overall_contraction_ratio": 0.5,
        "last_depth_max": 0.10,
        # 主流は最長65週≒325営業日。現行は200。
        # なお base_max_days だけ上げても find_base_origin が scan_days_extended で
        # ベース長を頭打ちにするので効かない。探索窓も一緒に伸ばす必要がある。
        "base_max_days": 325,
        "scan_days_extended": 325,
    },
}

ARM_ORDER = ["current", "no_atr_gate", "plain_v5", "strict_v2", "strict_v4", "plain_all"]

ARM_LABEL = {
    "current": "現行(ベースライン)",
    "no_atr_gate": "ATR除外を撤廃",
    "plain_v5": "V5を素直版",
    "strict_v2": "V2を厳格版(参考)",
    "strict_v4": "V4を厳格版(参考)",
    "plain_all": "全部素直版",
}


def variant_config(base: dict, name: str) -> dict:
    cfg = deepcopy(base)
    cfg["vcp"].update(VARIANTS[name])
    return cfg


# ---------------------------------------------------------------------------
# 検出・スキャン(compare_tv_vcp と同じ骨格)
# ---------------------------------------------------------------------------

def detect_vcp(df: pd.DataFrame, i: int, config: dict) -> dict | None:
    r = vcp_mod.evaluate_vcp(df.iloc[: i + 1], config)
    if r.get("status") != "WATCH_A" or not r.get("contractions"):
        return None
    last_c = r["contractions"][-1]
    return {
        "pivot": last_c["high_price"],
        "stop_ref_low": last_c["low_price"],
        "vcp_score": r.get("vcp_score"),
        "base_days": r.get("base_days"),
        "shakeout_detected": bool(r.get("shakeout_detected")),
    }


def scan(code, df, rs_series, config, rs_min, start_idx, step=SCAN_STEP) -> list[dict]:
    n = len(df)
    setups: list[dict] = []
    seen_pivots: list[float] = []

    for i in range(start_idx, n, step):
        row = df.iloc[i]
        if pd.isna(row.get("ma200")) or pd.isna(row.get("ma50")) or pd.isna(row.get("high_52w")):
            continue

        date = row["date"]
        rs = rs_series.get(date)
        if rs is None or pd.isna(rs) or rs < rs_min:
            continue

        latest = row.to_dict()
        latest["rs"] = rs
        flags = trend_template.check_must_conditions(latest, config)
        if not trend_template.passes_trend_template(flags):
            continue

        hit = detect_vcp(df, i, config)
        if hit is None:
            continue

        pivot = hit["pivot"]
        if any(abs(pivot - p) / p <= PIVOT_DEDUPE_TOLERANCE for p in seen_pivots):
            continue
        seen_pivots.append(pivot)

        base_days = hit.get("base_days")
        base_start_idx = (i - base_days + 1) if base_days else None
        setups.append(
            {
                "code": code,
                "setup_date": date,
                "setup_idx": i,
                "pivot": pivot,
                "stop_ref_low": hit["stop_ref_low"],
                "vcp_score": hit.get("vcp_score"),
                "base_days": base_days,
                "dryup_setup": build_dryup_layer(
                    df, i, base_start_idx, pivot,
                    shakeout_detected=hit.get("shakeout_detected", False),
                ),
            }
        )
    return setups


def run(frames, rs_by_date, days, rs_min, vol_mult, stop_pct, config) -> list[dict]:
    extended_pct = config["entry"]["extended_pct"]
    all_setups: list[dict] = []
    for code, df in frames.items():
        n = len(df)
        start_idx = max(0, n - days)
        rs_series = rs_by_date[code] if code in rs_by_date.columns else pd.Series(dtype="float64")
        setups = scan(code, df, rs_series, config, rs_min, start_idx)

        for s in setups:
            bidx = find_breakout_index(df, s["setup_idx"], s["pivot"], MAX_BREAKOUT_WAIT_DAYS)
            s["breakout"] = bidx is not None
            if bidx is None:
                continue
            s["breakout_idx"] = bidx
            s["breakout_date"] = df.iloc[bidx]["date"]
            s["strong"] = is_strong_breakout(df, bidx, vol_mult)
            entry_price = _entry_price(df, bidx)
            if entry_price > s["pivot"] * (1 + extended_pct):
                s["extended_skip"] = True
                continue
            s["extended_skip"] = False
            s.update(measure_performance(df, bidx, stop_pct, HORIZONS))
        all_setups.extend(setups)
    return all_setups


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def summarize(setups: list[dict]) -> dict:
    measured = [s for s in setups if s.get("breakout") and not s.get("extended_skip", True)]
    rs = sorted(s["r_multiple"] for s in measured if s.get("r_multiple") is not None)
    by_month: dict[str, int] = defaultdict(int)
    for s in setups:
        by_month[pd.Timestamp(s["setup_date"]).strftime("%Y-%m")] += 1

    # 期待Rは裾が全部を決めるので、上位5%を落とした値も併記する
    exp_r = round(float(np.mean(rs)), 2) if rs else None
    if len(rs) >= 20:
        cut = int(len(rs) * 0.95)
        trimmed = rs[:cut]
        exp_r_trim = round(float(np.mean(trimmed)), 2) if trimmed else None
    else:
        exp_r_trim = None
    med_r = round(float(np.median(rs)), 2) if rs else None

    return {
        "setups": len(setups),
        "codes": len({s["code"] for s in setups}),
        "breakouts": sum(1 for s in setups if s.get("breakout")),
        "extended": sum(1 for s in setups if s.get("extended_skip")),
        "measured": len(measured),
        "strong": sum(1 for s in measured if s.get("strong")),
        "stop_hit": sum(1 for s in measured if s.get("stop_hit")),
        "exp_r": exp_r,
        "exp_r_trim95": exp_r_trim,
        "median_r": med_r,
        "perf": {h: _perf_stats(measured, h) for h in HORIZONS},
        "by_month": dict(sorted(by_month.items())),
        "keys": sorted({(s["code"], str(s["setup_date"])[:10]) for s in setups}),
        "measured_keys": sorted({(s["code"], str(s["setup_date"])[:10]) for s in measured}),
    }


def _pct(a, b):
    return f"{a} ({a / b * 100:.1f}%)" if b else "-"


def build_report(res: dict[str, dict], params: dict) -> str:
    arms = [a for a in ARM_ORDER if a in res]
    hdr = "| 指標 | " + " | ".join(ARM_LABEL[a] for a in arms) + " |"
    sep = "| --- |" + " --- |" * len(arms)

    def row(label, fn):
        return f"| {label} | " + " | ".join(str(fn(res[a])) for a in arms) + " |"

    L = [
        f"# 現行V1〜V7 vs 素直版 VCP 比較 ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        "**差し替えたのは config[\"vcp\"] の数値だけ。** vcp.py のコードには一切手を入れていない。",
        "トレンドテンプレート(MUST)・RS>=rs_min・ブレイク判定(終値>pivot、60営業日以内)・",
        "エントリー(ブレイク翌日始値)・損切り・EXTENDED除外・成績集計は全アーム共通。",
        "",
        "**前提**: Minerviniの数値は米国市場のもの。日本市場で全部そのままに合わせると",
        "エントリーが出なくなるため、V2/V4は重みが小さいと判断して意図的に緩めてある。",
        "このレポートで本当に見たいのは **ATR除外 と V5** の2つ(由来が不明なもの)。",
        "V2/V4の厳格版は参考情報として並べているだけで、戻すべきという主張ではない。",
        "",
        "**既知の限界**: 現ユニバースで過去を見るため生存者バイアスあり。サンプルは",
        "同一銘柄の再検出を含むため独立ではなく、期待Rは少数の大当たりで決まる。",
        "そのため上位5%を落とした期待R(exp_r_trim95)と中央値Rを併記している。",
        "",
        "## アーム定義",
        "",
    ]
    for a in arms:
        ov = VARIANTS[a]
        body = "変更なし" if not ov else ", ".join(f"`{k}`: {v}" for k, v in ov.items())
        L.append(f"- **{ARM_LABEL[a]}** (`{a}`) — {body}")

    L += [
        "",
        "## パラメータ",
        f"- 検証ウィンドウ: 直近{params['days']}営業日 / スキャン粒度: 日次",
        f"- rs_min: {params['rs_min']} / breakout_vol_mult: {params['vol_mult']} / stop_loss_pct: {params['stop_pct']}",
        f"- 読み込み銘柄数: {params['codes_scanned']}",
        "",
        "## サマリ",
        "",
        hdr,
        sep,
        row("セットアップ検出数", lambda r: r["setups"]),
        row("検出された銘柄数", lambda r: r["codes"]),
        row("ブレイク発生", lambda r: _pct(r["breakouts"], r["setups"])),
        row("EXTENDED除外", lambda r: r["extended"]),
        row("成績集計対象(実測)", lambda r: r["measured"]),
        row("うち強ブレイク", lambda r: r["strong"]),
        row("損切り到達", lambda r: _pct(r["stop_hit"], r["measured"])),
        row("**期待R**", lambda r: f"**{r['exp_r']}**"),
        row("期待R(上位5%除外)", lambda r: r["exp_r_trim95"]),
        row("中央値R", lambda r: r["median_r"]),
        "",
        "## ホライズン別リターン(EXTENDED除外後)",
        "",
    ]
    for h in HORIZONS:
        L += [
            f"### +{h}営業日",
            "",
            hdr,
            sep,
            row("平均", lambda r, h=h: f"{r['perf'][h]['mean']}%"),
            row("中央値", lambda r, h=h: f"{r['perf'][h]['median']}%"),
            row("勝率", lambda r, h=h: f"{r['perf'][h]['win_rate']}%"),
            row("n", lambda r, h=h: r["perf"][h]["n"]),
            "",
        ]

    # 現行との差分(検出集合)
    base_keys = set(res["current"]["keys"]) if "current" in res else set()
    L += [
        "## 現行との検出の差分(銘柄×検出日)",
        "",
        "| アーム | 現行のみ | 両方 | 当該アームのみ |",
        "| --- | --- | --- | --- |",
    ]
    for a in arms:
        if a == "current":
            continue
        k = set(res[a]["keys"])
        L.append(f"| {ARM_LABEL[a]} | {len(base_keys - k)} | {len(base_keys & k)} | {len(k - base_keys)} |")

    L += ["", "## 月別セットアップ検出数", "", hdr, sep]
    months = sorted(set().union(*[set(res[a]["by_month"]) for a in arms]))
    for m in months:
        L.append(f"| {m} | " + " | ".join(str(res[a]["by_month"].get(m, 0)) for a in arms) + " |")

    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------

def _load_frames(limit):
    fr_pkl = CKPT_DIR / "fr.pkl"
    if fr_pkl.exists() and limit is None:
        with open(fr_pkl, "rb") as f:
            d = pickle.load(f)
        return d["f"], d["rs"]
    frames = load_universe_frames(limit)
    rs_by_date = build_rs_by_date(frames)
    if limit is None:
        with open(fr_pkl, "wb") as f:
            pickle.dump({"f": frames, "rs": rs_by_date}, f)
    return frames, rs_by_date


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["scan", "report"], required=True)
    p.add_argument("--variant", choices=list(VARIANTS), default=None)
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rs-min", type=float, default=None)
    p.add_argument("--vol-mult", type=float, default=None)
    p.add_argument("--stop-pct", type=float, default=0.05)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    base = load_config()
    rs_min = args.rs_min if args.rs_min is not None else base["trend_template"]["rs_min"]
    vol_mult = args.vol_mult if args.vol_mult is not None else base["entry"]["breakout_vol_mult"]

    if args.stage == "scan":
        if not args.variant:
            p.error("--stage scan requires --variant")
        frames, rs_by_date = _load_frames(args.limit)
        cfg = variant_config(base, args.variant)
        setups = run(frames, rs_by_date, args.days, rs_min, vol_mult, args.stop_pct, cfg)
        s = summarize(setups)
        s["_meta"] = {
            "days": args.days, "rs_min": rs_min, "vol_mult": vol_mult,
            "stop_pct": args.stop_pct, "codes_scanned": len(frames),
        }
        with open(CKPT_DIR / f"plain_{args.variant}.pkl", "wb") as f:
            pickle.dump(s, f)
        print(f"{args.variant}: setups={s['setups']} measured={s['measured']} "
              f"exp_r={s['exp_r']} trim95={s['exp_r_trim95']} med={s['median_r']}")
        return

    # report
    res: dict[str, dict] = {}
    for a in ARM_ORDER:
        fp = CKPT_DIR / f"plain_{a}.pkl"
        if fp.exists():
            with open(fp, "rb") as f:
                res[a] = pickle.load(f)
    if not res:
        raise SystemExit("no checkpoints found; run --stage scan first")

    meta = res.get("current", next(iter(res.values())))["_meta"]
    md = build_report(res, meta)
    out = args.out or (BACKTEST_DIR / f"compare_plain_vcp_{datetime.now():%Y%m%d}.md")
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
