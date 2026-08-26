"""src/deepdive/cli.py のテスト。watch add/list、fetch、predict/actual/note/ver/score/calendar、
R4(削除系サブコマンド不在)。"""
from __future__ import annotations

import argparse
import json

from src.deepdive import cli, jq_raw, metrics, prep, store
from src.deepdive.cli import build_parser, main


def _fake_a_layer(earnings_date="2099-01-01", topix_relative=1.5, quarter="2026Q2"):
    return {
        "quarter": quarter,
        "next_earnings_date": {"date": earnings_date, "source": "カレンダー"},
        "returns": {"1M": {"abs": 3.0, "topix_relative": topix_relative}},
    }


def test_no_delete_or_rm_subcommand_anywhere():
    """R4: 削除コマンドはどの階層にも存在しない。訂正は新しい ver を切るしかない。"""
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                assert name not in ("delete", "rm")
                for sub_action in subparser._actions:
                    if isinstance(sub_action, argparse._SubParsersAction):
                        assert "delete" not in sub_action.choices
                        assert "rm" not in sub_action.choices


def test_watch_add_requires_drivers_and_break():
    parser = build_parser()
    args = parser.parse_args([
        "watch", "add", "7134",
        "--name", "アップガレージグループ",
        "--fy-end", "3",
        "--drivers", "",
        "--break", "既存店2ヶ月連続マイナス",
    ])
    rc = args.func(args)
    assert rc == 1  # store.add_watch の ValueError を拾って終了コード1


