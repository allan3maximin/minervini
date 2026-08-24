#!/usr/bin/env python3
"""scan.py で出た「決算期の外での出来高急増＋大幅高は毒」を検算する(2026-08-24)。

scan.py の 3番で、同じイベントの定義なのに暦で切ると景色が反転した。

    出来高5倍で+5%以上上がった日の その後20日     決算期 +0.72%  決算期外 -1.34%
    同じく 60日                                決算期 +0.34%  決算期外 -2.85%

これが本物かを、判定基準の4条件のうち scan.py で当てきれていない
**条件2(決め方を変えても同じ向きか)と条件3(坂か崖か)** で潰す。

  python3 tools/event/verify.py
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from tools.event import core as E  # noqa: E402


def main() -> None:
    b = E.Book()
    dts = b.dts
    di = pd.DatetimeIndex(dts)
    mo, dy = di.month.values, di.day.values

    vm = E.f64("volmed10")
    with np.errstate(all="ignore"):
        vr = b.volume / vm
    r0 = b.ret
    prevu = np.zeros_like(b.u)
    prevu[1:] = b.u[:-1]
    base = b.u & prevu & np.isfinite(vr) & np.isfinite(r0) & ~b.bad

    def show(nm, mask, holds=(20, 60)):
        t, i = E.events(mask)
        if t.size == 0:
            print(f"{nm:<30}  該当なし")
            return
        for h in holds:
            r, mk = b.fwd(t, i, h)
            print(E.line(f"{nm} +{h}日", E.stat(r - mk, t, dts)))

    def season_of(days: int, months=(2, 5, 8, 11)):
        s = np.isin(mo, months) & (dy <= days)
        return np.broadcast_to(s[:, None], base.shape)

    # ------------------------------------------------------------------ 1
    print("=" * 118)
    print("1. 条件3(坂か崖か): 決算期の外で+5%以上上がった日を、出来高倍率で10等分")
    print("=" * 118)
    sea = season_of(20)
    m = base & ~sea & (r0 >= 0.05) & (vr >= 1.0)
    t, i = E.events(m)
    v = vr[t, i]
    q = np.quantile(v, np.linspace(0, 1, 11))
    q[0], q[-1] = 0.0, np.inf
    print(E.HEAD)
    for h in (20, 60):
        for k in range(10):
            sel = (v >= q[k]) & (v < q[k + 1])
            r, mk = b.fwd(t[sel], i[sel], h)
            print(E.line(f"D{k+1:<2}(出来高{q[k]:.1f}〜{q[k+1]:.1f}倍) +{h}日",
                         E.stat(r - mk, t[sel], dts)))
        print("-" * 118)

    # ------------------------------------------------------------------ 2
    print()
    print("=" * 118)
    print("2. 条件2: 決算期の決め方を変える。日数・月・上がり幅・出来高倍率を振る")
    print("=" * 118)
    print(E.HEAD)
    for d in (10, 15, 20, 25):
        s = season_of(d)
        show(f"決算期外(各月1〜{d}日を決算期)", base & ~s & (vr >= 5) & (r0 >= 0.05))
    print("-" * 118)
    for up in (0.03, 0.05, 0.08, 0.12):
        show(f"決算期外 上がり幅{up*100:.0f}%以上",
             base & ~sea & (vr >= 5) & (r0 >= up))
    print("-" * 118)
    for v_ in (2, 3, 5, 8):
        show(f"決算期外 出来高{v_}倍以上", base & ~sea & (vr >= v_) & (r0 >= 0.05))
    print("-" * 118)
    for lim, tag in ((3e8, "売買代金3億以上"), (1e9, "売買代金10億以上")):
        show(f"決算期外 {tag}", base & ~sea & (vr >= 5) & (r0 >= 0.05) & (b.tv >= lim))
    for d, tag in ((0, "当日終値で買う"), (2, "2日後の終値で買う"), (5, "5日後に買う")):
        t2, i2 = E.events(base & ~sea & (vr >= 5) & (r0 >= 0.05))
        for h in (20,):
            r, mk = b.fwd(t2, i2, h, delay=d)
            print(E.line(f"決算期外 {tag} +{h}日", E.stat(r - mk, t2, dts)))

    # ------------------------------------------------------------------ 3
    print()
    print("=" * 118)
    print("3. 出来高の条件を外したらどうなるか(効いているのは出来高か上がり幅か)")
    print("=" * 118)
    print(E.HEAD)
    show("決算期  +5%以上(出来高不問)", base & sea & (r0 >= 0.05))
    show("決算期外 +5%以上(出来高不問)", base & ~sea & (r0 >= 0.05))
    show("決算期  出来高5倍(上がり不問)", base & sea & (vr >= 5))
    show("決算期外 出来高5倍(上がり不問)", base & ~sea & (vr >= 5))

    # ------------------------------------------------------------------ 4
    print()
    print("=" * 118)
    print("4. 暦ではなく本物の発表日で切る(2024-05〜2026-05の2年ぶん・参考)")
    print("   ※2年しかないので前半後半に割れない。条件1は当てられない")
    print("=" * 118)
    import json
    ci = {c: k for k, c in enumerate(b.codes)}
    alld = dts.astype("datetime64[D]")
    f = json.load(open(E.ROOT / "data" / "fundamentals_auto.json"))
    ann = np.zeros((b.T, b.N), dtype=bool)
    for c, v in f.items():
        k = ci.get(c)
        if k is None:
            continue
        for qz in v.get("quarters") or []:
            dd = qz.get("disc_date")
            if not dd:
                continue
            j = int(np.searchsorted(alld, np.datetime64(dd)))
            # 引け後の発表が多いので、当日と翌営業日の両方を発表まわりとみなす
            for lag in (0, 1):
                if j + lag < b.T:
                    ann[j + lag, k] = True
    w = (alld >= np.datetime64("2024-05-01")) & (alld <= np.datetime64("2026-05-31"))
    cov = np.broadcast_to(w[:, None], base.shape)
    print(E.HEAD)
    show("発表まわり +5%以上", base & cov & ann & (r0 >= 0.05))
    show("発表まわり 出来高5倍+5%", base & cov & ann & (vr >= 5) & (r0 >= 0.05))
    show("発表と無関係 出来高5倍+5%", base & cov & ~ann & (vr >= 5) & (r0 >= 0.05))
    show("発表まわり 出来高5倍-5%", base & cov & ann & (vr >= 5) & (r0 <= -0.05))
    show("発表と無関係 出来高5倍-5%", base & cov & ~ann & (vr >= 5) & (r0 <= -0.05))


if __name__ == "__main__":
    main()
