"""JPXの銘柄別信用取引週末残高ページ(05.html)に現存する過去分PDFをまとめて
取得し、data/margin_weekly.json の history をbackfillする補修用スクリプト。

通常運用ではsrc.pipelineが毎日 update_margin_store() を呼び最新1週分だけ進める。
このスクリプトは初回導入時など、ページにまだ残っている過去分(実測で直近5週分
程度。JPXがページ上でバックナンバーを保持する範囲でそれより古い週は取得不能)
を一気に取り込みたい場合に使う。それより古い週は
`python -m src.data.margin --backfill-jquants --weeks 26` を使う。

【2026-07-30改定】既に保存済みの週も取り直して、その週に入っていない銘柄だけを
足すようになった(既定)。以前は保存済みの週を丸ごとスキップしていたため、
保存時に銘柄を絞り込んでいた時期に作られた週は永久に穴が残っていた。
保存済みの週をそのままにしたい場合は --no-widen を付ける。

data/margin_weekly.json は docs/data/配下ではなく暗号化もされていない平文JSON
なので、breadth.jsonのbackfillと異なり鍵無しでそのまま実行・コミットできる。

実行: python scripts/backfill_margin_history.py [--no-widen]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.margin import backfill_margin_history


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JPXページに残っている過去週の信用残をまとめて取り込む")
    parser.add_argument(
        "--no-widen", action="store_true",
        help="既に保存済みの週は取り直さない(既定は取り直して足りない銘柄だけ足す)")
    args = parser.parse_args()

    store = backfill_margin_history(widen=not args.no_widen)
    history = store.get("history", [])
    print(f"history entries: {len(history)}")
    print(f"dates: {[h['date'] for h in history]}")
    counts = {h["date"]: len(h.get("by_code") or {}) for h in history}
    print(f"codes per week: {counts}")
    if store.get("warnings"):
        print(f"warnings: {store['warnings']}")


if __name__ == "__main__":
    main()
