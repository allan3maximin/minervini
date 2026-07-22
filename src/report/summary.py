"""個別銘柄画面のルールベース日本語サマリー生成 (LLM不使用)。

report.json の各銘柄レコード + 補助データ(四半期ファンダ・直近値動き・地合い)
から「見出し(状態と次のアクション) / 根拠 / 注意点」の3ブロックを組み立てる。
すべてスクリーナー自身が計算済みの判定・数値の言語化であり、ここで新しい
判断はしない(判断ロジックは vcp.py / entry.py / fundamentals.py 側が正)。

出力形式: {"headline": str, "points": [str], "cautions": [str]}
report.json の各銘柄の "summary" フィールドに格納され、フロント
(docs/assets/app.js の renderStockSummary)がそのまま表示する。
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from src.config import load_config
from src.screener.trend_template import quarter_sort_key

# エントリーステータスの日本語ラベル (docs/assets/app.js の STATUS_LABELS と対で
# 保守する。キーは entry.py / vcp.py が返すステータス文字列)。
STATUS_LABELS_JA = {
    "BREAKOUT": "本日のブレイクアウト",
    "BREAKOUT_WEAK": "ブレイクアウト(出来高不足)",
    "WATCH_A": "監視A(ピボット待ち)",
    "WATCH_B": "監視B(ベース形成中)",
    "EXTENDED": "伸びすぎ(追いかけ禁止)",
    "STALE": "ブレイク鮮度切れ(追いかけ禁止)",
    "REJECTED": "ベース不合格",
    "IMMATURE": "ベース形成中(日数不足)",
    "TOO_RECENT": "高値更新中(ベース未形成)",
    "TOO_VOLATILE": "ボラ過大(VCP評価対象外)",
    "NO_BASE": "ベース未検出",
}

# 決算接近の目安: 四半期開示はおよそ90日周期なので、前回開示からこの日数を
# 超えたら「次回発表が近い可能性」を注意点に出す。
EARNINGS_PROXIMITY_DAYS = 75


def _num(v, digits: int = 1) -> str:
    if v is None:
        return "-"
    return f"{float(v):,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{float(v):,.0f}"


def _signed_pct(v, digits: int = 1) -> str:
    if v is None:
        return "-"
    return f"{float(v):+.{digits}f}%"


def _vcp_fail_labels(config: dict) -> dict[str, str]:
    """V1〜V7不合格時の説明文。閾値はconfigから埋めてラベルの陳腐化を防ぐ。"""
    v = config["vcp"]
    lo, hi = v["contraction_count"]
    return {
        "V1": f"収縮回数が{lo}〜{hi}回の範囲外",
        "V2": "収縮の深さが段階的に減っていない",
        "V3": f"最初の収縮が{v['first_depth_max'] * 100:.0f}%超と深すぎ",
        "V4": f"最後の収縮が{v['last_depth_max'] * 100:.0f}%超でまだ緩い",
        "V5": f"出来高ドライアップ未達(直近10日の中央値が50日平均の{v['volume_dryup_median_ratio'] * 100:.0f}%以下になっていない)",
        "V6": f"ベース期間が{v['base_min_days']}〜{v['base_max_days']}日の範囲外",
        "V7": "収縮の安値が切り下がっている",
    }


def compute_momentum(df: pd.DataFrame) -> dict:
    """指標DataFrame(close/volume列)から直近の値動きサマリーを計算する。

    戻り値はreport.jsonに載るためJSON化可能な丸め済みの値のみ。
    """
    def chg(bars: int):
        if len(df) <= bars:
            return None
        cur = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-1 - bars])
        if prev == 0:
            return None
        return round((cur / prev - 1.0) * 100, 1)

    vol_ratio = None
    vol_median_ratio = None
    if "volume" in df.columns and len(df) >= 50:
        v10 = float(df["volume"].tail(10).mean())
        v50 = float(df["volume"].tail(50).mean())
        if v50 > 0:
            vol_ratio = round(v10 / v50, 2)
        v10_median = float(df["volume"].tail(10).median())
        v50_mean = v50
        if v50_mean > 0:
            vol_median_ratio = round(v10_median / v50_mean, 2)

    return {
        "chg_5d": chg(5),
        "chg_20d": chg(20),
        "chg_60d": chg(60),
        "vol_ratio_10_50": vol_ratio,
        "vol_median_ratio_10_50": vol_median_ratio,
    }


def yoy_series(quarters: list[dict], key: str, max_points: int = 4) -> list[tuple[str, float]]:
    """四半期リストから直近max_points個の前年同期比(%)系列を返す(古い順)。

    前年同期が無い/非正のときはその点をスキップする(trend_template.
    latest_yoy_growth と同じ「前年値<=0は計算不能」の扱い)。
    """
    by_label = {q.get("fiscal_quarter"): q for q in quarters if q.get("fiscal_quarter")}
    ordered = sorted(by_label, key=quarter_sort_key)

    out: list[tuple[str, float]] = []
    for label in ordered:
        try:
            prev_label = f"{int(label[:4]) - 1}{label[4:]}"
        except ValueError:
            continue
        cur = by_label[label].get(key)
        prev = (by_label.get(prev_label) or {}).get(key)
        if cur is None or prev is None or prev <= 0:
            continue
        out.append((label, round((cur - prev) / prev * 100, 1)))
    return out[-max_points:]


def derive_guidance_view(quarters: list[dict], guidance: dict | None,
                         close: float | None = None) -> dict | None:
    """会社予想(ガイダンス)を実績四半期と突き合わせた解釈済みビューを作る。

    guidance: jquants.record_to_guidance 由来 {fy_start, per_n, disc_date,
    feps, fsales, nx_feps, nx_fsales, shares_fy}。

    「進行期の計画」を選ぶ: 本決算(per_n=4)開示なら翌期予想(NxF系)、
    四半期開示・予想修正なら当期予想(F系)。計画YoYは前期の実績四半期4本の
    合計と比較(4本揃わなければNone)。進捗率は計画期の実績四半期累計÷計画。
    予想PER = 終値÷計画EPS。計算不能な項目はNoneのまま返し、全部Noneなら
    ビュー自体をNoneにする。
    """
    if not guidance:
        return None
    fy_start = guidance.get("fy_start") or ""
    if len(fy_start) < 4:
        return None
    try:
        fy_year = int(fy_start[:4])
    except ValueError:
        return None

    per_n = guidance.get("per_n")
    if per_n == 4 and (guidance.get("nx_feps") is not None or guidance.get("nx_fsales") is not None):
        plan_year = fy_year + 1
        eps_plan = guidance.get("nx_feps")
        sales_plan = guidance.get("nx_fsales")
    else:
        plan_year = fy_year
        eps_plan = guidance.get("feps")
        sales_plan = guidance.get("fsales")
    if eps_plan is None and sales_plan is None:
        return None

    by_label = {q.get("fiscal_quarter"): q for q in quarters or [] if q.get("fiscal_quarter")}

    def fy_sum(year: int, key: str, require_full: bool):
        vals = [(by_label.get(f"{year}Q{n}") or {}).get(key) for n in (1, 2, 3, 4)]
        present = [v for v in vals if v is not None]
        if require_full and len(present) < 4:
            return None, 0
        return (sum(present) if present else None), len(present)

    def plan_yoy(plan, prior):
        if plan is None or prior is None or prior <= 0:
            return None
        return round((plan - prior) / prior * 100, 1)

    prior_eps, _ = fy_sum(plan_year - 1, "eps", require_full=True)
    prior_sales, _ = fy_sum(plan_year - 1, "revenue", require_full=True)
    ytd_eps, n_eps_q = fy_sum(plan_year, "eps", require_full=False)
    ytd_sales, n_sales_q = fy_sum(plan_year, "revenue", require_full=False)

    def progress(ytd, plan):
        if ytd is None or plan is None or plan <= 0:
            return None
        return round(ytd / plan * 100, 1)

    per = None
    if close is not None and eps_plan is not None and eps_plan > 0:
        per = round(close / eps_plan, 1)

    view = {
        "plan_fy": plan_year,
        "eps_plan": eps_plan,
        "sales_plan": sales_plan,
        "eps_plan_yoy": plan_yoy(eps_plan, prior_eps),
        "sales_plan_yoy": plan_yoy(sales_plan, prior_sales),
        "eps_progress_pct": progress(ytd_eps, eps_plan),
        "sales_progress_pct": progress(ytd_sales, sales_plan),
        "quarters_reported": max(n_eps_q, n_sales_q),
        "forward_per": per,
        "disc_date": guidance.get("disc_date"),
    }
    return view


def _trend_word(values: list[float]) -> str | None:
    """3点以上の系列が単調に増加/減少していれば「加速中」/「減速中」を返す。"""
    if len(values) < 3:
        return None
    tail = values[-3:]
    if tail[0] < tail[1] < tail[2]:
        return "加速中"
    if tail[0] > tail[1] > tail[2]:
        return "減速中"
    return None


# ---------------------------------------------------------------------------
# Headline (状態と次のアクション)
# ---------------------------------------------------------------------------

def _build_headline(record: dict, config: dict) -> str:
    status = record.get("status")
    vcp_cfg = config["vcp"]
    vd = record.get("vcp_detail") or {}
    pivot = record.get("pivot")
    dist = record.get("dist_to_pivot")

    if status == "BREAKOUT":
        return f"本日ピボット{_num(pivot)}円をブレイクアウト。"

    if status == "BREAKOUT_WEAK":
        mult = config["entry"]["breakout_vol_mult"]
        return (f"ピボット{_num(pivot)}円を上抜けたが出来高が基準(50日平均×{mult})に届いていない。"
                "だまし・失速に注意。")

    if status == "WATCH_A":
        pos = (f"あと{_signed_pct(dist)}" if dist is not None and dist > 0
               else f"ピボット圏内({_signed_pct(dist)})" if dist is not None else "接近中")
        return (f"セットアップ完成。ピボット{_num(pivot)}円まで{pos}。"
                f"逆指値{_num(record.get('buy_stop'))}円・損切り{_num(record.get('stop_loss'))}円"
                f"(リスク{_num(record.get('risk_pct'), 1)}%)でブレイクアウト待ち。")

    if status == "WATCH_B":
        flags = (record.get("must_flags") or {}).get("vcp") or {}
        labels = _vcp_fail_labels(config)
        unmet = [labels[k] for k, v in flags.items() if v is False]
        detail = f" 残る課題: {'、'.join(unmet)}。" if unmet else ""
        return f"ベースは有効、仕上がり待ち。{detail}"

    if status == "EXTENDED":
        ext = config["entry"]["extended_pct"] * 100
        return f"ピボットから{ext:.0f}%超上放れており追いかけ買いは禁止。次のベース形成まで待機。"

    if status == "STALE":
        stale_days = config["entry"].get("breakout_stale_days")
        age = record.get("breakout_age_days")
        age_part = f"初回ブレイクから{age}日経過。" if age is not None else ""
        return (f"ピボット{_num(pivot)}円のブレイクは鮮度切れ({age_part}"
                f"買いチャンスは突破後{stale_days}日以内)。追いかけ買いは禁止、次のベース形成まで待機。")

    if status == "REJECTED":
        flags = (record.get("must_flags") or {}).get("vcp") or {}
        labels = _vcp_fail_labels(config)
        unmet = [labels[k] for k, v in flags.items() if v is False]
        detail = f": {'、'.join(unmet)}" if unmet else ""
        return f"VCP不合格{detail}。ベースの再構築を待つ。"

    if status == "IMMATURE":
        base_days = vd.get("base_days")
        need = vcp_cfg["base_min_days"]
        if base_days is not None:
            return (f"ベース形成{base_days}営業日目(評価には{need}日必要、あと{max(need - base_days, 0)}日)。"
                    "形成が進めば自動的に判定対象になる。")
        return f"ベース形成中(評価には{need}日必要)。"

    if status == "TOO_RECENT":
        days = vd.get("days_from_high")
        need = vcp_cfg["min_days_from_high"]
        if days is not None:
            return (f"高値をつけてからまだ{days}営業日(ベース起点の判定には{need}日必要)。"
                    "上昇が一服してベースを作り始めたら評価対象になる。")
        return "高値更新直後でベース未形成。上昇一服待ち。"

    if status == "NO_BASE":
        return f"直近{vcp_cfg['scan_days_extended']}営業日に有効なベース起点が見つからない。"

    return STATUS_LABELS_JA.get(status, status or "状態不明")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def build_stock_summary(
    record: dict,
    quarters: list[dict] | None = None,
    guidance: dict | None = None,
    market_signal: dict | None = None,
    config: dict | None = None,
    today: date | None = None,
) -> dict:
    """1銘柄分のサマリー {headline, points, cautions} を組み立てる。

    record: build_site.assemble_stock_record + attach_priority 済みのレコード
    (momentum / vcp_detail / sector33等が付いていれば使う。無くても壊れない)。
    """
    config = config or load_config()
    today = today or datetime.now().date()
    fcfg = config["fundamentals"]

    points: list[str] = []
    cautions: list[str] = []

    headline = _build_headline(record, config)
    if record.get("new_breakout_today"):
        points.append("昨日まで監視A → 本日新規ブレイクアウト。")
    if record.get("market_guard_warning"):
        cautions.append("TOPIX急落日のブレイクアウト(マーケットガード発動)。見送り推奨。")

    # --- 根拠: テクニカル ---
    rs = record.get("rs")
    high_dist = record.get("high52w_distance_pct")
    tt_line = "トレンドテンプレート8条件すべて合格"
    if rs is not None:
        tt_line += f"(RS {rs}"
        tt_line += f"、52週高値まで-{_num(high_dist)}%)" if high_dist is not None else ")"
    points.append(tt_line + "。")

    vd = record.get("vcp_detail") or {}
    depths = vd.get("depths_pct") or []
    if depths and vd.get("base_days"):
        weeks = round(vd["base_days"] / 5)
        seq = " → ".join(f"{d:.0f}%" for d in depths)
        line = f"ベース{vd['base_days']}営業日(約{weeks}週)・収縮{len(depths)}回: {seq}"
        if len(depths) >= 2 and depths[-1] < depths[0]:
            line += " と順当にタイト化"
        points.append(line + "。")

    mom = record.get("momentum") or {}
    if mom.get("chg_20d") is not None or mom.get("chg_60d") is not None:
        points.append(
            f"騰落率: 20日{_signed_pct(mom.get('chg_20d'))} / 60日{_signed_pct(mom.get('chg_60d'))}。")
    median_ratio = mom.get("vol_median_ratio_10_50")
    if median_ratio is not None:
        dry = config["vcp"]["volume_dryup_median_ratio"]
        line = f"出来高: 直近10日中央値は50日平均の{median_ratio * 100:.0f}%"
        line += "(ドライアップ水準)。" if median_ratio <= dry else "。"
        points.append(line)

    if record.get("sector33"):
        strength = record.get("sector_strength") or "-"
        direction = record.get("sector_direction") or ""
        points.append(f"セクター「{record['sector33']}」は強度{strength}{direction}。")

    # --- 需給(信用取引週末残高。表示専用・総合スコアには一切使わない) ---
    margin = record.get("margin")
    if margin:
        if margin.get("ratio") is not None:
            line = f"需給: 信用倍率{_num(margin['ratio'], 2)}倍(買残{_num(margin.get('buy'), 0)}株/売残{_num(margin.get('sell'), 0)}株)"
        else:
            line = f"需給: 売残なし(買残{_num(margin.get('buy'), 0)}株)"
        dtc = margin.get("days_to_cover")
        if dtc is not None:
            line += f"、買残回転日数{_num(dtc, 1)}日分"
        badge = margin.get("badge")
        if badge == "heavy_buy":
            cautions.append(line + "。買残が重く上値の重さに注意。")
        elif badge == "short":
            points.append(line + "。売り長で踏み上げ余地あり。")
        else:
            points.append(line + "。")

    mcap = record.get("market_cap_oku")
    if mcap is not None:
        cls = "小型" if mcap < 500 else "中型" if mcap < 3000 else "大型"
        seg = record.get("market_segment")
        points.append(f"時価総額 約{mcap:,.0f}億円({cls}株" + (f"・{seg}" if seg else "") + ")。")

    # --- ファンダ ---
    eps_yoy = record.get("fund_eps_yoy")
    rev_yoy = record.get("fund_rev_yoy")
    if record.get("fund_strong"):
        points.append(
            f"ファンダ本命基準クリア: 直近EPS YoY {_signed_pct(eps_yoy)} / 売上YoY {_signed_pct(rev_yoy)}。")
    if quarters:
        series = yoy_series(quarters, "eps")
        if len(series) >= 2:
            seq = " → ".join(_signed_pct(v) for _, v in series)
            trend = _trend_word([v for _, v in series])
            points.append(f"EPS YoY推移(直近{len(series)}Q): {seq}" + (f"({trend})" if trend else "") + "。")

    # --- 会社予想(ガイダンス)と進捗率 ---
    gv = derive_guidance_view(quarters or [], guidance, close=record.get("close"))
    if gv:
        yoy_parts = []
        if gv["eps_plan_yoy"] is not None:
            yoy_parts.append(f"EPS {_signed_pct(gv['eps_plan_yoy'])}")
        if gv["sales_plan_yoy"] is not None:
            yoy_parts.append(f"売上 {_signed_pct(gv['sales_plan_yoy'])}")
        if yoy_parts:
            line = f"会社計画({gv['plan_fy']}年度): 前期比 {'・'.join(yoy_parts)}"
        elif gv["eps_plan"] is not None:
            line = f"会社計画({gv['plan_fy']}年度): EPS {_num(gv['eps_plan'], 2)}円"
        else:
            line = f"会社計画({gv['plan_fy']}年度)開示あり"
        prog = gv["eps_progress_pct"] if gv["eps_progress_pct"] is not None else gv["sales_progress_pct"]
        if prog is not None and gv["quarters_reported"]:
            label = "EPS" if gv["eps_progress_pct"] is not None else "売上"
            line += f"。進捗率 {label} {prog:.0f}%(Q{gv['quarters_reported']}時点)"
        points.append(line + "。")
        if gv["forward_per"] is not None:
            points.append(f"予想PER {gv['forward_per']}倍(会社計画EPSベース)。")

        # 誤読防止: 直近四半期の実績YoYと通期計画YoYの向きが食い違う場合は明示する。
        plan_eps_yoy = gv["eps_plan_yoy"]
        if eps_yoy is not None and plan_eps_yoy is not None:
            if eps_yoy < 0 <= plan_eps_yoy:
                points.append(f"直近四半期は減益だが会社計画は通期増益({_signed_pct(plan_eps_yoy)})。")
            elif plan_eps_yoy < 0 <= eps_yoy:
                cautions.append(f"直近四半期は増益だが会社計画は通期減益({_signed_pct(plan_eps_yoy)})。")

        # 進捗が経過四半期数から見て±15pt以上ずれていたら言及する。
        if gv["eps_progress_pct"] is not None and gv["quarters_reported"] in (1, 2, 3):
            expected = gv["quarters_reported"] / 4 * 100
            if gv["eps_progress_pct"] < expected - 15:
                cautions.append(
                    f"通期計画に対する進捗が低調(Q{gv['quarters_reported']}時点で{gv['eps_progress_pct']:.0f}%、"
                    f"目安{expected:.0f}%)。下方修正リスクに注意。")
            elif gv["eps_progress_pct"] > expected + 15:
                points.append(
                    f"通期計画に対する進捗が良好(Q{gv['quarters_reported']}時点で{gv['eps_progress_pct']:.0f}%、"
                    f"目安{expected:.0f}%)。上方修正余地。")

    # サイズ係数(fund_verdict)ベースの注意喚起 (2026-07-22)。
    # fail=取り止め / unknown=ハーフ / 旧report.json(フィールド無し)は従来文言。
    coverage = record.get("fund_coverage")
    verdict = record.get("fund_verdict")
    if verdict == "fail":
        cautions.append(
            f"ファンダ不合格→エントリー取り止め(サイズ係数0): 直近EPS YoY {_signed_pct(eps_yoy)}・"
            f"売上YoY {_signed_pct(rev_yoy)}"
            f"(基準 EPS+{fcfg['confirmed_eps_yoy_min']:.0f}%/売上+{fcfg['confirmed_rev_yoy_min']:.0f}%に未達)。"
            "セットアップが完成しても見送り。")
    elif verdict == "unknown":
        cautions.append(
            "ファンダ強度未確認(サイズ係数0.5): エントリーする場合はハーフサイズ推奨。"
            + ("データ自体なし。" if coverage == "none" else "前年比較が計算不能。"))
    elif coverage == "none":
        cautions.append("ファンダデータなし。EPS・売上の裏付け未確認。")
    elif record.get("fund_strong") is False:
        cautions.append(
            f"ファンダ弱: 直近EPS YoY {_signed_pct(eps_yoy)}・売上YoY {_signed_pct(rev_yoy)}"
            f"(本命基準 EPS+{fcfg['confirmed_eps_yoy_min']:.0f}%/売上+{fcfg['confirmed_rev_yoy_min']:.0f}%に未達)。")

    # 決算発表タイミング: カレンダーの正確な予定日があればそれを使い、
    # 無い銘柄(3月期・9月期以外など)は前回開示からの経過日数で推定する。
    next_earnings = record.get("next_earnings_date")
    days_to_earnings = None
    if next_earnings:
        try:
            days_to_earnings = (date.fromisoformat(str(next_earnings)[:10]) - today).days
        except ValueError:
            days_to_earnings = None
    if days_to_earnings is not None:
        if days_to_earnings <= 14:
            cautions.append(
                f"決算発表予定 {next_earnings}(あと{days_to_earnings}日)。発表跨ぎのエントリーに注意。")
        else:
            points.append(f"次回決算発表予定: {next_earnings}。")

    checked = record.get("fund_checked_date")
    if checked:
        try:
            days_since = (today - pd.to_datetime(checked).date()).days
        except (ValueError, TypeError):
            days_since = None
        if record.get("fund_stale"):
            cautions.append(f"ファンダ確認日({checked})が古く、最新四半期が未反映の可能性。")
        elif (days_to_earnings is None and days_since is not None
              and days_since >= EARNINGS_PROXIMITY_DAYS):
            cautions.append(
                f"前回の決算確認から{days_since}日経過。次回決算発表が近い可能性があり、発表跨ぎのエントリーに注意。")

    # --- 注意: 伸びすぎ・リスク幅 ---
    dev = record.get("ma_deviation_pct") or {}
    if dev.get("ma50") is not None and dev["ma50"] > 15:
        cautions.append(f"MA50乖離{_signed_pct(dev['ma50'])}と伸びた位置。押し目の無い飛び乗りは避ける。")
    risk = record.get("risk_pct")
    if risk is not None and risk > 7:
        cautions.append(f"逆指値→損切り幅が{_num(risk)}%と広め。ポジションサイズで調整を。")

    # --- 地合い ---
    if market_signal:
        sig = market_signal.get("signal")
        reason = (market_signal.get("reasons") or [None])[0]
        if sig == "red":
            cautions.append("地合いシグナル🔴(守り)" + (f": {reason}" if reason else "") + "。新規エントリーは抑制。")
        elif sig == "yellow":
            cautions.append("地合いシグナル🟡(中立)" + (f": {reason}" if reason else "") + "。ポジションは控えめに。")
        elif sig == "green":
            points.append("地合いシグナル🟢(攻め)" + (f": {reason}" if reason else "") + "。")

    return {"headline": headline, "points": points, "cautions": cautions}
