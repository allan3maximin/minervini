"""J-Quants 生データ取得層(深掘りツール専用)。

DESIGN_DEEPDIVE.md §1.1 / §7.2 の方針どおり、`src/data/jquants.py :: fetch_summaries` の
取得ロジック(ページネーション・429リトライ)を**コピーして持つ**(import しない)。
本番の日次パイプライン(src/data/jquants.py, src/pipeline.py)からは一切参照されない。

レスポンスは加工せずそのまま `data/deepdive/raw/{code}.jsonl` に追記する。加工は
下流(prep.py / metrics.py)で行う。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from src import history_store
from src.config import REPO_ROOT

API_KEY_ENV = "JQUANTS_API_KEY"
DEFAULT_API_URL = "https://api.jquants.com/v2"

# store.DEEPDIVE_DIR を import せず REPO_ROOT から独自に組み立てる。理由は2つ:
# (1) §1.1/§7.2 の疎結合方針(他モジュールの定数に依存しない)
# (2) tests/conftest.py の isolate_write_paths は各モジュールの定数を直接
#     monkeypatch する設計なので、ここで store.DEEPDIVE_DIR を import すると
#     import 時点の値で固定されてしまい monkeypatch が効かなくなる。
RAW_DIR = REPO_ROOT / "data" / "deepdive" / "raw"


def raw_path(code: str) -> Path:
    return RAW_DIR / f"{code}.jsonl"


def _api_url(config: dict | None) -> str:
    """config.yaml の jquants.api_url を読む。無ければ既定値。

    src/data/jquants.py の _jq_cfg は import しない(§7.2 のコピー方針どおり)。
    """
    if not config:
        return DEFAULT_API_URL
    return (config.get("jquants", {}) or {}).get("api_url", DEFAULT_API_URL)


def fetch_summaries(api_key: str, config: dict | None, *, code: str) -> list[dict]:
    """J-Quants `/fins/summary` を `?code=` で全ページ取得する。

    src/data/jquants.py :: fetch_summaries からコピー(§7.2)。深掘りツールは
    常に単一銘柄・全期間を取るので `day` 引数は持たない。
    """
    api_url = _api_url(config)
    params: dict = {"code": code}

    records: list[dict] = []
    while True:
        resp = requests.get(
            f"{api_url}/fins/summary",
            params=params,
            headers={"x-api-key": api_key},
            timeout=60,
        )
        if resp.status_code == 429:
            time.sleep(30)
            resp = requests.get(
                f"{api_url}/fins/summary",
                params=params,
                headers={"x-api-key": api_key},
                timeout=60,
            )
        resp.raise_for_status()
        body = resp.json()
        records.extend(body.get("data") or [])
        pk = body.get("pagination_key")
        if not pk:
            return records
        params["pagination_key"] = pk


def fetch_and_store(code: str, api_key: str | None = None, config: dict | None = None) -> int:
    """1銘柄ぶん取得し、`raw/{code}.jsonl` に未取得ぶんだけ追記する。

    再実行しても重複が無限に増えないよう、`DiscDate` を鍵に既存行との重複を除いてから
    追記する(このファイル自体は追記専用のまま。既存行の書き換えはしない)。
    戻り値は新規追記した件数。
    """
    if api_key is None:
        api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise ValueError(f"{API_KEY_ENV} が設定されていない")

    records = fetch_summaries(api_key, config, code=code)
    if not records:
        return 0

    path = raw_path(code)
    existing_disc_dates = {r.get("DiscDate") for r in history_store.iter_records(path)}
    new_records = [r for r in records if r.get("DiscDate") not in existing_disc_dates]
    if new_records:
        history_store.append_records(path, new_records)
    return len(new_records)
