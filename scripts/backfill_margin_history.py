"""JPXの銘柄別信用取引週末残高ページ(05.html)に現存する過去分PDFをまとめて
取得し、data/margin_weekly.json の history をbackfillする一回限りのスクリプト。

通常運用ではsrc.pipelineが毎日 update_margin_store() を呼び最新1週分だけ進める。
このスクリプトは初回導入時など、ページにまだ残っている過去分(実測で直近5週分
程度。JPXがページ上でバックナンバーを保持する範囲でそれより古い週は取得不能)
を一気に取り込みたい場合に使う。

data/margin_weekly.json は docs/data/配下ではなく暗号化もされていない平文JSON
なので、breadth.jsonのbackfillと異なり鍵無しでそのまま実行・コミットできる。

実行: python scripts/backfill_margin_history.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.margin import backfill_margin_history


def main() -> None:
    store = backfill_margin_history()
    history = store.get("history", [])
    print(f"history entries: {len(history)}")
    print(f"dates: {[h['date'] for h in history]}")
    if store.get("warnings"):
        print(f"warnings: {store['warnings']}")


if __name__ == "__main__":
    main()
