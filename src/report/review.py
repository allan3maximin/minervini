"""その日の相場を前場断面と大引断面から機械的にまとめる「日次レビュー」(2026-07-31追加)。

## なぜ要るか

このスクリーナーは1日2回走る。前場バッチ(11:35 JST)が report_maezyou.json などの
前場断面を、大引バッチ(夕方)が canonical な report.json を書く。画面の断面切替で
どちらも見られるようにはなったが、**「前場に出ていた候補が引けまでにどうなったか」は
2つの画面を人間が見比べないと分からない**。見比べるのは毎日やる作業ではないので、
結局「前場にブレイクしていた銘柄が実際どれくらい引けまで残るのか」が体感でしか
分からないままになる。

そこで大引バッチの最後で2断面を突き合わせ、その日の要約を review.json に落とす。
LLMは噛まない。すべてルールベースで、文章の組み立て方は src/report/summary.py
(個別銘柄のサマリー生成)に倣っている。

## 大引バッチでしか作らない

前場ランで review.json を書くと、前場の途中の値が「その日の確定レビュー」の顔をして
半日公開されてしまう。pipeline 側で is_snapshot のときは呼ばない。

## 前場が「無かった」判定は時計でなくファイルの日付で行う

前場バッチの cron は高負荷時にドロップし得る(だから 11:35 と 12:05 の2回投げている)。
report_maezyou.json はドロップした日も前日のものが残っているので、**ファイルの存在で
判定すると前日の断面を今日の前場として扱ってしまう**。generated_at の日付が当日で
なければ「前場は無かった」とみなし、has_maezyou を false にして前場列を丸ごと落とす。
断面切替(docs/assets/app.js の resolveDefaultSnapshot)と同じ考え方。

## 履歴 data/history/review.jsonl

行は毎日貯める。銘柄コードの集合だけを焼き込んでおけば、後から振り返って
「前場のブレイクが引けまで残る割合」が出せる。名前や価格は report 側にあるので
入れない(1日1行が何百日も git 差分に積まれるため)。

この履歴を読んで基準線を出すのが build_baseline。「今日だまし3件」だけでは多いのか
少ないのか分からないので、同じ数え方の過去の値を横に並べる。運用初日から数週間は
行が貯まっていないので基準線は出ない(それが正常)。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from src.config import REPO_ROOT
from src.report.stage_log import classify_bucket

JST = timezone(timedelta(hours=9))

REVIEW_JSON_PATH = REPO_ROOT / "docs" / "data" / "review.json"
# 指数の日中ティックの置き場所(src/data/indices.py が書く)。import を増やさないため
# パスだけ持つ。あちらの定数と食い違わないよう、変える時は両方直すこと。
REVIEW_INTRADAY_PATH = REPO_ROOT / "data" / "history" / "indices_intraday.jsonl"
REVIEW_HISTORY_JSONL = REPO_ROOT / "data" / "history" / "review.jsonl"
REVIEW_HISTORY_KEY = ("date",)
# 「前場のブレイクが引けまで残る率」を四半期スケールで見たいので長めに持つ。
# 1日1行なので長期保存してもファイルは小さい。
REVIEW_HISTORY_KEEP_DAYS = 400
REVIEW_HISTORY_MAX_LINES = 800

# ブレイク扱いにする status。弱いブレイクも「前場は上に抜けていた」ことに変わりは
# ないので含める(だまし判定の母集団を狭めすぎない)。
BREAKOUT_STATUSES = ("BREAKOUT", "BREAKOUT_WEAK")

# 監視バケットの日本語ラベル。docs/assets/app.js の REVIEW_BUCKET_LABEL と同じ語に
# 揃える(同じものを2通りの言い方で出すと読む側が別物だと思うため)。
BUCKET_LABELS = {
    "order": "発注",
    "watch": "監視",
    "cooled": "追撃禁止",
    "near": "あと一歩",
    "forming": "形成中",
    "fresh_high": "新高値直後",
    "rejected": "未達",
    "inactive": "対象外",
    "unknown": "不明",
}
# 画面に出す順。件数0のバケットも列は立てる(集計されなかったのか本当に0なのかを
# 区別できるようにするため。stage_log の funnel と同じ方針)。
BUCKET_ORDER = ("order", "watch", "near", "forming", "fresh_high", "cooled", "rejected", "inactive")

# 地合いスコアの内訳キー→日本語。app.js の REVIEW_BREAKDOWN_LABEL と同じ語。
BREAKDOWN_LABELS = {
    "breadth": "値上がり銘柄の広がり",
    "index_trend": "指数の向き",
    "momentum": "勢い",
    "risk_appetite": "リスクの取りやすさ",
}

# ---- 後場の失速/伸長判定のしきい値 ----------------------------------------
# 前場と大引で「増えた/減った」と言うために必要な最小の変化幅。ここを0にすると
# 1銘柄の増減でも「伸長」になってしまい、毎日どちらかに振れて意味を持たなくなる。
AFTERNOON_MIN_COUNT_DIFF = 30      # 値上がり/値下がり/新高値/新安値の件数
AFTERNOON_MIN_PCT_DIFF = 0.5       # 移動平均線を上回る銘柄の比率(%ポイント)
AFTERNOON_MIN_SCORE_DIFF = 2       # 地合いスコア(pt)
# 上の各項目を +1 / -1 で数えた合計がこの値以上なら「伸長」、マイナス側なら「失速」。
AFTERNOON_VERDICT_THRESHOLD = 2

# ---- 一日の値動きの形(market.shape)のしきい値 ----------------------------
# 指数の日中ティック(data/history/indices_intraday.jsonl)から形を分類する。
SHAPE_INDEX_KEY = "topix"          # 東証全体の地合いなので TOPIX を基準にする
SHAPE_MIN_TICKS = 6                # 15分間隔なので6本 = 1時間半。これ未満は形を語らない
SHAPE_SESSION_START = time(9, 0)   # 東証のザラ場だけを見る(この cron は米国時間にも走る)
SHAPE_SESSION_END = time(15, 30)
SHAPE_MORNING_END = time(11, 30)   # 前場の終わり
SHAPE_FLAT_RANGE_PCT = 0.5         # 一日の高安の幅がこれ未満なら形を語らず「もみ合い」
SHAPE_EDGE_LOW = 0.35              # 終値が高安レンジの下から35%以内なら「安値圏で引けた」
SHAPE_EDGE_HIGH = 0.65             # 上から35%以内(=0.65以上)なら「高値圏で引けた」

# 終値が日中のどのあたりで引けたかを一言で添えるしきい値(個別銘柄用)。
STOCK_EDGE_LOW = 0.25
STOCK_EDGE_HIGH = 0.75

# 銘柄リストが長い日に JSON が膨らむのを防ぐ上限。画面側は5件で畳むので、
# 「もっと見る」で開いて意味がある範囲に留める。
MAX_STOCK_ROWS = 20

# 決算発表が近いブレイクを注意喚起する窓(暦日)。0 = 発表当日。5日にしたのは
# 「今から入って発表を跨ぐか」が判断の分かれ目になる範囲だから。あくまで注意喚起で、
# ここに載った銘柄を候補から外すことはしない(決算跨ぎは別のゲームというだけ)。
EARNINGS_SOON_MAX_DAYS = 5

# 「前場のブレイクが引けまで残る率」の基準線を出す集計窓(営業日)。短すぎると
# 数銘柄の増減で割合が跳ね、長すぎると相場つきが変わった後も古い地合いを引きずる。
BASELINE_WINDOW_DAYS = 20
# 期間通算の件数がこれに届かないうちは割合を言い切らない(画面は「参考値」と出す)。
# 20件で「60%」と書くと、実体は12/20で1件ずれるだけで5ポイント動く。
BASELINE_MIN_SAMPLE = 30


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------

def _num(value):
    """数値なら float/int で返し、そうでなければ None。

    docs/data の JSON は欠損を None で持つ場所と、そもそもキーごと無い場所が
    混在している(バッチの世代差)。呼び出し側で毎回 try するのを避ける。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if number.is_integer() else number


