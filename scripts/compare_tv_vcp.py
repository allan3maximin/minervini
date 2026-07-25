"""現行VCP検出 vs TradingView "Minervini Ultimate +VCP" 式 SQUEEZE 検出の成績比較。

    python -m scripts.compare_tv_vcp [--days 400] [--limit N] [--squeeze-pct 0.025] [--window 5]

【比較の切り分け】
トレンドテンプレート(MUST)・RSしきい値・ブレイク判定・エントリー価格・損切り・
成績集計は src.backtest と完全に同一。**VCP検出のレイヤーだけ**を差し替える。

- current: src.screener.vcp.evaluate_vcp が status=WATCH_A を返した日をセットアップとし、
           pivot = 最終収縮の高値。
- tv     : TradingViewインジケーターの VCP Action と同じ定義。直近 window(既定5)本の
           **終値**の最高/最低のレンジが squeeze_pct(既定2.5%)未満なら SQUEEZE。
           TVインジケーターはピボットを定義しないため、バックテストの都合上
           pivot = 直近 window 本の高値の最大、stop_ref_low = 同区間の安値の最小 とする。

同一銘柄でpivotが±1%以内のセットアップは1件に統合する(src.backtest と同じ)。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime

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


# ---------------------------------------------------------------------------
# 検出器
# ---------------------------------------------------------------------------

def detect_current(df: pd.DataFrame, i: int, config: dict) -> dict | None:
    """現行ロジック: evaluate_vcp が WATCH_A の日。"""
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


def detect_tv_squeeze(df: pd.DataFrame, i: int, config: dict, window: int, squeeze_pct: float) -> dict | None:
    """TV式: 直近 window 本の終値レンジ < squeeze_pct なら SQUEEZE。"""
    if i + 1 < window:
        return None
    seg = df.iloc[i + 1 - window : i + 1]
    c_hi = float(seg["close"].max())
    c_lo = float(seg["close"].min())
    if c_lo <= 0:
        return None
    if (c_hi - c_lo) / c_lo >= squeeze_pct:
        return None
    return {
        "pivot": float(seg["high"].max()),
        "stop_ref_low": float(seg["low"].min()),
        "vcp_score": None,
        "base_days": window,
        "shakeout_detected": False,
    }


# ---------------------------------------------------------------------------
# スキャン(src.backtest.scan_setups と同じ骨格、検出器だけ差し替え)
# ---------------------------------------------------------------------------

def scan(code, df, rs_series, config, rs_min, start_idx, detector, step=SCAN_STEP) -> list[dict]:
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

        hit = detector(df, i, config)
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
                "dryup_setup": build_dryup_layer(
                    df, i, base_start_idx, pivot,
                    shakeout_detected=hit.get("shakeout_detected", False),
                ),
            }
        )
    return setups


def run(frames, rs_by_date, days, rs_min, vol_mult, stop_pct, config, detector) -> list[dict]:
    extended_pct = config["entry"]["extended_pct"]
    all_setups: list[dict] = []
    for code, df in frames.items():
        n = len(df)
        start_idx = max(0, n - days)
        rs_series = rs_by_date[code] if code in rs_by_date.columns else pd.Series(dtype="float64")
        setups = scan(code, df, rs_series, config, rs_min, start_idx, detector)

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
    rs = [s["r_multiple"] for s in measured if s.get("r_multiple") is not None]
    by_month: dict[str, int] = defaultdict(int)
    for s in setups:
        by_month[pd.Timestamp(s["setup_date"]).strftime("%Y-%m")] += 1
    return {
        "setups": len(setups),
        "codes": len({s["code"] for s in setups}),
        "breakouts": sum(1 for s in setups if s.get("breakout")),
        "extended": sum(1 for s in setups if s.get("extended_skip")),
        "measured": len(measured),
        "strong": sum(1 for s in measured if s.get("strong")),
        "stop_hit": sum(1 for s in measured if s.get("stop_hit")),
        "exp_r": round(sum(rs) / len(rs), 2) if rs else None,
        "perf": {h: _perf_stats(measured, h) for h in HORIZONS},
        "by_month": dict(sorted(by_month.items())),
        "measured_list": measured,
    }


def _row(label, a, b):
    return f"| {label} | {a} | {b} |"


def build_report(cur: dict, tv: dict, params: dict) -> str:
    L = [
        f"# 現行VCP vs TradingView式SQUEEZE 比較 ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        "対象: <https://jp.tradingview.com/script/YqIDj8rq-Minervini-Ultimate-VCP/>",
        "",
        "**差し替えたのはVCP検出レイヤーのみ。** トレンドテンプレート(MUST)・RS>=rs_min・",
        "ブレイク判定(終値>pivot、60営業日以内)・エントリー(ブレイク翌日始値)・損切り・",
        "EXTENDED除外・成績集計は両者で完全に同一。",
        "",
        "**既知の限界**: 現ユニバースで過去を見るため生存者バイアスあり。TVインジケーターは",
        "ピボットを定義しないため、TV側のpivotはSQUEEZE区間の高値最大で代用している",
        "(この代用がTV側の成績を左右しうる点に注意)。",
        "",
        "## パラメータ",
        f"- 検証ウィンドウ: 直近{params['days']}営業日 / スキャン粒度: 日次",
        f"- rs_min: {params['rs_min']} / breakout_vol_mult: {params['vol_mult']} / stop_loss_pct: {params['stop_pct']}",
        f"- TV式SQUEEZE: 直近{params['window']}本の終値レンジ < {params['squeeze_pct']:.1%}",
        f"- 読み込み銘柄数: {params['codes_scanned']}",
        "",
        "## サマリ",
        "",
        "| 指標 | 現行VCP | TV式SQUEEZE |",
        "| --- | --- | --- |",
        _row("セットアップ検出数", cur["setups"], tv["setups"]),
        _row("検出された銘柄数", cur["codes"], tv["codes"]),
        _row("ブレイク発生", f"{cur['breakouts']} ({cur['breakouts']/cur['setups']*100:.1f}%)" if cur["setups"] else "-",
             f"{tv['breakouts']} ({tv['breakouts']/tv['setups']*100:.1f}%)" if tv["setups"] else "-"),
        _row("EXTENDED除外", cur["extended"], tv["extended"]),
        _row("成績集計対象(実測ブレイク)", cur["measured"], tv["measured"]),
        _row("うち強ブレイク(出来高1.4倍以上)", cur["strong"], tv["strong"]),
        _row("損切り到達", f"{cur['stop_hit']} ({cur['stop_hit']/cur['measured']*100:.1f}%)" if cur["measured"] else "-",
             f"{tv['stop_hit']} ({tv['stop_hit']/tv['measured']*100:.1f}%)" if tv["measured"] else "-"),
        _row("**期待R (20営業日/損切り5%)**", f"**{cur['exp_r']}**", f"**{tv['exp_r']}**"),
        "",
        "## ホライズン別リターン(EXTENDED除外後)",
        "",
        "| ホライズン | 現行 平均 | 現行 中央値 | 現行 勝率 | 現行 n | TV 平均 | TV 中央値 | TV 勝率 | TV n |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for h in HORIZONS:
        c, t = cur["perf"][h], tv["perf"][h]
        L.append(
            f"| +{h}営業日 | {c['mean']}% | {c['median']}% | {c['win_rate']}% | {c['n']} "
            f"| {t['mean']}% | {t['median']}% | {t['win_rate']}% | {t['n']} |"
        )

    # 重複度
    cur_keys = {(s["code"], str(s["setup_date"])[:10]) for s in cur["measured_list"]}
    tv_keys = {(s["code"], str(s["setup_date"])[:10]) for s in tv["measured_list"]}
    both = cur_keys & tv_keys
    L += [
        "",
        "## 検出の重なり(実測ブレイクの銘柄×検出日ベース)",
        f"- 現行のみ: {len(cur_keys - tv_keys)}件",
        f"- 両方: {len(both)}件",
        f"- TVのみ: {len(tv_keys - cur_keys)}件",
        "",
        "## 月別セットアップ検出数",
        "",
        "| 月 | 現行 | TV |",
        "| --- | --- | --- |",
    ]
    for m in sorted(set(cur["by_month"]) | set(tv["by_month"])):
        L.append(f"| {m} | {cur['by_month'].get(m, 0)} | {tv['by_month'].get(m, 0)} |")

    return "\n".join(L) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=400)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rs-min", type=float, default=None)
    p.add_argument("--vol-mult", type=float, default=None)
    p.add_argument("--stop-pct", type=float, default=0.05)
    p.add_argument("--window", type=int, default=5)
    p.add_argument("--squeeze-pct", type=float, default=0.025)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    config = load_config()
    rs_min = args.rs_min if args.rs_min is not None else config["trend_template"]["rs_min"]
    vol_mult = args.vol_mult if args.vol_mult is not None else config["entry"]["breakout_vol_mult"]

    frames = load_universe_frames(args.limit)
    rs_by_date = build_rs_by_date(frames)
    print(f"loaded {len(frames)} codes")

    cur = summarize(run(frames, rs_by_date, args.days, rs_min, vol_mult, args.stop_pct, config,
                        lambda df, i, cfg: detect_current(df, i, cfg)))
    print(f"current: {cur['setups']} setups, measured {cur['measured']}")
    tv = summarize(run(frames, rs_by_date, args.days, rs_min, vol_mult, args.stop_pct, config,
                       lambda df, i, cfg: detect_tv_squeeze(df, i, cfg, args.window, args.squeeze_pct)))
    print(f"tv     : {tv['setups']} setups, measured {tv['measured']}")

    params = {
        "days": args.days, "rs_min": rs_min, "vol_mult": vol_mult, "stop_pct": args.stop_pct,
        "window": args.window, "squeeze_pct": args.squeeze_pct, "codes_scanned": len(frames),
    }
    md = build_report(cur, tv, params)
    out = args.out or (BACKTEST_DIR / f"compare_tv_vcp_{datetime.now():%Y%m%d}.md")
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
