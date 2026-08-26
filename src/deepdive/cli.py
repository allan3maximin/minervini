"""深掘り銘柄分析ツール CLI。

Usage:
    python -m src.deepdive watch add <code> --name ... --fy-end 3 \\
        --drivers ... --break ...
    python -m src.deepdive watch list
    python -m src.deepdive fetch <code>
    python -m src.deepdive fetch --all
    python -m src.deepdive prep <code> [--quarter 2026Q2]
    python -m src.deepdive predict <code> --quarter 2026Q2 --ver v1 \\
        --company-op N --my-op N --confidence 中 --action 買う --rationale "..."
    python -m src.deepdive predict <code> --quarter 2026Q2 --from-file pred.json
    python -m src.deepdive actual <code> --quarter 2026Q2 --from-file actual.json
    python -m src.deepdive note <code> --quarter 2026Q2 --text "..."
    python -m src.deepdive ver add --ver v2 --change "..." --reason "..."
    python -m src.deepdive score [--by ver|ticker]
    python -m src.deepdive calendar

`watch add` / `watch list` / `fetch` / `prep` / `predict` / `actual` / `note` /
`ver add` / `score` / `calendar` を実装済み(DESIGN_DEEPDIVE.md §10 手順1〜9)。

`predict` の `earnings_date` / `priced_in_1m_vs_topix` は手で書かせず、必ず
`prep.build_a_layer` の A レイヤから自動で埋める(§3.2: 「織り込み」は後から
都合よく書き換えられる余地を残してはいけない値)。

R4(削除コマンドを提供しない)を満たすため、`delete` / `rm` に類するサブコマンドは
どの階層にも作らないこと。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from src.data.jquants import load_earnings_calendar
from src.deepdive import jq_raw, metrics, outcome, prep, sheet, store
from src.utils_io import atomic_write_text, safe_load_json


def _cmd_watch_add(args: argparse.Namespace) -> int:
    rec = {
        "ticker": args.code,
        "name": args.name,
        "fy_end_month": args.fy_end,
        "drivers": args.drivers,
        "break_conditions": args.break_conditions,
        "next_earnings_date_manual": args.next_earnings_date_manual,
    }
    try:
        store.add_watch(rec)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print(f"登録しました: {args.code} {args.name}")
    return 0


def _cmd_watch_list(args: argparse.Namespace) -> int:
    rows = store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
    rows = [r for r in rows if r.get("status", "active") == "active"]
    if not rows:
        print("登録銘柄なし")
        return 0
    for r in sorted(rows, key=lambda r: str(r.get("ticker", ""))):
        print(f"{r.get('ticker')}  {r.get('name', '')}  (fy_end={r.get('fy_end_month')})")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    if args.all:
        rows = store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
        codes = sorted({r["ticker"] for r in rows if r.get("status", "active") == "active"})
        if not codes:
            print("登録銘柄なし(先に watch add で登録すること)", file=sys.stderr)
            return 1
    elif args.code:
        codes = [args.code]
    else:
        print("エラー: code か --all のどちらかを指定すること", file=sys.stderr)
        return 1

    rc = 0
    for code in codes:
        try:
            n = jq_raw.fetch_and_store(code)
        except ValueError as e:
            print(f"エラー: {code}: {e}", file=sys.stderr)
            rc = 1
            continue
        except requests.RequestException as e:
            print(f"エラー: {code}: 取得失敗 ({e})", file=sys.stderr)
            rc = 1
            continue
        print(f"{code}: 新規 {n} 件を {jq_raw.raw_path(code)} に追記")
    return rc


def _cmd_prep(args: argparse.Namespace) -> int:
    try:
        a_layer = prep.build_a_layer(args.code, quarter=args.quarter)
    except prep.MissingDataError as e:
        print(str(e), file=sys.stderr)
        return 2

    text = sheet.render(a_layer)
    out_path = prep.prep_path(args.code, a_layer["quarter"])
    atomic_write_text(out_path, text)
    print(text)
    print(f"({out_path} に書き込み)", file=sys.stderr)
    return 0


def _cmd_predict(args: argparse.Namespace) -> int:
    if args.from_file:
        rec = safe_load_json(Path(args.from_file), None)
        if rec is None:
            print(f"エラー: {args.from_file} が読めません", file=sys.stderr)
            return 1
        rec = dict(rec)
        rec.setdefault("ticker", args.code)
        rec.setdefault("quarter", args.quarter)
    else:
        rec = {
            "ticker": args.code,
            "quarter": args.quarter,
            "model_ver": args.model_ver,
            "company_op": args.company_op,
            "my_op": args.my_op,
            "confidence": args.confidence,
            "action": args.action,
            "rationale": args.rationale,
        }

    # earnings_date / priced_in_1m_vs_topix は手で書かせない(§3.2)。A レイヤから自動で埋める。
    try:
        a_layer = prep.build_a_layer(rec["ticker"], quarter=rec.get("quarter"))
    except prep.MissingDataError as e:
        print(str(e), file=sys.stderr)
        return 2
    rec["earnings_date"] = a_layer["next_earnings_date"]["date"]
    rec["priced_in_1m_vs_topix"] = a_layer["returns"].get("1M", {}).get("topix_relative")

    try:
        store.add_prediction(rec)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print(f"{rec['ticker']} {rec['quarter']} {rec.get('model_ver')}: 予想を記録しました")
    return 0


def _cmd_actual(args: argparse.Namespace) -> int:
    rec = safe_load_json(Path(args.from_file), None)
    if rec is None:
        print(f"エラー: {args.from_file} が読めません", file=sys.stderr)
        return 1
    rec = dict(rec)
    rec.setdefault("ticker", args.code)
    rec.setdefault("quarter", args.quarter)
    try:
        store.add_actual(rec)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    outcomes = outcome.store_outcomes(args.code, args.quarter)
    print(f"{args.code} {args.quarter}: 実績を記録し、的中判定 {len(outcomes)}件を outcomes へ書きました")
    return 0


def _cmd_note(args: argparse.Namespace) -> int:
    rec = {"ticker": args.code, "quarter": args.quarter, "text": args.text}
    try:
        store.add_note(rec)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print(f"{args.code} {args.quarter}: メモを記録しました")
    return 0


def _cmd_ver_add(args: argparse.Namespace) -> int:
    rec = {"ver": args.ver, "change": args.change, "reason": args.reason}
    try:
        store.add_version(rec)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print(f"ver={args.ver} を記録しました")
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    result = outcome.score(by=args.by)
    print(outcome.format_score(result))
    return 0


def _cmd_calendar(args: argparse.Namespace) -> int:
    rows = store.load_last_wins(store.WATCHLIST_PATH, ("ticker",))
    rows = [r for r in rows if r.get("status", "active") == "active"]
    if not rows:
        print("登録銘柄なし")
        return 0
    calendar = (load_earnings_calendar() or {}).get("by_code", {})
    lines = []
    for r in sorted(rows, key=lambda r: str(r.get("ticker", ""))):
        code = r.get("ticker")
        raw_records = prep.load_raw_records(code)
        date, source = metrics.next_earnings_date(
            code, calendar, raw_records, r.get("next_earnings_date_manual")
        )
        lines.append(f"{code}  {r.get('name', '')}  {date or '不明'}(出典: {source})")
    print("\n".join(lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.deepdive", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    top = parser.add_subparsers(dest="command", required=True)

    watch = top.add_parser("watch", help="ウォッチ銘柄の登録・一覧")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)

    watch_add = watch_sub.add_parser(
        "add", help="銘柄登録(drivers/break-conditions が空だと拒否される)"
    )
    watch_add.add_argument("code", help="証券コード")
    watch_add.add_argument("--name", required=True)
    watch_add.add_argument("--fy-end", type=int, required=True, dest="fy_end",
                            help="決算月(3, 2 など)")
    watch_add.add_argument("--drivers", required=True,
                            help="何がこの株を動かすか(空文字は拒否)")
    watch_add.add_argument("--break", required=True, dest="break_conditions",
                            help="どうなったら前提が崩れたと判断するか(空文字は拒否)")
    watch_add.add_argument("--next-earnings-date-manual", default=None,
                            dest="next_earnings_date_manual",
                            help="発表予定日が自動で取れない銘柄向けの手入力(YYYY-MM-DD)")
    watch_add.set_defaults(func=_cmd_watch_add)

    watch_list = watch_sub.add_parser("list", help="登録銘柄一覧(active のみ)")
    watch_list.set_defaults(func=_cmd_watch_list)

    fetch = top.add_parser("fetch", help="J-Quants から生取得 -> data/deepdive/raw/{code}.jsonl")
    fetch.add_argument("code", nargs="?", default=None, help="証券コード(--all 指定時は省略可)")
    fetch.add_argument("--all", action="store_true", help="watch list の active 銘柄すべてを取得")
    fetch.set_defaults(func=_cmd_fetch)

    prep_cmd = top.add_parser(
        "prep", help="A レイヤ生成 -> prep/{code}_{quarter}.md に書き、標準出力にも出す"
    )
    prep_cmd.add_argument("code", help="証券コード")
    prep_cmd.add_argument("--quarter", default=None,
                           help="例: 2026Q2。省略時は raw データから機械的に推定したラベル")
    prep_cmd.set_defaults(func=_cmd_prep)

    predict_cmd = top.add_parser(
        "predict", help="予想を追記(R1/R2 を強制。earnings_date/priced_in_1m_vs_topix は自動補完)"
    )
    predict_cmd.add_argument("code", help="証券コード")
    predict_cmd.add_argument("--quarter", default=None, help="例: 2026Q2")
    predict_cmd.add_argument("--ver", dest="model_ver", default=None, help="モデルバージョン(ver add で先に作成)")
    predict_cmd.add_argument("--company-op", dest="company_op", type=float, default=None,
                              help="会社予想 営業利益")
    predict_cmd.add_argument("--my-op", dest="my_op", type=float, default=None,
                              help="自分の予想 営業利益")
    predict_cmd.add_argument("--confidence", default=None, help="高|中|低")
    predict_cmd.add_argument("--action", default=None, help="買う|買わん|保有継続")
    predict_cmd.add_argument("--rationale", default=None, help="根拠")
    predict_cmd.add_argument("--from-file", dest="from_file", default=None,
                              help="個別フラグの代わりにJSONファイルから読む")
    predict_cmd.set_defaults(func=_cmd_predict)

    actual_cmd = top.add_parser(
        "actual", help="実績を追記し、全model_verぶんの的中判定を outcomes へ書く"
    )
    actual_cmd.add_argument("code", help="証券コード")
    actual_cmd.add_argument("--quarter", required=True, help="例: 2026Q2")
    actual_cmd.add_argument("--from-file", dest="from_file", required=True,
                             help="実績JSONファイル(§3.4のフィールド)")
    actual_cmd.set_defaults(func=_cmd_actual)

    note_cmd = top.add_parser("note", help="Cレイヤ(定性メモ)を追記")
    note_cmd.add_argument("code", help="証券コード")
    note_cmd.add_argument("--quarter", required=True, help="例: 2026Q2")
    note_cmd.add_argument("--text", required=True, help="メモ本文")
    note_cmd.set_defaults(func=_cmd_note)

    ver = top.add_parser("ver", help="判断ロジックの変更ログ")
    ver_sub = ver.add_subparsers(dest="ver_command", required=True)
    ver_add = ver_sub.add_parser(
        "add", help="新しい ver を追加(既存 ver への再追加は拒否。R3)"
    )
    ver_add.add_argument("--ver", required=True)
    ver_add.add_argument("--change", required=True, help="何を変えたか")
    ver_add.add_argument("--reason", required=True, help="なぜ変えたか")
    ver_add.set_defaults(func=_cmd_ver_add)

    score_cmd = top.add_parser("score", help="成績サマリ(valid:false は除外し、除外件数を表示)")
    score_cmd.add_argument("--by", choices=["ver", "ticker"], default="ver")
    score_cmd.set_defaults(func=_cmd_score)

    calendar_cmd = top.add_parser("calendar", help="登録銘柄の発表予定日一覧 + 出典")
    calendar_cmd.set_defaults(func=_cmd_calendar)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
