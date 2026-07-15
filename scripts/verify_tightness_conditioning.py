"""Item2【検証のみ】tightnessスコア条件化(案X vs 案Y)の期待R比較(2026-07-15指示)。

**configは変更しない。バックテストでの比較のみ。**

背景: 交絡調査で tightness_10d<=0.05 帯が全滅(期待R0.23)し、tightness単独は無価値〜逆効果。
「タイトさは枯れとの組み合わせでのみ加点」する方が期待Rを改善するかを検証する。

  案X(現行): vcp_score の tightness 配点(最終収縮の浅さ linear_score)をそのまま使う。
  案Y(条件化): tightness 加点を dryup_med_10_50 < mild閾値(0.77)の銘柄にのみ与え、
              それ以外(枯れ不足)は tightness 加点をゼロにする。**足切りではなく加点の条件化**。

比較指標(measured セットアップのみ):
  (1) tightness加点と r_multiple の順位相関(Spearman)を X/Y で比較。
  (2) tightness加点を受ける「加点コホート」の期待R を X/Y で比較 + Yが除外する
      (タイト×枯れ不足)群の期待R(=足を引っ張る群か)。
  (3) 総合スコアを X/Y で再構成し、上位50%の期待R を比較(条件化で上位が良化するか)。

MUST条件(V1〜V7)は不変。案Yはスコア加点の条件化のみ。
出力: data/backtest/verify_tightness_conditioning_YYYYMMDD.md
"""
from __future__ import annotations

import statistics as st
from datetime import datetime

import pandas as pd

from src.backtest import BACKTEST_DIR, run_backtest
from src.config import load_config
from src.screener.scoring import linear_score


def _measured(setups: list[dict]) -> list[dict]:
    return [
        s for s in setups
        if s.get("breakout") and not s.get("extended_skip") and s.get("r_multiple") is not None
    ]


def _mean(vals):
    return round(st.mean(vals), 2) if vals else None


