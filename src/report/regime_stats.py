"""本番で毎日出している候補の「その後」を貯めて集計する (2026-07-31追加)。

## なぜ要るか

このスクリーナーは毎日「発注(order)」「監視(watch)」の候補を出しているが、
**その判断が当たっていたかを本番データ側で測る仕組みが無かった**。
src/backtest.py は過去の日足からセットアップを作り直して測るもので、本番で実際に
画面に出した候補そのものの成績ではない。

答えたいのは2つだけ:

1. 今日はエントリーしていい地合いか。地合いスコアが高い日に出た候補と低い日に
   出た候補で、その後の騰落率がどれだけ違うか。
2. 出来高を伴わないブレイクは本当にダメなのか。ブレイク日の出来高が50日平均の
   何倍だったかで分けたときの差。

どちらも人間の記憶では必ず都合よく歪むので、機械が数えた数字だけを出す。

## 作りは src/report/dryup_log.py に倣う

毎日ぜんぶ計算し直すのではなく、**決着した行はもう触らない**。将来の値動きが
足りない行は「まだ分からない」のまま据え置き、後日また見に行く。前の日の結果を
引き継ぐ形なので、1日だけ計算しても同じ答えにはならない(だから貯める)。

## ブレイク判定は「記録日の高値」を基準にした代理判定

data/history/stage.jsonl には pivot の値が入っていない(入っているのは pivot が
有るか無いかの真偽値だけ)。そこでブレイクは
**「記録日の高値を、その後の終値が初めて上回った日」** を代理条件とする。
本来のピボット上抜けとは別物である点に注意。dryup_log.py の broken 判定が
pivot 基準の代理になっているのと同じ考え方。

## 追跡日数

    FORWARD_DAYS        = 10  記録日(またはブレイク日)から何営業日後を見るか
    BREAKOUT_WAIT_DAYS  = 20  記録日から何営業日以内のブレイクを待つか

どちらも src/report/dryup_log.py の POST_BREAKOUT_DAYS / BREAKOUT_WAIT_DAYS と
同じ値。ここだけ別の日数にすると、同じリポジトリの中に「10営業日後の成績」と
「15営業日後の成績」が並んで読む側が取り違えるため揃えている
(tests/test_regime_stats.py が値の一致を見張っている)。

## 単位の約束(ここを取り違えると画面に嘘が出る)

- `win_rate` は 0〜1 の割合。画面側が100倍して%にする。
- `median_return` / `return_pct` は **%表記の数値**。-0.8 は -0.8% であって
  -80% でも -0.008 でもない。
- `market_score` は 0〜100 のスコアそのもの(割合ではない)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import REPO_ROOT

JST = timezone(timedelta(hours=9))

CANDIDATE_OUTCOMES_JSONL = REPO_ROOT / "data" / "history" / "candidate_outcomes.jsonl"
CANDIDATE_OUTCOMES_KEY = ("date", "code")
STATS_JSON_PATH = REPO_ROOT / "docs" / "data" / "stats.json"

# 母集団: 発注(order)と監視(watch)だけ。あと一歩(near)や形成中(forming)は
# 「今日エントリーしていいか」の問いの対象ではないので入れない。
TARGET_BUCKETS = ("order", "watch")

# 追跡日数(営業日)。src/report/dryup_log.py と同じ値に揃えること。
FORWARD_DAYS = 10
BREAKOUT_WAIT_DAYS = 20

# 帯ごとの最小件数。これ未満は reliable=false にして「参考値」として出す
# (行そのものは消さない。消すとその帯にサンプルが無いことに気付けなくなる)。
# 既存の集計 (tools/aggregate_dryup_log.py) は n<10 を参考値としているが、
# ここは勝率と中央値という「割合」を出すので、もう少し厳しく 20 を採る。
MIN_BUCKET_N = 20

# 保持。1日あたり十数行しか増えないので長めに持つ(四半期をまたいで比べたい)。
OUTCOMES_KEEP_DAYS = 400
OUTCOMES_MAX_LINES = 20000

# ---------------------------------------------------------------------------
# 帯(バケット)の定義
# ---------------------------------------------------------------------------

# 出来高倍率の区切り 1.4 / 2.0 / 3.0 は scripts/investigate_dryup_confound.py の
# vol_bucket() をそのまま持ってきたもの。下限の 1.4 は config.yaml の
# entry.breakout_vol_mult(=強ブレイクの閾値。src/backtest.py の is_strong_breakout と
# src/screener/entry.py が使う)と同じ値。**新しい区切りを勝手に作らないこと** —
# 区切りが違うと過去のバックテスト結果と横に並べられなくなる。
VOLUME_BANDS = (
    (1.4, "1.4倍未満"),
    (2.0, "1.4〜2.0倍"),
    (3.0, "2.0〜3.0倍"),
    (None, "3.0倍以上"),
)

# 地合いスコアの帯は 20 点刻みの固定。分布の四分位で切らないのは、四分位だと
# **データが増えるたびに区切り自体が動く**ため。区切りと中身の両方が動くと
# 「先週より良くなった」のか「線が動いただけ」なのかが分からなくなる。
# 20点刻みなのは、スコアの各内訳が値を取れないとき 50 点で埋まる仕様
# (src/report/market_signal.py)で 50 が中立点だから。50 をまたぐ 40〜60 を
# 中立の帯として、その上下に同じ幅の帯を置いている。
REGIME_BANDS = (
    (40.0, "40未満"),
    (60.0, "40〜60"),
    (80.0, "60〜80"),
    (None, "80以上"),
)


def _band_label(value, bands) -> str | None:
    """値を帯のラベルへ。下限を含み上限を含まない(40〜60 は 40 <= x < 60)。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    for upper, label in bands:
        if upper is None or v < upper:
            return label
    return None