def _median(values: list) -> float | None:
    nums = sorted(v for v in (_num(x) for x in values) if v is not None)
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return round(float(nums[mid]), 2)
    return round((nums[mid - 1] + nums[mid]) / 2.0, 2)


def _entry_for_date(breadth: dict | None, date_str: str) -> dict | None:
    """breadth.json の history から指定日のエントリを取る。"""
    for row in reversed((breadth or {}).get("history") or []):
        if row.get("date") == date_str:
            return row
    return None


def _entry_before_date(breadth: dict | None, date_str: str) -> dict | None:
    """指定日より前の直近エントリ(=前日大引)。日付は文字列比較で足りる(ISO)。"""
    candidates = [r for r in ((breadth or {}).get("history") or []) if (r.get("date") or "") < date_str]
    return candidates[-1] if candidates else None


def _stocks_by_code(report: dict | None) -> dict:
    return {s.get("code"): s for s in ((report or {}).get("stocks") or []) if s.get("code")}


def _generated_date(report: dict | None) -> str | None:
    """report の generated_at を JST の日付文字列にする。タイムゾーン無しはJST扱い。"""
    raw = (report or {}).get("generated_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST).date().isoformat()


def _range_position(stock: dict) -> float | None:
    """終値が日中の高安レンジのどこで引けたか。0=安値、1=高値。

    始値・高値・安値は 2026-07-31 に銘柄レコードへ足したばかりなので、それ以前に
    作られた断面には入っていない。無ければ None を返して一言を省く。
    """
    high = _num(stock.get("high"))
    low = _num(stock.get("low"))
    close = _num(stock.get("close"))
    if high is None or low is None or close is None or high <= low:
        return None
    return (close - low) / (high - low)


def _stock_note(stock: dict) -> str:
    pos = _range_position(stock)
    if pos is None:
        return ""
    if pos <= STOCK_EDGE_LOW:
        return "終値は日中の安値圏"
    if pos >= STOCK_EDGE_HIGH:
        return "終値は日中の高値圏"
    return ""


def _stock_row(stock: dict) -> dict:
    return {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "close": _num(stock.get("close")),
        "pivot": _num(stock.get("pivot")),
        "note": _stock_note(stock),
    }


def _by_score(stock: dict):
    """スコアの高い順。同点は銘柄コード順(実行ごとに並びが変わらないように)。"""
    score = _num(stock.get("total_score"))
    return (-(score if score is not None else -1), str(stock.get("code") or ""))


# ---------------------------------------------------------------------------
# 1) その日の地合いの動き
# ---------------------------------------------------------------------------

def build_market_block(prev_entry, maezyou_entry, close_entry, indices, maezyou_indices) -> dict:
    """前日大引→前場→大引 の3点で地合いがどう動いたかをまとめる。"""
    def pick(entry, key):
        return _num((entry or {}).get(key)) if entry else None

    score = {
        "prev_close": pick(prev_entry, "market_score"),
        "maezyou": pick(maezyou_entry, "market_score"),
        "close": pick(close_entry, "market_score"),
    }
    signal = {
        "prev_close": (prev_entry or {}).get("signal") if prev_entry else None,
        "maezyou": (maezyou_entry or {}).get("signal") if maezyou_entry else None,
        "close": (close_entry or {}).get("signal") if close_entry else None,
    }

    # 内訳の増減は「前日大引 → 当日大引」で取る。前場との比較は後場ブロックの仕事で、
    # ここは一日を通してどの要素が地合いを動かしたかを見る場所。
    prev_bd = (prev_entry or {}).get("score_breakdown") or {}
    close_bd = (close_entry or {}).get("score_breakdown") or {}
    breakdown_delta = {}
    for key in set(prev_bd) | set(close_bd):
        before, after = _num(prev_bd.get(key)), _num(close_bd.get(key))
        if before is None or after is None:
            continue
        breakdown_delta[key] = round(after - before, 1)

    drivers = _build_drivers(breakdown_delta, prev_entry, close_entry)

    block = {
        "score": {k: v for k, v in score.items() if v is not None},
        "signal": {k: v for k, v in signal.items() if v},
        "breakdown_delta": breakdown_delta,
        "drivers": drivers,
    }
    moves = _build_index_moves(indices, maezyou_indices)
    if moves:
        block["index_moves"] = moves
    return block


def _build_drivers(breakdown_delta: dict, prev_entry, close_entry) -> list[str]:
    """地合いを動かした要素を、内訳の増減が大きい順に日本語で2つまで。

    「値上がり銘柄の広がりが4pt低下」だけでは抽象的なので、可能なら実際の数字
    (200日線を上回る銘柄の比率、新高値の件数)を括弧で添える。
    """
    moved = sorted(
        ((k, v) for k, v in breakdown_delta.items() if v is not None and abs(v) >= 1),
        key=lambda kv: -abs(kv[1]),
    )[:2]
    lines = []
    for key, delta in moved:
        label = BREAKDOWN_LABELS.get(key, key)
        direction = "上昇" if delta > 0 else "低下"
        detail = _driver_detail(key, prev_entry, close_entry)
        lines.append(f"{label}が{abs(delta):g}pt{direction}{detail}")
    if not lines:
        lines.append("地合いスコアの内訳に目立った変化はなし")
    return lines


def _driver_detail(key: str, prev_entry, close_entry) -> str:
    def pair(field):
        before, after = _num((prev_entry or {}).get(field)), _num((close_entry or {}).get(field))
        return (before, after) if before is not None and after is not None else (None, None)

    if key == "breadth":
        before, after = pair("pct_above_ma200")
        if before is not None:
            # breadth.json の pct_above_ma200 は 0〜1 の割合。画面は%表記なので100倍する。
            return f"(200日線を上回る銘柄が{before * 100:.1f}%→{after * 100:.1f}%)"
    if key == "momentum":
        before, after = pair("new_high_count")
        if before is not None:
            return f"(新高値が{before:g}件→{after:g}件)"
    return ""


def _build_index_moves(indices, maezyou_indices) -> list[dict]:
    """主要指数の前場時点と大引の騰落率。前場断面が無ければ大引だけ入る。"""
    def by_key(payload):
        return {e.get("key"): e for e in ((payload or {}).get("indices") or []) if e.get("key")}

    close_map, maezyou_map = by_key(indices), by_key(maezyou_indices)
    moves = []
    for key, entry in close_map.items():
        row = {
            "key": key,
            "name": entry.get("name") or key,
            "close_pct": _num(entry.get("change_pct")),
        }
        mz = maezyou_map.get(key)
        if mz is not None:
            row["maezyou_pct"] = _num(mz.get("change_pct"))
        if row["close_pct"] is not None or row.get("maezyou_pct") is not None:
            moves.append(row)
    return moves


# ---------------------------------------------------------------------------
# 2) 一日の値動きの形(指数の日中ティックから)
# ---------------------------------------------------------------------------

def classify_shape(ticks: list[dict], date_str: str, index_key: str = SHAPE_INDEX_KEY) -> dict:
    """指数の日中ティックから「寄り天」「後場に切り返し」などの形を判定する。

    ticks は data/history/indices_intraday.jsonl の行の並び
    ({"ts": ISO8601(JST), "date": "YYYY-MM-DD", "values": {"topix": 2850.1, ...}})。
    このワークフローは米国市場の時間帯にも走るので、東証のザラ場(9:00〜15:30)の
    行だけに絞ってから見る。

    判定は素朴でよい。ここで欲しいのは「一日をどう総括するか」の一言であって、
    テクニカル分析ではない。運用初日は行が貯まっていないので available=false で
    返る(それが正常)。
    """
    points = []
    for row in ticks or []:
        if row.get("date") != date_str:
            continue
        value = _num((row.get("values") or {}).get(index_key))
        if value is None:
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("ts")))
        except (TypeError, ValueError):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=JST)
        local = stamp.astimezone(JST).time()
        if local < SHAPE_SESSION_START or local > SHAPE_SESSION_END:
            continue
        points.append((local, value))

    if len(points) < SHAPE_MIN_TICKS:
        return {"available": False}

    points.sort(key=lambda p: p[0])
    open_v, close_v = points[0][1], points[-1][1]
    high_t, high_v = max(points, key=lambda p: p[1])
    low_t, low_v = min(points, key=lambda p: p[1])
    if not open_v or high_v <= low_v:
        return {"available": False}

    range_pct = (high_v - low_v) / open_v * 100.0
    position = (close_v - low_v) / (high_v - low_v)
    change_pct = (close_v / open_v - 1.0) * 100.0
    name = index_key.upper() if index_key == "topix" else index_key

    if range_pct < SHAPE_FLAT_RANGE_PCT:
        label = "もみ合い"
        detail = f"{name}は一日の値幅が{range_pct:.1f}%と狭く、方向感が出なかった"
    elif high_t <= SHAPE_MORNING_END and position <= SHAPE_EDGE_LOW:
        label = "寄り天"
        drop = (high_v - close_v) / high_v * 100.0
        detail = f"{name}は前場につけた高値から大引にかけて{drop:.1f}%下げた"
    elif low_t <= SHAPE_MORNING_END and position >= SHAPE_EDGE_HIGH:
        label = "後場に切り返し"
        rebound = (close_v - low_v) / low_v * 100.0
        detail = f"{name}は前場の安値から{rebound:.1f}%戻して引けた"
    elif position >= SHAPE_EDGE_HIGH and change_pct > 0:
        label = "じり高"
        detail = f"{name}は高値圏で引け、寄り付きから{change_pct:.1f}%上げた"
    elif position <= SHAPE_EDGE_LOW and change_pct < 0:
        label = "じり安"
        detail = f"{name}は安値圏で引け、寄り付きから{abs(change_pct):.1f}%下げた"
    else:
        label = "もみ合い"
        detail = f"{name}は高安{range_pct:.1f}%の範囲で上下し、引けは中ほど"

    return {"available": True, "label": label, "detail": detail}


