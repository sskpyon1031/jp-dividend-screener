# 日本株 高配当スクリーナー

時価総額の大きい日本株(初期設定は TOPIX500 = Core30 + Large70 + Mid400)から、
**予想配当利回りが 4% を超える銘柄**を毎営業日に自動抽出し、スマホ最適化した
静的サイトで表示します。GitHub Pages + GitHub Actions のみで動き、サーバー費用はかかりません。

- スクリーニング条件は [`config.json`](config.json) で変更可能
- 結果は `docs/data/latest.json` に出力され、GitHub Pages で配信されます
- ホーム画面に追加すればアプリのように使えます(PWA 対応)
- 各銘柄に **26日移動平均線が上向きか**(直近5営業日での傾き %)を併記。
  サイト側で「26日線が上向きの銘柄のみ」に絞り込み可

> ⚠️ 本ツールは公開情報を機械的に集計・表示するだけのものです。特定銘柄の売買推奨や
> 投資助言ではありません。データ元(Yahoo Finance / yfinance)の仕様変更・遅延・欠損・
> 誤りにより数値が実際と異なることがあります。投資判断はご自身の責任で。

---

## 仕組み

```
JPX 上場銘柄一覧(data_j.xls)
        │  scripts/fetch.py が母集団を生成(TOPIX500 など)
        ▼
yfinance で 株価 / 時価総額 / 予想配当利回り を取得
        │  加えて日足終値をまとめて取得し 26日移動平均の傾きを算出
        │  config.json の条件で絞り込み・時価総額の大きい順にソート
        ▼
docs/data/latest.json  ＋  docs/data/history/YYYY-MM-DD.json
        │  GitHub Actions が毎営業日 17:00 JST に実行し commit
        ▼
GitHub Pages(docs/ を公開)→ スマホで閲覧
```

## セットアップ(GitHub)

1. このフォルダを空の GitHub リポジトリとして push する
   ```bash
   cd jp-dividend-screener
   git init
   git add .
   git commit -m "init: 日本株 高配当スクリーナー"
   git branch -M main
   git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
   git push -u origin main
   ```
2. リポジトリの **Settings → Actions → General → Workflow permissions** を
   **「Read and write permissions」** に変更(Actions が結果を commit するため)
3. **Settings → Pages** で **Source = Deploy from a branch**, **Branch = `main` / `/docs`** を選択
4. **Actions** タブ → **update-screener** → **Run workflow** で初回実行
   (以降は平日 17:00 JST に自動実行)
5. 数分後、`https://<ユーザー名>.github.io/<リポジトリ名>/` をスマホで開く
   → メニューから「ホーム画面に追加」

## ローカルで試す

```bash
pip install -r requirements.txt
python scripts/fetch.py                 # docs/data/latest.json を更新
python -m http.server -d docs 8000      # http://localhost:8000 を開く
```

`file://` で直接開くと `fetch()` がブロックされるため、必ず簡易サーバー経由で開いてください。

## スクリーニング条件の変更 — `config.json`

| キー | 意味 | 例 |
|---|---|---|
| `universe` | 母集団。`"topix500"`(推奨) / `"prime"`(プライム全銘柄・重い) / `"all"` | `"topix500"` |
| `min_dividend_yield` | 予想配当利回りの下限(%) | `4.0` |
| `min_market_cap_yen` | 時価総額の下限(円)。`300000000000` = 3,000億円 | `500000000000` |
| `max_results` | 出力する最大件数(`0` で無制限) | `100` |
| `workers` | 並列取得数。小さいほど Yahoo にブロックされにくいが遅くなる | `4` |
| `min_success_ratio` | 取得成功が母集団のこの割合を下回ったら、既存データを**上書きせず失敗終了**する | `0.5` |

サイト側でも利回り・時価総額・業種・並び順・「26日線が上向きのみ」をその場で絞り込めます(条件は端末に保存)。

26日移動平均は `fetch.py` の `MA_WINDOW`(期間)/ `MA_SLOPE_LOOKBACK`(何営業日前と比較して上向き判定するか)で調整できます。日足はまとめて1回ダウンロードするため、`workers` の影響は受けません。

## 自動更新のタイミング

`.github/workflows/update.yml` の cron は `0 8 * * 1-5`(UTC)= **平日 17:00 JST**。
変更する場合は UTC で指定してください(JST = UTC + 9)。祝日判定はしていないため、
休場日は前営業日と同じ値が出ます。

> **⚠️ 60日ルール**: GitHub は「60日間リポジトリに活動が無い」とスケジュール実行を
> 自動停止します。Actions bot 自身のコミットは活動に**数えられません**。60日近く
> 手を触れないと停止し、警告メールが届きます。対策は次のいずれか:
> - 月1回程度、何でもよいので手動コミット/push する(最も簡単)
> - Settings → Actions で停止後に再有効化する
> - push を `GITHUB_TOKEN` ではなく個人アクセストークン(PAT)で行うようにする(上級)

## うまく動かないとき

- **`fetch.py` が「取得成功 … が下限未満」で失敗する(exit 1)**
  Yahoo Finance が GitHub Actions の IP を一時的にブロックしている可能性があります。
  この場合 `latest.json` は**上書きされず前日のデータが残ります**。時間を置いて
  再実行するか、`config.json` の `workers` を `4` に下げてください。恒久的に不安定な
  場合は、下記「ローカル実行 + push」に切り替えられます。
  一時的に下限を無視したいときは `min_success_ratio` を `0` にします。
- **ローカル実行 + push(フォールバック)**
  Windows のタスクスケジューラで毎日 `python scripts/fetch.py` を実行し、
  `git add docs/data && git commit -m data && git push` する .bat を登録すれば、
  GitHub Actions を使わずに同じサイトを更新できます。
- **フロント(HTML/CSS/JS)を変えても反映されない**
  Service Worker がキャッシュしています。通常は次回アクセスで自動更新されますが、
  即時反映したいときは `docs/sw.js` の `VERSION`(`"v2"` など)を上げてください。
- **配当は「予想」か「実績」か**
  yfinance の予想値(`dividendRate`)があればそれを、無ければ過去12カ月実績を使い、
  カードに「1株配当(予想)」「(実績)」と明示します。証券会社の予想配当とズレることがあります。

## アイコンを差し替えるとき

`docs/icon.svg` を編集し、`python scripts/make_icons.py` で
`icon-180/192/512.png`(iOS ホーム画面・PWA 用)を再生成します(要 `pip install pillow`)。

## ファイル構成

```
jp-dividend-screener/
├── config.json                  スクリーニング条件
├── requirements.txt
├── scripts/
│   ├── fetch.py                 データ取得・絞り込みバッチ
│   └── make_icons.py            icon.svg から PNG を生成
├── .github/workflows/update.yml 毎営業日の自動実行
└── docs/                        GitHub Pages の公開ルート
    ├── index.html / style.css / app.js
    ├── manifest.webmanifest / sw.js
    ├── icon.svg / icon-180.png / icon-192.png / icon-512.png   PWA・iOS 用
    └── data/
        ├── latest.json          最新の抽出結果(初回はサンプル)
        ├── history/YYYY-MM-DD.json  日次スナップショット
        └── history.json         利用可能な日付一覧
```
