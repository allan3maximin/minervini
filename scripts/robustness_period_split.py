"""期間分割ロバストネスチェック(2026-07-15指示、傾向の頑健性確認のみ)。

検証ウィンドウ400日を前半200日/後半200日にカレンダー分割し、
既存の所見が両期間で符号一致して再現するかだけを確認する。
**パラメータ探索・新バケット追加は一切しない**(多重比較回避)。既存の _bucket_med を再利用。

確認する3傾向:
  (A) dryup_med_10_50 が低い帯ほど期待Rが高い(単調性)
  (B) shakeout_detected=true の期待R優位(true > false)
  (C) TOPIX -1%超急落日ブレイクの期待R劣位(crash < non-crash)

n<10の帯は「参考」と明示し結論に使わない。両期間で符号一致した傾向のみ「頑健」。
出力: data/backtest/robustness_period_split_YYYYMMDD.md
"""
from __future__ import annotations

import statistics as st
from datetime import datetime

import pandas as pd

from src.backtest import BACKTEST_DIR, _bucket_med, load_universe_frames, run_backtest
from src.config import REPO_ROOT, load_config


MED_BANDS = ["<0.4", "0.4-0.6", "0.6-0.8", ">=0.8"]  # _bucket_med と同一(新規追加ではない)


def _measured(setups: list[dict]) -> list[dict]:
    return [
        s for s in setups
        if s.get("breakout") and not s.get("extended_skip") and s.get("r_multiple") is not None
    ]


def _mean(vals: list[float]):
    return st.mean(vals) if vals else None


def _fmt(vals: list[float]) -> str:
    m = _mean(vals)
    return f"{m:.2f}" if m is not None else "n/a"


def _flag(n: int) -> str:
    return "" if n >= 10 else " ※参考"