# ---------------------------------------------------------------------------
# 3) 後場の失速・伸長
# ---------------------------------------------------------------------------

# 前場と大引で比べる項目。(内部キー, 画面のラベル, 単位, 増えたら地合いに対して
# プラスか, 「増えた/減った」と言うのに必要な最小差, 表示倍率)。
# 表示倍率が 100 なのは breadth.json の 200日線/50日線を上回る銘柄が 0〜1 の割合で
# 入っているため。ここで%に直しておかないと画面に「0.5%」と出るし、最小差の判定も
# 一生ひっかからない。
AFTERNOON_METRICS = (
    ("advancers", "値上がり銘柄数", "", True, AFTERNOON_MIN_COUNT_DIFF, 1),
    ("decliners", "値下がり銘柄数", "", False, AFTERNOON_MIN_COUNT_DIFF, 1),
    ("new_high_count", "新高値", "件", True, AFTERNOON_MIN_COUNT_DIFF, 1),
    ("new_low_count", "新安値", "件", False, AFTERNOON_MIN_COUNT_DIFF, 1),
    ("pct_above_ma200", "200日線を上回る銘柄", "%", True, AFTERNOON_MIN_PCT_DIFF, 100),
    ("pct_above_ma50", "50日線を上回る銘柄", "%", True, AFTERNOON_MIN_PCT_DIFF, 100),
    ("market_score", "地合いスコア", "pt", True, AFTERNOON_MIN_SCORE_DIFF, 1),
)


