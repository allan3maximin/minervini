# 設計書: 深掘り銘柄分析ツール — src/deepdive/

対象: Sonnet がこれ1本を読んで実装できる粒度で書く。要件定義は別途ユーザーから提示済み。
本書は「要件をこのリポジトリの現物にどう落とすか」だけを扱う。

作成: 2026-08-25

---

## 0. 何を作るのか / 既存スクリーナーとの関係

ミネルヴィニ式スクリーナー(以下スクリーナー)は**エッジを見つける**道具で、母集団は
1600銘柄、判定は統計で行う。本ツールは**エッジを見つけない**。2〜5銘柄について
「決算前に期待値を書き、記録し、後で答え合わせする」ことだけを担う。

したがって以下を厳守する。

- **日次パイプライン(`src/pipeline.py :: run_daily`)には一切割り込まない。** import もしない。
  毎朝のバッチが本ツールのバグで止まる経路を作らないこと。
- **`docs/` に何も出さない。** フロントエンドは無関係。出力はマークダウンと JSONL のみ。
- **`data/prices/` に書かない。** 読むだけ。
- 共有するのは「読み取り専用のユーティリティ」だけ(§2)。

### 既存スクリーナーとの棲み分け早見

| | スクリーナー | 深掘りツール |
|---|---|---|
| 母集団 | 約1600銘柄 | 2〜5銘柄 |
| 判定 | 統計(件数で殴る) | 記録(件数は原理的に足りない) |
| 正のデータ | 自動取得 | **手入力** |
| 実行 | GitHub Actions 日次 | ローカル手動 |
| 出力 | report.json → SPA | マークダウン → Obsidian |

---

## 1. 先に潰しておく前提の穴(実装前に必ず読むこと)

設計時点で現物を確認した結果、要件定義の記述のまま作ると**動かない箇所が4つ**ある。
実装者はここを飛ばさないこと。

### 1.1 営業利益がこのリポジトリのどこにも無い ★最重要★

要件の中核は「自分の予想(**営業利益**)」だが、現状の保存物は売上・EPS・純利益しか持たない。

```
src/data/jquants.py :: record_to_point → eps / revenue / ni / shares のみ
src/data/edinetdb.py :: record_to_point → eps / revenue のみ
data/fundamentals_auto.json の quarters → {fiscal_quarter, eps, revenue, ni, disc_date}
```

リポジトリ全体を `OpProfit|OrdProfit|営業利益` で grep しても**ヒットゼロ**。
J-Quants の `/fins/summary` レスポンス自体には営業利益が入っているはずだが、
既存コードが拾っていないだけなので、**フィールド名を実地で確認するところから始める**(§7)。

対応方針: 既存の `src/data/jquants.py` は**触らない**。スクリーナーの本番経路なので、
営業利益を足すために手を入れるとリグレッションの矢面に立つ。深掘り用に
**独立した生データ取得層 `src/deepdive/jq_raw.py` を新設**し、API のレスポンスを
加工せずそのまま JSONL に落とす。加工は下流で行う。

### 1.2 ウォッチ銘柄がユニバースに入っていない

`data/fundamentals_auto.json` を引くと **7134(アップガレージ)は null**。
20日平均売買代金1億円という足切りに引っかかってユニバース外だからで、
バグではない。7611(ハイデイ日高)は入っている。

つまり**「ウォッチ銘柄 ⊄ ユニバース」を前提に組む**こと。
日別取得(`?date=`)は使えない。必ず `?code=` の銘柄指定で取る。

価格キャッシュも同様に穴がある(2026-08-25時点):

| | data/prices/ | data/prices_long/ |
|---|---|---|
| 7134 | **無し** | あり |
| 7611 | あり | あり |

`data/prices/` は `config.yaml: data.history_days`(520)で `tail()` 切り捨てされるため
構造的に約2年しか残らない。**5年レンジのパーセンタイルを出すには `data/prices_long/` を
使う**(`tools/fetch_long_history.py` が2000年以降を貯めている)。
無い銘柄は本ツールから `tools/fetch_long_history.py` を呼んで埋める(§4.2)。

なお `data/prices/` と `data/prices_long/` は**両方とも .gitignore 済み**。
消えていることを異常終了ではなく通常フローとして扱い、「取得してください」と案内すること。

### 1.3 J-Quants Free は2年しか返さない → 「過去5期」は原理的に出せない

`tools/event/fetch_jq.py` の冒頭に検証済みの記述がある。Free プランは
「12週間前 〜 2年12週間前」しか返さない。保存側の切り詰めではないので、
コードでは直らない。

要件の以下は**そのままでは出せない**:

