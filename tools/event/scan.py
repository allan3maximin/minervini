#!/usr/bin/env python3
"""イベント第1弾: 「普段より桁違いに売買された日」のあと、どうなるか(2026-08-24)。

決算の中身そのものは2年ぶんしか無い(`data/fundamentals_auto.json`)。
2年では前半と後半に割れないので、判定基準の条件1が最初から当てられない。
そこで **値動きと出来高だけで作れるイベント** を26年ぶんで測る。

照合済みの事実(2年ぶんで確認):
  ・発表日の翌営業日に 出来高は普段の2.4倍・値幅は3.2% が中央値
    → 発表は引け後が多く、反応は翌日に出る
  ・逆に「出来高3倍以上の日」のうち決算の当日か翌日は 27.9% しかない
    → 出来高急増 ≠ 決算。決算だと名乗ってはいけない。混ざりものとして扱う

なので出すのは「決算のあとどうなるか」ではなく
**「普段より桁違いに売買された日のあと、どうなるか」**。
決算らしさを上げたい場合は暦(決算が集中する週)で絞った版も併記する。

  python3 tools/event/scan.py
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from tools.event import core as E  # noqa: E402

HOLDS = (5, 20, 60)          # 1週間 / 1ヶ月 / 3ヶ月
ROUND_TRIP = E.COST_ONEWAY * 2


def main() -> None:
    b = E.Book()
    dts = b.dts
    alld = dts.astype("datetime64[D]")
    yr = pd.DatetimeIndex(dts).year
    mo = pd.DatetimeIndex(dts).month
    dy = pd.DatetimeIndex(dts).day

    vm = E.f64("volmed10")
    with np.errstate(all="ignore"):
        vr = b.volume / vm                    # 出来高が普段の何倍か
    r0 = b.ret                                # その日の値動き

    base = b.u & np.isfinite(vr) & np.isfinite(r0) & ~b.bad
    # 前日も母集団に居たものだけ(初日は前日の値段が無い)
    prevu = np.zeros_like(b.u)
    prevu[1:] = b.u[:-1]
    base &= prevu

    print(f"母集団 {int(np.median(b.u.sum(1)))}銘柄/日  期間 {alld[0]}〜{alld[-1]}")
    print(f"市場平均(等ウェイト・毎日組み直し) 年利 "
          f"{(np.exp(b._mktcum[-1]) ** (252/b.T) - 1)*100:.2f}%")
    print("★比べる相手は「同じ日にサイコロで1銘柄選んで同じ期間持った場合の平均」。")
    print("  指数と比べると荒い銘柄が必ず不利に出るので使わない(検算は下の0番)。")
    print(f"  コストは往復{ROUND_TRIP*100:.1f}%。下の「超過」はコストを引く前\n")

    # ----------------------------------------------------------------- 0
    print("=" * 118)
    print("0. 検算: 何の条件も付けずに1銘柄買ったときの「超過」。公平ならゼロのはず")
    print("=" * 118)
    rng = np.random.default_rng(0)
    t9, i9 = E.events(base)
    s9 = rng.choice(t9.size, min(200000, t9.size), replace=False)
    t9, i9 = t9[s9], i9[s9]
    for h in HOLDS:
        r9, _ = b.fwd(t9, i9, h)
        _, mi = b.fwd(t9, i9, h, bench="index")
        _, mp = b.fwd(t9, i9, h, bench="peer")
        print(f"  +{h:>2}日   指数と比べて {np.nanmean(r9-mi)*100:+.2f}%"
              f"   同じ持ち方と比べて {np.nanmean(r9-mp)*100:+.2f}%")
    print()

    def run(nm: str, mask: np.ndarray, holds=HOLDS) -> None:
        t, i = E.events(mask)
        if t.size == 0:
            print(f"{nm:<26}  該当なし")
            return
        for h in holds:
            r, mk = b.fwd(t, i, h)
            exc = r - mk
            s = E.stat(exc, t, dts)
            E_ = f"{nm} +{h}日"
            print(E.line(E_, s))

    # ----------------------------------------------------------------- 1
    print("=" * 118)
    print("1. 出来高が普段の何倍か × その日の値動き で分ける")
    print("=" * 118)
    print(E.HEAD)
    for v in (3.0, 5.0):
        for lo, hi, tag in ((0.05, 9.9, "大幅高+5%〜"),
                            (0.00, 0.05, "小幅高0〜+5%"),
                            (-0.05, 0.0, "小幅安-5%〜0"),
                            (-9.9, -0.05, "大幅安〜-5%")):
            m = base & (vr >= v) & (r0 >= lo) & (r0 < hi)
            run(f"出来高{v:g}倍 {tag}", m)
        print("-" * 118)

    # ----------------------------------------------------------------- 2
    print()
    print("=" * 118)
    print("2. 坂か崖か: 出来高3倍以上の日を、その日の値動きで10等分")
    print("=" * 118)
    m0 = base & (vr >= 3.0)
    t0, i0 = E.events(m0)
    v0 = r0[t0, i0]
    q = np.quantile(v0, np.linspace(0, 1, 11))
    q[0], q[-1] = -np.inf, np.inf
    print(E.HEAD)
    for h in (20, 60):
        for k in range(10):
            sel = (v0 >= q[k]) & (v0 < q[k + 1])
            r, mk = b.fwd(t0[sel], i0[sel], h)
            s = E.stat(r - mk, t0[sel], dts)
            print(E.line(f"D{k+1:<2}({q[k]*100:+5.1f}〜{q[k+1]*100:+5.1f}%) +{h}日", s))
        print("-" * 118)

    # ----------------------------------------------------------------- 3
    print()
    print("=" * 118)
    print("3. 決算が集中する週だけに絞る(決算らしさを上げた版) ※条件2の当て方")
    print("=" * 118)
    # 日本の3月決算: 本決算5月上〜中旬 / 1Q 8月上〜中旬 / 2Q 11月上〜中旬 / 3Q 2月上〜中旬
    season = (((mo == 5) | (mo == 8) | (mo == 11) | (mo == 2)) & (dy <= 20))
    seasonm = np.broadcast_to(season[:, None], base.shape)
    print(E.HEAD)
    for v in (3.0, 5.0):
        run(f"決算期 出来高{v:g}倍 大幅高", base & seasonm & (vr >= v) & (r0 >= 0.05))
        run(f"決算期 出来高{v:g}倍 大幅安", base & seasonm & (vr >= v) & (r0 <= -0.05))
        run(f"決算期外 出来高{v:g}倍 大幅高",
            base & ~seasonm & (vr >= v) & (r0 >= 0.05))
        run(f"決算期外 出来高{v:g}倍 大幅安",
            base & ~seasonm & (vr >= v) & (r0 <= -0.05))
        print("-" * 118)

    # ----------------------------------------------------------------- 4
    print()
    print("=" * 118)
    print("4. 定義を変えても向きが同じか(条件2)")
    print("=" * 118)
    print(E.HEAD)
    m = base & (vr >= 3.0) & (r0 >= 0.05)
    run("基準: 出来高3倍 大幅高", m, holds=(20,))
    tv = b.tv
    for lim, tag in ((3e8, "売買代金3億以上"), (1e9, "売買代金10億以上")):
        run(f"  {tag}", m & (tv >= lim), holds=(20,))
    for d, tag in ((0, "その日の終値で買う"), (2, "2日後の終値で買う")):
        t, i = E.events(m)
        r, mk = b.fwd(t, i, 20, delay=d)
        print(E.line(f"  {tag} +20日", E.stat(r - mk, t, dts)))
    # 出来高のはかり方を変える
    vm50 = E.f64("vma50")
    with np.errstate(all="ignore"):
        vr50 = b.volume / vm50
    run("  出来高は50日平均で測る", base & np.isfinite(vr50) & (vr50 >= 3.0)
        & (r0 >= 0.05), holds=(20,))

    # ----------------------------------------------------------------- 5
    print()
    print("=" * 118)
    print("5. 年ごとの超過(出来高3倍 大幅高 +20日)。効果が消えていないか")
    print("=" * 118)
    t, i = E.events(base & (vr >= 3.0) & (r0 >= 0.05))
    r, mk = b.fwd(t, i, 20)
    exc = r - mk
    ok = np.isfinite(exc)
    g = pd.Series(exc[ok]).groupby(yr[t[ok]])
    tab = pd.DataFrame({"件数": g.size(), "超過%": g.mean() * 100}).round(2)
    print(tab.to_string())


if __name__ == "__main__":
    main()