def build_afternoon_block(maezyou_entry: dict, close_entry: dict, sector_note: str = "") -> dict:
    """前場と大引を比べて、後場に失速したのか伸びたのかを判定する。

    1項目だけを見ると値上がり銘柄数の増減に振り回されるので、複数項目を
    +1/-1 で数えた合計で決める。合計がしきい値に届かない日は素直に「横ばい」。
    """
    metrics, tally, movers = [], 0, []
    for key, label, unit, higher_is_better, min_diff, scale in AFTERNOON_METRICS:
        before, after = _num((maezyou_entry or {}).get(key)), _num((close_entry or {}).get(key))
        if before is None or after is None:
            continue
        if scale != 1:
            before, after = round(before * scale, 2), round(after * scale, 2)
        metrics.append({"label": label, "maezyou": before, "close": after, "unit": unit})
        diff = after - before
        if abs(diff) < min_diff:
            continue
        tally += 1 if (diff > 0) == higher_is_better else -1
        movers.append((abs(diff), label, before, after, diff))

    if not metrics:
        return {}

    if tally >= AFTERNOON_VERDICT_THRESHOLD:
        verdict = "伸長"
    elif tally <= -AFTERNOON_VERDICT_THRESHOLD:
        verdict = "失速"
    elif metrics:
        verdict = "横ばい"
    else:
        verdict = "不明"

    reasons = []
    for _, label, before, after, diff in sorted(movers, key=lambda m: -m[0])[:3]:
        verb = "増加" if diff > 0 else "減少"
        reasons.append(f"{label}が{before:g}から{after:g}へ{verb}")
    if sector_note:
        reasons.append(sector_note)
    if not reasons:
        reasons.append("前場から大引まで目立った変化はなし")

    return {"verdict": verdict, "reasons": reasons, "metrics": metrics}