- 過去5期の「期初予想 → 着地」乖離率 → **取れるのは概ね2期**
- 過去3期の同時点進捗率 → **取れるのは概ね1〜2期**
- バリュエーションの5年レンジ → **株価は5年取れるが、EPS が2年しか無いので PER の5年レンジは作れない**

ユーザー合意済みの方針: **取れる範囲で出し、件数(期数)を必ず併記する。**
出力に `n=2` のように書き、n が閾値未満なら「参考値」と明示する。
将来 Light 以上に上げたら自動で伸びる作りにしておくこと(期数をハードコードしない)。

### 1.4 決算発表予定日は2社のうち1社しか取れない

`data/earnings_calendar.json` は `src/data/jquants.py :: update_earnings_calendar` が作るが、
**提供側の制約で3月期・9月期決算企業のみ**。7611(ハイデイ日高)は `fy_start: "2025-03-01"`
= **2月期決算**なのでカレンダーに載らない。

対応: カレンダー → 無ければ**前年同期の開示日から推定**(`data/deepdive/raw/{code}.jsonl` の
`DiscDate` の前年同四半期 + 曜日補正なし)→ それも無ければ手入力(`watchlist` の
`next_earnings_date_manual`)。3段フォールバックにする。

---

## 2. 既存資産のうち流用してよいもの

読み取り専用で使う。**いずれも書き込み側には回さない。**

| 使うもの | 何に使う |
|---|---|
| `src/config.py :: load_config, REPO_ROOT` | 設定とパス解決。新規セクション `deepdive:` を読む |
| `src/history_store.py :: append_records, iter_records, count_lines` | JSONL の追記と読み出し |
| `src/data/prices.py :: load_cache(code)` | `data/prices/` の読み出し(短期用。読むだけ) |
| `src/utils_io.py :: atomic_write_text` | マークダウン出力の書き込み |
| `data/sector_map.json` | 同業比の母集団(東証33業種) |
| `data/prices_asset/jp_1306.parquet` | TOPIX比較用ベンチマーク(TOPIX連動ETF・配当込み。§4.2.1参照) |
| `data/universe.json` の `stocks[].shares_outstanding` | 時価総額(ユニバース内銘柄のみ) |
| `data/earnings_calendar.json` | 発表予定日の第1候補 |
| `tools/fetch_long_history.py` | `data/prices_long/` の穴埋め(subprocess で呼ぶ) |

**`src/history_store.py :: load_deduped` は使わない。** あれは**後勝ち**(last-write-wins)で、
「同日再実行は既存を置換」という日次バッチの意味論を実現するためのもの。
本ツールの予想レコードは逆に**初出勝ち**でなければならない(§3.3 R2)。専用の
`store.load_first_wins` を書くこと。ここを取り違えると規律ルールが丸ごと無効になる。

---

## 3. データモデル

保存先は全て `data/deepdive/`。**このディレクトリは .gitignore に足さない**
(履歴自体をバージョン管理するのが要件。`data/prices/` 等が ignore されているのと対照的)。

```
data/deepdive/
  watchlist.jsonl        銘柄マスタ
  predictions.jsonl      予想(固定フィールド)  ← 初出勝ち
  actuals.jsonl          実績(Bレイヤ手入力)
  notes.jsonl            自由記述(Cレイヤ)
  model_versions.jsonl   判断ロジックの変更ログ
  raw/{code}.jsonl       J-Quants 生レコード     ← .gitignore する
  prep/{code}_{quarter}.md  準備シート出力       ← コミットする
```

`raw/` だけ `.gitignore` に1行足す(既存の `data/raw/` の直後に `data/deepdive/raw/`)。
生 API レスポンスは再取得できるうえ嵩むため。

全ファイル共通で、レコードには必ず `written_at`(ISO8601、**実行時刻をコードで埋める**。
引数で受け取らない)を入れる。これが R1 の実効的な防御になる。

### 3.1 watchlist.jsonl

キー: `(ticker,)`。**後勝ち**でよい(マスタは書き換わる前提)。

```json
{
  "ticker": "7134",
  "name": "アップガレージグループ",
  "fy_end_month": 3,
  "has_monthly": true,
  "monthly_url_pattern": "https://www.upgarage.com/ir/monthly/{yyyymm}/",
  "drivers": "既存店売上 = 客数 × 客単価。買取台数が先行指標",
  "break_conditions": "既存店が2ヶ月連続マイナス / 買取単価の上昇で粗利率が3pt以上低下",
  "next_earnings_date_manual": null,
  "status": "active",
  "written_at": "2026-08-25T10:00:00+09:00"
}
```

`drivers` と `break_conditions` が**空文字なら登録を拒否する**(要件 UC-1: ここが書けない
銘柄は登録しない = 適性判定を兼ねる)。バリデーションであってオプションではない。

