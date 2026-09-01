#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日本株「高配当 × 大型株」スクリーナー — データ取得バッチ

処理の流れ:
  1. JPX公表の「東証上場銘柄一覧 (data_j.xls)」から母集団を作る
  2. yfinance で各銘柄の株価・時価総額・予想配当利回り・実績EPSを取得
  3. config.json の条件(利回り下限・時価総額下限)で絞り込む
  4. 該当銘柄の日足からテクニカル(移動平均・GC・RSI・52週レンジ・押し目)を算出。
     前日スナップショットと比べて押し目シグナルの「本日新規」も判定
  5. docs/data/latest.json と docs/data/history/<日付>.json に書き出す

GitHub Actions から毎営業日実行する想定。ローカルでも実行可。
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA_DIR = ROOT / "docs" / "data"
HIST_DIR = DATA_DIR / "history"
CACHE_DIR = ROOT / "_cache"

# JPX「その他統計資料」— 東証上場銘柄一覧
JPX_XLS_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xls"
)
TOPIX500_SCALES = {"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; jp-dividend-screener/1.0)"}

MA_SHORT = 25            # 短期移動平均(日本株の標準)
MA_LONG = 75             # 中期移動平均(ゴールデンクロス判定用)
MA_SLOPE_LOOKBACK = 5    # 傾きを測る営業日数
TREND_SCAN_MAX = 60      # 「〜日目」を数える上限(超えたら None)
GC_NEAR_PCT = 0.03       # 25日線が75日線の 3% 以内まで接近したら「GC間近」候補
GC_CONFIRM_DAYS = 5      # GC成立前に「25日線 < 75日線」が続くべき営業日数(ヒゲ除去)
MA_SPARK_POINTS = 45     # カードのミニチャートに描く直近の移動平均の点数

RSI_PERIOD = 14          # RSI の期間(Wilder 平滑)
BB_MULT = 2.0            # ボリンジャーバンドの σ 倍率(押し目の下限判定に使用)
PULLBACK_RSI_MAX = 40    # 「短期は調整中」とみなす RSI の上限
RANGE_WINDOW = 252       # 52週レンジ(高値・安値)を測る営業日数


def num(x):
    """数値化できなければ None。NaN / inf も None にする。"""
    try:
        if x is None:
            return None
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def load_universe() -> pd.DataFrame:
    """JPXの上場銘柄一覧を取得し、config.json の universe 設定で母集団を絞る。"""
    CACHE_DIR.mkdir(exist_ok=True)
    xls = CACHE_DIR / "data_j.xls"
    fresh = xls.exists() and (time.time() - xls.stat().st_mtime < 86400)
    if not fresh:
        try:
            r = requests.get(JPX_XLS_URL, headers=UA, timeout=60)
            r.raise_for_status()
            xls.write_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            if not xls.exists():
                raise
            print(f"[warn] JPX一覧の取得に失敗。キャッシュを使用します: {e}", file=sys.stderr)

    df = pd.read_excel(xls, dtype={"コード": str})
    df = df.rename(
        columns={
            "コード": "code",
            "銘柄名": "name",
            "市場・商品区分": "market",
            "33業種区分": "sector",
            "規模区分": "scale",
        }
    )
    missing = {"code", "name", "market", "sector", "scale"} - set(df.columns)
    if missing:
        raise SystemExit(
            f"JPX一覧の列名が想定と異なります(不足: {sorted(missing)})。"
            f" 実際の列: {list(df.columns)}"
        )
    # 銘柄コードは 4 桁。数字のみ／英数字混在(例 285A)の両方に対応。
    df = df[df["code"].astype(str).str.fullmatch(r"[0-9A-Z]{4}", na=False)].copy()

    universe = CONFIG.get("universe", "topix500")
    if universe == "topix500":
        df = df[df["scale"].isin(TOPIX500_SCALES)]
    elif universe == "prime":
        df = df[df["market"].str.contains("プライム", na=False)]
    elif universe == "all":
        df = df[df["market"].str.contains("プライム|スタンダード|グロース", na=False)]
    else:
        raise SystemExit(f"config.json の universe が不正です: {universe!r}")

    df["symbol"] = df["code"] + ".T"
    df["sector"] = df["sector"].fillna("その他").replace("-", "その他")
    return df[["code", "name", "sector", "scale", "symbol"]].reset_index(drop=True)


