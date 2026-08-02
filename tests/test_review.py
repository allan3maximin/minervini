"""日次レビュー(src/report/review.py)の組み立てテスト。

ファイルI/Oを伴う update_review ではなく純粋関数の build_review を叩く。狙いは
「前場バッチが落ちた日に前日の断面を今日の前場として扱わないこと」と
「素材が欠けたブロックを黙って落とすこと」で、ここを外すと画面に前日の数字が
当日の顔をして出る。
"""
from datetime import datetime, timedelta, timezone

from src.report.review import (
    build_afternoon_block,
    build_baseline,
    build_followup,
    build_review,
    build_sector_note,
    build_stocks_block,
    classify_shape,
)

JST = timezone(timedelta(hours=9))
TODAY = "2026-07-30"
YESTERDAY = "2026-07-29"
NOW = datetime(2026, 7, 30, 16, 22, tzinfo=JST)


def _stock(code, name, status, close=1000, pivot=990, score=70, near=False,
           high=None, low=None, dist=1.0, days_to_earnings=None,
           next_earnings_date=None):
    return {
        "code": code, "name": name, "status": status, "close": close,
        "pivot": pivot, "total_score": score, "dist_to_pivot": dist,
        "high": high if high is not None else close * 1.02,
        "low": low if low is not None else close * 0.98,
        "open": close,
        "setup_stage": {"stage": "forming", "near": near},
        "days_to_earnings": days_to_earnings,
        "next_earnings_date": next_earnings_date,
    }


def _breadth_entry(date, score, signal, advancers, new_high, ma200, funnel=None):
    """ma200 は%表記(48.4 = 48.4%)で渡す。breadth.json 側は 0〜1 の割合で持って
    いるので、ここで割ってから詰める(表示倍率の扱いを取り違えないため)。"""
    return {
        "date": date,
        "market_score": score,
        "signal": signal,
        "score_breakdown": {"breadth": 20, "index_trend": 15, "momentum": 12, "risk_appetite": 10},
        "advancers": advancers,
        "decliners": 3800 - advancers,
        "new_high_count": new_high,
        "new_low_count": 20,
        "pct_above_ma200": round(ma200 / 100, 4),
        "pct_above_ma50": round((ma200 + 3) / 100, 4),
        "breakout_success_rate": 0.55,
        "stage_funnel": funnel or {"order": 10, "watch": 5, "near": 24, "forming": 40},
    }


def _close_report(generated=TODAY):
    return {
        "generated_at": f"{generated}T16:22:10+09:00",
        "stocks": [
            # 前場はブレイクしていたが大引で押し戻された(だまし)。安値引け。
            _stock("7203", "トヨタ", "WATCH_A", close=2810, pivot=2795, high=2900, low=2800),
            # 前場は監視、大引でブレイク(引け際のブレイク)。
            _stock("6146", "ディスコ", "BREAKOUT", close=41200, pivot=40800, high=41300, low=40000),
            # 前場も大引もブレイクを維持。
            _stock("4478", "フリー", "BREAKOUT", close=3120, pivot=3050),
            # 前日「あと一歩」で今日もあと一歩のまま。
            _stock("3697", "SHIFT", None, close=2000, pivot=None, near=True),
        ],
    }


def _maezyou_report(generated=TODAY):
    return {
        "generated_at": f"{generated}T11:41:02+09:00",
        "stocks": [
            _stock("7203", "トヨタ", "BREAKOUT", close=2890, pivot=2795),
            _stock("6146", "ディスコ", "WATCH_A", close=40500, pivot=40800),
            _stock("4478", "フリー", "BREAKOUT", close=3100, pivot=3050),
            _stock("3697", "SHIFT", None, close=1980, pivot=None, near=True),
        ],
    }


def _close_breadth():
    return {"history": [
        _breadth_entry(YESTERDAY, 62, "GREEN", 2100, 62, 52.0),
        _breadth_entry(TODAY, 55, "AMBER", 940, 41, 48.4,
                       funnel={"order": 8, "watch": 6, "near": 27, "forming": 42}),
    ]}


