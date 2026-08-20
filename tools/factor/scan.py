#!/usr/bin/env python3
"""どの並べ方に一番差が出るかの地図を作る(2026-08-19)。

188 で「型1は指数にリターンでは勝てない」と出たので、いったん型1とミネルヴィニを
離れて、リターンそのもので指数に勝てる形を探す。

やり方は総当たり。毎月末に全銘柄をある指標で並べ、上位から下位まで10等分して
それぞれを1か月持ったら何%だったかを26年ぶん積む。指標を15本用意して、
どれに一番はっきり差が出るかを一枚の表にする。

    python tools/factor/scan.py

比べる相手は「母集団を等ウェイトで全部持った場合」= その月の全銘柄の平均。

★注意
  ・並べ方は月末の終値までで決まる。買うのは翌営業日の終値。先読みしていない
  ・売買コストは入れ替えたぶんだけ引く(片道0.15%×入れ替え率)
  ・1日で±50%動く行はデータの壊れとして外す
  ・売買代金(20日中央値)1億円以上に絞る。全部入れると買えない銘柄が混ざる
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factor import core as F  # noqa: E402


def build_scores(close: np.ndarray, me: np.ndarray):
    """月末時点の並べ方を作る。すべて当日の終値までしか見ていない。

    返り値は {表示名: (スコア行列, 説明)}。スコアは「小さい順に並べる」ので、
    D1 が一番小さいグループ、D10 が一番大きいグループになる。
    """
    T, N = close.shape
    high = np.asarray(F.load("high"), dtype=np.float32)
    low = np.asarray(F.load("low"), dtype=np.float32)
    vol = np.asarray(F.load("volume"), dtype=np.float32)
    atr = np.asarray(F.load("atr"), dtype=np.float32)
    ma50 = np.asarray(F.load("ma50"), dtype=np.float32)
    ma200 = np.asarray(F.load("ma200"), dtype=np.float32)
    h250 = np.asarray(F.load("h250"), dtype=np.float32)
    l250 = np.asarray(F.load("l250"), dtype=np.float32)
    rs = np.asarray(F.load("rs"), dtype=np.float32)
    vma50 = np.asarray(F.load("vma50"), dtype=np.float32)
    volmed10 = np.asarray(F.load("volmed10"), dtype=np.float32)
    tv = np.asarray(F.load("tv20med", src=False), dtype=np.float32)
    slope = np.asarray(F.load("ma200slope21", src=False), dtype=np.float32)

    def back(k):
        """k営業日前の行番号(先頭より前は0で止める)。"""
        return np.maximum(me - k, 0)

    def ratio(a_rows, b_rows):
        with np.errstate(all="ignore"):
            return close[a_rows, :] / close[b_rows, :] - 1.0

    # 日次リターン(荒れ具合・最大日次リターン用)。壊れた行は落とす
    dret = np.full((T, N), np.nan, dtype=np.float32)
    with np.errstate(all="ignore"):
        dret[1:] = (close[1:] / close[:-1] - 1.0).astype(np.float32)
    dret[np.abs(dret) > F.BAD_JUMP] = np.nan

    def win_stat(k, how):
        """直近k営業日の日次リターンから統計量を取る。"""
        out = np.full((len(me), N), np.nan)
        for i, t in enumerate(me):
            a = max(t - k + 1, 0)
            w = dret[a:t + 1]
            with np.errstate(all="ignore"):
                if how == "std":
                    out[i] = np.nanstd(w, axis=0)
                elif how == "max":
                    out[i] = np.nanmax(w, axis=0)
                elif how == "skew":
                    mu = np.nanmean(w, axis=0)
                    sd = np.nanstd(w, axis=0)
                    out[i] = np.nanmean((w - mu) ** 3, axis=0) / (sd ** 3 + 1e-12)
        return out

    with np.errstate(all="ignore"):
        S = {
            "12-1ヶ月モメンタム": (
                ratio(back(21), back(252)),
                "1年前から1ヶ月前までの上がり方。世界で一番再現されている軸"),
            "6-1ヶ月モメンタム": (
                ratio(back(21), back(126)),
                "半年前から1ヶ月前まで"),
            "12ヶ月モメンタム(直近込み)": (
                ratio(me, back(252)),
                "直近1ヶ月も含めた1年の上がり方"),
            "直近1ヶ月リターン": (
                ratio(me, back(21)),
                "短期の行きすぎ。下がったほうが良いなら反転が効いている"),
            "直近1週リターン": (
                ratio(me, back(5)),
                "もっと短い行きすぎ"),
            "52週高値からの位置": (
                close[me] / np.where(h250[me] > 0, h250[me], np.nan),
                "高値に近いほど1.0。ミネルヴィニの中心にある考え方"),
            "52週安値からの位置": (
                close[me] / np.where(l250[me] > 0, l250[me], np.nan),
                "安値からどれだけ離れたか"),
            "200日線からの位置": (
                close[me] / np.where(ma200[me] > 0, ma200[me], np.nan) - 1.0,
                "長期線の上にどれだけいるか"),
            "50日線からの位置": (
                close[me] / np.where(ma50[me] > 0, ma50[me], np.nan) - 1.0,
                "中期線の上にどれだけいるか"),
            "200日線の傾き": (slope[me], "200日線の21日前比。トレンドの向き"),
            "荒れ具合(60日)": (
                win_stat(60, "std"), "日々の値動きの散らばり。小さいほど退屈な株"),
            "値幅(ATR%)": (
                atr[me] / np.where(close[me] > 0, close[me], np.nan),
                "1日の値幅の割合。荒れ具合の別の測り方"),
            "直近1ヶ月の最大日次上げ": (
                win_stat(21, "max"),
                "1日でどれだけ跳ねたか。宝くじ的な株ほど大きい"),
            "売買代金(規模の代用)": (
                np.log(np.maximum(tv[me], 1.0)),
                "20日中央値。小さいほど小型株。時価総額の代用"),
            "出来高の増え方": (
                volmed10[me] / np.where(vma50[me] > 0, vma50[me], np.nan),
                "直近10日の出来高が50日平均の何倍か"),
            "RS(既存の相対力)": (rs[me], "ミネルヴィニ側で使っている相対力"),
        }
    return S


def main() -> None:
    dts = F.dates()
    close = F.close_f64()
    me = F.month_ends(dts)
    bad = F.bad_bar(close)
    r = F.fwd(close, me, bad, delay=1)
    u = F.univ(me)[:-1]
    mdts = dts[me[1:]]

    mkt = F.market_month(u, r)
    base = F.summary(mkt, mdts)
    print("=== 比べる相手: 母集団を等ウェイトで全部持つ ===")
    print(f"  {str(dts[me[0]])[:10]} 〜 {str(dts[me[-1]])[:10]}  "
          f"{len(mdts)}ヶ月  1ヶ月あたり銘柄数 {u.sum(1).mean():.0f}")
    print(f"  月平均 {base['mean']*100:+.2f}%  年利 {base['cagr']*100:.1f}%  "
          f"最大の落ち込み {base['mdd']*100:.1f}%  {base['final']:.1f}倍  "
          f"前半 {base['early']*100:+.2f}% 後半 {base['late']*100:+.2f}%")
    print("  ★売買コスト・配当は入っていない理想値。この相手に勝つのが目標")

    S = build_scores(close, me)

    print("\n=== 10等分したときの年利(D1=一番小さい / D10=一番大きい) ===")
    print("   ★コスト込み。入れ替えたぶんだけ引いてある")
    head = "".join(f"{'D'+str(i+1):>7s}" for i in range(F.NQ))
    print(f"{'並べ方':26s}{head}{'D10-D1':>10s}{'向き':>6s}")

    results = {}
    for name, (score, note) in S.items():
        sc = np.asarray(score, dtype=np.float64)[:-1]
        q, memb = F.quantile_returns(sc, u, r, F.NQ)
        cells = []
        cagrs = []
        for k in range(F.NQ):
            to = F.turnover(memb, k)
            c = F.COST_ONEWAY * 2 * (to if np.isfinite(to) else 1.0)
            s = F.summary(q[:, k], mdts, cost=c)
            cagrs.append(s["cagr"])
            cells.append(f"{s['cagr']*100:>6.1f}%")
        spread = np.nanmean(q[:, -1] - q[:, 0]) * 100
        arrow = "上が強" if cagrs[-1] > cagrs[0] else "下が強"
        results[name] = (q, memb, cagrs, spread, note)
        print(f"{name:26s}" + "".join(cells) + f"{spread:>+9.2f}%{arrow:>8s}")

    # ------------------------------------------------ 差の大きい順に詳しく
    print("\n=== D10とD1の差が大きい順に、上位6本を詳しく ===")
    order = sorted(results, key=lambda k: -abs(results[k][3]))
    for name in order[:6]:
        q, memb, cagrs, spread, note = results[name]
        strong = F.NQ - 1 if cagrs[-1] > cagrs[0] else 0
        weak = 0 if strong else F.NQ - 1
        to = F.turnover(memb, strong)
        c = F.COST_ONEWAY * 2 * (to if np.isfinite(to) else 1.0)
        sa = F.summary(q[:, strong], mdts, cost=c)
        sb = F.summary(q[:, weak], mdts,
                       cost=F.COST_ONEWAY * 2 * F.turnover(memb, weak))
        print(f"\n--- {name} ---")
        print(f"    {note}")
        print(f"  強い側(D{strong+1}) 年利 {sa['cagr']*100:5.1f}%  "
              f"落ち込み {sa['mdd']*100:6.1f}%  {sa['final']:6.1f}倍  "
              f"月平均 {sa['mean']*100:+.2f}%±{2*sa['se']*100:.2f}  "
              f"前半 {sa['early']*100:+.2f}% 後半 {sa['late']*100:+.2f}%  "
              f"入替 {to*100:.0f}%")
        print(f"  弱い側(D{weak+1}) 年利 {sb['cagr']*100:5.1f}%  "
              f"落ち込み {sb['mdd']*100:6.1f}%  {sb['final']:6.1f}倍  "
              f"月平均 {sb['mean']*100:+.2f}%±{2*sb['se']*100:.2f}  "
              f"前半 {sb['early']*100:+.2f}% 後半 {sb['late']*100:+.2f}%")
        print(f"  指数との差    年利 {(sa['cagr']-base['cagr'])*100:+.1f}%  "
              f"月平均 {(sa['mean']-base['mean'])*100:+.2f}%  "
              f"勝った月 {np.nanmean(q[:, strong] > mkt)*100:.1f}%")
        # 坂になっているか
        print("  分位の年利: " + " ".join(f"{v*100:.1f}" for v in cagrs))


if __name__ == "__main__":
    main()
