#!/usr/bin/env python3
"""「良いものを選ぶ」より「悪いものを外す」ほうが効くのかを確かめる(2026-08-19)。

scan.py の地図でわかったこと:

  ・どの軸も、良いほうの端は年利 5〜7% で横並び。市場の 6.1% とほとんど変わらない
  ・ところが悪いほうの端はどれも壊滅的(荒れ具合 -8.4% / 値幅 -7.7% /
    最大日次上げ -5.4% / 出来高の増え方 -3.9% / 52週高値からの位置 -3.4%)

つまり差の正体は「勝つ銘柄を当てる」ことではなく「地雷を踏まない」ことにある。
ならば、選ぶのではなく**外して残りを全部持つ**ほうが素直なはず。ここではそれを測る。

  1. 外すだけ(残り全部を等ウェイトで持つ)。1つずつ足していく
  2. 低ボラをどこまで絞ると良いのか(上位30%→20%→10%→5%→3%)
  3. 重みを1/荒れ具合にする(選ばずに配分だけ変える)
  4. 2つの軸を掛け合わせる(5×5のマス目)
  5. 年ごとに市場と並べる

    python tools/factor/combo.py

★大前提の限界: このデータには**今も上場している会社しか入っていない**。
  倒産や上場廃止で消えた会社が1社も入っていない(実測: 消えた銘柄0件)。
  2000年末に立っている銘柄はわずか162。市場側の年利6.1%も、各分位の数字も、
  実際より良く出ている。**「市場に何%勝った」の絶対値は信用してはいけない。**
  信用できるのは同じ土俵の中での並び順と向き。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factor import core as F  # noqa: E402
from factor.scan import build_scores  # noqa: E402


def pct(score: np.ndarray, u: np.ndarray) -> np.ndarray:
    """各月ごとに、母集団の中での順位を0〜1に直す。欠けているところは nan。"""
    out = np.full(score.shape, np.nan)
    for i in range(score.shape[0]):
        ok = u[i] & np.isfinite(score[i])
        idx = np.nonzero(ok)[0]
        if idx.size < 50:
            continue
        r = np.argsort(np.argsort(score[i][idx], kind="mergesort"))
        out[i, idx] = r / max(idx.size - 1, 1)
    return out


def hold(mask: np.ndarray, r: np.ndarray, w: np.ndarray | None = None):
    """毎月 mask の銘柄を持ったときの月次リターンと入れ替え率。"""
    M = r.shape[0]
    out = np.full(M, np.nan)
    tos = []
    prev = None
    for i in range(M):
        ok = mask[i] & np.isfinite(r[i])
        idx = np.nonzero(ok)[0]
        if idx.size < 20:
            prev = None
            continue
        if w is None:
            out[i] = float(r[i][idx].mean())
        else:
            ww = w[i][idx]
            ww = np.where(np.isfinite(ww) & (ww > 0), ww, np.nan)
            if not np.isfinite(ww).any():
                continue
            ww = np.nan_to_num(ww, nan=np.nanmedian(ww))
            out[i] = float((r[i][idx] * ww).sum() / ww.sum())
        if prev is not None and prev.size:
            keep = np.intersect1d(idx, prev).size
            tos.append(1.0 - keep / idx.size)
        prev = idx
    return out, (float(np.mean(tos)) if tos else float("nan"))


def line(name, mret, mdts, to, base, n=None):
    c = F.COST_ONEWAY * 2 * (to if np.isfinite(to) else 1.0)
    s = F.summary(mret, mdts, cost=c)
    extra = f"{n:>6.0f}銘柄" if n is not None else " " * 9
    print(f"{name:34s}{s['cagr']*100:>7.1f}%{s['mdd']*100:>9.1f}%"
          f"{s['final']:>8.1f}倍{s['mean']*100:>+8.2f}%"
          f"{s['early']*100:>+8.2f}%{s['late']*100:>+8.2f}%"
          f"{to*100:>7.0f}%{extra}"
          f"{(s['cagr']-base['cagr'])*100:>+8.1f}%")
    return s


HEAD = (f"{'':34s}{'年利':>8s}{'落ち込み':>9s}{'倍率':>8s}{'月平均':>9s}"
        f"{'前半':>9s}{'後半':>9s}{'入替':>8s}{'銘柄数':>9s}{'市場との差':>10s}")


def main() -> None:
    dts = F.dates()
    close = F.close_f64()
    me = F.month_ends(dts)
    bad = F.bad_bar(close)
    r = F.fwd(close, me, bad, delay=1)
    u = F.univ(me)[:-1]
    mdts = dts[me[1:]]

    mkt, to_m = hold(u, r)
    base = F.summary(mkt, mdts)
    print("=== 比べる相手: 母集団を等ウェイトで全部持つ ===")
    print(HEAD)
    line("市場(全部持つ)", mkt, mdts, 0.0, base, u.sum(1).mean())

    S = build_scores(close, me)
    P = {k: pct(np.asarray(v[0], dtype=np.float64)[:-1], u) for k, v in S.items()}

    # ------------------------------------------------------ 1. 外すだけ
    print("\n=== 1. 悪いほうの端を外して、残りを全部持つ ===")
    print("   下の行にいくほど条件を足している(重ねがけ)")
    print(HEAD)
    cuts = [
        ("荒れ具合が上位10%", P["荒れ具合(60日)"] < 0.90),
        ("値幅が上位10%", P["値幅(ATR%)"] < 0.90),
        ("最大日次上げが上位10%", P["直近1ヶ月の最大日次上げ"] < 0.90),
        ("出来高の増え方が下位10%", P["出来高の増え方"] > 0.10),
        ("52週高値から一番遠い10%", P["52週高値からの位置"] > 0.10),
        ("直近1ヶ月が下位10%", P["直近1ヶ月リターン"] > 0.10),
    ]
    acc = u.copy()
    for nm, c in cuts:
        m, t = hold(u & c, r)
        line(f"  {nm}だけ外す", m, mdts, t, base, (u & c).sum(1).mean())
    print("  --- 重ねがけ ---")
    for nm, c in cuts:
        acc = acc & c
        m, t = hold(acc, r)
        line(f"  +{nm}", m, mdts, t, base, acc.sum(1).mean())

    # ------------------------------------------------------ 2. 低ボラの絞り
    print("\n=== 2. 荒れていない側をどこまで絞るか ===")
    print(HEAD)
    for th in (0.50, 0.30, 0.20, 0.10, 0.05, 0.03):
        c = P["荒れ具合(60日)"] < th
        m, t = hold(u & c, r)
        line(f"  荒れ具合が下位{th*100:.0f}%だけ持つ", m, mdts, t, base,
             (u & c).sum(1).mean())

    # ------------------------------------------------------ 3. 重みを変える
    print("\n=== 3. 選ばずに、配分だけ『荒れていない銘柄を厚く』する ===")
    print(HEAD)
    sd = np.asarray(S["荒れ具合(60日)"][0], dtype=np.float64)[:-1]
    with np.errstate(all="ignore"):
        w_inv = 1.0 / np.where(sd > 1e-6, sd, np.nan)
    m, t = hold(u, r, w=w_inv)
    line("  1/荒れ具合で重みづけ(全部持つ)", m, mdts, t, base, u.sum(1).mean())
    c = P["荒れ具合(60日)"] < 0.90
    m, t = hold(u & c, r, w=w_inv)
    line("  上位10%を外して1/荒れ具合", m, mdts, t, base, (u & c).sum(1).mean())

    # ------------------------------------------------------ 4. 2軸の掛け合わせ
    print("\n=== 4. 荒れ具合(縦) × もう1軸(横) の年利 ===")
    for other in ("12-1ヶ月モメンタム", "出来高の増え方", "52週高値からの位置",
                  "売買代金(規模の代用)"):
        print(f"\n--- 横: {other} (Q1=小さい 〜 Q5=大きい) ---")
        pv, po = P["荒れ具合(60日)"], P[other]
        print(f"{'荒れ具合':16s}" + "".join(f"{'Q'+str(j+1):>9s}" for j in range(5)))
        for i in range(5):
            cells = []
            for j in range(5):
                c = (pv >= i / 5) & (pv < (i + 1) / 5) & \
                    (po >= j / 5) & (po < (j + 1) / 5)
                m, t = hold(u & c, r)
                s = F.summary(m, mdts, cost=F.COST_ONEWAY * 2 *
                              (t if np.isfinite(t) else 1.0))
                cells.append(f"{s['cagr']*100:>8.1f}%")
            nm = f"Q{i+1}" + ("(退屈)" if i == 0 else "(荒い)" if i == 4 else "")
            print(f"{nm:16s}" + "".join(cells))

    # ------------------------------------------------------ 5. 年ごと
    print("\n=== 5. 採用候補を年ごとに市場と並べる ===")
    cand = u & (P["荒れ具合(60日)"] < 0.90) & (P["出来高の増え方"] > 0.10) \
        & (P["52週高値からの位置"] > 0.10)
    m, t = hold(cand, r)
    cst = F.COST_ONEWAY * 2 * t
    y = pd.DatetimeIndex(mdts).year
    ea = pd.Series(np.where(np.isfinite(mkt), mkt, 0.0), index=y)
    eb = pd.Series(np.where(np.isfinite(m), m, 0.0) - cst, index=y)
    ga = ea.groupby(level=0).apply(lambda s: (1 + s).prod() - 1)
    gb = eb.groupby(level=0).apply(lambda s: (1 + s).prod() - 1)
    print(f"    {'年':6s}{'市場':>9s}{'候補':>9s}{'差':>9s}")
    win = 0
    for k in ga.index:
        print(f"    {k:<6d}{ga[k]*100:>+8.1f}%{gb[k]*100:>+8.1f}%"
              f"{(gb[k]-ga[k])*100:>+8.1f}%")
        win += gb[k] > ga[k]
    print(f"  勝った年 {win}/{len(ga)}")


if __name__ == "__main__":
    main()