### 3.2 predictions.jsonl(固定フィールド・初出勝ち)

キー: `(ticker, quarter, model_ver)`。

```json
{
  "ticker": "7134",
  "quarter": "2026Q2",
  "earnings_date": "2026-11-07",
  "written_at": "2026-08-25T10:00:00+09:00",
  "company_op": 1200000000,
  "my_op": 1350000000,
  "confidence": "中",
  "priced_in_1m_vs_topix": 3.1,
  "action": "買う",
  "model_ver": "v1",
  "rationale": "既存店が3ヶ月連続で+5%超。会社予想は例年どおり保守的",
  "valid": true,
  "invalid_reason": null
}
```

- `confidence` は `高|中|低` の3値。それ以外は拒否。
- `action` は `買う|買わん|保有継続` の3値。それ以外は拒否。
- `priced_in_1m_vs_topix` は**手で書かせない**。記入時に §4 の A レイヤから自動で埋める。
  「織り込み」は後から都合よく書き換えられる余地を残してはいけない値。
- `valid` / `invalid_reason` は R1 の判定結果(§3.3)。

### 3.3 規律ルールの実装

要件の R1〜R4 を、運用の心がけではなく**コードで強制する**。

**R1 — 発表後に書いた予想は集計から外す**

判定は `written_at`(実行時刻)と `earnings_date` を比べる。ただし要件の
「記入日 > 発表予定日」は日付粒度が粗すぎる。決算は**寄り前 / 場中 / 引け後**があるので、
`earnings_date` 当日に書いた予想は**当日中でも無効**にする(当日に書ける時点で
発表を見た可能性を排除できない)。

```python
valid = written_at.date() < date.fromisoformat(earnings_date)
```

無効でも**保存する**。`valid: false` と `invalid_reason: "記入日が発表日以降"` を立てて
残し、集計側で落とす。消したら R4 に反する。

**R2 — 発表後に固定フィールドを書き換えられない**

`load_first_wins(path, key_fields)` を実装する。同一キーの2行目以降は**読み捨てる**。
`history_store.load_deduped` の逆。

```python
def load_first_wins(path, key_fields: tuple[str, ...]) -> list[dict]:
    out: dict[tuple, dict] = {}
    for rec in history_store.iter_records(path):
        key = tuple(rec.get(f) for f in key_fields)
        if key not in out:          # ← 初出だけ採用。ここが load_deduped と真逆
            out[key] = rec
    return list(out.values())
```

さらに `predict` コマンド側で、同一キーが既に存在したら**追記そのものを拒否**して
終了コード1で落とす。ファイルを直接エディタで編集された場合の最後の砦が
`load_first_wins` で、通常操作の砦がコマンド側の拒否。二重にする。

**R3 — ロジック変更は ver 採番、遡及禁止**

`model_versions.jsonl` も初出勝ち。既存の `ver` に対する `add` は拒否する。
`predict` は `--ver` で既存 ver を指定するだけで、新規 ver は
`python -m src.deepdive ver add` でしか作れない。

**R4 — 削除コマンドを提供しない**

`delete` / `rm` サブコマンドを**作らない**。訂正は「新しい ver を切って書き直す」しかない。

### 3.4 actuals.jsonl(Bレイヤ・手入力)

キー: `(ticker, quarter)`。**後勝ち**にする(訂正短信・入力ミス修正がある。
予想と違い実績は客観的な事実なので、書き換えを禁じる理由が無い)。

```json
{
  "ticker": "7134", "quarter": "2026Q2",
  "disclosed_at": "2026-11-07", "timing": "引け後",
  "revenue": 8500000000, "op": 1380000000, "ord": 1390000000, "ni": 940000000,
  "cogs_ratio": 0.412, "sga_ratio": 0.455,
  "inventory": 3200000000, "inventory_days": 78,
  "segments": {"リユース": {"revenue": 6100000000, "op": 1050000000}},
  "stores": {"end": 132, "opened": 4, "closed": 1},
  "guidance_revised": true, "guidance_revision_pct": 6.2,
  "one_off": "店舗閉鎖損 80百万",
  "written_at": "..."
}
```

`timing` は `寄り前|場中|引け後` の3値。**必須**(翌日騰落率の起点が変わるため。
引け後なら翌営業日、寄り前なら当日が「発表翌日」に相当する)。

`segments` と `stores` は銘柄ごとに項目が違うので**自由な dict** にする。
スキーマを固定しない。集計では使わず、チャートと目視のためだけに使う。

### 3.5 実績の紐付けと的中判定