def fi_get(fast_info, *keys):
    """FastInfo から最初に見つかった非 None の値を返す。"""
    for k in keys:
        try:
            v = fast_info[k]
        except Exception:  # noqa: BLE001
            continue
        if v is not None:
            return v
    return None


def _trailing_run(mask) -> int:
    """末尾から連続して True が続く長さ。"""
    n = 0
    for v in reversed(list(mask)):
        if bool(v):
            n += 1
        else:
            break
    return n


def _days_since_cross(above: list[bool], confirm: int, cap: int):
    """末尾が「25日線 > 75日線」(GC状態)のとき、直近の *確定した* 下→上クロスからの
    営業日数を返す。クロス直前に confirm 日以上の「下」が無ければヒゲとみなし、
    その手前のクロスまで遡る。窓内に確定クロスが見つからなければ None(=古い)。"""
    n = len(above)
    if n == 0 or not above[-1]:
        return None
    i = n - 1
    since = 0
    while i >= 0 and above[i]:      # 現在の「上」連続区間
        since += 1
        i -= 1
    while i >= 0:
        gap = 0                     # 直前の「下」連続区間
        while i >= 0 and not above[i]:
            gap += 1
            i -= 1
        if gap >= confirm:
            return since if since <= cap else None
        since += gap                # ヒゲ。手前の「上」区間も足して継続
        while i >= 0 and above[i]:
            since += 1
            i -= 1
    return None                     # 窓の先頭まで確定クロス無し


