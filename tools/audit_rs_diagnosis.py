#!/usr/bin/env python3
"""RS が逆行して見える原因を切り分ける診断(2026-08-12)。

`tools/audit_thresholds_long.py` の25年検証で、RS だけが
「低い方が期待Rが良い」という逆行を見せ、しかもストップの定義を変えると
符号が反転した。一方 H3(52週安値からの倍率)は単調に効いている。

申し送りの出発点は「H3 と RS は同じ主張をしているのだから、
壊れているのは概念ではなく RS の実装だろう」というものだった。
コードを読んだ限り **RS の実装そのものに誤りは見つからなかった**ので、
代わりに「測り方の側が RS だけ不利になっていないか」を数字で確かめる。

疑っているのは次の3つ。全部このスクリプトで測れる。

1. **RS だけがその日の中での順位で、H2/H3/H4 は生の数値**
   RS は「同じ日の他の銘柄と比べて何番目か」なので、相場が全体で
   良い日か悪い日かの情報が最初から抜けている。
   一方 lr(終値/52週安値)は生の数値のまま25年ぶんを一緒くたにして
   帯分けしている。lr が大きい標本は自動的に強気相場の日に、
   小さい標本は弱気相場の日に偏る。
   つまり **H3 の単調性は「銘柄の選び方」ではなく「相場の局面」を
   測っているだけかもしれない**。もしそうなら H3 と「局面フィルタ」は
   同じ発見を2回数えていることになる。
   → 同じ日の中だけで比べれば局面の効果は完全に消える。それで H3 が
     残るかどうかを見る。

2. **RS とATRが相関していて、期待Rの分母がATR**
   期待R = 10日リターン / (1.5×ATR%) なので、ATRが大きい銘柄ほど
   分母が大きくなり、同じ値動きでもRが小さく出る。
   RS が高い銘柄ほどATRが大きいなら、**ストップの定義を変えただけで
   符号が変わるのは当たり前**で、それは RS の良し悪しではなく
   正規化の選び方の話になる。
   → ATRを揃えた上で RS 帯を比べる。

3. **セットアップの形が RS 帯ごとに違う**
   ピボットは20日高値。強い銘柄は毎日のように20日高値を更新するので
   ピボットは52週高値のすぐそば、弱い銘柄のピボットはずっと下の
   戻り高値になる。同じ「20日高値抜け」でも中身が別物の可能性がある。
   → RS 帯ごとにセットアップの形(ピボットまでの距離・52週高値比・
     ブレイク到達率)を並べる。

★このスクリプトは診断だけで、本体のパラメータには一切触らない。
  フォワード検証中の凍結を破らないこと。

実行(`audit_thresholds_long.py` の build/feat/rs/setups が済んでいる前提):

    python tools/audit_rs_diagnosis.py --part impl      # 実装の突合
    python tools/audit_rs_diagnosis.py --part confound  # 交絡の測定
    python tools/audit_rs_diagnosis.py --part all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LONG_DIR = ROOT / "data" / "prices_long"
WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))

RS_LOOKBACKS = (63, 126, 189, 252)
RS_WEIGHTS = (2.0, 1.0, 1.0, 1.0)
MIN_TRADING_VALUE = 1e8

REGIMES = [
    ("2000-02 ITバブル崩壊", "2000-01-01", "2002-12-31"),
    ("2003-06 小泉相場", "2003-01-01", "2006-12-31"),
    ("2007-09 リーマン", "2007-01-01", "2009-12-31"),
    ("2010-12 震災・超円高", "2010-01-01", "2012-12-31"),
    ("2013-14 アベノミクス初期", "2013-01-01", "2014-12-31"),
    ("2015-17 アベノミクス中期", "2015-01-01", "2017-12-31"),
    ("2018-19 米中摩擦・年末急落", "2018-01-01", "2019-12-31"),
    ("2020    コロナ", "2020-01-01", "2020-12-31"),
    ("2021-22 グロース崩壊", "2021-01-01", "2022-12-31"),
    ("2023-24 日経高値・24年8月暴落", "2023-01-01", "2024-12-31"),
    ("2025-26 直近", "2025-01-01", "2026-12-31"),
]

# ストップの定義。audit_thresholds_long.py の「感度」表と同じ5本。
def stop_defs(atrp: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "1.5ATR(3-12%)": np.clip(1.5 * atrp, 0.03, 0.12),
        "1.5ATR(clip無)": 1.5 * atrp,
        "2.0ATR": 2.0 * atrp,
        "固定5%": np.full_like(atrp, 0.05),
        "固定8%": np.full_like(atrp, 0.08),
    }


def npy(name: str) -> Path:
    return WORK / f"{name}.npy"


def need(*names: str) -> None:
    missing = [n for n in names if not (WORK / n).exists()]
    if missing:
        sys.exit(
            f"{WORK} に {missing} が無い。先に\n"
            "  python tools/audit_thresholds_long.py --stage build / feat / rs / setups\n"
            "を流すこと。"
        )


# ===========================================================================
# part impl: RS の実装が定義どおりか
# ===========================================================================

def part_impl(n_sample: int = 30, seed: int = 0) -> None:
    need("close.npy", "rs.npy", "dates.npy", "inuniv.npy", "codes.json")
    close = np.load(npy("close"), mmap_mode="r")
    rs = np.load(npy("rs"), mmap_mode="r")
    inuniv = np.load(npy("inuniv"), mmap_mode="r")
    dates = np.load(npy("dates"))
    codes = json.loads((WORK / "codes.json").read_text(encoding="utf-8"))
    T, N = close.shape
    print(f"行列 {T} 日 × {N} 銘柄  ({pd.Timestamp(dates[0]).date()} 〜 "
          f"{pd.Timestamp(dates[-1]).date()})\n")

    # ---------------------------------------------------------------
    # 1. 本番の計算式(src/indicators.add_rs_raw)と突合する
    #    audit 側は行列で、本番側は銘柄ごとの DataFrame。同じ数字が
    #    出ないなら、25年検証で見ていた RS は本番の RS ではない。
    # ---------------------------------------------------------------
    from src.indicators import add_rs_raw  # noqa: E402

    print("=" * 78)
    print("【1】本番の add_rs_raw と audit 側の加重リターンが一致するか")
    rng = np.random.default_rng(seed)
    files = {p.stem: p for p in LONG_DIR.glob("*.parquet")}
    pool = [c for c in codes if c in files]
    picks = list(rng.choice(pool, size=min(n_sample, len(pool)), replace=False))

    worst = 0.0
    worst_code = None
    gap_rows = []
    for code in picks:
        d = pd.read_parquet(files[code])
        d["date"] = pd.to_datetime(d["date"])
        d = d.sort_values("date").reset_index(drop=True)
        prod = add_rs_raw(d[["date", "close"]].copy())["rs_raw"].to_numpy()

        ci = codes.index(code)
        col = np.asarray(close[:, ci], dtype=np.float64)
        tot = np.zeros(T)
        ok = np.ones(T, dtype=bool)
        for lb, w in zip(RS_LOOKBACKS, RS_WEIGHTS):
            sh = np.full(T, np.nan)
            sh[lb:] = col[:-lb]
            with np.errstate(all="ignore"):
                tot += w * (col / sh - 1.0)
            ok &= np.isfinite(sh) & (sh > 0)
        aud = np.where(ok, tot, np.nan)

        # 日付で突き合わせる(行列側は全銘柄の日付の和集合)
        pos = np.searchsorted(dates, d["date"].to_numpy(dtype="datetime64[ns]"))
        pos = np.clip(pos, 0, T - 1)
        a = aud[pos]
        m = np.isfinite(a) & np.isfinite(prod)
        if m.sum() == 0:
            continue
        diff = float(np.nanmax(np.abs(a[m] - prod[m])))
        if diff > worst:
            worst, worst_code = diff, code

        # 和集合の日付のうち、この銘柄に値が無い日が何日あるか。
        # audit 側の shift は「和集合の行数」でずらしているので、
        # 抜けが多い銘柄ほど「252日前」が実際には252営業日前ではなくなる。
        first = pos[0]
        span = T - first
        have = np.isfinite(col[first:]).sum()
        gap_rows.append((code, span, int(have), span - int(have)))

    print(f"  照合 {len(picks)} 銘柄  最大差 = {worst:.3e}  ({worst_code})")
    print("  → 1e-6 以下なら本番と audit は同じ式。桁が大きいなら実装が割れている。")

    g = pd.DataFrame(gap_rows, columns=["code", "和集合日数", "値のある日数", "抜け"])
    g["抜け率"] = g["抜け"] / g["和集合日数"]
    print(f"\n  和集合の日付に対する抜け率: 中央値 {g['抜け率'].median():.2%} / "
          f"最大 {g['抜け率'].max():.2%} ({g.loc[g['抜け率'].idxmax(),'code']})")
    print("  → ここが数%なら『252行前 ≒ 252営業日前』は成立している。")
    print("     十数%を超える銘柄が多いなら、shift が銘柄ごとにズレていて")
    print("     RS のリターン期間が銘柄によって違うことになる。")

    # ---------------------------------------------------------------
    # 2. 未来を覗いていないか。
    #    ある日 t の RS を、t より後ろのデータを丸ごと捨てて計算し直し、
    #    元の値と一致するかを見る。ローリングと断面順位しか使って
    #    いないはずなので、一致しなければどこかで未来が漏れている。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【2】未来のデータが混ざっていないか(打ち切って再計算して突合)")
    test_ts = rng.choice(np.arange(400, T - 5), size=8, replace=False)
    bad = 0
    for t in sorted(test_ts):
        # t より後ろの行は一切読まない。読んだ行だけで元の値が復元できれば、
        # 元の計算も t 以前しか使っていないということ。
        cur = np.asarray(close[t], dtype=np.float64)
        tot = np.zeros(N)
        ok = np.ones(N, dtype=bool)
        for lb, w in zip(RS_LOOKBACKS, RS_WEIGHTS):
            prev = (np.asarray(close[t - lb], dtype=np.float64)
                    if t - lb >= 0 else np.full(N, np.nan))
            with np.errstate(all="ignore"):
                tot += w * (cur / prev - 1.0)
            ok &= np.isfinite(prev) & (prev > 0)
        raw_t = np.where(ok, tot, np.nan)
        m = np.asarray(inuniv[t]) & np.isfinite(raw_t)
        k = int(m.sum())
        if k < 100:
            continue
        v = raw_t[m]
        r = v.argsort().argsort() + 1
        recomputed = np.clip(np.round(r / k * 98 + 1), 1, 99)
        orig = np.asarray(rs[t])[np.where(m)[0]]
        ok_n = int(np.nansum(np.abs(recomputed - orig) <= 1e-6))
        if ok_n != k:
            bad += 1
        print(f"  {pd.Timestamp(dates[t]).date()}  母集団 {k:5d} 銘柄  "
              f"一致 {ok_n}/{k}")
    print(f"  → 不一致の日 {bad} 件。0 なら未来は入っていない。")

    # ---------------------------------------------------------------
    # 3. 母集団の推移。「RS70」の意味が時代で変わっていないか。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【3】母集団(その日ユニバース)の推移と RS の中身")
    yr = pd.DatetimeIndex(dates).year
    print(f"  {'年':>6s} {'母集団':>8s} {'RS85以上の実数':>14s} "
          f"{'加重リターン中央値':>20s}")
    close_full = np.load(npy("close"), mmap_mode="r")
    for y in range(int(yr.min()), int(yr.max()) + 1):
        sel = np.where(yr == y)[0]
        if sel.size == 0:
            continue
        t = int(sel[len(sel) // 2])
        if t < max(RS_LOOKBACKS):
            continue
        m = np.asarray(inuniv[t])
        k = int(m.sum())
        if k < 100:
            continue
        # その日の加重リターンの中央値(相場の温度。順位化で消える情報)
        col = np.asarray(close_full[t], dtype=np.float64)
        tot = np.zeros(N)
        ok = np.ones(N, dtype=bool)
        for lb, w in zip(RS_LOOKBACKS, RS_WEIGHTS):
            prev = np.asarray(close_full[t - lb], dtype=np.float64)
            with np.errstate(all="ignore"):
                tot += w * (col / prev - 1.0)
            ok &= np.isfinite(prev) & (prev > 0)
        raw_t = np.where(ok & m, tot, np.nan)
        n85 = int(np.nansum(np.asarray(rs[t])[m] >= 85))
        print(f"  {y:>6d} {k:8d} {n85:14d} {np.nanmedian(raw_t):20.3f}")
    print("  → 一番右の列が『その日の相場の温度』。RS は順位なので、この列を")
    print("     まるごと捨てている。lr(52週安倍率)は捨てていない。")
    print("     ここが RS と H3 の一番大きな違い。")


# ===========================================================================
# part confound: 測り方の側の交絡
# ===========================================================================

# --since / --until で期間を切るための置き場。main() が入れる。
# 「直近だけで効いて全期間で消える」を炙り出すのに使う(申し送りの棄却基準)。
_SINCE: str | None = None
_UNTIL: str | None = None


def _load_setups() -> pd.DataFrame:
    p = WORK / "setups.parquet"
    if not p.exists():
        sys.exit(f"{p} が無い。先に --stage setups を流すこと。")
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    if _SINCE:
        df = df[df["date"] >= _SINCE]
    if _UNTIL:
        df = df[df["date"] <= _UNTIL]
    if _SINCE or _UNTIL:
        print(f"※期間を {_SINCE or '最初'} 〜 {_UNTIL or '最後'} に限定: "
              f"n={len(df):,}\n")
    return df.reset_index(drop=True)


def _R(mae: np.ndarray, ret: np.ndarray, sd: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        return np.where(mae > sd, -1.0, ret / sd)


def _bucket(x: np.ndarray, edges: list[float]) -> tuple[np.ndarray, list[str]]:
    b = np.digitize(x, edges)
    lab = ([f"<{edges[0]:g}"]
           + [f"{edges[i]:g}-{edges[i+1]:g}" for i in range(len(edges) - 1)]
           + [f">={edges[-1]:g}"])
    return b, lab


def part_confound() -> None:
    df = _load_setups()
    B = df[df["bo"] == 1].reset_index(drop=True)
    atrp = B["atrp"].to_numpy()
    mae = B["mae"].to_numpy()
    ret = B["ret10"].to_numpy()
    Rm = _R(mae, ret, np.clip(1.5 * atrp, 0.03, 0.12))
    print(f"ブレイク到達 n={len(B):,}  平均R={np.nanmean(Rm):+.3f}\n")

    # ---------------------------------------------------------------
    # A. RS 帯ごとに「何が一緒に動いているか」を並べる。
    #    RS が高い帯でATRも高いなら、期待Rの分母がATRである以上
    #    RS の効果はストップの定義で符号が変わる。
    # ---------------------------------------------------------------
    print("=" * 78)
    print("【A】RS 帯ごとに他の指標がどう動いているか")
    b, lab = _bucket(B["rs"].to_numpy(), [30, 50, 70, 85])
    cols = ["atrp", "lr", "hr", "ma200sl", "dryup", "dist"]
    print(f"  {'帯':>10s} {'n':>7s} " + "".join(f"{c:>10s}" for c in cols)
          + f"{'到達率':>9s}{'平均R':>9s}")
    # np.digitize は NaN を一番上の帯へ入れてしまうので、必ず有限だけに絞る
    fin = np.isfinite(B["rs"].to_numpy())
    allb, _ = _bucket(df["rs"].to_numpy(), [30, 50, 70, 85])
    allfin = np.isfinite(df["rs"].to_numpy())
    for i in range(len(lab)):
        m = (b == i) & fin
        if m.sum() < 50:
            continue
        row = f"  {lab[i]:>10s} {m.sum():7,d} "
        for c in cols:
            row += f"{np.nanmean(B[c].to_numpy()[m]):10.3f}"
        rate = (df["bo"].to_numpy()[(allb == i) & allfin] == 1).mean()
        row += f"{rate:9.1%}{np.nanmean(Rm[m]):+9.3f}"
        print(row)
    r_ = B[["rs", "atrp"]].dropna()
    print(f"\n  RS と ATR% の順位相関 = {r_['rs'].corr(r_['atrp'], method='spearman'):+.3f}")
    print("  → プラスに大きいなら、RS が高い銘柄ほど分母のATRが大きい。")
    print("     期待R = 10日リターン / ストップ幅 なので、この時点で")
    print("     『RSの効果』はストップの定義で符号が動く。")

    # ---------------------------------------------------------------
    # B. ATR を揃えて RS を比べる。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【B】ATR を揃えた上で RS 帯を比べる(ATR五分位の中だけで比較)")
    q = pd.qcut(pd.Series(atrp), 5, labels=False, duplicates="drop").to_numpy()
    defs = stop_defs(atrp)
    print(f"  {'ATR五分位':>12s} {'ATR平均':>9s} " + "".join(
        f"{k:>16s}" for k in defs))
    print(f"  {'':12s} {'':9s} " + "".join(f"{'RS85+ − RS<30':>16s}" for _ in defs))
    for qi in range(5):
        mq = q == qi
        if mq.sum() < 200:
            continue
        row = f"  {qi+1:>12d} {np.nanmean(atrp[mq]):9.3f} "
        for _, sd in defs.items():
            R2 = _R(mae, ret, sd)
            hi = mq & (B["rs"].to_numpy() >= 85) & np.isfinite(R2)
            lo = mq & (B["rs"].to_numpy() < 30) & np.isfinite(R2)
            if hi.sum() < 30 or lo.sum() < 30:
                row += f"{'-':>16s}"
            else:
                row += f"{np.nanmean(R2[hi]) - np.nanmean(R2[lo]):+16.3f}"
        print(row)
    print("  → ATRを揃えた各行で符号が揃うなら、RS には向きがある。")
    print("     行ごとにバラバラなら、元の逆行はATRの違いを見ていただけ。")

    # ---------------------------------------------------------------
    # C. 同じ日の中だけで比べる。これが本命。
    #    その日のセットアップ全体の平均Rを引くと、相場の良し悪しは
    #    完全に消えて「同じ日に何を選んだか」だけが残る。
    #    H3 の効果がここで消えるなら、H3 は局面フィルタの言い換え。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【C】同じ日の中だけで比べる(その日の平均Rを引く)")
    dfB = B.copy()
    dfB["R"] = Rm
    day_n = dfB.groupby("date")["R"].transform("size")
    day_mu = dfB.groupby("date")["R"].transform("mean")
    keep = (day_n >= 10).to_numpy() & np.isfinite(Rm)
    Rd = (dfB["R"] - day_mu).to_numpy()
    print(f"  同じ日に10件以上あるセットアップだけ使う: n={int(keep.sum()):,} "
          f"/ {len(B):,}  ({keep.sum()/len(B):.0%})")

    for title, key, edges in [
        ("H1 RS", "rs", [30, 50, 70, 85]),
        ("H3 close/52w安", "lr", [1.25, 1.5, 2.0, 3.0]),
        ("H4 MA200 21日傾き", "ma200sl", [0, 0.01, 0.03, 0.06]),
        ("H2 close/52w高", "hr", [0.75, 0.85, 0.92, 0.97]),
        ("枯れ度", "dryup", [0.66, 0.77, 1.0]),
    ]:
        x = B[key].to_numpy()
        bb, ll = _bucket(x, edges)
        print(f"\n  --- {title}")
        print(f"    {'帯':>12s} {'n':>8s} {'そのまま':>10s} {'同日内':>10s}")
        for i in range(len(ll)):
            m = (bb == i) & keep & np.isfinite(x)
            if m.sum() < 100:
                print(f"    {ll[i]:>12s} {m.sum():8,d}   (少)")
                continue
            print(f"    {ll[i]:>12s} {m.sum():8,d} {np.nanmean(Rm[m]):+10.3f} "
                  f"{np.nanmean(Rd[m]):+10.3f}")
    print("\n  → 『そのまま』で単調なのに『同日内』で潰れる指標は、")
    print("     銘柄を選んでいたのではなく相場の良い日を選んでいただけ。")
    print("     H3 がこれに該当するなら、H3 と局面フィルタは同じ発見。")

    # ---------------------------------------------------------------
    # D. lr を RS と同じ土俵に乗せる。
    #    その日のセットアップの中での順位に直してから帯分けする。
    #    RS は最初からこの形なので、これで初めて対等な比較になる。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【D】lr(52週安倍率)を RS と同じ『その日の中での順位』に直す")
    for key, nm in [("lr", "H3 52w安倍率"), ("ma200sl", "H4 MA200傾き"),
                    ("hr", "H2 52w高比")]:
        pctile = dfB.groupby("date")[key].rank(pct=True).to_numpy() * 100
        bb, ll = _bucket(pctile, [30, 50, 70, 85])
        print(f"\n  --- {nm} を順位化(1-100)")
        print(f"    {'帯':>12s} {'n':>8s} {'平均R':>10s} {'同日内':>10s}")
        for i in range(len(ll)):
            m = (bb == i) & keep & np.isfinite(pctile)
            if m.sum() < 100:
                print(f"    {ll[i]:>12s} {m.sum():8,d}   (少)")
                continue
            print(f"    {ll[i]:>12s} {m.sum():8,d} {np.nanmean(Rm[m]):+10.3f} "
                  f"{np.nanmean(Rd[m]):+10.3f}")
    print("\n  → 順位に直した lr が RS と同じように潰れるなら、")
    print("     『強い銘柄を買う』という主張そのものが、この標本では")
    print("     銘柄選択としては効いていないことになる。")
    print("     順位化しても lr が効くなら、RS の計算期間や重みの側を疑う。")

    # ---------------------------------------------------------------
    # E. 局面ごとの RS。25年をまとめると消えるのが局面のせいか
    #    実装のせいかを分ける。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【E】局面別に見た RS(高い側 − 低い側 の期待R差)")
    dt = B["date"]
    x = B["rs"].to_numpy()
    print(f"  {'局面':32s} {'n':>7s} " + "".join(f"{k:>16s}" for k in defs))
    for nm, a, bnd in REGIMES:
        m0 = ((dt >= a) & (dt <= bnd)).to_numpy()
        if m0.sum() < 200:
            continue
        row = f"  {nm:32s} {m0.sum():7,d} "
        for _, sd in defs.items():
            R2 = _R(mae, ret, sd)
            hi = m0 & (x >= 85) & np.isfinite(R2)
            lo = m0 & (x < 30) & np.isfinite(R2)
            if hi.sum() < 30 or lo.sum() < 30:
                row += f"{'-':>16s}"
            else:
                row += f"{np.nanmean(R2[hi]) - np.nanmean(R2[lo]):+16.3f}"
        print(row)
    print("  → どの局面でもマイナス寄りなら、RS は本当に効いていない。")
    print("     上げ相場でプラス・下げ相場で大きくマイナスなら、RS は")
    print("     銘柄選択ではなく相場への感応度(ベータ)を買っているだけ。")

    print("\n★注記: 本データは上場廃止銘柄を含まない。")
    print("  古い局面だけで見えた効果を単独の根拠にしないこと(log.md 135)。")


# ===========================================================================
# part joint: 「日」と「ATR」を同時に統制する
# ===========================================================================
#
# confound の結果、【B】(ATRだけ揃える)と【C】(日だけ揃える)で
# RS の符号が正面から逆になった。
#   ATRを揃える → RS85+ − RS<30 は全マスでマイナス(-0.002〜-0.122)
#   日を揃える  → RS>=85 が +0.055、RS<30 が -0.003 でプラス
# 片方だけ揃えても向きは決まらない。両方同時に揃える必要がある。
#
# もうひとつ、効果が「上位15%」ではなく「上位3%」に居る疑いがある。
#   生の 52週安倍率 >=3(標本の3.3%)の同日内Rが +0.140
#   それを順位化して上位15%に均すと +0.063 に半減する
# RS>=85 は定義上その日の上位15%なので、この尻尾を構造的に拾えない。
# 現閾値70が緩すぎるだけ、という可能性が残っている。
# ===========================================================================

def _daydemean(R: np.ndarray, dates: pd.Series, min_n: int = 10):
    """その日のセットアップ全体の平均Rを引く。相場の良し悪しが消える。

    同じ日に min_n 件未満しか無い日は、平均が1〜2件で決まってしまい
    引き算が雑音を増やすだけなので使わない。
    """
    s = pd.DataFrame({"d": dates.to_numpy(), "R": R})
    n = s.groupby("d")["R"].transform("size").to_numpy()
    mu = s.groupby("d")["R"].transform("mean").to_numpy()
    keep = (n >= min_n) & np.isfinite(R)
    return R - mu, keep


def part_joint() -> None:
    df = _load_setups()
    B = df[df["bo"] == 1].reset_index(drop=True)
    atrp = B["atrp"].to_numpy()
    mae = B["mae"].to_numpy()
    ret = B["ret10"].to_numpy()
    dt = B["date"]
    rs = B["rs"].to_numpy()
    defs = stop_defs(atrp)
    prim = "1.5ATR(3-12%)"

    # ATR五分位。RS帯とATR帯は相関 +0.437 で絡んでいるので、
    # 必ずこの中だけで比べる。
    q = pd.qcut(pd.Series(atrp), 5, labels=False, duplicates="drop").to_numpy()

    print(f"ブレイク到達 n={len(B):,}\n")

    # ---------------------------------------------------------------
    # J1. 二重統制。これが RS の向きの決着。
    # ---------------------------------------------------------------
    print("=" * 78)
    print("【J1】日とATRを同時に揃えて RS を見る")
    print("  各マス = その日の平均Rを引いた上で、ATR五分位の中だけで取った平均")
    Rp = _R(mae, ret, defs[prim])
    Rd, keep = _daydemean(Rp, dt)
    edges = [30, 50, 70, 85]
    b, lab = _bucket(rs, edges)
    fin = np.isfinite(rs)
    print(f"\n  ストップ = {prim}")
    print(f"  {'RS帯':>10s} " + "".join(f"{f'ATR{i+1}':>10s}" for i in range(5))
          + f"{'全体':>10s}")
    for i in range(len(lab)):
        row = f"  {lab[i]:>10s} "
        for qi in range(5):
            m = (b == i) & fin & keep & (q == qi)
            row += f"{np.nanmean(Rd[m]):+10.3f}" if m.sum() >= 100 else f"{'-':>10s}"
        m = (b == i) & fin & keep
        row += f"{np.nanmean(Rd[m]):+10.3f}" if m.sum() >= 100 else f"{'-':>10s}"
        print(row)
    print(f"  {'(件数)':>10s} " + "".join(
        f"{int(((b>=0)&fin&keep&(q==qi)).sum()):>10,d}" for qi in range(5)))

    print("\n  RS85以上 − RS30未満 を、ストップの定義5本すべてで:")
    print(f"  {'':10s}" + "".join(f"{f'ATR{i+1}':>16s}" for i in range(5))
          + f"{'全体':>16s}")
    for nm, sd in defs.items():
        R2 = _R(mae, ret, sd)
        R2d, k2 = _daydemean(R2, dt)
        row = f"  {nm:10s}"
        for qi in list(range(5)) + [None]:
            base = fin & k2 & ((q == qi) if qi is not None else np.ones(len(B), bool))
            hi = base & (rs >= 85)
            lo = base & (rs < 30)
            if hi.sum() < 50 or lo.sum() < 50:
                row += f"{'-':>16s}"
            else:
                row += f"{np.nanmean(R2d[hi]) - np.nanmean(R2d[lo]):+16.3f}"
        print(row)
    print("\n  → 5×5=25マスすべてでプラスなら RS は効いている。")
    print("     すべてマイナスなら効いていない。混ざるなら、RS単体では決まらない。")

    # ---------------------------------------------------------------
    # J2. 上位を細かく切る。効果が尻尾に居るのかを見る。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【J2】上位を細かく切る(効果は上位15%か、上位3%か)")
    fine = [70, 85, 95, 98]
    for key, nm, ranked in [
        ("rs", "RS(元から順位)", False),
        ("lr", "52週安倍率(順位に直す)", True),
        ("ma200sl", "MA200傾き(順位に直す)", True),
    ]:
        if ranked:
            x = (B.assign(_d=dt).groupby("_d")[key].rank(pct=True).to_numpy() * 100)
        else:
            x = B[key].to_numpy()
        bb, ll = _bucket(x, fine)
        xf = np.isfinite(x)
        print(f"\n  --- {nm}")
        print(f"    {'帯':>10s} {'n':>8s} {'同日内':>10s} " + "".join(
            f"{f'ATR{i+1}':>10s}" for i in range(5)))
        for i in range(len(ll)):
            m = (bb == i) & xf & keep
            if m.sum() < 100:
                print(f"    {ll[i]:>10s} {m.sum():8,d}   (少)")
                continue
            row = f"    {ll[i]:>10s} {m.sum():8,d} {np.nanmean(Rd[m]):+10.3f} "
            for qi in range(5):
                mm = m & (q == qi)
                row += f"{np.nanmean(Rd[mm]):+10.3f}" if mm.sum() >= 60 else f"{'-':>10s}"
            print(row)

    # 生の52週安倍率も同じ細かさで。順位化で尻尾が薄まる分を見る。
    print("\n  --- 52週安倍率(生の値のまま)")
    x = B["lr"].to_numpy()
    bb, ll = _bucket(x, [1.5, 2.0, 3.0, 5.0, 10.0])
    xf = np.isfinite(x)
    print(f"    {'帯':>10s} {'n':>8s} {'割合':>7s} {'同日内':>10s} {'ATR平均':>9s}")
    for i in range(len(ll)):
        m = (bb == i) & xf & keep
        if m.sum() < 100:
            print(f"    {ll[i]:>10s} {m.sum():8,d}   (少)")
            continue
        print(f"    {ll[i]:>10s} {m.sum():8,d} {m.sum()/keep.sum():6.1%} "
              f"{np.nanmean(Rd[m]):+10.3f} {np.nanmean(atrp[m]):9.3f}")
    print("\n  → 上へ行くほど伸び続けるなら、閾値が緩すぎるだけ(70→90台へ)。")
    print("     85で頭打ちなら、尻尾は別の何か(小型・低位・急騰後)を見ている。")

    # ---------------------------------------------------------------
    # J3. 尻尾の中身。少数の銘柄で出来ていないか。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【J3】52週安倍率の尻尾(>=3)が何で出来ているか")
    tail = (B["lr"].to_numpy() >= 3.0) & keep
    T = B[tail]
    print(f"  n={int(tail.sum()):,}  銘柄数={T['code'].nunique():,}  "
          f"同日内R={np.nanmean(Rd[tail]):+.3f}")
    vc = T["code"].value_counts()
    print(f"  1銘柄あたり中央値 {vc.median():.0f} 件 / 最多 {vc.iloc[0]} 件 ({vc.index[0]})")
    print(f"  上位10銘柄で全体の {vc.head(10).sum()/len(T):.1%} を占める")
    print(f"  ATR平均 {np.nanmean(atrp[tail]):.3f} (全体 {np.nanmean(atrp[keep]):.3f})")
    print("  年別の件数:")
    yc = T["date"].dt.year.value_counts().sort_index()
    print("    " + "  ".join(f"{y}:{c:,}" for y, c in yc.items()))
    # 件数の多い上位20銘柄を丸ごと抜いても残るか
    drop = set(vc.head(20).index)
    m2 = tail & ~B["code"].isin(drop).to_numpy()
    print(f"\n  最多20銘柄を抜くと n={int(m2.sum()):,} "
          f"同日内R={np.nanmean(Rd[m2]):+.3f}")
    print("  → ここで大きく落ちるなら、尻尾は数銘柄の当たりで出来ている。")

    # ---------------------------------------------------------------
    # J4. 局面別を同日内Rでやり直す。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【J4】局面別(同日内Rでやり直し)RS85以上 − RS30未満")
    print(f"  {'局面':32s} {'n':>7s} " + "".join(f"{k:>16s}" for k in defs))
    for nm, a, bnd in REGIMES:
        m0 = ((dt >= a) & (dt <= bnd)).to_numpy()
        if m0.sum() < 200:
            continue
        row = f"  {nm:32s} {m0.sum():7,d} "
        for _, sd in defs.items():
            R2 = _R(mae, ret, sd)
            R2d, k2 = _daydemean(R2, dt)
            hi = m0 & k2 & (rs >= 85)
            lo = m0 & k2 & (rs < 30)
            if hi.sum() < 50 or lo.sum() < 50:
                row += f"{'-':>16s}"
            else:
                row += f"{np.nanmean(R2d[hi]) - np.nanmean(R2d[lo]):+16.3f}"
        print(row)

    print("\n  同じものを 52週安倍率(生の値 >=3 − <1.25)で:")
    lr = B["lr"].to_numpy()
    print(f"  {'局面':32s} {'n':>7s} " + "".join(f"{k:>16s}" for k in defs))
    for nm, a, bnd in REGIMES:
        m0 = ((dt >= a) & (dt <= bnd)).to_numpy()
        if m0.sum() < 200:
            continue
        row = f"  {nm:32s} {m0.sum():7,d} "
        for _, sd in defs.items():
            R2 = _R(mae, ret, sd)
            R2d, k2 = _daydemean(R2, dt)
            hi = m0 & k2 & (lr >= 3.0)
            lo = m0 & k2 & (lr < 1.25)
            if hi.sum() < 50 or lo.sum() < 50:
                row += f"{'-':>16s}"
            else:
                row += f"{np.nanmean(R2d[hi]) - np.nanmean(R2d[lo]):+16.3f}"
        print(row)
    print("\n  → 局面をまたいで符号が揃う方を採る。RS側だけが揃わないなら、")
    print("     RSは相場への感応度を買っているだけということになる。")

    # ---------------------------------------------------------------
    # J5. 枯れ度。confound で同日内にすると効果が倍以上に増えた。
    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("【J5】枯れ度(同日内 × ATR五分位)")
    x = B["dryup"].to_numpy()
    bb, ll = _bucket(x, [0.66, 0.77, 1.0])
    xf = np.isfinite(x)
    print(f"  {'帯':>10s} {'n':>8s} {'同日内':>10s} " + "".join(
        f"{f'ATR{i+1}':>10s}" for i in range(5)))
    for i in range(len(ll)):
        m = (bb == i) & xf & keep
        if m.sum() < 100:
            continue
        row = f"  {ll[i]:>10s} {m.sum():8,d} {np.nanmean(Rd[m]):+10.3f} "
        for qi in range(5):
            mm = m & (q == qi)
            row += f"{np.nanmean(Rd[mm]):+10.3f}" if mm.sum() >= 60 else f"{'-':>10s}"
        print(row)
    print("  枯れている側 − 枯れていない側(<0.66 − >=1)を定義5本で:")
    for nm, sd in defs.items():
        R2 = _R(mae, ret, sd)
        R2d, k2 = _daydemean(R2, dt)
        hi = xf & k2 & (x < 0.66)
        lo = xf & k2 & (x >= 1.0)
        print(f"    {nm:16s} {np.nanmean(R2d[hi]) - np.nanmean(R2d[lo]):+.3f}")

    print("\n★注記: 本データは上場廃止銘柄を含まない(log.md 135)。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all",
                    choices=["impl", "confound", "joint", "all"])
    ap.add_argument("--sample", type=int, default=30, help="突合に使う銘柄数")
    ap.add_argument("--since", default=None, help="この日以降のセットアップだけ使う")
    ap.add_argument("--until", default=None, help="この日以前のセットアップだけ使う")
    args = ap.parse_args()
    global _SINCE, _UNTIL
    _SINCE, _UNTIL = args.since, args.until
    if args.part in ("impl", "all"):
        part_impl(n_sample=args.sample)
    if args.part in ("confound", "all"):
        if args.part == "all":
            print("\n\n")
        part_confound()
    if args.part in ("joint", "all"):
        if args.part == "all":
            print("\n\n")
        part_joint()


if __name__ == "__main__":
    main()