`actual` コマンドを実行したとき、同一 `(ticker, quarter)` の予想を**全 ver ぶん**引いて、
判定結果を `outcomes.jsonl` に書く(predictions を書き換えないため別ファイル)。

```json
{
  "ticker": "7134", "quarter": "2026Q2", "model_ver": "v1",
  "actual_op": 1380000000, "my_op": 1350000000, "company_op": 1200000000,
  "dir_hit": true,      // 会社予想に対する上振れ/下振れの向きが自分の予想と一致したか
  "level_err_pct": -2.2, // (my_op - actual_op) / actual_op * 100
  "ret_next_day": 5.8, "ret_5d": 7.1,
  "written_at": "..."
}
```

**方向の的中(`dir_hit`)の定義を実装前に固定する**: 「会社予想を上回るか下回るか」を
当てられたか。`sign(my_op - company_op) == sign(actual_op - company_op)`。
どちらかが 0 なら `None`(判定不能)にして集計から外す。
「前年同期比で増益か減益か」ではない。会社予想が織り込みの基準線だから。

`ret_next_day` / `ret_5d` は `timing` を見て起点を決める:

- `引け後` → 起点は発表日終値、`next_day` は翌営業日終値
- `寄り前` → 起点は前営業日終値、`next_day` は当日終値
- `場中` → 起点は前営業日終値、`next_day` は当日終値(場中は分離不能。割り切る)

---

## 4. A レイヤ(自動取得指標)の算出仕様

`src/deepdive/prep.py`。全ての値は `{"value": ..., "n": 2, "note": "..."}` の形で返し、
**件数 n を必ず持たせる**(§1.3)。n が無い指標は `n: null`。

### 4.1 出せる / 出せない一覧

| 指標 | 可否 | 出典 | 備考 |
|---|---|---|---|
| 通期予想に対する累計進捗率(売上/営業利益) | ○ | raw/{code}.jsonl | 営業利益は §7 の確認後 |
| 過去N期の同時点進捗率との差分 | △ | 同上 | **N は概ね1〜2。n を明記** |
| 期初予想→着地 乖離率 | △ | 同上 | **概ね2期ぶん。中央値は n<3 なら出さず全値を並べる** |
| 期中修正の回数と方向 | △ | 同上(`ForecastRevision`) | 2年ぶんのみ |
| PER 5年レンジ内パーセンタイル | **×** | — | **EPS が2年しか無い。PER 2年レンジで代替し、その旨を明記** |
| PBR 5年レンジ | **×** | — | BPS が取れない。**出さない**(欄ごと消す) |
| EV/EBITDA | **×** | — | 有利子負債・現金・減価償却が取れない。**出さない** |
| 配当利回り 5年レンジ | △ | yfinance | 配当は5年取れる。株価も5年。**これは唯一5年で出せる** |
| 1M/3M 騰落率(絶対/TOPIX比) | ○ | prices_long + prices_asset/jp_1306.parquet | **TOPIX比は`jp_1306`(TOPIX連動ETF・配当込み)で代用(2026-08-25変更。§4.2.1参照)** |
| 同業比 | ○ | sector_map.json + prices_long | 同一33業種の中央値との差 |
| 前回決算発表日からの騰落率 | ○ | raw の DiscDate + prices_long | |
| 出来高 5日/60日, 5日/20日 | ○ | prices_long | |
| 月次 既存店前年比 | **手入力** | — | 自動化しない(要件の非対象) |

**出せないものは欄を作らない。** 「取得失敗」と書いた空欄を毎回見るのは、
そこに何かあるはずだという誤った期待を持ち続けることになる。
消したうえで、シート末尾の「この期に出せなかったもの」節に理由を1行ずつ書く。

### 4.2 価格系の取得

```python
def load_long_prices(code: str) -> pd.DataFrame | None:
    """data/prices_long/{code}.parquet を読む。無ければ None。"""
```

`None` の場合、**自動で取りに行かない**。以下を stderr に出して終了コード2で落とす:

```
7134 の長期株価がありません。先に取得してください:
  .venv/bin/python tools/fetch_long_history.py --only 7134
```

**★ `--only` は現状まだ無い。実装すること。** `tools/fetch_long_history.py` の
オプションは `--codes {all,universe}` の2択しかなく(2026-08-25 時点)、
銘柄を1つだけ取る手段が無い。`--codes all` は約3700銘柄で数十分かかる。

`load_codes()`(313行目)に分岐を1つ足すだけで済む:

```python
ap.add_argument("--only", default="", help="カンマ区切りの銘柄コードだけ取る")
...
codes = [c.strip() for c in args.only.split(",") if c.strip()] if args.only \
        else load_codes(args.codes)
```

