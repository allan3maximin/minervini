# 設計書: EDINET DB (決算短信) によるファンダ補完 — src/data/edinetdb.py

作成: 2026-07-07 (Fable) / 実装担当: Sonnet (別セッション)
背景: log.md 2026-07-07 エントリ参照。IRBANKスクレイピングは規約違反でクローズ済み。
統合方針: **案A (補完バックアップ)** / 料金プラン: **Free (100req/日)** — ユーザー確認済み。

---

## 0. 目的と役割分担

J-Quants Freeプランはデータが12週間(84日)遅延する。EDINET DB の `get_earnings`
(決算短信ベース、開示後ほぼ即日〜翌日) でこの遅延窓の直近四半期のみを補完する。

| ソース | 役割 | 優先度 |
|---|---|---|
| manual/fundamentals.csv | 人間の確定値 | 最強 |
| J-Quants (`data/fundamentals_auto.json`) | メイン。全履歴 (12週遅延) | 中 |
| EDINET DB (`data/edinetdb_auto.json`) | 遅延窓の直近四半期のみ補完 | 最弱 |

同一 (code, fiscal_quarter) は manual > jquants > edinetdb。J-Quants の遅延が追いつけば
同ラベルを J-Quants 値が自然に上書きする(=EDINET DB値は暫定速報の扱い)。

## 1. EDINET DB API 仕様 (2026-07-07 検証済み・ドキュメントベース)

- **ベースURL**: `https://edinetdb.jp/v1`
- **認証**: ヘッダ `X-API-Key: <key>`。キーは https://edinetdb.jp/developers で無料発行。
  環境変数名は **`EDINETDB_API_KEY`** とする (J-Quantsの `JQUANTS_API_KEY` と対称)。
- **レートリミット**: Free = **アカウント単位 100リクエスト/日** (1 req = 1 unit、全エンドポイント共通)。
  リクエスト間隔の制約なし ("No sleep/wait between requests is required")。
  レスポンスヘッダ `X-RateLimit-Remaining` あり。`GET /v1/usage` で残数確認可。
- **主要エンドポイント**:
  - `GET /v1/companies?per_page=5000` — 全上場 ~4,631社の基本情報を**1リクエスト**で取得。
    証券コード→EDINETコード(E02144形式)のマッピングテーブル構築に使う。
  - `GET /v1/events?event_type=earnings_summary&since=YYYY-MM-DD&until=YYYY-MM-DD&limit=1000&offset=N`
    — 決算短信の開示イベントフィード (J-Quants `?date=` 相当)。カバレッジ2025-04-28以降、15分毎更新。
  - `GET /v1/companies/{edinet_code}/earnings?limit=8` — 決算短信データ。**latest only**
    (limit はdefault 8 / max 30。日付レンジ指定不可)。`include_nulls=true` で固定スキーマ。
  - `GET /v1/search?q=7203` — 認証不要。証券コードから単発でEDINETコード解決 (補助用)。
- **get_earnings 主要フィールド** (snake_case): `disclosure_date`, `quarter` (Q1/Q2/Q3/FY),
  `eps` (円), `revenue` (**百万円**), `accounting_standard`, `is_correction`, `*_change` (前年同期比%)。
  デフォルトで null フィールドは**省略**される → 必ず `.get()` で読むか `include_nulls=true`。
- **データ範囲**: 決算短信データは**2026年1月以降に公表された短信のみ** (バックフィル不可)。
- **値の意味**: 決算短信の数値そのまま = **YTD累計** (トヨタQ3 revenue 38,087,604百万円 ≒ 9ヶ月累計38.1兆円で確認済み)。
  → J-Quants と同じく **YTD差分導出が必要**。

### 実装前にSonnetがAPIキーで実地確認すべき点 (設計の前提検証)

**2026-07-07 追記 (ユーザーの実レスポンスで部分確認済み)**: `get_financials` (annual, E03622) の
実レスポンスを確認。判明事項:
- `fiscal_year` フィールドが存在する (財務データ側)。earnings 側にも同種フィールドがある可能性が高い。
- **`get_financials` の金額は円単位** (revenue: 155147000000 = 1551億円)。ドキュメント上
  `get_earnings` は百万円とされており、**エンドポイントによって単位が異なる**。3.3 の ×1_000_000
  換算は earnings 専用。実地確認3 (earnings側の単位) は依然必須。