def load_ma(items: list[dict]) -> dict[str, dict]:
    """表示対象の銘柄について日足を取得し、25日/75日移動平均まわりの指標を計算する。

    引数 items は "symbol" と "price" を持つレコードのリスト(絞り込み後の picked)。
    乖離率・株価との位置は、カード表示と揃えるため items の "price" を使う。

    各銘柄に付ける情報:
      ma25 / ma75                : 移動平均の現在値
      ma25_slope_pct             : 直近 MA_SLOPE_LOOKBACK 営業日での 25日線の傾き(%)
      ma25_rising                : 25日線が上向きか
      ma25_rising_days           : 上向きが連続して何営業日続いているか(初動なら小さい)
      price_vs_ma25_pct          : 株価が25日線から何%上(+)/下(-)か(乖離率)
      above_ma25                 : 株価が25日線より上か
      ma25_above_ma75            : 25日線 > 75日線(ゴールデンクロス状態)か
      ma75_rising                : 75日線(中期トレンド)が上向きか
      days_since_golden_cross    : 確定GCから何営業日か(未クロス/古すぎは None)
      gc_gap_pct                 : (25日線 / 75日線 - 1) * 100
      gc_approaching             : 未クロスだが 25日線が上向き & 差が縮小中 & 3%以内
      rsi14                      : RSI(14, Wilder)。30以下=売られすぎ / 70以上=過熱
      range_52w_low / _high      : 直近 RANGE_WINDOW 営業日の安値 / 高値
      range_pos_pct              : 52週レンジ内での株価の位置(0=安値, 100=高値)
      drawdown_from_high_pct     : 52週高値からの下落率(<=0)
      bb_lower / below_bb_lower  : 25日 ± 2σ の下限 / 株価がそれ以下か
      pullback_signal            : 中期は上昇継続 & 短期は調整中(押し目の目安)

    補助情報なので、取れなくても全体は止めない。
    """
    out: dict[str, dict] = {}
    if not items:
        return out
    symbols = [it["symbol"] for it in items]
    price_by_sym = {it["symbol"]: it.get("price") for it in items}

    try:
        hist = yf.download(
            symbols,
            period="1y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 移動平均データの取得に失敗しました: {e}", file=sys.stderr)
        return out
    if hist is None or hist.empty:
        return out

    multi = isinstance(hist.columns, pd.MultiIndex)
    available = set(hist.columns.get_level_values(0)) if multi else set(symbols)

    for sym in symbols:
        if multi and sym not in available:
            continue
        try:
            close = (hist[sym]["Close"] if multi else hist["Close"]).dropna()
        except Exception:  # noqa: BLE001
            continue
        if len(close) < MA_LONG + MA_SLOPE_LOOKBACK + 1:
            continue

        ma_s_full = close.rolling(MA_SHORT).mean()
        ma_l_full = close.rolling(MA_LONG).mean()
        s = ma_s_full.dropna()
        if len(s) < MA_SLOPE_LOOKBACK + 2:
            continue

        px = price_by_sym.get(sym)
        price = float(px) if px else float(close.iloc[-1])
        cur_s = float(s.iloc[-1])
        past_s = float(s.iloc[-1 - MA_SLOPE_LOOKBACK])
        if cur_s <= 0 or past_s <= 0:
            continue
        slope_pct = (cur_s / past_s - 1) * 100
        rising = slope_pct > 0
        rising_days = min(_trailing_run(s.diff() > 0), TREND_SCAN_MAX)

        rec = {
            "ma25": round(cur_s, 1),
            "ma25_slope_pct": round(slope_pct, 2),
            "ma25_rising": bool(rising),
            "ma25_rising_days": rising_days if rising else 0,
            "price_vs_ma25_pct": round((price / cur_s - 1) * 100, 2),
            "above_ma25": bool(price > cur_s),
        }

        # --- B. RSI(14, Wilder 平滑) ---
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        roll_up = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
        roll_dn = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False, min_periods=RSI_PERIOD).mean()
        up_now, dn_now = num(roll_up.iloc[-1]), num(roll_dn.iloc[-1])
        if up_now is not None and dn_now is not None:
            if dn_now == 0:
                rsi_val = 100.0 if up_now > 0 else None
            else:
                rsi_val = 100.0 - 100.0 / (1.0 + up_now / dn_now)
            if rsi_val is not None:
                rec["rsi14"] = round(rsi_val, 1)

        # --- A. 52週レンジ内の位置 / 高値からの下落率 ---
        # 高安と現在値は同じ「調整済み終値」ベースで比較する。intraday の price は
        # 未調整のため、配当調整ぶんだけレンジが下方にずれて位置・下落率が甘く出る。
        # 直近の終値は必ず窓に含まれるので位置は 0〜100 / 下落率は <=0 に収まる。
        win = close.tail(RANGE_WINDOW)
        if len(win) >= 200:                      # 「52週」を名乗れるだけの営業日数
            lo, hi = num(win.min()), num(win.max())
            last = num(close.iloc[-1])
            if lo is not None and hi is not None and last is not None and hi > lo:
                rec["range_52w_low"] = round(lo, 1)
                rec["range_52w_high"] = round(hi, 1)
                rec["range_pos_pct"] = round((last - lo) / (hi - lo) * 100, 1)
                rec["drawdown_from_high_pct"] = round((last / hi - 1) * 100, 1)

        # --- ボリンジャーバンド下限(25日 - 2σ) ---
        sd = num(close.rolling(MA_SHORT).std().iloc[-1])
        if sd is not None and sd > 0:
            bb_lower = cur_s - BB_MULT * sd
            rec["bb_lower"] = round(bb_lower, 1)
            rec["below_bb_lower"] = bool(price <= bb_lower)

        both = ma_s_full.notna() & ma_l_full.notna()
        if both.any():
            s_v = ma_s_full[both].to_numpy()
            l_v = ma_l_full[both].to_numpy()
            cur_l = float(l_v[-1])
            if cur_l > 0:
                gap_now = cur_s / cur_l - 1
                above_now = bool(s_v[-1] > l_v[-1])
                rec["ma75"] = round(cur_l, 1)
                rec["ma25_above_ma75"] = above_now
                rec["gc_gap_pct"] = round(gap_now * 100, 2)
                if len(l_v) > MA_SLOPE_LOOKBACK:
                    past_l = float(l_v[-1 - MA_SLOPE_LOOKBACK])
                    if past_l > 0:
                        rec["ma75_rising"] = bool(cur_l / past_l - 1 > 0)
                # カードのミニチャート用: 25日線/75日線の直近推移(同じ日付で揃える)
                rec["ma25_hist"] = [round(float(v), 1) for v in s_v[-MA_SPARK_POINTS:]]
                rec["ma75_hist"] = [round(float(v), 1) for v in l_v[-MA_SPARK_POINTS:]]

                if above_now:
                    above_list = [bool(a > b) for a, b in zip(s_v, l_v)]
                    rec["days_since_golden_cross"] = _days_since_cross(
                        above_list, GC_CONFIRM_DAYS, TREND_SCAN_MAX
                    )
                    rec["gc_approaching"] = False
                else:
                    rec["days_since_golden_cross"] = None
                    past_gap = None
                    if len(l_v) > MA_SLOPE_LOOKBACK:
                        p_s = float(s_v[-1 - MA_SLOPE_LOOKBACK])
                        p_l = float(l_v[-1 - MA_SLOPE_LOOKBACK])
                        if p_l > 0:
                            past_gap = p_s / p_l - 1
                    rec["gc_approaching"] = bool(
                        rising
                        and -GC_NEAR_PCT <= gap_now < 0
                        and past_gap is not None
                        and gap_now > past_gap
                    )

        # --- C. 押し目シグナル: 中期は上昇継続 & 短期は調整中 ---
        mid_up = rec.get("ma25_above_ma75") is True or rec.get("ma75_rising") is True
        rsi = rec.get("rsi14")
        short_pullback = (
            (rsi is not None and rsi < PULLBACK_RSI_MAX)
            or rec.get("above_ma25") is False
            or rec.get("below_bb_lower") is True
        )
        rec["pullback_signal"] = bool(mid_up and short_pullback)
        out[sym] = rec
    return out