def build_sector_note(maezyou_heatmap, close_heatmap) -> str:
    """前場から大引にかけて最も崩れた(または伸びた)セクターを一言で。

    セクターは銘柄選びの前提になるので、地合い全体が横ばいでも「どこが息切れしたか」
    が分かると翌日の見方が変わる。差が小さい日は何も言わない。
    """
    def by_sector(payload):
        return {
            s.get("sector"): _num((s.get("returns") or {}).get("d1"))
            for s in ((payload or {}).get("sectors") or [])
            if s.get("sector")
        }

    before_map, after_map = by_sector(maezyou_heatmap), by_sector(close_heatmap)
    diffs = [
        (after_map[k] - before_map[k], k, before_map[k], after_map[k])
        for k in before_map
        if k in after_map and before_map[k] is not None and after_map[k] is not None
    ]
    if not diffs:
        return ""
    diff, sector, before, after = min(diffs, key=lambda d: d[0])
    gain = max(diffs, key=lambda d: d[0])
    if abs(diff) >= abs(gain[0]) and diff <= -0.5:
        return f"{sector}が前場{before:+.1f}%から大引{after:+.1f}%へ失速"
    if gain[0] >= 0.5:
        return f"{gain[1]}が前場{gain[2]:+.1f}%から大引{gain[3]:+.1f}%へ伸長"
    return ""


# ---------------------------------------------------------------------------
# 4) 銘柄レベルの遷移
# ---------------------------------------------------------------------------

def build_stocks_block(
    maezyou_stocks: dict,
    close_stocks: dict,
    history_rows: list[dict] | None = None,
    date_str: str = "",
) -> dict:
    """前場と大引で銘柄の状態がどう変わったかを4つのリストに分ける。

    - だまし        前場はブレイクしていたのに大引では抜けを維持できなかった
    - 引け際のブレイク 前場は抜けていなかったのに大引でブレイクした
    - 新しく候補入り  発注できる状態(ピボットあり)に大引で新たに入った
    - 候補から外れた  前場は発注できたのに大引で外れた

    あわせて「決算が近いブレイク」と、過去の履歴から出した維持率の基準線を添える。

    history_rows は review.jsonl の行(当日より前)。ファイルは読まない —
    読むのは update_review 側の仕事(_load_intraday_ticks と同じ作り)。

    前場断面が無い日は空の辞書を返す(比較対象が無いので何も言えない)。
    """
    if not maezyou_stocks:
        return {}

    def breakout_codes(stocks: dict) -> set:
        return {c for c, s in stocks.items() if s.get("status") in BREAKOUT_STATUSES}

    def order_codes(stocks: dict) -> set:
        return {c for c, s in stocks.items() if classify_bucket(s) == "order"}

    mz_break, close_break = breakout_codes(maezyou_stocks), breakout_codes(close_stocks)
    mz_order, close_order = order_codes(maezyou_stocks), order_codes(close_stocks)

    def rows(codes, source):
        picked = [source[c] for c in codes if c in source]
        picked.sort(key=_by_score)
        return [_stock_row(s) for s in picked[:MAX_STOCK_ROWS]]

    codes = {
        "maezyou_breakout": sorted(mz_break),
        "close_breakout": sorted(close_break),
        "held": sorted(mz_break & close_break),
        "fakeout": sorted(mz_break - close_break),
        "late_breakout": sorted(close_break - mz_break),
    }

    block = {
        "counts": {
            "maezyou_breakout": len(mz_break),
            "close_breakout": len(close_break),
            "held": len(mz_break & close_break),
        },
        "fakeout": rows(mz_break - close_break, close_stocks),
        "late_breakout": rows(close_break - mz_break, close_stocks),
        "new_candidates": rows(close_order - mz_order, close_stocks),
        "dropped": rows(mz_order - close_order, close_stocks),
        # 履歴用(画面には出さない)。だましの実測率を後日出すための素の集合。
        "_codes": codes,
    }

    earnings_soon = _earnings_soon_rows(close_break | close_order, close_stocks)
    if earnings_soon:
        block["earnings_soon"] = earnings_soon

    baseline = build_baseline(history_rows or [], codes, date_str)
    if baseline:
        block["baseline"] = baseline
    return block