def _maezyou_breadth():
    return {"history": [
        _breadth_entry(YESTERDAY, 62, "GREEN", 2100, 62, 52.0),
        _breadth_entry(TODAY, 58, "AMBER", 1180, 62, 51.2,
                       funnel={"order": 12, "watch": 4, "near": 31, "forming": 41}),
    ]}


def _ticks(values):
    """(時刻文字列, TOPIXの値) の並びを日中ティックの行に変換する。"""
    return [
        {"ts": f"{TODAY}T{hhmm}:00+09:00", "date": TODAY, "values": {"topix": v}}
        for hhmm, v in values
    ]


# ---------------------------------------------------------------------------
# (a) 通常日: 前場も大引も揃っている
# ---------------------------------------------------------------------------

def test_normal_day_has_all_blocks():
    review = build_review(
        TODAY, _close_report(), _close_breadth(),
        maezyou_report=_maezyou_report(), maezyou_breadth=_maezyou_breadth(),
        intraday_ticks=_ticks([("09:00", 2850), ("10:00", 2870), ("11:00", 2875),
                               ("12:30", 2860), ("14:00", 2845), ("15:15", 2840)]),
        yesterday_near_rows=[{"code": "3697", "bucket": "near", "date": YESTERDAY}],
        now=NOW,
    )

    assert review["has_maezyou"] is True
    assert review["date"] == TODAY
    # 前日大引→前場→大引 の3点が揃う。
    assert review["market"]["score"] == {"prev_close": 62, "maezyou": 58, "close": 55}
    assert review["market"]["signal"]["close"] == "AMBER"

    # 値上がり銘柄も新高値も200日線上回り率も減っているので「失速」。
    assert review["afternoon"]["verdict"] == "失速"
    assert any("値上がり銘柄数" in r for r in review["afternoon"]["reasons"])

    stocks = review["stocks"]
    assert stocks["counts"] == {"maezyou_breakout": 2, "close_breakout": 2, "held": 1}
    assert [s["code"] for s in stocks["fakeout"]] == ["7203"]
    assert [s["code"] for s in stocks["late_breakout"]] == ["6146"]
    # 始値・高値・安値が入っていれば引けの位置を一言添える。
    assert stocks["fakeout"][0]["note"] == "終値は日中の安値圏"

    funnel = {row["label"]: row for row in review["buckets"]["stage_funnel"]}
    assert funnel["あと一歩"] == {"label": "あと一歩", "prev_close": 24, "maezyou": 31, "close": 27}
    assert review["buckets"]["breakout_success_rate"] == 0.55

    assert review["followup"]["items"][0]["code"] == "3697"
    assert review["followup"]["hit_rate"] == 0.0

    # 履歴行にはコードの集合が焼き込まれる(だましの実測率を後日出すため)。
    row = review["_history_row"]
    assert row["maezyou_breakout"] == ["4478", "7203"]
    assert row["held"] == ["4478"]
    assert row["fakeout"] == ["7203"]
    assert row["afternoon_verdict"] == "失速"


# ---------------------------------------------------------------------------
# (b) 前場バッチが落ちた日: 前場ファイルが無い
# ---------------------------------------------------------------------------

def test_missing_maezyou_falls_back_to_close_only():
    review = build_review(TODAY, _close_report(), _close_breadth(), now=NOW)

    assert review["has_maezyou"] is False
    assert "afternoon" not in review
    assert "stocks" not in review          # 比較対象が無いので銘柄の遷移は出さない
    assert "maezyou" not in review["market"]["score"]
    assert review["market"]["score"]["close"] == 55
    assert any("前場のデータが無い" in n for n in review["notes"])
    # 大引だけでも監視バケットは出る。
    assert "stage_funnel" in review["buckets"]


# ---------------------------------------------------------------------------
# (c) 日中ティックが無い日
# ---------------------------------------------------------------------------

def test_missing_intraday_ticks_marks_shape_unavailable():
    review = build_review(
        TODAY, _close_report(), _close_breadth(),
        maezyou_report=_maezyou_report(), maezyou_breadth=_maezyou_breadth(), now=NOW,
    )
    assert review["market"]["shape"] == {"available": False}
    assert any("値動きの形" in n for n in review["notes"])


# ---------------------------------------------------------------------------
# (d) 前場ファイルが前日のものだった日(cron ドロップ)
# ---------------------------------------------------------------------------

