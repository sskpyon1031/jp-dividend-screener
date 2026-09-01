"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const GC_NEW_DAYS = 20;   // 「ゴールデンクロスが新しい」とみなす営業日数(表示・絞り込み共通)
const state = { raw: null, f: {} };
try { state.f = JSON.parse(localStorage.getItem("filters") || "{}"); } catch { state.f = {}; }

/* ---------- 表示フォーマット ---------- */
const yen = n => {
  if (n == null) return "—";
  if (n >= 1e12) return "¥" + (n / 1e12).toFixed(2) + "兆";
  if (n >= 1e8)  return "¥" + Math.round(n / 1e8).toLocaleString("ja-JP") + "億";
  return "¥" + Math.round(n).toLocaleString("ja-JP");
};
const price = n => n == null ? "—"
  : "¥" + n.toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const pct = n => n == null ? "—" : (n > 0 ? "+" : "") + n.toFixed(2) + "%";
const slope = n => n == null ? "" : " " + (n > 0 ? "+" : "") + n.toFixed(1) + "%";
const fmtDate = iso => {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = x => String(x).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};
const esc = s => String(s).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- データ読み込み ---------- */
async function load() {
  $("#list").innerHTML = "";
  $("#summary").textContent = "読み込み中…";
  try {
    // cache:"no-store" で HTTP キャッシュは回避しつつ、URL は固定して
    // Service Worker がオフライン時に再利用できるようにする（?_= は付けない）
    const res = await fetch("data/latest.json", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    state.raw = await res.json();
  } catch (e) {
    $("#summary").textContent = "データを読み込めませんでした（" + e.message + "）";
    return;
  }
  initControls();
  render();
}

function initControls() {
  const d = state.raw;
  const sectors = [...new Set(d.items.map(i => i.sector))].sort((a, b) => a.localeCompare(b, "ja"));
  const sel = $("#sector");
  sel.length = 1;
  sectors.forEach(s => sel.add(new Option(s, s)));

  const serverDy = (d.criteria && d.criteria.min_dividend_yield_pct) || 4;
  const dy = $("#dy");
  dy.min = serverDy;
  const f = state.f;
  dy.value = Math.max(serverDy, parseFloat(f.dy) || serverDy);
  $("#mc").value = f.mc ?? "0";
  $("#sort").value = f.sort ?? "mc";
  $("#sector").value = sectors.includes(f.sector) ? f.sector : "";
  $("#q").value = f.q ?? "";
  $("#tech").value = f.tech ?? "";
  // basis / payout は後から追加した要素。旧 index.html がキャッシュから
  // 出た過渡期でも落ちないよう存在を確認してから触る。
  const basisEl = $("#basis"); if (basisEl) basisEl.value = f.basis ?? "";
  const payoutEl = $("#payout"); if (payoutEl) payoutEl.value = f.payout ?? "";

  applyTheme(f.theme);
  $("#theme").checked = document.documentElement.dataset.theme === "dark";
}

/* イベントバインドはデータ取得の成否と無関係に一度だけ行う
   (取得失敗時でも再読み込みボタンを効かせるため) */
function bindControls() {
  ["dy", "mc", "sort", "sector", "tech", "basis", "payout"].forEach(id => $("#" + id)?.addEventListener("input", onFilter));
  $("#q").addEventListener("input", onFilter);
  $("#theme").addEventListener("change", () => {
    applyTheme($("#theme").checked ? "dark" : "light");
    persist();
  });
  $("#reload").addEventListener("click", load);
}

function applyTheme(pref) {
  const dark = pref ? pref === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = dark ? "#0b0f14" : "#eef1f4";
  if (pref) state.f.theme = pref;
}

function onFilter() {
  if (!state.raw) return;          // データ未取得なら操作を無視(保存内容を壊さない)
  persist();
  render();
}

function persist() {
  // データ取得前(コントロールが既定値のまま)は、保存済みの絞り込み条件を
  // 上書きしない。テーマだけは変更を反映する。
  if (state.raw) {
    state.f = {
      dy: $("#dy").value,
      mc: $("#mc").value,
      sort: $("#sort").value,
      sector: $("#sector").value,
      q: $("#q").value.trim(),
      tech: $("#tech").value,
      basis: $("#basis")?.value ?? state.f.basis ?? "",
      payout: $("#payout")?.value ?? state.f.payout ?? "",
      theme: state.f.theme,
    };
  }
  localStorage.setItem("filters", JSON.stringify(state.f));
}

/* ---------- 描画 ---------- */
function render() {
  const d = state.raw;
  if (!d) return;

  const minDy = parseFloat($("#dy").value);
  $("#dyOut").textContent = minDy.toFixed(1) + "%";
  const minMc = parseFloat($("#mc").value) || 0;
  const sec = $("#sector").value;
  const q = $("#q").value.trim().toLowerCase();
  const sort = $("#sort").value;
  const tech = $("#tech").value;
  const basis = $("#basis")?.value || "";
  const maxPayout = parseFloat($("#payout")?.value) || 0;

  const rows = d.items.filter(i =>
    (i.dividend_yield ?? 0) >= minDy &&
    i.market_cap >= minMc &&
    (!sec || i.sector === sec) &&
    techPass(i, tech) &&
    (!basis || i.dividend_basis === basis) &&
    (!maxPayout || (i.payout_ratio != null && i.payout_ratio <= maxPayout)) &&
    (!q || i.name.toLowerCase().includes(q) || String(i.code).includes(q))
  );

  const cmps = {
    mc: (a, b) => b.market_cap - a.market_cap,
    dy: (a, b) => (b.dividend_yield ?? 0) - (a.dividend_yield ?? 0),
    ma: (a, b) => (b.ma25_slope_pct ?? -1e9) - (a.ma25_slope_pct ?? -1e9),
    turn: (a, b) => turnRank(a) - turnRank(b),
    gc: (a, b) => (a.days_since_golden_cross ?? 1e9) - (b.days_since_golden_cross ?? 1e9),
    chg_desc: (a, b) => (b.change_pct ?? -1e9) - (a.change_pct ?? -1e9),
    chg_asc: (a, b) => (a.change_pct ?? 1e9) - (b.change_pct ?? 1e9),
    range_pos: (a, b) => (a.range_pos_pct ?? 1e9) - (b.range_pos_pct ?? 1e9),
    rsi: (a, b) => (a.rsi14 ?? 1e9) - (b.rsi14 ?? 1e9),
    dd: (a, b) => (a.drawdown_from_high_pct ?? 1e9) - (b.drawdown_from_high_pct ?? 1e9),
    pullback: (a, b) =>
      ((b.pullback_signal === true) - (a.pullback_signal === true)) ||
      ((b.pullback_new === true) - (a.pullback_new === true)) ||
      ((a.rsi14 ?? 1e9) - (b.rsi14 ?? 1e9)),
    code: (a, b) => String(a.code).localeCompare(String(b.code)),
  };
  rows.sort(cmps[sort] || cmps.mc);

  const c = d.criteria || {};
  $("#summary").innerHTML =
    `<strong>${rows.length}</strong> 銘柄該当` +
    `<span class="sub">利回り ${c.min_dividend_yield_pct}%以上 ・ ` +
    `時価総額 ${yen(c.min_market_cap_yen)}以上 ・ 母集団 ${d.universe_count}銘柄</span>` +
    `<span class="sub">最終更新 ${fmtDate(d.generated_at)} （取得成功 ${d.fetched_count}銘柄）</span>`;

  const note = $("#notice");
  if (d.sample) {
    note.hidden = false;
    note.textContent = "※ サンプルデータです。GitHub Actions の初回実行後に実データへ切り替わります。";
  } else {
    note.hidden = true;
  }

  // ミニチャートを持つ銘柄が1つも無ければ凡例を隠す
  const legend = $(".legend");
  if (legend) {
    legend.hidden = !d.items.some(x => Array.isArray(x.ma25_hist) && x.ma25_hist.length >= 2);
  }

  $("#list").innerHTML = rows.map(card).join("");
  $("#empty").hidden = rows.length > 0;
}

/* テクニカル絞り込みの判定 */
function techPass(i, tech) {
  switch (tech) {
    case "ma_up":  return i.ma25_rising === true;
    case "turn":   return i.ma25_rising === true && i.ma25_rising_days >= 1 && i.ma25_rising_days <= 10;
    case "above":  return i.above_ma25 === true;
    case "gc_new": return i.days_since_golden_cross != null && i.days_since_golden_cross <= GC_NEW_DAYS;
    case "gc_near": return i.gc_approaching === true;
    case "pullback":     return i.pullback_signal === true;
    case "pullback_new": return i.pullback_new === true;
    case "rsi_os":       return i.rsi14 != null && i.rsi14 <= 30;
    case "near_low":      return i.range_pos_pct != null && i.range_pos_pct <= 25;
    default:       return true;
  }
}
/* 「上向きに転じて日が浅い順」用: 上向きでないものは最後へ */
function turnRank(i) {
  return (i.ma25_rising === true && i.ma25_rising_days >= 1) ? i.ma25_rising_days : 1e9;
}

/* 時間の限られたGC状態(新しいGC / 接近中)だけをカード本体に太字1行で出す。
   静的な「上昇配列 / クロス前」は detailBlock 側にまわす。 */
function gcFreshLine(i) {
  const g = i.days_since_golden_cross;
  if (g != null && g <= GC_NEW_DAYS) {
    return `<div class="gc-line fresh">ゴールデンクロス <b>${g}営業日前</b>・強気転換のサイン</div>`;
  }
  if (i.gc_approaching === true) {
    const gap = i.gc_gap_pct != null ? Math.abs(i.gc_gap_pct).toFixed(1) : "";
    return `<div class="gc-line near">ゴールデンクロスまで <b>あと ${gap}%</b>・接近中</div>`;
  }
  return "";
}

/* 折りたたみ内に出す、移動平均まわりのプレーンな説明行 */
function maDetailRows(i) {
  const rows = [];
  if (i.ma25_rising === true) {
    const d = i.ma25_rising_days >= 1 ? ` ${i.ma25_rising_days}日目` : "";
    rows.push(`<span class="t-up">25日線 ↗ 上向き${d}</span>`);
  } else if (i.ma25_rising === false) {
    rows.push(`<span class="t-muted">25日線 ↘ 下向き</span>`);
  }
  if (i.price_vs_ma25_pct != null) {
    rows.push(`<span class="${i.above_ma25 ? "t-up" : "t-muted"}">株価は25日線${slope(i.price_vs_ma25_pct)}</span>`);
  }
  // 新しいGC/接近中はカード本体で出しているので、ここは静的な状態だけ
  const g = i.days_since_golden_cross;
  const fresh = (g != null && g <= GC_NEW_DAYS) || i.gc_approaching === true;
  if (!fresh && i.ma25_above_ma75 === true) {
    const gap = i.gc_gap_pct != null ? `(差 +${i.gc_gap_pct.toFixed(1)}%)` : "";
    rows.push(`<span class="t-muted">上昇配列 25日線 &gt; 75日線 ${gap}</span>`);
  } else if (!fresh && i.ma25_above_ma75 === false) {
    const gap = i.gc_gap_pct != null ? `(あと ${Math.abs(i.gc_gap_pct).toFixed(1)}%)` : "";
    rows.push(`<span class="t-muted">クロス前 25日線 &lt; 75日線 ${gap}</span>`);
  }
  return rows;
}

/* 配当性向のバー(0〜100%+、緑=余裕 / 琥珀=高め・超過) */
function payoutGauge(i) {
  const p = i.payout_ratio;
  if (p == null) return "";
  const r = Math.round(p);
  const cls = r > 100 ? "bad" : r > 80 ? "warn" : "ok";
  const note = r > 100 ? "⚠ 利益を超える配当" : r > 80 ? "やや高め" : "利益に余裕";
  return `<div class="gauge">
    <div class="gauge-h"><span>配当性向</span><b>${r}%</b><span class="gauge-note ${cls}">${note}</span></div>
    <div class="gauge-track" aria-hidden="true"><i class="gauge-fill ${cls}" style="width:${Math.min(r, 100)}%"></i></div>
  </div>`;
}

/* 押し目シグナルが「なぜ点灯したか」を短く添える */
function pullbackWhy(i) {
  const w = [];
  if (i.rsi14 != null && i.rsi14 < 40) w.push("RSI " + Math.round(i.rsi14));
  if (i.above_ma25 === false) w.push("25日線割れ");
  if (i.below_bb_lower === true) w.push("バンド下限");
  return w.length ? "（" + w.join("・") + "）" : "";
}

/* カード上部の状態チップ列。20枚を読まずにスクロールして選別するための一覧性。
   何も該当しなければ空(=特筆すべき状態なし)。 */
function badges(i) {
  const b = [];
  if (i.pullback_new === true) {
    b.push(`<span class="bdg bdg-new">押し目・本日新規</span>`);
  } else if (i.pullback_signal === true) {
    b.push(`<span class="bdg bdg-good">押し目</span>`);
  }
  if (i.rsi14 != null) {
    const r = Math.round(i.rsi14);
    if (r <= 40) b.push(`<span class="bdg bdg-good">RSI ${r}</span>`);
    else if (r >= 70) b.push(`<span class="bdg bdg-warn">RSI ${r}</span>`);
  }
  if (i.range_pos_pct != null) {
    const p = Math.round(i.range_pos_pct);
    if (p <= 25) b.push(`<span class="bdg bdg-good">安値圏 ${p}%</span>`);
    else if (p >= 75) b.push(`<span class="bdg bdg-warn">高値圏 ${p}%</span>`);
  }
  const g = i.days_since_golden_cross;
  if (g != null && g <= GC_NEW_DAYS) b.push(`<span class="bdg bdg-good">GC ${g}日</span>`);
  if (i.payout_ratio != null && Math.round(i.payout_ratio) > 100) {
    b.push(`<span class="bdg bdg-warn">配当性向 ${Math.round(i.payout_ratio)}%</span>`);
  }
  return b.length ? `<div class="bdgs">${b.join("")}</div>` : "";
}

/* 最重要シグナル。点灯時だけ枠付きバナーに格上げする。 */
function pullbackBanner(i) {
  if (i.pullback_signal !== true) return "";
  const tag = i.pullback_new === true ? `<span class="pbn-new">本日新規</span>` : "";
  return `<div class="pbn">
    <b>押し目シグナル</b>${tag}
    <span class="pbn-txt">中期は上昇継続・短期は調整中${pullbackWhy(i)}</span>
  </div>`;
}

/* 52週レンジ内の位置をバーで示す(左=安値・緑 / 右=高値・琥珀、●=現在地)。 */
function range52Bar(i) {
  if (i.range_pos_pct == null || i.range_52w_low == null || i.range_52w_high == null) return "";
  const p = Math.max(0, Math.min(100, i.range_pos_pct));
  const dd = i.drawdown_from_high_pct;
  const tag = `位置 ${Math.round(p)}%` +
    (dd != null && dd < 0 ? ` ・ 高値 ${Math.round(dd)}%` : "");
  return `<div class="r52">
    <div class="r52-h"><span>52週レンジ</span><span class="r52-tag">${tag}</span></div>
    <div class="r52-track" aria-hidden="true"><i class="r52-dot" style="left:${p}%"></i></div>
    <div class="r52-ends"><span>${yen(i.range_52w_low)}</span><span>${yen(i.range_52w_high)}</span></div>
  </div>`;
}

/* RSI(14) のゲージ。0/30/70/100 目盛り、●の色でゾーンを示す。折りたたみ内で使用。 */
function rsiGauge(i) {
  if (i.rsi14 == null) return "";
  const r = Math.round(i.rsi14);
  const z = r <= 30 ? "os" : r >= 70 ? "ob" : "mid";
  const lab = r <= 30 ? "売られすぎ" : r < 40 ? "やや売られ" : r >= 70 ? "過熱" : "中立";
  const left = Math.max(0, Math.min(100, r));
  return `<div class="gauge rsi">
    <div class="gauge-h"><span>RSI(14)</span><b>${r}</b><span class="gauge-note">${lab}</span></div>
    <div class="rsi-track" aria-hidden="true"><i class="rsi-dot ${z}" style="left:${left}%"></i></div>
    <div class="rsi-scale" aria-hidden="true"><span>0</span><span>30</span><span>70</span><span>100</span></div>
  </div>`;
}

/* 補助情報は折りたたみに集約(既定は閉じる)。中身が無ければ何も出さない。 */
function detailBlock(i) {
  const rows = maDetailRows(i);
  const body = (rows.length ? `<div class="dtl-rows">${rows.join("")}</div>` : "") + rsiGauge(i);
  if (!body) return "";
  return `<details class="more"><summary>詳細（移動平均・GC・RSI）</summary>${body}</details>`;
}

/* 25日線(緑)・75日線(灰)の直近推移と、交差点(●)を描くミニチャート */
function sparkline(i) {
  const a = i.ma25_hist, b = i.ma75_hist;
  if (!Array.isArray(a) || !Array.isArray(b) || a.length < 2 || a.length !== b.length) return "";
  const n = a.length, W = 300, H = 44, pad = 4;
  const lo = Math.min(...a, ...b), hi = Math.max(...a, ...b);
  // Y軸レンジには下限(価格水準の約1%)を設け、ほぼ平行な2線を誇張しない。
  // 余白は上下に均等配分してレンジ中央に描く。
  const rawSpan = hi - lo;
  const span = Math.max(rawSpan, ((hi + lo) / 2) * 0.01) || 1;
  const base = lo - (span - rawSpan) / 2;
  const X = k => pad + (W - 2 * pad) * k / (n - 1);
  const Y = v => pad + (H - 2 * pad) * (1 - (v - base) / span);
  const d = arr => arr.map((v, k) => `${k ? "L" : "M"}${X(k).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ");

  // 「25日線 >= 75日線」の状態が反転した箇所を交差点とみなす(丸め値の同値タッチにも対応)
  let mx = null, my = null, kind = "";
  let wasAbove = a[0] >= b[0];
  for (let k = 1; k < n; k++) {
    const nowAbove = a[k] >= b[k];
    if (nowAbove === wasAbove) continue;
    const d0 = a[k - 1] - b[k - 1], d1 = a[k] - b[k];
    const denom = d0 - d1;
    const t = Math.min(Math.max(denom !== 0 ? d0 / denom : 0.5, 0), 1);
    mx = X(k - 1 + t);
    my = Y(a[k - 1] + t * (a[k] - a[k - 1]));
    kind = nowAbove ? "gc" : "dc";
    wasAbove = nowAbove;
  }
  // 交差点マーカーは HTML 要素で重ねる(SVG を横伸ばししても真円を保つため)
  const dot = mx == null ? ""
    : `<i class="spark-dot ${kind}" style="left:${(mx / W * 100).toFixed(1)}%;top:${(my / H * 100).toFixed(1)}%"></i>`;

  return `<div class="spark-wrap">
    <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d(b)}" class="spark-l75"/><path d="${d(a)}" class="spark-l25"/>
    </svg>${dot}
  </div>`;
}

/* ミニチャート + 株探の個別チャートへのリンク(チャート全体がタップ可能) */
function chartBlock(i) {
  if (!i.code) return sparkline(i);
  const url = "https://kabutan.jp/stock/chart?code=" + encodeURIComponent(i.code);
  return `<a class="chart-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">
    ${sparkline(i)}
    <span class="chart-cta">株探でチャートを見る<span aria-hidden="true"> ↗</span></span>
  </a>`;
}

function card(i) {
  const chg = i.change_pct;
  const chgCls = chg == null ? "" : chg > 0 ? "up" : chg < 0 ? "down" : "";
  const dividend = i.dividend_rate != null
    ? "¥" + i.dividend_rate.toLocaleString("ja-JP") : "—";
  const divLabel = "1株配当" + (i.dividend_basis ? `（${i.dividend_basis}）` : "");
  const dy = i.dividend_yield != null ? i.dividend_yield.toFixed(2) : "—";
  return `<li class="card">
    <div class="card-head">
      <span class="code">${esc(i.code)}</span>
      <span class="name">${esc(i.name)}</span>
      <span class="chip">${esc(i.sector)}</span>
    </div>
    ${badges(i)}
    ${pullbackBanner(i)}
    ${gcFreshLine(i)}
    ${chartBlock(i)}
    <div class="yield"><span>配当利回り</span><b>${dy}<i>%</i></b></div>
    ${payoutGauge(i)}
    ${range52Bar(i)}
    <dl class="metrics">
      <div><dt>株価</dt><dd>${price(i.price)}</dd></div>
      <div><dt>前日比</dt><dd class="${chgCls}">${pct(chg)}</dd></div>
      <div><dt>時価総額</dt><dd>${yen(i.market_cap)}</dd></div>
      <div><dt>${divLabel}</dt><dd>${dividend}</dd></div>
    </dl>
    ${detailBlock(i)}
  </li>`;
}

applyTheme(state.f.theme);
bindControls();

if ("serviceWorker" in navigator) {
  addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

load();
