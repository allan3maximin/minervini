"""Item3&4 調査スクリプト(2026-07-15指示)。

バックテスト(run_backtest, 本番configパラメータ)を1回走らせ、以下を出力する:

  Item3 交絡チェック:
    (3a) tightness_10d バケット(<=0.05 / 0.05-0.08 / >0.08)ごとの
         dryup_med_10_50 中央値 + n(枯れ度が薄い方に偏っていないかの確認)。
    (3b) tightness × dryup_med の2次元クロス表(期待R / n)。
    (3c) タイト×枯れ(tightness<=0.08 × dryup_med<0.66) と
         タイト×枯れてない(<=0.08 × >=0.77) の期待R差。

  Item4 強ブレイク劣位の深掘り:
    (4a) 出来高倍率バケット(1.4-2.0 / 2.0-3.0 / 3.0+)ごとの期待R/n。
         ※倍率はブレイク日の volume/vol_ma50 を再計算。
    (4b) 強ブレイク(vol_mult>=1.4)エントリー日のTOPIX騰落率分布
         (地合い急落日ブレイク混入の確認)。

集計対象は measured セットアップ(breakout & not extended_skip & r_multiple not None)のみ。
出力は data/backtest/investigate_dryup_confound_YYYYMMDD.md。
"""
from __future__ import annotations

import statistics as st
from datetime import datetime

import pandas as pd

from src.backtest import BACKTEST_DIR, load_universe_frames, run_backtest
from src.config import load_config
from src.data import prices as prices_mod


def _measured(setups: list[dict]) -> list[dict]:
    return [
        s for s in setups
        if s.get("breakout") and not s.get("extended_skip") and s.get("r_multiple") is not None
    ]


def _fmt(vals: list[float]) -> str:
    if not vals:
        return "n/a"
    return f"{st.mean(vals):.2f}"


def _median(vals: list[float]):
    return st.median(vals) if vals else None


# ---- tightness / dryup バケット定義 ----
def tight_bucket(t) -> str | None:
    if t is None:
        return None
    if t <= 0.05:
        return "<=0.05"
    if t <= 0.08:
        return "0.05-0.08"
    return ">0.08"


def dry_bucket(d) -> str | None:
    """新バッジ閾値(0.66/0.77)準拠の3分割。"""
    if d is None:
        return None
    if d < 0.66:
        return "dry(<0.66)"
    if d < 0.77:
        return "mild(0.66-0.77)"
    return "wet(>=0.77)"


def vol_bucket(r) -> str | None:
    if r is None:
        return None
    if r < 1.4:
        return "<1.4(弱)"
    if r < 2.0:
        return "1.4-2.0"
    if r < 3.0:
        return "2.0-3.0"
    return "3.0+"