def test_stale_maezyou_is_treated_as_absent():
    """前場バッチの cron はドロップし得る。ファイルが在ることを根拠にすると
    前日の断面を今日の前場として表示してしまう。generated_at で弾くこと。"""
    review = build_review(
        TODAY, _close_report(), _close_breadth(),
        maezyou_report=_maezyou_report(generated=YESTERDAY),
        maezyou_breadth=_maezyou_breadth(), now=NOW,
    )
    assert review["has_maezyou"] is False
    assert "afternoon" not in review
    assert "stocks" not in review
    assert any("当日のものではない" in n for n in review["notes"])


# ---------------------------------------------------------------------------
# 値動きの形の分類
# ---------------------------------------------------------------------------

def test_shape_detects_morning_top():
    shape = classify_shape(
        _ticks([("09:00", 2850), ("10:00", 2880), ("11:00", 2890),
                ("12:30", 2870), ("14:00", 2850), ("15:15", 2845)]),
        TODAY,
    )
    assert shape["available"] is True
    assert shape["label"] == "寄り天"


def test_shape_detects_afternoon_rebound():
    shape = classify_shape(
        _ticks([("09:00", 2880), ("10:00", 2850), ("11:00", 2845),
                ("12:30", 2870), ("14:00", 2890), ("15:15", 2895)]),
        TODAY,
    )
    assert shape["label"] == "後場に切り返し"


def test_shape_ignores_ticks_outside_the_tokyo_session():
    """このワークフローは米国市場の時間帯にも走る。夜間の行を混ぜてはいけない。"""
    ticks = _ticks([("09:00", 2850), ("10:00", 2860), ("15:15", 2855)])
    ticks += [
        {"ts": f"{TODAY}T{h}:00:00+09:00", "date": TODAY, "values": {"topix": 3000}}
        for h in ("22", "23")
    ]
    assert classify_shape(ticks, TODAY) == {"available": False}


def test_shape_needs_a_different_day_to_be_ignored():
    assert classify_shape(_ticks([("09:00", 2850)]), "2026-07-29") == {"available": False}


# ---------------------------------------------------------------------------
# 後場判定・セクター・答え合わせの細部
# ---------------------------------------------------------------------------

def test_afternoon_small_moves_are_flat():
    """1〜2銘柄の増減で「伸長」「失速」を名乗らないこと。"""
    before = _breadth_entry(TODAY, 55, "AMBER", 1000, 40, 50.0)
    after = _breadth_entry(TODAY, 55, "AMBER", 1005, 41, 50.1)
    assert build_afternoon_block(before, after)["verdict"] == "横ばい"


def test_afternoon_pct_metrics_are_shown_in_percent():
    """breadth.json は 0〜1 の割合で持っている。画面に 0.5% と出さないこと。"""
    before = _breadth_entry(TODAY, 55, "AMBER", 1000, 40, 50.0)
    after = _breadth_entry(TODAY, 55, "AMBER", 1000, 40, 48.0)
    metrics = {m["label"]: m for m in build_afternoon_block(before, after)["metrics"]}
    assert metrics["200日線を上回る銘柄"]["maezyou"] == 50.0
    assert metrics["200日線を上回る銘柄"]["close"] == 48.0
    # 2ポイント下げているので「減った」と数えられる(割合のままだと0.02で埋もれる)。
    assert any("200日線" in r for r in build_afternoon_block(before, after)["reasons"])


def test_afternoon_growth_is_detected():
    before = _breadth_entry(TODAY, 52, "AMBER", 900, 30, 46.0)
    after = _breadth_entry(TODAY, 58, "GREEN", 1400, 75, 49.0)
    block = build_afternoon_block(before, after)
    assert block["verdict"] == "伸長"
    assert block["metrics"][0]["label"] == "値上がり銘柄数"


def test_sector_note_reports_the_biggest_fade():
    maezyou = {"sectors": [
        {"sector": "電気機器", "returns": {"d1": 1.2}},
        {"sector": "銀行業", "returns": {"d1": 0.1}},
    ]}
    close = {"sectors": [
        {"sector": "電気機器", "returns": {"d1": 0.1}},
        {"sector": "銀行業", "returns": {"d1": 0.2}},
    ]}
    note = build_sector_note(maezyou, close)
    assert "電気機器" in note and "失速" in note


