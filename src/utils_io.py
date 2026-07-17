"""内部状態ファイル向けの安全なJSON読み書きユーティリティ。

背景 (2026-07-17): status_history.json / jquants_state.json / edinetdb_state.json
等の内部状態ファイルが `open("w")` 直書きだったため、書き込み途中のクラッシュで
壊れたJSONがコミットされると翌日以降のrunが読み込み例外で落ち続ける問題があった。

- atomic_write_json: 同一ディレクトリの一時ファイルに書き切ってから os.replace で
  差し替える(POSIXでは同一ファイルシステム内の rename はアトミック)。親ディレクトリ
  は自動作成する。
- atomic_write_text: JSONL等の非JSONテキスト向けの同型ヘルパ。
- safe_load_json: 破損JSON(パース不能・読み込み失敗)は例外にせず warning を print
  して default を返す(呼び出し側は「初回実行と同じ空状態」から再構築できる設計の
  ファイルにのみ使うこと。履歴の静かな消失が致命的な docs/data 配下の読み戻しには
  secure_io.read_docs_json を使う)。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path, obj, *, indent: int = 1, sort_keys: bool = False,
                      ensure_ascii: bool = False, default=None) -> None:
    """tmpファイルへ全量書き込み→os.replaceでアトミックに差し替える。

    途中クラッシュしても既存ファイルは無傷のまま残る(残るのは *.tmp のみ)。
    親ディレクトリが無ければ作成する。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=ensure_ascii, indent=indent,
                  sort_keys=sort_keys, default=default)
    os.replace(tmp, path)


def atomic_write_text(path, text: str) -> None:
    """テキスト(JSONL等)版の tmp書き込み→os.replace。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def safe_load_json(path, default):
    """JSONを読む。ファイルが無い・壊れている場合は warning を print して default。

    default は呼び出しごとに新しいオブジェクトを渡すこと(共有ミュータブルの
    使い回し事故防止のため、この関数は default をそのまま返す)。
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        print(f"WARNING: {path} is corrupted or unreadable ({e}); using default.")
        return default
