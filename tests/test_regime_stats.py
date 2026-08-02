"""候補のその後の集計(src/report/regime_stats.py)のテスト。

ファイルI/Oを伴う update_candidate_outcomes ではなく、その中身の純粋関数
(resolve_record / summarize_bands / build_stats / select_new_rows)を直接叩く。

見張っているのは主に3つで、どれも外れると画面に嘘の数字が出る:

1. 追跡日数が src/report/dryup_log.py と食い違わないこと。片方だけ動かすと
   同じリポジトリの中に「10営業日後の成績」と「15営業日後の成績」が並ぶ。
2. 将来の値動きが足りない行を「決着した」ことにしないこと。まだ分からない行が
   分母に入ると勝率が跳ねる。
3. 単位の約束(win_rate は 0〜1、騰落率は %表記、market_score は 0〜100)。
"""
from datetime import date, timedelta

import pandas as pd

from src.report import regime_stats as rs


# ---------------------------------------------------------------------------
# 材料づくり
# ---------------------------------------------------------------------------

def _mkbars(rows, start="2026-01-01"):
    """(高値, 終値, 出来高倍率) の並びを resolve_record が食うバー列にする。

    日付は start から1日ずつ。resolve_record は日付を文字列としてしか比べないので、
    土日祝を飛ばす必要は無い(営業日の数え方は「何本目か」だけで決まる)。
    """
    d0 = date.fromisoformat(start)
    return [
        ((d0 + timedelta(days=i)).isoformat(), high, close, vol_ratio)
        for i, (high, close, vol_ratio) in enumerate(rows)
    ]


def _record(date_str="2026-01-01", code="A", bucket="order", market_score=None):
    return rs.build_outcome_record(
        {"date": date_str, "code": code, "bucket": bucket, "total_score": 70},
        market_score=market_score,
    )


def _settled(market_score, return_pct, *, vol_ratio=None, after_pct=None):
    """集計だけを見たいときの、決着済みレコードの手組み。"""
    rec = _record()
    rec.update({
        "market_score": market_score,
        "return_pct": return_pct,
        "breakout": vol_ratio is not None or after_pct is not None,
        "vol_ratio_at_breakout": vol_ratio,
        "return_after_breakout_pct": after_pct,
        "resolved": True,
    })
    return rec


# ---------------------------------------------------------------------------
# 1. 追跡日数が dryup_log と揃っていること
# ---------------------------------------------------------------------------

def test_forward_days_matches_dryup_log():
    """ここが食い違うと、同じリポジトリの中で「N営業日後」の N が二種類になる。"""
    from src.report import dryup_log

    assert rs.FORWARD_DAYS == dryup_log.POST_BREAKOUT_DAYS
    assert rs.BREAKOUT_WAIT_DAYS == dryup_log.BREAKOUT_WAIT_DAYS


def test_target_buckets_are_only_order_and_watch():
    """母集団は発注と監視だけ。near/forming を入れると問いの対象が変わる。"""
    assert rs.TARGET_BUCKETS == ("order", "watch")


# ---------------------------------------------------------------------------
# 2. 帯の境界値(下限を含み、上限を含まない)
# ---------------------------------------------------------------------------

def test_regime_band_boundaries_include_the_lower_edge():
    assert rs.regime_band(0) == "40未満"
    assert rs.regime_band(39.99) == "40未満"
    assert rs.regime_band(40) == "40〜60"      # 下限ちょうどは上の帯
    assert rs.regime_band(59.99) == "40〜60"
    assert rs.regime_band(60) == "60〜80"
    assert rs.regime_band(79.99) == "60〜80"
    assert rs.regime_band(80) == "80以上"
    assert rs.regime_band(100) == "80以上"


def test_regime_band_is_none_for_unusable_values():
    """スコアが取れない日を 0 点扱いすると「40未満」の帯が汚れる。"""
    assert rs.regime_band(None) is None
    assert rs.regime_band("") is None
    assert rs.regime_band("なし") is None
    assert rs.regime_band(float("nan")) is None