def regime_band(market_score) -> str | None:
    return _band_label(market_score, REGIME_BANDS)


def volume_band(vol_ratio) -> str | None:
    return _band_label(vol_ratio, VOLUME_BANDS)


# ---------------------------------------------------------------------------
# レコード生成
# ---------------------------------------------------------------------------

def build_outcome_record(stage_row: dict, market_score=None) -> dict:
    """stage.jsonl の1行から、まだ何も分かっていない候補レコードを作る。

    価格由来の欄(ref_high / ref_close / breakout / 騰落率)は全部 None で始める。
    足りないデータを楽観で埋めないこと。後日 resolve_record が前進させる。
    """
    return {
        "date": stage_row.get("date"),
        "code": stage_row.get("code"),
        "bucket": stage_row.get("bucket"),
        "total_score": stage_row.get("total_score"),
        # その日の地合いスコア。行に焼き込むのは breadth.json の履歴が60日で
        # 切られるため(あとから引き直そうとしても古い日は取れない)。
        "market_score": market_score,
        # ブレイク判定の基準にした記録日の高値と、騰落率の起点にした記録日の終値。
        "ref_high": None,
        "ref_close": None,
        # None = まだ分からない / True = 上抜けた / False = 待機窓を空振り
        "breakout": None,
        "breakout_date": None,
        "vol_ratio_at_breakout": None,
        # 記録日の終値 -> FORWARD_DAYS営業日後の終値。単位は %。
        "return_pct": None,
        # ブレイク日の終値 -> そのFORWARD_DAYS営業日後の終値。単位は %。
        "return_after_breakout_pct": None,
        # True になったらもう再計算しない(毎日フルスキャンしないための印)。
        "resolved": False,
    }


# ---------------------------------------------------------------------------
# 先行きの解決(pandas を使わない純粋関数)
# ---------------------------------------------------------------------------

