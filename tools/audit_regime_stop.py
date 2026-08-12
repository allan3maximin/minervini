#!/usr/bin/env python3
"""申し送り3番: ストップのATR正規化と、地合いフィルタ (2026-08-12)。

`audit_thresholds_dd.py` / `audit_base_quality.py` と同じく **別モジュール**。
本体のパラメータには一切触らない(10月のフォワード検証が終わるまで凍結)。

------------------------------------------------------------------------
なぜ「同じ日の中での比較」を使わないのか
------------------------------------------------------------------------
166 以降ずっと同日内Rで測ってきたが、**地合いフィルタだけはそれで測れない。**
同日内Rは定義上その日の平均をゼロにするので、「相場が悪い日は全部だめ」という
効果がまるごと消える。地合いの話は **そのままのR(生R)** で測るしかない。

代わりに、生Rで測ることの危うさ(良い日に固まった帯が良く見えるだけ)は、
  ・前半 / 後半で割る
  ・同じ表に同日内Rを並べて「ほぼゼロになる」ことを確認する
    (ゼロになるなら、その差は丸ごと『日の取り分』だと分かる)
で押さえる。

ストップの方は逆に、日をまたいで比べるものではないので生Rでよい。

------------------------------------------------------------------------
ストップについて確かめること
------------------------------------------------------------------------
1. 今の `1.5ATR を 3〜12% に丸める` は、丸めているせいで
   ボラティリティの低い銘柄と高い銘柄で意味が変わっていないか。
   → **ATR五分位ごとの平均Rが揃っているか**で見る。揃っていれば
     「1単位のリスク」の意味が銘柄によらず同じ、ということ。
     一番低いATRと一番高いATRの差が小さい定義ほど正規化としてまとも。
2. 幅そのものは何%が良いのか(2%〜20%を総当たり)。
3. **ATRをセットアップ日で測っている問題。**実際に買うのは
   最大20営業日あとのブレイク日なので、その間にATRは変わる。
   ブレイク日のATRで測り直すとどうなるか。

実行(`audit_thresholds_long.py` の build/feat/rs/setups が済んでいる前提):

    python tools/audit_regime_stop.py --part stop
    python tools/audit_regime_stop.py --part regime
    python tools/audit_regime_stop.py                 # 両方
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))

import audit_thresholds_dd as dd  # noqa: E402  統計の部分を借りる

CHUNK = 256  # 市場系列を作るときに一度に読む日数


def npy(name: str) -> Path:
    return WORK / f"{name}.npy"


def roll_pct_rank(v: np.ndarray, win: int = 250, min_n: int = 200) -> np.ndarray:
    """直近 win 日の中で今日が下から何割の位置にいるか(0〜1)。

    「ボラティリティが高い」は絶対値では時代によって意味が変わる
    (2008年と2017年では全体の水準が違う)ので、1年の中での位置で見る。
    今日を含む過去だけを使うので先読みにならない。
    """
    T = v.size
    out = np.full(T, np.nan)
    for t in range(T):
        s = max(0, t - win + 1)
        w = v[s : t + 1]
        w = w[np.isfinite(w)]
        if w.size < min_n or not np.isfinite(v[t]):
            continue
        out[t] = float((w <= v[t]).sum()) / w.size
    return out


# ===========================================================================
# ブレイク日のATR
# ===========================================================================

def atrp_at_breakout(B: pd.DataFrame) -> np.ndarray:
    """ブレイク当日の ATR / 終値。

    setups.parquet の `atrp` はセットアップ日の値。買うのは最大20営業日あとなので
    別物になっている可能性がある。ここで測り直す。
    """
    cache = WORK / "atrp_bo.npy"
    bt = B["bo_t"].to_numpy()
    ci = B["ci"].to_numpy()
    if cache.exists():
        v = np.load(cache)
        if v.size == len(B):
            return v
    atr = np.load(npy("atr"), mmap_mode="r")
    close = np.load(npy("close"), mmap_mode="r")
    with np.errstate(all="ignore"):
        v = (np.asarray(atr[bt, ci], dtype=np.float64)
             / np.asarray(close[bt, ci], dtype=np.float64))
    np.save(cache, v)
    return v


# ===========================================================================
# ストップ
# ===========================================================================

def stop_grid(atrp: np.ndarray) -> dict[str, np.ndarray]:
    g: dict[str, np.ndarray] = {}
    for f in (0.03, 0.05, 0.08, 0.10, 0.12):
        g[f"固定{f*100:.0f}%"] = np.full_like(atrp, f)
    for m in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        g[f"{m:g}ATR(丸めなし)"] = m * atrp
    g["1.5ATR(3-12%)★現行"] = np.clip(1.5 * atrp, 0.03, 0.12)
    g["1.5ATR(2-20%)"] = np.clip(1.5 * atrp, 0.02, 0.20)
    g["2.0ATR(3-12%)"] = np.clip(2.0 * atrp, 0.03, 0.12)
    g["2.5ATR(4-15%)"] = np.clip(2.5 * atrp, 0.04, 0.15)
    return g


def stop_row(mae, ret, sd, q, early):
    """1つのストップ定義について、成績と『ATRによる偏り』を出す。"""
    ok = np.isfinite(mae) & np.isfinite(ret) & np.isfinite(sd) & (sd > 0)
    R = np.where(mae > sd, -1.0, ret / sd)
    R = np.where(ok, R, np.nan)
    cut = np.where(ok, (mae > sd).astype(float), np.nan)
    per_q = [float(np.nanmean(R[q == i])) if (ok & (q == i)).sum() >= 200 else np.nan
             for i in range(5)]
    spread = (per_q[0] - per_q[4]
              if np.isfinite(per_q[0]) and np.isfinite(per_q[4]) else np.nan)
    return {
        "n": int(ok.sum()),
        "cut": float(np.nanmean(cut)),
        "width": float(np.nanmedian(sd[ok])),
        "R": float(np.nanmean(R)),
        "q": per_q,
        "spread": spread,
        "early": float(np.nanmean(R[early])),
        "late": float(np.nanmean(R[~early])),
    }


def section_stop(fr: dd.Frame) -> None:
    B = fr.B
    mae, ret = fr.mae, fr.ret
    early = (B["date"] < "2015-01-01").to_numpy()
    ab = atrp_at_breakout(B)

    print("\n" + "=" * 96)
    print("【ATRをいつ測るか】セットアップ日 vs ブレイク日")
    d = ab - fr.atrp
    okd = np.isfinite(d)
    print(f"  セットアップ日のATR%  中央値 {np.nanmedian(fr.atrp):.4f}")
    print(f"  ブレイク日のATR%      中央値 {np.nanmedian(ab):.4f}")
    print(f"  差(ブレイク日 − セットアップ日) 中央値 {np.nanmedian(d[okd]):+.4f}  "
          f"平均 {np.nanmean(d[okd]):+.4f}")
    with np.errstate(all="ignore"):
        rel = np.abs(d) / fr.atrp
    print(f"  ずれの大きさ |差|/元 の中央値 {np.nanmedian(rel[okd]):.1%}  "
          f"25%以上ずれている割合 {np.nanmean(rel[okd] > 0.25):.1%}")
    print("  → ブレイク待ちの最大20営業日でATRがこれだけ動く。")
    print("    今のストップは『買う前の日のボラティリティ』で幅を決めている。")

    for src_name, src in (("セットアップ日のATR", fr.atrp),
                          ("ブレイク日のATR", ab)):
        q = pd.qcut(pd.Series(src), 5, labels=False, duplicates="drop").to_numpy()
        q = np.where(np.isfinite(src), q, -1)
        print("\n" + "=" * 96)
        print(f"【ストップ定義の総当たり】ATRの測り方 = {src_name}")
        print("  刈られ率 … 10日以内に一度でもストップまで下げた割合")
        print("  R … 刈られたら -1、生き残ったら 10日リターン / ストップ幅")
        print("  ATR1〜5 … ボラティリティの低い順の五分位。**ここが揃っている定義ほど、")
        print("            『1単位のリスク』の意味が銘柄によらず同じ**ということ。")
        print("  偏り … ATR1 − ATR5。ゼロに近いほど正規化としてまとも。")
        print()
        print(f"  {'定義':20s}{'幅中央':>8s}{'刈られ率':>9s}{'平均R':>8s}"
              + "".join(f"{f'ATR{i+1}':>8s}" for i in range(5))
              + f"{'偏り':>8s}{'前半':>8s}{'後半':>8s}")
        for nm, sd in stop_grid(src).items():
            r = stop_row(mae, ret, sd, q, early)
            print(f"  {nm:20s}{r['width']:8.3f}{r['cut']:9.1%}{r['R']:+8.3f}"
                  + "".join(f"{v:+8.3f}" if np.isfinite(v) else f"{'-':>8s}"
                            for v in r["q"])
                  + (f"{r['spread']:+8.3f}" if np.isfinite(r["spread"]) else f"{'-':>8s}")
                  + f"{r['early']:+8.3f}{r['late']:+8.3f}")

    # ---- 幅そのものを総当たり ----
    print("\n" + "=" * 96)
    print("【固定幅の総当たり】幅を変えるとどこで平均Rが最大になるか")
    print("  ※Rはリスク1単位あたりの利益なので、幅が違っても比べてよい単位。")
    print(f"  {'幅':>6s}{'刈られ率':>10s}{'平均R':>9s}{'前半':>9s}{'後半':>9s}"
          f"{'10日リターン平均':>18s}")
    for w in [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20]:
        ok = np.isfinite(mae) & np.isfinite(ret)
        R = np.where(ok, np.where(mae > w, -1.0, ret / w), np.nan)
        realized = np.where(ok, np.where(mae > w, -w, ret), np.nan)
        print(f"  {w:6.0%}{np.nanmean((mae > w)[ok]):10.1%}{np.nanmean(R):+9.3f}"
              f"{np.nanmean(R[early]):+9.3f}{np.nanmean(R[~early]):+9.3f}"
              f"{np.nanmean(realized):+18.3%}")
    print("  ★『10日リターン平均』は同じ株数を買った場合の実際の損益。")
    print("    Rが最大になる幅と、損益が最大になる幅は一致しない。")
    print("    Rはリスクを揃えたときの効率、損益は素の取り分。")

    # ---- 丸めの影響 ----
    print("\n" + "=" * 96)
    print("【丸め(3〜12%)が効いている範囲】")
    raw = 1.5 * fr.atrp
    lo = raw < 0.03
    hi = raw > 0.12
    mid = ~lo & ~hi & np.isfinite(raw)
    for nm, m in (("3%に切り上げられた側", lo), ("12%に切り下げられた側", hi),
                  ("丸めが効いていない", mid)):
        if m.sum() < 100:
            print(f"  {nm:22s} n={int(m.sum()):,}  (少)")
            continue
        sd_c = np.clip(raw, 0.03, 0.12)
        R_c = np.where(mae > sd_c, -1.0, ret / sd_c)
        R_r = np.where(mae > raw, -1.0, ret / raw)
        print(f"  {nm:22s} n={int(m.sum()):6,d} ({m.sum()/len(B):5.1%})  "
              f"元の幅中央 {np.nanmedian(raw[m]):.3f}  "
              f"丸めあり R={np.nanmean(R_c[m]):+.3f}  "
              f"丸めなし R={np.nanmean(R_r[m]):+.3f}")
    print("  → 丸めが実際に何%の取引を動かしているか、動かした結果良くなっているか。")


# ===========================================================================
# 地合い
# ===========================================================================

def build_market() -> pd.DataFrame:
    """ユニバース全体から市場の状態を作る。すべてその日までの情報だけで作る。"""
    cache = WORK / "market.parquet"
    if cache.exists():
        m = pd.read_parquet(cache)
        m["date"] = pd.to_datetime(m["date"])
        return m

    close = np.load(npy("close"), mmap_mode="r")
    ma50 = np.load(npy("ma50"), mmap_mode="r")
    ma200 = np.load(npy("ma200"), mmap_mode="r")
    h250 = np.load(npy("h250"), mmap_mode="r")
    atr = np.load(npy("atr"), mmap_mode="r")
    inuniv = np.load(npy("inuniv"), mmap_mode="r")
    dates = np.load(npy("dates"))
    T = close.shape[0]

    br200 = np.full(T, np.nan)
    br50 = np.full(T, np.nan)
    nh = np.full(T, np.nan)
    mvol = np.full(T, np.nan)
    rmean = np.full(T, np.nan)
    nuniv = np.zeros(T, dtype=np.int64)

    print(f"市場系列を作る… T={T:,}")
    for s in range(0, T, CHUNK):
        e = min(s + CHUNK, T)
        p = max(s - 1, 0)
        c = np.asarray(close[p:e], dtype=np.float64)
        u = np.asarray(inuniv[p:e], dtype=bool) & np.isfinite(c)
        m2 = np.asarray(ma200[p:e], dtype=np.float64)
        m5 = np.asarray(ma50[p:e], dtype=np.float64)
        hh = np.asarray(h250[p:e], dtype=np.float64)
        aa = np.asarray(atr[p:e], dtype=np.float64)
        with np.errstate(all="ignore"):
            ret = c[1:] / c[:-1] - 1.0
            um = u[1:] & u[:-1] & np.isfinite(ret) & (np.abs(ret) < 0.5)
            av = np.where(u, aa / c, np.nan)
            hr = np.where(u, c / hh, np.nan)
        for k in range(s, e):
            i = k - p
            uu = u[i]
            n = int(uu.sum())
            nuniv[k] = n
            if n < 50:
                continue
            # 移動平均がまだ引けていない銘柄を「線の下」に数えないよう、
            # 値が有る銘柄だけを分母にする
            g2 = uu & np.isfinite(m2[i])
            g5 = uu & np.isfinite(m5[i])
            gh = uu & np.isfinite(hr[i])
            if g2.sum() >= 50:
                br200[k] = float((c[i] > m2[i])[g2].mean())
            if g5.sum() >= 50:
                br50[k] = float((c[i] > m5[i])[g5].mean())
            if gh.sum() >= 50:
                nh[k] = float((hr[i] >= 0.98)[gh].mean())
            mvol[k] = float(np.nanmedian(av[i][uu]))
            # ret の行 i-1 が close の行 i(= 全体の k 日目)への変化率
            if i - 1 >= 0:
                sel = um[i - 1]
                if sel.sum() >= 50:
                    rmean[k] = float(np.nanmean(ret[i - 1][sel]))

    idx = pd.Series(rmean).fillna(0.0)
    lvl = (1.0 + idx).cumprod().to_numpy()
    s_lvl = pd.Series(lvl)
    ma50i = s_lvl.rolling(50, min_periods=50).mean().to_numpy()
    ma200i = s_lvl.rolling(200, min_periods=200).mean().to_numpy()
    with np.errstate(all="ignore"):
        idx_vs200 = lvl / ma200i - 1.0
        idx_vs50 = lvl / ma50i - 1.0
        idx_sl = np.full(T, np.nan)
        idx_sl[21:] = lvl[21:] / lvl[:-21] - 1.0
    # 市場のボラティリティは、絶対値ではなく1年分の中での高さで見る
    mvol_rank = roll_pct_rank(mvol)
    br200_rank = roll_pct_rank(br200)

    m = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "n_univ": nuniv,
        "br200": br200,          # 200日線の上にいる銘柄の割合
        "br50": br50,            # 50日線の上にいる銘柄の割合
        "newhigh": nh,           # 52週高値の98%以上にいる銘柄の割合
        "mvol": mvol,            # 市場のボラティリティ(ATR%の中央値)
        "idx": lvl,              # 等加重の市場指数
        "idx_vs200": idx_vs200,  # 指数が200日線から何%上か
        "idx_vs50": idx_vs50,
        "idx_sl": idx_sl,        # 指数の21日変化率
        "mvol_rank": mvol_rank,  # ボラティリティの1年内での位置
        "br200_rank": br200_rank,
    })
    m.to_parquet(cache, index=False)
    print(f"  → {cache}")
    return m


MKT_SPECS = [
    ("200日線の上の銘柄の割合", "br200", [0.30, 0.45, 0.60, 0.75]),
    ("50日線の上の銘柄の割合", "br50", [0.30, 0.45, 0.60, 0.75]),
    ("52週高値圏の銘柄の割合", "newhigh", [0.01, 0.03, 0.07, 0.12]),
    ("市場指数の200日線からの乖離", "idx_vs200", [-0.05, 0.0, 0.05, 0.12]),
    ("市場指数の50日線からの乖離", "idx_vs50", [-0.03, 0.0, 0.03, 0.06]),
    ("市場指数の21日変化率", "idx_sl", [-0.03, 0.0, 0.03, 0.08]),
    ("市場のボラティリティ(1年内順位)", "mvol_rank", [0.25, 0.50, 0.75, 0.90]),
    ("200日線の上の割合(1年内順位)", "br200_rank", [0.25, 0.50, 0.75, 0.90]),
]


def section_regime(fr: dd.Frame) -> None:
    m = build_market()
    B = fr.B
    J = B[["bo_date"]].merge(m, left_on="bo_date", right_on="date", how="left")
    R = fr.R(fr.prim)
    ddv, keep = fr.dd(fr.prim, "bo")
    early = (B["date"] < "2015-01-01").to_numpy()

    print("\n" + "=" * 96)
    print("【地合い】買った日の市場の状態ごとの成績")
    print("  ★ここだけは『そのままのR』で見る。同日内Rはその日の平均をゼロにするので、")
    print("    地合いの効果が定義上消えてしまう。右端の同日内Rの列がほぼゼロなら、")
    print("    その差はまるごと『日の取り分』── つまり本物の地合いの効果。")
    print("  逆に同日内Rにも差が残るなら、それは地合いではなく")
    print("    『そういう日に出てくる銘柄の質』が違うという話になる。")

    for title, key, edges in MKT_SPECS:
        x = J[key].to_numpy()
        b, lab = dd.buckets(x, edges)
        xf = np.isfinite(x)
        print("\n  --- " + title)
        pv = np.nanpercentile(x[xf], [10, 50, 90]) if xf.sum() else [np.nan] * 3
        print(f"      分布 p10/50/90 = {np.round(pv, 3)}")
        print(f"      {'帯':>14s}{'n':>9s}{'割合':>7s}{'そのままR':>11s}"
              f"{'前半':>9s}{'後半':>9s}{'同日内R':>10s}")
        for i in range(len(lab)):
            mm = (b == i) & xf
            if mm.sum() < 200:
                print(f"      {lab[i]:>14s}{int(mm.sum()):9,d}   (少)")
                continue
            me = mm & early
            ml = mm & ~early
            print(f"      {lab[i]:>14s}{int(mm.sum()):9,d}{mm.sum()/len(B):7.1%}"
                  f"{np.nanmean(R[mm]):+11.3f}"
                  + (f"{np.nanmean(R[me]):+9.3f}" if me.sum() >= 200 else f"{'-':>9s}")
                  + (f"{np.nanmean(R[ml]):+9.3f}" if ml.sum() >= 200 else f"{'-':>9s}")
                  + f"{np.nanmean(ddv[mm & keep]):+10.3f}")

    # ---- フィルタとして使ったらどうなるか ----
    print("\n" + "=" * 96)
    print("【地合いフィルタの試算】その条件の日だけ買っていたら")
    print("  取引を減らすとその分の機会も減る。『平均Rが上がる』だけでは足りないので、")
    print("  **合計R比**(取引数 × 平均R を、無条件で全部買った場合と比べた比)も出す。")
    print("  1.00 より大きければ、減らした分を上回って効いている。")
    base_mean = float(np.nanmean(R))
    base_tot = base_mean * np.isfinite(R).sum()
    rules = [
        ("200日線の上が45%以上", J["br200"].to_numpy() >= 0.45),
        ("200日線の上が60%以上", J["br200"].to_numpy() >= 0.60),
        ("50日線の上が45%以上", J["br50"].to_numpy() >= 0.45),
        ("市場指数が200日線の上", J["idx_vs200"].to_numpy() > 0),
        ("市場指数が50日線の上", J["idx_vs50"].to_numpy() > 0),
        ("市場指数の21日変化がプラス", J["idx_sl"].to_numpy() > 0),
        ("ボラが1年内で上位25%でない", J["mvol_rank"].to_numpy() < 0.75),
        ("ボラが1年内で上位10%でない", J["mvol_rank"].to_numpy() < 0.90),
        ("指数200日線の上 かつ ボラ上位25%でない",
         (J["idx_vs200"].to_numpy() > 0) & (J["mvol_rank"].to_numpy() < 0.75)),
        ("200日線の上が45%以上 かつ 指数が200日線の上",
         (J["br200"].to_numpy() >= 0.45) & (J["idx_vs200"].to_numpy() > 0)),
    ]
    print(f"\n  無条件: n={int(np.isfinite(R).sum()):,}  平均R={base_mean:+.3f}")
    print(f"  {'条件':40s}{'残る割合':>9s}{'採る側R':>9s}{'捨てる側R':>10s}"
          f"{'合計R比':>9s}{'前半':>8s}{'後半':>8s}")
    # NaN との比較は False になるので、市場系列が作れていない日は自動的に「見送り」側。
    for nm, cond in rules:
        c = np.asarray(cond, dtype=bool)
        inm = c & np.isfinite(R)
        outm = ~c & np.isfinite(R)
        if inm.sum() < 500:
            print(f"  {nm:40s}  (少)")
            continue
        tot = float(np.nanmean(R[inm])) * inm.sum()
        print(f"  {nm:40s}{inm.sum()/np.isfinite(R).sum():9.1%}"
              f"{np.nanmean(R[inm]):+9.3f}"
              + (f"{np.nanmean(R[outm]):+10.3f}" if outm.sum() >= 200 else f"{'-':>10s}")
              + f"{tot/base_tot:9.2f}"
              + (f"{np.nanmean(R[inm & early]):+8.3f}" if (inm & early).sum() >= 200
                 else f"{'-':>8s}")
              + (f"{np.nanmean(R[inm & ~early]):+8.3f}" if (inm & ~early).sum() >= 200
                 else f"{'-':>8s}"))
    print("\n  ★『合計R比』が 1.00 前後なら、そのフィルタは")
    print("    『負ける取引を避けた』のではなく『取引数を減らしただけ』。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all", choices=["all", "stop", "regime"])
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    args = ap.parse_args()

    df = dd.load_setups(args.since, args.until)
    fr = dd.Frame(df)
    dd.header(fr, df)

    if args.part in ("all", "stop"):
        section_stop(fr)
    if args.part in ("all", "regime"):
        section_regime(fr)

    print("\n★注記: 本データは上場廃止銘柄を含まない(log.md 135)。")
    print("  地合いの悪い時期ほど消えた銘柄が多いので、")
    print("  地合いフィルタの効果はここで出る数字より **弱く見えている**可能性が高い。")


if __name__ == "__main__":
    main()