def test_sector_note_is_silent_when_nothing_moved():
    same = {"sectors": [{"sector": "電気機器", "returns": {"d1": 0.5}}]}
    assert build_sector_note(same, same) == ""


def test_followup_counts_a_breakout_as_a_hit():
    close_stocks = {
        "6146": _stock("6146", "ディスコ", "BREAKOUT"),
        "3697": _stock("3697", "SHIFT", None, near=True, pivot=None),
    }
    result = build_followup(
        [{"code": "6146", "bucket": "near"}, {"code": "3697", "bucket": "near"}],
        close_stocks,
    )
    assert result["hit_rate"] == 0.5
    assert result["items"][0]["result"] == "成功"
    assert result["items"][1]["result"] == "据え置き"


def test_followup_is_empty_without_yesterday():
    assert build_followup([], {"6146": _stock("6146", "ディスコ", "BREAKOUT")}) == {}


# ---------------------------------------------------------------------------
# 決算が近いブレイク
# ---------------------------------------------------------------------------
# 決算跨ぎのエントリーは別のゲームなので注意喚起だけする。候補から外しはしない。

def _earnings_stocks():
    return {
        # 大引でブレイク中、発表まであと2日。
        "6146": _stock("6146", "ディスコ", "BREAKOUT", pivot=1000,
                       days_to_earnings=2, next_earnings_date="2026-08-01"),
        # 大引でブレイク中、発表まであと0日(=今日が発表日)。
        "4478": _stock("4478", "フリー", "BREAKOUT", pivot=1000,
                       days_to_earnings=0, next_earnings_date=TODAY),
        # 発注候補(ブレイク前だがピボットあり)で発表まであと5日。窓の端は含める。
        "3697": _stock("3697", "SHIFT", "WATCH_A", pivot=1000,
                       days_to_earnings=5, next_earnings_date="2026-08-04"),
        # 発表まで遠いので出さない。
        "7203": _stock("7203", "トヨタ", "BREAKOUT", pivot=1000,
                       days_to_earnings=20, next_earnings_date="2026-08-19"),
        # 予定日が取れていない銘柄は出さない。
        "9984": _stock("9984", "ソフトバンクG", "BREAKOUT", pivot=1000),
    }


def test_earnings_soon_lists_breakouts_close_to_their_report_date():
    block = build_stocks_block({"6146": _stock("6146", "ディスコ", "WATCH_A")}, _earnings_stocks())
    rows = block["earnings_soon"]
    assert [r["code"] for r in rows] == ["4478", "6146", "3697"]  # 近い順
    assert rows[0]["days_to_earnings"] == 0
    assert rows[1]["next_earnings_date"] == "2026-08-01"
    # 注意喚起であって除外ではない。候補のリストからは消えていないこと。
    assert "4478" in block["_codes"]["close_breakout"]


def test_earnings_soon_ignores_stocks_whose_report_already_passed():
    """発表が済んだ銘柄(日数が負)は注意喚起の対象ではない。"""
    stocks = {"6146": _stock("6146", "ディスコ", "BREAKOUT", pivot=1000,
                             days_to_earnings=-1, next_earnings_date=YESTERDAY)}
    block = build_stocks_block({"6146": _stock("6146", "ディスコ", "WATCH_A")}, stocks)
    assert "earnings_soon" not in block


def test_earnings_soon_key_is_dropped_when_nothing_qualifies():
    """該当0件ならキーごと出さない(素材が欠けたブロックは黙って落とす)。"""
    stocks = {"6146": _stock("6146", "ディスコ", "BREAKOUT", pivot=1000)}
    block = build_stocks_block({"6146": _stock("6146", "ディスコ", "WATCH_A")}, stocks)
    assert "earnings_soon" not in block


# ---------------------------------------------------------------------------
# だましの実測率(基準線)
# ---------------------------------------------------------------------------
# 「今日だまし3件」だけでは多いのか少ないのか分からないので、同じ数え方の過去の値を
# 横に並べる。件数が足りないのに割合を言い切らないことが肝。