def test_volume_band_boundaries_include_the_lower_edge():
    assert rs.volume_band(0.9) == "1.4倍未満"
    assert rs.volume_band(1.399) == "1.4倍未満"
    assert rs.volume_band(1.4) == "1.4〜2.0倍"  # config の breakout_vol_mult と同じ値
    assert rs.volume_band(1.99) == "1.4〜2.0倍"
    assert rs.volume_band(2.0) == "2.0〜3.0倍"
    assert rs.volume_band(2.999) == "2.0〜3.0倍"
    assert rs.volume_band(3.0) == "3.0倍以上"
    assert rs.volume_band(12.5) == "3.0倍以上"


def test_volume_band_is_none_when_the_ratio_is_missing():
    assert rs.volume_band(None) is None
    assert rs.volume_band(float("nan")) is None


# ---------------------------------------------------------------------------
# 3. レコードの生成
# ---------------------------------------------------------------------------

def test_new_record_starts_with_nothing_known():
    """足りないデータを楽観で埋めない。価格由来の欄は全部 None で始まる。"""
    rec = _record(market_score=72.0)
    assert rec["date"] == "2026-01-01" and rec["code"] == "A"
    assert rec["bucket"] == "order" and rec["total_score"] == 70
    assert rec["market_score"] == 72.0
    for key in ("ref_high", "ref_close", "breakout", "breakout_date",
                "vol_ratio_at_breakout", "return_pct", "return_after_breakout_pct"):
        assert rec[key] is None, key
    assert rec["resolved"] is False


# ---------------------------------------------------------------------------
# 4. resolve_record — 足りない行は据え置き、決着した行は再計算しない
# ---------------------------------------------------------------------------

def test_resolve_leaves_the_record_untouched_without_future_bars():
    """記録日のバーしか無い日は何も決まらない。"""
    rec = _record()
    rs.resolve_record(rec, _mkbars([(110, 100, None)]))
    assert rec["ref_high"] == 110 and rec["ref_close"] == 100
    assert rec["breakout"] is None
    assert rec["return_pct"] is None
    assert rec["resolved"] is False


def test_resolve_does_nothing_when_the_record_day_is_missing():
    """記録日のバーが無ければ基準(高値・終値)が取れないので触らない。

    まだ将来のバーが待機窓ぶん揃っていないうちは「後で埋まるかもしれない」ので
    据え置く(諦めない)。
    """
    rec = _record(date_str="2026-01-01")
    short = rs.BREAKOUT_WAIT_DAYS - 1
    rs.resolve_record(rec, _mkbars([(110, 100, None)] * short, start="2026-02-01"))
    assert rec["ref_high"] is None and rec["ref_close"] is None
    assert rec["resolved"] is False
    assert rec.get("unusable") is None


def test_resolve_gives_up_when_the_record_day_never_arrives():
    """待機窓ぶん先まで進んでも記録日の足が来ないなら、待っても一生埋まらない。

    その日の価格取得に失敗したまま埋め戻されなかった行。決着扱いにしないと
    「まだ決着していない候補がN件あります」の注記が永久に消えない。
    """
    rec = _record(date_str="2026-01-01")
    rs.resolve_record(rec, _mkbars([(110, 100, None)] * 30, start="2026-02-01"))
    assert rec["ref_high"] is None and rec["ref_close"] is None
    assert rec["unusable"] is True
    assert rec["resolved"] is True

    stats = rs.build_stats([rec])
    # どちらの表にも入らないが、「まだ分からない行」としても数えない。
    assert not any("まだ決着していない" in n for n in stats["notes"])
    assert any("値段が残っていない" in n for n in stats["notes"])


