"""Site generation: report.json, breadth.json, per-stock chart JSON, and
copying the static dashboard/detail-page assets into docs/ (design doc 7).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.report.secure_io import read_docs_json, write_docs_json
from src.screener.scoring import combined_score

JST = timezone(timedelta(hours=9))


def market_session(now: datetime | None = None) -> str:
    """バッチ実行時刻(JST)から東証の市場セッションを判定する。

    フロント(前場/後場コピーボタン)が「この report.json がどのセッションの
    スナップショットか」を表示・警告に使うためのラベル。値そのものはスコアや
    判定に一切影響しない表示専用フィールド。

    - 前場          : 09:00〜12:30 JST (前場終了バッチ = 11:35頃の想定)
    - 後場(ザラ場中): 12:30〜15:00 JST
    - 引け後        : 15:00〜翌09:00 JST (日次バッチ = 16時以降の想定)
    """
    now = now or datetime.now(JST)
    # tz-naive で渡された場合は JST とみなす
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)
    minutes = now.hour * 60 + now.minute
    if 9 * 60 <= minutes < 12 * 60 + 30:
        return "前場"
    if 12 * 60 + 30 <= minutes < 15 * 60:
        return "後場(ザラ場中)"
    return "引け後"

DOCS_DIR = REPO_ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"
CHARTS_DIR = DOCS_DATA_DIR / "charts"
REPORT_PATH = DOCS_DATA_DIR / "report.json"
BREADTH_PATH = DOCS_DATA_DIR / "breadth.json"

STATUS_ORDER = {
    "BREAKOUT": 0,
    "BREAKOUT_WEAK": 1,
    "WATCH_A": 2,
    "WATCH_B": 3,
    "EXTENDED": 4,
    "STALE": 5,  # ブレイク鮮度切れ(2026-07-21追加)。EXTENDEDと同じ追いかけ禁止枠
    # Watchlist tier: trend template passed, but VCP hasn't produced an
    # actionable setup yet. Ordered roughly by "how close to a real base".
    "REJECTED": 6,
    "IMMATURE": 7,
    "TOO_RECENT": 8,
    "TOO_VOLATILE": 9,
    "NO_BASE": 10,
}
# ティアの序列。**2026-07-29以降、一覧の並び順には使っていない**(_sort_key は
# total_score 単一軸になった)。ティアはバッジ表示用にレコードへ残るだけなので、
# この定数は意味の序列を記録しておくためのもの。
TIER_ORDER = {"confirmed": 0, "pool": 1, "watchlist": 2, "cooled": 3}

# セクター強度(機能B)の序列: 強 -> 中 -> 弱 -> 不明。
# 上と同じく 2026-07-29 以降 _sort_key では使っていない(表示・分析用に残置)。
SECTOR_STRENGTH_ORDER = {"強": 0, "中": 1, "弱": 2}


# ---------------------------------------------------------------------------
# 決算発表までの日数 (2026-07-31追加)
# ---------------------------------------------------------------------------

def _as_date(value) -> date | None:
    """ISO文字列 / date / datetime / pandas Timestamp を date に揃える。

    銘柄レコードの日付は pandas の Timestamp で入ってくるが、決算カレンダー側は
    "2026-08-07" のような文字列で来る。どちらも受けられないと呼び出し側が
    毎回型を気にすることになる。読めない値(NaT・空文字)は None に落とす。

    **NaT は真っ先に弾く。** `isinstance(pd.NaT, datetime)` は True なので下の
    datetime 分岐に入ってしまい、`pd.NaT.date()` は None ではなく NaT を返す。
    そのまま通すと report.json に文字列 "NaT" が載り、days_to_earnings の引き算が
    TypeError を投げて日次バッチごと落ちる(呼び出し側は try で囲っていない)。
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        # 「欠損かどうか」を真偽値1個で答えられない型(配列など)。日付ではないので下で落ちる。
        pass
    # datetime は date の派生なので先に見る(順番を逆にすると時刻が落ちない)。
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def days_to_earnings(next_earnings_date, as_of) -> int | None:
    """基準日から次回決算発表予定日までの暦日数。過ぎていれば負、不明なら None。

    値を生成時に確定させるのが目的。画面側でブラウザの時計から引き算すると、
    バッチが走った日と画面を見ている日がずれたとたんに嘘になる(週末を挟むと
    「あと1日」が金曜のまま月曜まで残る)。基準日はバッチの実行時刻ではなく
    そのレコードの元になった足の日付を使う。

    営業日ではなく暦日で数えているのは、注意喚起の相手が「発表を跨いでポジションを
    持つかどうか」だから。土日を除いた日数を出しても判断は変わらない。
    """
    base = _as_date(as_of)
    target = _as_date(next_earnings_date)
    if base is None or target is None:
        return None
    return (target - base).days


