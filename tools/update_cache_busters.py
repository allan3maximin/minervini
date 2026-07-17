#!/usr/bin/env python3
"""docs/index.html のローカルアセット参照 (?v=...) を内容ハッシュに更新する。

GitHub Pages はビルド工程が無いため、CSS/JS を変更してもブラウザ/CDN の
キャッシュが古いまま残ることがある。このスクリプトは index.html 内の
`assets/*.css?v=...` / `assets/*.js?v=...` 参照を走査し、参照先ファイルの
内容 md5 の先頭8文字にクエリを書き換える。ファイルを変更したら忘れずに
これを実行してからコミットする運用。

使い方:
    python tools/update_cache_busters.py

冪等: 内容が変わっていなければ index.html は書き換わらない。
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
INDEX_HTML = DOCS_DIR / "index.html"

# assets/xxx.css?v=yyy / assets/xxx.js?v=yyy (ローカル参照のみ。CDNのhttp(s)://は対象外)
ASSET_RE = re.compile(r'(?P<path>assets/[\w.\-]+\.(?:css|js))\?v=(?P<ver>[\w.\-]+)')


def content_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def main() -> int:
    html = INDEX_HTML.read_text(encoding="utf-8")
    changed: list[tuple[str, str, str]] = []
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        rel = m.group("path")
        old_ver = m.group("ver")
        asset = DOCS_DIR / rel
        if not asset.is_file():
            missing.append(rel)
            return m.group(0)
        new_ver = content_hash(asset)
        if new_ver != old_ver:
            changed.append((rel, old_ver, new_ver))
        return f"{rel}?v={new_ver}"

    new_html = ASSET_RE.sub(repl, html)

    for rel in missing:
        print(f"[warn] 参照先ファイルが見つかりません: docs/{rel}", file=sys.stderr)

    if new_html != html:
        INDEX_HTML.write_text(new_html, encoding="utf-8")
        for rel, old, new in changed:
            print(f"updated: {rel}  ?v={old} -> ?v={new}")
        print(f"{INDEX_HTML.relative_to(REPO_ROOT)} を更新しました ({len(changed)}件)")
    else:
        print("変更なし (全バスターが最新)")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
