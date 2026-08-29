#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日本株「高配当 × 大型株」スクリーナー — データ取得バッチ

処理の流れ:
  1. JPX公表の「東証上場銘柄一覧 (data_j.xls)」から母集団を作る
  2. yfinance で各銘柄の株価・時価総額・予想配当利回りを取得
  3. config.json の条件(利回り下限・時価総額下限)で絞り込む
  4. docs/data/latest.json と docs/data/history/<日付>.json に書き出す

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

MA_WINDOW = 26            # 移動平均の期間(営業日)
MA_SLOPE_LOOKBACK = 5     # 何営業日前と比べて「上向き」を判定するか


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


def load_ma26(symbols: list[str]) -> dict[str, dict]:
    """全銘柄の日足終値をまとめて取得し、26日移動平均とその傾きを計算する。

    返り値: {symbol: {"ma26": float, "ma26_rising": bool, "ma26_slope_pct": float}}
    取れなかった銘柄はキーごと省略(フロント側で「—」表示)。補助情報なので
    ここで失敗しても全体は止めない。
    """
    out: dict[str, dict] = {}
    try:
        hist = yf.download(
            symbols,
            period="6mo",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 26日移動平均の取得に失敗しました: {e}", file=sys.stderr)
        return out
    if hist is None or hist.empty:
        return out

    multi = isinstance(hist.columns, pd.MultiIndex)
    available = set(hist.columns.get_level_values(0)) if multi else set(symbols)

    for sym in symbols:
        try:
            close = (hist[sym]["Close"] if multi else hist["Close"]).dropna()
        except Exception:  # noqa: BLE001
            continue
        if multi and sym not in available:
            continue
        if len(close) < MA_WINDOW + MA_SLOPE_LOOKBACK:
            continue
        ma = close.rolling(MA_WINDOW).mean().dropna()
        if len(ma) <= MA_SLOPE_LOOKBACK:
            continue
        cur = float(ma.iloc[-1])
        past = float(ma.iloc[-1 - MA_SLOPE_LOOKBACK])
        if past <= 0:
            continue
        out[sym] = {
            "ma26": round(cur, 1),
            "ma26_rising": bool(cur > past),
            "ma26_slope_pct": round((cur / past - 1) * 100, 2),
        }
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

    # 26日移動平均(補助情報)。まとめて1回のダウンロードで取得。
    ma_map = load_ma26(list(meta))
    print(f"26日移動平均: {len(ma_map)}/{len(meta)} 銘柄で算出")
    for r in results:
        r.update(ma_map.get(r["symbol"], {}))

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

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "criteria": {
            "min_dividend_yield_pct": min_dy,
            "min_market_cap_yen": min_mc,
            "universe": CONFIG.get("universe"),
            "ma_window": MA_WINDOW,
            "ma_slope_lookback": MA_SLOPE_LOOKBACK,
        },
        "universe_count": len(meta),
        "fetched_count": len(results),
        "match_count": len(picked),
        "items": picked,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (HIST_DIR / f"{now:%Y-%m-%d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    dates = sorted(p.stem for p in HIST_DIR.glob("20*.json"))
    (DATA_DIR / "history.json").write_text(
        json.dumps({"dates": dates}, ensure_ascii=False), encoding="utf-8"
    )

    print(f"該当 {len(picked)} 件 / 取得成功 {len(results)} 件 を書き出しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