# ---------------------------------------------------------------------------
# 7.2 report.json assembly
# ---------------------------------------------------------------------------

def assemble_stock_record(
    code: str,
    name: str,
    latest_row: dict,
    tt_flags: dict,
    vcp_result: dict,
    entry_result: dict,
    fund_info: dict,
    config: dict | None = None,
    tier_override: str | None = None,
    margin_store: dict | None = None,
    next_earnings_date=None,
) -> dict:
    """Combine the outputs of trend_template/vcp/entry/fundamentals into one
    report.json stock record.

    `tier_override` lets the caller place a stock in a non-standard tier
    instead of the fundamentals-coverage-derived confirmed/pool tier:
    - "watchlist": trend template passed, but no actionable VCP/entry setup yet
    - "cooled": breakout already happened but it's too late to enter
      (EXTENDED=伸びすぎ / STALE=ブレイク鮮度切れ). Pivot/stop levels are
      still computed and included in the record.

    `margin_store` is data/margin_weekly.json's dict, pre-loaded once by the
    caller (pipeline.py) so this per-stock function doesn't re-read the file
    from disk on every call.表示専用(信用残)。総合スコアには一切使わない。

    `next_earnings_date` (2026-07-31追加、省略可) は次回決算発表予定日。渡されると
    レコードの日付を基準にした残り暦日数 (`days_to_earnings`) までここで確定する。
    決算カレンダーを別途引いてから後付けする経路もあるので省略可能にしてあり、
    その場合の日数は build_report が書き出し直前に埋める。
    """
    config = config or load_config()

    tier = tier_override or fund_info["tier"]
    # 2026-07-22改定: ランキングは全ティア tech_score に統一(純セットアップ品質)。
    # full_score はレコードに残るが表示・分析専用でランキングには使わない。
    # ファンダは tier バッジ + fund_verdict/fund_multiplier(サイズ係数)に再配置。
    phase1_score = fund_info.get("tech_score")
    vcp_score = vcp_result.get("vcp_score")
    # 2026-07-29改定: VCPセットアップ未成立(vcp_score=None)は 0 として合成する。
    # 旧実装は total_score = phase1_score にフォールバックしていたため、監視銘柄の
    # 「70」と本命銘柄の「70」が別物になり同じ列で並べられなかった。0に倒すことで
    # 監視銘柄は上限50に沈み、ティア隔離なしで1本のリストに並べられる。log.md (143)。
    if phase1_score is not None:
        total_score = combined_score(phase1_score, vcp_score, config)
    else:
        total_score = None

    # このレコードが何日の足でできているか。決算までの日数の基準日であり、
    # 生成時刻(バッチが走った時刻)とは別物。データ取得が失敗して前日の足のまま
    # 走った日に、実行日を基準にして日数を数えてしまわないようにするため持つ。
    as_of = _as_date(latest_row.get("date"))

    return {
        "code": code,
        "name": name,
        "tier": tier,
        "status": entry_result.get("status"),
        "date": as_of.isoformat() if as_of else None,
        "close": latest_row.get("close"),
        # 日中レンジ(始値・高値・安値)と出来高。日次レビューが「終値が日中レンジの
        # どこで引けたか」を機械的に判定するための素材(2026-07-31追加)。終値だけでは
        # 「上ヒゲを残して失速した日」と「高値引けした日」が同じ数字に見えてしまう。
        # 前場スナップショットの終値は前場終値なので、前場と大引の2断面を突き合わせると
        # 「前場に高値をつけて大引は安値引け(寄り天)」のような形まで復元できる。
        "open": _finite(latest_row.get("open")),
        "high": _finite(latest_row.get("high")),
        "low": _finite(latest_row.get("low")),
        # 出来高は株数なので小数は要らない(レポートの肥大を避けて整数に丸める)。
        "volume": _finite(latest_row.get("volume"), digits=0),
        # 出来高の平均比。詳細は _relative_volume の説明を参照。
        "rvol": _relative_volume(latest_row, entry_result),
        "total_score": total_score,
        "tech_score": fund_info.get("tech_score"),
        # tech_score の3成分(当日断面パーセンタイル)。個別画面のスコア内訳用。
        # 等ウェイトなので「成分値 = そのままパーセンタイル」でフロント側の再計算は不要。
        # attach_score_percentiles が latest_row に書いた値をそのまま通す。
        "score_pct": latest_row.get("score_pct") or None,
        "full_score": fund_info.get("full_score"),
        "vcp_score": vcp_score,
        "rs": latest_row.get("rs"),
        "footprint": vcp_result.get("footprint"),
        "pivot": entry_result.get("pivot"),
        "buy_stop": entry_result.get("buy_stop"),
        "stop_loss": entry_result.get("stop_loss"),
        "risk_pct": entry_result.get("risk_pct"),
        "dist_to_pivot": entry_result.get("dist_to_pivot"),
        # STALE時のみ入る初回ブレイクからの経過暦日(summary.pyの文言・個別画面用)。
        "breakout_age_days": entry_result.get("breakout_age_days"),
        "fund_coverage": fund_info.get("fund_coverage"),
        "fund_strong": fund_info.get("fund_strong"),
        # ファンダのサイズ係数レイヤー(2026-07-22)。pass=1.0(フル)/unknown=0.5
        # (ハーフ)/fail=0.0(エントリー取り止め)。フロントのサイジング計算機が
        # 乗数として適用する。判定基準は Code33 (fund_coverage_tier と共用)。
        "fund_verdict": fund_info.get("fund_verdict"),
        "fund_multiplier": fund_info.get("fund_multiplier"),
        "fund_eps_yoy": fund_info.get("fund_eps_yoy"),
        "fund_rev_yoy": fund_info.get("fund_rev_yoy"),
        "fund_stale": fund_info.get("fund_stale", False),
        "fund_checked_date": fund_info.get("fund_checked_date"),
        "eps_accel_slope": fund_info.get("eps_accel_slope"),
        "must_flags": {"tt": tt_flags, "vcp": vcp_result.get("must_flags")},
        # サマリー生成(summary.py)・個別銘柄画面用のVCP文脈。footprint文字列
        # より一段細かい素の数値(ベース日数・高値からの日数・各収縮の深さ%)。
        "vcp_detail": _build_vcp_detail(vcp_result, config),
        # 枯れ度(DRY-UP)バッジ/ソート用。VCP MUST・vcp_scoreとは独立(表示専用、
        # スコア融合なし)。バッジ値=dryup_med_10_50。
        "dryup": _build_dryup_badge(vcp_result, config),
        # 監視タブ分類用(actionableならNone)。stage/near/missing/detail。
        "setup_stage": build_setup_stage(vcp_result, config),
        # 次回決算発表予定日と、そこまでの残り暦日数。日数を画面で計算させないのは
        # days_to_earnings の説明のとおり(見ている日とバッチが走った日がずれると嘘になる)。
        "next_earnings_date": next_earnings_date,
        "days_to_earnings": days_to_earnings(next_earnings_date, as_of),
        # 信用残(需給)。表示専用レイヤー、総合スコアには一切組み込まない
        # (「スコアは順位付け、フラグは事実」。dryupと同じ方針)。データ無しはNone。
        "margin": _build_margin_metrics(code, latest_row, margin_store, config),
    }


