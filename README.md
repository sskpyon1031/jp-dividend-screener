# 日本株 高配当スクリーナー

時価総額の大きい日本株(初期設定は TOPIX500 = Core30 + Large70 + Mid400)から、
**予想配当利回りが 4% を超える銘柄**を毎営業日に自動抽出し、スマホ最適化した
静的サイトで表示します。GitHub Pages + GitHub Actions のみで動き、サーバー費用はかかりません。

- スクリーニング条件は [`config.json`](config.json) で変更可能
- 結果は `docs/data/latest.json` に出力され、GitHub Pages で配信されます
- ホーム画面に追加すればアプリのように使えます(PWA 対応)
- 各銘柄に **配当性向**(1株配当 ÷ 実績EPS)を併記。予想配当は「(予想)」/ 実績配当は
  「(実績)」と明示し、サイト側で「予想配当のみ」「配当性向◯%以下」に絞り込み可
  (高利回りが減配・株価下落の織り込みでないかの目安)
- 各銘柄に **25日/75日移動平均のテクニカル情報**を併記:
  25日線の向き・上向きに転じてからの日数(初動判定)・株価の乖離率・
  ゴールデンクロス(25日線が75日線を上抜け)成立からの日数 / 接近中フラグ。
  サイト側で「初動のみ」「GCが新しい」等に絞り込み可
- カードに **25日線・75日線のミニチャート**(交差点を ● で表示)と、
  GC状態を「ゴールデンクロス N営業日前 / あと X% で接近中 / クロス前」と
  ことばで示す行を表示
- **買いタイミングの目安**も併記(すべて日足ベース・打診買いの目安):
  - **押し目シグナル** — 中期(75日線 or 25>75)は上昇継続なのに、短期は調整中
    (RSI&lt;40 / 25日線割れ / ボリンジャー下限)という「押し目」状態。
    前日は非点灯で当日点灯したものに「本日新規」バッジ
  - **RSI(14)** — 30以下=売られすぎ / 70以上=過熱
  - **52週レンジ内の位置**(0%=安値・100%=高値)と **52週高値からの下落率**
  - サイト側で「押し目シグナルのみ」「本日新規」「RSI売られすぎ」「52週安値圏」で
    絞り込み、「押し目優先」「レンジ下位順」「RSIが低い順」等で並び替え可

> ⚠️ 本ツールは公開情報を機械的に集計・表示するだけのものです。特定銘柄の売買推奨や
> 投資助言ではありません。データ元(Yahoo Finance / yfinance)の仕様変更・遅延・欠損・
> 誤りにより数値が実際と異なることがあります。投資判断はご自身の責任で。

---

## 仕組み