def test_resolve_keeps_breakout_undecided_until_the_wait_window_is_full():
    """待機窓ぶんの将来のバーが揃うまで「空振り」とは言えない。

    騰落率(10営業日)は先に出るが、ブレイクの可否(20営業日)がまだなので
    決着にはならない。ここを取り違えると未確定の行が分母に入る。
    """
    rec = _record()
    bars = _mkbars([(110, 100, None)] + [(105, 100, None)] * 15)
    rs.resolve_record(rec, bars)
    assert rec["return_pct"] == 0.0      # 10営業日後は出ている
    assert rec["breakout"] is None       # ブレイクの可否はまだ分からない
    assert rec["resolved"] is False


def test_resolve_marks_a_missed_wait_window_as_no_breakout():
    rec = _record()
    bars = _mkbars([(110, 100, None)] + [(105, 100, None)] * rs.BREAKOUT_WAIT_DAYS)
    rs.resolve_record(rec, bars)
    assert rec["breakout"] is False
    assert rec["breakout_date"] is None
    assert rec["return_pct"] == 0.0
    assert rec["resolved"] is True       # ブレイクしなかった行は騰落率が出れば終わり


def test_resolve_needs_the_close_to_exceed_the_reference_high():
    """記録日の高値ちょうどはブレイクにしない(上回った、が条件)。"""
    rec = _record()
    bars = _mkbars([(110, 100, None)] + [(110, 110, 3.0)] * rs.BREAKOUT_WAIT_DAYS)
    rs.resolve_record(rec, bars)
    assert rec["breakout"] is False


def test_resolve_records_the_breakout_and_both_returns():
    rec = _record()
    bars = _mkbars(
        [(110, 100, None),      # 0 記録日: 高値110 / 終値100
         (105, 100, None),      # 1
         (105, 100, None),      # 2
         (125, 120, 2.5)]       # 3 終値120が記録日高値110を初めて上回る
        + [(125, 110, 1.0)] * 9  # 4〜12
        + [(140, 132, 1.0)]      # 13 ブレイク日から10営業日後
    )
    rs.resolve_record(rec, bars)
    assert rec["breakout"] is True
    assert rec["breakout_date"] == "2026-01-04"
    assert rec["vol_ratio_at_breakout"] == 2.5
    # 記録日終値100 -> 10営業日後(index 10)の終値110
    assert rec["return_pct"] == 10.0
    # ブレイク日終値120 -> その10営業日後(index 13)の終値132
    assert rec["return_after_breakout_pct"] == 10.0
    assert rec["resolved"] is True


def test_resolve_holds_a_breakout_open_until_the_forward_return_lands():
    """ブレイクした行は、ブレイク日からの騰落率が出るまで決着させない。

    出来高の集計がこの値を使うので、先に閉じてしまうと出来高の表に穴が空く。
    """
    rec = _record()
    bars = _mkbars(
        [(110, 100, None), (105, 100, None), (105, 100, None), (125, 120, 2.5)]
        + [(125, 110, 1.0)] * 8   # ブレイク日から8本しか無い(10本必要)
    )
    rs.resolve_record(rec, bars)
    assert rec["breakout"] is True
    assert rec["return_pct"] == 10.0
    assert rec["return_after_breakout_pct"] is None
    assert rec["resolved"] is False


def test_resolve_is_idempotent():
    """同じバーを二度渡しても結果が変わらない。"""
    bars = _mkbars([(110, 100, None)] + [(105, 100, None)] * rs.BREAKOUT_WAIT_DAYS)
    once = rs.resolve_record(_record(), bars)
    twice = rs.resolve_record(rs.resolve_record(_record(), bars), bars)
    assert once == twice


def test_resolve_never_recomputes_a_settled_record():
    """決着済みは即座に返す。ここが効かないと毎日フルスキャンになる。"""
    rec = _record()
    rec.update({"resolved": True, "breakout": False, "return_pct": -3.0,
                "ref_high": 110, "ref_close": 100})
    before = dict(rec)
    # 本来ならブレイク扱いになるバーを渡しても書き換わらない。
    rs.resolve_record(rec, _mkbars([(110, 100, None)] + [(200, 190, 5.0)] * 30))
    assert rec == before


# ---------------------------------------------------------------------------
# 5. frame_to_bars — DataFrame を触るのはここだけ
# ---------------------------------------------------------------------------

