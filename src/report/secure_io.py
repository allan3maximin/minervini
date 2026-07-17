"""docs/data 配下のJSON書き出しの暗号化層 (パスキーによるアクセス制御の実体)。

公開リポジトリ + GitHub Pages では「画面のロック」だけかけてもJSONのURL直接
アクセスで中身が見えてしまう。そこで docs/data/*.json を書き出す時点で
AES-256-GCM で暗号化し、平文はどこにもコミットしない。

- 鍵: 環境変数 DASHBOARD_DATA_KEY (base64エンコードの32バイト)。GitHub Actions
  では Secrets から渡す。**鍵が無ければ従来どおり平文で書く**(ローカル開発・
  テストを壊さないため。フロントは平文/暗号文の両方を自動判別する)。
- 封筒形式: {"__enc__": "aesgcm-v1", "iv": <b64>, "ct": <b64>}
  (ct はGCMタグ込み。ivは12バイト乱数)。
- 復号側: docs/assets/secure-fetch.js (WebCrypto) と、リカバリ用の
  `python -m src.report.secure_io --decrypt <file>`。
- 鍵の配送: フロントはWebAuthn PRFパスキー保管庫 (docs/auth/vault.json、
  webauthn-vault.js) に暗号化保管された dataKey を解錠時に取り出して使う。
  つまり「パスキー → データ鍵 → データ復号」の2段構え。
"""
from __future__ import annotations

import argparse
import base64
import json
import os

DATA_KEY_ENV = "DASHBOARD_DATA_KEY"
ENVELOPE_MARKER = "__enc__"
ENVELOPE_ALGO = "aesgcm-v1"


def load_data_key() -> bytes | None:
    """環境変数からデータ鍵を読む。未設定なら None (=平文モード)。"""
    raw = os.environ.get(DATA_KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise ValueError(f"{DATA_KEY_ENV} はbase64である必要があります: {e}") from e
    if len(key) != 32:
        raise ValueError(f"{DATA_KEY_ENV} はデコード後32バイトである必要があります (実際: {len(key)})")
    return key


def encrypt_envelope(obj, key: bytes) -> dict:
    """JSON化可能なobjをAES-256-GCMで暗号化した封筒dictにする。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    plain = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plain, None)
    return {
        ENVELOPE_MARKER: ENVELOPE_ALGO,
        "iv": base64.b64encode(iv).decode(),
        "ct": base64.b64encode(ct).decode(),
    }


def is_envelope(obj) -> bool:
    return isinstance(obj, dict) and obj.get(ENVELOPE_MARKER) == ENVELOPE_ALGO


def decrypt_envelope(envelope: dict, key: bytes):
    """封筒dictを復号して元のオブジェクトを返す (リカバリ・テスト用)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not is_envelope(envelope):
        raise ValueError("not an aesgcm-v1 envelope")
    iv = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ct"])
    plain = AESGCM(key).decrypt(iv, ct, None)
    return json.loads(plain.decode("utf-8"))


def write_docs_json(path, obj, indent: int = 2) -> None:
    """docs/ 配下(Pagesで配信される)向けJSON書き出しの共通入口。

    DASHBOARD_DATA_KEY があれば暗号化封筒を、無ければ平文を書く。
    既存の `json.dump(obj, f, ensure_ascii=False, indent=N)` の置き換え。
    2026-07-17: tmp書き込み→os.replace のアトミック置換に変更(途中クラッシュで
    壊れたJSONがコミットされるのを防ぐ。暗号化ロジックは不変)。
    """
    key = load_data_key()
    payload = encrypt_envelope(obj, key) if key else obj
    from src.utils_io import atomic_write_json
    atomic_write_json(path, payload, indent=indent)


def read_docs_json(path, default=None):
    """docs/ 配下のJSONを読む(封筒なら復号)。パイプラインの読み戻し用。

    暗号化ファイルなのに鍵が無い場合は、黙って既定値を返すと履歴リセット等の
    静かなデータ破壊になるため、明示的にエラーにする。
    """
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    if not is_envelope(obj):
        return obj
    key = load_data_key()
    if key is None:
        raise RuntimeError(
            f"{path} は暗号化されていますが env {DATA_KEY_ENV} が未設定です。"
            "鍵を設定するか、平文の状態で実行してください。")
    return decrypt_envelope(obj, key)


def main() -> None:
    parser = argparse.ArgumentParser(description="docs/data JSONの暗号化封筒ツール")
    parser.add_argument("--decrypt", metavar="FILE", help="封筒JSONを復号して標準出力へ(鍵は環境変数)")
    parser.add_argument("--encrypt", metavar="FILE", help="平文JSONを封筒に変換して上書き(鍵は環境変数)")
    args = parser.parse_args()

    key = load_data_key()
    if key is None:
        raise SystemExit(f"env {DATA_KEY_ENV} が必要です")

    if args.decrypt:
        with open(args.decrypt, encoding="utf-8") as f:
            envelope = json.load(f)
        print(json.dumps(decrypt_envelope(envelope, key), ensure_ascii=False, indent=2))
    elif args.encrypt:
        from pathlib import Path
        p = Path(args.encrypt)
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        if is_envelope(obj):
            raise SystemExit("既に封筒形式です")
        write_docs_json(p, obj)
        print(f"encrypted: {p}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