def test_watch_add_then_list(capsys):
    rc = main([
        "watch", "add", "7134",
        "--name", "アップガレージグループ",
        "--fy-end", "3",
        "--drivers", "既存店売上 = 客数 × 客単価",
        "--break", "既存店が2ヶ月連続マイナス",
    ])
    assert rc == 0
    capsys.readouterr()

    rc = main(["watch", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "7134" in out
    assert "アップガレージグループ" in out


def test_watch_list_empty(capsys):
    rc = main(["watch", "list"])
    assert rc == 0
    assert "登録銘柄なし" in capsys.readouterr().out


def test_fetch_requires_code_or_all(capsys):
    rc = main(["fetch"])
    assert rc == 1
    assert "code か --all" in capsys.readouterr().err


def test_fetch_single_code_calls_jq_raw(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(jq_raw, "fetch_and_store", lambda code, **kw: calls.append(code) or 3)
    rc = main(["fetch", "7134"])
    assert rc == 0
    assert calls == ["7134"]
    assert "新規 3 件" in capsys.readouterr().out


def test_fetch_all_uses_active_watchlist(monkeypatch, capsys):
    store.add_watch({
        "ticker": "7134",
        "name": "アップガレージグループ",
        "drivers": "既存店売上",
        "break_conditions": "2ヶ月連続マイナス",
    })
    store.add_watch({
        "ticker": "9999",
        "name": "非アクティブ銘柄",
        "drivers": "d",
        "break_conditions": "b",
        "status": "inactive",
    })
    calls = []
    monkeypatch.setattr(jq_raw, "fetch_and_store", lambda code, **kw: calls.append(code) or 0)
    rc = main(["fetch", "--all"])
    assert rc == 0
    assert calls == ["7134"]


def test_fetch_all_without_watchlist_fails(capsys):
    rc = main(["fetch", "--all"])
    assert rc == 1
    assert "登録銘柄なし" in capsys.readouterr().err


def test_watch_list_skips_inactive(capsys):
    store.add_watch({
        "ticker": "7134",
        "name": "アップガレージグループ",
        "drivers": "既存店売上",
        "break_conditions": "2ヶ月連続マイナス",
        "status": "inactive",
    })
    rc = main(["watch", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "7134" not in out


# ---------------------------------------------------------------------------
# predict
# ---------------------------------------------------------------------------

def test_predict_from_flags_autofills_earnings_date_and_priced_in(monkeypatch, capsys):
    monkeypatch.setattr(prep, "build_a_layer", lambda code, quarter=None, config=None: _fake_a_layer())
    rc = main([
        "predict", "7134", "--quarter", "2026Q2", "--ver", "v1",
        "--company-op", "100", "--my-op", "120",
        "--confidence", "中", "--action", "買う", "--rationale", "既存店好調",
    ])
    assert rc == 0
    assert "予想を記録しました" in capsys.readouterr().out

    saved = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert len(saved) == 1
    rec = saved[0]
    assert rec["earnings_date"] == "2099-01-01"  # 手で書かせず A レイヤから自動補完(§3.2)
    assert rec["priced_in_1m_vs_topix"] == 1.5
    assert rec["valid"] is True


def test_predict_from_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(prep, "build_a_layer", lambda code, quarter=None, config=None: _fake_a_layer())
    payload = {
        "model_ver": "v1",
        "company_op": 100,
        "my_op": 90,
        "confidence": "低",
        "action": "保有継続",
        "rationale": "様子見",
    }
    path = tmp_path / "pred.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    rc = main(["predict", "7134", "--quarter", "2026Q2", "--from-file", str(path)])
    assert rc == 0
    saved = store.load_first_wins(store.PREDICTIONS_PATH, ("ticker", "quarter", "model_ver"))
    assert saved[0]["ticker"] == "7134"
    assert saved[0]["my_op"] == 90


def test_predict_from_file_missing_returns_1(capsys):
    rc = main(["predict", "7134", "--quarter", "2026Q2", "--from-file", "/no/such/file.json"])
    assert rc == 1
    assert "エラー" in capsys.readouterr().err


def test_predict_missing_data_returns_2(monkeypatch, capsys):
    def _raise(code, quarter=None, config=None):
        raise prep.MissingDataError("株価データがありません")

    monkeypatch.setattr(prep, "build_a_layer", _raise)
    rc = main([
        "predict", "7134", "--quarter", "2026Q2", "--ver", "v1",
        "--company-op", "100", "--my-op", "120",
        "--confidence", "中", "--action", "買う", "--rationale", "根拠",
    ])
    assert rc == 2
    assert "株価データがありません" in capsys.readouterr().err


def test_predict_duplicate_key_rejected(monkeypatch, capsys):
    monkeypatch.setattr(prep, "build_a_layer", lambda code, quarter=None, config=None: _fake_a_layer())
    args = [
        "predict", "7134", "--quarter", "2026Q2", "--ver", "v1",
        "--company-op", "100", "--my-op", "120",
        "--confidence", "中", "--action", "買う", "--rationale", "根拠",
    ]
    assert main(args) == 0
    capsys.readouterr()
    rc = main(args)  # R2: 同じ (ticker, quarter, model_ver) は再登録できない
    assert rc == 1
    assert "既に記録済み" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# actual
# ---------------------------------------------------------------------------

def test_actual_records_and_triggers_outcome(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(prep, "build_a_layer", lambda code, quarter=None, config=None: _fake_a_layer())
    main([
        "predict", "7134", "--quarter", "2026Q2", "--ver", "v1",
        "--company-op", "100", "--my-op", "120",
        "--confidence", "中", "--action", "買う", "--rationale", "根拠",
    ])
    capsys.readouterr()

    monkeypatch.setattr(prep, "load_long_prices", lambda code: None)  # 株価無しでも判定は進む
    payload = {"op": 110.0, "disclosed_at": "2026-08-01", "timing": "引け後"}
    path = tmp_path / "actual.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    rc = main(["actual", "7134", "--quarter", "2026Q2", "--from-file", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "的中判定 1件" in out

    outcomes = store.load_last_wins(store.OUTCOMES_PATH, ("ticker", "quarter", "model_ver"))
    assert len(outcomes) == 1
    assert outcomes[0]["dir_hit"] is True  # 会社100→自分120(上振れ)、実績110(会社比+)


def test_actual_from_file_missing_returns_1(capsys):
    rc = main(["actual", "7134", "--quarter", "2026Q2", "--from-file", "/no/such/actual.json"])
    assert rc == 1
    assert "エラー" in capsys.readouterr().err


def test_actual_invalid_timing_rejected(tmp_path, capsys):
    payload = {"op": 110.0, "disclosed_at": "2026-08-01", "timing": "不明なタイミング"}
    path = tmp_path / "actual.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    rc = main(["actual", "7134", "--quarter", "2026Q2", "--from-file", str(path)])
    assert rc == 1
    assert "エラー" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------

def test_note_add(capsys):
    rc = main(["note", "7134", "--quarter", "2026Q2", "--text", "決算説明会メモ"])
    assert rc == 0
    assert "メモを記録しました" in capsys.readouterr().out
    notes = list(store.load_first_wins(store.NOTES_PATH, ("ticker", "quarter", "text")))
    assert notes[0]["text"] == "決算説明会メモ"


def test_note_empty_text_rejected(capsys):
    rc = main(["note", "7134", "--quarter", "2026Q2", "--text", ""])
    assert rc == 1
    assert "エラー" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# ver add
# ---------------------------------------------------------------------------

def test_ver_add_then_duplicate_rejected(capsys):
    rc = main(["ver", "add", "--ver", "v1", "--change", "PER重視に変更", "--reason", "地合い転換"])
    assert rc == 0
    assert "ver=v1 を記録しました" in capsys.readouterr().out

    rc = main(["ver", "add", "--ver", "v1", "--change", "再変更", "--reason", "理由"])
    assert rc == 1
    assert "既に存在します" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------

def test_score_no_data(capsys):
    rc = main(["score"])
    assert rc == 0
    assert "判定済みの実績なし" in capsys.readouterr().out


def test_score_never_mentions_confidence_language(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(prep, "build_a_layer", lambda code, quarter=None, config=None: _fake_a_layer())
    monkeypatch.setattr(prep, "load_long_prices", lambda code: None)
    main([
        "predict", "7134", "--quarter", "2026Q2", "--ver", "v1",
        "--company-op", "100", "--my-op", "120",
        "--confidence", "中", "--action", "買う", "--rationale", "根拠",
    ])
    capsys.readouterr()
    payload = {"op": 110.0, "disclosed_at": "2026-08-01", "timing": "引け後"}
    path = tmp_path / "actual.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    main(["actual", "7134", "--quarter", "2026Q2", "--from-file", str(path)])
    capsys.readouterr()

    rc = main(["score"])
    assert rc == 0
    out = capsys.readouterr().out
    for banned in ("信頼区間", "有意", "統計的"):
        assert banned not in out  # §12: nは永遠に足りない


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------

def test_calendar_empty(capsys):
    rc = main(["calendar"])
    assert rc == 0
    assert "登録銘柄なし" in capsys.readouterr().out


def test_calendar_with_watchlist(monkeypatch, capsys):
    store.add_watch({
        "ticker": "7134",
        "name": "アップガレージグループ",
        "drivers": "既存店売上",
        "break_conditions": "2ヶ月連続マイナス",
    })
    monkeypatch.setattr(cli, "load_earnings_calendar", lambda: {"by_code": {"7134": "2026-11-13"}})
    monkeypatch.setattr(prep, "load_raw_records", lambda code: [])
    monkeypatch.setattr(
        metrics, "next_earnings_date",
        lambda code, calendar, raw_records, manual: (calendar.get(code), "カレンダー"),
    )
    rc = main(["calendar"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "7134" in out
    assert "2026-11-13" in out
    assert "カレンダー" in out
