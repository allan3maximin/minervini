#!/usr/bin/env python3
"""指数の乗り降りだけで勝てるかを総当たりする(2026-08-19)。

189 で「どの銘柄を選ぶか」では勝てないと出た。ならば逆に、
**銘柄は選ばず母集団を全部持って、持つか降りるかだけを切り替える**形を試す。

比べる相手は「ずっと持ちっぱなし」。降りている間は現金(0%)。
降りる判断の材料は、価格と出来高の行列から作れるものだけ:

  ・指数が長期線/中期線の上にいるか
  ・長期線が上を向いているか
  ・指数の直近リターンがプラスか(1/3/6/12ヶ月)
  ・長期線の上にいる銘柄の割合(市場の広がり)
  ・52週高値を更新した銘柄数 − 52週安値を更新した銘柄数
  ・指数の荒れ具合が高すぎないか
  ・指数が直近の山からどれだけ落ちているか

    python tools/factor/timing.py

★判断は当日の終値までで決まり、実際に持ち方を変えるのは翌営業日から(先読みしない)。
★乗り換えのたびに片道0.15%引く(791銘柄の等ウェイトを組み替える前提の厳しめの数字)。
  参考に片道0.05%の場合も出す。

★大前提の限界: このデータには今も上場している会社しか入っていない(消えた銘柄0件)。
  年利の絶対値は実際より良く出る。信用できるのは「持ちっぱなし」との比較だけ。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factor import core as F  # noqa: E402

COST_SWITCH = 0.0015     # 乗り換え片道
COST_LIGHT = 0.0005      # 参考(ETFなど安く回せる場合)


# ---------------------------------------------------------------------------
# 土台
# ---------------------------------------------------------------------------

def daily_index(close: np.ndarray, u: np.ndarray) -> np.ndarray:
    """母集団を等ウェイトで全部持ったときの日次リターン。"""
    T = close.shape[0]
    prev = np.full_like(close, np.nan)
    prev[1:] = close[:-1]
    with np.errstate(all="ignore"):
        d = close / prev - 1.0
    d[np.abs(d) > F.BAD_JUMP] = np.nan
    ok = u & np.isfinite(d)
    cnt = ok.sum(1)
    s = np.where(ok, d, 0.0).sum(1)
    out = np.full(T, np.nan)
    m = cnt >= 100
    out[m] = s[m] / cnt[m]
    return out


def ma(x: np.ndarray, k: int) -> np.ndarray:
    """単純移動平均。足りないところは nan。"""
    s = pd.Series(x)
    return s.rolling(k, min_periods=k).mean().to_numpy()


def run(sig: np.ndarray, iret: np.ndarray, cost: float):
    """sig(当日終値時点の判断)で翌日から持ち方を変える。資産曲線を返す。"""
    T = len(iret)
    pos = np.zeros(T)
    pos[1:] = np.where(np.isfinite(sig[:-1]), sig[:-1].astype(float), 1.0)
    pos[0] = 1.0
    r = np.where(np.isfinite(iret), iret, 0.0) * pos
    sw = np.abs(np.diff(np.concatenate([[1.0], pos])))
    r -= sw * cost
    return np.cumprod(1.0 + r), pos, float(sw.sum())


def stat(eq: np.ndarray, dts: np.ndarray, pos: np.ndarray, sw: float) -> dict:
    yrs = len(eq) / 252.0
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    mo = pd.PeriodIndex(dts, freq="M")
    ms = pd.Series(eq, index=mo).groupby(level=0).last().pct_change()
    md = np.asarray([np.datetime64(str(p)) for p in ms.index])
    early = md < np.datetime64("2015-01")
    return dict(cagr=eq[-1] ** (1 / yrs) - 1, mdd=float(dd.min()), final=float(eq[-1]),
                inmkt=float(pos.mean()), sw=sw / yrs,
                early=float(np.nanmean(ms.to_numpy()[early])),
                late=float(np.nanmean(ms.to_numpy()[~early])),
                ms=ms)


HEAD = (f"{'降り方':34s}{'年利':>8s}{'落ち込み':>10s}{'倍率':>9s}"
        f"{'相場にいた':>11s}{'年の乗換':>10s}{'前半':>9s}{'後半':>9s}{'差':>9s}")


def line(nm, s, base=None):
    d = f"{(s['cagr']-base['cagr'])*100:>+8.1f}%" if base else " " * 9
    print(f"{nm:34s}{s['cagr']*100:>7.1f}%{s['mdd']*100:>9.1f}%{s['final']:>8.1f}倍"
          f"{s['inmkt']*100:>10.0f}%{s['sw']:>9.1f}回{s['early']*100:>+8.2f}%"
          f"{s['late']*100:>+8.2f}%{d}")


# ---------------------------------------------------------------------------

def main() -> None:
    dts = F.dates()
    close = F.close_f64()
    inu = np.asarray(F.load("inuniv"), dtype=bool)
    tv = np.asarray(F.load("tv20med", src=False), dtype=np.float32)
    ma200 = np.asarray(F.load("ma200"), dtype=np.float32)
    h250 = np.asarray(F.load("h250"), dtype=np.float32)
    l250 = np.asarray(F.load("l250"), dtype=np.float32)

    u = inu & np.isfinite(close) & (close > 0) & np.isfinite(tv) & (tv >= F.LIQ_MIN)
    iret = daily_index(close, u)
    lvl = np.cumprod(1.0 + np.where(np.isfinite(iret), iret, 0.0))

    print("=== 比べる相手: 母集団を等ウェイトで全部持ちっぱなし ===")
    eq0, pos0, sw0 = run(np.ones(len(iret), dtype=bool), iret, COST_SWITCH)
    base = stat(eq0, dts, pos0, sw0)
    print(f"  {str(dts[0])[:10]} 〜 {str(dts[-1])[:10]}  {len(dts)}日  "
          f"1日あたり銘柄数 {u.sum(1).mean():.0f}")
    print(HEAD)
    line("持ちっぱなし", base)
    print("  ★売買コストも配当も入っていない理想値。この相手を超えるのが目標\n")

    # ------------------------------------------------------------ 材料を作る
    ma50i, ma200i = ma(lvl, 50), ma(lvl, 200)
    slope = np.full(len(lvl), np.nan)
    slope[21:] = ma200i[21:] - ma200i[:-21]

    def back(k):
        out = np.full(len(lvl), np.nan)
        out[k:] = lvl[k:] / lvl[:-k] - 1.0
        return out

    okma = u & np.isfinite(ma200) & (ma200 > 0)
    with np.errstate(all="ignore"):
        above = (close > ma200) & okma
    br = np.where(okma.sum(1) >= 100, above.sum(1) / np.maximum(okma.sum(1), 1), np.nan)
    br50 = ma(br, 50)

    with np.errstate(all="ignore"):
        nh = ((close >= h250 * 0.999) & u).sum(1)
        nl = ((close <= l250 * 1.001) & u).sum(1)
    hl = ma(np.where(u.sum(1) >= 100, (nh - nl) / np.maximum(u.sum(1), 1), np.nan), 21)

    vol = pd.Series(iret).rolling(60, min_periods=60).std().to_numpy()
    vol_med = pd.Series(vol).rolling(1000, min_periods=250).median().to_numpy()

    peak = np.maximum.accumulate(lvl)
    ddx = lvl / peak - 1.0

    # ------------------------------------------------------------ 1本ずつ
    print("=== 1. 材料を1本ずつ試す(乗り換え片道0.15%) ===")
    print(HEAD)
    sigs = {
        "指数が200日線の上のときだけ持つ": lvl > ma200i,
        "指数が50日線の上のときだけ持つ": lvl > ma50i,
        "200日線が上向きのときだけ持つ": slope > 0,
        "直近1ヶ月がプラスのときだけ持つ": back(21) > 0,
        "直近3ヶ月がプラスのときだけ持つ": back(63) > 0,
        "直近6ヶ月がプラスのときだけ持つ": back(126) > 0,
        "直近12ヶ月がプラスのときだけ持つ": back(252) > 0,
        "200日線の上の銘柄が5割超のとき": br > 0.50,
        "200日線の上の銘柄が4割超のとき": br > 0.40,
        "その割合が50日平均を超えたとき": br > br50,
        "新高値が新安値より多いとき": hl > 0,
        "荒れ具合が普段より低いとき": vol < vol_med,
        "山から10%以上落ちていないとき": ddx > -0.10,
        "山から20%以上落ちていないとき": ddx > -0.20,
    }
    res = {}
    for nm, s in sigs.items():
        eq, pos, sw = run(np.asarray(s), iret, COST_SWITCH)
        st = stat(eq, dts, pos, sw)
        res[nm] = st
        line("  " + nm, st, base)

    # ------------------------------------------------------------ 遅らせる
    print("\n=== 2. だましを減らす: 条件がN日続いたときだけ動く(200日線の上) ===")
    print(HEAD)
    raw = np.asarray(lvl > ma200i)
    for k in (1, 5, 10, 21, 42):
        s = pd.Series(raw.astype(float)).rolling(k, min_periods=k).min().to_numpy() > 0.5
        out = pd.Series((~raw).astype(float)).rolling(k, min_periods=k).min().to_numpy() > 0.5
        sig = np.full(len(raw), np.nan)
        cur = True
        for i in range(len(raw)):
            if s[i]:
                cur = True
            elif out[i]:
                cur = False
            sig[i] = cur
        eq, pos, sw = run(sig.astype(bool), iret, COST_SWITCH)
        line(f"  {k}日続いたら切り替える", stat(eq, dts, pos, sw), base)

    # ------------------------------------------------------------ 月1回だけ
    print("\n=== 3. 毎日ではなく月末にだけ判断する(乗り換え回数を減らす) ===")
    print(HEAD)
    me = F.month_ends(dts)
    mmask = np.zeros(len(dts), dtype=bool)
    mmask[me] = True
    for nm in ("指数が200日線の上のときだけ持つ", "200日線が上向きのときだけ持つ",
               "直近12ヶ月がプラスのときだけ持つ", "200日線の上の銘柄が5割超のとき"):
        raw = np.asarray(sigs[nm])
        sig = np.full(len(raw), np.nan)
        cur = True
        for i in range(len(raw)):
            if mmask[i] and np.isfinite(float(raw[i])):
                cur = bool(raw[i])
            sig[i] = cur
        eq, pos, sw = run(sig.astype(bool), iret, COST_SWITCH)
        line("  " + nm, stat(eq, dts, pos, sw), base)

    # ------------------------------------------------------------ 重ねがけ
    print("\n=== 4. 2つ重ねる / 段階的に減らす ===")
    print(HEAD)
    a = np.asarray(lvl > ma200i)
    b = np.asarray(br > 0.40)
    for nm, s in (("200日線の上 かつ 銘柄の4割が長期線の上", a & b),
                  ("200日線の上 または 銘柄の4割が長期線の上", a | b)):
        eq, pos, sw = run(s, iret, COST_SWITCH)
        line("  " + nm, stat(eq, dts, pos, sw), base)
    # 段階(0/0.5/1)
    step = np.where(a & b, 1.0, np.where(a | b, 0.5, 0.0))
    T = len(iret)
    p = np.ones(T)
    p[1:] = step[:-1]
    rr = np.where(np.isfinite(iret), iret, 0.0) * p
    sw = np.abs(np.diff(np.concatenate([[1.0], p])))
    eq = np.cumprod(1.0 + rr - sw * COST_SWITCH)
    line("  2つとも○なら全部/1つなら半分/両方×なら降りる",
         stat(eq, dts, p, float(sw.sum())), base)

    # ------------------------------------------------------------ 安いコスト
    print("\n=== 5. 乗り換えが安い場合(片道0.05%。ETFで回す前提) ===")
    print(HEAD)
    for nm in ("指数が200日線の上のときだけ持つ", "200日線が上向きのときだけ持つ",
               "200日線の上の銘柄が5割超のとき", "新高値が新安値より多いとき"):
        eq, pos, sw = run(np.asarray(sigs[nm]), iret, COST_LIGHT)
        line("  " + nm, stat(eq, dts, pos, sw), base)

    # ------------------------------------------------------------ 年ごと
    best = "指数が200日線の上のときだけ持つ"
    print(f"\n=== 6. 年ごとに並べる({best}) ===")
    eq, pos, sw = run(np.asarray(sigs[best]), iret, COST_SWITCH)
    y = pd.DatetimeIndex(dts).year
    sa = pd.Series(eq0, index=y).groupby(level=0).last().pct_change()
    sb = pd.Series(eq, index=y).groupby(level=0).last().pct_change()
    print(f"    {'年':6s}{'持ちっぱなし':>13s}{'乗り降り':>11s}{'差':>9s}{'相場にいた':>12s}")
    inm = pd.Series(pos, index=y).groupby(level=0).mean()
    win = tot = 0
    for k in sa.index:
        if not (np.isfinite(sa[k]) and np.isfinite(sb[k])):
            continue
        tot += 1
        win += sb[k] > sa[k]
        print(f"    {k:<6d}{sa[k]*100:>+12.1f}%{sb[k]*100:>+10.1f}%"
              f"{(sb[k]-sa[k])*100:>+8.1f}%{inm[k]*100:>11.0f}%")
    d = ((sb - sa) * 100).dropna().sort_values()
    print(f"  勝った年 {win}/{tot}  差の中央値 {d.median():+.1f}%  "
          f"上位3年を抜いた平均 {d[:-3].mean():+.1f}%")


if __name__ == "__main__":
    main()
