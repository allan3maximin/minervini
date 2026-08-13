#!/usr/bin/env python3
"""現行の閾値と 2026-08 の見直し案(A-1〜A-4)を、同じ土俵で並べて比べる。

`THRESHOLD_REVIEW_2026-08.md` に書いた案が、25年ぶんのデータで実際に
どれだけ成績を変えるのかを出す。**本体のコードも既存の検証スクリプトも
一切触らない**(フォワード検証中の凍結を守るため。log.md 165〜173)。

前提: `tools/audit_thresholds_long.py --stage build/feat/rs/setups` が
済んでいて `data/audit_cache/setups.parquet` がある状態。

    python tools/audit_proposal_compare.py
    python tools/audit_proposal_compare.py --until 2024-12-31   # 直近2年を抜く

────────────────────────────────────────────────────────────────────────
この比較で分かること・分からないこと
────────────────────────────────────────────────────────────────────────
分かること:
  - 各案が「1件あたりの成績」をどれだけ上げるか
  - そのために**取引が何件減るか**、そして掛け算した**合計**が増えるのか
  - その改善が前半(2001-2014)と後半(2015-2026)の両方で出るのか
  - 11の局面それぞれで、現行に勝つのか負けるのか

分からないこと(読むときの注意):
  1. `setups.parquet` には150日線が入っていないので、
     「150日線 > 200日線」「50日線 > 150日線 > 200日線」の2条件だけは
     現行側にも案側にも掛かっていない。**両方に等しく掛かっていない**ので
     比較そのものは公平だが、絶対値は本番の抽出とズレる。
  2. 上場廃止になった銘柄がデータに居ない。負けた銘柄が消えているので、
     どの数字も実際より良く出ている。**案どうしの比較には効かないが、
     絶対値は信用しないこと。**
  3. 期待R(R)は、損切り幅で割った値。幅が狭いほど自動的に大きくなる
     クセがあるので、実際の損益(10日リターン)も並べて出している。
     判断は両方を見てからにすること(log.md 171)。
  4. 手数料・スリッページは入っていない。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

WORK = Path(os.environ.get("MNV_AUDIT_WORK", ROOT / "data" / "audit_cache"))

import audit_thresholds_dd as dd  # noqa: E402

ERA_SPLIT = "2015-01-01"


# ===========================================================================
# ルールの定義
# ===========================================================================
#
# 抽出の時点ですでに掛かっている条件(現行・案の共通土台):
#   ユニバース内 / 終値 > 200日線 / ピボットまで0〜15%
#
# ここで足すのが、config.yaml に書いてある閾値のうち parquet で表現できるもの。
#   rs        >= 70     trend_template.rs_min
#   lr        >= 1.25   trend_template.low52w_margin
#   hr        >= 0.75   trend_template.high52w_margin
#   ma200sl   >  0      trend_template.ma200_up_days_min: 21 の代理
#                       (本番は「上向きの日数」を数えるが、parquet には
#                        21日前比しか無い。H4 の診断もこの代理で通してきた)
#   above50   == 1      trend_template.close_above_ma50
#   bo_vol    >= 1.4    entry.breakout_vol_mult   ←ブレイク当日
#   bo_gap    <= 0.05   entry.extended_pct        ←ブレイク当日
#
# 案:
#   A-1  rs >= 85
#   A-2  ma200sl >= 0.06 を追加
#   A-3  bo_vol の条件を外す
#   A-4  dist(ピボットまでの距離)を足切りではなく加点に使う
#        → 加点は「候補が資金より多いとき」にしか効かないので、
#          ここでは代理として「その日の通過分のうち dist が近い方の半分」を
#          採った場合を出す。あくまで参考値。


def make_rule(rs_min=70.0, slope_min=0.0, use_vol=True, gap_max=0.05):
    def must(d: pd.DataFrame) -> np.ndarray:
        m = (
            (d["rs"].to_numpy() >= rs_min)
            & (d["lr"].to_numpy() >= 1.25)
            & (d["hr"].to_numpy() >= 0.75)
            & (d["ma200sl"].to_numpy() >= slope_min)
            & (d["above50"].to_numpy() > 0.5)
        )
        return np.asarray(m, dtype=bool)

    def entry(d: pd.DataFrame) -> np.ndarray:
        # NaN との比較は False になるので、ブレイクしていない行は自動で落ちる。
        m = d["bo_gap"].to_numpy() <= gap_max
        if use_vol:
            m = m & (d["bo_vol"].to_numpy() >= 1.4)
        return np.asarray(m, dtype=bool)

    return must, entry


RULES: list[tuple[str, dict, bool]] = [
    # (表示名, make_rule への引数, A-4の絞り込みを掛けるか)
    ("(参考)条件なし", dict(rs_min=-1e9, slope_min=-1e9, use_vol=False, gap_max=1e9), False),
    ("現行", dict(), False),
    ("現行 +A-1 (RS>=85)", dict(rs_min=85.0), False),
    ("現行 +A-2 (200日線+6%)", dict(slope_min=0.06), False),
    ("現行 -A-3 (出来高条件を外す)", dict(use_vol=False), False),
    ("A-1+A-3", dict(rs_min=85.0, use_vol=False), False),
    ("A-1+A-2", dict(rs_min=85.0, slope_min=0.06), False),
    ("提案 A-1+A-2+A-3", dict(rs_min=85.0, slope_min=0.06, use_vol=False), False),
    ("提案 +A-4 (近い方半分)", dict(rs_min=85.0, slope_min=0.06, use_vol=False), True),
]


# ===========================================================================
# 集計
# ===========================================================================

class Book:
    """ブレイク到達した全取引と、そこから成績を出す道具。"""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.bo = np.asarray(df["bo"].to_numpy() == 1, dtype=bool)
        self.B = df[self.bo].reset_index(drop=True)
        self.atrp = self.B["atrp"].to_numpy()
        self.mae = self.B["mae"].to_numpy()
        self.ret = self.B["ret10"].to_numpy()
        self.dist = self.B["dist"].to_numpy()
        self.defs = dd.stop_defs(self.atrp)
        self.prim = "1.5ATR(3-12%)"
        self.R = {k: dd.R_of(self.mae, self.ret, v) for k, v in self.defs.items()}

        # 実際の損益。刈られたらその幅ぶんの損、生き残ったら10日リターン。
        sd = self.defs[self.prim]
        with np.errstate(all="ignore"):
            self.pnl = np.where(self.mae > sd, -sd, self.ret)

        self.early = (self.B["date"] < ERA_SPLIT).to_numpy()
        self.bo_day = self.B["bo_date"]
        self.n_days = df["date"].dt.normalize().nunique()
        self.years = (df["date"].max() - df["date"].min()).days / 365.25

    def apply_a4(self, sel: np.ndarray) -> np.ndarray:
        """通過分のうち、その日のなかで dist が近い方の半分だけ残す。"""
        if sel.sum() == 0:
            return sel
        s = pd.DataFrame({"d": self.bo_day.to_numpy(), "x": self.dist})
        s = s[sel]
        # 同じ日に1件しか無ければ残す(順位づけようがない)
        r = s.groupby("d")["x"].rank(pct=True, method="average").to_numpy()
        n = s.groupby("d")["x"].transform("size").to_numpy()
        keep_sub = (n <= 1) | (r <= 0.5)
        out = np.zeros_like(sel)
        out[np.flatnonzero(sel)[keep_sub]] = True
        return out


def fmt(v, w=8, p=3):
    return " " * w if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:>{w}.{p}f}"


def row_stats(bk: Book, sel: np.ndarray) -> dict:
    n = int(sel.sum())
    if n == 0:
        return dict(n=0)
    Rp = bk.R[bk.prim][sel]
    Rp = Rp[np.isfinite(Rp)]
    pnl = bk.pnl[sel]
    pnl = pnl[np.isfinite(pnl)]
    e = bk.early[sel]
    Re = bk.R[bk.prim][sel]
    out = dict(
        n=n,
        meanR=float(np.mean(Rp)) if Rp.size else np.nan,
        totR=float(np.sum(Rp)) if Rp.size else np.nan,
        meanP=float(np.mean(pnl)) * 100 if pnl.size else np.nan,
        totP=float(np.sum(pnl)) * 100 if pnl.size else np.nan,
        win=float(np.mean(pnl > 0)) * 100 if pnl.size else np.nan,
        n_e=int(e.sum()),
        n_l=int((~e).sum()),
        R_e=float(np.nanmean(Re[e])) if e.sum() else np.nan,
        R_l=float(np.nanmean(Re[~e])) if (~e).sum() else np.nan,
    )
    for k, arr in bk.R.items():
        v = arr[sel]
        v = v[np.isfinite(v)]
        out[f"R:{k}"] = float(np.mean(v)) if v.size else np.nan
    return out


def build_rows(bk: Book) -> list[tuple[str, dict, np.ndarray, int]]:
    rows = []
    for label, kw, use_a4 in RULES:
        must, entry = make_rule(**kw)
        must_all = must(bk.df)
        n_setup = int(must_all.sum())
        sel = must(bk.B) & entry(bk.B)
        if use_a4:
            sel = bk.apply_a4(sel)
        st = row_stats(bk, sel)
        st["n_setup"] = n_setup
        rows.append((label, st, sel, n_setup))
    return rows


# ===========================================================================
# 出力
# ===========================================================================

def section_main(bk: Book, rows) -> None:
    base = next(st for lab, st, _, _ in rows if lab == "現行")
    bR = base.get("totR", np.nan)
    bP = base.get("totP", np.nan)

    print("\n" + "=" * 100)
    print("【1】現行 vs 案  ── 1件あたりと、掛け算した合計の両方を見る")
    print("=" * 100)
    print("  合計R比 / 合計損益比 は現行を1.00としたとき。1.00を割るなら、")
    print("  「1件あたりは良くなったが取引が減りすぎて総取りは負けている」ということ。\n")

    hd = (f"  {'ルール':<26s}{'候補数':>10s}{'取引数':>9s}{'期待R':>8s}"
          f"{'合計R':>10s}{'合計R比':>9s}{'平均損益%':>10s}{'合計損益比':>10s}{'勝率%':>7s}")
    print(hd)
    print("  " + "-" * (len(hd) - 2))
    for label, st, _, _ in rows:
        if st["n"] == 0:
            print(f"  {label:<26s}{'該当なし':>10s}")
            continue
        rr = st["totR"] / bR if np.isfinite(bR) and bR else np.nan
        pp = st["totP"] / bP if np.isfinite(bP) and bP else np.nan
        print(f"  {label:<26s}{st['n_setup']:>10,}{st['n']:>9,}"
              f"{st['meanR']:>8.3f}{st['totR']:>10.0f}{rr:>9.2f}"
              f"{st['meanP']:>10.3f}{pp:>10.2f}{st['win']:>7.1f}")

    print(f"\n  候補数 = セットアップ段階で条件を通った件数(25年で {bk.years:.1f}年 / "
          f"営業日 {bk.n_days:,}日)")
    print("  取引数 = そのうちブレイクが成立し、ブレイク当日の条件も通った件数")


def section_era(bk: Book, rows) -> None:
    print("\n" + "=" * 100)
    print("【2】前半(2001-2014) / 後半(2015-2026) ── 後半で消えるなら採らない")
    print("=" * 100)
    print("  『直近2年で効くが25年で消える』の裏返しで、『前半でしか効かない』のも同じく失格。")
    print("  現行との差(Δ)が後半でもプラスに残っているかだけを見ればいい。\n")

    base = next(st for lab, st, _, _ in rows if lab == "現行")
    hd = (f"  {'ルール':<26s}{'前半n':>9s}{'前半R':>8s}{'Δ前半':>8s}"
          f"{'後半n':>9s}{'後半R':>8s}{'Δ後半':>8s}")
    print(hd)
    print("  " + "-" * (len(hd) - 2))
    for label, st, _, _ in rows:
        if st["n"] == 0:
            continue
        de = st["R_e"] - base["R_e"]
        dl = st["R_l"] - base["R_l"]
        mark = ""
        if label not in ("現行", "(参考)条件なし"):
            mark = "  ←後半で消えている" if dl <= 0.01 else ""
        print(f"  {label:<26s}{st['n_e']:>9,}{st['R_e']:>8.3f}{de:>+8.3f}"
              f"{st['n_l']:>9,}{st['R_l']:>8.3f}{dl:>+8.3f}{mark}")


def section_stopdefs(bk: Book, rows) -> None:
    print("\n" + "=" * 100)
    print("【3】損切りの定義を5本とも変えてみる ── 1本でも符号が変われば採らない")
    print("=" * 100)
    print("  期待Rは損切り幅で割った値なので、幅の決め方を変えると順位が入れ替わることがある。")
    print("  現行との差(Δ)が5本すべてでプラスなら本物。\n")

    base = next(st for lab, st, _, _ in rows if lab == "現行")
    names = list(bk.defs.keys())
    hd = f"  {'ルール':<26s}" + "".join(f"{n:>16s}" for n in names)
    print(hd)
    print("  " + "-" * (len(hd) - 2))
    for label, st, _, _ in rows:
        if st["n"] == 0:
            continue
        cells = ""
        for n in names:
            d = st[f"R:{n}"] - base[f"R:{n}"]
            cells += f"{st[f'R:{n}']:>8.3f}({d:>+.3f})".rjust(16)
        print(f"  {label:<26s}{cells}")


def section_regime(bk: Book, rows) -> None:
    print("\n" + "=" * 100)
    print("【4】局面別 ── 現行に何勝何敗か")
    print("=" * 100)
    print("  11局面のうち、現行に勝った数。5勝6敗ならコイン投げなので採らない。\n")

    labels = [lab for lab, st, _, _ in rows if st["n"] > 0]
    sels = {lab: sel for lab, st, sel, _ in rows if st["n"] > 0}
    d = bk.B["date"]
    Rp = bk.R[bk.prim]

    hd = f"  {'局面':<28s}" + "".join(f"{lab[:13]:>14s}" for lab in labels)
    print(hd)
    print("  " + "-" * (len(hd) - 2))

    wins = {lab: 0 for lab in labels}
    plays = {lab: 0 for lab in labels}
    for name, s, u in dd.REGIMES:
        inr = ((d >= s) & (d <= u)).to_numpy()
        vals = {}
        for lab in labels:
            m = sels[lab] & inr
            v = Rp[m]
            v = v[np.isfinite(v)]
            vals[lab] = float(np.mean(v)) if v.size >= 30 else np.nan
        cells = ""
        for lab in labels:
            v = vals[lab]
            cells += ("        --    " if not np.isfinite(v) else f"{v:>14.3f}")
            if lab not in ("現行",) and np.isfinite(v) and np.isfinite(vals.get("現行", np.nan)):
                plays[lab] += 1
                if v > vals["現行"]:
                    wins[lab] += 1
        print(f"  {name:<28s}{cells}")

    print()
    for lab in labels:
        if lab == "現行" or plays[lab] == 0:
            continue
        print(f"  {lab:<26s} 現行に {wins[lab]}勝{plays[lab]-wins[lab]}敗")
    print("\n  (件数30未満の局面は '--' にして勝敗から外している)")


def section_notes() -> None:
    print("\n" + "=" * 100)
    print("【5】読むときの注意")
    print("=" * 100)
    print("""
  1. 上場廃止になった銘柄がデータに居ないので、どの行も実際より良く出ている。
     案どうしの差には効かないが、絶対値は信用しないこと。
  2. 150日線の2条件だけは現行側にも案側にも掛かっていない(parquet に無い)。
     両方に等しく掛かっていないので比較は公平だが、本番の抽出とはズレる。
  3. 「200日線が21日で上向き」は、本番では日数を数えるがここでは21日前比で代用。
  4. 手数料もスリッページも入っていない。損切りが増える案ほど実際は不利になる。
  5. A-4 は本来「候補が資金より多いときの順位づけ」なので、
     ここの『近い方半分』はあくまで当たりをつけるための代用。
""")


# ===========================================================================

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--part", default="all",
                    choices=["all", "main", "era", "stop", "regime"])
    a = ap.parse_args()

    df = dd.load_setups(a.since, a.until)
    bk = Book(df)
    print(f"\n全セットアップ n={len(df):,}  ブレイク到達 n={len(bk.B):,} "
          f"({len(bk.B)/max(len(df),1):.1%})  "
          f"期間 {df['date'].min():%Y-%m-%d} 〜 {df['date'].max():%Y-%m-%d}")

    rows = build_rows(bk)
    if a.part in ("all", "main"):
        section_main(bk, rows)
    if a.part in ("all", "era"):
        section_era(bk, rows)
    if a.part in ("all", "stop"):
        section_stopdefs(bk, rows)
    if a.part in ("all", "regime"):
        section_regime(bk, rows)
    if a.part == "all":
        section_notes()


if __name__ == "__main__":
    main()
