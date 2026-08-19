#!/usr/bin/env python3
"""型1の母集団を作り直す(2026-08-19)。

181 の結論: 「200日線の上・200日線が上向き」という母集団条件がミネルヴィニと
同じなので、下げ相場では両方いっぺんに玉が無くなる。分散にならない。

そこでこの条件を外す。ただし外しっぱなしだと落ちるナイフを掴むので、
**代わりの歯止めを何にするか**を横並びで測る。先に1つ選ばない。

    python tools/meanrev/population.py

相場の良し悪しは指数が200日線の上か下かで切る(audit_cache/market.parquet の
idx_vs200)。これは当日に分かる値なので、実際にフィルタとして使える。
全6630日のうち下が2315日(35%)。

各行に「無条件買い」のベースラインを並べる。手取りがプラスでも
それが相場の追い風なら意味がないので、上乗せぶんを分けて見るため。
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
BASE_HOLD = 5  # ベースラインの保有日数。シグナル側の平均保有(約5日)に合わせる


def _f(name: str, src: bool = True) -> np.ndarray:
    return np.asarray(C.load(name, src=src), dtype=np.float32)


def market_bull() -> np.ndarray:
    """その日、指数が200日線の上か。NaN(算出前)は除外できるよう別で返す。"""
    m = pd.read_parquet(C.SRC / "market.parquet")
    dts = C.dates()
    assert len(m) == dts.size, (len(m), dts.size)
    v = m["idx_vs200"].to_numpy(dtype=float)
    return np.isfinite(v), v >= 0.0


def pop_defs() -> list[tuple[str, np.ndarray]]:
    close = _f("close")
    ma50 = _f("ma50")
    ma200 = _f("ma200")
    h250 = _f("h250")
    rs = _f("rs")
    slope = np.asarray(C.load("ma200slope21"), dtype=np.float32)

    fin = np.isfinite(close) & (close > 0)
    with np.errstate(all="ignore"):
        from_high = close / h250 - 1.0

    a200 = fin & np.isfinite(ma200) & (close > ma200)
    a50 = fin & np.isfinite(ma50) & (close > ma50)
    up200 = fin & np.isfinite(slope) & (slope >= 0.0)
    rs70 = fin & np.isfinite(rs) & (rs >= 70)
    rs85 = fin & np.isfinite(rs) & (rs >= 85)
    nd30 = fin & np.isfinite(from_high) & (from_high >= -0.30)
    nd25 = fin & np.isfinite(from_high) & (from_high >= -0.25)

    return [
        ("現行 200日線の上&傾き3%以上", a200 & np.isfinite(slope) & (slope >= 0.03)),
        ("200日線の上のみ", a200),
        ("200日線が上向きのみ", up200),
        ("50日線の上のみ", a50),
        ("歯止めなし(流動性だけ)", fin),
        ("市場より強い(RS70以上)", rs70),
        ("市場よりかなり強い(RS85以上)", rs85),
        ("RS70以上 & 50日線の上", rs70 & a50),
        ("RS70以上 & 年初来高値から-30%以内", rs70 & nd30),
        ("年初来高値から-25%以内のみ", nd25),
    ]


def eval_signal(pop: np.ndarray, rsi, close, sd_mat, dts, win):
    """RSI(2)<5 で入って「RSI(2)>70で降りる(上限10日)」。手取りと日付を返す。"""
    m = pop & C.tradable() & np.isfinite(rsi) & (np.asarray(rsi) < 5)
    m = C.dedup(m, E.MAXH)
    ts, cs = np.nonzero(m)
    if ts.size == 0:
        return None
    ex = E.exit_index(ts, cs, np.isfinite(rsi) & (rsi > 70), E.MAXH)
    T = close.shape[0]
    ok = (ex < T) & ~win[ts, cs]
    sd = sd_mat[ts, cs].astype(np.float64)
    ok &= np.isfinite(sd) & (sd > 0)
    ts, cs, ex, sd = ts[ok], cs[ok], ex[ok], sd[ok]
    r = (close[ex, cs].astype(np.float64) * (1 - C.COST_ONEWAY)) / \
        (close[ts, cs].astype(np.float64) * (1 + C.COST_ONEWAY)) - 1.0
    g = np.isfinite(r)
    return r[g], (r[g] / sd[g]), ts[g]


def eval_base(pop: np.ndarray, ret, sd_mat, win):
    """同じ母集団を無条件に BASE_HOLD 日持った場合。"""
    m = C.thin_baseline(pop & C.tradable(), BASE_HOLD)
    m &= np.isfinite(ret) & np.isfinite(sd_mat) & (sd_mat > 0) & ~win
    ts, cs = np.nonzero(m)
    r = ret[ts, cs].astype(np.float64)
    return r, r / sd_mat[ts, cs].astype(np.float64), ts


def row(label: str, sel_s, sel_b, sig, base, pop_days) -> None:
    r, R, _ = sig
    rb, _, _ = base
    n = int(sel_s.sum())
    if n < 50:
        print(f"  {label:<12} n={n:>6,}  (件数不足)")
        return
    se2 = 2 * R[sel_s].std(ddof=1) / np.sqrt(n)
    bm = rb[sel_b].mean() * 100 if sel_b.sum() > 50 else float("nan")
    print(f"  {label:<12} n={n:>6,} 期待R {R[sel_s].mean():+.3f} ±{se2:.3f} "
          f"勝率 {(r[sel_s] > 0).mean() * 100:4.1f}% "
          f"手取り {r[sel_s].mean() * 100:+.2f}% "
          f"(無条件 {bm:+.2f}% / 上乗せ {r[sel_s].mean() * 100 - bm:+.2f}%) "
          f"母集団 {pop_days:>4.0f}銘柄/日")


def main() -> None:
    close = _f("close")
    rsi = np.asarray(C.load("rsi2"))
    dts = C.dates()
    sd_mat = C.stop_size(STOP)
    ret = C.forward_return(BASE_HOLD)

    bad = C.bad_bar()
    T = bad.shape[0]
    win = np.zeros_like(bad)
    for k in range(0, E.MAXH + 1):
        win[: T - k] |= bad[k:]

    tv = np.asarray(C.load("tv20med"))
    liq = np.isfinite(tv) & (tv >= LIQ)

    have, bull = market_bull()

    print(f"シグナル RSI(2)<5 / 降り方 RSI(2)>70(上限10日) / "
          f"売買代金 {LIQ/1e8:.0f}億円以上 / コスト往復 {C.COST_ONEWAY*200:.1f}%")
    print(f"相場の切り方: 指数が200日線の上 {int((have & bull).sum()):,}日 / "
          f"下 {int((have & ~bull).sum()):,}日")

    for name, base_mask in pop_defs():
        pop = base_mask & liq
        sig = eval_signal(pop, rsi, close, sd_mat, dts, win)
        bas = eval_base(pop, ret, sd_mat, win)
        print(f"\n=== {name} ===")
        if sig is None:
            print("  シグナル0件")
            continue
        per_day = pop.sum(axis=1)
        for lbl, daymask in (("全期間", have),
                             ("上げ相場", have & bull),
                             ("下げ相場", have & ~bull)):
            row(lbl, daymask[sig[2]], daymask[bas[2]], sig, bas,
                per_day[daymask].mean())


if __name__ == "__main__":
    main()
