"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
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

  applyTheme(f.theme);
  $("#theme").checked = document.documentElement.dataset.theme === "dark";
}

/* イベントバインドはデータ取得の成否と無関係に一度だけ行う
   (取得失敗時でも再読み込みボタンを効かせるため) */
function bindControls() {
  ["dy", "mc", "sort", "sector", "tech"].forEach(id => $("#" + id).addEventListener("input", onFilter));
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

  const rows = d.items.filter(i =>
    (i.dividend_yield ?? 0) >= minDy &&
    i.market_cap >= minMc &&
    (!sec || i.sector === sec) &&
    techPass(i, tech) &&
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

  $("#list").innerHTML = rows.map(card).join("");
  $("#empty").hidden = rows.length > 0;
}

/* テクニカル絞り込みの判定 */
function techPass(i, tech) {
  switch (tech) {
    case "ma_up":  return i.ma25_rising === true;
    case "turn":   return i.ma25_rising === true && i.ma25_rising_days >= 1 && i.ma25_rising_days <= 10;
    case "above":  return i.above_ma25 === true;
    case "gc_new": return i.days_since_golden_cross != null && i.days_since_golden_cross <= 20;
    case "gc_near": return i.gc_approaching === true;
    default:       return true;
  }
}
/* 「上向きに転じて日が浅い順」用: 上向きでないものは最後へ */
function turnRank(i) {
  return (i.ma25_rising === true && i.ma25_rising_days >= 1) ? i.ma25_rising_days : 1e9;
}

/* カード上部のテクニカル1行(25日線の向き / 株価乖離 / 75日線・GC) */
function techLines(i) {
  if (i.ma25_rising == null && i.above_ma25 == null) return "";
  const parts = [];

  if (i.ma25_rising === true) {
    const d = i.ma25_rising_days >= 1 ? ` ${i.ma25_rising_days}日目` : "";
    parts.push(`<span class="t-up">25日線 ↗ 上向き${d}</span>`);
  } else if (i.ma25_rising === false) {
    parts.push(`<span class="t-muted">25日線 ↘ 下向き</span>`);
  }

  if (i.price_vs_ma25_pct != null) {
    parts.push(`<span class="${i.above_ma25 ? "t-up" : "t-muted"}">株価${slope(i.price_vs_ma25_pct)}</span>`);
  }

  if (i.days_since_golden_cross != null && i.days_since_golden_cross <= 25) {
    parts.push(`<span class="t-gc">GC ${i.days_since_golden_cross}日目</span>`);
  } else if (i.gc_approaching === true) {
    parts.push(`<span class="t-gc">GC間近${slope(i.gc_gap_pct)}</span>`);
  } else if (i.ma25_above_ma75 === true) {
    parts.push(`<span class="t-up">25&gt;75日線</span>`);
  } else if (i.ma25_above_ma75 === false) {
    parts.push(`<span class="t-muted">25&lt;75日線</span>`);
  }

  return `<div class="tech">${parts.join('<span class="t-sep">・</span>')}</div>`;
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
    ${techLines(i)}
    <div class="yield"><span>配当利回り</span><b>${dy}<i>%</i></b></div>
    <dl class="metrics">
      <div><dt>株価</dt><dd>${price(i.price)}</dd></div>
      <div><dt>前日比</dt><dd class="${chgCls}">${pct(chg)}</dd></div>
      <div><dt>時価総額</dt><dd>${yen(i.market_cap)}</dd></div>
      <div><dt>${divLabel}</dt><dd>${dividend}</dd></div>
    </dl>
  </li>`;
}

applyTheme(state.f.theme);
bindControls();

if ("serviceWorker" in navigator) {
  addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}

load();