def _finite(value, digits: int | None = 2):
    """数値を丸めて返す。数値でないもの・NaN・無限大は None に落とす。

    docs/data の JSON はブラウザの JSON.parse が読む。Python の json は NaN を
    そのまま `NaN` というリテラルで書き出すが、これは JSON の規格外でパースが
    ファイルまるごと失敗する。指標の計算元が欠損している行(上場直後で平均が
    出せない等)を素通しさせないための関門。
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / ±Inf
        return None
    if digits == 0:
        return int(round(number))
    return round(number, digits) if digits is not None else number


def _relative_volume(latest_row: dict, entry_result: dict) -> float | None:
    """出来高の平均比(当日出来高 ÷ 50日平均出来高)。1.0 なら平均並み。

    分母を50日平均にしているのは、このリポジトリで「出来高が多い/枯れている」を
    語っている場所がすべて50日平均を基準にしているため。エントリー判定のブレイク時
    出来高条件(volume_multiple)も、VCPの枯れ度バッジ(直近10日中央値÷50日平均)も
    50日平均。ここだけ20日平均にすると、同じ銘柄レコードの中に基準の違う「出来高
    倍率」が2つ並ぶことになり、レビューを読む側が取り違える。

    ピボットが立っている銘柄はエントリー判定側で既に同じ割り算を済ませているので、
    その値をそのまま使う(二重計算しない)。ピボットが無い銘柄(監視ティア等)は
    エントリー判定が途中で打ち切られていて値が無いので、ここで計算する。
    """
    ratio = _finite(entry_result.get("volume_multiple"))
    if ratio is not None:
        return ratio
    volume = _finite(latest_row.get("volume"), digits=None)
    vol_ma50 = _finite(latest_row.get("vol_ma50"), digits=None)
    if volume is None or not vol_ma50:
        return None
    return round(volume / vol_ma50, 2)


def _build_margin_metrics(code: str, latest_row: dict, margin_store: dict | None, config: dict) -> dict | None:
    try:
        from src.data.margin import build_margin_metrics

        metrics = build_margin_metrics(code, latest_row, store=margin_store)
    except Exception as e:
        print(f"WARNING: margin metrics build failed for {code} (ignored): {e}")
        return None
    if metrics is None:
        return None
    metrics["badge"] = margin_badge(metrics, config)
    return metrics


# 信用残バッジ(表示専用): dryupバッジ(_build_dryup_badge)と同じ「サーバ側で
# config閾値を見て判定文字列を確定し、フロントはそのまま表示するだけ」の流儀。
# positions.py の _margin_for でも使うのでモジュール関数として公開する。
def margin_badge(metrics: dict, config: dict) -> str | None:
    mcfg = config.get("margin", {})
    ratio = metrics.get("ratio")
    dtc = metrics.get("days_to_cover")
    high_ratio_warn = mcfg.get("high_ratio_warn", 5.0)
    dtc_warn = mcfg.get("dtc_warn", 3.0)
    low_ratio_info = mcfg.get("low_ratio_info", 1.0)
    if ratio is not None and ratio >= high_ratio_warn and dtc is not None and dtc >= dtc_warn:
        return "heavy_buy"  # 買残重い(warn)
    if ratio is not None and ratio <= low_ratio_info:
        return "short"  # 売り長・踏み上げ余地(accent)
    return None


def _build_dryup_badge(vcp_result: dict, config: dict) -> dict:
    """DRY-UPバッジ用レコード。

    バッジ値 dryup_med_10_50 は V5(a)診断の recent10_median/vol_ma50 と同一系列
    (indicators.dryup_metrics の定義と一致。base_df 末尾=full df 末尾のため tail(10)
    も一致する)。config.dryup の2段閾値で badge 種別(激枯れ/枯れ気味)を決める。
    VCP MUST・vcp_score には一切融合しない(「スコアは順位付け、フラグは事実」)。
    """
    diagnostics = vcp_result.get("vcp_diagnostics") or {}
    v5 = diagnostics.get("v5") or {}
    med = v5.get("recent10_median")
    vol_ma50 = v5.get("vol_ma50")
    value = round(med / vol_ma50, 4) if (med is not None and vol_ma50) else None

    d = config.get("dryup", {})
    strong_th = d.get("dryup_badge_strong", 0.66)  # ≒p25
    mild_th = d.get("dryup_badge_mild", 0.77)      # ≒p50
    if value is None:
        badge = None
    elif value <= strong_th:
        badge = "extreme"   # 激枯れ
    elif value <= mild_th:
        badge = "dryup"     # 枯れ気味
    else:
        badge = None
    return {"value": value, "badge": badge}


def _build_contraction_rows(vcp_result: dict) -> list[dict]:
    """収縮テーブル用の1行1収縮データ。

    footprint("11W 19/5/3 3T")は深さしか持たないので、どの収縮がいつ・何日かけて
    形成されたかが読めない。深さ5%でも29営業日かけた収縮と3日で終わった収縮では
    意味がまったく違うため、日付と営業日数をフロントに渡す。
    """
    rows = []
    for i, c in enumerate(vcp_result.get("contractions") or []):
        rows.append({
            "t": i + 1,
            "high_date": c.get("high_date"),
            "low_date": c.get("low_date"),
            "high_price": round(float(c["high_price"]), 2),
            "low_price": round(float(c["low_price"]), 2),
            "depth_pct": round(c["depth"] * 100, 1),
            # bars = 下落脚(高値→安値)の営業日数、rally_bars = 前収縮の安値から
            # この収縮の高値までの戻し脚の営業日数(先頭の収縮は None)。
            "bars": c.get("bars"),
            "rally_bars": c.get("rally_bars"),
            "provisional": bool(c.get("provisional", False)),
        })
    return rows


def _build_vcp_detail(vcp_result: dict, config: dict) -> dict:
    depths_pct = [round(c["depth"] * 100, 1) for c in vcp_result.get("contractions") or []]
    diagnostics = vcp_result.get("vcp_diagnostics") or {}
    v5_diag = diagnostics.get("v5") or {}
    return {
        "base_days": vcp_result.get("base_days"),
        "days_from_high": vcp_result.get("days_from_high"),
        "t0_date": str(vcp_result["t0_date"])[:10] if vcp_result.get("t0_date") is not None else None,
        "depths_pct": depths_pct,
        "contractions": _build_contraction_rows(vcp_result),
        "depth_last_pct": depths_pct[-1] if depths_pct else None,
        "last_depth_max_pct": round(config["vcp"]["last_depth_max"] * 100, 1),
        "volume_dryup": {
            "recent10_median": v5_diag.get("recent10_median"),
            "vol_ma50": v5_diag.get("vol_ma50"),
            "median_ratio_threshold": v5_diag.get("median_ratio_threshold"),
            "sub_a_pass": v5_diag.get("sub_a_pass"),
            "sub_b_pass": v5_diag.get("sub_b_pass"),
        },
        "shakeout_detected": vcp_result.get("shakeout_detected", False),
    }


# ---------------------------------------------------------------------------
# 監視(watchlist)分類: セットアップ進行度 (2026-07-17 新設)
# ---------------------------------------------------------------------------
# 監視タブ100件超を毎日全部見るのは不可能なので、VCP評価の非アクショナブル
# ステータス+診断値から「今どの段階で、何が足りないか」を機械分類する。
# stage:
#   forming    = IMMATURE (ベース形成中。base_min_daysまでの残日数を出す)
#   fresh_high = TOO_RECENT (高値更新直後でベース自体が未開始)
#   rejected   = REJECTED (ベースはあるがV1〜V7のどれかで不合格。missingに列挙)
#   volatile   = TOO_VOLATILE (ATR過大で評価対象外)
#   no_base    = NO_BASE (スキャン窓に基準高値なし)
# near: 「あと一歩」フラグ。forming で残日数<=near_days、rejected で未達フラグが
#       ちょうど1個のときTrue。フロントはこのグループだけ最上段に出す。

SETUP_STAGE_NEAR_DAYS_DEFAULT = 5


def build_setup_stage(vcp_result: dict, config: dict | None = None) -> dict | None:
    """非アクショナブルVCP結果を進行度ステージへ分類する。actionableならNone。"""
    config = config or load_config()
    status = vcp_result.get("status")
    vcp_cfg = config.get("vcp", {})

    if status == "IMMATURE":
        base_min = vcp_cfg.get("base_min_days", 15)
        near_days = vcp_cfg.get("setup_stage_near_days", SETUP_STAGE_NEAR_DAYS_DEFAULT)
        bd = vcp_result.get("base_days") or 0
        remain = max(0, base_min - bd)
        return {
            "stage": "forming",
            "near": remain <= near_days,
            "missing": [],
            "detail": f"ベース{bd}日目 (最短{base_min}日まであと{remain}日)",
        }
    if status == "TOO_RECENT":
        dfh = vcp_result.get("days_from_high")
        suffix = f" (高値から{dfh}日)" if dfh is not None else ""
        return {
            "stage": "fresh_high",
            "near": False,
            "missing": [],
            "detail": f"高値更新直後・押し待ち{suffix}",
        }
    if status == "REJECTED":
        flags = vcp_result.get("must_flags") or {}
        missing = [k for k, v in flags.items() if not v]
        return {
            "stage": "rejected",
            "near": len(missing) == 1,
            "missing": missing,
            "detail": "VCP未達: " + "/".join(missing) if missing else "VCP未達",
        }
    if status == "TOO_VOLATILE":
        return {
            "stage": "volatile",
            "near": False,
            "missing": [],
            "detail": "ボラティリティ過大 (評価対象外)",
        }
    if status == "NO_BASE":
        return {
            "stage": "no_base",
            "near": False,
            "missing": [],
            "detail": "基準となる高値/ベースなし",
        }
    return None  # WATCH_A/B・BREAKOUT等のactionableステータス


def attach_priority(record: dict, priority_eval: dict | None) -> dict:
    """機能A: プライオリティ評価結果をレコードにマージする。"""
    if priority_eval is None:
        return record
    record["priority"] = priority_eval["priority"]
    record["priority_penalty"] = priority_eval["penalty"]
    record["priority_unmet"] = priority_eval["unmet"]
    record["ma_deviation_pct"] = priority_eval["ma_deviation_pct"]
    record["high52w_distance_pct"] = priority_eval["high52w_distance_pct"]
    return record


def _sort_key(stock: dict) -> tuple:
    """総合スコア降順の単一軸(2026-07-29改定)。

    旧実装は tier_rank を第1キーにし、watchlist だけ priority → セクター強度 → RS
    という別軸でソートしていた。VCP欠損を0扱いにして total_score の意味を全ティアで
    揃えたので(combined_score 参照)、ティアで層別する必要がなくなった。ティア自体は
    バッジ表示用にレコードへ残る。log.md (143)。

    同点処理のみ status_rank を使う(ブレイク > 待機 > 形成中の順)。
    """
    score = stock.get("total_score") or 0.0
    status_rank = STATUS_ORDER.get(stock["status"], 99)
    return (-score, status_rank, stock.get("code") or "")


def _fill_days_to_earnings(stocks: list[dict]) -> None:
    """決算までの日数が空のレコードを、書き出し直前に埋める。

    次回決算発表予定日はレコードを組み立てた後から差し込まれる経路がある
    (決算カレンダーを別に引いて record に載せる)。その経路で入った銘柄は
    組み立て時点では予定日が分からず日数を出せないので、どちらの経路で入っても
    report.json には日数が載っている状態にしてから書き出す。基準日はレコード自身の
    日付なので、ここで実行日を持ち込むことはしない。
    """
    for stock in stocks:
        if stock.get("days_to_earnings") is None and stock.get("next_earnings_date"):
            stock["days_to_earnings"] = days_to_earnings(
                stock.get("next_earnings_date"), stock.get("date")
            )


def build_report(
    stocks: list[dict],
    universe_size: int,
    template_pass: int,
    data_warnings: dict | None = None,
    generated_at: str | None = None,
    priority_counts: dict | None = None,
    p1_scarce: bool | None = None,
    source_freshness: dict | None = None,
    session: str | None = None,
    snapshot_suffix: str = "",
) -> dict:
    """report.json を組み立てて書き出す。

    source_freshness (2026-07-17追加、省略可=後方互換): データソースごとの
    最終成功日 {"jquants": {"last_success": ...}, "edinetdb": {...}, "prices": {...}}。
    パイプライン側 (pipeline.run_daily) が state ファイルから組み立てて渡す。

    session (2026-07-21追加、省略可): "前場"/"後場(ザラ場中)"/"引け後"。
    前場終了バッチと日次バッチのどちらが生成したスナップショットかをフロントの
    前場/後場コピーボタンが表示するための表示専用ラベル。未指定なら実行時刻(JST)
    から market_session() で自動判定する。

    snapshot_suffix (2026-07-21追加、省略可): "" 以外なら canonical report.json では
    なく report{suffix}.json へ書き出す(スナップショット方式)。前場終了バッチが
    EOD の report.json を上書きせず、前場断面を独立ファイルとして残すために使う。
    """
    ordered = sorted(stocks, key=_sort_key)
    _fill_days_to_earnings(ordered)
    report = {
        "generated_at": generated_at or datetime.now().astimezone().isoformat(),
        "market_session": session or market_session(),
        "universe_size": universe_size,
        "template_pass": template_pass,
        "priority_counts": priority_counts,
        "p1_scarce": p1_scarce,
        "data_warnings": data_warnings or {
            "failed_tickers": [], "stale_tickers": [], "csv_errors": [],
            "fundamentals_mismatch": [],
        },
        "source_freshness": source_freshness,
        "stocks": ordered,
    }
    path = REPORT_PATH if not snapshot_suffix else DOCS_DATA_DIR / f"report{snapshot_suffix}.json"
    write_docs_json(path, report)
    return report


# ---------------------------------------------------------------------------
# 6. Breadth meter
# ---------------------------------------------------------------------------

def load_breadth() -> dict:
    return read_docs_json(BREADTH_PATH, default={"history": []})


def compute_breakout_success_rate(
    status_history: dict, lookback_days: int = 20, hold_days: int = 5
) -> float | None:
    """Share of BREAKOUT events (in the trailing `lookback_days` entries per
    code) that were still above their pivot `hold_days` trading days later.

    Uses status alone as a proxy: BREAKOUT/BREAKOUT_WEAK/EXTENDED imply
    close > pivot, while WATCH_A implies the stock fell back below pivot.
    STALE(2026-07-21追加)はピボットの上下どちらでも付き得る(鮮度切れは終値位置に
    依らない)ため上下の代理情報を持たない → 母数に数えず判定不能としてスキップする。
    """
    successes = 0
    total = 0
    for entries in status_history.values():
        n = len(entries)
        start = max(0, n - lookback_days - hold_days)
        for i in range(start, n - hold_days):
            if entries[i]["status"] == "BREAKOUT":
                later_status = entries[i + hold_days]["status"]
                if later_status == "STALE":
                    continue  # 上下不定: 成功にも失敗にも数えない
                total += 1
                if later_status in ("BREAKOUT", "BREAKOUT_WEAK", "EXTENDED"):
                    successes += 1
    if total == 0:
        return None
    return round(successes / total, 3)


def update_breadth(
    date_str: str,
    universe_size: int,
    template_pass: int,
    watch_count: int,
    status_history: dict,
    keep_days: int = 60,
    priority_counts: dict | None = None,
    market_signal: dict | None = None,
    vcp_funnel: dict | None = None,
    stage_funnel: dict | None = None,
    snapshot_suffix: str = "",
) -> dict:
    # snapshot_suffix 指定時は canonical breadth.json の履歴をベースに当日エントリを
    # 足した断面を breadth{suffix}.json へ書くだけで、canonical breadth.json は触らない
    # (前場スナップショットが EOD の地合い履歴を汚さないようにするため)。
    breadth = load_breadth()
    entry = {
        "date": date_str,
        "universe_size": universe_size,
        "template_pass": template_pass,
        "template_pass_rate": round(template_pass / universe_size, 4) if universe_size else None,
        "watch_count": watch_count,
        "breakout_success_rate": compute_breakout_success_rate(status_history),
    }
    if priority_counts is not None:
        # 機能A: P1〜P4件数を地合い指標として毎回記録
        entry.update(
            {
                "p1_count": priority_counts.get("p1", 0),
                "p2_count": priority_counts.get("p2", 0),
                "p3_count": priority_counts.get("p3", 0),
                "p4_count": priority_counts.get("p4", 0),
            }
        )
    if market_signal is not None:
        entry.update(market_signal)
    if vcp_funnel is not None:
        # VCP評価対象(P1)の origin/status 分布を地合い観測用に記録。
        # 二段目リーダーが高値更新中(TOO_RECENT)で土俵に乗らない比率などを追う。
        entry["vcp_funnel"] = vcp_funnel
    if stage_funnel is not None:
        # 監視タブのバケット別内訳 (src/report/stage_log.py)。vcp_funnel は
        # VCP評価時点の status なので EXTENDED/STALE の上書き前で report.json と
        # 数件ずれる。こちらは stock_records から数えるので画面と一致する。
        entry["stage_funnel"] = stage_funnel
    breadth["history"] = [h for h in breadth["history"] if h.get("date") != date_str]
    breadth["history"].append(entry)
    breadth["history"] = breadth["history"][-keep_days:]
    path = BREADTH_PATH if not snapshot_suffix else DOCS_DATA_DIR / f"breadth{snapshot_suffix}.json"
    write_docs_json(path, breadth)
    return breadth


def snapshot_docs_json(name: str, snapshot_suffix: str) -> None:
    """docs/data/{name}.json を {name}{suffix}.json へ複製する(暗号化封筒を保つ)。

    前場スナップショット方式で、パイプライン内で個別書き出し口を持たない
    docs/data ファイル(例: indices.json)を断面として固定するために使う。
    read_docs_json→write_docs_json を経由するので、鍵ありなら再暗号化される。
    """
    if not snapshot_suffix:
        return
    src = DOCS_DATA_DIR / f"{name}.json"
    obj = read_docs_json(src, default=None)
    if obj is None:
        return
    write_docs_json(DOCS_DATA_DIR / f"{name}{snapshot_suffix}.json", obj)


# ---------------------------------------------------------------------------
# 7.4 per-stock chart data
# ---------------------------------------------------------------------------

def _series_points(df: pd.DataFrame, col: str) -> list[dict]:
    if col not in df.columns:
        return []
    out = []
    for row in df.itertuples(index=False):
        value = getattr(row, col)
        if pd.isna(value):
            continue
        out.append({"time": row.date.strftime("%Y-%m-%d"), "value": round(float(value), 4)})
    return out


def _earnings_markers(recent: pd.DataFrame, fund_entry: dict | None) -> list[dict]:
    """表示期間内に収まる決算発表(開示日)を、チャートのバー日付にスナップして返す。

    fund_entry["quarters"] の disc_date(J-Quants開示日)のうち、表示中のローソク
    期間 [最古日, 最新日] に入るものだけを対象にする。開示は場中/引け後どちらも
    あり得るが値動きの反応は開示日当日〜翌営業日なので、開示日以上で最も近い
    バー日付(=反応日)にスナップする。該当が無ければ開示日以下で最も近いバーに
    フォールバック。売買日でない開示(休日発表等)でもマーカーが宙に浮かない。
    戻り値は time 昇順でユニーク(同一営業日に複数開示が重なっても1本)。
    """
    if not fund_entry:
        return []
    quarters = fund_entry.get("quarters") or []
    if len(recent) == 0:
        return []
    bar_dates = [row.date.strftime("%Y-%m-%d") for row in recent.itertuples(index=False)]
    lo, hi = bar_dates[0], bar_dates[-1]
    by_time: dict[str, dict] = {}
    for q in quarters:
        disc = q.get("disc_date")
        if not disc or disc < lo or disc > hi:
            continue
        # 開示日以上で最も近いバー(反応日)。無ければ以下で最も近いバー。
        snapped = next((d for d in bar_dates if d >= disc), None)
        if snapped is None:
            snapped = next((d for d in reversed(bar_dates) if d <= disc), None)
        if snapped is None:
            continue
        # 同一バーに複数四半期が重なったら開示日が新しい方を残す。
        prev = by_time.get(snapped)
        if prev is None or (q.get("disc_date") or "") >= (prev.get("disc_date") or ""):
            by_time[snapped] = {
                "time": snapped,
                "quarter": q.get("fiscal_quarter"),
                "disc_date": disc,
                "eps": q.get("eps"),
            }
    return [by_time[t] for t in sorted(by_time)]


def build_chart_data(code: str, df: pd.DataFrame, vcp_result: dict, entry_result: dict,
                     lookback_days: int = 260, fund_entry: dict | None = None) -> dict:
    recent = df.tail(lookback_days).reset_index(drop=True)

    candles = [
        {
            "time": row.date.strftime("%Y-%m-%d"),
            "open": round(float(row.open), 2),
            "high": round(float(row.high), 2),
            "low": round(float(row.low), 2),
            "close": round(float(row.close), 2),
        }
        for row in recent.itertuples(index=False)
    ]
    volume = [
        {"time": row.date.strftime("%Y-%m-%d"), "value": float(row.volume)}
        for row in recent.itertuples(index=False)
    ]

    # t(収縮番号)と bars/rally_bars を載せる。フロントは swing_high の出現回数から
    # 番号を振っていたが、それだとマージ済みの列と表の行番号がずれ得るので、
    # 生成側で確定させた番号をそのまま使えるようにする(旧JSON互換のため
    # フロント側のカウンタは残す)。
    markers = []
    for i, c in enumerate(vcp_result.get("contractions", []) or []):
        markers.append({"type": "swing_high", "time": c.get("high_date"),
                        "price": round(float(c["high_price"]), 2),
                        "t": i + 1, "bars": c.get("bars"),
                        "rally_bars": c.get("rally_bars")})
        markers.append({"type": "swing_low", "time": c.get("low_date"),
                        "price": round(float(c["low_price"]), 2),
                        "t": i + 1, "bars": c.get("bars"),
                        "rally_bars": c.get("rally_bars")})

    return {
        "code": code,
        "candles": candles,
        "volume": volume,
        "ma50": _series_points(recent, "ma50"),
        "ma150": _series_points(recent, "ma150"),
        "ma200": _series_points(recent, "ma200"),
        "rs_line": _series_points(recent, "rs_line"),
        "pivot": entry_result.get("pivot"),
        "stop_loss": entry_result.get("stop_loss"),
        "markers": markers,
        # IMMATURE(ベース熟成中)の形成途中ラインはフロントで破線表示に切り替える。
        "vcp_forming": vcp_result.get("status") == "IMMATURE",
        "earnings": _earnings_markers(recent, fund_entry),
    }


def write_chart_data(code: str, chart_data: dict) -> None:
    write_docs_json(CHARTS_DIR / f"{code}.json", chart_data)


# ---------------------------------------------------------------------------
# Static asset placeholders (index.html/stock.html/assets/* are static files
# maintained directly in the repo; this just guarantees the data/ directory
# tree exists so the pages don't 404 on a clean checkout before the first run)
# ---------------------------------------------------------------------------

def ensure_data_dir_exists() -> None:
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    if not REPORT_PATH.exists():
        build_report(stocks=[], universe_size=0, template_pass=0)
    if not BREADTH_PATH.exists():
        write_docs_json(BREADTH_PATH, {"history": []})