def fetch_one(symbol: str) -> dict | None:
    """1銘柄ぶんの株価・時価総額・配当利回りを取得。失敗時は None。"""
    last_err = None
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.0, 0.4))
            t = yf.Ticker(symbol)

            fast = t.fast_info
            price = num(fi_get(fast, "last_price", "lastPrice"))
            mcap = num(fi_get(fast, "market_cap", "marketCap"))
            prev = num(fi_get(fast, "previous_close", "previousClose"))

            try:
                info = t.info or {}
            except Exception:  # noqa: BLE001
                info = {}

            price = price or num(info.get("currentPrice")) or num(info.get("regularMarketPrice"))
            mcap = mcap or num(info.get("marketCap"))
            prev = prev or num(info.get("previousClose"))

            # 1株あたり配当: 「予想(dividendRate)」を優先、無ければ「実績12カ月」。
            fwd_rate = num(info.get("dividendRate"))
            ttm_rate = num(info.get("trailingAnnualDividendRate"))
            if fwd_rate:
                drate, dbasis = fwd_rate, "予想"
            elif ttm_rate:
                drate, dbasis = ttm_rate, "実績"
            else:
                drate, dbasis = None, None

            # 配当利回り: 単位が明確な「配当 ÷ 株価」を最優先で算出する。
            # yfinance の dividendYield は比率(0.043)と%(4.3)が混在し不安定なため保険扱い。
            dy = None
            if drate and price:
                dy = drate / price * 100
            if dy is None:
                dy_raw = num(info.get("dividendYield"))
                if dy_raw is not None:
                    dy = dy_raw * 100 if dy_raw < 1 else dy_raw

            # 大型株で 30% 超・0 以下は価格と配当のズレ等によるデータ異常とみなし無効化。
            if dy is not None and not (0 < dy <= 30):
                dy = None

            # 配当性向(%): 1株配当 ÷ 実績EPS。dividend_basis が「予想」なら
            # 予想配当÷実績EPS(配当が直近利益でどれだけ賄えているか)、「実績」なら
            # 教科書どおりの配当性向になる。赤字(EPS<=0)・桁違いは無効化し、
            # 自前で出せなければ info の payoutRatio(比率)で代替する。
            eps = num(info.get("trailingEps"))
            payout = None
            if drate and eps and eps > 0:
                payout = drate / eps * 100
            elif drate and eps is None:
                # EPS が「取れない」ときだけ payoutRatio で代替する。
                # EPS が判っていて 0 以下(赤字)なら配当性向は無意味なので空欄のまま。
                # payoutRatio は 0〜1(まれに >1)の「比率」。% で来る版に備え
                # 5(=500%)以下のときだけ比率とみなして 100 倍する。
                pr = num(info.get("payoutRatio"))
                if pr is not None and 0 < pr <= 5:
                    payout = pr * 100
            if payout is not None and not (0 < payout <= 400):
                payout = None

            if price is None or mcap is None:
                raise ValueError("株価または時価総額が取得できません")

            return {
                "symbol": symbol,
                "price": round(price, 1),
                "prev_close": round(prev, 1) if prev else None,
                "change_pct": round((price / prev - 1) * 100, 2) if prev else None,
                "market_cap": int(mcap),
                "dividend_yield": round(dy, 2) if dy is not None else None,
                "dividend_rate": round(drate, 2) if drate else None,
                "dividend_basis": dbasis,
                "payout_ratio": round(payout, 1) if payout is not None else None,
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    print(f"[skip] {symbol}: {last_err}", file=sys.stderr)
    return None


def main() -> int:
    uni = load_universe()
    meta = {r.symbol: r for r in uni.itertuples(index=False)}
    if not meta:
        raise SystemExit(
            "母集団が空です。config.json の universe 設定と JPX 一覧を確認してください。"
        )
    print(f"母集団: {len(meta)} 銘柄 (universe={CONFIG.get('universe')})")

    workers = int(CONFIG.get("workers", 8))
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_one, s): s for s in meta}
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            if i % 50 == 0 or i == len(futures):
                print(f"  取得 {i}/{len(futures)}")
            if not row:
                continue
            m = meta[row["symbol"]]
            row.update(code=m.code, name=m.name, sector=m.sector, scale=m.scale)
            results.append(row)

    # 取得成功が少なすぎるときは、既存の latest.json を上書きせず失敗扱いで抜ける。
    # (Yahoo のスロットリング等で部分的にしか取れなかった日に前日データを守る)
    min_ratio = float(CONFIG.get("min_success_ratio", 0.5))
    if len(results) < len(meta) * min_ratio:
        print(
            f"[error] 取得成功 {len(results)}/{len(meta)} 件が下限({min_ratio:.0%})未満。"
            f" データ元でブロックされた可能性があるため、既存データを保持して終了します。",
            file=sys.stderr,
        )
        return 1

    min_dy = float(CONFIG["min_dividend_yield"])
    min_mc = int(CONFIG["min_market_cap_yen"])
    picked = [
        r
        for r in results
        if r["dividend_yield"] is not None
        and r["dividend_yield"] >= min_dy
        and r["market_cap"] >= min_mc
    ]
    picked.sort(key=lambda r: r["market_cap"], reverse=True)
    limit = int(CONFIG.get("max_results") or 0)
    if limit:
        picked = picked[:limit]

    # 移動平均(補助情報)は、最終的に表示する銘柄ぶんだけ日足を取得する。
    ma_map = load_ma(picked)
    print(f"移動平均: {len(ma_map)}/{len(picked)} 銘柄で算出")
    for r in picked:
        r.update(ma_map.get(r["symbol"], {}))

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))

    # D. 押し目シグナルの「本日 新規点灯」判定: 直近の *前営業日* スナップショットと比較。
    # (同日に複数回実行しても、比較先は常に前日ぶん。前日データにフィールドが無ければ
    #  誤検知を避けるため全銘柄 False にする)
    today_str = f"{now:%Y-%m-%d}"
    prev_files = sorted(p for p in HIST_DIR.glob("20*.json") if p.stem < today_str)
    prev_signals: set[str] = set()
    prev_has_field = False
    if prev_files:
        try:
            prev = json.loads(prev_files[-1].read_text(encoding="utf-8"))
            prev_items = prev.get("items", [])
            prev_has_field = any("pullback_signal" in it for it in prev_items)
            prev_signals = {
                it["symbol"] for it in prev_items if it.get("pullback_signal")
            }
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 前日スナップショットの読み込みに失敗: {e}", file=sys.stderr)
    for r in picked:
        r["pullback_new"] = bool(
            prev_has_field
            and r.get("pullback_signal")
            and r["symbol"] not in prev_signals
        )
    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "criteria": {
            "min_dividend_yield_pct": min_dy,
            "min_market_cap_yen": min_mc,
            "universe": CONFIG.get("universe"),
            "ma_short": MA_SHORT,
            "ma_long": MA_LONG,
            "ma_slope_lookback": MA_SLOPE_LOOKBACK,
            "rsi_period": RSI_PERIOD,
        },
        "universe_count": len(meta),
        "fetched_count": len(results),
        "match_count": len(picked),
        "items": picked,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    # latest / history とも無整形(機械読み取り専用。ma25_hist 等の配列で肥大化しないように)
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (DATA_DIR / "latest.json").write_text(compact, encoding="utf-8")
    (HIST_DIR / f"{now:%Y-%m-%d}.json").write_text(compact, encoding="utf-8")
    dates = sorted(p.stem for p in HIST_DIR.glob("20*.json"))
    (DATA_DIR / "history.json").write_text(
        json.dumps({"dates": dates}, ensure_ascii=False), encoding="utf-8"
    )

    print(f"該当 {len(picked)} 件 / 取得成功 {len(results)} 件 を書き出しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
