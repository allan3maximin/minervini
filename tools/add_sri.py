#!/usr/bin/env python3
"""docs/index.html の CDN 参照に SRI (integrity) 属性を付与/更新する。

index.html 内の unpkg / jsDelivr への <link rel="stylesheet"> と <script src>
を走査し、各リソースを実際にダウンロードして sha384 ハッシュを計算、
`integrity="sha384-..."` と `crossorigin="anonymous"` を挿入(既存なら更新)する。

★ このスクリプトは **ユーザーがローカルで実行する** こと:

    python tools/add_sri.py

(Claude の作業サンドボックスは外部 CDN へ接続できないため、サンドボックス内
では実行しない。また、integrity ハッシュを手書き/推測で index.html に直接
書くのは絶対に禁止 — 1文字でも違うとブラウザがスクリプト/CSS のロード自体を
拒否し、ページ全体が動かなくなる。必ず本スクリプトで実測値を埋め込むこと。)

CDN の参照バージョンを上げた時も、このスクリプトを再実行すれば integrity が
新しいファイルのハッシュに更新される。

冪等: すでに正しいハッシュが付いていれば index.html は書き換わらない。
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "docs" / "index.html"

# SRI を付ける対象の CDN ホスト
CDN_HOSTS = ("unpkg.com", "cdn.jsdelivr.net")

# <script src="https://..."></script> / <link ... href="https://...">
TAG_RE = re.compile(
    r'<(?P<tag>script|link)\b(?P<attrs>[^>]*?)(?P<self>/?)>',
    re.IGNORECASE,
)
URL_ATTR_RE = re.compile(r'\b(?:src|href)="(?P<url>https://[^"]+)"')
INTEGRITY_RE = re.compile(r'\s*\bintegrity="[^"]*"')
CROSSORIGIN_RE = re.compile(r'\s*\bcrossorigin="[^"]*"')


def sha384_integrity(url: str) -> str:
    print(f"  downloading {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "add-sri-script"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    digest = hashlib.sha384(body).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii")


def main() -> int:
    html = INDEX_HTML.read_text(encoding="utf-8")
    updated = 0

    def process(m: re.Match) -> str:
        nonlocal updated
        attrs = m.group("attrs")
        url_m = URL_ATTR_RE.search(attrs)
        if not url_m:
            return m.group(0)
        url = url_m.group("url")
        if not any(host in url for host in CDN_HOSTS):
            return m.group(0)

        try:
            integrity = sha384_integrity(url)
        except Exception as e:  # noqa: BLE001
            print(f"[error] ダウンロード失敗のためスキップ: {url} ({e})", file=sys.stderr)
            return m.group(0)

        # 既存の integrity / crossorigin を除去してから付け直す
        new_attrs = INTEGRITY_RE.sub("", attrs)
        new_attrs = CROSSORIGIN_RE.sub("", new_attrs)
        new_attrs = new_attrs.rstrip()
        new_attrs += f' integrity="{integrity}" crossorigin="anonymous"'

        new_tag = f'<{m.group("tag")}{new_attrs}{m.group("self")}>'
        if new_tag != m.group(0):
            updated += 1
            print(f"  -> integrity 更新: {url}")
        return new_tag

    new_html = TAG_RE.sub(process, html)

    if new_html != html:
        INDEX_HTML.write_text(new_html, encoding="utf-8")
        print(f"{INDEX_HTML.relative_to(REPO_ROOT)} を更新しました ({updated}件)")
    else:
        print("変更なし (全CDN参照のintegrityが最新)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