def test_frame_to_bars_sorts_and_computes_the_volume_ratio():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-01"]),
        "high": [120.0, 110.0],
        "close": [115.0, 100.0],
        "volume": [3000, 1000],
        "vol_ma50": [1000.0, 1000.0],
    })
    bars = rs.frame_to_bars(df)
    assert [b[0] for b in bars] == ["2026-01-01", "2026-01-02"]   # 昇順に直る
    assert bars[0] == ("2026-01-01", 110.0, 100.0, 1.0)
    assert bars[1][3] == 3.0


def test_frame_to_bars_leaves_the_volume_ratio_none_without_the_average():
    """vol_ma50 が無い/0 の日を 0 や 1.0 で埋めると出来高の帯が汚れる。"""
    df = pd.DataFrame({
        "date": ["2026-01-01", "2026-01-02"],
        "high": [110.0, 120.0],
        "close": [100.0, 115.0],
        "volume": [1000, 2000],
        "vol_ma50": [0.0, None],
    })
    assert [b[3] for b in rs.frame_to_bars(df)] == [None, None]
    assert rs.frame_to_bars(None) == []


# ---------------------------------------------------------------------------
# 6. summarize_bands — 単位の約束と、件数の足りない帯の扱い
# ---------------------------------------------------------------------------

def test_summarize_bands_keeps_all_bands_even_when_empty():
    """件数0の帯も行として残す。消すと「サンプルが無い」ことに気付けなくなる。"""
    rows = rs.summarize_bands([("40〜60", 1.0)], rs.REGIME_BANDS)
    assert [r["label"] for r in rows] == [label for _u, label in rs.REGIME_BANDS]
    empty = next(r for r in rows if r["label"] == "80以上")
    assert empty["n"] == 0
    assert empty["win_rate"] is None and empty["median_return"] is None
    assert empty["reliable"] is False


def test_summarize_bands_marks_thin_buckets_as_not_reliable():
    """MIN_BUCKET_N に届かない帯は reliable=False(行は消さず参考値として出す)。"""
    thin = [("40〜60", 1.0)] * (rs.MIN_BUCKET_N - 1)
    row = next(r for r in rs.summarize_bands(thin, rs.REGIME_BANDS) if r["label"] == "40〜60")
    assert row["n"] == rs.MIN_BUCKET_N - 1
    assert row["reliable"] is False

    enough = [("40〜60", 1.0)] * rs.MIN_BUCKET_N
    row = next(r for r in rs.summarize_bands(enough, rs.REGIME_BANDS) if r["label"] == "40〜60")
    assert row["reliable"] is True      # ちょうど MIN_BUCKET_N は参考値ではない


def test_summarize_bands_win_rate_is_a_ratio_and_zero_is_not_a_win():
    """win_rate は 0〜1 の割合。ちょうど 0% の行は勝ちに数えない。"""
    labelled = [("40〜60", 1.0), ("40〜60", 0.0), ("40〜60", -1.0), ("40〜60", 2.0)]
    row = next(r for r in rs.summarize_bands(labelled, rs.REGIME_BANDS) if r["label"] == "40〜60")
    assert row["n"] == 4
    assert row["win_rate"] == 0.5       # 2/4。0.0 は勝ちではない
    assert 0.0 <= row["win_rate"] <= 1.0


def test_summarize_bands_median_return_stays_in_percent():
    """-0.8 は -0.8% であって -80% でも -0.008 でもない。"""
    labelled = [("40〜60", -2.0), ("40〜60", -0.8), ("40〜60", 0.4), ("40〜60", 3.2)]
    row = next(r for r in rs.summarize_bands(labelled, rs.REGIME_BANDS) if r["label"] == "40〜60")
    assert row["median_return"] == -0.2     # (-0.8 + 0.4) / 2
    labelled3 = [("40〜60", -2.0), ("40〜60", -0.8), ("40〜60", 0.4)]
    row3 = next(r for r in rs.summarize_bands(labelled3, rs.REGIME_BANDS) if r["label"] == "40〜60")
    assert row3["median_return"] == -0.8