def _history_row(date, breakout, held, has_maezyou=True):
    return {
        "date": date,
        "has_maezyou": has_maezyou,
        "maezyou_breakout": [f"{i:04d}" for i in range(breakout)],
        "held": [f"{i:04d}" for i in range(held)],
    }


def test_baseline_totals_the_period_and_marks_it_reliable():
    rows = [_history_row(f"2026-07-{d:02d}", 10, 6) for d in range(1, 6)]
    baseline = build_baseline(rows, _history_row(TODAY, 4, 2), TODAY)
    assert baseline["days"] == 5
    assert baseline["sample"] == 50
    assert baseline["held_rate"] == 0.6      # 通算30/50。日ごとの割合の平均ではない
    assert baseline["today_held_rate"] == 0.5
    assert baseline["reliable"] is True


def test_baseline_is_empty_without_history():
    """履歴が空でも例外を出さず、キーごと出さない。運用開始直後はこれが正常。"""
    assert build_baseline([], _history_row(TODAY, 4, 2), TODAY) == {}


def test_baseline_skips_days_without_a_morning_session():
    """前場バッチが落ちた日は前場ブレイクが0件で残る。維持率0%の日として数えない。"""
    rows = [
        _history_row("2026-07-01", 10, 6),
        _history_row("2026-07-02", 0, 0, has_maezyou=False),
        _history_row("2026-07-03", 10, 6),
    ]
    baseline = build_baseline(rows, None, TODAY)
    assert baseline["days"] == 2
    assert baseline["sample"] == 20
    assert baseline["held_rate"] == 0.6


def test_baseline_excludes_today():
    """今日の行はこれから書くもの。自分自身と比べても基準にならない。"""
    rows = [
        _history_row("2026-07-01", 10, 6),
        _history_row(TODAY, 100, 100),   # 混ざると維持率が跳ね上がる
    ]
    baseline = build_baseline(rows, _history_row(TODAY, 100, 100), TODAY)
    assert baseline["days"] == 1
    assert baseline["sample"] == 10
    assert baseline["held_rate"] == 0.6


def test_baseline_is_not_reliable_with_a_small_sample():
    """件数が足りないのに「62%」と言い切らないための目印。"""
    rows = [_history_row("2026-07-01", 4, 3), _history_row("2026-07-02", 4, 2)]
    baseline = build_baseline(rows, None, TODAY)
    assert baseline["sample"] == 8
    assert baseline["reliable"] is False
    assert baseline["held_rate"] == 0.625


def test_baseline_today_rate_is_null_without_a_morning_breakout():
    """今日の前場ブレイクが0件なら割合そのものが存在しない(0%ではない)。"""
    rows = [_history_row("2026-07-01", 10, 6)]
    baseline = build_baseline(rows, _history_row(TODAY, 0, 0), TODAY)
    assert baseline["today_held_rate"] is None


def test_baseline_uses_only_the_most_recent_window():
    from src.report.review import BASELINE_WINDOW_DAYS

    old = [_history_row(f"2026-06-{d:02d}", 10, 10) for d in range(1, 11)]
    recent = [_history_row(f"2026-07-{d:02d}", 10, 5)
              for d in range(1, BASELINE_WINDOW_DAYS + 1)]
    baseline = build_baseline(old + recent, None, TODAY)
    assert baseline["days"] == BASELINE_WINDOW_DAYS
    assert baseline["held_rate"] == 0.5   # 古い10日は窓の外なので混ざらない


def test_baseline_reaches_the_review_through_build_review():
    review = build_review(
        TODAY, _close_report(), _close_breadth(),
        maezyou_report=_maezyou_report(), maezyou_breadth=_maezyou_breadth(),
        history_rows=[_history_row(f"2026-07-{d:02d}", 10, 6) for d in range(1, 6)],
        now=NOW,
    )
    assert review["stocks"]["baseline"]["held_rate"] == 0.6
    # 今日は前場ブレイク2件のうち1件が引けまで残っている。
    assert review["stocks"]["baseline"]["today_held_rate"] == 0.5


def test_baseline_key_is_absent_when_history_is_empty():
    review = build_review(
        TODAY, _close_report(), _close_breadth(),
        maezyou_report=_maezyou_report(), maezyou_breadth=_maezyou_breadth(), now=NOW,
    )
    assert "baseline" not in review["stocks"]