def _earnings_soon_rows(codes: set, close_stocks: dict) -> list[dict]:
    """大引時点でブレイクしている銘柄・発注候補のうち、決算発表が目前のもの。

    銘柄レコードが持っている残り日数(build_site が生成時に確定させたもの)を
    そのまま使う。ここで日付から引き算し直すと、レビューを作った時刻を基準に
    数えることになってレコードの値とずれる。

    注意喚起であって除外ではない。候補から外す処理はここにも他所にも入れていない。
    """
    picked = []
    for code in codes:
        stock = close_stocks.get(code) or {}
        days = _num(stock.get("days_to_earnings"))
        if days is None or days < 0 or days > EARNINGS_SOON_MAX_DAYS:
            continue
        picked.append({
            "code": code,
            "name": stock.get("name"),
            "days_to_earnings": int(days),
            "next_earnings_date": stock.get("next_earnings_date"),
        })
    # 近い順。同日は銘柄コード順(実行ごとに並びが変わらないように)。
    picked.sort(key=lambda r: (r["days_to_earnings"], str(r["code"])))
    return picked[:MAX_STOCK_ROWS]


def build_baseline(history_rows: list[dict], today_row: dict | None, date_str: str) -> dict:
    """前場のブレイクが引けまで残った割合を、過去の記録から出す。

    「今日だまし3件」だけでは多いのか少ないのか分からない。同じ数え方の過去の値を
    横に並べて初めて意味を持つので、review.jsonl に焼き込んである前場ブレイクと
    引けまで維持したコードの集合を通算して基準線にする。

    - **今日の行は入れない。** 今日の行はこれから書くものだし、自分自身と比べても
      基準にならない。date が当日より前の行だけを使う。
    - **前場が無かった日は分母から外す。** 前場バッチが落ちた日は前場ブレイクが
      0件として残るので、そのまま数えると維持率0%の日を毎回混ぜることになる。
    - 履歴が空なら空の辞書を返す(このファイルは素材が欠けたブロックを黙って落とす)。

    reliable は「割合を言い切ってよいか」の目印。件数が足りないのに 62% と書くと、
    1件ずれるだけで数字が動くことが読む側に伝わらない。
    """
    usable = []
    for row in history_rows or []:
        day = str(row.get("date") or "")
        if not day or (date_str and day >= date_str):
            continue
        if not row.get("has_maezyou"):
            continue
        usable.append((day, len(row.get("maezyou_breakout") or []), len(row.get("held") or [])))

    if not usable:
        return {}
    usable.sort(key=lambda r: r[0])
    usable = usable[-BASELINE_WINDOW_DAYS:]

    sample = sum(breakout for _, breakout, _ in usable)
    held = sum(kept for _, _, kept in usable)
    if sample <= 0:
        # 前場はあったが一度もブレイクが出ていない期間。割る相手が無いので黙る。
        return {}

    today_breakout = len((today_row or {}).get("maezyou_breakout") or [])
    today_held = len((today_row or {}).get("held") or [])
    return {
        "days": len(usable),
        "sample": sample,
        "held_rate": round(held / sample, 3),
        # 今日の前場ブレイクが0件の日は割合そのものが存在しない(0%ではない)。
        "today_held_rate": round(today_held / today_breakout, 3) if today_breakout else None,
        "reliable": sample >= BASELINE_MIN_SAMPLE,
    }


# ---------------------------------------------------------------------------
# 5) 監視バケットと実績
# ---------------------------------------------------------------------------

def build_buckets_block(prev_entry, maezyou_entry, close_entry, maezyou_stocks, close_stocks) -> dict:
    """監視バケットの件数推移と、ピボットまでの距離・ブレイク成功率。"""
    def funnel(entry):
        return (entry or {}).get("stage_funnel") or {}

    prev_f, mz_f, close_f = funnel(prev_entry), funnel(maezyou_entry), funnel(close_entry)
    rows = []
    for key in BUCKET_ORDER:
        row = {"label": BUCKET_LABELS.get(key, key)}
        for col, source in (("prev_close", prev_f), ("maezyou", mz_f), ("close", close_f)):
            value = _num(source.get(key))
            if value is not None:
                row[col] = value
        if len(row) > 1:
            rows.append(row)

    def median_dist(stocks: dict):
        return _median([s.get("dist_to_pivot") for s in stocks.values() if s.get("pivot") is not None])

    block = {}
    if rows:
        block["stage_funnel"] = rows
    dist = {}
    if maezyou_stocks:
        value = median_dist(maezyou_stocks)
        if value is not None:
            dist["maezyou"] = value
    if close_stocks:
        value = median_dist(close_stocks)
        if value is not None:
            dist["close"] = value
    if dist:
        block["median_dist_to_pivot"] = dist
    rate = _num((close_entry or {}).get("breakout_success_rate"))
    if rate is not None:
        block["breakout_success_rate"] = rate
    return block


# ---------------------------------------------------------------------------
# 6) 前日「あと一歩」だった銘柄の答え合わせ
# ---------------------------------------------------------------------------

