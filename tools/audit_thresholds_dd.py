#!/usr/bin/env python3
"""SEPA骨格7閾値の診断を「同じ日の中での比較」でやり直す(2026-08-12)。

`tools/audit_thresholds_long.py --stage report` の作り直し版。
**あちらのファイルは一切触らない**(フォワード検証中の凍結を守るため、
本体はもちろん既存の検証スクリプトも書き換えない)。こちらは新規の別モジュール。

なぜ作り直すのか(log.md 166 / 167):
  25年検証の「生き残った指標 / 壊れている指標」の表は、**25年ぶんの
  セットアップを一緒くたにして帯分けしていた**。ところが指標が低い
  セットアップは「相場全体が跳ねた日」に固まって出るので、
  帯ごとの平均には「銘柄の良し悪し」と「日の良し悪し」が混ざる。
  実際 RS は、その日の平均Rを引くだけで結論が丸ごと反転した
  (RS<30 の +0.168 が -0.003 になって消えた)。

  さらに、期待Rの分母がATRで、RSとATRは相関している(+0.437)。
  日だけ揃えるとプラス、ATRだけ揃えるとマイナス、両方揃えて初めて
  向きが決まる、という状態だった。

  したがって **H1〜H7 と枯れ度の表は全部引き直す必要がある**。
  それがこのスクリプト。

このスクリプトが各指標について出すもの:
  1. 帯ごとの「そのまま」「同日内(ブレイク日基準)」「同日内(セットアップ日基準)」
     と、ATR五分位の中だけで取った同日内R
  2. 良い側 − 悪い側 を **ストップの定義5本すべて**で(定義を変えて符号が
     変わる指標は採らない、という申し送りの基準をそのまま適用)
  3. 同じものを **ATR五分位ごと**に(5×5=25マス)
  4. 2025-26 を抜いても残るか(「直近だけで効く」の棄却基準)
  5. 局面別(同日内R)
  6. 上を機械的に採点した判定表

★ブレイク日を復元している点について
  `setups.parquet` にはブレイク成立日が入っていない(セットアップ日と
  成立/不成立フラグだけ)。ところが H5(ブレイク日の出来高)と
  H7(ピボット超過)は**ブレイク当日**の値なので、日を揃えるなら
  セットアップ日ではなくブレイク日で揃えないと意味がない。
  そこで `audit_thresholds_long.stage_setups` と同じ手順でブレイク日を
  復元し直して使う(復元結果が元の成立フラグと一致するかも検算する)。

実行(`audit_thresholds_long.py` の build/feat/rs/setups が済んでいる前提):

    python tools/audit_thresholds_dd.py                      # 全期間
    python tools/audit_thresholds_dd.py --until 2024-12-31   # 2025-26を除く
    python tools/audit_thresholds_dd.py --part verdict       # 判定表だけ
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))

WAIT, POST = 20, 10  # audit_thresholds_long.py と同一
MIN_DAY_N = 10       # 同じ日に何件以上あれば「日の平均」を信用するか

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

# 申し送りの感度表と同じ5本。1本でも符号が違う指標は採らない。
def stop_defs(atrp: np.ndarray) -> dict[str, np.ndarray]:
    return {
        "1.5ATR(3-12%)": np.clip(1.5 * atrp, 0.03, 0.12),
        "1.5ATR(clip無)": 1.5 * atrp,
        "2.0ATR": 2.0 * atrp,
        "固定5%": np.full_like(atrp, 0.05),
        "固定8%": np.full_like(atrp, 0.08),
    }


# ---------------------------------------------------------------------------
# 指標の一覧。「良い側」は仮説が言っている向きで、数字がそれに従うかを見る。
#   good="hi" … 大きい方が良いはず   good="lo" … 小さい方が良いはず
# ---------------------------------------------------------------------------
SPECS = [
    ("H1 RS", "rs", [30, 50, 70, 85], "hi", "RS >= 70"),
    ("H2 52週高値比", "hr", [0.75, 0.85, 0.92, 0.97], "hi", "close/52w高 >= 0.75"),
    ("H3 52週安値倍率", "lr", [1.25, 1.5, 2.0, 3.0], "hi", "close/52w安 >= 1.25"),
    ("H4 MA200の21日傾き", "ma200sl", [0, 0.01, 0.03, 0.06], "hi", "21日前比プラス"),
    ("H5 ブレイク日の出来高", "bo_vol", [1.0, 1.4, 2.0, 3.0], "hi", ">= 1.4倍"),
    ("H7 ピボット超過", "bo_gap", [0.01, 0.03, 0.05, 0.10], "lo", "5%超で見送り"),
    ("枯れ度", "dryup", [0.66, 0.77, 1.0], "lo", "0.66で強・0.77で弱"),
    ("ピボットまでの距離", "dist", [0.02, 0.05, 0.10], "lo", "(閾値なし・15%以内で抽出)"),
]

# H5/H7 はブレイク当日の値なので、日を揃えるならブレイク日で揃える。
# それ以外はセットアップ日の値なので、どちらでも大差ないはずだが両方出す。
BREAKOUT_DAY_KEYS = {"bo_vol", "bo_gap"}


def npy(name: str) -> Path:
    return WORK / f"{name}.npy"


# ===========================================================================
# 読み込みとブレイク日の復元
# ===========================================================================

def load_setups(since: str | None, until: str | None) -> pd.DataFrame:
    p = WORK / "setups.parquet"
    if not p.exists():
        sys.exit(
            f"{p} が無い。先に\n"
            "  python tools/audit_thresholds_long.py --stage build / feat / rs / setups\n"
            "を流すこと。"
        )
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    df["bo_t"] = restore_breakout_index(df)
    dates = np.load(npy("dates"))
    bt = df["bo_t"].to_numpy()
    bd = np.full(len(df), np.datetime64("NaT"), dtype="datetime64[ns]")
    ok = bt >= 0
    bd[ok] = dates[bt[ok]]
    df["bo_date"] = pd.to_datetime(bd)

    if since:
        df = df[df["date"] >= since]
    if until:
        df = df[df["date"] <= until]
    if since or until:
        print(f"※期間を {since or '最初'} 〜 {until or '最後'} に限定")
    return df.reset_index(drop=True)


def restore_breakout_index(df: pd.DataFrame) -> np.ndarray:
    """セットアップごとのブレイク成立日(行番号)を復元する。

    `setups.parquet` には成立/不成立のフラグしか入っていないので、
    `audit_thresholds_long.stage_setups` と同じ手順をなぞって日付を取り直す。
    ピボットを終値で上抜けた最初の日が成立日。ピボットの-10%を先に割ったら失格。
    """
    cache = WORK / "bo_t.npy"
    if cache.exists():
        v = np.load(cache)
        if v.size == len(df):
            return v

    close = np.load(npy("close"), mmap_mode="r")
    h20 = np.load(npy("h20"), mmap_mode="r")
    T = close.shape[0]
    t = df["t"].to_numpy()
    ci = df["ci"].to_numpy()
    piv = np.asarray(h20[t, ci], dtype=np.float64)

    bo = np.full(len(df), -1, dtype=np.int64)
    alive = np.ones(len(df), dtype=bool)
    for k in range(1, WAIT + 1):
        j = np.minimum(t + k, T - 1)
        cj = np.asarray(close[j, ci], dtype=np.float64)
        hit = alive & (cj > piv)
        bo[hit] = j[hit]
        alive &= ~hit
        alive &= ~(alive & (cj < piv * 0.90))

    # 検算: 復元した成立/不成立が、保存されているフラグと一致するか
    mism = int(((bo >= 0) != (df["bo"].to_numpy() == 1)).sum())
    print(f"ブレイク日の復元: 不一致 {mism:,} / {len(df):,} 件"
          + ("  ← 0 でないなら復元手順が元と違う。結果を信用しないこと。"
             if mism else "  (一致)"))
    np.save(cache, bo)
    return bo


def R_of(mae: np.ndarray, ret: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """刈られたら -1R、生き残ったら 10日リターン / ストップ幅。"""
    with np.errstate(all="ignore"):
        return np.where(mae > sd, -1.0, ret / sd)


def daydemean(R: np.ndarray, day: pd.Series, min_n: int = MIN_DAY_N):
    """その日のセットアップ全体の平均Rを引く。相場の良し悪しが丸ごと消える。

    同じ日に min_n 件未満しか無い日は、平均が1〜2件で決まって
    引き算が雑音を足すだけなので使わない。
    """
    s = pd.DataFrame({"d": day.to_numpy(), "R": R})
    n = s.groupby("d")["R"].transform("size").to_numpy()
    mu = s.groupby("d")["R"].transform("mean").to_numpy()
    keep = (n >= min_n) & np.isfinite(R) & np.isfinite(mu)
    return R - mu, keep


def buckets(x: np.ndarray, edges: list[float]):
    b = np.digitize(x, edges)
    lab = ([f"<{edges[0]:g}"]
           + [f"{edges[i]:g}-{edges[i+1]:g}" for i in range(len(edges) - 1)]
           + [f">={edges[-1]:g}"])
    return b, lab


# ===========================================================================
# 本体
# ===========================================================================

class Frame:
    """ブレイク到達したセットアップと、比較に必要な道具一式。"""

    def __init__(self, df: pd.DataFrame):
        B = df[df["bo"] == 1].reset_index(drop=True)
        self.B = B
        self.atrp = B["atrp"].to_numpy()
        self.mae = B["mae"].to_numpy()
        self.ret = B["ret10"].to_numpy()
        self.setup_day = B["date"]
        self.bo_day = B["bo_date"]
        self.defs = stop_defs(self.atrp)
        self.prim = "1.5ATR(3-12%)"
        # ATR五分位。指標とATRは絡んでいるので、必ずこの中だけで比べる。
        self.q = pd.qcut(pd.Series(self.atrp), 5, labels=False,
                         duplicates="drop").to_numpy()
        self.ex2025 = (B["date"] < "2025-01-01").to_numpy()
        self.n_all = len(df)

    def R(self, name: str) -> np.ndarray:
        return R_of(self.mae, self.ret, self.defs[name])

    def dd(self, name: str, key: str):
        """ストップ定義 name の同日内R。key='bo' ならブレイク日で揃える。"""
        day = self.bo_day if key == "bo" else self.setup_day
        return daydemean(self.R(name), day)


def header(fr: Frame, df: pd.DataFrame) -> None:
    B = fr.B
    Rp = fr.R(fr.prim)
    print(f"\n全セットアップ n={len(df):,}  ブレイク到達 n={len(B):,} "
          f"({len(B)/max(len(df),1):.1%})  "
          f"期間 {df['date'].min().date()}〜{df['date'].max().date()}")
    print(f"銘柄数 {df['code'].nunique():,}  そのままの平均R={np.nanmean(Rp):+.3f}")
    for k in ("bo", "setup"):
        _, keep = fr.dd(fr.prim, k)
        nm = "ブレイク日" if k == "bo" else "セットアップ日"
        print(f"  同じ日に{MIN_DAY_N}件以上ある分だけ使う({nm}で揃える): "
              f"n={int(keep.sum()):,} ({keep.sum()/len(B):.0%})")


def section_baseline(fr: Frame, df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("【局面別のベースライン】")
    print("  同日内Rは定義上その日の平均がゼロなので、局面平均もほぼゼロになる。")
    print("  ここに出る『そのまま』の差が、指標ではなく相場そのものの取り分。")
    Rp = fr.R(fr.prim)
    dt = fr.B["date"]
    print(f"  {'局面':32s} {'n':>7s} {'到達率':>7s} {'そのままの平均R':>16s}")
    for nm, a, b in REGIMES:
        m = ((dt >= a) & (dt <= b)).to_numpy()
        allm = ((df["date"] >= a) & (df["date"] <= b)).to_numpy()
        if m.sum() < 50:
            continue
        print(f"  {nm:32s} {m.sum():7,d} {m.sum()/max(allm.sum(),1):6.1%} "
              f"{np.nanmean(Rp[m]):+16.3f}")


def section_buckets(fr: Frame, title: str, key: str, edges: list[float],
                    cur: str, df: pd.DataFrame) -> None:
    B = fr.B
    x = B[key].to_numpy()
    xa = df[key].to_numpy()
    b, lab = buckets(x, edges)
    xf = np.isfinite(x)

    daykey = "bo" if key in BREAKOUT_DAY_KEYS else "setup"
    Rp = fr.R(fr.prim)
    dd_bo, keep_bo = fr.dd(fr.prim, "bo")
    dd_su, keep_su = fr.dd(fr.prim, "setup")
    # ATR五分位の列は、その指標にふさわしい日の揃え方の方を使う
    dd_main, keep_main = (dd_bo, keep_bo) if daykey == "bo" else (dd_su, keep_su)

    print("\n" + "=" * 78)
    print(f"{title}   現閾値: {cur}")
    print(f"  分布 p10/25/50/75/90 = {np.nanpercentile(xa[np.isfinite(xa)], [10,25,50,75,90]).round(3)}")
    if key in BREAKOUT_DAY_KEYS:
        print("  ※ブレイク当日の値なので、日はブレイク日で揃えている")
    print(f"  {'帯':>12s} {'n':>8s} {'そのまま':>10s} {'同日内(ブ)':>12s} "
          f"{'同日内(セ)':>12s} " + "".join(f"{f'ATR{i+1}':>9s}" for i in range(5)))
    for i in range(len(lab)):
        m = (b == i) & xf
        if m.sum() < 100:
            print(f"  {lab[i]:>12s} {m.sum():8,d}   (少)")
            continue
        mb, ms = m & keep_bo, m & keep_su
        row = (f"  {lab[i]:>12s} {m.sum():8,d} {np.nanmean(Rp[m]):+10.3f} "
               f"{np.nanmean(dd_bo[mb]):+12.3f} {np.nanmean(dd_su[ms]):+12.3f} ")
        for qi in range(5):
            mm = m & keep_main & (fr.q == qi)
            row += f"{np.nanmean(dd_main[mm]):+9.3f}" if mm.sum() >= 60 else f"{'-':>9s}"
        print(row)


def good_bad_masks(fr: Frame, key: str, edges: list[float], good: str):
    """仮説が『良い』と言う側と『悪い』と言う側の2つのマスクを返す。"""
    x = fr.B[key].to_numpy()
    xf = np.isfinite(x)
    if good == "hi":
        g, bmask = xf & (x >= edges[-1]), xf & (x < edges[0])
        glab, blab = f">={edges[-1]:g}", f"<{edges[0]:g}"
    else:
        g, bmask = xf & (x < edges[0]), xf & (x >= edges[-1])
        glab, blab = f"<{edges[0]:g}", f">={edges[-1]:g}"
    return g, bmask, glab, blab


def section_robust(fr: Frame) -> dict[str, dict]:
    """ストップ定義5本 × ATR五分位5つ × 2025-26除外、で符号が保つか。"""
    print("\n" + "=" * 78)
    print("【頑健性】良い側 − 悪い側 を、ストップの定義5本すべてで")
    print("  1本でも符号が違う指標は採らない(申し送りの基準)。")
    out: dict[str, dict] = {}
    for title, key, edges, good, _cur in SPECS:
        g, bd, glab, blab = good_bad_masks(fr, key, edges, good)
        daykey = "bo" if key in BREAKOUT_DAY_KEYS else "setup"
        print(f"\n  --- {title}   ({glab}) − ({blab})   n={int(g.sum()):,} / {int(bd.sum()):,}")
        print(f"      {'ストップ':16s}" + "".join(f"{f'ATR{i+1}':>10s}" for i in range(5))
              + f"{'全体':>10s}{'2025-26除く':>12s}")
        rec = {"spreads": [], "cells": [], "ex": []}
        for nm in fr.defs:
            dd, keep = fr.dd(nm, daykey)
            row = f"      {nm:16s}"
            for qi in list(range(5)) + [None]:
                mq = (fr.q == qi) if qi is not None else np.ones(len(fr.B), bool)
                hi, lo = g & keep & mq, bd & keep & mq
                if hi.sum() < 50 or lo.sum() < 50:
                    row += f"{'-':>10s}"
                    if qi is not None:
                        rec["cells"].append(np.nan)
                    else:
                        rec["spreads"].append(np.nan)
                    continue
                v = float(np.nanmean(dd[hi]) - np.nanmean(dd[lo]))
                row += f"{v:+10.3f}"
                (rec["cells"] if qi is not None else rec["spreads"]).append(v)
            hi = g & keep & fr.ex2025
            lo = bd & keep & fr.ex2025
            if hi.sum() >= 50 and lo.sum() >= 50:
                v = float(np.nanmean(dd[hi]) - np.nanmean(dd[lo]))
                row += f"{v:+12.3f}"
                rec["ex"].append(v)
            else:
                row += f"{'-':>12s}"
                rec["ex"].append(np.nan)
            print(row)
        out[title] = rec
    return out


def section_regime(fr: Frame) -> None:
    print("\n" + "=" * 78)
    print("【局面別】良い側 − 悪い側(同日内R・1.5ATR(3-12%))")
    print("  符号が局面をまたいで揃うかを見る。片方の局面だけで出る指標は当てにしない。")
    dt = fr.B["date"]
    rows = []
    for title, key, edges, good, _c in SPECS:
        g, bd, _, _ = good_bad_masks(fr, key, edges, good)
        daykey = "bo" if key in BREAKOUT_DAY_KEYS else "setup"
        dd, keep = fr.dd(fr.prim, daykey)
        row = [title]
        for _nm, a, b in REGIMES:
            m0 = ((dt >= a) & (dt <= b)).to_numpy()
            hi, lo = g & keep & m0, bd & keep & m0
            row.append(f"{np.nanmean(dd[hi]) - np.nanmean(dd[lo]):+7.2f}"
                       if (hi.sum() >= 30 and lo.sum() >= 30) else f"{'-':>7s}")
        rows.append(row)
    print(f"  {'指標':22s}" + "".join(f"{nm.split()[0]:>8s}" for nm, _, _ in REGIMES))
    for row in rows:
        print(f"  {row[0]:22s}" + "".join(f"{v:>8s}" for v in row[1:]))


def section_verdict(res: dict[str, dict]) -> None:
    print("\n" + "=" * 78)
    print("【判定】")
    print("  定義5本 … ストップの定義を変えても符号が同じか (5点満点)")
    print("  ATR25マス … ATR五分位 × 定義5本の25マスで符号が同じか (25点満点)")
    print("  2025-26除く … 直近2年を抜いても符号が残るか / その時の大きさ")
    print()
    print("  向き … 仮説どおりなら『順』。『逆』は閾値が裏目に回っているという意味。")
    print("  ※『マスが足りない』は符号が違うのではなく標本が無いだけの場合がある。")
    print()
    print(f"  {'指標':22s}{'幅':>8s}{'向き':>5s}{'定義5本':>9s}{'ATR有効マス':>12s}"
          f"{'2025-26除く':>12s}{'残存率':>8s}  判定")
    for title, rec in res.items():
        sp = np.array(rec["spreads"], dtype=float)
        ce = np.array(rec["cells"], dtype=float)
        ex = np.array(rec["ex"], dtype=float)
        base = sp[0] if sp.size and np.isfinite(sp[0]) else np.nan
        if not np.isfinite(base) or base == 0:
            print(f"  {title:22s}{'-':>8s}{'-':>5s}{'-':>9s}{'-':>12s}"
                  f"{'-':>12s}{'-':>8s}  判定不能")
            continue
        s = np.sign(base)
        n_def = int(np.nansum(np.sign(sp) == s))
        # 標本が足りずに空いたマスは分母から外す(符号が違うのと混同しない)
        n_have = int(np.isfinite(ce).sum())
        n_cell = int(np.nansum(np.sign(ce) == s))
        exv = ex[0] if ex.size and np.isfinite(ex[0]) else np.nan
        keep_ratio = exv / base if np.isfinite(exv) else np.nan
        cell_ok = n_have >= 15 and n_cell >= n_have - 2
        cell_mid = n_have >= 15 and n_cell >= n_have - 5
        if n_def == 5 and cell_ok and np.isfinite(keep_ratio) and keep_ratio >= 0.5:
            v = "◎ 効果あり"
        elif n_def == 5 and cell_mid and np.isfinite(keep_ratio) and keep_ratio > 0:
            v = "○ 残る"
        elif n_def >= 4 and np.isfinite(keep_ratio) and keep_ratio > 0:
            v = "△ 条件つき"
        else:
            v = "× 採らない"
        if s < 0:
            v += " ★閾値が逆向き"
        print(f"  {title:22s}{base:+8.3f}{'順' if s > 0 else '逆':>5s}"
              f"{n_def:>7d}/5{n_cell:>9d}/{n_have:<2d}"
              f"{exv:+12.3f}{keep_ratio:>8.0%}  {v}")
    print("\n  ★『幅』は 1.5ATR(3-12%) での 良い側 − 悪い側(同日内R)。")
    print("    『良い側』は仮説が良いと言っている側なので、仮説が当たっていれば")
    print("    必ずプラスになる。マイナスなら仮説が逆を向いている。")


def section_era(fr: Frame) -> None:
    """時代で符号が変わる指標を炙り出す。2014年までと2015年からで割る。

    2025-26 を抜くだけでは「直近2年への当てはめ」しか見えない。
    もっと長い単位で意味が入れ替わっている指標(売買の主体や
    値動きの粗さが変わったなど)は、前半と後半で割ると見える。
    """
    print("\n" + "=" * 78)
    print("【前半 / 後半】良い側 − 悪い側(同日内R・1.5ATR(3-12%))")
    print("  2001-2014 と 2015-2026 で符号が入れ替わる指標は、")
    print("  『いつの相場の話か』を確かめずに使えない。")
    dt = fr.B["date"]
    early = (dt < "2015-01-01").to_numpy()
    late = ~early
    print(f"  {'指標':22s}{'2001-2014':>12s}{'n(良/悪)':>16s}"
          f"{'2015-2026':>12s}{'n(良/悪)':>16s}")
    for title, key, edges, good, _c in SPECS:
        g, bd, _, _ = good_bad_masks(fr, key, edges, good)
        daykey = "bo" if key in BREAKOUT_DAY_KEYS else "setup"
        dd, keep = fr.dd(fr.prim, daykey)
        row = f"  {title:22s}"
        for m0 in (early, late):
            hi, lo = g & keep & m0, bd & keep & m0
            if hi.sum() >= 50 and lo.sum() >= 50:
                row += f"{np.nanmean(dd[hi]) - np.nanmean(dd[lo]):+12.3f}"
            else:
                row += f"{'-':>12s}"
            row += f"{f'{int(hi.sum()):,}/{int(lo.sum()):,}':>16s}"
        print(row)


def section_tail(fr: Frame) -> None:
    """数の少ない側の帯が、少数の銘柄の当たりで出来ていないかを見る。

    ピボットを大きく飛び越えた側(H7)と 52週安値倍率の尻尾(H3)は
    件数が少ないので、ここが数銘柄で出来ているなら結論にできない。
    """
    print("\n" + "=" * 78)
    print("【少数帯の中身】その帯が何銘柄で出来ているか")
    dd, keep = fr.dd(fr.prim, "bo")
    dd_s, keep_s = fr.dd(fr.prim, "setup")
    B = fr.B
    targets = [
        ("H7 ピボット超過 >=10%", B["bo_gap"].to_numpy() >= 0.10, True),
        ("H7 ピボット超過 5-10%", (B["bo_gap"].to_numpy() >= 0.05)
         & (B["bo_gap"].to_numpy() < 0.10), True),
        ("H3 52週安値倍率 >=3", B["lr"].to_numpy() >= 3.0, False),
        ("ピボットまでの距離 >=10%", B["dist"].to_numpy() >= 0.10, False),
    ]
    for nm, cond, use_bo in targets:
        d, k = (dd, keep) if use_bo else (dd_s, keep_s)
        m = cond & k
        if m.sum() < 50:
            print(f"\n  --- {nm}: 件数不足")
            continue
        T = B[m]
        vc = T["code"].value_counts()
        drop = set(vc.head(20).index)
        m2 = m & ~B["code"].isin(drop).to_numpy()
        early = m & (B["date"] < "2015-01-01").to_numpy()
        late = m & ~(B["date"] < "2015-01-01").to_numpy()
        print(f"\n  --- {nm}")
        print(f"      n={int(m.sum()):,}  銘柄数={T['code'].nunique():,}  "
              f"同日内R={np.nanmean(d[m]):+.3f}  ATR平均={np.nanmean(fr.atrp[m]):.3f}")
        print(f"      1銘柄あたり中央値 {vc.median():.0f} 件 / 最多 {vc.iloc[0]} 件 "
              f"({vc.index[0]}) / 上位10銘柄で {vc.head(10).sum()/len(T):.1%}")
        print(f"      最多20銘柄を抜くと n={int(m2.sum()):,} "
              f"同日内R={np.nanmean(d[m2]):+.3f}")
        print(f"      2001-2014: n={int(early.sum()):,} {np.nanmean(d[early]):+.3f}  /  "
              f"2015-2026: n={int(late.sum()):,} {np.nanmean(d[late]):+.3f}")
        yc = T["date"].dt.year.value_counts().sort_index()
        print("      年別: " + " ".join(f"{y}:{c:,}" for y, c in yc.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all",
                    choices=["all", "buckets", "robust", "regime", "verdict",
                             "era", "tail"])
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    args = ap.parse_args()

    df = load_setups(args.since, args.until)
    fr = Frame(df)
    header(fr, df)

    if args.part in ("all", "buckets"):
        section_baseline(fr, df)
        for title, key, edges, _good, cur in SPECS:
            section_buckets(fr, title, key, edges, cur, df)

    res = {}
    if args.part in ("all", "robust", "verdict"):
        res = section_robust(fr)
    if args.part in ("all", "regime"):
        section_regime(fr)
    if args.part in ("all", "era"):
        section_era(fr)
    if args.part in ("all", "tail"):
        section_tail(fr)
    if args.part in ("all", "verdict") and res:
        section_verdict(res)

    print("\n★注記: 本データは上場廃止銘柄を含まない(log.md 135)。")
    print("  日を揃えたことで『その日の生き残り同士の比較』にはなったが、")
    print("  消えた銘柄が母集団に居ないことは変わらない。")


if __name__ == "__main__":
    main()