def main() -> None:
    config = load_config()
    vcp_cfg = config["vcp"]
    w_tight = vcp_cfg["score_weights"]["tightness"]
    last_depth_max = vcp_cfg["last_depth_max"]
    last_depth_perfect = vcp_cfg["last_depth_perfect"]
    mild = config["dryup"]["dryup_badge_mild"]  # 0.77

    result = run_backtest(
        days=400, limit=None,
        rs_min=config["trend_template"]["rs_min"],
        vol_mult=config["entry"]["breakout_vol_mult"],
        stop_pct=config["entry"]["stop_loss_pct"],
        config=config,
    )
    setups = _measured(result["setups"])

    # 各setupの tightness加点(X)と条件化後(Y)、総合スコアX/Y、dryup_med を付与
    for s in setups:
        pivot = s.get("pivot")
        low = s.get("stop_ref_low")
        depth_last = (pivot - low) / pivot if (pivot and low is not None) else None
        pts_x = linear_score(depth_last, last_depth_max, last_depth_perfect, w_tight)
        med = (s.get("dryup_setup") or {}).get("dryup_med_10_50")
        is_dry = med is not None and med < mild
        pts_y = pts_x if is_dry else 0.0
        s["_tight_pts_x"] = pts_x
        s["_tight_pts_y"] = pts_y
        s["_med"] = med
        s["_is_dry"] = is_dry
        # 総合スコア: 現行vcp_scoreは案Xのtightnessを含む。案Yは枯れ不足銘柄から加点を差し引く。
        s["_total_x"] = s.get("vcp_score")
        s["_total_y"] = (s.get("vcp_score") - (pts_x - pts_y)) if s.get("vcp_score") is not None else None

    lines: list[str] = []
    ap = lines.append
    ap(f"# tightnessスコア条件化 検証(案X vs 案Y) ({datetime.now():%Y-%m-%d})")
    ap("")
    ap(f"- configは未変更(検証のみ)。mild閾値={mild}、tightness配点={w_tight}、"
       f"last_depth_max={last_depth_max}/perfect={last_depth_perfect}")
    ap(f"- measured n = {len(setups)}")
    ap("- 案Y = tightness加点を dryup_med<{:.2f} の銘柄のみに与える(加点の条件化。足切りではない)".format(mild))
    ap("")

    # (1) 順位相関
    df = pd.DataFrame({
        "r": [s["r_multiple"] for s in setups],
        "x": [s["_tight_pts_x"] for s in setups],
        "y": [s["_tight_pts_y"] for s in setups],
    })
    # Spearman = ランクのPearson(scipy不要)。
    rr = df["r"].rank()
    corr_x = df["x"].rank().corr(rr)
    corr_y = df["y"].rank().corr(rr)
    ap("## (1) tightness加点 × r_multiple 順位相関(Spearman)")
    ap("")
    ap(f"- 案X: {corr_x:+.3f}")
    ap(f"- 案Y: {corr_y:+.3f}")
    ap(f"- 改善(Y−X): {corr_y - corr_x:+.3f} … 正なら条件化で「加点が良い結果をより予測」")
    ap("")

    # (2) 加点コホートの期待R
    credited_x = [s for s in setups if s["_tight_pts_x"] > 0]
    credited_y = [s for s in setups if s["_tight_pts_y"] > 0]
    zeroed_y = [s for s in setups if s["_tight_pts_x"] > 0 and s["_tight_pts_y"] == 0]  # タイトだが枯れ不足
    ap("## (2) tightness加点を受けるコホートの期待R")
    ap("")
    ap("| コホート | n | 期待R |")
    ap("|---|---:|---:|")
    ap(f"| 案X 加点対象(全タイト) | {len(credited_x)} | {_mean([s['r_multiple'] for s in credited_x])} |")
    ap(f"| 案Y 加点対象(タイト×枯れ) | {len(credited_y)} | {_mean([s['r_multiple'] for s in credited_y])} |")
    ap(f"| 案Yが加点ゼロにする群(タイト×枯れ不足) | {len(zeroed_y)} | {_mean([s['r_multiple'] for s in zeroed_y])} |")
    ap("")
    ap("> 加点対象の期待Rが X<Y で、除外群が低いなら「タイトさは枯れとの組合せでのみ有効」を支持。")
    ap("")

    # (3) 総合スコア上位50%の期待R(ランキング効果)
    def top_half_er(setups_list, key):
        ranked = sorted([s for s in setups_list if s.get(key) is not None], key=lambda s: s[key], reverse=True)
        if not ranked:
            return None, 0
        k = max(1, len(ranked) // 2)
        top = ranked[:k]
        return _mean([s["r_multiple"] for s in top]), len(top)
    er_x, n_x = top_half_er(setups, "_total_x")
    er_y, n_y = top_half_er(setups, "_total_y")
    ap("## (3) 総合スコア上位50%の期待R(ランキング効果)")
    ap("")
    ap(f"- 案X 上位50%(n={n_x}): 期待R {er_x}")
    ap(f"- 案Y 上位50%(n={n_y}): 期待R {er_y}")
    if er_x is not None and er_y is not None:
        ap(f"- 改善(Y−X): {er_y - er_x:+.2f} R")
    ap("")

    # 総括
    improved = (corr_y - corr_x > 0)
    coh_x = _mean([s["r_multiple"] for s in credited_x])
    coh_y = _mean([s["r_multiple"] for s in credited_y])
    ap("## 総括(人間の採用判断用)")
    ap("")
    ap(f"- 相関: {'改善' if improved else '不変/悪化'}(Y−X {corr_y - corr_x:+.3f})")
    ap(f"- 加点コホート期待R: 案X {coh_x} → 案Y {coh_y}")
    ap(f"- 上位50%期待R: 案X {er_x} → 案Y {er_y}")
    ap("")
    ap("※ configは未変更。上記で改善が確認できれば、tightness配点の条件化(案Y)を人間が採用判断する。")
    ap("※ n<30水準のため確度は限定的。MUST条件(V1〜V7)は本検証と無関係に不変。")

    md = "\n".join(lines)
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKTEST_DIR / f"verify_tightness_conditioning_{datetime.now():%Y%m%d}.md"
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n保存: {out}")


if __name__ == "__main__":
    main()