def build_followup(yesterday_rows: list[dict], close_stocks: dict) -> dict:
    """前日「あと一歩」だった銘柄が今日どうなったかを突き合わせる。

    前日の銘柄別スナップショット(data/history/stage.jsonl)のうちバケットが
    「あと一歩」だった行を拾い、今日のレコードと比べる。ブレイクまで行けた比率を
    hit_rate として出す。1日ぶんの母数は小さいので、当日の数字そのものより
    「毎日目に入ること」に意味がある(手応えと実測がずれていないかの確認)。

    前日ぶんが無ければ空を返す(運用初日や連休明け)。
    """
    if not yesterday_rows or not close_stocks:
        return {}

    items, hits = [], 0
    for row in yesterday_rows:
        code = row.get("code")
        today = close_stocks.get(code)
        if not today:
            continue
        bucket = classify_bucket(today)
        if today.get("status") in BREAKOUT_STATUSES:
            result = "成功"
            hits += 1
        elif bucket == "order":
            result = "前進"
        elif bucket == "near":
            result = "据え置き"
        else:
            result = "後退"
        items.append({
            "code": code,
            "name": today.get("name"),
            "yesterday": "あと一歩",
            "today": BUCKET_LABELS.get(bucket, bucket),
            "result": result,
        })

    if not items:
        return {}
    # 成功→前進→据え置き→後退の順に並べる(見たいのは上に上がった銘柄)。
    order = {"成功": 0, "前進": 1, "据え置き": 2, "後退": 3}
    items.sort(key=lambda i: (order.get(i["result"], 9), str(i["code"])))
    return {"hit_rate": round(hits / len(items), 3), "items": items[:MAX_STOCK_ROWS]}


# ---------------------------------------------------------------------------
# 組み立て(純粋関数。I/O は update_review 側)
# ---------------------------------------------------------------------------

def build_review(
    date_str: str,
    close_report: dict,
    close_breadth: dict,
    maezyou_report: dict | None = None,
    maezyou_breadth: dict | None = None,
    indices: dict | None = None,
    maezyou_indices: dict | None = None,
    close_heatmap: dict | None = None,
    maezyou_heatmap: dict | None = None,
    intraday_ticks: list[dict] | None = None,
    yesterday_near_rows: list[dict] | None = None,
    history_rows: list[dict] | None = None,
    now: datetime | None = None,
) -> dict:
    """当日の review.json の中身を組み立てる。ファイルは一切触らない。

    前場ぶんの引数は「当日の前場断面」であることを呼び出し側が保証しなくてよい。
    generated_at が当日でなければここで捨てる(前場バッチが落ちた日に前日の断面を
    今日の前場として扱わないため)。
    """
    now = now or datetime.now(JST)
    notes = []

    has_maezyou = bool(maezyou_report) and _generated_date(maezyou_report) == date_str
    if maezyou_report and not has_maezyou:
        notes.append("前場のデータが当日のものではないため、前場との比較は省略しています")
    elif not maezyou_report:
        notes.append("前場のデータが無いため、前場との比較は省略しています")

    if not has_maezyou:
        maezyou_report = maezyou_breadth = maezyou_indices = maezyou_heatmap = None

    close_stocks = _stocks_by_code(close_report)
    maezyou_stocks = _stocks_by_code(maezyou_report) if has_maezyou else {}

    prev_entry = _entry_before_date(close_breadth, date_str)
    # 当日エントリが見つからない日(日付表記のずれ等)は履歴の最後を当日とみなす。
    # ここで落ちるとレビューだけでなくバッチのログが読みにくくなるため。
    history = (close_breadth or {}).get("history") or []
    close_entry = _entry_for_date(close_breadth, date_str) or (history[-1] if history else {})
    maezyou_entry = _entry_for_date(maezyou_breadth, date_str) if has_maezyou else None

    market = build_market_block(prev_entry, maezyou_entry, close_entry, indices, maezyou_indices)
    shape = classify_shape(intraday_ticks or [], date_str)
    market["shape"] = shape
    if not shape.get("available"):
        notes.append("指数の日中の記録が足りないため、値動きの形は判定していません")

    review = {
        "generated_at": now.astimezone(JST).isoformat(timespec="seconds"),
        "date": date_str,
        "has_maezyou": has_maezyou,
        "market": market,
    }
    if has_maezyou:
        review["maezyou_generated_at"] = (maezyou_report or {}).get("generated_at")
        afternoon = build_afternoon_block(
            maezyou_entry, close_entry, build_sector_note(maezyou_heatmap, close_heatmap)
        )
        if afternoon:
            review["afternoon"] = afternoon

    stocks = build_stocks_block(maezyou_stocks, close_stocks, history_rows or [], date_str)
    codes = stocks.pop("_codes", {}) if stocks else {}
    if stocks:
        review["stocks"] = stocks

    buckets = build_buckets_block(prev_entry, maezyou_entry, close_entry, maezyou_stocks, close_stocks)
    if buckets:
        review["buckets"] = buckets

    followup = build_followup(yesterday_near_rows or [], close_stocks)
    if followup:
        review["followup"] = followup

    if notes:
        review["notes"] = notes

    # 履歴行は画面に出さないので review 本体には混ぜず、別のキーで持ち回る。
    review["_history_row"] = _history_row(date_str, market, review.get("afternoon"), has_maezyou, codes)
    return review


