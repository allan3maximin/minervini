#!/usr/bin/env python3
"""ベース(保ち合い)の形そのものを測る(2026-08-12・申し送り2番)。

なぜ必要か:
  今の H1〜H7 は**全部ブレイク当日かそれ以降のパラメータ**で、
  ベースの形を見ている指標は「枯れ度」(出来高の細り)しか無い。
  つまり SEPA の中身 — ステージ判定・ベースの形・収縮の構造 — は
  一度も測られていない。今のシステムは実質「52週高値圏のブレイク +
  出来高フィルタ」でしかない。ここを埋めるのがこのスクリプト。

測るもの:
  1. ベースの長さ・深さ
  2. 収縮の回数(高値→安値を1回と数える)
  3. 収縮の深さの推移(毎回浅くなっているか)
  4. 収縮の縮み方(最後 ÷ 最初)
  5. 直前の引き締まり具合
  6. ステージ判定(1〜4)
  7. トレンドテンプレート8項目の達成数

測り方の約束(166〜169で分かった落とし穴を最初から避ける):
  - **同じ日の中だけで比べる**(その日の全セットアップの平均Rを引く)
  - **ATR五分位の中だけで比べる**
  - **ストップの定義5本すべてで符号を確かめる**
  - **2001-2014 と 2015-2026 に割って、どちらでも成立するかを見る**
  統計の部分は `tools/audit_thresholds_dd.py` をそのまま呼び出して使う。
  同じ計算を2箇所に書かない。

未来を覗かないための約束:
  ベースの高値・安値は t 以前の値しか使わない。
  山谷(スイング)の判定は前後5日を見るので、**t-5 より後ろの山谷は使わない**。
  確定していない山谷を使うと、そこだけ未来が入る。

★このスクリプトは診断だけで、本体のパラメータには一切触らない。
  `audit_thresholds_long.py` も `audit_thresholds_dd.py` も書き換えない。

実行(`audit_thresholds_long.py` の build/feat/rs/setups が済んでいる前提):

    python tools/audit_base_quality.py --stage feat     # 特徴量を作る(数分)
    python tools/audit_base_quality.py --stage report   # 統計
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import audit_thresholds_dd as dd  # noqa: E402  統計の部分を借りる

WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))
OUT = WORK / "base_feat.parquet"

MAXB = 250      # ベースを遡って探す上限(営業日・約1年)
FRACT = 5       # 山谷の判定に使う前後の日数
MIN_CTR = 0.02  # これ未満の上下は「収縮」と数えない(刻みノイズ)


def npy(name: str) -> Path:
    return WORK / f"{name}.npy"


# ===========================================================================
# stage feat: ベースの形を測る
# ===========================================================================

def ensure_ma150() -> np.ndarray:
    """トレンドテンプレートに必要な150日平均。無ければ作って置いておく。"""
    p = npy("ma150")
    if p.exists():
        return np.load(p, mmap_mode="r")
    close = np.load(npy("close"))
    ma = pd.DataFrame(close).rolling(150, min_periods=150).mean()
    v = ma.to_numpy(dtype=np.float32)
    np.save(p, v)
    print("  ma150 を作った")
    return v


def swing_flags(high: np.ndarray, low: np.ndarray, k: int = FRACT):
    """前後 k 日の中で一番高い/安い日を山/谷とする。

    i 日が山かどうかは i+k 日まで見ないと決まらないので、
    使う側で「t-k より後ろの山谷は使わない」ことを必ず守る。
    """
    h = pd.Series(high)
    l = pd.Series(low)
    is_h = (h == h.rolling(2 * k + 1, center=True, min_periods=2 * k + 1).max()).to_numpy()
    is_l = (l == l.rolling(2 * k + 1, center=True, min_periods=2 * k + 1).min()).to_numpy()
    return np.flatnonzero(is_h), np.flatnonzero(is_l)


def base_features_for_code(t_arr, high, low, close, sh, sl):
    """1銘柄ぶんのセットアップについて、ベースの形を測る。"""
    n = len(t_arr)
    out = {k: np.full(n, np.nan, dtype=np.float64) for k in (
        "base_len", "base_depth", "n_ctr", "ctr_first", "ctr_last",
        "ctr_shrink", "ctr_mono", "ctr_len_last", "tight10", "dd_from_hi")}

    for i, t in enumerate(t_arr):
        b0 = max(0, t - MAXB)
        w_hi = high[b0:t + 1]
        if w_hi.size < 20 or not np.isfinite(w_hi).any():
            continue
        # ベースの左端 = 直近1年で一番高い日。ここから保ち合いが始まったとみなす。
        bh = b0 + int(np.nanargmax(w_hi))
        base_high = float(high[bh])
        if not np.isfinite(base_high) or base_high <= 0:
            continue
        seg_lo = low[bh:t + 1]
        base_low = float(np.nanmin(seg_lo)) if np.isfinite(seg_lo).any() else np.nan

        out["base_len"][i] = t - bh
        out["base_depth"][i] = (base_high - base_low) / base_high
        out["dd_from_hi"][i] = (base_high - close[t]) / base_high

        # 直前10日の(高値−安値)/終値。引き締まっているほど小さい。
        w = slice(max(0, t - 9), t + 1)
        with np.errstate(all="ignore"):
            out["tight10"][i] = float(np.nanmean(
                (high[w] - low[w]) / np.where(close[w] > 0, close[w], np.nan)))

        # ---- 収縮の列を作る ----
        # ベースの左端(=一番高い日)を1つ目の山として、
        # 山 → 谷 → 山 → 谷 … と交互に拾い、山から谷までの下げ幅を1回の収縮と数える。
        lim = t - FRACT           # 確定していない山谷は使わない
        depths, lens = [], []
        cur_h, cur_hv = bh, base_high
        guard = 0
        while guard < 20:
            guard += 1
            j = np.searchsorted(sl, cur_h, side="right")
            if j >= sl.size or sl[j] > lim:
                break
            lo_i = int(sl[j])
            lo_v = float(low[lo_i])
            if not np.isfinite(lo_v) or cur_hv <= 0:
                break
            d = (cur_hv - lo_v) / cur_hv
            if d >= MIN_CTR:
                depths.append(d)
                lens.append(lo_i - cur_h)
            j2 = np.searchsorted(sh, lo_i, side="right")
            if j2 >= sh.size or sh[j2] > lim:
                break
            cur_h = int(sh[j2])
            cur_hv = float(high[cur_h])

        out["n_ctr"][i] = len(depths)
        if depths:
            out["ctr_first"][i] = depths[0]
            out["ctr_last"][i] = depths[-1]
            out["ctr_shrink"][i] = depths[-1] / depths[0] if depths[0] > 0 else np.nan
            out["ctr_len_last"][i] = lens[-1]
            # 毎回浅くなっているか(同じ深さは許容しない)
            out["ctr_mono"][i] = float(all(
                depths[j + 1] < depths[j] for j in range(len(depths) - 1))
                and len(depths) >= 2)
    return out


def stage_feat() -> None:
    p = WORK / "setups.parquet"
    if not p.exists():
        sys.exit("setups.parquet が無い。先に audit_thresholds_long.py を流すこと。")
    S = pd.read_parquet(p)[["ci", "t"]]
    print(f"セットアップ {len(S):,} 件のベースを測る")

    close = np.load(npy("close"))
    high = np.load(npy("high"))
    low = np.load(npy("low"))
    ma50 = np.load(npy("ma50"), mmap_mode="r")
    ma200 = np.load(npy("ma200"), mmap_mode="r")
    h250 = np.load(npy("h250"), mmap_mode="r")
    l250 = np.load(npy("l250"), mmap_mode="r")
    rs = np.load(npy("rs"), mmap_mode="r")
    ma150 = ensure_ma150()

    ci_all = S["ci"].to_numpy()
    t_all = S["t"].to_numpy()
    order = np.argsort(ci_all, kind="stable")
    cols: dict[str, np.ndarray] = {}
    t0 = time.time()
    uniq, starts = np.unique(ci_all[order], return_index=True)
    starts = list(starts) + [len(order)]

    for u, (ci, a) in enumerate(zip(uniq, starts[:-1])):
        idx = order[a:starts[u + 1]]
        t_arr = t_all[idx]
        h = high[:, ci].astype(np.float64)
        l = low[:, ci].astype(np.float64)
        c = close[:, ci].astype(np.float64)
        sh, sl = swing_flags(h, l)
        res = base_features_for_code(t_arr, h, l, c, sh, sl)
        for k, v in res.items():
            if k not in cols:
                cols[k] = np.full(len(S), np.nan)
            cols[k][idx] = v
        if (u + 1) % 500 == 0:
            print(f"  {u+1}/{len(uniq)} 銘柄  {time.time()-t0:.0f}秒", flush=True)

    # ---- ステージ判定とトレンドテンプレート ----
    print("  ステージ判定とトレンドテンプレート", flush=True)
    ti, ck = t_all, ci_all
    c_t = close[ti, ck].astype(np.float64)
    m50 = np.asarray(ma50[ti, ck], dtype=np.float64)
    m150 = np.asarray(ma150[ti, ck], dtype=np.float64)
    m200 = np.asarray(ma200[ti, ck], dtype=np.float64)
    m200p = np.asarray(ma200[ti - 21, ck], dtype=np.float64)
    m50p = np.asarray(ma50[ti - 21, ck], dtype=np.float64)
    hi52 = np.asarray(h250[ti, ck], dtype=np.float64)
    lo52 = np.asarray(l250[ti, ck], dtype=np.float64)
    rs_t = np.asarray(rs[ti, ck], dtype=np.float64)
    with np.errstate(all="ignore"):
        sl200 = m200 / m200p - 1.0
        sl50 = m50 / m50p - 1.0

    # ステージ: Weinstein/Minervini の4段階を、手元にある平均線で機械化したもの。
    #   4 下降  … 200日線を割っている
    #   2 上昇  … 200日線が上向き、株価>50日線>200日線、50日線も上向き
    #   3 天井圏 … 200日線は上向きだが、株価が50日線を割る or 50日線が下向き
    #   1 底ばい … 200日線がほぼ横ばい
    stage = np.full(len(S), np.nan)
    up200 = sl200 > 0.01
    stage[np.isfinite(c_t)] = 3.0
    stage[np.abs(sl200) <= 0.01] = 1.0
    stage[up200 & (c_t > m50) & (m50 > m200) & (sl50 > 0)] = 2.0
    stage[up200 & ((c_t <= m50) | (sl50 <= 0))] = 3.0
    stage[c_t < m200] = 4.0

    # トレンドテンプレート8項目。満たした数を0〜8で数える。
    tt = np.zeros(len(S))
    for cond in [
        (c_t > m150) & (c_t > m200),
        m150 > m200,
        sl200 > 0,
        (m50 > m150) & (m150 > m200),
        c_t > m50,
        c_t >= lo52 * 1.30,
        c_t >= hi52 * 0.75,
        rs_t >= 70,
    ]:
        # NaN との比較は False になるので、そのまま「満たさなかった」に落ちる
        tt += np.where(cond, 1.0, 0.0)
    # どれか1つでも計算できない(平均線が未確定)なら判定不能にする
    bad = (~np.isfinite(m150) | ~np.isfinite(m200) | ~np.isfinite(m50)
           | ~np.isfinite(sl200) | ~np.isfinite(sl50))
    tt[bad] = np.nan
    stage[bad] = np.nan

    out = pd.DataFrame(cols)
    out["stage"] = stage
    out["tt_score"] = tt
    out["ma50sl"] = sl50
    out["ci"] = ci_all
    out["t"] = t_all
    out.to_parquet(OUT, index=False)
    print(f"\n{OUT} に保存  ({time.time()-t0:.0f}秒)")
    print(out.drop(columns=["ci", "t"]).describe().T.round(3).to_string())


# ===========================================================================
# stage report: 166〜169 と同じ測り方で評価する
# ===========================================================================

# (表示名, 列, 帯の区切り, 仮説の向き, 現状)
BASE_SPECS = [
    ("収縮の回数", "n_ctr", [1, 2, 3, 4], "hi", "(測っていない)"),
    ("収縮が毎回浅いか", "ctr_mono", [1], "hi", "(測っていない)"),
    ("収縮の縮み方", "ctr_shrink", [0.3, 0.5, 0.8], "lo", "(測っていない)"),
    ("最後の収縮の深さ", "ctr_last", [0.05, 0.10, 0.20], "lo", "(測っていない)"),
    ("最初の収縮の深さ", "ctr_first", [0.10, 0.20, 0.35], "lo", "(測っていない)"),
    ("ベースの深さ", "base_depth", [0.15, 0.25, 0.40], "lo", "(測っていない)"),
    ("ベースの長さ", "base_len", [20, 60, 120], "hi", "(測っていない)"),
    ("直前10日の値幅", "tight10", [0.02, 0.03, 0.05], "lo", "(測っていない)"),
    ("高値からの下落率", "dd_from_hi", [0.02, 0.05, 0.12], "lo", "(測っていない)"),
    ("トレンドテンプレート達成数", "tt_score", [5, 6, 7, 8], "hi", "(測っていない)"),
]


def stage_report(since: str | None, until: str | None) -> None:
    if not OUT.exists():
        sys.exit(f"{OUT} が無い。先に --stage feat を流すこと。")
    df = dd.load_setups(since, until)
    F = pd.read_parquet(OUT)
    n0 = len(df)
    df = df.merge(F, on=["ci", "t"], how="left", validate="one_to_one")
    assert len(df) == n0, "結合で件数が変わった"

    fr = dd.Frame(df)
    dd.header(fr, df)

    # 統計の部分は audit_thresholds_dd.py のものをそのまま使う。
    # 見る指標だけ差し替える(同じ計算を2箇所に書かない)。
    dd.SPECS = BASE_SPECS

    print("\n" + "=" * 78)
    print("【ステージ判定】")
    print("  4=200日線割れ / 3=天井圏 / 2=上昇 / 1=底ばい")
    print("  ※セットアップの抽出条件に「200日線の上」が入っているので、")
    print("    ステージ4はほぼ出ない。出るのは判定の境目にいるものだけ。")
    ddm, keep = fr.dd(fr.prim, "setup")
    Rp = fr.R(fr.prim)
    st = fr.B["stage"].to_numpy()
    print(f"  {'ステージ':>10s} {'n':>9s} {'割合':>7s} {'そのまま':>10s} {'同日内':>10s} "
          + "".join(f"{f'ATR{i+1}':>9s}" for i in range(5)))
    for s in (1, 2, 3, 4):
        m = st == s
        if m.sum() < 100:
            print(f"  {s:>10d} {int(m.sum()):9,d}   (少)")
            continue
        row = (f"  {s:>10d} {int(m.sum()):9,d} {m.sum()/len(fr.B):6.1%} "
               f"{np.nanmean(Rp[m]):+10.3f} {np.nanmean(ddm[m & keep]):+10.3f} ")
        for qi in range(5):
            mm = m & keep & (fr.q == qi)
            row += f"{np.nanmean(ddm[mm]):+9.3f}" if mm.sum() >= 60 else f"{'-':>9s}"
        print(row)

    for title, key, edges, _good, cur in BASE_SPECS:
        dd.section_buckets(fr, title, key, edges, cur, df)

    res = dd.section_robust(fr)
    dd.section_era(fr)
    dd.section_verdict(res)

    print("\n★注記: 本データは上場廃止銘柄を含まない(log.md 135)。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="report", choices=["feat", "report"])
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    a = ap.parse_args()
    if a.stage == "feat":
        stage_feat()
    else:
        stage_report(a.since, a.until)


if __name__ == "__main__":
    main()