- null フィールド省略の挙動を確認 (年度によりフィールド構成が変わる) → `.get()` 読みが必須。
- meta の `latest_shares_snapshot` に直近短信の開示日・四半期番号 (`quarter: 4` 等) と
  `fiscal_year_end` が含まれる → fy_start 推定 (3.3(b)) の補助情報に使える。

キー未登録のためドキュメントベース。実装の最初に以下を curl で確認し、差異があれば本設計を修正すること:

1. `/v1/companies` レスポンスの証券コードフィールド名と形式 (**4桁か5桁か**。J-Quants は5桁を4桁に正規化している —
   `jquants.py record_to_point` の `code5[:4]` 参照。同じ正規化が必要)。
2. `/earnings?include_nulls=true` のフルスキーマ。特に**会計年度を特定できるフィールド**
   (`fiscal_year` / `period_start` / `fiscal_year_start` 等) の有無。→ 3.3節のラベル決定に直結。
3. `revenue` の単位整合: 既存 `data/fundamentals_auto.json` の revenue (J-Quants `Sales`) は**円単位**のはず。
   トヨタ等1銘柄で桁を突き合わせ、EDINET DB(百万円)→円への `× 1_000_000` 換算が正しいか確認。
4. `/v1/events` のレスポンス形式 (銘柄コードのフィールド名、`event_type` の実値、ページング挙動)。
5. 429/枠超過時のレスポンス (Retry-After ヘッダ有無)。

## 2. ファイル構成

```
src/data/edinetdb.py          新規モジュール (jquants.py と同スタイル・同規約)
data/edinetdb_auto.json       四半期化済みストア (fundamentals_auto.json と同スキーマ, source: "edinetdb")
data/edinetdb_state.json      {"last_events_date", "codemap", "codemap_date", "backlog"}
tests/test_edinetdb.py        新規テスト
```

ストアスキーマ (既存 `load_auto_store` 互換):
```json
{"7203": {"quarters": [{"fiscal_quarter": "2025Q3", "eps": 232.55, "revenue": 12345678000000}],
          "checked_date": "2026-02-06", "source": "edinetdb"}}
```

state スキーマ:
```json
{"last_events_date": "2026-07-04",
 "codemap": {"7203": "E02144", "...": "..."},
 "codemap_date": "2026-07-01",
 "backlog": ["7203", "6758"]}
```
(codemap を state に同居させるのはファイル数削減のため。~1000銘柄分のみ保持で数十KB。)

## 3. src/data/edinetdb.py の設計

### 3.1 モジュール定数・共通関数 (jquants.py に倣う)

```python
STATE_PATH = REPO_ROOT / "data" / "edinetdb_state.json"
STORE_PATH = REPO_ROOT / "data" / "edinetdb_auto.json"
API_KEY_ENV = "EDINETDB_API_KEY"
DEFAULT_API_URL = "https://edinetdb.jp/v1"
EARLIEST_DATA_DATE = "2026-01-01"   # 決算短信データの提供開始。初回runのevents開始日
_QUARTER_TO_N = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}

def _ed_cfg(config: dict) -> dict            # config["edinetdb"]
def load_state(path=None) -> dict            # jquants.load_state と同型
def save_state(state, path=None) -> None
def load_store(path=None) -> dict            # fundamentals.load_auto_store(STORE_PATH) を呼ぶだけで可
def save_store(store, path=None) -> None     # jquants.save_auto_store と同型 (indent=1, sort_keys=True)
```

### 3.2 API アクセス

```python
def _get(api_key: str, config: dict, path: str, params: dict | None = None) -> dict:
    """GET {api_url}{path}。X-API-Keyヘッダ。429は30秒待って1回だけ再試行
    (jquants.fetch_summaries と同じ粘り方)。resp.raise_for_status() 後 json を返す。"""

def fetch_companies_map(api_key, config) -> dict[str, str]:
    """GET /companies?per_page=5000 (1リクエスト) → {証券コード4桁: edinet_code}。
    証券コードは4桁に正規化 (5桁なら末尾を落とす — 要実地確認1)。
    ユニバース外も含め全社分を返し、呼び出し側でfilterする。"""

def fetch_events(api_key, config, since: date, until: date) -> list[dict]:
    """GET /events?event_type=earnings_summary&since&until。
    limit=1000 + offset でページング (len(batch) == limit の間続行)。"""

def fetch_earnings(api_key, config, edinet_code: str) -> list[dict]:
    """GET /companies/{edinet_code}/earnings?limit={cfg earnings_limit}&include_nulls=true"""
```

