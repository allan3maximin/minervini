#!/usr/bin/env python3
"""SEPA骨格7閾値の診断を、長期データ(2000〜)で再実行する(2026-07-28)。

`tools/audit_sepa_thresholds.py` の2年版(log.md 134)を、
`tools/fetch_long_history.py` が作る `data/prices_long/` に対して回す版。

2年版との違いは3点だけ。閾値の最適値は**探索しない**方針は変えない。

1. 期間が長い。前半/後半ではなく**相場局面**で切って符号の保存を見る。
   2000-02 / 2003-06 / 2007-09 / 2010-12 / 2013-14 / 2015-17 /
   2018-19 / 2020 / 2021-22 / 2023-24 / 2025-26 の11局面。
   見たいのは「この閾値はどの局面でも効くのか」であって最適値ではない。
   ★2026-07-28 に 2015年起点(6局面)から 2000年起点(11局面)へ拡張した。
     大きく壊れる相場が 2020 の1回しか入っておらず、暴落局面で符号が
     保たれるかを確かめようがなかったため。これで 2000-02 / 2007-09 /
     2020 の3回入る。拡張の代償は生存バイアスの増大(下記)。

2. ユニバースを**各日時点の売買代金**で再構築する。`data/universe.json`
   (2026年時点の上位1000)を使うと、2026年の流動性で2015年の銘柄を選ぶことになり、
   結果が未来情報で汚れる。閾値は config.yaml: universe.min_trading_value を踏襲。

3. RSのクロスセクション順位も**その日のユニバース内**で取る。母集団が変われば
   同じ「RS70」の意味が変わるので、ここを固定しないと期間比較が成立しない。

成否の定義は134で改めたものを踏襲する(ヒット率ではなくATR正規化の期待R)。
理由は log.md 2026-07-28(134)。固定-5%は低ボラ銘柄フィルタとして働き、
Minerviniの非対称ペイオフと正反対の方向に最適化してしまう。

★生存バイアスの注記 — 2000年まで遡ると更に深刻になる★
`data/prices_long/` は上場廃止銘柄を含まない(yfinanceが返さない)。
標本外率は2015年で17.4%、2023年で5.4%(log.md 135)。2000年代前半は
これより遥かに大きい(2013年より前は東証の会社数表と突合できないので
未測定。`fetch_long_history.py --survivorship-report` 参照)。
日本の上場廃止はTOB/MBOが大きな比率を占め、これは勝ちトレードなので
歪みの向きは自明でない。

**古い局面ほどデータ量は増えるが標本の代表性は落ちる。**
2000-02 / 2003-06 の列は「符号が合っているか」の参考にとどめ、
その列だけで効果を主張しないこと。単調性や符号の安定性を見るときは
2010年以降で成立しているかを必ず併せて確認する。
更に `--coverage-report` で分かる通り、古い年は yfinance の収載自体が
薄く、「生存バイアス」とは別に「そもそも標本が小さい」問題もある。

実行(メモリ3GB・bash45秒制限を想定して段階分割):
    python tools/audit_thresholds_long.py --stage build
    python tools/audit_thresholds_long.py --stage feat
    python tools/audit_thresholds_long.py --stage rs
    python tools/audit_thresholds_long.py --stage setups
    python tools/audit_thresholds_long.py --stage report
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LONG_DIR = ROOT / "data" / "prices_long"

# 中間行列の置き場。2026-07-28 に /tmp から data/ 配下へ移した。
# 理由: 検証期間を2000年まで延ばすと日付が約2,850→約6,400行になり、
# 中間の .npy が合計1.5GB超になる。sandbox の / は空きが300MB弱しかなく、
# /tmp では確実に溢れる。プロジェクト側のディスクには十分な空きがある。
# .gitignore 済み。消しても --stage build からやり直せば再生成できる。
WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))

MIN_TRADING_VALUE = 1e8  # config.yaml: universe.min_trading_value
RS_LOOKBACKS = (63, 126, 189, 252)
RS_WEIGHTS = (2.0, 1.0, 1.0, 1.0)
WAIT, POST = 20, 10  # src/report/dryup_log.py と同一
DEDUP_BARS = 10

# 相場局面。前半/後半の機械的2分割ではなく、性格の違う局面で切る。
# 「どの局面でも符号が保たれるか」だけを見るためのもので、
# 局面ごとに閾値を変えるためのものではない(それはカーブフィット)。
# 2026-07-28: 取得起点を2000年に延ばしたのに合わせて前半5局面を追加。
# これで「大きく壊れる相場」が 2000-02 / 2007-09 / 2020 の3回入る。
# 2015年起点だとコロナの1回しかなく、暴落局面で符号が保たれるかを
# 確かめようがなかった。区切りは事後の成績ではなく、事前に知られている
# マクロイベント(バブル崩壊・リーマン・震災・政権交代)で引いている。
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


def npy(name: str) -> Path:
    return WORK / f"{name}.npy"


# ---------------------------------------------------------------------------
# stage build: parquet群 → 日付×銘柄の密行列
# ---------------------------------------------------------------------------

def stage_build() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in LONG_DIR.glob("*.parquet") if not p.stem.startswith("_"))
    print(f"parquet {len(files)} 件を読み込み中...", flush=True)

    # 素朴に「銘柄ごとに行列へ代入」すると日付→行番号の辞書引きが数千万回走って
    # bashの45秒制限を超える。縦持ちで一度に連結してから searchsorted で
    # ベクトル化代入する。
    parts: list[pd.DataFrame] = []
    codes: list[str] = []
    t0 = time.time()
    for p in files:
        try:
            d = pd.read_parquet(p)
        except Exception:
            continue
        if d.empty or "close" not in d.columns:
            continue
        # 全期間で一度も売買代金1億円に届かない銘柄は、どの日もユニバースに
        # 入らないので最初から落とす。メモリ節約(3GB制約)のため。
        tv = (d["close"] * d["volume"]).max()
        if not np.isfinite(tv) or tv < MIN_TRADING_VALUE:
            continue
        d = d[["date", "close", "high", "low", "volume"]].copy()
        # 2000年起点だと縦持ちが約2,000万行になる。float64のままだと連結時に
        # 1GB超を一時的に2重に持ってOOMする(sandbox 3.9GB)。ここで落としておく。
        for c in ("close", "high", "low", "volume"):
            d[c] = d[c].astype(np.float32)
        d["ci"] = np.int32(len(codes))
        codes.append(p.stem)
        parts.append(d)
    print(f"  読み込み {len(codes)} 銘柄 採用 ({time.time()-t0:.0f}秒)", flush=True)

    big = pd.concat(parts, ignore_index=True)
    del parts
    big["date"] = pd.to_datetime(big["date"])
    dates = np.sort(big["date"].unique()).astype("datetime64[ns]")
    T, N = len(dates), len(codes)
    print(f"日付 {T} × 銘柄 {N}  ({dates[0]} 〜 {dates[-1]})  縦持ち {len(big):,}行", flush=True)

    di = np.searchsorted(dates, big["date"].to_numpy(dtype="datetime64[ns]"))
    ci = big["ci"].to_numpy()
    for field in ["close", "high", "low", "volume"]:
        M = np.full((T, N), np.nan, dtype=np.float32)
        M[di, ci] = big[field].to_numpy(dtype=np.float32)
        np.save(npy(field), M)
        del M
        print(f"  saved {field}  ({time.time()-t0:.0f}秒)", flush=True)

    np.save(npy("dates"), dates)
    (WORK / "codes.json").write_text(json.dumps(codes), encoding="utf-8")
    print("build 完了")


# ---------------------------------------------------------------------------
# stage feat: ローリング指標
# ---------------------------------------------------------------------------

def _roll(M: np.ndarray, win: int, how: str) -> np.ndarray:
    df = pd.DataFrame(M)
    r = df.rolling(win, min_periods=win)
    out = {"mean": r.mean, "max": r.max, "min": r.min, "median": r.median}[how]()
    return out.to_numpy(dtype=np.float32)


def stage_feat() -> None:
    close = np.load(npy("close"))
    high = np.load(npy("high"))
    low = np.load(npy("low"))
    vol = np.load(npy("volume"))

    specs = [
        ("ma50", lambda: _roll(close, 50, "mean")),
        ("ma200", lambda: _roll(close, 200, "mean")),
        ("vma50", lambda: _roll(vol, 50, "mean")),
        ("volmed10", lambda: _roll(vol, 10, "median")),
        ("h20", lambda: _roll(high, 20, "max")),
        ("h250", lambda: _roll(high, 250, "max")),
        ("l250", lambda: _roll(low, 250, "min")),
    ]
    for name, fn in specs:
        np.save(npy(name), fn())
        print(f"  {name} ok", flush=True)

    # ATR(14): True Range の14日単純平均。既存 audit スクリプトと同一定義。
    pc = np.vstack([close[:1], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    np.save(npy("atr"), _roll(tr, 14, "mean"))
    print("  atr ok")
    print("feat 完了")


# ---------------------------------------------------------------------------
# stage rs: 各日ユニバース内でのRSパーセンタイル
# ---------------------------------------------------------------------------

def stage_rs() -> None:
    close = np.load(npy("close"))
    vma50 = np.load(npy("vma50"))
    T, N = close.shape

    # その日ユニバース = 終値×50日平均出来高 >= 1億円
    inuniv = (close * vma50) >= MIN_TRADING_VALUE
    inuniv &= np.isfinite(close) & np.isfinite(vma50)
    np.save(npy("inuniv"), inuniv)
    print("ユニバース銘柄数 p10/50/90 =", np.percentile(inuniv.sum(axis=1), [10, 50, 90]).round(0))

    # IBD式加重リターン (src/indicators.py: RS_LOOKBACKS / RS_WEIGHTS と同一)
    tot = np.zeros((T, N), dtype=np.float64)
    ok = np.ones((T, N), dtype=bool)
    for lb, w in zip(RS_LOOKBACKS, RS_WEIGHTS):
        sh = np.full((T, N), np.nan, dtype=np.float32)
        sh[lb:] = close[:-lb]
        with np.errstate(all="ignore"):
            tot += w * (close / sh - 1.0)
        ok &= np.isfinite(sh) & (sh > 0)
    raw = np.where(ok, tot, np.nan)
    del tot, ok

    # ★その日のユニバース内だけで順位を取る★
    # 母集団が変われば同じ「RS70」の意味が変わるので、期間比較のために固定する。
    rs = np.full((T, N), np.nan, dtype=np.float32)
    for t in range(T):
        m = inuniv[t] & np.isfinite(raw[t])
        k = int(m.sum())
        if k < 100:
            continue
        v = raw[t, m]
        r = v.argsort().argsort() + 1
        rs[t, np.where(m)[0]] = np.clip(np.round(r / k * 98 + 1), 1, 99)
    np.save(npy("rs"), rs)
    print("rs 完了")


# ---------------------------------------------------------------------------
# stage setups: セットアップ抽出
# ---------------------------------------------------------------------------

def stage_setups() -> None:
    # 2026-07-28: 全部を実体でロードすると 2000年起点では 13行列 × 約94MB = 1.2GB を
    # 常駐で抱えることになる。ここは銘柄ごとに列を1本ずつ舐めるだけなので
    # mmap で開いてOSのページキャッシュに任せる。速度はほぼ変わらない。
    def L(name: str) -> np.ndarray:
        return np.load(npy(name), mmap_mode="r")

    close = L("close")
    high = L("high")
    low = L("low")
    vol = L("volume")
    ma50 = L("ma50")
    ma200 = L("ma200")
    vma50 = L("vma50")
    volmed10 = L("volmed10")
    h20 = L("h20")
    h250 = L("h250")
    l250 = L("l250")
    atr = L("atr")
    rs = L("rs")
    inuniv = L("inuniv")
    dates = np.load(npy("dates"))
    codes = json.loads((WORK / "codes.json").read_text(encoding="utf-8"))
    T, N = close.shape

    recs = []
    t0 = time.time()
    for ci in range(N):
        c = close[:, ci].astype(np.float64)
        piv_all = h20[:, ci].astype(np.float64)
        with np.errstate(all="ignore"):
            dist = (piv_all - c) / c
        # 緩和抽出: ステージ2の最低限(MA200上)+ユニバース+ピボット手前15%以内。
        # 各閾値の分布を見るのが目的なので、診断対象の条件は掛けない。
        cand = (
            inuniv[:, ci]
            & (c > ma200[:, ci])
            & np.isfinite(rs[:, ci])
            & np.isfinite(atr[:, ci])
            & np.isfinite(h250[:, ci])
            & np.isfinite(l250[:, ci])
            & np.isfinite(ma50[:, ci])
            & (dist > 0)
            & (dist <= 0.15)
        )
        cand[: 260] = False
        cand[T - WAIT - POST :] = False
        idx = np.flatnonzero(cand)
        if idx.size == 0:
            continue
        # 同一銘柄の重複セットアップを10営業日間隔で間引く(時系列クラスタリング緩和)
        keep, last = [], -99
        for t in idx:
            if t - last >= DEDUP_BARS:
                keep.append(t)
                last = t
        t_arr = np.array(keep, dtype=np.int64)

        piv = piv_all[t_arr]
        bo_j = np.full(t_arr.size, -1, dtype=np.int64)
        broken = np.zeros(t_arr.size, dtype=bool)
        alive = np.ones(t_arr.size, dtype=bool)
        for k in range(1, WAIT + 1):
            j = t_arr + k
            cj = c[j]
            hit = alive & (cj > piv)
            bo_j[hit] = j[hit]
            alive &= ~hit
            brk = alive & (cj < piv * 0.90)
            broken |= brk
            alive &= ~brk

        atrp = atr[t_arr, ci] / c[t_arr]
        base = dict(
            rs=rs[t_arr, ci],
            hr=c[t_arr] / h250[t_arr, ci],
            lr=c[t_arr] / l250[t_arr, ci],
            ma200sl=ma200[t_arr, ci] / ma200[t_arr - 21, ci] - 1.0,
            atrp=atrp,
            dryup=volmed10[t_arr, ci] / vma50[t_arr, ci],
            dist=dist[t_arr],
            above50=(c[t_arr] > ma50[t_arr, ci]).astype(np.float32),
        )

        has_bo = bo_j >= 0
        n = t_arr.size
        bo_vol = np.full(n, np.nan)
        bo_gap = np.full(n, np.nan)
        mae = np.full(n, np.nan)
        mfe = np.full(n, np.nan)
        ret10 = np.full(n, np.nan)
        if has_bo.any():
            bj = bo_j[has_bo]
            e = c[bj]
            with np.errstate(all="ignore"):
                bo_vol[has_bo] = vol[bj, ci] / vma50[bj, ci]
                bo_gap[has_bo] = (e - piv[has_bo]) / piv[has_bo]
            lo_min = np.full(bj.size, np.inf)
            hi_max = np.full(bj.size, -np.inf)
            for k in range(1, POST + 1):
                jj = np.minimum(bj + k, T - 1)
                lo_min = np.minimum(lo_min, low[jj, ci])
                hi_max = np.maximum(hi_max, high[jj, ci])
            last_j = np.minimum(bj + POST, T - 1)
            mae[has_bo] = (e - lo_min) / e
            mfe[has_bo] = (hi_max - e) / e
            ret10[has_bo] = (c[last_j] - e) / e

        for i in range(n):
            recs.append(
                (
                    ci,
                    int(t_arr[i]),
                    float(base["rs"][i]),
                    float(base["hr"][i]),
                    float(base["lr"][i]),
                    float(base["ma200sl"][i]),
                    float(base["atrp"][i]),
                    float(base["dryup"][i]),
                    float(base["dist"][i]),
                    float(base["above50"][i]),
                    1 if has_bo[i] else (0 if broken[i] else -1),
                    float(bo_vol[i]),
                    float(bo_gap[i]),
                    float(mae[i]),
                    float(mfe[i]),
                    float(ret10[i]),
                )
            )
        if (ci + 1) % 500 == 0:
            print(f"  {ci+1}/{N}  setups={len(recs)}  {time.time()-t0:.0f}秒", flush=True)

    cols = [
        "ci", "t", "rs", "hr", "lr", "ma200sl", "atrp", "dryup", "dist", "above50",
        "bo", "bo_vol", "bo_gap", "mae", "mfe", "ret10",
    ]
    df = pd.DataFrame(recs, columns=cols)
    df["date"] = pd.to_datetime(dates[df["t"].to_numpy()])
    df["code"] = [codes[i] for i in df["ci"]]
    df.to_parquet(WORK / "setups.parquet", index=False)
    print(f"setups n={len(df)}  ブレイク到達={int((df['bo']==1).sum())}  → {WORK/'setups.parquet'}")


# ---------------------------------------------------------------------------
# stage report: 診断
# ---------------------------------------------------------------------------

def stage_report() -> None:
    df = pd.read_parquet(WORK / "setups.parquet")
    B = df[df["bo"] == 1].reset_index(drop=True)

    atrp = B["atrp"].to_numpy()
    mae = B["mae"].to_numpy()
    ret = B["ret10"].to_numpy()
    # ATR正規化ストップ (134で採用した定義)。刈られたら-1R、生存なら10日リターン/ストップ幅。
    sd = np.clip(1.5 * atrp, 0.03, 0.12)
    Rm = np.where(mae > sd, -1.0, ret / sd)

    dt = B["date"]
    reg_id = np.full(len(B), -1)
    for i, (_, a, b) in enumerate(REGIMES):
        reg_id[(dt >= a) & (dt <= b)] = i

    print(f"全セットアップ n={len(df):,}  ブレイク到達 n={len(B):,} "
          f"({len(B)/len(df):.1%})  期間 {df['date'].min().date()}〜{df['date'].max().date()}")
    print(f"銘柄数 {df['code'].nunique():,}  全体 平均R={np.nanmean(Rm):+.3f}\n")

    print("【局面別のベースライン】")
    print(f"{'局面':32s} {'n':>7s} {'到達率':>7s} {'平均R':>8s}")
    for i, (nm, a, b) in enumerate(REGIMES):
        m = reg_id == i
        allm = (df["date"] >= a) & (df["date"] <= b)
        if m.sum() < 50:
            continue
        print(f"{nm:32s} {m.sum():7,d} {m.sum()/max(allm.sum(),1):6.1%} {np.nanmean(Rm[m]):+8.3f}")

    def rep(title: str, key: str, edges: list[float], cur: str) -> None:
        x = B[key].to_numpy()
        xa = df[key].to_numpy()
        print(f"\n{'='*78}\n{title}\n  現閾値: {cur}")
        print(f"  分布 p10/25/50/75/90 = {np.nanpercentile(xa,[10,25,50,75,90]).round(3)}")
        b = np.digitize(x, edges)
        lab = ([f"<{edges[0]:g}"]
               + [f"{edges[i]:g}-{edges[i+1]:g}" for i in range(len(edges) - 1)]
               + [f">={edges[-1]:g}"])
        head = f"  {'帯':>12s} {'n':>7s} {'期待R':>8s} " + "".join(
            f"{nm.split()[0]:>8s}" for nm, _, _ in REGIMES)
        print(head)
        for i in range(len(edges) + 1):
            m = (b == i) & np.isfinite(x) & np.isfinite(Rm)
            if m.sum() < 50:
                print(f"  {lab[i]:>12s} {m.sum():7,d}   (少)")
                continue
            row = f"  {lab[i]:>12s} {m.sum():7,d} {np.nanmean(Rm[m]):+8.3f} "
            for ri in range(len(REGIMES)):
                mm = m & (reg_id == ri)
                row += f"{np.nanmean(Rm[mm]):+8.2f}" if mm.sum() >= 30 else f"{'-':>8s}"
            print(row)

    rep("H1 rs_min = 70", "rs", [30, 50, 70, 85], "RS>=70")
    rep("H2 high52w_margin = 0.75", "hr", [0.75, 0.85, 0.92, 0.97], "close/52w高 >= 0.75")
    rep("H3 low52w_margin = 1.25", "lr", [1.25, 1.5, 2.0, 3.0], "close/52w安 >= 1.25")
    rep("H4 ma200_up_days_min = 21", "ma200sl", [0, 0.01, 0.03, 0.06], "21日前比プラス")
    rep("H5 breakout_vol_mult = 1.4", "bo_vol", [1.0, 1.4, 2.0, 3.0], "ブレイク日出来高 >= 1.4倍")
    rep("H7 extended_pct = 0.05", "bo_gap", [0.01, 0.03, 0.05, 0.10], "pivot超過 <= 5%で見送り")
    rep("(再検証) 枯れ度 dryup_med_10_50", "dryup", [0.66, 0.77, 1.0], "badge_strong 0.66 / mild 0.77")

    print(f"\n{'='*78}\nH6 stop_loss_pct = 0.05")
    mfe = B["mfe"].to_numpy()
    win = mfe >= 0.10
    print(f"  「10日で+10%到達」= 本来の勝ち  n={int(np.nansum(win)):,} ({np.nanmean(win):.1%})")
    for s in [0.05, 0.06, 0.08, 0.10]:
        print(f"    固定ストップ -{s:.0%}: 勝ち組のうち先に刈られる割合 = {np.nanmean(mae[win]>s):.1%}")
    print("  ATR帯別に見た固定-5%の実質的な厳しさ:")
    for lo, hi, lab in [(0, .02, "低ボラ ATR<2%"), (.02, .035, "中 2-3.5%"),
                        (.035, .06, "高 3.5-6%"), (.06, 9, "激高 >6%")]:
        m = (atrp >= lo) & (atrp < hi) & win
        if m.sum() < 30:
            continue
        print(f"    {lab:16s} n={m.sum():5,d}  -5%で刈られる={np.nanmean(mae[m]>0.05):5.1%}"
              f"  -5%はATRの{0.05/np.nanmean(atrp[m]):.1f}倍")

    print(f"\n{'='*78}\n【ストップ定義の感度】高い側 - 低い側 の期待R差。符号が定義で反転しないか。")
    defs = {
        "1.5ATR(3-12%)": np.clip(1.5 * atrp, 0.03, 0.12),
        "1.5ATR(clip無)": 1.5 * atrp,
        "2.0ATR": 2.0 * atrp,
        "固定5%": np.full_like(atrp, 0.05),
        "固定8%": np.full_like(atrp, 0.08),
    }
    vars_ = {
        "H1 RS": ("rs", [70, 85]),
        "H3 52w安倍率": ("lr", [2.0, 3.0]),
        "H4 MA200傾き": ("ma200sl", [0.03, 0.06]),
        "H5 ブレイク出来高": ("bo_vol", [1.4, 3.0]),
        "H7 pivot超過": ("bo_gap", [0.05, 0.10]),
        "枯れ度": ("dryup", [0.66, 0.77]),
    }
    print(f"  {'':20s}" + "".join(f"{k:>16s}" for k in defs))
    for nm, (key, e) in vars_.items():
        x = B[key].to_numpy()
        row = f"  {nm:20s}"
        for _, s in defs.items():
            R2 = np.where(mae > s, -1.0, ret / s)
            m = np.isfinite(x) & np.isfinite(R2)
            row += f"{np.nanmean(R2[(x>=e[-1])&m]) - np.nanmean(R2[(x<e[0])&m]):+16.3f}"
        print(row)

    # -----------------------------------------------------------------
    # 無条件評価: ブレイク未達を「非トレード=0R」として全セットアップで評価する。
    # ブレイク到達後のRだけを見ると、「そもそもブレイクまで行くか」という
    # 経路が丸ごと落ちる。閾値の役目は多くの場合そちら側にある。
    # -----------------------------------------------------------------
    print(f"\n{'='*78}\n【無条件評価】ブレイク未達=0R として全セットアップで見る")
    a_atrp = df["atrp"].to_numpy()
    a_mae = df["mae"].to_numpy()
    a_ret = df["ret10"].to_numpy()
    a_sd = np.clip(1.5 * a_atrp, 0.03, 0.12)
    isbo = (df["bo"] == 1).to_numpy()
    with np.errstate(all="ignore"):
        Ru = np.where(isbo, np.where(a_mae > a_sd, -1.0, a_ret / a_sd), 0.0)
    a_dt = df["date"]
    a_reg = np.full(len(df), -1)
    for i, (_, a, b) in enumerate(REGIMES):
        a_reg[(a_dt >= a) & (a_dt <= b)] = i

    for title, key, edges in [
        ("H1 RS", "rs", [30, 50, 70, 85]),
        ("H2 close/52w高", "hr", [0.75, 0.85, 0.92, 0.97]),
        ("H3 close/52w安", "lr", [1.25, 1.5, 2.0, 3.0]),
        ("H4 MA200 21日傾き", "ma200sl", [0, 0.01, 0.03, 0.06]),
        ("枯れ度 dryup", "dryup", [0.66, 0.77, 1.0]),
    ]:
        x = df[key].to_numpy()
        b = np.digitize(x, edges)
        lab = ([f"<{edges[0]:g}"]
               + [f"{edges[i]:g}-{edges[i+1]:g}" for i in range(len(edges) - 1)]
               + [f">={edges[-1]:g}"])
        print(f"\n--- {title}")
        print(f"  {'帯':>12s} {'n':>8s} {'到達率':>7s} {'無条件R':>9s} " + "".join(
            f"{nm.split()[0]:>8s}" for nm, _, _ in REGIMES))
        for i in range(len(edges) + 1):
            m = (b == i) & np.isfinite(x)
            if m.sum() < 100:
                print(f"  {lab[i]:>12s} {m.sum():8,d}   (少)")
                continue
            row = f"  {lab[i]:>12s} {m.sum():8,d} {isbo[m].mean():6.1%} {np.nanmean(Ru[m]):+9.3f} "
            for ri in range(len(REGIMES)):
                mm = m & (a_reg == ri)
                row += f"{np.nanmean(Ru[mm]):+8.2f}" if mm.sum() >= 50 else f"{'-':>8s}"
            print(row)

    # -----------------------------------------------------------------
    # 134(2年版)の再現チェック。同じ期間に絞って同じ向きが出るか。
    # 出ないなら134の結論は「相場局面に固有」か「手法差(ユニバース/RS母集団)」であり、
    # どちらなのかを切り分けないと11年版の結論も信用できない。
    # -----------------------------------------------------------------
    print(f"\n{'='*78}\n【134(2年版)の期間だけに絞った再現チェック】2024-07-03〜2026-07-27")
    sub = (a_dt >= "2024-07-03") & (a_dt <= "2026-07-27")
    subb = (dt >= "2024-07-03") & (dt <= "2026-07-27")
    print(f"  該当 全セットアップ {int(sub.sum()):,} / ブレイク {int(subb.sum()):,}")
    for title, key, edges in [
        ("H1 RS", "rs", [30, 50, 70, 85]),
        ("H3 close/52w安", "lr", [1.25, 1.5, 2.0, 3.0]),
        ("H4 MA200傾き", "ma200sl", [0, 0.01, 0.03, 0.06]),
        ("枯れ度", "dryup", [0.66, 0.77, 1.0]),
    ]:
        x = B[key].to_numpy()
        b = np.digitize(x, edges)
        lab = ([f"<{edges[0]:g}"]
               + [f"{edges[i]:g}-{edges[i+1]:g}" for i in range(len(edges) - 1)]
               + [f">={edges[-1]:g}"])
        cells = []
        for i in range(len(edges) + 1):
            m = (b == i) & np.isfinite(x) & subb.to_numpy()
            cells.append(f"{lab[i]}={np.nanmean(Rm[m]):+.3f}(n={m.sum():,})" if m.sum() >= 50 else f"{lab[i]}=少")
        print(f"  {title:18s} " + "  ".join(cells))

    print("\n★注記: 本データは上場廃止銘柄を含まない。標本外率は2015年17.4% / 2023年5.4%。")
    print("  日本の上場廃止はTOB/MBOの比率が高く歪みの向きは自明でない(log.md 135)。")
    print("  古い局面だけで見えた効果を単独の根拠にしないこと。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True,
                    choices=["build", "feat", "rs", "setups", "report"])
    args = ap.parse_args()
    {"build": stage_build, "feat": stage_feat, "rs": stage_rs,
     "setups": stage_setups, "report": stage_report}[args.stage]()


if __name__ == "__main__":
    main()