`tools/fetch_long_history.py` は**研究用ツールで日次パイプラインから呼ばれない**ので
(本番の株価取得は `src/data/prices.py`)、ここへの追記はリスクが低い。
逆に `src/data/prices.py` には絶対に手を入れないこと。

理由: `tools/fetch_long_history.py` は yfinance を叩くので数十秒〜分かかり、
レート制限で落ちることもある。準備シート生成の途中で暗黙にネットワークへ出るのは
「30分で準備を終える」という要件に反する挙動になる。取得は明示的な別ステップにする。

### 4.2.1 TOPIX は `get_benchmark_close` を呼んではいけない ★

`src/data/prices.py :: get_benchmark_close(config)` は中で
**`update_prices([code], config)` を呼んでいる**。つまり `data/prices/` へ書き込み、
ネットワークにも出る。本ツールの「読むだけ・暗黙にネットワークへ出ない」方針に反する。

代わりに **`data/prices_asset/jp_1306.parquet` を直接読む**
(`tools/fetch_long_history.py --assets` が作る長期系列)。
無ければ §4.2 と同じく取得コマンドを案内して終了コード2。

**★2026-08-25 変更: `idx_topix`(^TPX)ではなく `jp_1306` を使う★**
当初は `data/prices_asset/idx_topix.parquet`(^TPX、値段だけの指数)を直読みする
想定だった。7611 での実地確認でこのファイルが1行(2015-10-20のみ)しか無いことが
判明。`tools/fetch_long_history.py` の `ASSET_TICKERS` に「^TPX はYahooに無く
取れないことがある」と既知の欠陥として明記されており、`--force` 再取得でも1行の
まま増えない。同ファイルが代替として案内している `jp_1306`(1306.T、TOPIX連動・
配当込みETF、2001〜)にベンチマークを切り替えた。

**注意: `jp_1306` は配当込み(tr=True)。** シート上の「TOPIX比」は厳密には
「TOPIX連動ETF(配当込み)比」であり、値段だけの純粋なTOPIX指数比とは配当分
(年1〜2%程度)だけズレる。この代用である旨はシート本文(値動きの行)と
「使ったデータの鮮度」節の両方に明記する(§5・§6.1の鮮度明記ルールに従う)。

```
TOPIX の長期系列がありません:
  .venv/bin/python tools/fetch_long_history.py --assets --asset-group idx
```

TOPIX 比 = 個別の同期間リターン − TOPIX の同期間リターン(単純差)。

### 4.2.2 同業比

`data/sector_map.json` の構造は `{"generated_at": ..., "sectors": {code: 業種名}}`。
**トップレベルが code の辞書ではない**ので `d["sectors"]` を見ること。
7134・7611 はどちらも `"小売業"`。

同業の全銘柄ぶん `data/prices_long/*.parquet` を読むと数百ファイルになる。
**`data/prices_long/` に既にあるものだけを使い、無い銘柄は黙って母集団から外す**
(ここで取得しに行かない)。母集団の実際の件数を出力に併記する。

### 4.3 パーセンタイルの出し方

「レンジ内位置」は最小・最大の線形位置ではなく、**過去の全観測に対する順位**で出す。
外れ値1本でレンジが歪むのを避けるため。

```python
pct = (series < current).sum() / len(series) * 100
```

出力には必ず**観測期間**を書く(「5年」と書けない場合があるので固定文言にしない):

```
PER  16.2倍  38%タイル（2024-05〜2026-08 の2.3年・n=560日）
```

---

## 5. ファイル構成と関数シグネチャ

```
src/deepdive/
  __init__.py
  store.py      レコードの読み書きと検証
  jq_raw.py     J-Quants 生取得
  metrics.py    A レイヤの各指標(純関数。副作用なし)
  prep.py       metrics を束ねて A レイヤ dict を組む
  sheet.py      A レイヤ dict → マークダウン
  outcome.py    的中判定と成績集計
  cli.py        argparse。python -m src.deepdive のエントリ
tests/
  test_deepdive_store.py
  test_deepdive_metrics.py
  test_deepdive_outcome.py
  test_deepdive_sheet.py
```

### store.py

