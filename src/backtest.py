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
from src.indicators import compute_all
from src.screener import trend_template
from src.screener import vcp as vcp_mod
from src.universe import load_universe

BACKTEST_DIR = REPO_ROOT / "data" / "backtest"
WEEKLY_STEP = 5  # 週次グリッド(営業日)
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
) -> list[dict]:
    """週次グリッドでトレンドテンプレート合格+RS>=rs_minの銘柄のみVCPをスキャンし、
    WATCH_A(ピボット確定)のセットアップを記録する。同一銘柄でpivotが近い(±1%以内)
    セットアップは1件に統合する。"""
    n = len(df)
    setups: list[dict] = []
    seen_pivots: list[float] = []

    for i in range(start_idx, n, WEEKLY_STEP):
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

        setups.append(
            {
                "code": code,
                "setup_date": date,
                "setup_idx": i,
                "pivot": pivot,
                "stop_ref_low": last_c["low_price"],
                "vcp_score": vcp_result.get("vcp_score"),
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


def run_backtest(days: int, limit: int | None, rs_min: float, vol_mult: float, stop_pct: float, config: dict) -> dict:
    frames = load_universe_frames(limit)
    if not frames:
        return {"setups": [], "codes_scanned": 0, "params": _params(days, rs_min, vol_mult, stop_pct)}

    rs_by_date = build_rs_by_date(frames)

    all_setups: list[dict] = []
    for code, df in frames.items():
        n = len(df)
        start_idx = max(0, n - days)
        rs_series = rs_by_date[code] if code in rs_by_date.columns else pd.Series(dtype="float64")
        setups = scan_setups(code, df, rs_series, config, rs_min, start_idx)

        for setup in setups:
            breakout_idx = find_breakout_index(df, setup["setup_idx"], setup["pivot"])
            setup["breakout"] = breakout_idx is not None
            if breakout_idx is None:
                continue
            setup["breakout_idx"] = breakout_idx
            setup["breakout_date"] = df.iloc[breakout_idx]["date"]
            setup["strong"] = is_strong_breakout(df, breakout_idx, vol_mult)
            setup.update(measure_performance(df, breakout_idx, stop_pct))

        all_setups.extend(setups)

    return {
        "setups": all_setups,
        "codes_scanned": len(frames),
        "params": _params(days, rs_min, vol_mult, stop_pct),
    }


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


def build_report_markdown(result: dict) -> str:
    setups = result["setups"]
    params = result["params"]
    broke_out = [s for s in setups if s.get("breakout")]
    strong = [s for s in broke_out if s.get("strong")]
    weak = [s for s in broke_out if not s.get("strong")]

    lines = [
        f"# 簡易バックテストレポート ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "",
        "**既知の限界**: 現在のユニバース(data/universe.json)で過去を見るため生存者バイアスが",
        "ある。RSの母集団はこのバックテストで読み込めた銘柄のみ(本番パイプラインより小さい場合",
        "がある)。VCPスキャンは週次グリッド近似であり全日付を評価していない。",
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
        f"  - 強ブレイク(出来高{params['breakout_vol_mult']}倍以上): {len(strong)}件",
        f"  - 弱ブレイク: {len(weak)}件",
        "",
        "## 成績(強/弱ブレイク別)",
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
    args = parser.parse_args()

    config = load_config()
    rs_min = args.rs_min if args.rs_min is not None else config["trend_template"]["rs_min"]
    vol_mult = args.vol_mult if args.vol_mult is not None else config["entry"]["breakout_vol_mult"]
    stop_pct = args.stop_pct if args.stop_pct is not None else config["entry"]["stop_loss_pct"]

    result = run_backtest(days=args.days, limit=args.limit, rs_min=rs_min, vol_mult=vol_mult, stop_pct=stop_pct, config=config)
    markdown = build_report_markdown(result)
    print(markdown)
    path = write_report(markdown)
    print(f"\nレポートを保存しました: {path}")


if __name__ == "__main__":
    main()