def main() -> None:
    config = load_config()
    rs_min = config["trend_template"]["rs_min"]
    vol_mult = config["entry"]["breakout_vol_mult"]
    stop_pct = config["entry"]["stop_loss_pct"]

    result = run_backtest(days=400, limit=None, rs_min=rs_min, vol_mult=vol_mult, stop_pct=stop_pct, config=config)
    measured = _measured(result["setups"])
    frames = load_universe_frames(None)

    # --- カレンダー分割の cutoff: ベンチマーク(1306)日足の直近400本の中間日 ---
    ticker = config["data"]["topix_proxy_ticker"].split(".")[0]
    bdf = pd.read_parquet(REPO_ROOT / "data" / "prices" / f"{ticker}.parquet").sort_values("date")
    bench_close = bdf.set_index("date")["close"]
    bench_ret = bench_close.pct_change() * 100
    win_dates = list(bench_close.index[-400:])
    cutoff = win_dates[len(win_dates) // 2]  # 200本目 ≒ 前半/後半の境界

    # entry日TOPIX騰落を付与
    for s in measured:
        s["_topix_ret"] = None
        df = frames.get(s["code"])
        ei = s.get("entry_idx")
        if df is not None and ei is not None and ei < len(df):
            d = df.iloc[ei]["date"]
            if d in bench_ret.index and pd.notna(bench_ret.loc[d]):
                s["_topix_ret"] = float(bench_ret.loc[d])

    first = [s for s in measured if s["setup_date"] < cutoff]
    second = [s for s in measured if s["setup_date"] >= cutoff]

    lines: list[str] = []
    ap = lines.append
    ap(f"# 期間分割ロバストネスチェック ({datetime.now():%Y-%m-%d})")
    ap("")
    ap(f"- パラメータ変更なし(rs_min={rs_min}/vol_mult={vol_mult}/stop={stop_pct})。新バケット追加なし。")
    ap(f"- 境界日 cutoff = {pd.Timestamp(cutoff):%Y-%m-%d}(ベンチ400本の中間)")
    ap(f"- measured n = {len(measured)} → 前半 {len(first)} / 後半 {len(second)}")
    ap("- n<10 の帯は「※参考」。両期間で符号一致した傾向のみ「頑健」と判定。")
    ap("")

    def band_r(setups, band):
        vals = [s["r_multiple"] for s in setups if _bucket_med(s["dryup_setup"].get("dryup_med_10_50")) == band]
        return vals

    # ===== (A) dryup_med 単調性 =====
    ap("## (A) dryup_med_10_50 低い帯ほど期待R高い(単調性)")
    ap("")
    ap("| dryup_med帯 | 前半 期待R(n) | 後半 期待R(n) |")
    ap("|---|---:|---:|")
    a_rows = {}
    for band in MED_BANDS:
        fv, sv = band_r(first, band), band_r(second, band)
        a_rows[band] = (fv, sv)
        ap(f"| {band} | {_fmt(fv)} (n={len(fv)}{_flag(len(fv))}) | {_fmt(sv)} (n={len(sv)}{_flag(len(sv))}) |")
    # 単調性判定: n>=10の帯だけ抜き出し、低帯→高帯でRが概ね単調減少か
    def monotone_sign(rows_idx):
        seq = []
        for band in MED_BANDS:
            vals = a_rows[band][rows_idx]
            if len(vals) >= 10:
                seq.append((band, _mean(vals)))
        if len(seq) < 2:
            return None, seq
        # 低帯ほど高Rなら、bandを低→高に並べたときRが減少方向
        decreasing = all(seq[i][1] >= seq[i + 1][1] for i in range(len(seq) - 1))
        increasing = all(seq[i][1] <= seq[i + 1][1] for i in range(len(seq) - 1))
        if decreasing and not increasing:
            return "低帯ほど高R(所見と一致)", seq
        if increasing and not decreasing:
            return "反転(高帯ほど高R)", seq
        return "非単調", seq
    fa, fseq = monotone_sign(0)
    sa, sseq = monotone_sign(1)
    ap("")
    ap(f"- 前半(n>=10帯 {[b for b,_ in fseq]}): {fa}")
    ap(f"- 後半(n>=10帯 {[b for b,_ in sseq]}): {sa}")
    a_robust = (fa == "低帯ほど高R(所見と一致)" and sa == "低帯ほど高R(所見と一致)")
    ap(f"- **判定: {'頑健(両期間で一致)' if a_robust else '非頑健/期間依存の可能性'}**")
    ap("")

    # ===== (B) shakeout true vs false =====
    ap("## (B) shakeout_detected=true の期待R優位")
    ap("")
    ap("| 期間 | true 期待R(n) | false 期待R(n) | 符号(true>false?) |")
    ap("|---|---:|---:|---:|")
    b_signs = []
    for name, grp in [("前半", first), ("後半", second)]:
        tv = [s["r_multiple"] for s in grp if s["dryup_setup"].get("shakeout_detected")]
        fv = [s["r_multiple"] for s in grp if not s["dryup_setup"].get("shakeout_detected")]
        ok = (_mean(tv) is not None and _mean(fv) is not None and len(tv) >= 10 and len(fv) >= 10)
        sign = ("優位" if _mean(tv) > _mean(fv) else "逆転") if (_mean(tv) is not None and _mean(fv) is not None) else "n/a"
        note = "" if ok else "(参考)"
        b_signs.append((sign, ok))
        ap(f"| {name} | {_fmt(tv)} (n={len(tv)}{_flag(len(tv))}) | {_fmt(fv)} (n={len(fv)}{_flag(len(fv))}) | {sign}{note} |")
    b_robust = all(s == "優位" for s, _ in b_signs)
    b_usable = [ok for _, ok in b_signs]
    ap("")
    ap(f"- **判定: {'頑健(両期間で優位)' if b_robust else '非頑健/期間依存の可能性'}**"
       + ("(ただしtrueのnが片期間<10で参考含み)" if not all(b_usable) else ""))
    ap("")

    # ===== (C) TOPIX -1%超急落日ブレイクの劣位 =====
    ap("## (C) TOPIX -1%超急落日ブレイクの期待R劣位")
    ap("")
    ap("| 期間 | 急落日(<-1%) 期待R(n) | それ以外 期待R(n) | 符号(急落<それ以外?) |")
    ap("|---|---:|---:|---:|")
    c_signs = []
    for name, grp in [("前半", first), ("後半", second)]:
        crash = [s["r_multiple"] for s in grp if (s.get("_topix_ret") is not None and s["_topix_ret"] < -1.0)]
        other = [s["r_multiple"] for s in grp if (s.get("_topix_ret") is not None and s["_topix_ret"] >= -1.0)]
        sign = ("劣位" if (_mean(crash) is not None and _mean(other) is not None and _mean(crash) < _mean(other)) else "非劣位") if crash and other else "n/a"
        c_signs.append((sign, len(crash) >= 10))
        ap(f"| {name} | {_fmt(crash)} (n={len(crash)}{_flag(len(crash))}) | {_fmt(other)} (n={len(other)}{_flag(len(other))}) | {sign} |")
    c_robust = all(s == "劣位" for s, _ in c_signs)
    ap("")
    ap("- 注: 急落日ブレイクは各期間で n がごく小さくなるため、符号一致しても「参考」水準。")
    ap(f"- **判定: {'両期間で符号一致(劣位)' if c_robust else '期間依存/サンプル過小'}**")
    ap("")

    # ===== 総括 =====
    ap("## 総括")
    ap("")
    ap(f"- (A) dryup_med単調性: {'頑健' if a_robust else '非頑健'}")
    ap(f"- (B) shakeout優位: {'頑健' if b_robust else '非頑健'}")
    ap(f"- (C) 急落日劣位: {'符号一致(ただしn過小=参考)' if c_robust else '非頑健/過小'}")
    ap("")
    ap("符号反転・片期間のみの傾向は「期間依存の偶然の可能性」として今後の判断で重み付けを下げる。")

    md = "\n".join(lines)
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKTEST_DIR / f"robustness_period_split_{datetime.now():%Y%m%d}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