```python
DEEPDIVE_DIR = REPO_ROOT / "data" / "deepdive"
WATCHLIST_PATH   = DEEPDIVE_DIR / "watchlist.jsonl"
PREDICTIONS_PATH = DEEPDIVE_DIR / "predictions.jsonl"
ACTUALS_PATH     = DEEPDIVE_DIR / "actuals.jsonl"
OUTCOMES_PATH    = DEEPDIVE_DIR / "outcomes.jsonl"
NOTES_PATH       = DEEPDIVE_DIR / "notes.jsonl"
VERSIONS_PATH    = DEEPDIVE_DIR / "model_versions.jsonl"

def now_iso() -> str: ...
    """JST の現在時刻。テストからは monkeypatch で差し替える。
    引数を取らないこと(呼び出し側から偽装できる余地を作らない)。"""

def load_first_wins(path, key_fields: tuple[str, ...]) -> list[dict]: ...
def load_last_wins(path, key_fields: tuple[str, ...]) -> list[dict]: ...
    """history_store.load_deduped の薄いラッパ。名前で意図を明示するため別名にする。"""

def add_watch(rec: dict) -> None: ...
    """drivers / break_conditions が空なら ValueError。"""

def add_prediction(rec: dict) -> None: ...
    """(ticker, quarter, model_ver) が既存なら ValueError(R2)。
    written_at は now_iso() で必ず上書き。valid は earnings_date と比較して自動判定。"""

def add_actual(rec: dict) -> None: ...
def add_version(rec: dict) -> None: ...
    """ver 重複なら ValueError(R3)。"""
```

### metrics.py(純関数。I/O しない)

```python
def progress_rate(ytd: float | None, plan: float | None) -> float | None
def progress_vs_history(cur: float, history: list[float]) -> dict
    """{"diff_pt": ..., "n": len(history)}"""
def guidance_gap(pairs: list[tuple[float, float]]) -> dict
    """[(期初予想, 着地)] → {"values": [...], "median": ... or None, "n": ...}
    n < 3 なら median は None(中央値を名乗れる件数ではない)。"""
def percentile_in_series(series: pd.Series, current: float) -> dict
    """{"pct": ..., "n": ..., "start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}"""
def return_pct(close: pd.Series, days: int) -> float | None
def relative_return(stock: pd.Series, bench: pd.Series, days: int) -> float | None
def volume_ratio(volume: pd.Series, short: int, long: int) -> float | None
def next_earnings_date(code, calendar, raw_records, manual) -> tuple[str | None, str]
    """(日付, 出典) を返す。出典は "カレンダー" / "前年同期からの推定" / "手入力" / "不明"。
    出典を返り値に含めるのは、推定日を確定日と誤読させないため。"""
```

### sheet.py

要件 6.1 のマークダウンをそのまま出す。加えて末尾に必ず以下を付ける:

```markdown
## この期に出せなかったもの
- PBR / EV/EBITDA: 純資産・有利子負債を取得していないため(恒久)
- PER の5年レンジ: J-Quants Free が2年しか返さないため 2.3年レンジで代用
- 月次: 手入力待ち

## 使ったデータの鮮度
株価: data/prices_long/7134.parquet 2026-08-22 まで
財務: data/deepdive/raw/7134.jsonl 最終開示 2026-05-14（J-Quants Free は12週遅延）
```

**鮮度の明記は必須。** 12週遅延のデータを最新だと思って予想を書くのが一番危ない。

---

## 6. CLI 仕様

```
python -m src.deepdive <subcommand>
```

| コマンド | 動作 |
|---|---|
| `watch add <code> --name ... --fy-end 3 --drivers ... --break ...` | 銘柄登録。drivers/break 必須 |
| `watch list` | 一覧 |
| `fetch <code> [--all]` | J-Quants `?code=` で生取得 → `raw/{code}.jsonl` |
| `prep <code> [--quarter 2026Q2]` | A レイヤ生成 → `prep/{code}_{q}.md` に書き、標準出力にも出す |
| `predict <code> --quarter 2026Q2 --ver v1 --company-op N --my-op N --confidence 中 --action 買う --rationale "..."` | 予想を追記。R1/R2 を強制 |
| `actual <code> --quarter 2026Q2 --from-file actual.json` | 実績を追記 → 的中判定を outcomes へ |
| `note <code> --quarter 2026Q2 --text "..."` | C レイヤ追記 |
| `ver add --ver v2 --change "..." --reason "..."` | 変更ログ |
| `score [--by ver\|ticker]` | 成績サマリ(要件 6.2 の表) |
| `calendar` | 登録銘柄の発表予定日一覧 + 出典 |

`predict` は引数が多いので `--from-file pred.json` も受ける。ただし
`written_at` はファイルに何が書いてあっても**必ず実行時刻で上書きする**。

`score` は `valid: false` の予想を除外したうえで、除外件数を表の下に出す:

```
v1   4   1/4   -12%   25%
v2   4   3/4    +4%   75%
（発表日以降に記入され除外: 1件）
```

除外件数を隠さないこと。無効票が増えているのは規律が緩んでいる兆候で、
それ自体が読むべき数字。

---

## 7. 実装前に実地確認すべきこと(API キーが要る作業)

**サンドボックスからは J-Quants に到達できない。萩山の手元で実行すること。**
既存の `tools/event/fetch_jq.py --probe` と同じ位置づけの作業。