### 3.3 レコード → YTD点変換

```python
def record_to_point(rec: dict, code: str, fiscal_year_end_month: int | None = None) -> dict | None:
    """earnings 1レコードを jquants.record_to_point と同じ点形式に変換:
    {"code", "fy_start", "n", "label", "eps", "revenue", "disc_date"}

    - n = _QUARTER_TO_N[rec["quarter"]]。不明値は None を返す。
    - eps はそのまま (円)。revenue は None でなければ × 1_000_000 (百万円→円。要実地確認3)。
    - eps も revenue も None なら None を返す (jquants と同じ)。
    - is_correction は特別扱い不要 (derive_quarters が開示日の新しい方を採用する)。
    - label = f"{fy_start[:4]}Q{n}" (J-Quants と同一規則)。
    """
```

**fy_start の決定** (要実地確認2の結果で分岐):
- (a) レスポンスに会計年度フィールドがあれば、それを使う (最優先)。
- (b) 無ければ `/companies` の決算月 (fiscal_year_end 等) から推定する:
  四半期 n の期末月 = fy_start月 + 3n − 1 (mod 12)。disclosure_date は期末の30〜45日後なので、
  `期末候補月 ≒ disclosure_date の1〜2ヶ月前` を満たす直近の fy_start を逆算する。
  3月決算 (fy_start=4月) で Q3 開示が2026-02 → 期末2025-12 → fy_start=2025-04 → label "2025Q3"。
  推定が一意に決まらないレコードは捨てる (安全側)。

### 3.4 YTD差分の四半期化 — J-Quantsストアを基準に使う

EDINET DB Free は2026年1月以降の開示しか持たないため、年度前半のYTD点が同ソース内に無い
ケースが常態 (例: 3月決算のQ3を取ったがQ2短信は2025年11月開示で範囲外)。
J-Quants にはその前半データが (遅延後) 存在するので、**差分基準を J-Quants 側の確定四半期から再構成する**:

```python
def derive_with_base(point: dict, base_quarters: list[dict]) -> dict | None:
    """point: 3.3のYTD点。base_quarters: 同一銘柄の確定四半期リスト
    (J-Quantsストア + 既存edinetdbストアをlabelでマージしたもの。値は単四半期値)。

    n == 1: 値 = YTDそのまま。
    n >= 2: 同一年度の Q1..Q(n-1) が base_quarters に全て揃っている場合のみ、
            prior_ytd = それらの合計、値 = ytd − prior_ytd。
            1つでも欠けていれば None を返しスキップ (YTD値の誤登録防止 —
            jquants._refetch_incomplete と同じ問題意識。EDINET DBには過去点の
            取り直し手段が無いので「揃うまで待つ」に倒す)。
    eps / revenue は独立に判定 (片方だけ揃っていればその項目だけ埋める)。
    戻り値: {"fiscal_quarter": label, "eps": ..., "revenue": ...} または None。
    round(x, 4) は jquants.derive_quarters に合わせる。
    """
```

同一年度のラベル判定は `fiscal_quarter` 文字列 (`"2025Q1"` 等) の前4桁一致で行う。
※ J-Quants側の revenue が円単位である前提 (要実地確認3)。

### 3.5 メインエントリポイント

```python
def update_fundamentals_auto(codes: list[str], config: dict | None = None,
                             base_store: dict | None = None) -> dict:
    """日次インクリメンタル。pipeline.py から J-Quants ストアを base_store として受け取る。
    APIキー無し or enabled: false → 既存ストアを返すだけ (ネットワーク不使用。jquants と同じ設計)。

    フロー (リクエスト予算 budget = cfg["requests_per_day"] を消費カウンタで管理):
    1. codemap 更新判定: state["codemap"] が空 or codemap_date が
       cfg["codemap_refresh_days"] より古い → fetch_companies_map (1req)。
       ユニバース codes に絞って state["codemap"] に保存。
       マップに無い code は警告printしてスキップ (新規上場等。次回refreshで拾う)。
    2. events 取得: since = state["last_events_date"]+1日 (初回は EARLIEST_DATA_DATE)、
       until = 今日。開示イベントの銘柄コードを4桁正規化し、ユニバースに含まれるものを
       state["backlog"] に追加 (重複はset的に排除)。成功したら last_events_date = 今日。
       events が全滅した場合は last_events_date を進めない (jquants の state 規約と同じ)。
    3. backlog 消化: 残り予算の範囲で backlog の先頭から fetch_earnings (1銘柄1req)。
       レコード → record_to_point → derive_with_base (base = base_store[code].quarters と
       自ストア quarters のマージ) → _merge_into_store 相当でストアに反映。
       成功した銘柄は backlog から除去。失敗 (例外) は backlog に残して次回へ。
    4. save_store / save_state。処理サマリを print (jquants と同じ1行スタイル)。
    """
```

