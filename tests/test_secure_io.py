"""src/report/secure_io.py -- docs/data JSON暗号化層のテスト。"""
import base64
import json
import secrets

import pytest

from src.report import secure_io as sio


@pytest.fixture
def key_env(monkeypatch):
    key = secrets.token_bytes(32)
    monkeypatch.setenv(sio.DATA_KEY_ENV, base64.b64encode(key).decode())
    return key


def test_load_data_key_absent(monkeypatch):
    monkeypatch.delenv(sio.DATA_KEY_ENV, raising=False)
    assert sio.load_data_key() is None


def test_load_data_key_valid(key_env):
    assert sio.load_data_key() == key_env


def test_load_data_key_rejects_bad_values(monkeypatch):
    monkeypatch.setenv(sio.DATA_KEY_ENV, "not-base64!!")
    with pytest.raises(ValueError):
        sio.load_data_key()
    monkeypatch.setenv(sio.DATA_KEY_ENV, base64.b64encode(b"short").decode())
    with pytest.raises(ValueError):
        sio.load_data_key()


def test_encrypt_decrypt_roundtrip(key_env):
    obj = {"stocks": [{"code": "7203", "close": 2972.5, "name": "トヨタ"}], "n": 184}
    env = sio.encrypt_envelope(obj, key_env)
    assert sio.is_envelope(env)
    assert "stocks" not in json.dumps(env)  # 平文が漏れていない
    assert sio.decrypt_envelope(env, key_env) == obj


def test_decrypt_with_wrong_key_fails(key_env):
    env = sio.encrypt_envelope({"a": 1}, key_env)
    with pytest.raises(Exception):
        sio.decrypt_envelope(env, secrets.token_bytes(32))


def test_write_docs_json_plaintext_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv(sio.DATA_KEY_ENV, raising=False)
    p = tmp_path / "out.json"
    sio.write_docs_json(p, {"hello": "世界"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"hello": "世界"}


def test_write_docs_json_envelope_with_key(tmp_path, key_env):
    p = tmp_path / "out.json"
    sio.write_docs_json(p, {"hello": "世界"})
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert sio.is_envelope(on_disk)
    assert sio.decrypt_envelope(on_disk, key_env) == {"hello": "世界"}


def test_read_docs_json_roundtrip_and_default(tmp_path, key_env):
    p = tmp_path / "b.json"
    assert sio.read_docs_json(p, default={"history": []}) == {"history": []}
    sio.write_docs_json(p, {"history": [1, 2]})
    assert sio.read_docs_json(p) == {"history": [1, 2]}


def test_read_docs_json_encrypted_without_key_raises(tmp_path, key_env, monkeypatch):
    p = tmp_path / "b.json"
    sio.write_docs_json(p, {"history": [1]})
    monkeypatch.delenv(sio.DATA_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError):
        sio.read_docs_json(p)


def test_read_docs_json_plaintext_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv(sio.DATA_KEY_ENV, raising=False)
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"history": [1]}), encoding="utf-8")
    assert sio.read_docs_json(p) == {"history": [1]}
