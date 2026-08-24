#!/usr/bin/env python3
"""J-Quants から「値動き以外のデータ」を生のまま落としてくる(2026-08-24)。

**このスクリプトは Claude のサンドボックスからは動かない(外に出られない)。
萩山の手元で動かすこと。** 必要なのは環境変数 JQUANTS_API_KEY だけ。

なぜ要るか
----------
data/fundamentals_auto.json は 2024-05〜2026-05 の2年しか入っていない。
これは保存側で切り詰めているのではなく、**Free プランが「12週間前〜2年12週間前」
までしか返さない**ため(公式仕様 jpx-jquants.com/ja/spec/data-spec)。
つまり今の契約のままでは、判定基準の条件1(前半と後半で向きが同じか)を
値動き以外のデータで当てることは原理的にできない。

有料プランに1ヶ月だけ入れば、財務情報は最大20年前まで(実際の格納開始は
2008-07-07)取れる。取り切ったら解約してよい。落としたファイルは手元に残る。

  プラン      月額      財務情報   投資部門別  信用週末残高  空売り残高
  Free        0円       2年        なし        なし          なし
  Light       1,650円   5年        5年         なし          なし
  Standard    3,300円   10年       10年        10年          10年
  Premium     16,500円  20年       20年        20年          20年

何を落とすか
------------
既存の src/data/jquants.py は「今のスクリーナーに要るもの」だけを残す作りで、
**会社予想を最新の1点しか保存していない**(store の guidance が上書きされる)。
検証で欲しいのは「予想がいつどう書き換わったか」の履歴そのものなので、
ここでは加工を一切せず、API が返した行をそのまま JSONL / CSV で保存する。

使い方
------
  # 1) 今の鍵で何年ぶん取れるかを確かめる(数リクエストだけ。無料で安全)
  JQUANTS_API_KEY=xxx python3 tools/event/fetch_jq.py --probe

  # 2) 実際に落とす(有料プランなら一括CSV、Freeなら銘柄ごとのAPI)
  JQUANTS_API_KEY=xxx python3 tools/event/fetch_jq.py --pull

  # 落とすものを絞る
  JQUANTS_API_KEY=xxx python3 tools/event/fetch_jq.py --pull --only 財務情報

保存先: data/raw/jq/<データ名>/  (git には入れない。.gitignore 済みを確認すること)
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "jq"
API = "https://api.jquants.com/v2"
KEY_ENV = "JQUANTS_API_KEY"
SLEEP = 1.1  # 60リクエスト/分の制限に対する安全マージン

# 落としたいデータ。(日本語名, エンドポイント, 一括CSVがあるか, 何に使うか)
WANT = [
    ("財務情報", "/fins/summary", True,
     "会社が出した予想EPS(FEPS/NxFEPS)の履歴。★本命。"
     "上方修正・下方修正した日をイベントにできる。年中いつでも出るので"
     "決算期に固まらない"),
    ("空売り残高", "/markets/short-sale-report", True,
     "銘柄別に「誰がどれだけ売り建てているか」。買われ方の裏側"),
    ("信用週末残高", "/markets/margin-interest", True,
     "銘柄別の信用買い残・売り残(週次)。今 data/margin_weekly.json は9週しかない"),
    ("投資部門別", "/equities/investor-types", True,
     "外国人・個人・機関がどれだけ買ったか(週次)。ただし市場全体の1本だけで"
     "銘柄別ではないので、銘柄を選ぶ材料にはならない"),
    ("決算発表予定日", "/fins/earnings-date", True,
     "「いつ出るか」が事前に分かっていた日。決算期の暦をちゃんと引ける"),
]


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def api_key() -> str:
    k = os.environ.get(KEY_ENV, "").strip()
    if not k:
        sys.exit(f"環境変数 {KEY_ENV} が空。J-Quants のダッシュボードで発行した鍵を入れる")
    return k


def get(key: str, path: str, params: dict | None = None, *, tries: int = 3):
    """1リクエスト。429(叩きすぎ)なら待って粘る。失敗したら (None, 理由)。"""
    for n in range(tries):
        try:
            r = requests.get(f"{API}{path}", params=params or {},
                             headers={"x-api-key": key}, timeout=60)
        except requests.RequestException as e:
            if n == tries - 1:
                return None, f"通信できず: {e}"
            time.sleep(5)
            continue
        if r.status_code == 429:
            time.sleep(30)
            continue
        if r.status_code in (400, 401, 403):
            # プラン外・期間外はここに来る。中身に理由が書いてある
            try:
                msg = r.json().get("message") or r.text[:200]
            except Exception:
                msg = r.text[:200]
            return None, f"{r.status_code} {msg}"
        if not r.ok:
            if n == tries - 1:
                return None, f"{r.status_code} {r.text[:200]}"
            time.sleep(5)
            continue
        return r.json(), ""
    return None, "リトライ切れ"


def pages(key: str, path: str, params: dict):
    """ページングを全部たどって行を返す。"""
    p = dict(params)
    rows: list[dict] = []
    while True:
        body, err = get(key, path, p)
        if body is None:
            return rows, err
        rows.extend(body.get("data") or [])
        pk = body.get("pagination_key")
        if not pk:
            return rows, ""
        p["pagination_key"] = pk
        time.sleep(SLEEP)


# ---------------------------------------------------------------------------
# --probe: 今の鍵で何年ぶん取れるのか
# ---------------------------------------------------------------------------

def probe() -> None:
    key = api_key()
    print("今の鍵で何が取れるかを確かめる(数リクエストだけ)\n")

    # 一括CSVが使えるか = 有料プランか。Free は取引カレンダー以外CSV不可
    body, err = get(key, "/bulk/list", {"endpoint": "/fins/summary"})
    if body is None:
        print(f"一括CSV: 使えない ({err})")
        print("  → Free プランの可能性が高い。以下はAPI経由で確かめる\n")
        bulk_ok = False
    else:
        files = body.get("data") or []
        names = sorted(f.get("Key", "") for f in files)
        size = sum(int(f.get("Size") or 0) for f in files)
        print(f"一括CSV: 使える。財務情報は {len(files)}ファイル "
              f"計{size/1e6:.0f}MB")
        if names:
            print(f"  一番古い {names[0]}")
            print(f"  一番新しい {names[-1]}")
        print()
        bulk_ok = True

    print(f"{'データ':<14} {'結果'}")
    print("-" * 90)
    for name, ep, has_bulk, _use in WANT:
        if bulk_ok and has_bulk:
            body, err = get(key, "/bulk/list", {"endpoint": ep})
            if body is None:
                print(f"{name:<14} 取れない ({err})")
            else:
                fs = body.get("data") or []
                ks = sorted(f.get("Key", "") for f in fs)
                span = f"{_ym(ks[0])}〜{_ym(ks[-1])}" if ks else "ファイルなし"
                print(f"{name:<14} {len(fs):>4}ファイル  {span}")
        else:
            body, err = get(key, ep, _probe_params(ep))
            n = len(body.get("data") or []) if body else 0
            print(f"{name:<14} {'API ' + str(n) + '行' if body else '取れない (' + err + ')'}")
        time.sleep(SLEEP)

    print()
    print("財務情報が2008年まで遡れていれば、決算シーズンが72回ぶん取れる。")
    print("2年しか出ないなら8回ぶんしかなく、条件1(前半と後半で向きが同じか)は当てられない。")


def _ym(k: str) -> str:
    """ファイル名から年月っぽい6桁を拾う。"""
    import re
    m = re.findall(r"(20\d{4})", k)
    return f"{m[-1][:4]}-{m[-1][4:]}" if m else k


def _probe_params(ep: str) -> dict:
    """API直叩きで様子を見るときの控えめなパラメータ。"""
    d = (date.today() - timedelta(days=120)).isoformat()
    if ep == "/fins/summary":
        return {"date": d}
    if ep == "/fins/earnings-date":
        return {"from": d, "to": d}
    return {"date": d}


# ---------------------------------------------------------------------------
# --pull: 実際に落とす
# ---------------------------------------------------------------------------

def pull(only: str | None, dry: bool) -> None:
    key = api_key()
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [w for w in WANT if only is None or w[0] == only]
    if not targets:
        sys.exit(f"--only {only} は候補にない。候補: "
                 + " / ".join(w[0] for w in WANT))

    body, _ = get(key, "/bulk/list", {"endpoint": "/fins/summary"})
    bulk_ok = body is not None

    for name, ep, has_bulk, use in targets:
        print(f"\n=== {name} ({ep}) ===")
        print(f"  用途: {use}")
        d = OUT / name
        d.mkdir(parents=True, exist_ok=True)
        if bulk_ok and has_bulk:
            _pull_bulk(key, ep, d, dry)
        elif ep == "/fins/summary":
            _pull_fins_by_code(key, d, dry)
        else:
            print("  一括CSVが使えず、代わりの取り方も無い。プランを上げるまで飛ばす")


def _pull_bulk(key: str, ep: str, d: Path, dry: bool) -> None:
    body, err = get(key, "/bulk/list", {"endpoint": ep})
    if body is None:
        print(f"  一覧が取れない ({err})")
        return
    files = sorted((f.get("Key", ""), int(f.get("Size") or 0))
                   for f in (body.get("data") or []))
    tot = sum(s for _, s in files)
    print(f"  {len(files)}ファイル 計{tot/1e6:.0f}MB")
    if dry:
        for k, s in files[:3]:
            print(f"    {k}  {s/1e6:.1f}MB")
        print("    ... (--dry なのでここまで)")
        return
    for i, (k, _s) in enumerate(files, 1):
        dest = d / (k.replace("/", "__").removesuffix(".gz"))
        if dest.exists() and dest.stat().st_size > 0:
            continue
        got, err = get(key, "/bulk/get", {"key": k})
        if got is None:
            print(f"  [{i}/{len(files)}] {k} URLが取れない ({err})")
            time.sleep(SLEEP)
            continue
        url = got.get("url") or got.get("Url") or (got.get("data") or {}).get("url")
        if not url:
            print(f"  [{i}/{len(files)}] {k} URLが応答に無い: {str(got)[:120]}")
            time.sleep(SLEEP)
            continue
        raw = requests.get(url, timeout=300).content
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass  # 既に非圧縮
        dest.write_bytes(raw)
        print(f"  [{i}/{len(files)}] {dest.name}  {len(raw)/1e6:.1f}MB")
        time.sleep(SLEEP)
    print(f"  → {d}")


def _pull_fins_by_code(key: str, d: Path, dry: bool) -> None:
    """一括CSVが使えないとき(Free)の代替。銘柄ごとに全期間を取る。

    加工は一切しない。API が返した行をそのまま1行1JSONで書く。
    既存の src/data/jquants.py と違って、業績予想修正の行も、
    同じ四半期の訂正短信も、全部残す。
    """
    codes = _universe_codes()
    print(f"  銘柄ごとに全期間を取る。{len(codes)}銘柄 × 1.1秒 ≒ "
          f"{len(codes)*SLEEP/60:.0f}分")
    if dry:
        print(f"    先頭5銘柄: {codes[:5]}")
        return
    dest = d / "fins_summary.jsonl"
    done = set()
    if dest.exists():
        with dest.open(encoding="utf-8") as f:
            for ln in f:
                try:
                    done.add(json.loads(ln).get("Code", "")[:4])
                except Exception:
                    pass
        print(f"    途中まで {len(done)}銘柄ぶんが既にある。続きから")
    n = 0
    with dest.open("a", encoding="utf-8") as f:
        for j, c in enumerate(codes, 1):
            if c in done:
                continue
            rows, err = pages(key, "/fins/summary", {"code": c})
            if err:
                print(f"    [{j}/{len(codes)}] {c} {err}")
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += len(rows)
            if j % 50 == 0:
                f.flush()
                print(f"    [{j}/{len(codes)}] 累計{n}行")
            time.sleep(SLEEP)
    print(f"  → {dest}  計{n}行")


def _universe_codes() -> list[str]:
    p = ROOT / "data" / "universe.json"
    if not p.exists():
        sys.exit(f"{p} が無い。銘柄一覧が要る")
    u = json.load(open(p, encoding="utf-8"))
    if isinstance(u, dict):
        for k in ("stocks", "codes", "universe", "data"):
            if isinstance(u.get(k), list):
                u = u[k]
                break
        else:
            sys.exit(f"{p} に銘柄の並びが見つからない。中身の形が変わった?")
    out = []
    for x in u:
        c = x if isinstance(x, str) else (x.get("code") or x.get("Code") or "")
        c = str(c).strip()[:4]
        if len(c) == 4:
            out.append(c)
    return sorted(set(out))


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="J-Quants から値動き以外のデータを生のまま落とす")
    ap.add_argument("--probe", action="store_true",
                    help="今の鍵で何年ぶん取れるかだけ確かめる(数リクエスト)")
    ap.add_argument("--pull", action="store_true", help="実際に落とす")
    ap.add_argument("--only", help="落とすものを1つに絞る(例: 財務情報)")
    ap.add_argument("--dry", action="store_true",
                    help="--pull で、何を落とすかだけ表示して落とさない")
    a = ap.parse_args()
    # 何十分もかかるので、途中経過がその場で流れるようにする
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    if a.probe:
        probe()
    elif a.pull:
        pull(a.only, a.dry)
    else:
        ap.print_help()
        print("\n候補:")
        for name, ep, _b, use in WANT:
            print(f"  {name:<14} {ep:<32} {use}")


if __name__ == "__main__":
    main()
