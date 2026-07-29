#!/usr/bin/env python3
"""gh-pages の生成物を取ってきて復号し、data/plain/ に平文で置く (ローカル解析用)。

## なぜ要るか

docs/data/*.json は AES-256-GCM 封筒で暗号化されている (src/report/secure_io.py)。
公開リポジトリ + Pages でURL直アクセスから中身を守るための仕組みで、これは
そのまま維持したい。一方で、ローカルで中身を見たい場面 (自分の目視確認、
Claude に分析させる、集計スクリプトを書く) では封筒のままだと何もできない。

そこで「配信物は暗号化のまま、ローカルの作業コピーだけ平文」に分ける。
出力先 data/plain/ は .gitignore 済みなので、平文がコミットされることはない。

## 使い方

    # 鍵を1回だけ置く (中身は GitHub Secret DASHBOARD_DATA_KEY と同じ base64 文字列)
    mkdir -p .secrets && echo '<base64の鍵>' > .secrets/data_key

    python tools/pull_site_data.py              # gh-pages から取得して主要JSONを復号
    python tools/pull_site_data.py --charts     # charts/*.json も込みで復号 (約15MB)
    python tools/pull_site_data.py --source local   # 手元の docs/data/ をそのまま復号

鍵は env DASHBOARD_DATA_KEY が優先。無ければ .secrets/data_key を読む。

## 設計メモ

- gh-pages の取得は .github/actions/restore-site-data と同じ
  `git fetch --depth=1` + `git archive` 方式。作業ツリーの docs/data/ には
  触らない (パイプラインの読み戻し対象を勝手に書き換えないため) 一時ディレクトリに
  展開してから復号する。
- 平文/封筒は自動判別する。鍵を消して平文運用に戻した場合もそのまま動く。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.report.secure_io import decrypt_envelope, is_envelope  # noqa: E402

KEY_ENV = "DASHBOARD_DATA_KEY"
KEY_FILE = REPO_ROOT / ".secrets" / "data_key"
OUT_DIR = REPO_ROOT / "data" / "plain"
PUBLISH_BRANCH = "gh-pages"

# 復号対象。「見て意味があるもの」に絞る。_maezyou (前場スナップショット) と
# .conflict_bak は解析の役に立たないので既定では入れない。
MAIN_FILES = [
    "report.json",
    "breadth.json",
    "indices.json",
    "heatmap.json",
    "positions.json",
    "fundamentals_public.json",
    "sector_history.json",
]


def load_key() -> bytes:
    """env → .secrets/data_key の順に鍵を探す。無ければ手順を出して終了。"""
    import base64

    raw = os.environ.get(KEY_ENV, "").strip()
    src = f"env {KEY_ENV}"
    if not raw and KEY_FILE.exists():
        raw = KEY_FILE.read_text(encoding="utf-8").strip()
        src = str(KEY_FILE.relative_to(REPO_ROOT))
    if not raw:
        raise SystemExit(
            f"復号鍵が見つからん。以下のどちらかで渡してくれ:\n"
            f"  1) mkdir -p {KEY_FILE.parent.relative_to(REPO_ROOT)} && "
            f"echo '<base64の鍵>' > {KEY_FILE.relative_to(REPO_ROOT)}\n"
            f"  2) export {KEY_ENV}='<base64の鍵>'\n"
            f"鍵の値は GitHub Secret {KEY_ENV} / パスキー保管庫の dataKey と同じもの。")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise SystemExit(f"{src} の鍵が base64 として読めん: {e}") from e
    if len(key) != 32:
        raise SystemExit(f"{src} の鍵はデコード後32バイトである必要がある (実際: {len(key)})")
    return key


def fetch_publish_data(dest: Path) -> Path:
    """origin/gh-pages の data/ を dest 配下へ展開し、そのパスを返す。"""
    subprocess.run(
        ["git", "fetch", "--depth=1", "origin", PUBLISH_BRANCH],
        cwd=REPO_ROOT, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    probe = subprocess.run(
        ["git", "cat-file", "-e", "FETCH_HEAD:data"], cwd=REPO_ROOT)
    if probe.returncode != 0:
        raise SystemExit(f"{PUBLISH_BRANCH} に data/ が無い。まだ公開されてないかブランチ名が違う。")
    archive = subprocess.run(
        ["git", "archive", "FETCH_HEAD", "data"],
        cwd=REPO_ROOT, check=True, stdout=subprocess.PIPE).stdout
    tar_path = dest / "data.tar"
    tar_path.write_bytes(archive)
    subprocess.run(["tar", "-xf", str(tar_path), "-C", str(dest)], check=True)
    tar_path.unlink()
    return dest / "data"


def convert(src_path: Path, out_path: Path, key: bytes) -> tuple[str, int]:
    """1ファイル復号して書き出す。(状態, 出力バイト数) を返す。"""
    with open(src_path, encoding="utf-8") as f:
        obj = json.load(f)
    state = "plain"
    if is_envelope(obj):
        obj = decrypt_envelope(obj, key)
        state = "decrypted"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    out_path.write_text(text, encoding="utf-8")
    return state, len(text.encode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["gh-pages", "local"], default="gh-pages",
                    help="gh-pages: 公開ブランチから取得 (既定) / local: 手元の docs/data/")
    ap.add_argument("--charts", action="store_true",
                    help="charts/*.json (銘柄別チャート、約15MB) も復号する")
    ap.add_argument("--all", action="store_true",
                    help="MAIN_FILES に限らず直下の *.json を全部復号する (_maezyou 等も含む)")
    args = ap.parse_args()

    key = load_key()

    with tempfile.TemporaryDirectory() as tmp:
        if args.source == "gh-pages":
            src_dir = fetch_publish_data(Path(tmp))
            origin = f"origin/{PUBLISH_BRANCH}:data"
        else:
            src_dir = REPO_ROOT / "docs" / "data"
            origin = str(src_dir.relative_to(REPO_ROOT))
        if not src_dir.is_dir():
            raise SystemExit(f"{src_dir} が無い")

        if args.all:
            targets = sorted(p.name for p in src_dir.glob("*.json"))
        else:
            targets = [n for n in MAIN_FILES if (src_dir / n).exists()]

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        total = 0
        print(f"source: {origin}\noutput: {OUT_DIR.relative_to(REPO_ROOT)}\n")
        for name in targets:
            state, size = convert(src_dir / name, OUT_DIR / name, key)
            total += size
            print(f"  {name:<28} {state:>9}  {size / 1024:8.1f} KB")

        if args.charts:
            charts = sorted((src_dir / "charts").glob("*.json"))
            n_ok = 0
            for p in charts:
                _, size = convert(p, OUT_DIR / "charts" / p.name, key)
                total += size
                n_ok += 1
            print(f"  charts/*.json                {'':>9}  {n_ok} files")

        print(f"\n合計 {total / 1024 / 1024:.1f} MB")

    generated = None
    report = OUT_DIR / "report.json"
    if report.exists():
        with open(report, encoding="utf-8") as f:
            generated = json.load(f).get("generated_at")
    if generated:
        print(f"report.json generated_at: {generated}")


if __name__ == "__main__":
    main()
