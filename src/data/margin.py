"""JPX無料公表「銘柄別信用取引週末残高」(週次PDF)の取得・解析。

https://www.jpx.co.jp/markets/statistics-equities/margin/05.html
毎週第2営業日(通常火曜)16:30頃に前週末(金曜申込時点)分のPDFが公表される。

【設計時の実測に基づく注意】HANDOFF_TASKS_20260718.txt の原仕様は
`requests + pandas.read_excel` 前提だったが、実際の本体ファイルは全銘柄
(約4,258銘柄)を収録した85ページ組のPDFだった(log.md (59)参照)。よって本モジュール
は openpyxl/xlrd ではなく pdfplumber でテキスト抽出→行パースする。関数名
`parse_margin_pdf` は元仕様の `parse_margin_excel` から実体に合わせて改名した。

PDF 1行(1銘柄)のテキスト形状(実測):
    [B] 銘柄名(普通株式サフィックス付き、コードと空白無しで連結することがある)
    コード(5桁。旧4桁銘柄は末尾0埋め "13010"、新英数コードも同様 "166A0")
    ISIN("JP"+10英数字) 数値12個(半角スペース区切り)
  数値12個の並び: [sell_total, sell_total_wow, buy_total, buy_total_wow,
                   general_sell, general_sell_wow, standard_sell, standard_sell_wow,
                   general_buy, general_buy_wow, standard_buy, standard_buy_wow]
  Total(合計)列は既に一般信用+制度信用の合算値(実測で検算済み)なので、
  そのまま使う(手動合算不要)。
  負数は "▲" が数字と別トークンになって現れる(例: "▲ 300" → -300)。

コード表記の正規化: PDFの5桁表記は末尾が常にチェックディジット"0"(実務上の
5桁化ルール)。data/universe.json は4桁(新英数含む)表記のため、5桁かつ末尾"0"
なら末尾を切り落として4桁化する。

【2026-07-30改定】保存範囲を「候補リストに載っている銘柄だけ」から「PDFに載って
いる全上場銘柄」へ変更した。以前は保存時に候補リスト(data/universe.json)で絞って
いたため、候補リストが1000→1579銘柄に増えた週(2026-07-28)に新しく入った581銘柄の
需給が全週で空になった。しかも絞り込みは黙って行われるので警告も出なかった。
信用残はどの銘柄も同じ1枚のPDFで一度に取れるので、絞らず全部持っておけば
候補リストが今後どう入れ替わっても穴が空かない(ファイルは26週で数MB規模)。
`parse_margin_pdf(content, universe_codes=None)` の第2引数は絞り込みたい場合のみ
渡す(テスト用。空/None なら絞らない)。
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import urljoin

import requests

from src.config import REPO_ROOT, load_config
from src.utils_io import atomic_write_json, safe_load_json

MARGIN_STORE_PATH = REPO_ROOT / "data" / "margin_weekly.json"

DEFAULT_PAGE_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html"
DEFAULT_KEEP_WEEKS = 13

_LINK_RE = re.compile(r'href="([^"]*syumatsu(\d{8})00\.pdf)"', re.IGNORECASE)

# 銘柄名(貪欲でない)+ コード(英数4桁+チェックディジット0) + ISIN + 残り数値列。
_ROW_RE = re.compile(
    r"^B?\s*(?P<name>\S.*?)(?P<code>[0-9][0-9A-Z]{2}[0-9A-Z]0)\s*"
    r"(?P<isin>JP[0-9A-Z]{10})\s+(?P<rest>.+)$"
)

_NUM_FIELDS = (
    "sell_total",
    "sell_total_wow",
    "buy_total",
    "buy_total_wow",
    "general_sell",
    "general_sell_wow",
    "standard_sell",
    "standard_sell_wow",
    "general_buy",
    "general_buy_wow",
    "standard_buy",
    "standard_buy_wow",
)


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------

def fetch_latest(config: dict | None = None) -> tuple[str, bytes] | None:
    """05.html から最新週のPDFリンクを抽出し、未取得なら (url, bytes) を返す。

    state(margin_weekly.json の last_url)と同一URLなら None(週次データを
    日次バッチで毎日ダウンロードしないため)。通信失敗は例外にせず None を返す。
    """
    config = config or load_config()
    mcfg = config.get("margin", {})
    if not mcfg.get("enabled", True):
        return None
    page_url = mcfg.get("page_url", DEFAULT_PAGE_URL)

    try:
        resp = requests.get(page_url, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"WARNING: margin.fetch_latest: page request failed ({e})")
        return None

    matches = _LINK_RE.findall(html)
    if not matches:
        print("WARNING: margin.fetch_latest: no weekly PDF link found on page")
        return None
    href, _ymd = max(matches, key=lambda m: m[1])
    pdf_url = urljoin(page_url, href)

    store = safe_load_json(MARGIN_STORE_PATH, {})
    if store.get("last_url") == pdf_url:
        return None

    try:
        resp = requests.get(pdf_url, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        print(f"WARNING: margin.fetch_latest: PDF download failed ({e})")
        return None

    return pdf_url, resp.content


def fetch_all_available(config: dict | None = None,
                        include_existing: bool = False) -> list[tuple[str, bytes]]:
    """05.html にある週末残高PDFリンクのうち、まだstoreに無い日付分をすべて
    ダウンロードして (url, bytes) のリストを日付昇順で返す。

    fetch_latest() と違い最新1件に絞らない。ただし実測ではJPXのページ自体が
    直近5週分程度しかバックナンバーを保持しないため、拾えるのは常にページに
    現存する分だけ(それより古い週は元データがJPX側に無く取得不能)。
    通信失敗時は例外を投げず、取れた分だけ(0件もありうる)を返す。

    include_existing=True にすると、既にstoreにある日付のPDFも再ダウンロードする
    (保存済みの週に足りない銘柄を後から足したい場合に使う)。
    """
    config = config or load_config()
    mcfg = config.get("margin", {})
    if not mcfg.get("enabled", True):
        return []
    page_url = mcfg.get("page_url", DEFAULT_PAGE_URL)

    try:
        resp = requests.get(page_url, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"WARNING: margin.fetch_all_available: page request failed ({e})")
        return []

    matches = _LINK_RE.findall(html)
    if not matches:
        return []
    matches = sorted(set(matches), key=lambda m: m[1])  # 日付昇順・重複排除

    store = safe_load_json(MARGIN_STORE_PATH, {})
    existing_dates = set() if include_existing else {
        h.get("date") for h in store.get("history", [])
    }

    results: list[tuple[str, bytes]] = []
    for href, ymd in matches:
        date_str = _ymd_to_iso(ymd)
        if date_str in existing_dates:
            continue
        pdf_url = urljoin(page_url, href)
        try:
            resp = requests.get(pdf_url, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"WARNING: margin.fetch_all_available: PDF download failed for {pdf_url} ({e})")
            continue
        results.append((pdf_url, resp.content))
    return results


def _extract_ymd(pdf_url: str) -> str | None:
    m = re.search(r"syumatsu(\d{8})00\.pdf", pdf_url, re.IGNORECASE)
    return m.group(1) if m else None


def _ymd_to_iso(ymd: str) -> str:
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


# ---------------------------------------------------------------------------
# パース
# ---------------------------------------------------------------------------

def _normalize_code(raw_code: str) -> str:
    if len(raw_code) == 5 and raw_code.endswith("0"):
        return raw_code[:4]
    return raw_code


def _parse_numbers(rest: str) -> list[int] | None:
    """"▲ 300" のような分離符号を再結合しつつ、先頭12個の整数を取り出す。"""
    raw_tokens = rest.split()
    tokens: list[str] = []
    i = 0
    while i < len(raw_tokens):
        tok = raw_tokens[i]
        if tok == "▲":  # ▲
            if i + 1 < len(raw_tokens):
                tokens.append("-" + raw_tokens[i + 1])
                i += 2
                continue
            i += 1
            continue
        tokens.append(tok)
        i += 1
    if len(tokens) < len(_NUM_FIELDS):
        return None
    nums: list[int] = []
    for tok in tokens[: len(_NUM_FIELDS)]:
        cleaned = tok.replace(",", "").replace("▲", "-")
        try:
            nums.append(int(cleaned))
        except ValueError:
            return None
    return nums


def _parse_row(line: str) -> dict | None:
    m = _ROW_RE.match(line.strip())
    if not m:
        return None
    nums = _parse_numbers(m.group("rest"))
    if nums is None:
        return None
    row = dict(zip(_NUM_FIELDS, nums))
    row["code"] = _normalize_code(m.group("code"))
    return row


def _extract_lines(content: bytes) -> list[str]:
    """PDFバイト列から全ページのテキスト行を抽出する(I/O境界。テストではここを
    monkeypatch して合成PDFバイナリ無しでパースロジックだけを検証する)。"""
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.splitlines())
    return lines


def parse_margin_pdf(content: bytes, universe_codes: set | None = None) -> tuple[dict, list[str]]:
    """PDFバイト列から {code: {"buy": int, "sell": int}} を作る。

    universe_codes を渡すとその集合に含まれる銘柄だけに絞る。省略/空なら
    PDFに載っている全銘柄を返す(通常運用はこちら。モジュール冒頭の注記参照)。

    1件もパースできなければ空dict + warning(例外は投げない。JPXの様式変更で
    パイプライン全体を止めないため)。
    """
    warnings: list[str] = []
    try:
        lines = _extract_lines(content)
    except Exception as e:
        return {}, [f"PDF open/extract failed: {e}"]

    by_code: dict = {}
    matched = 0
    for line in lines:
        row = _parse_row(line)
        if row is None:
            continue
        matched += 1
        code = row["code"]
        if universe_codes and code not in universe_codes:
            continue
        by_code[code] = {"buy": row["buy_total"], "sell": row["sell_total"]}

    if matched == 0:
        return {}, ["margin PDF format changed: 0 rows parsed"]

    return by_code, warnings


# ---------------------------------------------------------------------------
# ストア更新
# ---------------------------------------------------------------------------

def update_margin_store(config: dict | None = None) -> dict:
    config = config or load_config()
    mcfg = config.get("margin", {})
    keep_weeks = int(mcfg.get("keep_weeks", DEFAULT_KEEP_WEEKS))

    store = safe_load_json(
        MARGIN_STORE_PATH,
        {"updated_at": None, "last_url": None, "warnings": [], "history": []},
    )
    warnings: list[str] = []

    try:
        fetched = fetch_latest(config)
    except Exception as e:
        fetched = None
        warnings.append(f"fetch_latest raised: {e}")

    if fetched is None:
        if warnings:
            store["warnings"] = warnings
            atomic_write_json(MARGIN_STORE_PATH, store)
        return store

    pdf_url, content = fetched
    ymd = _extract_ymd(pdf_url)
    if ymd is None:
        warnings.append(f"could not extract date from url: {pdf_url}")
        store["warnings"] = warnings
        atomic_write_json(MARGIN_STORE_PATH, store)
        return store
    date_str = _ymd_to_iso(ymd)

    by_code, parse_warnings = parse_margin_pdf(content)
    warnings.extend(parse_warnings)

    history = list(store.get("history", []))
    history = [h for h in history if h.get("date") != date_str]  # 同一date置換
    history.append({"date": date_str, "by_code": by_code})
    history.sort(key=lambda h: h["date"])
    history = history[-keep_weeks:]

    store = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "last_url": pdf_url,
        "warnings": warnings,
        "history": history,
    }
    atomic_write_json(MARGIN_STORE_PATH, store)
    return store


def backfill_margin_history(config: dict | None = None, widen: bool = True) -> dict:
    """05.htmlに現存する過去分PDFをまとめて取得し、historyへ一気に追加する。

    通常運用の update_margin_store() は日次バッチから呼ばれ1週分ずつしか
    進めない設計。こちらは初回導入時などに、ページ上にまだ残っている過去分
    (実測で直近5週分程度)を一括で埋めたい場合に使う一回限りの関数。
    日次パイプラインからは呼ばれない。

    widen=True(既定)なら、既に保存済みの週も再ダウンロードして**足りない銘柄だけ**
    を足す(既存の値は上書きしない)。以前は保存時に候補リストで絞っていたので、
    リストが増えた後の週に古いリストぶんしか入っていない状態が残っている。
    widen=False なら従来どおり未保存の日付だけを足す。
    """
    config = config or load_config()
    mcfg = config.get("margin", {})
    keep_weeks = int(mcfg.get("keep_weeks", DEFAULT_KEEP_WEEKS))

    store = safe_load_json(
        MARGIN_STORE_PATH,
        {"updated_at": None, "last_url": None, "warnings": [], "history": []},
    )
    warnings: list[str] = []

    fetched = fetch_all_available(config, include_existing=widen)
    if not fetched:
        print("margin backfill: nothing new available on page")
        return store

    history = list(store.get("history", []))
    entry_by_date = {h.get("date"): h for h in history}
    last_url = store.get("last_url")
    added = 0
    filled = 0

    for pdf_url, content in fetched:
        ymd = _extract_ymd(pdf_url)
        if ymd is None:
            warnings.append(f"could not extract date from url: {pdf_url}")
            continue
        date_str = _ymd_to_iso(ymd)
        existing = entry_by_date.get(date_str)
        if existing is not None and not widen:
            continue
        by_code, parse_warnings = parse_margin_pdf(content)
        warnings.extend(parse_warnings)
        if not by_code:
            continue
        if existing is None:
            entry = {"date": date_str, "by_code": by_code}
            history.append(entry)
            entry_by_date[date_str] = entry
            added += 1
        else:
            stored = existing.setdefault("by_code", {})
            new_codes = [c for c in by_code if c not in stored]
            for c in new_codes:
                stored[c] = by_code[c]
            if new_codes:
                filled += len(new_codes)
                print(f"margin backfill: {date_str} filled {len(new_codes)} missing code(s)")
        last_url = pdf_url

    history.sort(key=lambda h: h["date"])
    history = history[-keep_weeks:]

    store = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "last_url": last_url,
        "warnings": warnings,
        "history": history,
    }
    if added or filled:
        atomic_write_json(MARGIN_STORE_PATH, store)
        print(f"margin backfill: added {added} week(s), filled {filled} missing code(s), "
              f"history now {len(history)} entries")
    else:
        print("margin backfill: nothing new to add (all already present)")
    return store


# ---------------------------------------------------------------------------
# J-Quants 週次信用残(過去分バックフィル用)
# ---------------------------------------------------------------------------
# JPXの無料PDF(05.html)は直近5週分程度しかバックナンバーを残さないため、半年分など
# 過去に遡った信用残は原理的に取得できない。J-Quants の /markets/margin-interest は
# 同じ週末残高を過去分まで配信しているので、初回の「過去半年埋め」にはこちらを使う。
# 日次運用は従来どおり update_margin_store()(JPX PDF)で1週ずつ進める。
# フィールド: LongVol=買残, ShrtVol=売残, Date=週末基準日(通常金曜), Code=5桁。

API_KEY_ENV = "JQUANTS_API_KEY"
DEFAULT_JQUANTS_API_URL = "https://api.jquants.com/v2"


class JQuantsAccessError(RuntimeError):
    """APIキーが無効、または契約プランが足りなくて弾かれた(401/403)。

    週次信用残 (/markets/margin-interest) は J-Quants の Standard プラン以上でしか
    配信されない。Free プランの鍵で叩くと 403 が返る。これを「その週はデータが無い」
    と同じ空リストで返してしまうと、バックフィルが全週を静かに空振りしたまま
    「nothing added」で正常終了したように見えてしまうので、区別して投げる。
    """


def _jquants_api_url(config: dict) -> str:
    return (config.get("jquants", {}) or {}).get("api_url", DEFAULT_JQUANTS_API_URL)


def fetch_weekly_margin_by_date(api_key: str, date_str: str, config: dict) -> list[dict]:
    """/markets/margin-interest?date= を叩き、その週末日の全銘柄レコードを返す。

    レスポンスは {"data": [...], "pagination_key": ...}。全ページ辿って結合する。
    その日付が基準日でない(祝日でFri休みの週など)なら空リスト。通信エラーは
    例外を投げず空リストにして、バックフィル全体を止めない。

    ただし **401/403 だけは JQuantsAccessError を投げる**。鍵かプランの問題は
    日付を変えても直らないので、残りの週を叩き続けても意味が無いうえ、空リストで
    返すと「データが無い週」と見分けが付かなくなる。
    """
    api_url = _jquants_api_url(config)
    params: dict = {"date": date_str}
    records: list[dict] = []
    while True:
        try:
            resp = requests.get(
                f"{api_url}/markets/margin-interest",
                params=params,
                headers={"x-api-key": api_key},
                timeout=60,
            )
            if resp.status_code == 429:  # レートリミット。少し待って1回だけ粘る
                time.sleep(30)
                resp = requests.get(
                    f"{api_url}/markets/margin-interest",
                    params=params,
                    headers={"x-api-key": api_key},
                    timeout=60,
                )
            if resp.status_code in (401, 403):
                raise JQuantsAccessError(
                    f"/markets/margin-interest が {resp.status_code} を返した"
                    f"(date={date_str})。APIキーが無効か、契約プランが足りていない。"
                    " このエンドポイントは Standard プラン以上でのみ配信される。"
                )
            resp.raise_for_status()
        except JQuantsAccessError:
            raise
        except Exception as e:
            print(f"WARNING: margin.fetch_weekly_margin_by_date({date_str}): {e}")
            return []
        body = resp.json()
        records.extend(body.get("data") or [])
        pk = body.get("pagination_key")
        if not pk:
            return records
        params["pagination_key"] = pk


def _jq_records_to_by_code(records: list[dict], universe_codes: set | None = None) -> dict:
    """margin-interest レコード列を {code: {"buy": int, "sell": int}} に畳む。
    LongVol=買残, ShrtVol=売残。5桁コードを4桁へ正規化する。
    universe_codes を渡した場合のみその集合外を捨てる(既定は絞らない。
    JPX PDF パースと同じ扱い。モジュール冒頭の2026-07-30改定の注記参照)。
    """
    by_code: dict = {}
    for rec in records:
        raw = str(rec.get("Code") or "")
        if not raw:
            continue
        code = _normalize_code(raw)
        if universe_codes and code not in universe_codes:
            continue
        buy = rec.get("LongVol")
        sell = rec.get("ShrtVol")
        if buy is None or sell is None:
            continue
        try:
            by_code[code] = {"buy": int(buy), "sell": int(sell)}
        except (TypeError, ValueError):
            continue
    return by_code


def _recent_week_end_dates(weeks: int, today: date | None = None) -> list[str]:
    """直近の金曜から weeks 週分の週末日(金曜基準)をISO文字列で新しい順に返す。"""
    today = today or date.today()
    offset = (today.weekday() - 4) % 7  # Mon=0..Fri=4。直近の金曜まで戻す
    friday = today - timedelta(days=offset)
    return [(friday - timedelta(days=7 * i)).isoformat() for i in range(weeks)]


def backfill_margin_jquants(config: dict | None = None, weeks: int | None = None,
                            widen: bool = True) -> dict:
    """J-Quants /markets/margin-interest から過去 weeks 週分の週末信用残を取得し、
    store の history へ一括投入する(初回の過去半年埋め用。日次からは呼ばない)。

    - 週末日は「直近金曜から7日ずつ遡る」で生成。祝日でFri休みの週に備え、
      Friで空なら Thu, Wed も試す(最初に非空が取れた日を採用)。
    - widen=True(既定)なら、既に保存済みの週も取り直して**足りない銘柄だけ**を足す
      (既存の値は上書きしない)。以前は保存時に候補リストで絞っていたので、
      リストが増えた後の週に古いリストぶんしか入っていない状態が残っている。
      widen=False なら従来どおり保存済みの日付をスキップする。
    - 取得後 keep_weeks で新しい順にトリム。API鍵が無ければ何もしない。
    """
    config = config or load_config()
    mcfg = config.get("margin", {})
    keep_weeks = int(mcfg.get("keep_weeks", DEFAULT_KEEP_WEEKS))
    weeks = int(weeks if weeks is not None else keep_weeks)

    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"margin jquants backfill: {API_KEY_ENV} not set; skipping")
        return safe_load_json(MARGIN_STORE_PATH, {})

    store = safe_load_json(
        MARGIN_STORE_PATH,
        {"updated_at": None, "last_url": None, "warnings": [], "history": []},
    )
    history = list(store.get("history", []))
    entry_by_date = {h.get("date"): h for h in history}
    added = 0
    filled = 0
    requested = 0   # 実際にAPIを叩いた回数
    got_rows = 0    # そのうち1件以上のレコードが返ってきた回数

    for friday in _recent_week_end_dates(weeks):
        if friday in entry_by_date and not widen:
            continue
        base = date.fromisoformat(friday)
        by_code: dict = {}
        used_date: str | None = None
        for back in (0, 1, 2):  # Fri→Thu→Wed(祝日シフト対策)
            cand = (base - timedelta(days=back)).isoformat()
            if cand in entry_by_date and not widen:
                break
            requested += 1
            try:
                records = fetch_weekly_margin_by_date(api_key, cand, config)
            except JQuantsAccessError as e:
                # 鍵かプランの問題。日付を変えても直らないので即座に打ち切る。
                # ここで store を書かずに返すことで、既存の履歴を壊さない。
                print(f"ERROR: margin jquants backfill aborted: {e}")
                return store
            if records:
                got_rows += 1
                by_code = _jq_records_to_by_code(records)
                used_date = cand
                break
        if not used_date or not by_code:
            continue
        existing = entry_by_date.get(used_date)
        if existing is None:
            entry = {"date": used_date, "by_code": by_code}
            history.append(entry)
            entry_by_date[used_date] = entry
            added += 1
        else:
            stored = existing.setdefault("by_code", {})
            new_codes = [c for c in by_code if c not in stored]
            for c in new_codes:
                stored[c] = by_code[c]
            if new_codes:
                filled += len(new_codes)
                print(f"margin jquants backfill: {used_date} filled {len(new_codes)} "
                      f"missing code(s)")

    history.sort(key=lambda h: h["date"])
    history = history[-keep_weeks:]

    store["updated_at"] = datetime.now().astimezone().isoformat()
    store["history"] = history
    if added or filled:
        atomic_write_json(MARGIN_STORE_PATH, store)
        print(f"margin jquants backfill: added {added} week(s), filled {filled} "
              f"missing code(s), history now {len(history)} entries")
    elif requested and not got_rows:
        # 叩いたのに1件も返ってこなかった。「全部保存済みだった」とは別物なので
        # 同じ文言にしない(そうしないと空振りが正常終了に見える)。
        print(f"WARNING: margin jquants backfill: {requested} request(s) all returned "
              f"no rows; nothing was stored. APIキー・契約プラン・日付範囲を確認すること")
    else:
        print("margin jquants backfill: nothing added (all present or no data)")
    return store


# ---------------------------------------------------------------------------
# メトリクス(表示専用。総合スコアには一切使わない)
# ---------------------------------------------------------------------------

def build_margin_metrics(code: str, latest_row: dict | None, store: dict | None = None) -> dict | None:
    """最新週の信用残メトリクスを組み立てる。データが無ければ None。

    store を省略すると data/margin_weekly.json を読む(パイプライン呼び出し用)。
    テストでは合成 store を直接渡してI/O無しで検証できる。
    """
    store = store if store is not None else safe_load_json(MARGIN_STORE_PATH, {})
    history = store.get("history") or []
    if not history:
        return None
    latest = history[-1]
    entry = (latest.get("by_code") or {}).get(code)
    if entry is None:
        return None
    buy = entry.get("buy")
    sell = entry.get("sell")
    if buy is None or sell is None:
        return None

    ratio = round(buy / sell, 3) if sell else None

    buy_wow_pct = None
    if len(history) >= 2:
        prev_entry = (history[-2].get("by_code") or {}).get(code)
        prev_buy = prev_entry.get("buy") if prev_entry else None
        if prev_buy:
            buy_wow_pct = round((buy / prev_buy - 1.0) * 100.0, 2)

    vol_ma50 = (latest_row or {}).get("vol_ma50")
    days_to_cover = round(buy / vol_ma50, 2) if vol_ma50 else None

    # 信用倍率の週次推移(遍歴グラフ用)。storeにある全週のうち、当該コードが
    # 収録されている週だけを日付昇順で {date, ratio} にする。売残0でratio不能な
    # 週は ratio=None(グラフ側で欠損として扱う)。表示専用・スコアには使わない。
    trend = []
    for h in history:
        e = (h.get("by_code") or {}).get(code)
        if not e:
            continue
        hb = e.get("buy")
        hs = e.get("sell")
        if hb is None or hs is None:
            continue
        trend.append({"date": h.get("date"), "ratio": round(hb / hs, 3) if hs else None, "buy": hb, "sell": hs})

    return {
        "ratio": ratio,
        "buy": buy,
        "sell": sell,
        "date": latest.get("date"),
        "buy_wow_pct": buy_wow_pct,
        "days_to_cover": days_to_cover,
        "history": trend,
    }


# ---------------------------------------------------------------------------
# CLI(過去分バックフィルの手動実行用。日次パイプラインからは呼ばない)
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="信用残ストアのユーティリティ")
    parser.add_argument(
        "--backfill-jquants",
        action="store_true",
        help="J-Quants /markets/margin-interest から過去週分を一括投入(要 JQUANTS_API_KEY)",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=None,
        help="遡る週数(既定: config の margin.keep_weeks)",
    )
    parser.add_argument(
        "--no-widen",
        action="store_true",
        help="既に保存済みの週は取り直さない(既定は取り直して足りない銘柄だけ足す)",
    )
    args = parser.parse_args()
    if args.backfill_jquants:
        backfill_margin_jquants(weeks=args.weeks, widen=not args.no_widen)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
