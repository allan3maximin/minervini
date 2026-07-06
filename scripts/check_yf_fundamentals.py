"""yfinance 四半期ファンダのカバレッジ調査(使い捨てスクリプト)。

ユニバースからランダムに N 銘柄サンプリングし、quarterly_income_stmt から
Basic EPS / 売上が何四半期分取れるか・直近四半期がいつかを集計する。

実行:  python3 scripts/check_yf_fundamentals.py [銘柄数(省略時30)]
"""
from __future__ import annotations

import json
import random
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import yfinance as yf

from src.config import REPO_ROOT

REVENUE_KEYS = ("Total Revenue", "Operating Revenue")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    with open(REPO_ROOT / "data" / "universe.json", encoding="utf-8") as f:
        stocks = json.load(f)["stocks"]
    random.seed(42)
    sample = random.sample(stocks, min(n, len(stocks)))

    rows = []
    for s in sample:
        ticker = s["code"] + ".T"
        try:
            q = yf.Ticker(ticker).quarterly_income_stmt
        except Exception as e:
            rows.append((ticker, s["name"][:10], -1, 0, 0, f"ERR {str(e)[:30]}"))
            time.sleep(0.5)
            continue
        if q is None or q.empty:
            rows.append((ticker, s["name"][:10], 0, 0, 0, "-"))
            time.sleep(0.5)
            continue
        cols = sorted(q.columns)
        n_eps = int(q.loc["Basic EPS"].notna().sum()) if "Basic EPS" in q.index else 0
        n_rev = max(
            (int(q.loc[k].notna().sum()) for k in REVENUE_KEYS if k in q.index),
            default=0,
        )
        latest = str(cols[-1].date())
        rows.append((ticker, s["name"][:10], len(cols), n_eps, n_rev, latest))
        time.sleep(0.5)

    print(f"{'ticker':10} {'name':12} {'Q数':>3} {'EPS':>4} {'売上':>4}  直近Q")
    for r in rows:
        print(f"{r[0]:10} {r[1]:12} {r[2]:>3} {r[3]:>4} {r[4]:>4}  {r[5]}")

    valid = [r for r in rows if r[2] >= 0]
    eps4 = sum(1 for r in valid if r[3] >= 4)
    rev4 = sum(1 for r in valid if r[4] >= 4)
    recent = sum(
        1
        for r in valid
        if r[5] not in ("-",) and not r[5].startswith("ERR")
        and pd.Timestamp(r[5]) >= pd.Timestamp.now() - pd.Timedelta(days=150)
    )
    print(f"\n{len(valid)}銘柄中: EPS 4Q以上={eps4}  売上 4Q以上={rev4}  直近5ヶ月以内のQ有り={recent}")
    print("目安: 8割以上で直近Qが取れるならyfinance採用可、穴だらけならJ-Quants検討。")


if __name__ == "__main__":
    main()