`_merge_into_store` は jquants.py の同名関数とほぼ同一 (source="edinetdb"、
max_keep=cfg["max_quarters_keep"])。**コピーして持つ** (jquants からの import はしない —
両モジュールを疎結合に保つ。共通化リファクタは今回のスコープ外)。

- 初回run: backlog にユニバース内の全開示銘柄 (2026-01以降、数百〜千弱) が積まれ、
  90req/日 ≒ 2〜3週間で自然に追いつく。専用バックフィルCLIは**作らない** (latest-only設計のため不可能)。
- CLI: `python -m src.data.edinetdb` で単独実行可にする (main() は jquants.main に倣う。
  --backfill オプションは無し)。

### 3.6 pipeline.py への組み込み

`src/pipeline.py` の J-Quants ブロック直後 (現在の L111-122 付近):

```python
    # EDINET DB (決算短信) で J-Quants 12週遅延窓の直近四半期を補完。
    # APIキー未設定なら既存ストアを読むだけ。失敗しても本体は止めない。
    try:
        tanshin_by_code = edinetdb_mod.update_fundamentals_auto(codes, config, base_store=auto_by_code)
    except Exception as e:
        print(f"EDINET DB fundamentals update failed (ignored): {e}")
        tanshin_by_code = {}

    fundamentals_by_code = merge_fundamentals(
        auto_by_code, build_fundamentals_by_code(csv_df), tanshin_by_code=tanshin_by_code)
```

import は既存規約どおり `from src.data import edinetdb as edinetdb_mod`。

### 3.7 merge_fundamentals の3ソース拡張 (src/data/fundamentals.py)

後方互換のためキーワード引数で追加する (既存呼び出し・既存テストを壊さない):

```python
def merge_fundamentals(auto_by_code: dict, manual_by_code: dict,
                       tanshin_by_code: dict | None = None) -> dict:
    """優先度: manual > auto(jquants) > tanshin(edinetdb)。
    by_label への投入順を tanshin → auto → manual にするだけ (後勝ち)。
    checked_date: manual があれば manual、無ければ max(auto, tanshin) (文字列比較でISO日付は正しく比較可)。
    monthly_yoy: 従来どおり manual のみ。"""
```

効果: EDINET DB で直近四半期が入った銘柄も quarters が非空になり
`fund_coverage_tier` が "confirmed" を返す → 〔本命〕昇格が最大12週早まる。

## 4. config.yaml 追加 (jquants セクションの直後)

```yaml
# EDINET DB API (決算短信でJ-Quants 12週遅延窓の直近四半期を補完。案A構成)
edinetdb:
  enabled: false            # APIキー登録+実地確認(設計書1節)完了後に true へ
  api_url: "https://edinetdb.jp/v1"
  requests_per_day: 90      # Free 100/日。手動検証・失敗リトライ用に10残す
  earnings_limit: 8         # /earnings の limit (default 8, max 30)
  codemap_refresh_days: 30  # 証券コード→EDINETコード表の再取得間隔
  max_quarters_keep: 12     # jquants と同じ
```

`enabled: false` で入れるのが重要 (キー登録前にマージされても挙動が変わらない)。

## 5. GitHub Actions

- `daily.yml`: env に `EDINETDB_API_KEY: ${{ secrets.EDINETDB_API_KEY }}` を追加
  (JQUANTS_API_KEY の隣)。Secret 登録はユーザーに依頼。
- コミット対象は既存の `git add data/ docs/` で `edinetdb_auto.json` / `edinetdb_state.json` を
  自動的に含むためワークフロー変更は env 追加のみ。
- 新規ワークフローは不要 (バックフィルCLIが無いため)。

## 6. テスト

### tests/test_edinetdb.py (新規)