```
JPX 上場銘柄一覧(data_j.xls)
        │  scripts/fetch.py が母集団を生成(TOPIX500 など)
        ▼
yfinance で 株価 / 時価総額 / 予想配当利回り / 実績EPS を取得
        │  config.json の条件で絞り込み・時価総額の大きい順にソート
        │  該当銘柄の日足1年ぶんをまとめて取得し、25日/75日移動平均・GC・
        │  RSI(14)・52週レンジ・押し目シグナルを算出。前日スナップショットと
        │  比べて押し目シグナルの「本日新規」も判定
        ▼
docs/data/latest.json  ＋  docs/data/history/YYYY-MM-DD.json
        │  GitHub Actions が毎営業日 12:15 / 17:00 JST に実行し commit
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

サイト側でも利回り・時価総額・業種・並び順、配当の基準(予想/実績)・配当性向の上限、
テクニカルで絞り込めます(条件は端末に保存):
「押し目シグナル点灯」「押し目シグナル(高値圏を除く=52週位置<75%)」
「押し目シグナル(本日新規)」「RSIが売られすぎ(30以下)」
「52週安値圏(レンジ下位25%)」「25日線が上向き」「上向きに転じて10日以内(初動)」
「株価が25日線より上」「ゴールデンクロスが新しい(20日以内)」「ゴールデンクロス間近」。
並び替えには「押し目シグナル優先」「52週レンジ下位順」「RSIが低い順」
「高値からの下落率が大きい順」も追加。

サマリー直下の「かんたん設定」ボタン:
- **押し目の買い候補** … 「押し目シグナル(高値圏を除く)」＋「52週レンジ下位順」＋
  配当性向100%超を除外。業種・検索・配当の基準は解除し、利回り下限と時価総額は維持
- **本日の新規点灯** … さらに当日新規点灯のみ
- **条件をリセット** … 絞り込み・並び替えを既定へ

移動平均・タイミング系のパラメータは `fetch.py` の定数で調整できます:
`MA_SHORT`(短期=25)/ `MA_LONG`(中期=75)/ `MA_SLOPE_LOOKBACK`(傾きを測る営業日)/
`TREND_SCAN_MAX`(「〜日目」の上限)/ `GC_NEAR_PCT`(GC間近とみなす乖離)/
`RSI_PERIOD`(RSI の期間=14)/ `BB_MULT`(ボリンジャー下限の σ 倍率=2)/
`PULLBACK_RSI_MAX`(「短期は調整中」とみなす RSI 上限=40)/
`RANGE_WINDOW`(52週レンジを測る営業日数=252)。
日足は1年ぶんをまとめて1回ダウンロードするため、`workers` の影響は受けません。

### 各銘柄に付くテクニカル項目(`latest.json`)

| キー | 意味 |
|---|---|
| `ma25` / `ma75` | 25日 / 75日移動平均の現在値 |
| `ma25_slope_pct` | 直近5営業日での25日線の傾き(%) |
| `ma25_rising` / `ma25_rising_days` | 25日線が上向きか / 連続して上向きの営業日数(初動なら小さい) |
| `price_vs_ma25_pct` / `above_ma25` | 株価が25日線から何%上(+)/下(-)か / 上にあるか |
| `ma25_above_ma75` | 25日線 > 75日線(ゴールデンクロス状態) |
| `days_since_golden_cross` | 確定GCから何営業日か(1〜2日のヒゲ逆クロスは無視。未クロス/1年以上前は `null`) |
| `gc_gap_pct` / `gc_approaching` | 25日線と75日線の差(%) / 未クロスだが上向き・縮小中・3%以内 |
| `ma75_rising` | 75日線(中期トレンド)が上向きか |
| `ma25_hist` / `ma75_hist` | ミニチャート用。25日線 / 75日線の直近45営業日の推移 |
| `rsi14` | RSI(14, Wilder 平滑)。30以下=売られすぎ / 70以上=過熱 |
| `range_52w_low` / `range_52w_high` | 直近252営業日の安値 / 高値 |
| `range_pos_pct` | 52週レンジ内での株価の位置(0=安値, 100=高値) |
| `drawdown_from_high_pct` | 52週高値からの下落率(`<= 0`) |
| `bb_lower` / `below_bb_lower` | 25日 − 2σ の下限 / 株価がそれ以下か |
| `pullback_signal` | 中期は上昇継続 & 短期は調整中(押し目の目安) |
| `pullback_new` | 前営業日は非点灯で当日 `pullback_signal` が点灯したか |

## 自動更新のタイミング

`.github/workflows/update.yml` の cron は平日 **1日2回**:
`15 3 * * 1-5`(UTC)= **12:15 JST**(前場終了後)と
`0 8 * * 1-5`(UTC)= **17:00 JST**(大引け後)。
変更する場合は UTC で指定してください(JST = UTC + 9)。祝日判定はしていないため、
休場日は前営業日と同じ値が出ます。

`docs/data/history/YYYY-MM-DD.json` は**1日1ファイル**で、昼の実行を夕方の実行が
上書きします(その日の最終値が残る)。`latest.json` は毎回いちばん新しい値に更新されます。

> スケジュール実行(GitHub の定期 cron)は 5〜15 分ほど遅れて起動するのが通常で、
> 混雑時はスキップされることもあります。また昼の実行時点では当日の日足が未確定なため、
> RSI・移動平均などの指標は夕方とほぼ同じで、主に株価・利回り等の「現在値」が新しくなります。

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
- **配当性向(`payout_ratio`)の見方**
  `1株配当 ÷ 実績EPS(trailingEps)` の % 。基準が「予想」なら *予想配当が直近利益で
  どれだけ賄えているか*、「実績」なら教科書どおりの配当性向です。赤字(EPS ≤ 0)・
  EPS が取れない銘柄・桁違い(> 400%)は空欄になります。100% 超は「利益を超える配当」で
  減配余地のサインとして注意。yfinance に値が無ければ `payoutRatio` で代替します。
- **ROE(`roe`)** — yfinance の `returnOnEquity`(比率)を % 表示。自己資本利益率で
  「稼ぐ力」の目安。カードのメトリクス欄に表示します。自己資本が極小/マイナスの銘柄で
  桁違いになるため、`-100% 〜 +300%` の範囲外は空欄にします。

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
