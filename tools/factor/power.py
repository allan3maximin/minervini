#!/usr/bin/env python3
"""10年ぶんのデータで何か判定できるのかを先に測る(2026-08-19)。

財務や需給を足したい。しかし J-Quants の有料プランは取れる期間が決まっている。

  Free      2年(しかも12週遅れ)   0円
  ライト    5年                    1,650円/月
  スタンダード 10年 + 信用残 + 空売り比率  3,300円/月
  プレミアム すべて(約15年) + BS/PL + 配当  16,500円/月

課金する前に確かめるべきことがある。**そもそも10年で答えが出るのか。**
今ある26年ぶんの価格データを、直近5年 / 10年 / 15年 / 26年に切って同じ総当たりを
回し、「差」と「ぶれ幅(±2)」を並べる。ぶれ幅のほうが大きければ、
どんなに良い材料を足しても期間が足りなくて判定できない。

    python tools/factor/power.py

★大前提の限界: このデータには今も上場している会社しか入っていない(消えた銘柄0件)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factor import core as F  # noqa: E402
from factor.combo import hold, pct  # noqa: E402
from factor.scan import build_scores  # noqa: E402

YEARS = (5, 10, 15, 26)


def main() -> None:
    dts = F.dates()
    close = F.close_f64()
    me = F.month_ends(dts)
    bad = F.bad_bar(close)
    r = F.fwd(close, me, bad, delay=1)
    u = F.univ(me)[:-1]
    mdts = dts[me[1:]]

    S = build_scores(close, me)
    P = {k: pct(np.asarray(v[0], dtype=np.float64)[:-1], u) for k, v in S.items()}
    mkt, _ = hold(u, r)

    # 26年で一番マシだった3本と、比較用に効かなかった1本
    cands = {
        "一番荒い10%を外す": P["荒れ具合(60日)"] < 0.90,
        "一番荒い20%を外す": P["荒れ具合(60日)"] < 0.80,
        "跳ねた上位10%を外す": P["直近1ヶ月の最大日次上げ"] < 0.90,
        "(参考)モメンタム上位10%だけ持つ": P["12-1ヶ月モメンタム"] > 0.90,
    }

    print("★このデータには今も上場している会社しか入っていない(消えた銘柄0件)。\n")
    print("=== 期間を切ったとき、差はぶれ幅を超えるか ===")
    print("   差 = その持ち方と『母集団を全部持つ』の月次リターンの差の平均")
    print("   ぶれ幅 = その差のばらつき2つぶん。差がこの中に入るなら判定できない\n")

    end = mdts[-1]
    for nm, c in cands.items():
        print(f"--- {nm} ---")
        print(f"   {'期間':10s}{'月数':>7s}{'差':>11s}{'ぶれ幅(±2)':>13s}"
              f"{'差÷ぶれ幅':>12s}{'判定':>8s}")
        m, to = hold(u & c, r)
        cst = F.COST_ONEWAY * 2 * to
        exc = (m - cst) - mkt
        for y in YEARS:
            start = end - np.timedelta64(365 * y, "D")
            w = mdts >= start
            x = exc[w]
            n = int(np.isfinite(x).sum())
            if n < 12:
                continue
            mu = float(np.nanmean(x))
            b = 2.0 * float(np.nanstd(x, ddof=1)) / np.sqrt(n)
            print(f"   直近{y:>2d}年   {n:>6d}ヶ月{mu*100:>+10.3f}%{b*100:>12.3f}%"
                  f"{mu/b:>11.2f}倍{'見える' if abs(mu) > b else '見えない':>8s}")
        print()

    # ------------------------------------------------------------------
    print("=== どれくらいの差があれば、その期間で見えるのか ===")
    print("   持ち方によって月次のばらつきが違うので、2通り出す\n")
    for nm, c in (("外すだけ(700銘柄ぐらい持つ)", cands["一番荒い10%を外す"]),
                  ("上位10%だけ持つ(70銘柄ぐらい)",
                   cands["(参考)モメンタム上位10%だけ持つ"])):
        m, to = hold(u & c, r)
        x = (m - F.COST_ONEWAY * 2 * to) - mkt
        sd = float(np.nanstd(x, ddof=1))
        print(f"--- {nm}(月次のばらつき {sd*100:.3f}%) ---")
        print(f"   {'期間':12s}{'必要な差(月)':>14s}{'年に直すと':>13s}")
        for y in (5, 10, 15, 26):
            nn = y * 12
            v = 2 * sd / np.sqrt(nn)
            print(f"   {y:>2d}年({nn:>3d}ヶ月){v*100:>12.3f}%{v*12*100:>12.1f}%")
        print()

    print("=== まとめ ===")
    print("   ・26年でも『見える』と言えるものは1本も無かった")
    print("   ・期間を10年に切ると必要な差はさらに大きくなる")
    print("   ・つまり新しい材料を足しても、10年では判定そのものができない")


if __name__ == "__main__":
    main()
