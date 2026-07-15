"""簡易バックテスト(フェーズ1): ブレイクアウト成功率の実績検証CLI。

    python -m src.backtest [--days 400] [--limit 20] [--rs-min N] [--vol-mult N] [--stop-pct N]

rs_min / breakout_vol_mult / stop_loss_pct 等のパラメータは一度も実績検証されていない。
data/prices/ の日足キャッシュ(520営業日分)を使ってイベントスタディを行い、
「VCPセットアップがそもそもどれだけ検出されるか」「ブレイク後の成績」を定量化する。

【スコープ(フェーズ1に限定)】
完全なウォークフォワード(RSパーセンタイルのpoint-in-time再計算 + VCPの全日付スキャン)は
計算量が膨大なため、以下の簡易版に限定する:
- RS: 全銘柄のrs_rawを日付×銘柄でピボットし、各日付の行でrank(pct=True)して1-99に変換する
  近似(indicators.rs_percentile_rankと同じ式)。ma50/ma150/ma200/atr20/52w高安と同様、
  rs_rawはbackward-lookingなrolling計算のみで構成されているため、フルhistoryを事前計算してから
  任意の日付で読んでも未来データの混入は起きない。
- VCPスキャン: 全日付ではなく週次(5営業日ごと)の日付グリッドで、トレンドテンプレート合格
  かつRS>=rs_minの銘柄のみを対象に evaluate_vcp を実行(計算量削減)。

【ルックアヘッドバイアス対策】
evaluate_vcp に渡す df は必ず `df.iloc[:i+1]` のようにその日までにスライスする。
ma/atr/52w高安/rs_raw は全て backward-looking な rolling 計算のみで構成されているため、
フルhistoryに対して事前に compute_all() を1回走らせてからスライスしても未来データは
混入しない(スライスした結果とスライス前に計算した値は各行で完全に一致する)。

【既知の限界】
- ユニバースの生存者バイアス: 現在のユニバース(data/universe.json)で過去を見るため、
  当時ユニバースに存在しなかった/その後上場廃止した銘柄は対象外。
- RSの母集団はこのバックテストで読み込めた銘柄のみ(本番パイプラインのユニバース全体より
  小さくなりうる、--limit指定時は特に)。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import REPO_ROOT, load_config
from src.data import prices as prices_mod
from src.indicators import build_dryup_layer, compute_all
from src.screener import trend_template
from src.screener import vcp as vcp_mod
from src.universe import load_universe

BACKTEST_DIR = REPO_ROOT / "data" / "backtest"
SCAN_STEP = 1  # スキャン粒度(営業日)。1=日次(既定)、5=週次グリッド近似。
MAX_BREAKOUT_WAIT_DAYS = 60  # セットアップ後、ブレイクを待つ上限
HORIZON_DAYS = (5, 10, 20)
PIVOT_DEDUPE_TOLERANCE = 0.01  # 同一銘柄でこの割合以内のpivotは1件に統合


def load_universe_frames(limit: int | None = None) -> dict[str, pd.DataFrame]:
    """data/universe.json の銘柄について data/prices/{code}.parquet を読み、
    指標を付与する。キャッシュが無い銘柄はスキップ。"""
    universe = load_universe()
    codes = [s["code"] for s in universe["stocks"]]
    if limit is not None:
        codes = codes[:limit]

    frames: dict[str, pd.DataFrame] = {}
    for code in codes:
        df = prices_mod.load_cache(code)
        if df is None or len(df) < 260:  # RS_LOOKBACKS最大252に満たない銘柄は評価不能
            continue
        frames[code] = compute_all(df.reset_index(drop=True))
    return frames


def build_rs_by_date(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """date x code のRS(1-99パーセンタイル)テーブル。indicators.rs_percentile_rankと同じ式。"""
    raw_by_code = {code: df.set_index("date")["rs_raw"] for code, df in frames.items()}
    wide = pd.DataFrame(raw_by_code)
    pct = wide.rank(axis=1, pct=True)
    return (pct * 98 + 1).round().clip(1, 99)


def scan_setups(
    code: str,
    df: pd.DataFrame,
    rs_series: pd.Series,
    config: dict,
    rs_min: float,
    start_idx: int,
    step: int = SCAN_STEP,
) -> list[dict]:
    """日次(step=1)グリッドでトレンドテンプレート合格+RS>=rs_minの銘柄のみVCPを
    スキャンし、WATCH_A(ピボット確定)のセットアップを記録する。同一銘柄でpivotが
    近い(±1%以内)セットアップは1件に統合する。

    各セットアップの検出日時点で枯れ度レイヤー(build_dryup_layer)を記録する
    (indicatorsの共通関数から取得。バックテスト側で再実装しない)。"""
    n = len(df)
    setups: list[dict] = []
    seen_pivots: list[float] = []

    for i in range(start_idx, n, step):
        row = df.iloc[i]
        if pd.isna(row.get("ma200")) or pd.isna(row.get("ma50")) or pd.isna(row.get("high_52w")):
            continue

        date = row["date"]
        rs = rs_series.get(date)
        if rs is None or pd.isna(rs) or rs < rs_min:
            continue

        latest = row.to_dict()
        latest["rs"] = rs
        flags = trend_template.check_must_conditions(latest, config)
        if not trend_template.passes_trend_template(flags):
            continue

        vcp_result = vcp_mod.evaluate_vcp(df.iloc[: i + 1], config)
        if vcp_result.get("status") != "WATCH_A" or not vcp_result.get("contractions"):
            continue

        last_c = vcp_result["contractions"][-1]
        pivot = last_c["high_price"]
        if any(abs(pivot - p) / p <= PIVOT_DEDUPE_TOLERANCE for p in seen_pivots):
            continue
        seen_pivots.append(pivot)

        base_days = vcp_result.get("base_days")
        base_start_idx = (i - base_days + 1) if base_days else None
        dryup_setup = build_dryup_layer(
            df, i, base_start_idx, pivot,
            shakeout_detected=bool(vcp_result.get("shakeout_detected")),
        )

        setups.append(
            {
                "code": code,
                "setup_date": date,
                "setup_idx": i,
                "pivot": pivot,
                "stop_ref_low": last_c["low_price"],
                "vcp_score": vcp_result.get("vcp_score"),
                "dryup_setup": dryup_setup,
            }
        )

    return setups


def find_breakout_index(df: pd.DataFrame, setup_idx: int, pivot: float, max_wait_days: int = MAX_BREAKOUT_WAIT_DAYS) -> int | None:
    """セットアップ後、closeが最初にpivotを上抜けた行のインデックス。max_wait_days以内に
    無ければNone(不発)。"""
    n = len(df)
    end = min(n, setup_idx + 1 + max_wait_days)
    for j in range(setup_idx + 1, end):
        if df.iloc[j]["close"] > pivot:
            return j
    return None


def is_strong_breakout(df: pd.DataFrame, breakout_idx: int, vol_mult: float) -> bool:
    row = df.iloc[breakout_idx]
    vol_ma50 = row.get("vol_ma50")
    if not vol_ma50 or pd.isna(vol_ma50):
        return False
    return bool((row["volume"] / vol_ma50) >= vol_mult)


def measure_performance(
    df: pd.DataFrame,
    breakout_idx: int,
    stop_loss_pct: float,
    horizons: tuple[int, ...] = HORIZON_DAYS,
) -> dict:
    """ブレイク日の翌日始値(無ければブレイク日終値)を仮エントリー価格とし、終値ベースの
    損切り・+N営業日後リターン・最大ドローダウン・ストップ到達率を測る。

    損切り後は建玉を閉じた扱いとし、それ以降のホライズンのリターンは損切り価格で固定する
    (損切り後も保有し続けた場合の含み損益は測らない)。
    """
    n = len(df)
    if breakout_idx + 1 < n:
        entry_idx = breakout_idx + 1
        entry_price = float(df.iloc[entry_idx]["open"])
    else:
        entry_idx = breakout_idx
        entry_price = float(df.iloc[entry_idx]["close"])

    stop_price = entry_price * (1 - stop_loss_pct)
    max_horizon = max(horizons)
    end = min(n, entry_idx + max_horizon + 1)

    stop_hit_idx = None
    min_close = entry_price
    for j in range(entry_idx, end):
        close = float(df.iloc[j]["close"])
        min_close = min(min_close, close)
        if close < stop_price:
            stop_hit_idx = j
            break

    returns: dict[int, float | None] = {}
    for h in horizons:
        idx = entry_idx + h
        if stop_hit_idx is not None and stop_hit_idx <= min(idx, end - 1):
            exit_close = float(df.iloc[stop_hit_idx]["close"])
            returns[h] = round((exit_close / entry_price - 1) * 100, 2)
        elif idx < n:
            returns[h] = round((float(df.iloc[idx]["close"]) / entry_price - 1) * 100, 2)
        else:
            returns[h] = None

    exit_price = float(df.iloc[stop_hit_idx]["close"]) if stop_hit_idx is not None else float(df.iloc[min(entry_idx + max_horizon, n - 1)]["close"])
    risk_per_share = entry_price - stop_price
    r_multiple = round((exit_price - entry_price) / risk_per_share, 2) if risk_per_share else None

    return {
        "entry_idx": entry_idx,
        "entry_price": round(entry_price, 2),
        "stop_price": round(stop_price, 2),
        "stop_hit": stop_hit_idx is not None,
        "max_drawdown_pct": round((min_close / entry_price - 1) * 100, 2),
        "returns": returns,
        "r_multiple": r_multiple,
    }


def _entry_price(df: pd.DataFrame, breakout_idx: int) -> float:
    """measure_performance と同じ仮エントリー価格(ブレイク翌日始値、無ければ当日終値)。"""
    n = len(df)
    if breakout_idx + 1 < n:
        return float(df.iloc[breakout_idx + 1]["open"])
    return float(df.iloc[breakout_idx]["close"])


def run_backtest(days: int, limit: int | None, rs_min: float, vol_mult: float, stop_pct: float, config: dict, step: int = SCAN_STEP) -> dict:
    frames = load_universe_frames(limit)
    if not frames:
        return {"setups": [], "codes_scanned": 0, "params": _params(days, rs_min, vol_mult, stop_pct)}

    rs_by_date = build_rs_by_date(frames)
    extended_pct = config["entry"]["extended_pct"]

    all_setups: list[dict] = []
    for code, df in frames.items():
        n = len(df)
        start_idx = max(0, n - days)
        rs_series = rs_by_date[code] if code in rs_by_date.columns else pd.Series(dtype="float64")
        setups = scan_setups(code, df, rs_series, config, rs_min, start_idx, step=step)

        for setup in setups:
            breakout_idx = find_breakout_index(df, setup["setup_idx"], setup["pivot"])
            setup["breakout"] = breakout_idx is not None
            if breakout_idx is None:
                continue
            setup["breakout_idx"] = breakout_idx
            setup["breakout_date"] = df.iloc[breakout_idx]["date"]
            setup["strong"] = is_strong_breakout(df, breakout_idx, vol_mult)

            # ブレイク日時点の枯れ度も記録(設定日 vs ブレイク日の比較用)。
            # base_start_idx は None(ブレイク時点でベース起点は再計算しない)。
            setup["dryup_breakout"] = build_dryup_layer(
                df, breakout_idx, None, setup["pivot"],
                shakeout_detected=bool(setup["dryup_setup"].get("shakeout_detected")),
            )

            # EXTENDED除外: 仮エントリー価格が pivot*(1+extended_pct) を超える
            # (=ギャップアップで既に伸びすぎ・追いかけ不可)ブレイクは成績集計から除外する。
            entry_price = _entry_price(df, breakout_idx)
            if entry_price > setup["pivot"] * (1 + extended_pct):
                setup["extended_skip"] = True
                continue
            setup["extended_skip"] = False
            setup.update(measure_performance(df, breakout_idx, stop_pct))

        all_setups.extend(setups)

    # 地合いガード分離用: 各実測setupのエントリー日TOPIX騰落率(%)を付与する。
    _attach_entry_benchmark_return(all_setups, frames, config)

    return {
        "setups": all_setups,
        "codes_scanned": len(frames),
        "params": _params(days, rs_min, vol_mult, stop_pct),
    }


def _load_benchmark_returns(config: dict) -> pd.Series | None:
    """TOPIXプロキシ(既定1306)の日次騰落率(%)を data/prices キャッシュから読む。
    ネット取得はしない(バックテストと同じ決定的データ源)。取得不能なら None。"""
    try:
        ticker = config["data"]["topix_proxy_ticker"].split(".")[0]
        bpath = REPO_ROOT / "data" / "prices" / f"{ticker}.parquet"
        bdf = pd.read_parquet(bpath).sort_values("date")
        return (bdf.set_index("date")["close"].pct_change() * 100)
    except Exception:  # noqa: BLE001
        return None


def _attach_entry_benchmark_return(setups: list[dict], frames: dict[str, pd.DataFrame], config: dict) -> None:
    bench_ret = _load_benchmark_returns(config)
    for s in setups:
        s["entry_topix_ret"] = None
        if bench_ret is None:
            continue
        df = frames.get(s["code"])
        ei = s.get("entry_idx")
        if df is None or ei is None or ei >= len(df):
            continue
        d = df.iloc[ei]["date"]
        if d in bench_ret.index and pd.notna(bench_ret.loc[d]):
            s["entry_topix_ret"] = float(bench_ret.loc[d])


def _params(days: int, rs_min: float, vol_mult: float, stop_pct: float) -> dict:
    return {"days": days, "rs_min": rs_min, "breakout_vol_mult": vol_mult, "stop_loss_pct": stop_pct}


# ---------------------------------------------------------------------------
# レポート組み立て
# ---------------------------------------------------------------------------

def _setups_by_month(setups: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for s in setups:
        month = pd.Timestamp(s["setup_date"]).strftime("%Y-%m")
        counts[month] += 1
    return dict(sorted(counts.items()))


def _perf_stats(setups: list[dict], horizon: int) -> dict:
    returns = [s["returns"][horizon] for s in setups if s.get("returns", {}).get(horizon) is not None]
    if not returns:
        return {"mean": None, "median": None, "win_rate": None, "n": 0}
    s = pd.Series(returns)
    return {
        "mean": round(float(s.mean()), 2),
        "median": round(float(s.median()), 2),
        "win_rate": round(float((s > 0).mean() * 100), 1),
        "n": len(returns),
    }


MIN_BUCKET_N = 10  # これ未満のバケットは「参考値」とし結論に使わない


def _setup_metric(setup: dict, key: str):
    return (setup.get("dryup_setup") or {}).get(key)


def _bucket_med(v):
    if v is None:
        return None
    if v < 0.4:
        return "<0.4"
    if v < 0.6:
        return "0.4-0.6"
    if v < 0.8:
        return "0.6-0.8"
    return ">=0.8"


def _bucket_tight(v):
    if v is None:
        return None
    if v <= 0.05:
        return "<=0.05"
    if v <= 0.08:
        return "0.05-0.08"
    return ">0.08"


def _bucket_stats(setups: list[dict]) -> dict:
    """バケット内の setups について、ブレイク到達率 / 期待R / ストップ到達率 / n を出す。
    到達率は全 setups 母数。期待R・ストップ率は EXTENDED除外後の実測ブレイクのみ。"""
    n = len(setups)
    broke = [s for s in setups if s.get("breakout")]
    measured = [s for s in broke if not s.get("extended_skip") and s.get("r_multiple") is not None]
    r_vals = [s["r_multiple"] for s in measured]
    return {
        "n": n,
        "n_measured": len(measured),
        "reach": round(len(broke) / n * 100, 1) if n else None,
        "expected_r": round(sum(r_vals) / len(r_vals), 2) if r_vals else None,
        "stop_rate": round(sum(1 for s in measured if s.get("stop_hit")) / len(measured) * 100, 1) if measured else None,
    }


def _stratify(setups: list[dict], bucket_of, ordered_labels: list) -> list[tuple]:
    groups: dict = defaultdict(list)
    for s in setups:
        label = bucket_of(s)
        if label is not None:
            groups[label].append(s)
    rows = []
    for label in ordered_labels:
        rows.append((label, _bucket_stats(groups.get(label, []))))
    return rows


def _render_strata(title: str, rows: list[tuple]) -> list[str]:
    lines = [f"### {title}"]
    for label, st in rows:
        if st["n"] == 0:
            lines.append(f"- {label}: n=0")
            continue
        ref = "  ※参考値(n<10)" if st["n"] < MIN_BUCKET_N else ""
        lines.append(
            f"- {label}: 到達率{st['reach']}% / 期待R{st['expected_r']} / "
            f"ストップ率{st['stop_rate']}% / n={st['n']}(実測{st['n_measured']}){ref}"
        )
    return lines


def _percentiles(values: list[float], ps=(10, 25, 50, 75, 90)) -> dict:
    if not values:
        return {p: None for p in ps}
    arr = pd.Series(values, dtype="float64")
    return {p: round(float(arr.quantile(p / 100)), 4) for p in ps}


def _build_dryup_section(setups: list[dict]) -> list[str]:
    """枯れ度レイヤーの層別分析(§2)+閾値の答え合わせ(§3)。"""
    lines = [
        "",
        "## 枯れ度(DRY-UP)レイヤー層別分析",
        "",
        f"枯れ度は設定日時点の値で層別。到達率=ブレイク到達/バケット内setups、期待R・"
        f"ストップ率=EXTENDED除外後の実測ブレイクのみ。n<{MIN_BUCKET_N}は参考値(結論に使わない)。",
        "",
    ]
    lines += _render_strata(
        "a. dryup_med_10_50 バケット(中央値版)",
        _stratify(setups, lambda s: _bucket_med(_setup_metric(s, "dryup_med_10_50")),
                  ["<0.4", "0.4-0.6", "0.6-0.8", ">=0.8"]),
    )
    lines.append("")
    lines += _render_strata(
        "b. dryup_avg_5_50 バケット(平均版・予測力比較用)",
        _stratify(setups, lambda s: _bucket_med(_setup_metric(s, "dryup_avg_5_50")),
                  ["<0.4", "0.4-0.6", "0.6-0.8", ">=0.8"]),
    )
    lines.append("")
    lines += _render_strata(
        "c. tightness_10d バケット",
        _stratify(setups, lambda s: _bucket_tight(_setup_metric(s, "tightness_10d")),
                  ["<=0.05", "0.05-0.08", ">0.08"]),
    )
    lines.append("")
    lines += _render_strata(
        "d. shakeout_detected",
        _stratify(setups, lambda s: "true" if _setup_metric(s, "shakeout_detected") else "false",
                  ["true", "false"]),
    )
    lines.append("")

    def _composite(s):
        med = _setup_metric(s, "dryup_med_10_50")
        tight = _setup_metric(s, "tightness_10d")
        if med is None or tight is None:
            return None
        return "激枯れ×タイト" if (med < 0.6 and tight <= 0.08) else "それ以外"

    lines += _render_strata(
        "e. 複合(dryup_med<0.6 × tightness<=0.08)vs それ以外",
        _stratify(setups, _composite, ["激枯れ×タイト", "それ以外"]),
    )
    lines.append("")

    # §3 閾値の答え合わせ: dryup_med_10_50 の分布と段階閾値の提案。
    med_values = [v for v in (_setup_metric(s, "dryup_med_10_50") for s in setups) if v is not None]
    pct = _percentiles(med_values)
    lines += [
        "## 閾値の答え合わせ(dryup_med_10_50 分布)",
        "",
        f"- セットアップ数(値あり): {len(med_values)}件",
        f"- パーセンタイル: p10={pct[10]} / p25={pct[25]} / p50={pct[50]} / p75={pct[75]} / p90={pct[90]}",
        "",
        "### 東証向け段階閾値の提案(採用判断は人間)",
    ]
    if med_values:
        lines += [
            f"- 「激枯れ」= dryup_med_10_50 <= p25({pct[25]}) …… 下位25%の最も枯れた群",
            f"- 「枯れ気味」= p25({pct[25]}) < dryup_med_10_50 <= p50({pct[50]}) …… 中央値以下",
            f"  (米株仮置きの固定0.4/0.6と比較し、東証の実分布中央値は {pct[50]}。"
            f"固定閾値が分布のどこに当たるかを上のパーセンタイルで確認のこと)",
            "- ※configは変更していない。上記は提案のみ。層別分析のバケット別成績と"
            "併せて、中央値版・平均版が同方向かつ n>=10 を満たす帯のみ採用検討すること。",
        ]
    else:
        lines.append("- 値ありセットアップが無く分布を出せない。")
    lines.append("")
    return lines


def _build_market_guard_section(measured: list[dict], config: dict) -> list[str]:
    """地合いガード分離(2026-07-15確定反映): エントリー日のTOPIX騰落率が
    market_guard_pct(既定-1.5%)以下のブレイクを「地合い警告日ブレイク」として
    常に別集計し、成績本体から分離する。本番status判定は既存の警告バナー仕様の
    ままで、MUST除外にはしない(ここはバックテスト集計上の分離のみ)。"""
    guard_pct = config["entry"].get("market_guard_pct", -0.015) * 100  # % に換算
    with_ret = [s for s in measured if s.get("entry_topix_ret") is not None and s.get("r_multiple") is not None]

    def _er(group):
        rv = [s["r_multiple"] for s in group]
        return (round(sum(rv) / len(rv), 2) if rv else None, len(group))

    lines = [
        "## 地合い警告日ブレイクの分離(成績本体から分離)",
        "",
        f"エントリー日のTOPIX騰落率 <= market_guard_pct({guard_pct:.1f}%)の実測ブレイクを"
        "「地合い警告日」として分離。強ブレイク劣位の主因がこの混入かを確認する集計。",
        "",
    ]
    if not with_ret:
        lines += ["- TOPIXベンチマーク未取得のため分離できず。", ""]
        return lines

    guard = [s for s in with_ret if s["entry_topix_ret"] <= guard_pct]
    body = [s for s in with_ret if s["entry_topix_ret"] > guard_pct]
    er_all, n_all = _er(with_ret)
    er_guard, n_guard = _er(guard)
    er_body, n_body = _er(body)
    # 参考: 強ブレイクに限った内訳(4bの-1%超の所見と接続)。
    g_strong = [s for s in guard if s.get("strong")]
    er_gs, n_gs = _er(g_strong)

    lines += [
        f"- 実測ブレイク(TOPIX取得済) n={n_all}: 期待R {er_all}",
        f"  - **地合い警告日(<= {guard_pct:.1f}%)** n={n_guard}: 期待R {er_guard}"
        f"{'  ※参考(n<10)' if n_guard < MIN_BUCKET_N else ''}",
        f"  - 地合い正常日(> {guard_pct:.1f}%)= **成績本体** n={n_body}: 期待R {er_body}"
        f"{'  ※参考(n<10)' if n_body < MIN_BUCKET_N else ''}",
        f"  - (参考)警告日のうち強ブレイク n={n_gs}: 期待R {er_gs}",
        "",
        "> 本番では既存の地合い警告バナーで対応済み。MUST除外はしない(方針)。",
        "",
    ]
    return lines


def build_report_markdown(result: dict) -> str:
    setups = result["setups"]
    params = result["params"]
    broke_out = [s for s in setups if s.get("breakout")]
    # EXTENDED除外後の実測ブレイク(成績集計対象)。
    measured = [s for s in broke_out if not s.get("extended_skip")]
    extended_skipped = [s for s in broke_out if s.get("extended_skip")]
    strong = [s for s in measured if s.get("strong")]
    weak = [s for s in measured if not s.get("strong")]

    lines = [
        f"# 簡易バックテストレポート ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "",
        "**既知の限界**: 現在のユニバース(data/universe.json)で過去を見るため生存者バイアスが",
        "ある。RSの母集団はこのバックテストで読み込めた銘柄のみ(本番パイプラインより小さい場合",
        f"がある)。スキャン粒度={params.get('step', SCAN_STEP)}営業日(1=日次)。EXTENDED除外"
        "(ギャップアップで追いかけ不可のブレイク)は成績集計から外している。",
        "",
        "## パラメータ",
        f"- 検証ウィンドウ: 直近{params['days']}営業日",
        f"- rs_min: {params['rs_min']}",
        f"- breakout_vol_mult: {params['breakout_vol_mult']}",
        f"- stop_loss_pct: {params['stop_loss_pct']}",
        f"- 対象銘柄数: {result['codes_scanned']}",
        "",
        "## セットアップ検出数(月別)",
    ]
    by_month = _setups_by_month(setups)
    if by_month:
        for month, count in by_month.items():
            lines.append(f"- {month}: {count}件")
    else:
        lines.append("- セットアップ検出なし")

    total = len(setups)
    breakout_rate = round(len(broke_out) / total * 100, 1) if total else None
    lines += [
        "",
        "## ブレイク発生率",
        f"- セットアップ総数: {total}件",
        f"- ブレイク発生: {len(broke_out)}件 ({breakout_rate}%)" if breakout_rate is not None else "- ブレイク発生: 0件",
        f"  - EXTENDED除外: {len(extended_skipped)}件(成績集計から除外)",
        f"  - 実測ブレイク: {len(measured)}件",
        f"    - 強ブレイク(出来高{params['breakout_vol_mult']}倍以上): {len(strong)}件",
        f"    - 弱ブレイク: {len(weak)}件",
        "",
        "## 成績(強/弱ブレイク別・EXTENDED除外後)",
    ]

    for label, group in (("強ブレイク", strong), ("弱ブレイク", weak)):
        lines.append(f"### {label} ({len(group)}件)")
        if not group:
            lines.append("- データなし")
            continue
        for h in HORIZON_DAYS:
            stats = _perf_stats(group, h)
            if stats["n"] == 0:
                lines.append(f"- +{h}営業日: データなし")
            else:
                lines.append(
                    f"- +{h}営業日: 平均{stats['mean']}% / 中央値{stats['median']}% / "
                    f"勝率{stats['win_rate']}% (n={stats['n']})"
                )
        stop_rate = round(sum(1 for s in group if s.get("stop_hit")) / len(group) * 100, 1)
        r_values = [s["r_multiple"] for s in group if s.get("r_multiple") is not None]
        expected_r = round(sum(r_values) / len(r_values), 2) if r_values else None
        lines.append(f"- ストップ到達率: {stop_rate}%")
        lines.append(f"- 期待R: {expected_r}")
        lines.append("")

    lines += [""]
    lines += _build_market_guard_section(measured, load_config())

    lines += _build_dryup_section(setups)

    return "\n".join(lines)


def write_report(markdown: str, date_str: str | None = None) -> Path:
    date_str = date_str or datetime.now().strftime("%Y%m%d")
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKTEST_DIR / f"backtest_{date_str}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Minervini screener simplified backtest (phase 1)")
    parser.add_argument("--days", type=int, default=400, help="検証ウィンドウ(直近N営業日)")
    parser.add_argument("--limit", type=int, default=None, help="対象銘柄数の上限(先頭からN件)")
    parser.add_argument("--rs-min", type=float, default=None, help="RS閾値(既定: config.yaml)")
    parser.add_argument("--vol-mult", type=float, default=None, help="強ブレイク判定の出来高倍率(既定: config.yaml)")
    parser.add_argument("--stop-pct", type=float, default=None, help="損切り率、小数(既定: config.yaml)")
    parser.add_argument("--step", type=int, default=SCAN_STEP, help="スキャン粒度(営業日)。1=日次(既定)")
    args = parser.parse_args()

    config = load_config()
    rs_min = args.rs_min if args.rs_min is not None else config["trend_template"]["rs_min"]
    vol_mult = args.vol_mult if args.vol_mult is not None else config["entry"]["breakout_vol_mult"]
    stop_pct = args.stop_pct if args.stop_pct is not None else config["entry"]["stop_loss_pct"]

    result = run_backtest(days=args.days, limit=args.limit, rs_min=rs_min, vol_mult=vol_mult, stop_pct=stop_pct, config=config, step=args.step)
    result["params"]["step"] = args.step
    markdown = build_report_markdown(result)
    print(markdown)
    path = write_report(markdown)
    print(f"\nレポートを保存しました: {path}")


if __name__ == "__main__":
    main()
