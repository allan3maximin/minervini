"""テスト共通フィクスチャ: 実リポジトリのデータ汚染に対する安全網。

各モジュールの**書き込み系パス定数**を autouse フィクスチャで tmp_path へ
差し替える。個々のテストがパッチを忘れても data/ や docs/data/ の実ファイルを
上書き・追記しないようにするのが目的(2026-07-17追加)。

方針:
- 書き込み先(またはテストが実データを読むべきでない state/cache)のみ差し替える。
  読み取り専用の定数(config.yaml、manual/*.csv の DEFAULT_CSV_PATH 等)は
  触らない。実パス読み取りに依存する既存テストを壊さないため。
- 各テストは従来どおり自分で monkeypatch してもよい(このフィクスチャの上から
  上書きされるだけなので共存できる)。
- 注意: dryup_log.py の append_records/load_records/write_records/log_and_resolve
  は `path: Path = DRYUP_LOG_PATH` の**デフォルト引数束縛**なので、ここで
  DRYUP_LOG_PATH 定数を差し替えても既定値には効かない。dryup_log を既定パスで
  呼ぶテストを書く場合は path 引数を明示するか関数ごと差し替えること
  (tests/test_pipeline.py の wired フィクスチャが後者の例)。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_write_paths(tmp_path, monkeypatch):
    import src.backtest as backtest_mod
    import src.pipeline as pipeline_mod
    import src.universe as universe_mod
    from src.data import edinetdb, fundamentals, indices, jquants, prices
    from src.report import build_site, dryup_log, heatmap, positions, stage_log
    from src.screener import entry

    data_dir = tmp_path / "data"
    docs_data_dir = tmp_path / "docs_data"

    # --- data/ 配下 (内部状態・キャッシュ) ---
    monkeypatch.setattr(prices, "PRICE_CACHE_DIR", data_dir / "prices")
    monkeypatch.setattr(indices, "INDICES_CACHE_DIR", data_dir / "indices")
    monkeypatch.setattr(fundamentals, "AUTO_PATH", data_dir / "fundamentals_auto.json")
    # jquants.py は `from src.data.fundamentals import AUTO_PATH` で名前を取り込む
    # ため、jquants 側の束縛も別途差し替えが必要。
    monkeypatch.setattr(jquants, "AUTO_PATH", data_dir / "fundamentals_auto.json")
    monkeypatch.setattr(jquants, "STATE_PATH", data_dir / "jquants_state.json")
    monkeypatch.setattr(jquants, "CALENDAR_PATH", data_dir / "earnings_calendar.json")
    monkeypatch.setattr(edinetdb, "STATE_PATH", data_dir / "edinetdb_state.json")
    monkeypatch.setattr(edinetdb, "STORE_PATH", data_dir / "edinetdb_auto.json")
    monkeypatch.setattr(entry, "STATUS_HISTORY_PATH", data_dir / "status_history.json")
    # 追記専用JSONL (2026-07-27〜) の実体。旧JSONだけ差し替えても本体は素通しなので
    # ここも塞ぐ。実際 stage.jsonl は安全網に無かったせいで test_pipeline が
    # data/history/stage.jsonl を実リポジトリに書いた (2026-07-30)。
    monkeypatch.setattr(entry, "STATUS_HISTORY_JSONL", data_dir / "history" / "status.jsonl")
    monkeypatch.setattr(heatmap, "SECTOR_HISTORY_JSONL", data_dir / "history" / "sector.jsonl")
    monkeypatch.setattr(stage_log, "STAGE_HISTORY_JSONL", data_dir / "history" / "stage.jsonl")
    # デフォルト引数束縛のため既定値には効かない(モジュール先頭docstring参照)。
    # それでも定数経由の将来コードのために差し替えておく。
    monkeypatch.setattr(dryup_log, "DRYUP_LOG_PATH", data_dir / "dryup_log.jsonl")
    monkeypatch.setattr(heatmap, "SECTOR_HISTORY_PATH", data_dir / "sector_history.json")
    monkeypatch.setattr(universe_mod, "UNIVERSE_PATH", data_dir / "universe.json")
    monkeypatch.setattr(universe_mod, "SECTOR_MAP_PATH", data_dir / "sector_map.json")
    monkeypatch.setattr(pipeline_mod, "DEBUG_PATH", data_dir / "trend_template_debug.json")
    monkeypatch.setattr(backtest_mod, "BACKTEST_DIR", data_dir / "backtest")

    # --- docs/data 配下 (Pages配信ファイル) ---
    monkeypatch.setattr(build_site, "DOCS_DATA_DIR", docs_data_dir)
    monkeypatch.setattr(build_site, "REPORT_PATH", docs_data_dir / "report.json")
    monkeypatch.setattr(build_site, "BREADTH_PATH", docs_data_dir / "breadth.json")
    monkeypatch.setattr(build_site, "CHARTS_DIR", docs_data_dir / "charts")
    monkeypatch.setattr(fundamentals, "PUBLIC_JSON_PATH", docs_data_dir / "fundamentals_public.json")
    monkeypatch.setattr(heatmap, "HEATMAP_PATH", docs_data_dir / "heatmap.json")
    monkeypatch.setattr(heatmap, "SECTOR_HISTORY_PUBLIC_PATH", docs_data_dir / "sector_history.json")
    monkeypatch.setattr(positions, "POSITIONS_JSON_PATH", docs_data_dir / "positions.json")
    monkeypatch.setattr(indices, "INDICES_JSON_PATH", docs_data_dir / "indices.json")

    return tmp_path
