#!/usr/bin/env python3
"""長期ヒストリカル取得ツール (研究用) — yfinance で 2015 年以降の日足を集める。

なぜ別ツールなのか
------------------
本番の `src/data/prices.py` は毎朝のスクリーナー運用のためのもので、
`config.yaml: data.history_days` (現在 520) で `df.tail()` 切り捨てをする。
つまり運用キャッシュ `data/prices/` には構造的に約2年分しか残らない。

2026-07-28 の検証(log.md 134)で、閾値診断の期間が2年しかないのは
「2年で検証しようと決めた」からではなく「運用の都合でそれしか残っていない」
だけだと判明した。レジーム変化が1回しか入っておらず、どの閾値が相場環境に
依らず効くのかが原理的に確かめられない。

そこで研究用に別ストア `data/prices_long/` を作る。

★重要な設計判断★
- `data/prices/` には一切書き込まない。運用を壊さないため。
- `tail()` 切り捨てをしない。これが本ツールの存在理由。
- 銘柄リストは既定で `data/sector_map.json` の全候補(約3700)。
  `data/universe.json` の1000銘柄だけを取ると「2026年時点の流動性で
  2015年の銘柄を選ぶ」という選択バイアスが入る。長期履歴さえあれば
  各日時点の売買代金からユニバースを再構築できるので、広く取っておく。
- 中断・再開可能。yfinance は 3700 銘柄だと数十分かかるうえ、
  レート制限で途中で落ちる。既に十分な履歴があるファイルはスキップする。

★解消できない限界: 生存バイアス★
yfinance(Yahoo Finance)は上場廃止・被買収銘柄のティッカーを削除する。
したがって過去に遡っても「今日まで生き残った銘柄」しか集まらない。
倒産・上場廃止した負け組が標本から消えるので、どんな戦略も過去ほど
良く見える方向に歪む。これは無料データでは消せない。
`--survivorship-report` で歪みの大きさを実測する。

使い方
------
    # 全候補を2015年から取得(初回、数十分。Ctrl-Cで中断→再実行で続きから)
    python tools/fetch_long_history.py --start 2015-01-01

    # ユニバース1000銘柄だけで試す
    python tools/fetch_long_history.py --start 2015-01-01 --codes universe

    # 少数で動作確認
    python tools/fetch_long_history.py --start 2015-01-01 --limit 20

    # 取得後、運用キャッシュと重複期間を突合して調整基準の一致を確認
    python tools/fetch_long_history.py --verify

    # 年ごとの生存銘柄数カーブを出す
    python tools/fetch_long_history.py --survivorship-report
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.data.prices import fetch_yfinance_chunk  # noqa: E402

LONG_DIR = ROOT / "data" / "prices_long"
LIVE_DIR = ROOT / "data" / "prices"
MANIFEST_PATH = LONG_DIR / "_manifest.json"
SECTOR_MAP_PATH = ROOT / "data" / "sector_map.json"
UNIVERSE_PATH = ROOT / "data" / "universe.json"

OHLCV = ["date", "open", "high", "low", "close", "volume"]

# JPX公表「上場会社数の推移」年末値から、TOKYO PRO Market と外国会社を差し引いた
# 内国会社数。本ストアの母集団(filter_domestic_common_stock)と比較可能な粒度。
# 出典: https://www.jpx.co.jp/listing/co/tvdivq0000004xgb-att/tvdivq0000017jt9.pdf
#   (合計 − TOKYO PRO Market − 外国会社)
# 2022年4月に市場再編があるが、会社数の連続性には影響しない。
JPX_DOMESTIC_YEAR_END = {
    2013: 3417 - 6 - 11,
    2014: 3468 - 9 - 12,
    2015: 3511 - 14 - 9,
    2016: 3539 - 16 - 6,
    2017: 3602 - 22 - 6,
    2018: 3655 - 29 - 5,
    2019: 3706 - 33 - 4,
    2020: 3756 - 41 - 4,
    2021: 3822 - 47 - 6,
    2022: 3869 - 64 - 6,
    2023: 3933 - 90 - 6,
    2024: 3975 - 133 - 6,
    2025: 3945 - 163 - 5,
}


# ---------------------------------------------------------------------------
# 銘柄リスト
# ---------------------------------------------------------------------------

def load_codes(source: str) -> list[str]:
    if source == "universe":
        u = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        return [s["code"] for s in u["stocks"]]
    if source == "all":
        sm = json.loads(SECTOR_MAP_PATH.read_text(encoding="utf-8"))
        return sorted(sm["sectors"].keys())
    raise ValueError(f"unknown code source: {source}")


# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------

def long_path(code: str) -> Path:
    return LONG_DIR / f"{code}.parquet"


def existing_coverage(code: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    p = long_path(code)
    if not p.exists():
        return None, None, 0
    try:
        df = pd.read_parquet(p, columns=["date"])
    except Exception:
        return None, None, 0
    if df.empty:
        return None, None, 0
    return df["date"].min(), df["date"].max(), len(df)


def needs_fetch(code: str, start: pd.Timestamp, force: bool) -> bool:
    """既に十分な履歴があるならスキップする。

    「first_date が start 付近まで遡れているか」で判定する。start より後でも、
    その銘柄の上場が start より後なら遡りようがない。上場日は持っていないので、
    start + 猶予(180営業日相当) より前まで遡れていれば十分とみなす。
    上場が新しい銘柄は毎回再取得されてしまうが、その分だけ余分にAPIを叩く
    コストは、履歴の取りこぼしを見逃すリスクより安い。
    """
    if force:
        return True
    first, _, n = existing_coverage(code)
    if first is None or n == 0:
        return True
    return first > start + pd.Timedelta(days=270)


def save_long(code: str, df: pd.DataFrame) -> int:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    if getattr(df["date"].dtype, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    keep = [c for c in OHLCV if c in df.columns]
    df = df[keep].dropna(subset=["close"]).drop_duplicates(subset=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if df.empty:
        return 0
    LONG_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(long_path(code), index=False)
    return len(df)


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------

def run_fetch(args, config: dict) -> None:
    start = pd.Timestamp(args.start)
    codes = load_codes(args.codes)
    if args.limit:
        codes = codes[: args.limit]

    todo = [c for c in codes if needs_fetch(c, start, args.force)]
    skipped = len(codes) - len(todo)
    print(f"対象 {len(codes)} 銘柄 / 取得対象 {len(todo)} / スキップ済 {skipped}")
    if not todo:
        print("取得するものがない。--force で強制再取得。")
        return

    data_cfg = config["data"]
    chunk_size = int(data_cfg.get("chunk_size", 50))
    sleep_lo, sleep_hi = data_cfg.get("sleep_range", [2.0, 4.0])

    ok = fail = rows_total = 0
    failed_codes: list[str] = []
    t0 = time.time()

    for i in range(0, len(todo), chunk_size):
        chunk = todo[i : i + chunk_size]
        tickers = [f"{c}.T" for c in chunk]
        fetched = fetch_yfinance_chunk(
            tickers, start=args.start, period=None, config=config
        )
        for code, ticker in zip(chunk, tickers):
            df = fetched.get(ticker)
            if df is None or df.empty:
                fail += 1
                failed_codes.append(code)
                continue
            n = save_long(code, df)
            if n == 0:
                fail += 1
                failed_codes.append(code)
            else:
                ok += 1
                rows_total += n

        done = min(i + chunk_size, len(todo))
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        eta = (len(todo) - done) / rate if rate else 0
        print(
            f"  [{done}/{len(todo)}] ok={ok} fail={fail} "
            f"rows={rows_total:,} 経過{elapsed/60:.1f}分 残り約{eta/60:.1f}分",
            flush=True,
        )
        if done < len(todo):
            time.sleep(random.uniform(sleep_lo, sleep_hi))

    write_manifest(args.start)
    print(f"\n完了: 成功 {ok} / 失敗 {fail} / 総行数 {rows_total:,}")
    if failed_codes:
        print(f"失敗銘柄(先頭30): {failed_codes[:30]}")
        print("※ 上場廃止・新規上場・Yahoo未収載など。再実行で再挑戦する。")


def write_manifest(start: str) -> None:
    rows = []
    for p in sorted(LONG_DIR.glob("*.parquet")):
        code = p.stem
        if code.startswith("_"):
            continue
        first, last, n = existing_coverage(code)
        if first is None:
            continue
        rows.append(
            {
                "code": code,
                "first": first.strftime("%Y-%m-%d"),
                "last": last.strftime("%Y-%m-%d"),
                "rows": n,
            }
        )
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "requested_start": start,
        "codes": len(rows),
        "entries": rows,
    }
    LONG_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"マニフェスト書き出し: {MANIFEST_PATH} ({len(rows)} 銘柄)")


# ---------------------------------------------------------------------------
# 検証: 運用キャッシュとの突合
# ---------------------------------------------------------------------------

def run_verify(args) -> None:
    """重複期間で終値・出来高を突合する。

    なぜ必要か: yfinance の auto_adjust=True は株式分割・配当で全履歴の価格が
    書き換わる。運用キャッシュ(2年)と新規取得(11年)が別タイミングで取られて
    いる以上、調整基準がズレている可能性がある。出来高も Yahoo 側が分割調整
    済みかどうかを実測で確かめないと、枯れ度(出来高中央値/50日平均)が
    分割をまたいだ瞬間に嘘をつく。
    """
    files = sorted(LONG_DIR.glob("*.parquet"))
    files = [f for f in files if not f.stem.startswith("_")]
    if not files:
        print("data/prices_long/ が空。先に取得すること。")
        return

    close_bad: list[tuple[str, float]] = []
    vol_bad: list[tuple[str, float]] = []
    checked = no_live = 0

    for p in files:
        code = p.stem
        lp = LIVE_DIR / f"{code}.parquet"
        if not lp.exists():
            no_live += 1
            continue
        try:
            a = pd.read_parquet(p)
            b = pd.read_parquet(lp)
        except Exception:
            continue
        a["date"] = pd.to_datetime(a["date"])
        b["date"] = pd.to_datetime(b["date"])
        m = a.merge(b, on="date", suffixes=("_long", "_live"))
        if len(m) < 60:
            continue
        checked += 1

        denom = m["close_live"].abs().replace(0, pd.NA)
        cerr = ((m["close_long"] - m["close_live"]).abs() / denom).dropna()
        if not cerr.empty and cerr.max() > 0.01:
            close_bad.append((code, float(cerr.max())))

        if "volume_long" in m.columns and "volume_live" in m.columns:
            vd = m["volume_live"].abs().replace(0, pd.NA)
            verr = ((m["volume_long"] - m["volume_live"]).abs() / vd).dropna()
            if not verr.empty and verr.max() > 0.01:
                vol_bad.append((code, float(verr.max())))

    print(f"突合対象 {checked} 銘柄 (運用キャッシュ無し {no_live})")
    print(f"終値が1%超ズレた銘柄: {len(close_bad)}")
    for code, e in sorted(close_bad, key=lambda x: -x[1])[:15]:
        print(f"   {code}: 最大 {e:.1%}")
    print(f"出来高が1%超ズレた銘柄: {len(vol_bad)}")
    for code, e in sorted(vol_bad, key=lambda x: -x[1])[:15]:
        print(f"   {code}: 最大 {e:.1%}")

    if close_bad:
        print(
            "\n★終値がズレている = 分割/配当の調整基準が運用キャッシュと違う。\n"
            "  その銘柄は運用側を全履歴再取得するか、研究では long 側に統一すること。"
        )
    if vol_bad:
        print(
            "\n★出来高がズレている = 分割の出来高調整の有無が食い違っている。\n"
            "  枯れ度の指標が直接汚染されるので、原因を特定するまで先に進まないこと。"
        )
    if not close_bad and not vol_bad:
        print("\n重複期間は一致。調整基準の食い違いは検出されず。")


# ---------------------------------------------------------------------------
# 生存バイアスの実測
# ---------------------------------------------------------------------------

def run_survivorship_report() -> None:
    """年ごとに「その年に価格が存在した銘柄数」を数える。

    yfinance は上場廃止銘柄を返さないので、これは『今日まで生き残った銘柄の
    うち、その年に既に上場していた数』でしかない。真の上場社数(JPXの
    月末上場銘柄数)と比べたときの差が、標本から消えた銘柄の規模になる。
    差が大きいほど、過去に遡った検証結果は楽観方向に歪んでいる。
    """
    files = [p for p in sorted(LONG_DIR.glob("*.parquet")) if not p.stem.startswith("_")]
    if not files:
        print("data/prices_long/ が空。先に取得すること。")
        return

    counts: dict[int, int] = {}
    for p in files:
        try:
            d = pd.read_parquet(p, columns=["date"])
        except Exception:
            continue
        if d.empty:
            continue
        yrs = pd.to_datetime(d["date"]).dt.year.unique()
        for y in yrs:
            counts[int(y)] = counts.get(int(y), 0) + 1

    # 基準年 = JPX実数を持つ最新年。その年のカバー率を「バイアスゼロ」とみなし、
    # 各年のカバー率がそこからどれだけ落ちるかを『標本外率』とする。
    # 基準年でも100%にならない(フィルタ差・Yahoo未収載)ので、その分は
    # 生存バイアスではないため差し引く必要がある。
    base_year = max(y for y in counts if y in JPX_DOMESTIC_YEAR_END)
    base_cov = counts[base_year] / JPX_DOMESTIC_YEAR_END[base_year]

    print(f"基準年 {base_year}: カバー率 {base_cov:.1%} (これをバイアス0とみなす)\n")
    print("年   | 標本  | JPX実数 | カバー率 | 標本外率(生存バイアス)")
    for y in sorted(counts):
        real = JPX_DOMESTIC_YEAR_END.get(y)
        if real is None:
            print(f"{y} | {counts[y]:>5} |    -    |    -     |   - (JPX実数未登録)")
            continue
        cov = counts[y] / real
        gap = 1.0 - cov / base_cov
        print(f"{y} | {counts[y]:>5} | {real:>7} | {cov:7.1%}  | {gap:6.1%}")
    print(
        "\n※ 標本外率 = その年に上場していたが今日まで生き残らず、Yahooから消えた銘柄の割合。\n"
        "  この割合だけ、遡った期間の成績は歪む。\n"
        "\n★歪みの向きは自明ではない★\n"
        "  日本の上場廃止はTOB/MBO/親子上場解消が大きな比率を占める。これらは\n"
        "  「株価が上がって買われて消えた」=勝ちトレードなので、消えることで\n"
        "  成績は下振れする。倒産・整理銘柄は逆に上振れさせる。\n"
        "  向きを確定させたければ JPX『上場廃止銘柄一覧』の廃止理由内訳を当たること。\n"
        "  それまでは『標本外率X%ぶんの不確実性がある』とだけ注記して結論を出すこと。"
    )


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2015-01-01", help="取得開始日 (既定 2015-01-01)")
    ap.add_argument("--codes", choices=["all", "universe"], default="all",
                    help="all=sector_map.jsonの全候補(既定) / universe=universe.jsonの1000銘柄")
    ap.add_argument("--limit", type=int, default=0, help="先頭N銘柄だけ(動作確認用)")
    ap.add_argument("--force", action="store_true", help="既存ファイルも再取得")
    ap.add_argument("--verify", action="store_true", help="運用キャッシュとの突合のみ実行")
    ap.add_argument("--survivorship-report", action="store_true", help="年別の標本銘柄数のみ出力")
    ap.add_argument("--manifest-only", action="store_true", help="マニフェスト再生成のみ")
    args = ap.parse_args()

    if args.verify:
        run_verify(args)
        return
    if args.survivorship_report:
        run_survivorship_report()
        return
    if args.manifest_only:
        write_manifest(args.start)
        return

    run_fetch(args, load_config())


if __name__ == "__main__":
    main()
