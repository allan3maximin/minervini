"""A レイヤ dict(`prep.build_a_layer` の戻り値)をマークダウンに整形する。

DESIGN_DEEPDIVE.md §5 sheet.py。末尾の「この期に出せなかったもの」「使ったデータの鮮度」は
**必須**(§5・§6.1: 鮮度の明記が無いと12週遅延データを最新だと誤読しかねない)。
純関数のみ、ファイル I/O はしない(書き込みは cli.py 側で `utils_io.atomic_write_text` を使う)。
"""
from __future__ import annotations


def _fmt_pct(v, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:+.{digits}f}%"


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v.is_integer():
        return f"{int(v):,}"
    if isinstance(v, float):
        return f"{v:,.1f}"
    return f"{v:,}"


def _fmt_ratio(v, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _period_note(n: int, min_n: int = 3) -> str:
    return "" if n >= min_n else "(参考値: n少)"


def _render_progress(a: dict) -> list[str]:
    lines = ["## 進捗"]
    for label, key in (("売上", "sales"), ("営業利益", "op")):
        block = a["progress"][key]
        hist = a["progress_vs_history"][key]
        if block["value"] is None:
            lines.append(f"- {label}: データ不足で算出不能")
            continue
        lines.append(
            f"- {label}: 進捗率 {_fmt_pct(block['value'])}"
            f"({block['period']}時点、YTD {_fmt_num(block['ytd'])} / 計画 {_fmt_num(block['plan'])})"
        )
        if hist["n"] > 0:
            lines.append(
                f"  - 過去同時点との差分: {_fmt_pct(hist['diff_pt'])} pt (n={hist['n']}) {_period_note(hist['n'])}"
            )
        else:
            lines.append("  - 過去同時点との比較: 比較対象データなし(n=0)")

    gg = a["guidance_gap"]
    if gg["n"] > 0:
        median_str = _fmt_pct(gg["median"]) if gg["median"] is not None else "算出せず(n<3)"
        values_str = ", ".join(_fmt_pct(v) for v in gg["values"])
        lines.append(f"- 期初予想→着地 乖離率: 中央値 {median_str} (n={gg['n']}) [{values_str}]")
    else:
        lines.append("- 期初予想→着地 乖離率: データ不足で算出不能")

    rev = a["revision"]
    if rev["n"] > 0:
        direction_ja = {"up": "上方", "down": "下方", "mixed": "上下混在", None: "変化なし"}
        lines.append(
            f"- 期中修正(通期営業利益予想の変化・簡易代用): {rev['count']}回 "
            f"{direction_ja.get(rev['direction'], '—')} (当期開示 n={rev['n']})"
        )
    else:
        lines.append("- 期中修正: データ不足で算出不能")

    return lines


def _render_valuation(a: dict) -> list[str]:
    lines = ["## バリュエーション"]
    per = a["per"]
    if per["n"] > 0 and per["current"] is not None:
        lines.append(
            f"- PER {_fmt_ratio(per['current'])}倍  {per['pct']:.0f}%タイル"
            f"(観測期間 {per['start']}〜{per['end']}・n={per['n']}日、EPS 2年レンジで代用)"
        )
    else:
        lines.append("- PER: データ不足で算出不能")
    return lines


def _render_price_action(a: dict) -> list[str]:
    lines = ["## 値動き"]
    for label, block in a["returns"].items():
        lines.append(
            f"- {label}騰落率: {_fmt_pct(block['abs'])}"
            f"(TOPIX比・1306で代用 {_fmt_pct(block['topix_relative'])})"
        )

    sr = a["sector_relative"]
    if sr["sector"]:
        lines.append(f"- 同業({sr['sector']}、母集団 n={sr['n_peer_codes']})との比較:")
        for label, block in sr["windows"].items():
            if block["n"] > 0:
                lines.append(
                    f"  - {label}: 同業中央値比 {_fmt_pct(block['value'])} pt "
                    f"(同業中央値 {_fmt_pct(block['median'])}, n={block['n']})"
                )
            else:
                lines.append(f"  - {label}: 同業データなし")
    else:
        lines.append("- 同業比: 業種不明のため算出不能")

    since = a["since_earnings_return"]
    if since["value"] is not None:
        lines.append(f"- 前回決算発表日({since['since']})からの騰落率: {_fmt_pct(since['value'])}")
    else:
        lines.append("- 前回決算発表日からの騰落率: データ不足で算出不能")

    vol = a["volume_ratio"]
    vol_parts = []
    for key, value in vol.items():
        short, long_ = key.split("_")
        vol_parts.append(f"{short}日/{long_}日 {_fmt_ratio(value)}倍" if value is not None else f"{short}日/{long_}日 —")
    lines.append(f"- 出来高比: {', '.join(vol_parts)}")

    return lines


def _render_earnings_date(a: dict) -> list[str]:
    ned = a["next_earnings_date"]
    if ned["date"]:
        return [f"## 次回決算発表予定", f"- {ned['date']}(出典: {ned['source']})"]
    return ["## 次回決算発表予定", f"- 不明(出典: {ned['source']})"]


def _render_omitted(a: dict) -> list[str]:
    lines = ["## この期に出せなかったもの"]
    for item in a["omitted"]:
        lines.append(f"- {item['item']}: {item['reason']}")
    return lines


def _render_freshness(a: dict) -> list[str]:
    fresh = a["data_freshness"]
    price = fresh["price"]
    raw = fresh["raw"]
    price_line = (
        f"株価: {price['path']} {price['latest']} まで" if price["latest"]
        else f"株価: {price['path']}(日付取得不能)"
    )
    raw_line = (
        f"財務: {raw['path']} 最終開示 {raw['latest_disc_date']}"
        f"(J-Quants Free は12週遅延)" if raw["latest_disc_date"]
        else f"財務: {raw['path']}(データなし。fetch 未実行の可能性)"
    )
    lines = ["## 使ったデータの鮮度", price_line, raw_line]
    benchmark = fresh.get("benchmark")
    if benchmark is not None:
        bench_line = (
            f"ベンチマーク: {benchmark['path']} {benchmark['latest']} まで"
            f"(TOPIX指数^TPXが実質取得不能なため、TOPIX連動ETF・配当込みの1306で代用)"
            if benchmark["latest"]
            else f"ベンチマーク: {benchmark['path']}(日付取得不能)"
        )
        lines.append(bench_line)
    return lines


def render(a: dict) -> str:
    """A レイヤ dict をマークダウン文字列にする(§5・§6.1)。"""
    header = [
        f"# {a['code']} {a['name'] or ''} — {a['quarter']} 準備シート".rstrip(),
        "",
        f"生成日時: {a['generated_at']}",
        "",
    ]
    sections = [
        _render_progress(a),
        _render_valuation(a),
        _render_price_action(a),
        _render_earnings_date(a),
        _render_omitted(a),
        _render_freshness(a),
    ]
    body = []
    for i, sec in enumerate(sections):
        body.extend(sec)
        body.append("")
    return "\n".join(header + body).rstrip() + "\n"