### 7.1 営業利益のフィールド名

```bash
JQUANTS_API_KEY=xxx .venv/bin/python - <<'PY'
import os, json, requests
r = requests.get("https://api.jquants.com/v2/fins/summary",
                 params={"code": "7611"},
                 headers={"x-api-key": os.environ["JQUANTS_API_KEY"]}, timeout=30)
recs = r.json().get("data") or []   # ← キーは "data"。fetch_summaries と同じ
print("件数:", len(recs))
if recs:
    print(json.dumps(sorted(recs[0].keys()), ensure_ascii=False, indent=1))
    print(json.dumps(recs[0], ensure_ascii=False, indent=1)[:3000])
PY
```

確認したいこと(既存の命名から推測はできるが、**推測で実装しない**):

1. 営業利益の実績のキー名(既存の `Sales` / `NP` / `EPS` と同じ命名系のはず)
2. 非連結フォールバックのキー名(既存は `NC` 接頭辞)
3. 通期の営業利益**予想**のキー名(既存は `F` 接頭辞 + 翌期が `NxF` 接頭辞)
4. 経常利益のキー名
5. **返ってきた最古のレコードの `DiscDate`**(= 実際に何年ぶん取れるか。§1.3 の裏取り)
6. `?code=` で 7134 が返るか(ユニバース外銘柄が取れるかの確認。§1.2)

判明したら、本書の §7 に確定したキー名を追記してから実装に入ること。

### 7.1.1 確認結果(2026-08-25 実施・確定)

萩山の手元で 7611・7134 の2銘柄について実行し、確認できた。**推測は不要、以下を実装に使う。**

| 項目 | キー | 備考 |
|---|---|---|
| 営業利益(実績) | `OP` | 既存の `Sales`/`NP`/`EPS` と同じ命名系。値は文字列(`"190000000"`)なので `int()` キャストが要る |
| 経常利益(実績) | `OdP` | |
| 非連結フォールバック | `NC` 接頭辞 | `NCOP` / `NCSales` / `NCNP` / `NCEPS` / `NCOdP` 等。今回の2銘柄はどちらも連結決算のため全部空文字だった(非連結企業で改めて確認は要るが、命名規則自体は確定) |
| 通期予想・営業利益(今期) | `FOP`(2Q時点予想は `FOP2Q`) | |
| 通期予想・営業利益(翌期) | `NxFOP`(2Q時点予想は `NxFOP2Q`) | |
| 通期予想・経常利益 | `FOdP` / `NxFOdP` | 今期/翌期・2Q時点も `OP` と同じ接尾辞規則 |

`?code=` でユニバース外銘柄が取れるか(§1.2): **○。** 7134(売買代金フィルタでユニバース外)も
`status 200`, 8件返ってきた。§1.2 の前提どおり、ユニバースかどうかと J-Quants API が
返すかどうかは無関係。

実際に取れる期間(§1.3 の裏取り): 7611 は `2024-07-05〜2026-04-10`(n=12)、
7134 は `2024-08-13〜2026-05-08`(n=8)。どちらも概ね2年ぶんで、
198 で確認した「Free プランは2年しか返さない」と一致する。

### 7.2 レスポンスの読み方(確認済み・実装時はこれに従う)

`src/data/jquants.py :: fetch_summaries` を読んで確認済み。推測不要:

- 一覧のキーは **`body["data"]`**
- 次ページは `body["pagination_key"]` を同じ params に載せて再送。無ければ終了
- 429 が返ったら 30秒待って**1回だけ**再送
- ヘッダは `{"x-api-key": key}`、`timeout=60`

`jq_raw.py` はこの取得部分を `fetch_summaries` から**コピーして持つ**(import しない)。
import すると本番モジュールの変更が深掘りツールに波及し、逆に深掘り都合の変更を
本番に入れたくなる。数十行の重複より疎結合を取る。

---

## 8. config.yaml 追加

`margin:` セクションの後ろに足す。既存セクションは触らない。

```yaml
# 深掘り銘柄分析ツール (src/deepdive/)。日次パイプラインからは呼ばれない。
deepdive:
  enabled: true
  tickers: ["7134", "7611"]       # watchlist.jsonl が正。ここは fetch の既定対象
  valuation_lookback_years: 5     # 取れなければ取れるだけ使い、実期間を出力に明記する
  progress_history_min_n: 3       # これ未満なら中央値を出さず全値を並べる
  return_windows: [21, 63]        # 1M / 3M (営業日)
  volume_windows: [[5, 20], [5, 60]]
  sheet_dir: "data/deepdive/prep"
```

---

## 9. テスト