ネットワークは全て `_get` のモックで遮断。カバーすべきケース:

1. `record_to_point`: 正常変換 / quarter不明値→None / eps・revenue両方None→None /
   revenue の百万円→円換算 / fy_start 推定 (実地確認2の結果に応じて)。
2. `derive_with_base`:
   - Q1 → YTDそのまま
   - Q3 + base に Q1,Q2 あり → 差分
   - Q3 + base に Q2 欠け → None (スキップ)
   - eps のみ揃い revenue 欠け → eps だけ埋まる
3. `update_fundamentals_auto`:
   - APIキー無し → ネットワーク関数が呼ばれず既存ストア返却
   - budget 超過分が backlog に残る / 次回実行で消化される
   - events 全滅 → last_events_date が進まない
   - codemap に無い code のスキップ
4. ストア永続化 round-trip。

### 既存テストの更新

- `tests/test_pipeline.py :: wired` fixture に**必ず**追加 (HANDOFF 12章チェックリスト):
  ```python
  monkeypatch.setattr(pipeline.edinetdb_mod, "update_fundamentals_auto",
                      lambda codes, config, base_store=None: {})
  ```
- `tests/test_fundamentals.py` の merge_fundamentals テスト群 (L151付近、既存2ソース呼び出しは
  位置引数のためキーワード追加で互換維持) に3ソースケースを追加:
  tanshin のみの四半期が入る / 同ラベルで auto が tanshin に勝つ / manual が両方に勝つ /
  checked_date の max 採用。

## 7. HANDOFF.md 更新箇所 (実装完了後)

- 新設「## 5. EDINET DB 決算短信補完 (src/data/edinetdb.py)」— 現4章 (J-Quants) の直後。
  経緯 (IRBANK断念→案A採用)・仕組み・Free枠バックログ設計・実地確認結果を4章と同粒度で記載。
- 以降の章番号を +1 リナンバー (5.config → 6, … 12.チェックリスト → 13)。章番号への内部参照
  (「4章参照」等) も grep して追従すること。
- 2章のリポジトリ構成ツリーに edinetdb.py / edinetdb_auto.json / edinetdb_state.json / test_edinetdb.py を追記。
- 7章(現) Actions 表の daily.yml 行に EDINETDB_API_KEY を追記。
- 9章(現) テスト件数を更新。

## 8. 実装手順 (Sonnet向け・推奨順)

1. ユーザーに EDINETDB_API_KEY の発行を依頼 → 1節の実地確認1〜5を curl で実施、結果を log.md に記録。
   設計と差異があればこのファイルを修正してから着手。
2. config.yaml 追加 (enabled: false) + src/data/edinetdb.py 実装 + tests/test_edinetdb.py。
3. fundamentals.py の merge_fundamentals 拡張 + test_jquants.py のマージテスト追加。
4. pipeline.py 組み込み + wired fixture モック追加。
5. `python -m pytest tests/ -q` 全パス確認 (フロント変更なし → `?v=N` 更新は不要)。
6. ローカルで `EDINETDB_API_KEY=... python -m src.data.edinetdb` を1回実行し、
   edinetdb_auto.json の数銘柄を決算短信PDFと突き合わせ検証 → 問題なければ enabled: true。
7. daily.yml に env 追加、ユーザーに Secret 登録と push を依頼。
8. HANDOFF.md 更新 (7節) + log.md 追記。

## 9. 既知のリスク・割り切り

- **fy_start 推定** (3.3(b)) は変則決算・決算期変更に弱い。推定不能は捨てる設計なので
  誤登録はしないが、取りこぼしはあり得る (許容。J-Quantsが12週後に必ず拾う)。
- 決算集中日 (5月GW明け・8月上旬・11月上旬・2月上旬) は backlog 消化に2〜4日かかる。
  それでも J-Quants 比で11週以上早い (許容)。
- EDINET DB は第三者サービス (Cabocia Inc.)。API仕様変更・サービス終了リスクあり。
  enabled フラグで即座に切れる設計 + J-Quants メインは維持しているので致命傷にならない。
- 短信は未監査の速報値。manual > jquants > edinetdb の優先順でリスクを段階的に吸収。
- 銀行・保険等の一部 IFRS/US GAAP 企業は revenue が null (XBRL開示フォーマット起因)。
  eps だけでも取り込む設計 (3.4) なので J-Quants の既知の同種問題と同等の挙動。
