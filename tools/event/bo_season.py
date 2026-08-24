#!/usr/bin/env python3
"""ミネルヴィニ側のブレイクアウトにも「決算期の外は毒」が当てはまるかを見る(2026-08-24)。

verify.py で出たのは「決算期の外で、出来高が膨らんで大きく上がった日」の後は
3ヶ月で市場平均に -2.85% 負ける、というもの。26年・全ての決め方で同じ向きに出た。

ここで確かめたいのは1点だけ:
**ベースを作った後のブレイクアウトに絞っても、同じことが起きるのか。**
起きるなら、スクリーナーに「決算期の外は見送る」を足す理由になる。

`data/audit_cache/setups_vcp.parquet` の bo=1(ブレイクアウトした日)を使う。

  python3 tools/event/bo_season.py
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

    d = pd.read_parquet(E.SRC / "setups_vcp.parquet")
    # ★先読みの罠: 行の `t` は「型を判定した日」で、ブレイクアウトが実際に起きたのは
    #   その後の `bo_t` の日。`t` を買った日にすると「これから抜ける」と知った上で
    #   抜ける前に買うことになる。必ず `bo_t` を使う。
    bo_t = np.load(E.SRC / "bo_t.npy", allow_pickle=True)
    d = d.assign(bo_day=bo_t)
    d = d[(d["bo"] == 1) & (d["bo_day"] >= 0)]
    lag = (d["bo_day"] - d["t"]).to_numpy()
    print(f"型を判定した日から実際に抜けるまで 中央{np.median(lag):.0f}日 "
          f"最大{lag.max()}日 → ここを使わないと先読みになる")
    t = d["bo_day"].to_numpy()
    i = d["ci"].to_numpy()
    ok = (t >= 0) & (t < b.T) & (i >= 0) & (i < b.N)
    t, i, d = t[ok], i[ok], d[ok]
    prevu = np.zeros_like(b.u)
    prevu[1:] = b.u[:-1]
    inu = b.u[t, i] & prevu[t, i] & ~b.bad[t, i]
    t, i, d = t[inu], i[inu], d[inu]
    print(f"ブレイクアウトした日 {t.size}件  {dts[t].min()}〜{dts[t].max()}")
    print("★比べる相手は同じ日に1銘柄を同じ期間持った場合の平均。コストは引く前\n")

    sea = np.isin(mo, (2, 5, 8, 11)) & (dy <= 20)
    is_sea = sea[t]
    vm = E.f64("volmed10")
    with np.errstate(all="ignore"):
        vr = b.volume[t, i] / vm[t, i]
    r0 = b.ret[t, i]
    rs = d["rs"].to_numpy()
    st = d["vcp_status"].to_numpy()

    def show(nm, sel, holds=(5, 20, 60)):
        if sel.sum() == 0:
            print(f"{nm:<30}  該当なし")
            return
        for h in holds:
            r, mk = b.fwd(t[sel], i[sel], h)
            print(E.line(f"{nm} +{h}日", E.stat(r - mk, t[sel], dts)))

    print("=" * 118)
    print("1. ブレイクアウト全体を、決算期かどうかで割る")
    print("=" * 118)
    print(E.HEAD)
    show("ブレイクアウト全部", np.ones(t.size, dtype=bool))
    print("-" * 118)
    show("うち決算期(2/5/8/11月の1〜20日)", is_sea)
    show("うち決算期の外", ~is_sea)

    print()
    print("=" * 118)
    print("2. 出来高が膨らんだブレイクアウトに絞る(verify.py と同じ形)")
    print("=" * 118)
    print(E.HEAD)
    for v in (2.0, 3.0, 5.0):
        big = np.isfinite(vr) & (vr >= v)
        show(f"決算期  出来高{v:g}倍", is_sea & big, holds=(20, 60))
        show(f"決算期外 出来高{v:g}倍", ~is_sea & big, holds=(20, 60))
        print("-" * 118)

    print()
    print("=" * 118)
    print("3. VCPの型が整っているものだけに絞る")
    print("=" * 118)
    print(E.HEAD)
    good = st == "CONFIRMED"
    print(f"(vcp_status の内訳: "
          f"{pd.Series(st).value_counts().head(6).to_dict()})")
    show("型が整った 決算期", good & is_sea, holds=(20, 60))
    show("型が整った 決算期外", good & ~is_sea, holds=(20, 60))
    print("-" * 118)
    strong = np.isfinite(rs) & (rs >= 90)
    show("勢い上位(RS90以上) 決算期", strong & is_sea, holds=(20, 60))
    show("勢い上位(RS90以上) 決算期外", strong & ~is_sea, holds=(20, 60))

    print()
    print("=" * 118)
    print("4. 条件2: 決算期の決め方を変える")
    print("=" * 118)
    print(E.HEAD)
    for dd in (10, 15, 20, 25):
        s = (np.isin(mo, (2, 5, 8, 11)) & (dy <= dd))[t]
        show(f"決算期外(1〜{dd}日を決算期)", ~s & np.isfinite(vr) & (vr >= 3),
             holds=(20, 60))

    print()
    print("=" * 118)
    print("5. 月ごとの内訳(出来高3倍以上のブレイクアウト +60日)")
    print("=" * 118)
    big = np.isfinite(vr) & (vr >= 3)
    r, mk = b.fwd(t[big], i[big], 60)
    e = r - mk
    m2 = mo[t[big]]
    dd2 = dy[t[big]]
    fin = np.isfinite(e)
    key = [f"{a:02d}月{'前半' if c <= 15 else '後半'}"
           for a, c in zip(m2[fin], dd2[fin])]
    g = pd.Series(e[fin]).groupby(key)
    print(pd.DataFrame({"件数": g.size(), "超過%": g.mean() * 100}).round(2).to_string())


if __name__ == "__main__":
    main()
