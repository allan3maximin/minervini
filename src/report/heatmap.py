"""機能B: セクターヒートマップ用データ生成 (docs/data/heatmap.json).

日次バッチで全計算を済ませ、フロント(heatmap.html)は描画のみ行う:
  - 銘柄別 1/5/20/60営業日リターン (価格キャッシュ由来、追加リクエストなし)
  - 時価総額 = 発行済株式数(月次取得) × 最新終値。取得不可は None (最小面積フォールバック)
  - TSE33業種で集計 (data/sector_map.json / universe.json 由来の静的マッピング)
  - セクターRS(対TOPIX): 強/中/弱 + 方向(↑/→/↓)。銘柄の独立属性でプライオリティには不参入
  - セクター集計履歴を data/history/sector.jsonl に蓄積 (グラフ化は対象外)

前場スナップショット実行 (snapshot_suffix 指定) では docs/data/heatmap_maezyou.json
だけを書き、大引の heatmap.json と履歴には一切触らない (build_heatmap の説明を参照)。
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from src.config import REPO_ROOT, load_config

HEATMAP_PATH = REPO_ROOT / "docs" / "data" / "heatmap.json"


def heatmap_output_path(snapshot_suffix: str = ""):
    """公開先のヒートマップJSONのパス。前場断面は別名(heatmap_maezyou.json)にする。

    大引の断面は従来どおり heatmap.json。前場ランは suffix 付きの別ファイルへ書く。
    """
    if not snapshot_suffix:
        return HEATMAP_PATH
    return HEATMAP_PATH.with_name(f"{HEATMAP_PATH.stem}{snapshot_suffix}.json")


# 旧: 全量書き戻し方式の JSON (2026-07-27 まで)。移行後は読み取り専用の
# フォールバックとしてのみ参照する。
SECTOR_HISTORY_PATH = REPO_ROOT / "data" / "sector_history.json"
# 新: 追記専用 JSONL。1行 = 1日ぶんの {date, topix_d1, sectors{...}}。
SECTOR_HISTORY_JSONL = REPO_ROOT / "data" / "history" / "sector.jsonl"
SECTOR_HISTORY_KEY = ("date",)
# フロント(ヒートマップ簡易ビューの履歴)が読む公開版。市況カードと同じく
# secure_io で暗号化して配信する。日付軸に揃えたセクター別の系列に整形する。
SECTOR_HISTORY_PUBLIC_PATH = REPO_ROOT / "docs" / "data" / "sector_history.json"
SECTOR_HISTORY_PUBLIC_DAYS = 120
SECTOR_MAP_PATH = REPO_ROOT / "data" / "sector_map.json"

UNKNOWN_SECTOR = "その他"

DEFAULTS = {
    "periods": [1, 5, 20, 60],
    "strength_window": 20,
    "direction_window": 5,
    "sector_strong_rel_pct": 2.0,
    "sector_weak_rel_pct": -2.0,
    "direction_up_rel_pct": 0.5,
    "direction_down_rel_pct": -0.5,
    "history_keep_days": 400,
}


def _cfg(config: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update(config.get("heatmap", {}) or {})
    return merged


def compute_returns(close: pd.Series, periods: list[int]) -> dict[str, float | None]:
    """終値系列から各期間の騰落率(%)。データ不足期間は None。"""
    out: dict[str, float | None] = {}
    n = len(close)
    for p in periods:
        key = f"d{p}"
        if n >= p + 1:
            base = float(close.iloc[-(p + 1)])
            out[key] = round((float(close.iloc[-1]) / base - 1.0) * 100.0, 2) if base else None
        else:
            out[key] = None
    return out


def load_sector_map() -> dict[str, str]:
    if not SECTOR_MAP_PATH.exists():
        return {}
    with open(SECTOR_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("sectors", {})


def sector_rs(
    sector_returns: dict[str, float | None],
    topix_returns: dict[str, float | None],
    cfg: dict,
) -> dict:
    """セクターRS(対TOPIX相対): 強/中/弱 + 方向(↑/→/↓)。

    強弱:  strength_window(既定20日)の相対リターンで判定
    方向:  direction_window(既定5日)の相対リターンで判定
    """
    sk = f"d{cfg['strength_window']}"
    dk = f"d{cfg['direction_window']}"

    rel_strength = None
    if sector_returns.get(sk) is not None and topix_returns.get(sk) is not None:
        rel_strength = round(sector_returns[sk] - topix_returns[sk], 2)
    rel_direction = None
    if sector_returns.get(dk) is not None and topix_returns.get(dk) is not None:
        rel_direction = round(sector_returns[dk] - topix_returns[dk], 2)

    if rel_strength is None:
        strength = None
    elif rel_strength >= cfg["sector_strong_rel_pct"]:
        strength = "強"
    elif rel_strength <= cfg["sector_weak_rel_pct"]:
        strength = "弱"
    else:
        strength = "中"

    if rel_direction is None:
        direction = None
    elif rel_direction >= cfg["direction_up_rel_pct"]:
        direction = "↑"
    elif rel_direction <= cfg["direction_down_rel_pct"]:
        direction = "↓"
    else:
        direction = "→"

    return {
        "strength": strength,
        "direction": direction,
        "rel_strength_pct": rel_strength,
        "rel_direction_pct": rel_direction,
    }


def _weighted_returns(stocks: list[dict], periods: list[int]) -> dict[str, float | None]:
    """セクター集計リターン: 時価総額加重平均(不明銘柄は等ウェイト扱いの中央値的簡略化として除外、
    全銘柄不明なら単純平均)。"""
    out: dict[str, float | None] = {}
    for p in periods:
        key = f"d{p}"
        pairs = [(s["returns"].get(key), s.get("mcap")) for s in stocks if s["returns"].get(key) is not None]
        if not pairs:
            out[key] = None
            continue
        weighted = [(r, m) for r, m in pairs if m]
        if weighted:
            total = sum(m for _, m in weighted)
            out[key] = round(sum(r * m for r, m in weighted) / total, 2)
        else:
            out[key] = round(sum(r for r, _ in pairs) / len(pairs), 2)
    return out


def build_heatmap(
    universe: dict,
    frames: dict[str, pd.DataFrame],
    benchmark_close: pd.Series,
    stock_records: list[dict],
    config: dict | None = None,
    today_str: str | None = None,
    snapshot_suffix: str = "",
) -> dict:
    """heatmap.json + sector_history.json を生成し、レポート付与用の
    sector_strength_by_code を返す。

    `snapshot_suffix` に "_maezyou" のようなラベルが入ると**前場断面モード**になる
    (2026-07-31)。この場合:

    - 公開JSONは heatmap_maezyou.json へ書き、大引の heatmap.json には一切触らない。
      これが無いと、前場バッチが11:40頃に前場の途中足で作ったセクター値を
      heatmap.json として公開してしまい、夕方の日次バッチが走るまでの数時間、
      途中の値が確定値の顔をして表示される。
    - セクターの日次履歴(sector.jsonl / 公開用 sector_history.json)は一切書かない。
      履歴は後勝ちなので通常は夕方の日次バッチが上書きしてくれるが、**日次バッチが
      落ちた日はその日のセクター履歴が前場の値のまま確定してしまう**。履歴は後から
      見返して「その日どうだったか」を語る土台なので、途中の値を混ぜない。

    返り値の sector_strength_by_code は前場ランでも通常どおり返す。これは銘柄
    レコードに載せる表示用の属性(所属セクターと強弱)で、履歴には残らないため。
    """
    config = config or load_config()
    cfg = _cfg(config)
    periods = cfg["periods"]
    today_str = today_str or datetime.now().date().isoformat()

    sector_map = load_sector_map()
    record_by_code = {r["code"]: r for r in stock_records}

    topix_returns = (
        compute_returns(benchmark_close, periods)
        if benchmark_close is not None and len(benchmark_close) > 0
        else {f"d{p}": None for p in periods}
    )

    # --- 銘柄タイル ---
    stocks_by_sector: dict[str, list[dict]] = {}
    for stock in universe.get("stocks", []):
        code = stock["code"]
        df = frames.get(code)
        if df is None or df.empty:
            continue
        close = df["close"]
        last_close = float(close.iloc[-1])
        shares = stock.get("shares_outstanding")
        mcap = round(shares * last_close) if shares else None
        sector = stock.get("sector33") or sector_map.get(code) or UNKNOWN_SECTOR

        record = record_by_code.get(code)
        tile = {
            "code": code,
            "name": stock.get("name", ""),
            "close": round(last_close, 2),
            "mcap": mcap,
            "returns": compute_returns(close, periods),
            "priority": record.get("priority") if record else None,
            "rs": record.get("rs") if record else None,
        }
        if record and record.get("priority") is not None:
            # タップ時ポップアップ用の機能A詳細
            tile["detail"] = {
                "tier": record.get("tier"),
                "status": record.get("status"),
                "priority_penalty": record.get("priority_penalty"),
                "priority_unmet": record.get("priority_unmet"),
                "ma_deviation_pct": record.get("ma_deviation_pct"),
                "high52w_distance_pct": record.get("high52w_distance_pct"),
                "has_chart": record.get("has_chart", False),
            }
        stocks_by_sector.setdefault(sector, []).append(tile)

    # --- セクター集計 ---
    sector_strength_by_code: dict[str, dict] = {}
    sectors = []
    for sector, tiles in stocks_by_sector.items():
        agg_returns = _weighted_returns(tiles, periods)
        rs_info = sector_rs(agg_returns, topix_returns, cfg)
        p1 = sum(1 for t in tiles if t.get("priority") == 1)
        p2 = sum(1 for t in tiles if t.get("priority") == 2)
        mcap_total = sum(t["mcap"] for t in tiles if t.get("mcap"))
        tiles_sorted = sorted(tiles, key=lambda t: -(t.get("mcap") or 0))
        sectors.append(
            {
                "sector": sector,
                "returns": agg_returns,
                "rs": rs_info,
                "p1_count": p1,
                "p2_count": p2,
                "stock_count": len(tiles),
                "mcap_total": mcap_total or None,
                "stocks": tiles_sorted,
            }
        )
        for t in tiles:
            sector_strength_by_code[t["code"]] = {
                "sector": sector,
                "strength": rs_info["strength"],
                "direction": rs_info["direction"],
            }

    sectors.sort(key=lambda s: -(s["mcap_total"] or 0))

    heatmap = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "date": today_str,
        "periods": periods,
        "topix_returns": topix_returns,
        "sectors": sectors,
    }
    from src.report.secure_io import write_docs_json
    write_docs_json(heatmap_output_path(snapshot_suffix), heatmap)

    # 前場断面はここで打ち切る。履歴を「書いてから消す/上書きする」のではなく
    # そもそも書かないので、日次バッチが落ちた日でも履歴に途中の値は残らない。
    if not snapshot_suffix:
        history = update_sector_history(today_str, sectors, topix_returns, cfg)
        publish_sector_history(history)

    return {"heatmap": heatmap, "sector_strength_by_code": sector_strength_by_code}


def publish_sector_history(history: dict) -> dict:
    """内部履歴(data/sector_history.json)を、フロントのヒートマップ簡易履歴が
    そのまま描ける「日付軸に揃えたセクター別系列」に整形して docs/data に公開。
    直近 SECTOR_HISTORY_PUBLIC_DAYS 日ぶんに絞る(ペイロード削減)。"""
    entries = sorted(history.get("history", []), key=lambda e: e.get("date") or "")
    entries = entries[-SECTOR_HISTORY_PUBLIC_DAYS:]
    dates = [e.get("date") for e in entries]
    topix_d1 = [e.get("topix_d1") for e in entries]

    # 全期間に現れるセクター名を収集し、各日付に揃える(欠損日はNone)。
    sector_names: list[str] = []
    seen = set()
    for e in entries:
        for name in (e.get("sectors") or {}).keys():
            if name not in seen:
                seen.add(name)
                sector_names.append(name)

    sectors_series: dict[str, dict] = {}
    for name in sector_names:
        d1s, rels, strengths = [], [], []
        for e in entries:
            rec = (e.get("sectors") or {}).get(name) or {}
            d1s.append(rec.get("d1"))
            rels.append(rec.get("rel_strength_pct"))
            strengths.append(rec.get("strength"))
        sectors_series[name] = {
            "d1": d1s,
            "rel_strength_pct": rels,
            "strength": strengths,
        }

    public = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "dates": dates,
        "topix_d1": topix_d1,
        "sectors": sectors_series,
    }
    from src.report.secure_io import write_docs_json

    write_docs_json(SECTOR_HISTORY_PUBLIC_PATH, public)
    return public


def _migrate_legacy_sector_history() -> int:
    """旧 data/sector_history.json が残っていて JSONL 未作成なら一度だけ変換する。

    移行スクリプトを流し忘れても壊れないようにするための保険。旧ファイルは残す。
    """
    from src.history_store import append_records
    from src.utils_io import safe_load_json

    if SECTOR_HISTORY_JSONL.exists() or not SECTOR_HISTORY_PATH.exists():
        return 0
    legacy = safe_load_json(SECTOR_HISTORY_PATH, {"history": []})
    rows = [e for e in (legacy or {}).get("history", []) if isinstance(e, dict)]
    if not rows:
        return 0
    append_records(SECTOR_HISTORY_JSONL, rows)
    print(f"sector_history: 旧JSONから {len(rows)} 行を {SECTOR_HISTORY_JSONL.name} へ移行しました")
    return len(rows)


def load_sector_history() -> dict:
    """`{"history": [{date, topix_d1, sectors}, ...]}` を返す(日付昇順)。

    構造は JSONL 移行前と同一。publish_sector_history がこの形をそのまま
    受け取る(= docs/data/sector_history.json のスキーマに影響しない)。
    """
    from src.history_store import load_deduped

    _migrate_legacy_sector_history()
    entries = load_deduped(SECTOR_HISTORY_JSONL, SECTOR_HISTORY_KEY)
    entries.sort(key=lambda e: e.get("date") or "")
    return {"history": entries}


def update_sector_history(
    date_str: str, sectors: list[dict], topix_returns: dict, cfg: dict
) -> dict:
    """セクター集計値の日次履歴 (data/history/sector.jsonl)。同日再実行は上書き。

    2026-07-27 に「全量書き戻しJSON」から「追記専用JSONL + 読み出し時に後勝ち
    dedup」へ移行した(理由は src/history_store.py の docstring)。同日再実行時は
    同じ date の行がもう1行増えるだけで、読み出すと最後の行が採用されるため
    従来の「上書き」と同じ結果になる。

    破損行は iter_records 側でスキップされるので、1行壊れても全履歴は失われない。
    """
    from src.history_store import append_records, compact, count_lines

    history = load_sector_history()

    entry = {
        "date": date_str,
        "topix_d1": topix_returns.get("d1"),
        "sectors": {
            s["sector"]: {
                "d1": s["returns"].get("d1"),
                "rel_strength_pct": s["rs"].get("rel_strength_pct"),
                "strength": s["rs"].get("strength"),
                "mcap_total": s["mcap_total"],
                "p1_count": s["p1_count"],
            }
            for s in sectors
        },
    }
    append_records(SECTOR_HISTORY_JSONL, [entry])

    # 重複行が溜まってきたときだけ dedup + 間引き。毎回やると全行書き戻しになり
    # 「git 差分は追記行だけ」という利点が消える。
    keep_days = cfg["history_keep_days"]
    lines = count_lines(SECTOR_HISTORY_JSONL)
    if lines > max(keep_days * 2, 100):
        from src.history_store import calendar_keep_days
        removed = compact(
            SECTOR_HISTORY_JSONL,
            SECTOR_HISTORY_KEY,
            keep_days=calendar_keep_days(keep_days),
            today=date_str,
        )
        print(f"sector_history: compaction で {removed} 行を削減")

    # 返り値は従来どおり「日付昇順・直近 history_keep_days 件」。呼び出し側
    # (publish_sector_history) が受け取る形を変えないため、間引きは JSONL の
    # compaction とは独立にここでも掛ける。ディスク上に一時的に古い行が残って
    # いても、公開データの内容は移行前と完全に同じになる。
    history["history"] = [e for e in history["history"] if e.get("date") != date_str]
    history["history"].append(entry)
    history["history"].sort(key=lambda e: e.get("date") or "")
    history["history"] = history["history"][-keep_days:]
    return history
