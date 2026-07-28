#!/usr/bin/env python3
"""長期ヒストリカル取得ツール (研究用) — yfinance で 2000 年以降の日足を集める。

2026-07-28 更新: 既定の取得開始日を 2015-01-01 から 2000-01-01 に変更した。
リーマンショック(2008)・ITバブル崩壊(2000-02)を標本に入れるため。
これで「大きく壊れる相場」が3回(2000, 2008, 2020)入る。2015年起点だと
コロナの1回しかなく、暴落局面で符号が保たれるかを確かめようがなかった。

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

★解消できない限界: 生存バイアス (2000年起点では致命的に大きい)★
yfinance(Yahoo Finance)は上場廃止・被買収銘柄のティッカーを削除する。
したがって過去に遡っても「今日まで生き残った銘柄」しか集まらない。
2015年起点でも2015年の標本外率は17.4%あった(log.md 135)。2000年まで
遡ると、その年に上場していた会社の相当数が既にこの世にない。
**遡るほどデータ量は増えるが、同時に標本の代表性は落ちる。**
`--survivorship-report` で年ごとの歪みの大きさを実測すること。

★2000年まで遡るときの追加の落とし穴★
1. **証券コードの再利用/変更**: 上場廃止後にコードが別会社へ再割当された例、
   経営統合でコードが変わった例がある。本ストアは今日のコードで遡るので、
   古い期間に「別会社の株価」が紛れ込む可能性がゼロではない。
2. **市場の非連続**: JASDAQ は 2010 年まで、大証は 2013 年まで東証と別の
   取引所だった。「全国の上場会社数」の系列がこの前後で繋がらないので、
   2013年より前は生存バイアスを JPX の表と突合できない(下記参照)。
3. **Yahoo の収載深度**: 銘柄によっては 2000 年まで遡れず、実際の上場日より
   後ろからしかデータが無いことがある。取得後に必ずマニフェストの
   first 日付の分布を見て、いつから標本が薄いかを把握すること。
4. **売買単位の変更**: 2018年10月に全銘柄100株単位へ統一された。それ以前は
   1000株単位の銘柄が多い。出来高の絶対値は比較できないが、本ツールの用途
   (枯れ度 = 出来高中央値/50日平均 など比率)では約分されるので影響しない。

使い方
------
    # 全候補を2000年から取得(初回、数時間。Ctrl-Cで中断→再実行で続きから)
    python tools/fetch_long_history.py

    # 既に2015年分を持っている状態から2000年まで延ばす場合も同じコマンドでよい。
    # 取得済み起点を _fetchlog.json に記録しているので、
    # 「2015年から取った銘柄」は自動で2000年からの再取得対象になる。

    # ユニバース1000銘柄だけで試す
    python tools/fetch_long_history.py --codes universe

    # 少数で動作確認
    python tools/fetch_long_history.py --limit 20

    # 取得後、運用キャッシュと重複期間を突合して調整基準の一致を確認
    python tools/fetch_long_history.py --verify

    # 年ごとの生存銘柄数カーブを出す
    python tools/fetch_long_history.py --survivorship-report

    # 標本がいつから薄くなるか(first日付の分布)を見る
    python tools/fetch_long_history.py --coverage-report
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
FETCHLOG_PATH = LONG_DIR / "_fetchlog.json"
SECTOR_MAP_PATH = ROOT / "data" / "sector_map.json"
UNIVERSE_PATH = ROOT / "data" / "universe.json"
# 2012年以前の上場会社数を手で登録するための任意の上書きファイル。
# {"2000": 3400, "2001": 3391, ...} の形式。存在すれば組み込み表にマージされる。
COUNTS_OVERRIDE_PATH = ROOT / "data" / "jpx_listed_counts.json"

OHLCV = ["date", "open", "high", "low", "close", "volume"]

# JPX公表「上場会社数の推移」年末値から、TOKYO PRO Market と外国会社を差し引いた
# 内国会社数。本ストアの母集団(filter_domestic_common_stock)と比較可能な粒度。
# 出典: https://www.jpx.co.jp/listing/co/tvdivq0000004xgb-att/tvdivq0000017jt9.pdf
#   (合計 − TOKYO PRO Market − 外国会社)
# 2022年4月に市場再編があるが、会社数の連続性には影響しない。
#
# ★2012年以前が無いのは意図的★
# この表は「東証グループの上場会社数」である。JASDAQ が東証に統合されたのは
# 2010年、大証の現物市場の統合は2013年7月。したがって2012年以前にこの表を
# そのまま当てると、JASDAQ/大証単独上場の銘柄を分母から落としてカバー率が
# 100%を超え、生存バイアスを過小評価する。
# 埋めたい場合は「全国上場会社数」(証券統計 https://www.shouken-toukei.jp/)の
# 系列を data/jpx_listed_counts.json に手で入れること。
# 推測値を入れるくらいなら空のままにして「未登録」と出させる方がよい。
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


def load_fetchlog() -> dict[str, str]:
    """銘柄ごとに『どの開始日で取得を完了したか』の記録を読む。"""
    if not FETCHLOG_PATH.exists():
        return {}
    try:
        return json.loads(FETCHLOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_fetchlog(log: dict[str, str]) -> None:
    LONG_DIR.mkdir(parents=True, exist_ok=True)
    FETCHLOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def needs_fetch(code: str, start: pd.Timestamp, force: bool, log: dict[str, str]) -> bool:
    """既に十分な履歴があるならスキップする。

    ★2026-07-28 に判定方法を変えた★
    以前は「first_date が start + 270日 より前まで遡れているか」で見ていた。
    2015年起点なら実害は小さかったが、2000年起点にすると
    **2000年9月以降に上場した銘柄が全部この条件に引っかかる**。
    現在の上場銘柄の半分以上は2000年以降のIPOなので、実行するたびに
    2000銘柄近くを無駄に再取得し続けることになる(数時間 × 毎回)。

    そこで「その銘柄をどの開始日で取りに行ったか」を _fetchlog.json に
    記録する方式に変えた。記録された開始日が今回の start 以前なら、
    Yahoo が返せるものは既に全部持っているとみなしてスキップする。
    起点を更に古くしたときだけ、全銘柄が自動で再取得対象に戻る。
    """
    if force:
        return True
    _, _, n = existing_coverage(code)
    if n == 0:
        return True
    got = log.get(code)
    if got is None:
        # 記録が無い = 旧方式で取った分。起点が分からないので取り直す。
        return True
    return pd.Timestamp(got) > start


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

    log = load_fetchlog()
    todo = [c for c in codes if needs_fetch(c, start, args.force, log)]
    skipped = len(codes) - len(todo)
    print(f"取得開始日 {args.start}")
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
                log[code] = args.start

        # チャンクごとに記録を落とす。数時間かかる処理なので、
        # Ctrl-C や rate limit で落ちても再開時に取り直しにならないようにする。
        save_fetchlog(log)

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

    real_counts = load_listed_counts()

    # 基準年 = 実数を持つ最新年。その年のカバー率を「バイアスゼロ」とみなし、
    # 各年のカバー率がそこからどれだけ落ちるかを『標本外率』とする。
    # 基準年でも100%にならない(フィルタ差・Yahoo未収載)ので、その分は
    # 生存バイアスではないため差し引く必要がある。
    base_year = max(y for y in counts if y in real_counts)
    base_cov = counts[base_year] / real_counts[base_year]

    print(f"基準年 {base_year}: カバー率 {base_cov:.1%} (これをバイアス0とみなす)\n")
    print("年   | 標本  | 実上場数 | カバー率 | 標本外率(生存バイアス)")
    unknown: list[int] = []
    for y in sorted(counts):
        real = real_counts.get(y)
        if real is None:
            unknown.append(y)
            print(f"{y} | {counts[y]:>5} |    -     |    -     |   - (実数未登録)")
            continue
        cov = counts[y] / real
        gap = 1.0 - cov / base_cov
        print(f"{y} | {counts[y]:>5} | {real:>8} | {cov:7.1%}  | {gap:6.1%}")

    if unknown:
        print(
            f"\n★{unknown[0]}〜{unknown[-1]} 年は実上場数が未登録なので、生存バイアスを数値化できていない★\n"
            "  組み込み表は東証グループの会社数で、JASDAQ統合(2010年)・大証統合(2013年7月)より\n"
            "  前には当てられない。そのまま使うとカバー率が100%を超えて歪みを過小評価する。\n"
            f"  埋めるには「全国上場会社数」の年末値を {COUNTS_OVERRIDE_PATH.name} に\n"
            '  {"2000": 3400, "2001": 3391, ...} の形式で置くこと(出典: 証券統計 shouken-toukei.jp)。\n'
            "  ★推測値で埋めないこと。埋まるまでは『この期間の歪みは未測定』と明記して結論を出す。"
        )

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


def load_listed_counts() -> dict[int, int]:
    """組み込みのJPX表に、任意の上書きファイルをマージして返す。"""
    out = dict(JPX_DOMESTIC_YEAR_END)
    if COUNTS_OVERRIDE_PATH.exists():
        try:
            ov = json.loads(COUNTS_OVERRIDE_PATH.read_text(encoding="utf-8"))
            for k, v in ov.items():
                out[int(k)] = int(v)
            print(f"(上場会社数の上書きを読み込み: {COUNTS_OVERRIDE_PATH.name} {len(ov)}年分)")
        except Exception as e:
            print(f"警告: {COUNTS_OVERRIDE_PATH.name} を読めない ({e})。組み込み表のみ使う。")
    return out


def run_coverage_report() -> None:
    """各銘柄の履歴開始日(first)の分布を出す。

    生存バイアスとは別の問題を見るためのもの。yfinance が古い期間を持って
    いない銘柄がどれだけあるかを把握しないと、「2000年から検証した」と言い
    ながら実際は2000年の標本が数百銘柄しかない、という事故が起きる。
    """
    files = [p for p in sorted(LONG_DIR.glob("*.parquet")) if not p.stem.startswith("_")]
    if not files:
        print("data/prices_long/ が空。先に取得すること。")
        return

    firsts: list[pd.Timestamp] = []
    rows: list[int] = []
    for p in files:
        first, _, n = existing_coverage(p.stem)
        if first is None:
            continue
        firsts.append(first)
        rows.append(n)

    s = pd.Series(firsts)
    print(f"銘柄 {len(s)} / 総行数 {sum(rows):,}")
    print(f"履歴開始日 最古 {s.min():%Y-%m-%d} / 中央 {s.median():%Y-%m-%d} / 最新 {s.max():%Y-%m-%d}\n")
    print("その年の初めまでに履歴が始まっている銘柄数 (=その年の実質的な標本サイズ)")
    for y in range(s.min().year, s.max().year + 1):
        n = int((s < pd.Timestamp(f"{y}-01-01")).sum())
        bar = "#" * int(n / max(len(s), 1) * 50)
        print(f"  {y} | {n:>5} ({n/len(s):5.1%}) {bar}")
    print(
        "\n※ ここが薄い年は『生存バイアス』とは別に『そもそも標本が小さい』。\n"
        "  検証結果を局面別に見るとき、標本の薄い年の列は信頼区間が広いことを忘れないこと。"
    )


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="2000-01-01", help="取得開始日 (既定 2000-01-01)")
    ap.add_argument("--codes", choices=["all", "universe"], default="all",
                    help="all=sector_map.jsonの全候補(既定) / universe=universe.jsonの1000銘柄")
    ap.add_argument("--limit", type=int, default=0, help="先頭N銘柄だけ(動作確認用)")
    ap.add_argument("--force", action="store_true", help="既存ファイルも再取得")
    ap.add_argument("--verify", action="store_true", help="運用キャッシュとの突合のみ実行")
    ap.add_argument("--survivorship-report", action="store_true", help="年別の標本銘柄数のみ出力")
    ap.add_argument("--coverage-report", action="store_true", help="履歴開始日の分布のみ出力")
    ap.add_argument("--manifest-only", action="store_true", help="マニフェスト再生成のみ")
    args = ap.parse_args()

    if args.verify:
        run_verify(args)
        return
    if args.survivorship_report:
        run_survivorship_report()
        return
    if args.coverage_report:
        run_coverage_report()
        return
    if args.manifest_only:
        write_manifest(args.start)
        return

    run_fetch(args, load_config())


if __name__ == "__main__":
    main()