def frame_to_bars(df) -> list[tuple]:
    """指標付きフレームを (日付文字列, 高値, 終値, 出来高倍率) の昇順リストにする。

    ここだけが DataFrame を触る。以降の判定は素の Python だけで動くので、
    テストが pandas 無しで書ける(重い依存をテストに持ち込まない)。
    出来高倍率は volume / vol_ma50。vol_ma50 が無い・0 の日は None。
    """
    if df is None or len(df) == 0:
        return []
    dates = df["date"].tolist()
    highs = df["high"].tolist()
    closes = df["close"].tolist()
    has_vol = "volume" in df.columns and "vol_ma50" in df.columns
    volumes = df["volume"].tolist() if has_vol else [None] * len(dates)
    vol_ma50s = df["vol_ma50"].tolist() if has_vol else [None] * len(dates)

    bars = []
    for d, h, c, v, vm in zip(dates, highs, closes, volumes, vol_ma50s):
        key = d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10]
        bars.append((key, _num(h), _num(c), _vol_ratio(v, vm)))
    bars.sort(key=lambda b: b[0])
    return bars


def _num(v):
    """float 化。None / NaN は None にする(NaN を数値として扱うと全部が黙って壊れる)。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _vol_ratio(volume, vol_ma50):
    vol = _num(volume)
    vma = _num(vol_ma50)
    if vol is None or vma is None or vma == 0:
        return None
    return round(vol / vma, 3)


def _pct_change(base, later):
    if base is None or later is None or base <= 0:
        return None
    return round((later / base - 1.0) * 100.0, 2)


def resolve_record(record: dict, bars: list[tuple]) -> dict:
    """1レコードを、いま手元にあるバーの範囲で前進させる(in-place)。

    冪等: 同じバーを渡せば何度呼んでも同じ結果になる。決着済み(resolved)の行は
    即座に返す。将来のバーが足りない欄は None のまま据え置いて後日また見に行く。
    """
    if record.get("resolved"):
        return record
    rec_date = record.get("date")
    if not rec_date or not bars:
        return record

    by_date = {b[0]: b for b in bars}
    same_day = by_date.get(rec_date)
    if same_day is None:
        # 記録日のバーがまだ(あるいはもう)無い。基準が取れないので触らない。
        #
        # ただし記録日より十分あとのバーが揃ってもまだ記録日の足が来ないなら、
        # その日の価格取得に失敗したまま埋め戻されなかった行なので、待っても
        # 一生埋まらない。放っておくと「まだ決着していない候補がN件あります」が
        # 永久に消えないので、諦めた印を付けて決着扱いにする(表には入らない)。
        if len([b for b in bars if b[0] > rec_date]) >= BREAKOUT_WAIT_DAYS:
            record["unusable"] = True
            record["resolved"] = True
        return record
    if record.get("ref_high") is None:
        record["ref_high"] = same_day[1]
    if record.get("ref_close") is None:
        record["ref_close"] = same_day[2]

    future = [b for b in bars if b[0] > rec_date]

    # --- ブレイクしたか ---
    if record.get("breakout") is None and record.get("ref_high") is not None:
        ref_high = record["ref_high"]
        wait = future[:BREAKOUT_WAIT_DAYS]
        for d, _h, close, vol_ratio in wait:
            if close is not None and close > ref_high:
                record["breakout"] = True
                record["breakout_date"] = d
                record["vol_ratio_at_breakout"] = vol_ratio
                break
        else:
            # 待機窓ぶんの将来のバーが揃って初めて「空振り」と言える。
            # 揃っていなければ「まだ分からない」のまま。
            if len(future) >= BREAKOUT_WAIT_DAYS:
                record["breakout"] = False

    # --- 記録日からの騰落率 ---
    if record.get("return_pct") is None and len(future) >= FORWARD_DAYS:
        record["return_pct"] = _pct_change(record.get("ref_close"), future[FORWARD_DAYS - 1][2])

    # --- ブレイク日からの騰落率(出来高倍率と突き合わせるのはこちら) ---
    if record.get("breakout") is True and record.get("return_after_breakout_pct") is None:
        bo_date = record.get("breakout_date")
        bo_bar = by_date.get(bo_date)
        post = [b for b in future if b[0] > bo_date] if bo_date else []
        if bo_bar is not None and len(post) >= FORWARD_DAYS:
            record["return_after_breakout_pct"] = _pct_change(bo_bar[2], post[FORWARD_DAYS - 1][2])

    record["resolved"] = _is_resolved(record)
    return record


def _is_resolved(record: dict) -> bool:
    """もう再計算しなくてよいか。

    ブレイクしなかった行は記録日からの騰落率が出れば終わり。ブレイクした行は
    ブレイク日からの騰落率も出るまで終わらない(出来高の集計がそれを使うため)。
    """
    if record.get("return_pct") is None:
        return False
    if record.get("breakout") is None:
        return False
    if record.get("breakout") is True:
        return record.get("return_after_breakout_pct") is not None
    return True


# ---------------------------------------------------------------------------
# 集計(pandas を使わない純粋関数)
# ---------------------------------------------------------------------------

def _median(values: list[float]):
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2:
        return round(s[mid], 2)
    return round((s[mid - 1] + s[mid]) / 2.0, 2)


def summarize_bands(labelled: list[tuple], bands, min_n: int = MIN_BUCKET_N) -> list[dict]:
    """(帯ラベル, 騰落率%) の並びを帯ごとの行にする。

    - `win_rate` は騰落率が 0 より大きい行の割合(0〜1)。ちょうど 0 は勝ちにしない。
    - `median_return` は %表記の数値(-0.8 は -0.8%)。
    - 件数0の帯も行を残す。消すと「その帯にサンプルが無い」ことに気付けなくなる。
    """
    grouped: dict[str, list[float]] = {label: [] for _u, label in bands}
    for label, ret in labelled:
        if label in grouped and ret is not None:
            grouped[label].append(float(ret))

    rows = []
    for _upper, label in bands:
        vals = grouped[label]
        n = len(vals)
        wins = sum(1 for v in vals if v > 0)
        rows.append({
            "label": label,
            "n": n,
            "win_rate": round(wins / n, 4) if n else None,
            "median_return": _median(vals),
            "reliable": n >= min_n,
        })
    return rows


def build_stats(records: list[dict], *, now: datetime | None = None,
                min_n: int = MIN_BUCKET_N) -> dict:
    """候補レコード一覧から docs/data/stats.json の中身を組み立てる。

    契約(キー名)は画面側と決め打ちなので変えないこと。
    """
    now = now or datetime.now(JST)
    dates = sorted({r.get("date") for r in records if r.get("date")})

    # **表ごとに「その表に必要な値が出ているか」で採否を決める。** 行全体の決着
    # (resolved)を条件にすると、地合いの表まで出来高の表の都合で待たされる。
    # 地合いの表に要るのは記録日からの騰落率だけで FORWARD_DAYS(10営業日)後には
    # 出ているのに、resolved はブレイクの空振り確定に BREAKOUT_WAIT_DAYS(20営業日)
    # かかるので、揃えると地合いの表が10営業日ぶん余計に遅れて出てくることになる。
    # 地合いの表: 候補に出た日を起点にした騰落率で見る。
    regime_pairs = [
        (regime_band(r.get("market_score")), r.get("return_pct"))
        for r in records if r.get("return_pct") is not None
    ]
    regime_rows = summarize_bands(regime_pairs, REGIME_BANDS, min_n)

    # 出来高の表: ブレイクした行だけ。起点はブレイク日
    # (記録日を起点にすると、ブレイク前の値動きまで出来高倍率のせいにしてしまう)。
    volume_pairs = [
        (volume_band(r.get("vol_ratio_at_breakout")), r.get("return_after_breakout_pct"))
        for r in records
        if r.get("breakout") is True and r.get("return_after_breakout_pct") is not None
    ]
    volume_rows = summarize_bands(volume_pairs, VOLUME_BANDS, min_n)

    # 注記の件数は「どちらの表にもまだ入れない行」を数える。表に1つでも入っていれば
    # 待ちではない。
    counted = sum(1 for _b, v in regime_pairs if v is not None)
    pending = sum(
        1 for r in records
        if not r.get("unusable")
        and r.get("return_pct") is None
        and not (r.get("breakout") is True and r.get("return_after_breakout_pct") is not None)
    )
    unusable = sum(1 for r in records if r.get("unusable"))

    no_score = [
        r for r in records
        if r.get("return_pct") is not None and regime_band(r.get("market_score")) is None
    ]
    no_score_days = len({r.get("date") for r in no_score})
    no_breakout = sum(1 for r in records if r.get("breakout") is False)
    no_vol_ratio = sum(
        1 for r in records
        if r.get("breakout") is True
        and r.get("return_after_breakout_pct") is not None
        and volume_band(r.get("vol_ratio_at_breakout")) is None
    )

    notes = [
        f"母集団は毎日の発注・監視の候補です。地合いの表は騰落率が出た{counted}件を数えています。",
        f"地合いの表は候補に出た日の終値を起点に、{FORWARD_DAYS}営業日後の終値までの騰落率です。",
        f"出来高の表はブレイクした日の終値を起点に、{FORWARD_DAYS}営業日後までの騰落率です"
        "(起点が違うので、地合いの表の数字とは直接比べられません)。",
        f"ブレイクは「候補に出た日の高値を、その後{BREAKOUT_WAIT_DAYS}営業日以内の終値が"
        "初めて上回ったこと」で判定しています。ピボットの値は履歴に残っていないための代用です。",
        "帯の区切りは下限を含み、上限を含みません(40〜60 は 40 以上 60 未満)。",
        "勝率は騰落率が0より大きかった行の割合です。ちょうど0は勝ちに数えていません。",
        f"件数が{min_n}件に満たない帯は参考値です(行は消さずに出しています)。",
        "帯を4つ並べて一番良いものを選ぶと、たまたま良く見えただけの帯を選びやすくなります。"
        "隣の帯と順番が揃っているときだけ手掛かりにしてください。",
    ]
    if pending:
        notes.append(
            f"まだ決着していない候補が{pending}件あります。この先の値動きが足りないだけなので、"
            "日が経てば表に入ります。")
    if unusable:
        notes.append(
            f"候補に出た日の値段が残っていない行が{unusable}件あり、起点が取れないので"
            "どちらの表にも入っていません。")
    if no_score:
        notes.append(
            f"地合いスコアが取れない日が{no_score_days}日あり、その日の候補{len(no_score)}件は"
            "地合いの表に入っていません。")
    if no_breakout:
        notes.append(
            f"一度もブレイクしなかった候補が{no_breakout}件あります。ブレイク日が無いので"
            "出来高の表には入りません。")
    if no_vol_ratio:
        notes.append(
            f"ブレイクした日の出来高の平均が取れなかった候補が{no_vol_ratio}件あり、"
            "出来高の表に入っていません。")

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {
            "from": dates[0] if dates else None,
            "to": dates[-1] if dates else None,
            "days": len(dates),
        },
        "forward_days": FORWARD_DAYS,
        "regime": {"min_n": min_n, "rows": regime_rows},
        "volume": {"min_n": min_n, "rows": volume_rows},
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# 新しい候補行の切り出し(純粋関数)
# ---------------------------------------------------------------------------

def select_new_rows(stage_rows, known_keys: set, score_by_date: dict | None = None) -> list[dict]:
    """stage.jsonl の行から、まだ記録していない発注・監視の候補だけを作る。

    stage.jsonl は追記専用で同じ (date, code) が複数回出るので、後勝ちで1件に潰す
    (history_store.load_deduped と同じ作法)。
    """
    score_by_date = score_by_date or {}
    picked: dict[tuple, dict] = {}
    for row in stage_rows:
        if row.get("bucket") not in TARGET_BUCKETS:
            continue
        key = (row.get("date"), row.get("code"))
        if None in key or key in known_keys:
            continue
        picked[key] = row
    return [
        build_outcome_record(row, market_score=score_by_date.get(row.get("date")))
        for row in picked.values()
    ]


def market_score_by_date(breadth: dict | None) -> dict:
    """breadth.json の history から 日付 -> 地合いスコア を作る。"""
    out = {}
    for entry in ((breadth or {}).get("history") or []):
        d = entry.get("date")
        score = entry.get("market_score")
        if d and score is not None:
            out[d] = score
    return out


# ---------------------------------------------------------------------------
# パイプラインからの入口
# ---------------------------------------------------------------------------

def update_candidate_outcomes(indicator_by_code: dict, now: datetime | None = None) -> dict:
    """大引バッチの最後に呼ぶ入口。候補レコードを貯めて stats.json を書く。

    価格は pipeline が既に読んで指標まで載せた `indicator_by_code` をそのまま使う
    (parquet を読み直さない)。戻り値は追加件数・前進件数などの内訳。
    """
    from src.history_store import (
        append_records, calendar_keep_days, compact, count_lines, iter_records, load_deduped,
    )
    from src.report.build_site import DOCS_DATA_DIR
    from src.report.secure_io import read_docs_json, write_docs_json
    from src.report.stage_log import STAGE_HISTORY_JSONL

    records = load_deduped(CANDIDATE_OUTCOMES_JSONL, CANDIDATE_OUTCOMES_KEY)
    known = {(r.get("date"), r.get("code")) for r in records}

    breadth = read_docs_json(DOCS_DATA_DIR / "breadth.json", default=None)
    score_by_date = market_score_by_date(breadth)

    new_rows = select_new_rows(iter_records(STAGE_HISTORY_JSONL), known, score_by_date)
    records.extend(new_rows)

    # 決着済みは触らない。ここが「毎日フルスキャンしない」の実体。
    changed = []
    bars_cache: dict[str, list[tuple]] = {}
    for rec in records:
        if rec.get("resolved"):
            continue
        code = rec.get("code")
        if code not in bars_cache:
            bars_cache[code] = frame_to_bars(indicator_by_code.get(code))
        bars = bars_cache[code]
        if not bars:
            continue
        before = _snapshot(rec)
        # 地合いスコアは記録時に取れていなくても、後から breadth に載る日がある。
        if rec.get("market_score") is None and rec.get("date") in score_by_date:
            rec["market_score"] = score_by_date[rec["date"]]
        resolve_record(rec, bars)
        if _snapshot(rec) != before:
            changed.append(rec)

    # 新規行と、中身が変わった行だけを追記する。変わっていない行を毎日書くと
    # 追記専用の利点(git 差分が増えた行だけ)が消える。同じ日に2回走らせても
    # 2回目は何も変わらないので何も追記されない(冪等)。
    to_append = new_rows + [r for r in changed if r not in new_rows]
    append_records(CANDIDATE_OUTCOMES_JSONL, to_append)
    if count_lines(CANDIDATE_OUTCOMES_JSONL) > OUTCOMES_MAX_LINES:
        removed = compact(
            CANDIDATE_OUTCOMES_JSONL, CANDIDATE_OUTCOMES_KEY,
            keep_days=calendar_keep_days(OUTCOMES_KEEP_DAYS),
        )
        print(f"candidate_outcomes: compaction で {removed} 行を削減")

    stats = build_stats(records, now=now)
    write_docs_json(STATS_JSON_PATH, stats)

    return {
        "appended": len(to_append),
        "new": len(new_rows),
        "advanced": len(changed),
        "total": len(records),
        "settled": sum(1 for r in records if r.get("resolved")),
    }


def _snapshot(record: dict) -> tuple:
    """変化を見るための比較用タプル(dict のコピーを作らずに済ませる)。"""
    return (
        record.get("ref_high"), record.get("ref_close"), record.get("market_score"),
        record.get("breakout"), record.get("breakout_date"),
        record.get("vol_ratio_at_breakout"), record.get("return_pct"),
        record.get("return_after_breakout_pct"), record.get("resolved"),
    )