def test_summarize_bands_ignores_rows_without_a_band_or_a_return():
    """帯に落ちない行(スコア欠損)と騰落率が無い行を分母に入れない。"""
    labelled = [("40〜60", 1.0), (None, 5.0), ("40〜60", None)]
    rows = rs.summarize_bands(labelled, rs.REGIME_BANDS)
    assert sum(r["n"] for r in rows) == 1


# ---------------------------------------------------------------------------
# 7. build_stats — 表ごとの採否、単位、キーの契約
# ---------------------------------------------------------------------------

def test_build_stats_counts_rows_per_table_not_per_record():
    """地合いの表は「記録日からの騰落率が出ているか」だけで採否を決める。

    行全体の決着(resolved)を条件にすると、ブレイクの空振り確定
    (BREAKOUT_WAIT_DAYS)を待つぶん、10営業日で出せる地合いの表まで
    余計に遅れて出てくることになる。
    """
    settled = [_settled(70.0, 1.0) for _ in range(rs.MIN_BUCKET_N)]
    half = _record(market_score=70.0)
    half["return_pct"] = 1.0            # 騰落率は出ている(ブレイクの判定はまだ)
    stats = rs.build_stats(settled + [half])

    row = next(r for r in stats["regime"]["rows"] if r["label"] == "60〜80")
    assert row["n"] == rs.MIN_BUCKET_N + 1
    assert row["reliable"] is True
    # 表に入っている行は「まだ分からない行」ではない。
    assert not any("決着していない候補" in n for n in stats["notes"])


def test_build_stats_keeps_records_with_no_numbers_out_of_the_tables():
    """騰落率がまだ何も出ていない行は表に入れず、待ちとして注記に出す。"""
    settled = [_settled(70.0, 1.0) for _ in range(rs.MIN_BUCKET_N)]
    pending = _record(market_score=70.0)   # 騰落率なし
    stats = rs.build_stats(settled + [pending])

    row = next(r for r in stats["regime"]["rows"] if r["label"] == "60〜80")
    assert row["n"] == rs.MIN_BUCKET_N
    assert any("決着していない候補が1件" in n for n in stats["notes"])


def test_build_stats_reports_the_units_it_promises():
    records = [
        _settled(35.0, -1.5),
        _settled(72.0, 4.0, vol_ratio=2.5, after_pct=3.0),
        _settled(88.0, 0.0),
    ]
    stats = rs.build_stats(records)
    assert stats["forward_days"] == rs.FORWARD_DAYS
    assert stats["regime"]["min_n"] == rs.MIN_BUCKET_N
    for section in ("regime", "volume"):
        for row in stats[section]["rows"]:
            if row["win_rate"] is not None:
                assert 0.0 <= row["win_rate"] <= 1.0, row
            if row["median_return"] is not None:
                # %表記なので、割合(|x|<=1 に収まる)ではない値を素通しできる。
                assert isinstance(row["median_return"], float), row
    # market_score は 0〜100 のスコアそのもの(0〜1 の割合ではない)。
    assert rs.regime_band(35.0) == "40未満"
    assert rs.regime_band(72.0) == "60〜80"
    assert rs.regime_band(88.0) == "80以上"


def test_build_stats_volume_table_uses_breakouts_only():
    """出来高の表はブレイクした行だけ。起点もブレイク日の終値。"""
    records = [
        _settled(70.0, 5.0),                                      # ブレイクなし
        _settled(70.0, 5.0, vol_ratio=1.5, after_pct=2.0),
        _settled(70.0, 5.0, vol_ratio=1.5, after_pct=-1.0),
    ]
    stats = rs.build_stats(records)
    row = next(r for r in stats["volume"]["rows"] if r["label"] == "1.4〜2.0倍")
    assert row["n"] == 2 and row["win_rate"] == 0.5
    assert sum(r["n"] for r in stats["volume"]["rows"]) == 2
    assert any("ブレイクしなかった候補が1件" in n for n in stats["notes"])