def main() -> None:
    config = load_config()
    rs_min = config["trend_template"]["rs_min"]
    vol_mult = config["entry"]["breakout_vol_mult"]
    stop_pct = config["entry"]["stop_loss_pct"]

    result = run_backtest(days=400, limit=None, rs_min=rs_min, vol_mult=vol_mult, stop_pct=stop_pct, config=config)
    setups = _measured(result["setups"])

    # ブレイク日の出来高倍率とエントリー日TOPIX騰落率のため frame を再ロード(決定的)
    frames = load_universe_frames(None)
    # TOPIXプロキシ(1306)はネット取得せず data/prices の日足キャッシュを直接読む
    # (バックテストと同じ決定的データ源。get_benchmark_close は yfinance を叩くため回避)。
    try:
        from src.config import REPO_ROOT
        ticker = config["data"]["topix_proxy_ticker"].split(".")[0]
        bpath = REPO_ROOT / "data" / "prices" / f"{ticker}.parquet"
        bdf = pd.read_parquet(bpath).sort_values("date")
        bench = bdf.set_index("date")["close"]
        bench_ret = bench.pct_change() * 100  # % 日次騰落
    except Exception as e:  # noqa: BLE001
        bench_ret = None
        bench_err = str(e)

    # 各setupに vol_ratio / entry_topix_ret を付与
    for s in setups:
        df = frames.get(s["code"])
        s["_vol_ratio"] = None
        s["_topix_ret"] = None
        if df is None:
            continue
        bi = s.get("breakout_idx")
        if bi is not None and bi < len(df):
            row = df.iloc[bi]
            vma = row.get("vol_ma50")
            if vma and not pd.isna(vma):
                s["_vol_ratio"] = float(row["volume"]) / float(vma)
        ei = s.get("entry_idx")
        if ei is not None and ei < len(df) and bench_ret is not None:
            d = df.iloc[ei]["date"]
            if d in bench_ret.index:
                v = bench_ret.loc[d]
                s["_topix_ret"] = float(v) if pd.notna(v) else None

    lines: list[str] = []
    ap = lines.append
    ap(f"# DRY-UP 交絡・強ブレイク深掘り調査 ({datetime.now():%Y-%m-%d})")
    ap("")
    ap(f"- パラメータ: days=400, rs_min={rs_min}, vol_mult={vol_mult}, stop_pct={stop_pct}, step=1")
    ap(f"- measured セットアップ n = {len(setups)}")
    ap("")

    # ------- Item3a: tightnessバケット別 dryup_med 中央値 -------
    ap("## 3a. tightnessバケット別の dryup_med_10_50 中央値(枯れ度の偏りチェック)")
    ap("")
    ap("| tightness帯 | n | dryup_med 中央値 | 期待R |")
    ap("|---|---:|---:|---:|")
    for tb in ["<=0.05", "0.05-0.08", ">0.08"]:
        grp = [s for s in setups if tight_bucket(s["dryup_setup"].get("tightness_10d")) == tb]
        meds = [s["dryup_setup"].get("dryup_med_10_50") for s in grp if s["dryup_setup"].get("dryup_med_10_50") is not None]
        rs = [s["r_multiple"] for s in grp]
        med = _median(meds)
        ap(f"| {tb} | {len(grp)} | {med:.4f} | {_fmt(rs)} |" if med is not None else f"| {tb} | {len(grp)} | n/a | {_fmt(rs)} |")
    ap("")
    ap("> tightが薄枯れ(dryup_med高)に偏っているなら、tightness逆転は枯れ度交絡の可能性。")
    ap("")

    # ------- Item3b: tightness × dryup クロス表 -------
    ap("## 3b. tightness × dryup_med 2次元クロス表(期待R / n)")
    ap("")
    dcols = ["dry(<0.66)", "mild(0.66-0.77)", "wet(>=0.77)"]
    ap("| tightness \\ dryup | " + " | ".join(dcols) + " |")
    ap("|---|" + "|".join(["---:"] * len(dcols)) + "|")
    for tb in ["<=0.05", "0.05-0.08", ">0.08"]:
        cells = []
        for db in dcols:
            grp = [
                s for s in setups
                if tight_bucket(s["dryup_setup"].get("tightness_10d")) == tb
                and dry_bucket(s["dryup_setup"].get("dryup_med_10_50")) == db
            ]
            rs = [s["r_multiple"] for s in grp]
            cells.append(f"{_fmt(rs)} (n={len(grp)})" if grp else "— (n=0)")
        ap(f"| {tb} | " + " | ".join(cells) + " |")
    ap("")

    # ------- Item3c: タイト×枯れ vs タイト×枯れてない -------
    ap("## 3c. タイト(<=0.08)内での枯れ有無の期待R差")
    ap("")
    tight = [s for s in setups if (s["dryup_setup"].get("tightness_10d") is not None and s["dryup_setup"]["tightness_10d"] <= 0.08)]
    tight_dry = [s for s in tight if (s["dryup_setup"].get("dryup_med_10_50") is not None and s["dryup_setup"]["dryup_med_10_50"] < 0.66)]
    tight_wet = [s for s in tight if (s["dryup_setup"].get("dryup_med_10_50") is not None and s["dryup_setup"]["dryup_med_10_50"] >= 0.77)]
    rd = [s["r_multiple"] for s in tight_dry]
    rw = [s["r_multiple"] for s in tight_wet]
    ap(f"- タイト×枯れ (tightness<=0.08 × dryup_med<0.66): 期待R {_fmt(rd)} (n={len(tight_dry)})")
    ap(f"- タイト×枯れてない (tightness<=0.08 × dryup_med>=0.77): 期待R {_fmt(rw)} (n={len(tight_wet)})")
    if rd and rw:
        ap(f"- 差 (枯れ − 枯れてない): {st.mean(rd) - st.mean(rw):+.2f} R")
    ap("")

    # ------- Item4a: 出来高倍率バケット別 期待R -------
    ap("## 4a. 出来高倍率(ブレイク日 volume/vol_ma50)バケット別 期待R")
    ap("")
    ap("| 倍率帯 | n | 期待R | ストップ到達率 |")
    ap("|---|---:|---:|---:|")
    for vb in ["<1.4(弱)", "1.4-2.0", "2.0-3.0", "3.0+"]:
        grp = [s for s in setups if vol_bucket(s.get("_vol_ratio")) == vb]
        rs = [s["r_multiple"] for s in grp]
        stops = [1 for s in grp if s.get("stop_hit")]
        rate = f"{len(stops)/len(grp)*100:.0f}%" if grp else "n/a"
        ap(f"| {vb} | {len(grp)} | {_fmt(rs)} | {rate} |")
    ap("")
    ap("> 1.4-2.0帯だけ劣位で3.0+が優位なら閾値問題、全帯で劣位なら構造問題。")
    ap("")

    # ------- Item4b: 強ブレイク エントリー日のTOPIX騰落率分布 -------
    ap("## 4b. 強ブレイク(vol_ratio>=1.4)エントリー日の TOPIX 騰落率分布")
    ap("")
    if bench_ret is None:
        ap(f"> TOPIXベンチマーク取得失敗: {bench_err}")
    else:
        strong = [s for s in setups if (s.get("_vol_ratio") is not None and s["_vol_ratio"] >= vol_mult)]
        trs = [s["_topix_ret"] for s in strong if s.get("_topix_ret") is not None]
        ap(f"- 強ブレイク n = {len(strong)}(うちTOPIX取得できた {len(trs)}件)")
        if trs:
            trs_sorted = sorted(trs)
            def pct(p):
                k = max(0, min(len(trs_sorted) - 1, int(round(p / 100 * (len(trs_sorted) - 1)))))
                return trs_sorted[k]
            ap(f"- min {min(trs):+.2f}% / p25 {pct(25):+.2f}% / 中央 {st.median(trs):+.2f}% / p75 {pct(75):+.2f}% / max {max(trs):+.2f}%")
            ap(f"- 平均 {st.mean(trs):+.2f}% / TOPIX下落日(<0%)ブレイク {sum(1 for x in trs if x < 0)}件 / -1%超の急落日 {sum(1 for x in trs if x < -1.0)}件")
            ap("")
            ap("| TOPIX騰落帯 | 件数 | 強ブレイク期待R |")
            ap("|---|---:|---:|")
            bands = [("<-1%", lambda x: x < -1.0), ("-1〜0%", lambda x: -1.0 <= x < 0), ("0〜+1%", lambda x: 0 <= x < 1.0), (">=+1%", lambda x: x >= 1.0)]
            for name, fn in bands:
                grp = [s for s in strong if (s.get("_topix_ret") is not None and fn(s["_topix_ret"]))]
                rs = [s["r_multiple"] for s in grp]
                ap(f"| {name} | {len(grp)} | {_fmt(rs)} |")
    ap("")

    md = "\n".join(lines)
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKTEST_DIR / f"investigate_dryup_confound_{datetime.now():%Y%m%d}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