def _history_row(date_str, market, afternoon, has_maezyou, codes) -> dict:
    """review.jsonl の1行。

    目的は「前場にブレイクしていた銘柄が引けまで残る実測率」を後日出すこと。
    なので銘柄はコードの集合だけを持ち、名前や価格は入れない(report 側にある)。
    """
    return {
        "date": date_str,
        "has_maezyou": has_maezyou,
        "score": market.get("score") or {},
        "signal": market.get("signal") or {},
        "shape": (market.get("shape") or {}).get("label"),
        "afternoon_verdict": (afternoon or {}).get("verdict"),
        "maezyou_breakout": codes.get("maezyou_breakout") or [],
        "close_breakout": codes.get("close_breakout") or [],
        "held": codes.get("held") or [],
        "fakeout": codes.get("fakeout") or [],
        "late_breakout": codes.get("late_breakout") or [],
    }


# ---------------------------------------------------------------------------
# I/O(pipeline から呼ぶ入口)
# ---------------------------------------------------------------------------

def _load_intraday_ticks(date_str: str) -> list[dict]:
    from src.history_store import iter_records

    return [r for r in iter_records(REVIEW_INTRADAY_PATH) if r.get("date") == date_str]


def _load_review_history(date_str: str) -> list[dict]:
    """自分が過去に書いた review.jsonl から、当日より前の行を読む。

    同じ日に再実行すると行が2本以上できるので load_deduped(後勝ち)で潰す。
    行数で切らずに日付で切っているのは、集計窓を何日にするかを build_baseline 側の
    定数一本で決めたいため。
    """
    from src.history_store import load_deduped

    rows = load_deduped(REVIEW_HISTORY_JSONL, REVIEW_HISTORY_KEY)
    return [r for r in rows if str(r.get("date") or "") < date_str]


def _load_yesterday_near_rows(date_str: str) -> list[dict]:
    """data/history/stage.jsonl から「当日より前の直近営業日」の あと一歩 行を拾う。

    連休や祝日で前日が営業日とは限らないので、カレンダー上の前日ではなく
    「記録がある直近の日」を取る。祝日判定のライブラリに依存しないのが狙い。
    """
    from src.history_store import iter_records
    from src.report.stage_log import STAGE_HISTORY_JSONL

    rows_by_date: dict[str, list[dict]] = {}
    for row in iter_records(STAGE_HISTORY_JSONL):
        day = row.get("date")
        if not day or day >= date_str:
            continue
        rows_by_date.setdefault(day, []).append(row)
    if not rows_by_date:
        return []
    latest = max(rows_by_date)
    # 同日再実行で同じ銘柄が複数行ある場合は後勝ち(JSONL の読み方の作法に合わせる)。
    deduped = {r.get("code"): r for r in rows_by_date[latest]}
    return [r for r in deduped.values() if r.get("bucket") == "near"]


def update_review(date_str: str, now: datetime | None = None) -> dict | None:
    """大引バッチの最後に呼ぶ入口。review.json と review.jsonl を書く。

    読むファイルはすべて docs/data 配下(大引バッチは restore-site-data で
    gh-pages から復元してから走るので、当日の前場断面も手元にある)。
    """
    from src.history_store import append_records, compact, count_lines
    from src.report.build_site import DOCS_DATA_DIR
    from src.report.secure_io import read_docs_json, write_docs_json

    def load(name):
        return read_docs_json(DOCS_DATA_DIR / name, default=None)

    close_report = load("report.json")
    close_breadth = load("breadth.json") or {"history": []}
    if not close_report:
        print("Review: report.json が読めないので生成をスキップしました。")
        return None

    review = build_review(
        date_str,
        close_report,
        close_breadth,
        maezyou_report=load("report_maezyou.json"),
        maezyou_breadth=load("breadth_maezyou.json"),
        indices=load("indices.json"),
        maezyou_indices=load("indices_maezyou.json"),
        close_heatmap=load("heatmap.json"),
        maezyou_heatmap=load("heatmap_maezyou.json"),
        intraday_ticks=_load_intraday_ticks(date_str),
        yesterday_near_rows=_load_yesterday_near_rows(date_str),
        history_rows=_load_review_history(date_str),
        now=now,
    )

    history_row = review.pop("_history_row", None)
    write_docs_json(REVIEW_JSON_PATH, review)

    if history_row:
        append_records(REVIEW_HISTORY_JSONL, [history_row])
        # 重複が溜まってきた時だけ間引く(毎回やると追記専用の利点が消える)。
        if count_lines(REVIEW_HISTORY_JSONL) > REVIEW_HISTORY_MAX_LINES:
            removed = compact(
                REVIEW_HISTORY_JSONL,
                REVIEW_HISTORY_KEY,
                keep_days=REVIEW_HISTORY_KEEP_DAYS,
                today=date_str,
            )
            print(f"review history: compaction で {removed} 行を削減")
    return review
