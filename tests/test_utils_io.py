"""utils_io (atomic_write_json / atomic_write_text / safe_load_json) のテスト。"""
import json

from src.utils_io import atomic_write_json, atomic_write_text, safe_load_json


def test_atomic_write_json_writes_complete_json_without_tmp_residue(tmp_path):
    path = tmp_path / "sub" / "state.json"  # 親ディレクトリ自動作成も確認
    obj = {"b": 2, "a": 1, "nested": {"list": [1, 2, 3]}, "日本語": "ok"}

    atomic_write_json(path, obj)

    assert json.loads(path.read_text(encoding="utf-8")) == obj
    # tmpファイルが残っていないこと
    assert list(tmp_path.rglob("*.tmp")) == []


def test_atomic_write_json_overwrites_existing_file(tmp_path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"v": 1})
    atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_text_roundtrip(tmp_path):
    path = tmp_path / "log.jsonl"
    text = '{"a": 1}\n{"b": 2}\n'
    atomic_write_text(path, text)
    assert path.read_text(encoding="utf-8") == text
    assert list(tmp_path.glob("*.tmp")) == []


def test_safe_load_json_missing_file_returns_default(tmp_path):
    default = {"fresh": True}
    result = safe_load_json(tmp_path / "nope.json", default)
    assert result is default


def test_safe_load_json_corrupt_file_returns_default(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text('{"truncated": ', encoding="utf-8")  # 書き込み途中クラッシュ想定
    default = {}
    result = safe_load_json(path, default)
    assert result is default
    assert "WARNING" in capsys.readouterr().out


def test_safe_load_json_valid_file_returns_content(tmp_path):
    path = tmp_path / "ok.json"
    path.write_text('{"v": 42}', encoding="utf-8")
    assert safe_load_json(path, {}) == {"v": 42}