def test_build_stats_window_spans_the_recorded_days():
    a = _settled(70.0, 1.0)
    b = _settled(70.0, 1.0)
    b["date"] = "2026-02-10"
    stats = rs.build_stats([b, a])
    assert stats["window"] == {"from": "2026-01-01", "to": "2026-02-10", "days": 2}
    assert stats["generated_at"]


def test_build_stats_is_empty_without_records():
    stats = rs.build_stats([])
    assert stats["window"] == {"from": None, "to": None, "days": 0}
    assert all(r["n"] == 0 for r in stats["regime"]["rows"])
    assert all(r["n"] == 0 for r in stats["volume"]["rows"])


# ---------------------------------------------------------------------------
# 8. select_new_rows — 既知のキーを二重に取り込まないこと
# ---------------------------------------------------------------------------

def _stage_row(date_str, code, bucket):
    return {"date": date_str, "code": code, "bucket": bucket, "total_score": 70}


def test_select_new_rows_skips_known_keys():
    """既に記録済みの (date, code) を取り込むと、その日の候補が二重に数えられる。"""
    stage_rows = [
        _stage_row("2026-01-01", "A", "order"),
        _stage_row("2026-01-01", "B", "watch"),
    ]
    known = {("2026-01-01", "A")}
    picked = rs.select_new_rows(stage_rows, known)
    assert [r["code"] for r in picked] == ["B"]


def test_select_new_rows_keeps_only_order_and_watch():
    stage_rows = [
        _stage_row("2026-01-01", "A", "order"),
        _stage_row("2026-01-01", "B", "watch"),
        _stage_row("2026-01-01", "C", "near"),
        _stage_row("2026-01-01", "D", "forming"),
        _stage_row("2026-01-01", "E", "cooled"),
    ]
    assert sorted(r["code"] for r in rs.select_new_rows(stage_rows, set())) == ["A", "B"]


def test_select_new_rows_collapses_repeats_of_the_same_day_last_wins():
    """stage.jsonl は追記専用なので同じ (date, code) が複数回出る。後勝ちで1件。"""
    stage_rows = [
        _stage_row("2026-01-01", "A", "watch"),
        {"date": "2026-01-01", "code": "A", "bucket": "order", "total_score": 88},
    ]
    picked = rs.select_new_rows(stage_rows, set())
    assert len(picked) == 1
    assert picked[0]["bucket"] == "order" and picked[0]["total_score"] == 88


def test_select_new_rows_drops_rows_without_a_key():
    stage_rows = [
        {"date": None, "code": "A", "bucket": "order"},
        {"date": "2026-01-01", "code": None, "bucket": "order"},
        _stage_row("2026-01-01", "A", "order"),
    ]
    assert len(rs.select_new_rows(stage_rows, set())) == 1


def test_select_new_rows_stamps_the_market_score_of_that_day():
    """地合いスコアは行に焼き込む(breadth.json の履歴は60日で切られて引き直せない)。"""
    stage_rows = [
        _stage_row("2026-01-01", "A", "order"),
        _stage_row("2026-01-02", "B", "order"),
    ]
    picked = rs.select_new_rows(stage_rows, set(), {"2026-01-01": 72.0})
    by_code = {r["code"]: r for r in picked}
    assert by_code["A"]["market_score"] == 72.0
    assert by_code["B"]["market_score"] is None     # 取れない日は None のまま


def test_market_score_by_date_reads_the_breadth_history():
    breadth = {"history": [
        {"date": "2026-01-01", "market_score": 72.0},
        {"date": "2026-01-02"},                       # 旧エントリはスコアが無い
        {"market_score": 50.0},                       # 日付が無い行は使えない
    ]}
    assert rs.market_score_by_date(breadth) == {"2026-01-01": 72.0}
    assert rs.market_score_by_date(None) == {}
    assert rs.market_score_by_date({}) == {}