`tests/test_deepdive_*.py`。**ネットワークに出るテストは書かない。**
生 API レスポンスは fixture の JSON を tests/fixtures/ に置く。

必須ケース:

| テスト | 何を守るか |
|---|---|
| 同一 (ticker, quarter, ver) の2回目の `add_prediction` が ValueError | R2 |
| JSONL に同一キーが2行ある状態で `load_first_wins` が1行目を返す | R2(直接編集された場合) |
| `written_at` >= `earnings_date` なら `valid: false` で保存される(例外にしない) | R1 |
| `score` が `valid: false` を除外し、除外件数を返す | R1 |
| 既存 ver への `add_version` が ValueError | R3 |
| `delete` サブコマンドが存在しないこと(parser の choices を検査) | R4 |
| `drivers` が空の `add_watch` が ValueError | UC-1 |
| `guidance_gap` が n<3 で median=None を返す | §1.3 |
| `percentile_in_series` が start/end/n を返す | §4.3 |
| `timing` 別に `ret_next_day` の起点が変わる | §3.5 |
| `next_earnings_date` が出典文字列を返す | §1.4 |

### pytest がデータを汚さない配慮 ★

このリポジトリには既知の事故がある(memory: 「pytestがdata/を汚す」)。
テスト実行で `data/history/*.jsonl` に偽の行が追記された。

**同じことを繰り返さないこと。** `store.py` のパス定数は
**モジュール定数を直接使わず、`_path()` 関数経由で解決する**か、
テスト側で `monkeypatch.setattr(store, "PREDICTIONS_PATH", tmp_path / "p.jsonl")` を
全テストの autouse フィクスチャで必ず差し替える。後者を採る場合は
`tests/conftest.py` に autouse フィクスチャを1つ置き、**個々のテストの記述に頼らない**。

---

## 10. 実装手順(推奨順)

1. `src/deepdive/store.py` + `tests/test_deepdive_store.py`。
   規律ルール R1〜R4 が先。ここが緩いと後で作ったものが全部無意味になる。
2. `cli.py` の `watch add/list`。手で7134・7611を登録して JSONL の形を目視確認。
3. **§7 の実地確認**(ユーザーに依頼)。営業利益のキー名が確定するまで先へ進まない。
4. `jq_raw.py` + `fetch`。`raw/{code}.jsonl` に落ちることを確認。
5. `metrics.py`(純関数) + テスト。ここは fixture だけで完結するので速い。
6. `prep.py` / `sheet.py` + `prep` コマンド。7611 で1枚出して目視。
7. `predict` コマンド。R1/R2 の拒否が効くか手で確認。
8. `actual` / `outcome.py` / `score`。
9. `calendar`。
10. `tools/fetch_long_history.py` に `--only` を追加(§4.2)。
11. `.gitignore` に `data/deepdive/raw/` を1行。`config.yaml` に `deepdive:` を追加。
12. `HANDOFF.md` に §15 として1節追加(§11)。

Phase 1 は 1〜7。8〜9 が Phase 2。

---

## 11. HANDOFF.md への追記(実装完了後)

`## 15. 深掘り銘柄分析ツール (src/deepdive/)` を新設し、以下を書く:

- 日次パイプラインから独立していること(呼ばれない・呼ばない)
- `load_first_wins` が `history_store.load_deduped` と**真逆**であること、その理由
- 出せない指標の一覧と理由(§4.1 の表)。将来「なぜ PBR が無いのか」を再調査させないため
- 2年制限が J-Quants の契約由来でコードでは直らないこと
- `data/deepdive/` は git 管理下、`data/deepdive/raw/` だけ ignore

`§2 リポジトリ構成` のツリーにも `src/deepdive/` と `data/deepdive/` を足す。

---

## 12. 割り切ること(設計時に受け入れる)

- **n は永遠に足りない。** 年4回×2〜5銘柄。統計的な結論は出ない。本ツールが返すのは
  「当たった / 外れた」の記録であって「有意に当たる」ではない。`score` の出力に
  信頼区間や有意差の表現を**入れない**。入れると、無い精度を有ると誤読する。
  スクリーナー側の判定基準(HANDOFF §10.1 の4条件)を本ツールに持ち込まないこと。
- **営業利益の四半期値は YTD 差分で作る。** 通期は総額なので、Q4 単独 = 通期 − Q3累計。
  訂正短信があると過去が動く。既存 `derive_quarters` と同じ割り切り。
- **同業比の母集団は33業種の粗い括り。** リユースと外食が同じ括りに入るわけではないが、
  真の競合を手で指定させるほどの精度は本ツールに要らない。
- **月次は手入力。** 自動パースは要件の非対象。銘柄が2〜5個なら手で足りる。
