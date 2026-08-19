#!/usr/bin/env python3
"""既にある地合いシグナルを型1の入口フィルタに使えるか(2026-08-19)。

182 で分かったこと: 型1が負けるのは「下げ相場」全般ではなく、一方通行で
崩れ始めた最初の数週間だけ(2020年は2月だけで年間の負けを全部作っている)。
そして指数の位置と傾きだけでは、ただの押し目と本物の崩れを切り分けられなかった。

指数の値段は「上位の重い銘柄」しか映さない。一方 src/report/market_signal.py が
使っている「200日線の上にいる銘柄の割合」は、全銘柄が一斉に線を割ったかどうかを
直接数える。これなら普通の押し目(割合は50%前後で踏みとどまる)と
本物の崩れ(10-20%まで落ちる)が別物として出るのではないか、という仮説を測る。

    python tools/meanrev/regime_filter.py

data/audit_cache/market.parquet に必要な材料がそのまま入っている:
  br200   200日線の上にいる銘柄の割合
  br50    50日線の上にいる銘柄の割合
  newhigh 52週高値の98%以上にいる銘柄の割合
  idx_vs200 / idx_vs50  等加重指数が各線から何%上か
  idx     等加重指数そのもの(200日線の向きをここから作る)

すべてその日までの値なので、当日に判断材料として使える。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meanrev import core as C  # noqa: E402
from meanrev import exits as E  # noqa: E402

LIQ = 3e8
STOP = "1.5ATR(3-12%)"
BASE_HOLD = 5
RS_MIN = 70  # 182 で決めた母集団:「市場より強い上位3割」

REGIMES = [
    ("2000-02 ITバブル崩壊", "2000-01-01", "2002-12-31"),
    ("2003-06 小泉相場", "2003-01-01", "2006-12-31"),
    ("2007-09 リーマン", "2007-01-01", "2009-12-31"),
    ("2010-12 震災・超円高", "2010-01-01", "2012-12-31"),
    ("2013-14 アベノミクス初期", "2013-01-01", "2014-12-31"),
    ("2015-17 アベノミクス中期", "2015-01-01", "2017-12-31"),
    ("2018-19 米中摩擦・年末急落", "2018-01-01", "2019-12-31"),
    ("2020    コロナ", "2020-01-01", "2020-12-31"),
    ("2021-22 グロース崩壊", "2021-01-01", "2022-12-31"),
    ("2023-24 日経高値・24年8月暴落", "2023-01-01", "2024-12-31"),
    ("2025-26 直近", "2025-01-01", "2026-12-31"),
]


def _f(name: str, src: bool = True) -> np.ndarray:
    return np.asarray(C.load(name, src=src), dtype=np.float32)


# ---------------------------------------------------------------- 地合い
def market() -> pd.DataFrame:
    m = pd.read_parquet(C.SRC / "market.parquet")
    assert len(m) == C.dates().size, (len(m), C.dates().size)
    lvl = m["idx"].to_numpy(dtype=float)
    ma200 = pd.Series(lvl).rolling(200, min_periods=200).mean().to_numpy()
    sl = np.full(len(m), np.nan)
    sl[21:] = ma200[21:] - ma200[:-21]  # 200日線が21日前より上を向いているか
    m = m.copy()
    m["idx_ma200_up"] = sl > 0
    m["idx_ma200_have"] = np.isfinite(sl)
    return m


def signal_hist(m: pd.DataFrame) -> np.ndarray:
    """src/report/market_signal.py と同じ判定を過去ぶんに当てる。

    本番は「52週高値の銘柄数 > 52週安値の銘柄数」も攻めの条件に入れているが、
    安値側の系列が market.parquet に無い。ここでは高値圏の割合が1%以上を
    その代わりに置く(全期間の中央値が約4%なので、緩めの条件)。
    緩めに置くのは、攻めの範囲を広く取って不利側で評価するため。
    """
    br200 = m["br200"].to_numpy(dtype=float)
    v200 = m["idx_vs200"].to_numpy(dtype=float)
    v50 = m["idx_vs50"].to_numpy(dtype=float)
    nh = m["newhigh"].to_numpy(dtype=float)
    up = m["idx_ma200_up"].to_numpy(dtype=bool)

    out = np.full(len(m), "", dtype=object)
    red = (v200 < 0) | (br200 < 0.30)
    green = (v50 > 0) & (v200 > 0) & up & (br200 >= 0.50) & (nh >= 0.01)
    out[:] = "中立"
    out[green & ~red] = "攻め"
    out[red] = "守り"
    have = np.isfinite(br200) & np.isfinite(v200) & np.isfinite(v50) & \
        m["idx_ma200_have"].to_numpy(dtype=bool)
    out[~have] = ""
    return out


# ---------------------------------------------------------------- 売買
def build():
    close = _f("close")
    rsi = np.asarray(C.load("rsi2"))
    rs = _f("rs")
    tv = np.asarray(C.load("tv20med"))
    sd_mat = C.stop_size(STOP)

    bad = C.bad_bar()
    T = bad.shape[0]
    win = np.zeros_like(bad)
    for k in range(0, E.MAXH + 1):
        win[: T - k] |= bad[k:]

    fin = np.isfinite(close) & (close > 0)
    pop = fin & np.isfinite(rs) & (rs >= RS_MIN) & np.isfinite(tv) & (tv >= LIQ)

    m = pop & C.tradable() & np.isfinite(rsi) & (np.asarray(rsi) < 5)
    m = C.dedup(m, E.MAXH)
    ts, cs = np.nonzero(m)
    ex = E.exit_index(ts, cs, np.isfinite(rsi) & (rsi > 70), E.MAXH)
    sd = sd_mat[ts, cs].astype(np.float64)
    ok = (ex < T) & ~win[ts, cs] & np.isfinite(sd) & (sd > 0)
    ts, cs, ex, sd = ts[ok], cs[ok], ex[ok], sd[ok]
    r = (close[ex, cs].astype(np.float64) * (1 - C.COST_ONEWAY)) / \
        (close[ts, cs].astype(np.float64) * (1 + C.COST_ONEWAY)) - 1.0
    g = np.isfinite(r)
    sig = (r[g], r[g] / sd[g], ts[g])

    # 無条件買いのベースライン(同じ母集団を5日持つだけ)
    ret = C.forward_return(BASE_HOLD)
    mb = C.thin_baseline(pop & C.tradable(), BASE_HOLD)
    mb &= np.isfinite(ret) & np.isfinite(sd_mat) & (sd_mat > 0) & ~win
    tb, cb = np.nonzero(mb)
    rb = ret[tb, cb].astype(np.float64)
    base = (rb, rb / sd_mat[tb, cb].astype(np.float64), tb)
    return sig, base, pop


def row(label, sel_s, sel_b, sig, base, extra=""):
    r, R, _ = sig
    rb, _, _ = base
    n = int(sel_s.sum())
    if n < 50:
        print(f"  {label:<26} n={n:>6,}  (件数不足)")
        return
    se2 = 2 * R[sel_s].std(ddof=1) / np.sqrt(n)
    bm = rb[sel_b].mean() * 100 if sel_b.sum() > 50 else float("nan")
    take = r[sel_s].mean() * 100
    print(f"  {label:<26} n={n:>6,} 勝率 {(r[sel_s] > 0).mean()*100:4.1f}% "
          f"手取り {take:+.2f}% (無条件 {bm:+.2f}% / 上乗せ {take-bm:+.2f}%) "
          f"期待R {R[sel_s].mean():+.3f}±{se2:.3f}{extra}")


def main() -> None:
    m = market()
    sigcls = signal_hist(m)
    dts = C.dates()
    sig, base, pop = build()
    ts, tb = sig[2], base[2]
    per_day = pop.sum(axis=1)

    n_days = pd.Series(sigcls).value_counts()
    print(f"母集団: 市場より強い上位3割(RS{RS_MIN}以上) / 売買代金 {LIQ/1e8:.0f}億円以上")
    print(f"シグナル RSI(2)<5 → RSI(2)>70で降りる(上限10日) / "
          f"コスト往復 {C.COST_ONEWAY*200:.1f}%")
    print("地合いの日数: " + " / ".join(
        f"{k or '判定前'} {v:,}日" for k, v in n_days.items()))

    print("\n=== 1. 既にある地合いシグナルで切る ===")
    for lab in ("攻め", "中立", "守り"):
        d = sigcls == lab
        row(lab, d[ts], d[tb], sig, base,
            f" 母集団 {per_day[d].mean():.0f}銘柄/日")
    d = sigcls != ""
    row("(参考)全部", d[ts], d[tb], sig, base)
    d = np.isin(sigcls, ("攻め", "中立"))
    row("攻め+中立だけ買う", d[ts], d[tb], sig, base)
    d = np.isin(sigcls, ("中立", "守り"))
    row("中立+守りだけ買う(攻めを外す)", d[ts], d[tb], sig, base)
    early = dts[ts] < C.SPLIT
    eb = dts[tb] < C.SPLIT
    row("  └ 前半(〜2014)", d[ts] & early, d[tb] & eb, sig, base)
    row("  └ 後半(2015〜)", d[ts] & ~early, d[tb] & ~eb, sig, base)

    print("\n=== 2. 200日線の上にいる銘柄の割合そのもので切る ===")
    br = m["br200"].to_numpy(dtype=float)
    edges = [(0.0, 0.15), (0.15, 0.30), (0.30, 0.45),
             (0.45, 0.60), (0.60, 0.75), (0.75, 1.01)]
    for lo, hi in edges:
        d = np.isfinite(br) & (br >= lo) & (br < hi)
        row(f"割合 {lo*100:.0f}〜{hi*100:.0f}%", d[ts], d[tb], sig, base,
            f" 母集団 {per_day[d].mean():.0f}銘柄/日")

    print("\n=== 3. 50日線の上にいる銘柄の割合で切る ===")
    b5 = m["br50"].to_numpy(dtype=float)
    for lo, hi in edges:
        d = np.isfinite(b5) & (b5 >= lo) & (b5 < hi)
        row(f"割合 {lo*100:.0f}〜{hi*100:.0f}%", d[ts], d[tb], sig, base)

    print("\n=== 4. 暦の局面 × 地合いシグナル(切り方が結論を作っていないかの確認) ===")
    r, R, _ = sig
    d = dts[ts]
    print(f"{'局面':32s}" + "".join(f"{k:>22s}" for k in ("攻め", "中立", "守り")))
    for nm, a, b in REGIMES:
        cal = (d >= np.datetime64(a)) & (d <= np.datetime64(b))
        cells = []
        for lab in ("攻め", "中立", "守り"):
            sel = cal & (sigcls[ts] == lab)
            k = int(sel.sum())
            cells.append(f"{k:5,d}件 {r[sel].mean()*100:+6.2f}%" if k >= 30
                         else f"{k:5,d}件      -")
        print(f"{nm:32s}" + "".join(f"{c:>22s}" for c in cells))

    print("\n=== 5. 守りを外すと何が消えるか(月別の悪い方から20か月) ===")
    mo = pd.PeriodIndex(d, freq="M")
    df = pd.DataFrame({"mo": mo, "r": r, "cls": sigcls[ts]})
    g = df.groupby("mo").agg(n=("r", "size"), take=("r", "mean"))
    g = g[g["n"] >= 20].nsmallest(20, "take")
    print(f"{'月':10s}{'件数':>7s}{'手取り':>9s}{'うち守り':>9s}{'守り除外後':>12s}")
    for k, v in g.iterrows():
        sub = df[df["mo"] == k]
        keep = sub[sub["cls"] != "守り"]
        after = f"{keep['r'].mean()*100:+.2f}% ({len(keep)}件)" if len(keep) else "0件"
        print(f"{str(k):10s}{int(v['n']):7,d}{v['take']*100:+8.2f}%"
              f"{(sub['cls']=='守り').sum():8,d}件{after:>14s}")


if __name__ == "__main__":
    main()
