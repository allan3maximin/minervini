#!/usr/bin/env python3
"""まだ触っていない無料の軸を測る(2026-08-20)。

189/190 で「価格の並べ方」と「指数の乗り降り」は行き止まりになった。
だが課金なしで使えるのに一度も測っていない材料がまだ残っている。

  1. 業種(sector_map.json の33業種。3,689銘柄すべてに付く)
     ★一番大事: 189 の「荒れ具合が小さいと良い」の正体が、
       ただ電力・鉄道を買っているだけなのかを切り分ける
  2. 上場からの経過年数(inuniv の立ち上がりから出せる)
  3. 株価の絶対水準(低位株)
  4. 保有期間(1週/1ヶ月/3ヶ月/6ヶ月/12ヶ月)
  5. カレンダー(月ごとの季節性)

    python tools/factor/extra.py

★大前提の限界: このデータには今も上場している会社しか入っていない(消えた銘柄0件)。
  年利の絶対値は実際より良く出る。信用できるのは同じ土俵の中での並び順と向き。
★業種は今の対応表を過去にもそのまま当てている。業種替えをした会社はその点だけずれる。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factor import core as F  # noqa: E402
from factor.combo import hold, pct  # noqa: E402


def vol_window(close: np.ndarray, me: np.ndarray, k: int = 60) -> np.ndarray:
    """直近k営業日の日次リターンの散らばり(荒れ具合)。"""
    T, N = close.shape
    dret = np.full((T, N), np.nan, dtype=np.float32)
    with np.errstate(all="ignore"):
        dret[1:] = (close[1:] / close[:-1] - 1.0).astype(np.float32)
    dret[np.abs(dret) > F.BAD_JUMP] = np.nan
    out = np.full((len(me), N), np.nan)
    for i, t in enumerate(me):
        with np.errstate(all="ignore"):
            out[i] = np.nanstd(dret[max(t - k + 1, 0):t + 1], axis=0)
    return out


def pct_within(score: np.ndarray, u: np.ndarray, grp: np.ndarray) -> np.ndarray:
    """業種の中だけで順位を0〜1に直す(業種間の偏りを消す)。"""
    out = np.full(score.shape, np.nan)
    gs = np.unique(grp[grp >= 0])
    for i in range(score.shape[0]):
        for g in gs:
            ok = u[i] & (grp == g) & np.isfinite(score[i])
            idx = np.nonzero(ok)[0]
            if idx.size < 10:
                continue
            r = np.argsort(np.argsort(score[i][idx], kind="mergesort"))
            out[i, idx] = r / max(idx.size - 1, 1)
    return out


def band(x: np.ndarray) -> float:
    n = int(np.isfinite(x).sum())
    return 2.0 * float(np.nanstd(x, ddof=1)) / np.sqrt(max(n, 1))


HEAD = (f"{'持ち方':32s}{'年利':>8s}{'落ち込み':>10s}{'超過の月平均':>14s}"
        f"{'ぶれ幅':>9s}{'前半':>9s}{'後半':>9s}{'銘柄':>8s}{'判定':>8s}")


def report(nm, mask, u, r, mdts, mkt, early):
    m, to = hold(u & mask, r)
    cst = F.COST_ONEWAY * 2 * to
    s = F.summary(m, mdts, cost=cst)
    exc = (m - cst) - mkt
    b = band(exc)
    e1, e2 = np.nanmean(exc[early]), np.nanmean(exc[~early])
    ok = (np.sign(e1) == np.sign(e2)) and (abs(np.nanmean(exc)) > b)
    print(f"{nm:32s}{s['cagr']*100:>7.1f}%{s['mdd']*100:>9.1f}%"
          f"{np.nanmean(exc)*100:>+13.3f}%{b*100:>8.3f}%{e1*100:>+8.3f}%"
          f"{e2*100:>+8.3f}%{(u & mask).sum(1).mean():>7.0f}"
          f"{'合格' if ok else '不合格':>8s}")
    return s


def main() -> None:
    dts = F.dates()
    close = F.close_f64()
    me = F.month_ends(dts)
    bad = F.bad_bar(close)
    r = F.fwd(close, me, bad, delay=1)
    u = F.univ(me)[:-1]
    mdts = dts[me[1:]]
    early = mdts < F.SPLIT
    codes = F.codes()

    print("★このデータには今も上場している会社しか入っていない(消えた銘柄0件)。")
    print("  絶対値ではなく、同じ土俵の中の並び順だけを見る。\n")

    sd = vol_window(close, me)[:-1]
    p = pct(sd, u)
    mkt, _ = hold(u, r)
    smkt = F.summary(mkt, mdts)
    print(f"比べる相手: 母集団を全部持つ  年利 {smkt['cagr']*100:.1f}%  "
          f"落ち込み {smkt['mdd']*100:.1f}%  {smkt['final']:.1f}倍  "
          f"{u.sum(1).mean():.0f}銘柄\n")

    # =================================================== 1. 業種
    sm = json.load(open(F.ROOT / "data" / "sector_map.json"))["sectors"]
    names = sorted(set(sm.values()))
    nid = {n: i for i, n in enumerate(names)}
    grp = np.array([nid.get(sm.get(c, ""), -1) for c in codes])

    print("=== 1a. 荒れ具合の上下は、どの業種に偏っているか ===")
    print("   母集団に対して何倍の比率で入っているか(1.0なら偏りなし)")
    base_share = np.array([(u & (grp == g)).sum() for g in range(len(names))],
                          dtype=float)
    base_share /= base_share.sum()
    for lab, c in (("退屈な側(下位10%)", p < 0.10), ("荒い側(上位10%)", p > 0.90)):
        sh = np.array([((u & c) & (grp == g)).sum() for g in range(len(names))],
                      dtype=float)
        sh /= sh.sum()
        with np.errstate(all="ignore"):
            rel = sh / np.where(base_share > 0, base_share, np.nan)
        o = np.argsort(-np.nan_to_num(rel))
        print(f"  {lab}: " + " / ".join(
            f"{names[g]}{rel[g]:.1f}倍" for g in o[:6]))

    print("\n=== 1b. 業種の中だけで順位を取り直しても効果が残るか ===")
    print("   (業種間の偏りを消した版。残るなら業種の言い換えではない)")
    print(HEAD)
    pw = pct_within(sd, u, grp)
    report("市場(全部持つ)", np.ones_like(u), u, r, mdts, mkt, early)
    report("  荒れ具合 上位10%を外す", p < 0.90, u, r, mdts, mkt, early)
    report("  業種の中で上位10%を外す", pw < 0.90, u, r, mdts, mkt, early)
    report("  業種の中で上位20%を外す", pw < 0.80, u, r, mdts, mkt, early)
    report("  業種の中で下位5%だけ持つ", pw < 0.05, u, r, mdts, mkt, early)

    print("\n=== 1c. 業種そのものを持ったら(26年の年利、上下5つ) ===")
    rows = []
    for g, n in enumerate(names):
        m, to = hold(u & (grp == g), r)
        if not np.isfinite(m).sum():
            continue
        s = F.summary(m, mdts, cost=F.COST_ONEWAY * 2 * (to if np.isfinite(to) else 0))
        rows.append((s["cagr"], n, s["mdd"], (u & (grp == g)).sum(1).mean()))
    rows.sort(reverse=True)
    print(f"   {'業種':16s}{'年利':>8s}{'落ち込み':>10s}{'銘柄':>7s}")
    for c, n, d, k in rows[:5] + [(None, "…", None, None)] + rows[-5:]:
        if c is None:
            print("   …")
            continue
        print(f"   {n:16s}{c*100:>7.1f}%{d*100:>9.1f}%{k:>7.0f}")

    print("\n=== 1d. 業種を毎月えらぶ(直近の強い業種に乗る) ===")
    print(HEAD)
    lvl = {}
    for g in range(len(names)):
        m, _ = hold(u & (grp == g), r)
        lvl[g] = np.where(np.isfinite(m), m, 0.0)
    L = np.vstack([lvl[g] for g in range(len(names))])          # (業種, 月)
    eqs = np.cumprod(1.0 + L, axis=1)
    for k in (3, 6, 12):
        # ★i行目のLは「i月末に買ってi+1月末に売った結果」なので、
        #   i月末の判断に使えるのは i-1 行目まで。1つずらさないと先読みになる。
        mom = np.full_like(eqs, np.nan)
        mom[:, k + 1:] = eqs[:, k:-1] / eqs[:, :-k - 1] - 1.0
        for top in (3, 8):
            pick = np.zeros(u.shape, dtype=bool)
            for i in range(u.shape[0]):
                v = mom[:, i]
                if not np.isfinite(v).any():
                    pick[i] = True
                    continue
                sel = np.argsort(-np.nan_to_num(v, nan=-9e9))[:top]
                pick[i] = np.isin(grp, sel)
            report(f"  直近{k}ヶ月で強い上位{top}業種", pick, u, r, mdts, mkt, early)

    # =================================================== 2. 経過年数・株価
    print("\n=== 2. 上場からの経過年数 / 株価の絶対水準 ===")
    inu = np.asarray(F.load("inuniv"), dtype=bool)
    firsti = np.argmax(inu, axis=0).astype(float)
    firsti[~inu.any(0)] = np.nan
    age = np.full(u.shape, np.nan)
    for i, t in enumerate(me[:-1]):
        age[i] = (t - firsti) / 252.0
    # 先頭から居る銘柄は「いつ上場したか」が分からないので外す
    age[:, firsti == 0] = np.nan

    print("   10等分したときの年利(D1=一番小さい / D10=一番大きい)")
    for nm, sc in (("上場からの経過年数", age),
                   ("株価の絶対水準", close[me][:-1])):
        q, memb = F.quantile_returns(np.asarray(sc, dtype=np.float64), u, r, F.NQ)
        cg = []
        for k in range(F.NQ):
            to = F.turnover(memb, k)
            s = F.summary(q[:, k], mdts,
                          cost=F.COST_ONEWAY * 2 * (to if np.isfinite(to) else 1.0))
            cg.append(s["cagr"] * 100)
        print(f"   {nm:16s}" + " ".join(f"{v:5.1f}%" for v in cg)
              + f"   D10-D1 {cg[-1]-cg[0]:+.1f}%")
    print(HEAD)
    pa = pct(np.asarray(age, dtype=np.float64), u)
    pp = pct(np.asarray(close[me][:-1], dtype=np.float64), u)
    report("  上場3年未満を外す", ~(pa < 0.10) | ~np.isfinite(pa), u, r, mdts, mkt, early)
    report("  株価が下位10%を外す", pp > 0.10, u, r, mdts, mkt, early)
    report("  荒れ上位10%+株価下位10%を外す",
           (p < 0.90) & (pp > 0.10), u, r, mdts, mkt, early)

    # =================================================== 3. 保有期間
    print("\n=== 3. 保有期間を変える(荒れ具合の上位10%を外す) ===")
    print("   ★入れ替えを何ヶ月ごとにするか。長いほどコストは減る")
    print(f"{'保有期間':32s}{'年利':>8s}{'落ち込み':>10s}{'超過の月平均':>14s}"
          f"{'ぶれ幅':>9s}{'前半':>9s}{'後半':>9s}{'入替':>8s}")
    for k in (1, 3, 6, 12):
        sub = np.zeros(u.shape[0], dtype=bool)
        sub[::k] = True
        mask = u & (p < 0.90)
        held = np.zeros(u.shape, dtype=bool)
        cur = None
        for i in range(u.shape[0]):
            if sub[i] or cur is None:
                cur = mask[i]
            held[i] = cur & u[i]
        m, to = hold(held, r)
        mk, _ = hold(u, r)
        cst = F.COST_ONEWAY * 2 * to
        s = F.summary(m, mdts, cost=cst)
        exc = (m - cst) - mk
        print(f"  {k}ヶ月ごとに入れ替え{'':14s}{s['cagr']*100:>7.1f}%"
              f"{s['mdd']*100:>9.1f}%{np.nanmean(exc)*100:>+13.3f}%"
              f"{band(exc)*100:>8.3f}%{np.nanmean(exc[early])*100:>+8.3f}%"
              f"{np.nanmean(exc[~early])*100:>+8.3f}%{to*100:>7.0f}%")

    # =================================================== 3.5 株価水準の罠
    print("\n=== 3.5 『株価が安い株は強い』は本物か ===")
    print("   このデータの株価は分割を後ろから割って直してある。")
    print("   例: 2021年に1株を5株にした会社は、2000年の株価も5で割られている。")
    print("   分割は株価が上がった会社がやる。つまり『昔の株価が安く見える』のは")
    print("   『そのあと上がった』の言い換えになりうる。")
    print("   → 直近ほどそのあとの分割が少ない。直近で効果が消えるなら罠。\n")
    print(f"   {'期間':14s}{'D1(安い)':>10s}{'D10(高い)':>10s}{'D1-D10':>10s}")
    q, memb = F.quantile_returns(np.asarray(close[me][:-1], dtype=np.float64),
                                 u, r, F.NQ)
    for a, b_, nm in ((1999, 2010, "2000〜2009"), (2010, 2018, "2010〜2017"),
                      (2018, 2023, "2018〜2022"), (2023, 2030, "2023〜2026"),
                      (2025, 2030, "2025〜2026")):
        yy = pd.DatetimeIndex(mdts).year
        w = (yy >= a) & (yy < b_)
        if w.sum() < 12:
            continue
        d1 = F.summary(q[w, 0], mdts[w])["cagr"] * 100
        d10 = F.summary(q[w, -1], mdts[w])["cagr"] * 100
        print(f"   {nm:14s}{d1:>9.1f}%{d10:>9.1f}%{d1-d10:>+9.1f}%")

    print("\n   経過年数も同じように期間で切る(こちらは分割と関係ない)")
    qa, _ = F.quantile_returns(np.asarray(age, dtype=np.float64), u, r, F.NQ)
    print(f"   {'期間':14s}{'D1(若い)':>10s}{'D10(古い)':>10s}{'D10-D1':>10s}")
    for a, b_, nm in ((1999, 2010, "2000〜2009"), (2010, 2018, "2010〜2017"),
                      (2018, 2023, "2018〜2022"), (2023, 2030, "2023〜2026"),
                      (2025, 2030, "2025〜2026")):
        yy = pd.DatetimeIndex(mdts).year
        w = (yy >= a) & (yy < b_)
        if w.sum() < 12:
            continue
        d1 = F.summary(qa[w, 0], mdts[w])["cagr"] * 100
        d10 = F.summary(qa[w, -1], mdts[w])["cagr"] * 100
        print(f"   {nm:14s}{d1:>9.1f}%{d10:>9.1f}%{d10-d1:>+9.1f}%")

    print("\n   経過年数: 決め方を変えても向きが同じか(上場3年未満を外す)")
    print(HEAD)
    for liq, nm in ((3e8, "売買代金の下限を3億円に"), (1e9, "下限を10億円に")):
        uu = F.univ(me, liq_min=liq)[:-1]
        mk, _ = hold(uu, r)
        pa2 = pct(np.asarray(age, dtype=np.float64), uu)
        report(f"  {nm}", ~(pa2 < 0.10) | ~np.isfinite(pa2), uu, r, mdts, mk, early)
    for dl, nm in ((0, "月末の終値で買う(先読み)"), (2, "2日ずらして買う")):
        rr = F.fwd(close, me, bad, delay=dl)
        mk, _ = hold(u, rr)
        report(f"  {nm}", ~(pa < 0.10) | ~np.isfinite(pa), u, rr, mdts, mk, early)
    report("  上場5年未満を外す", ~(pa < 0.20) | ~np.isfinite(pa), u, r, mdts,
           mkt, early)
    report("  荒れ上位10%+上場3年未満を外す",
           (p < 0.90) & (~(pa < 0.10) | ~np.isfinite(pa)), u, r, mdts, mkt, early)

    # =================================================== 4. カレンダー
    print("\n=== 4. 月ごとの季節性(母集団を全部持ったときの月平均) ===")
    mo = pd.DatetimeIndex(mdts).month
    df = pd.DataFrame({"m": mo, "r": mkt}).groupby("m")["r"]
    print(f"   {'月':>4s}{'月平均':>10s}{'ぶれ幅':>9s}{'勝った月':>10s}{'件数':>7s}")
    for k, v in df:
        x = v.to_numpy()
        print(f"   {k:>3d}月{np.nanmean(x)*100:>+9.2f}%{band(x)*100:>8.2f}%"
              f"{np.nanmean(x>0)*100:>9.0f}%{np.isfinite(x).sum():>7d}")


if __name__ == "__main__":
    main()
