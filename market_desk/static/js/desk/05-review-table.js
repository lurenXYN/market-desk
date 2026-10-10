const REV_COL_STORAGE = "md_rev_cols_v1";
const REV_FILTER_STORAGE = (window.MDReview && MDReview.FILTER_STORAGE) || "md_rev_filters_v1";
function saveRevFilters() {
  if (window.MDReview && MDReview.saveFilters) MDReview.saveFilters();
}
function loadRevFilters() {
  if (window.MDReview && MDReview.loadFilters) MDReview.loadFilters();
}
loadRevFilters();
function liveArrowHtml(r) {
  const dir = r.live_arrow;
  if (!dir) return "";
  const arrow = dir === "up" ? "↑" : (dir === "down" ? "↓" : "→");
  const sec = Number(r.live_vs_sec || 0);
  const tip = sec > 0
    ? `较约${sec >= 60 ? Math.round(sec / 60) + "分钟" : sec + "秒"}前`
    : "短时方向";
  const vs = r.live_vs_pct;
  const vsHtml = vs == null
    ? ""
    : `<span class="live-vs ${dir === "flat" ? "" : (vs >= 0 ? "up" : "down")}">${(vs > 0 ? "+" : "") + Number(vs).toFixed(2)}%</span>`;
  return ` <span class="live-arrow ${dir}" title="${tip}">${arrow}</span>${vsHtml}`;
}
function hitRateSpark(hist, activeDate) {
  const pts = (hist || [])
    .map((h) => ({ d: h.trade_date || h.date || "", v: h.buy_hit_rate }))
    .filter((x) => x.v != null);
  if (!pts.length) return `<div class="meta">暂无已打分的历史命中率</div>`;
  const w = 560, h = 56, pad = 6;
  const vals = pts.map((p) => Number(p.v));
  const min = Math.min(0, ...vals);
  const max = Math.max(100, ...vals);
  const span = Math.max(1, max - min);
  const step = pts.length === 1 ? 0 : (w - pad * 2) / (pts.length - 1);
  const path = pts.map((p, i) => {
    const x = pad + i * step;
    const y = h - pad - ((Number(p.v) - min) / span) * (h - pad * 2);
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = pts[pts.length - 1];
  const dayBtns = pts.map((p) =>
    `<button type="button" class="${p.d === activeDate ? "on" : ""}" data-rev-day="${p.d}">${String(p.d).slice(5)} ${Number(p.v).toFixed(0)}%</button>`
  ).join("");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <path d="${path}" fill="none" stroke="#38bdf8" stroke-width="2" />
      </svg>
      <div class="meta">${pts[0].d} → ${last.d} · 最近命中率 ${last.v}% · 点日期切换</div>
      <div class="rev-hist-days">${dayBtns}</div>`;
}
const REV_COL_CATALOG = [
  { id: "date", label: "信号时间" },
  { id: "type", label: "类型" },
  { id: "name", label: "名称" },
  { id: "score", label: "风险清单" },
  { id: "code", label: "代码" },
  { id: "price", label: "建议价" },
  { id: "fill", label: "成交" },
  { id: "chase", label: "不追" },
  { id: "mainline", label: "主线" },
  { id: "board", label: "板块" },
  { id: "vs_ml", label: "对照主线" },
  { id: "phase", label: "相位" },
  { id: "holder_n", label: "股东户数" },
  { id: "holder_chg", label: "户数增减%" },
  { id: "holder_avg", label: "户均市值" },
  { id: "zt_ytd", label: "年内涨停" },
  { id: "day1", label: "次日%" },
  { id: "day3", label: "三日%" },
  { id: "excess", label: "超中证1000" },
  { id: "result", label: "结果" },
  { id: "ops", label: "操作" },
];
const REV_COL_DEFAULT = REV_COL_CATALOG.map((c) => ({ id: c.id, on: true }));
// Review signal ids ticked for 纠结对比; survives table repaints / auto-refresh.
const revPickIds = new Set();
// Per-row pick scores from /api/review/scores (today live, past days stored at signal time).
let revScores = { day: "", live: false, items: {}, top: [], loading: false };
let reviewScoreSeq = 0;
let revSort = { id: "", dir: 1 }; // dir: 1 asc, -1 desc

function fmtHolderNum(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 10000) return (v / 10000).toFixed(1) + "万";
  return String(Math.round(v));
}
function fmtHolderAvgWan(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 100) return v.toFixed(0) + "万";
  return v.toFixed(2) + "万";
}

function loadRevColState() {
  try {
    const raw = JSON.parse(localStorage.getItem(REV_COL_STORAGE) || "null");
    if (!raw || !Array.isArray(raw) || !raw.length) return REV_COL_DEFAULT.map((x) => ({ ...x }));
    const known = new Set(REV_COL_CATALOG.map((c) => c.id));
    const seen = new Set();
    const out = [];
    for (const row of raw) {
      const id = row && row.id;
      if (!known.has(id) || seen.has(id)) continue;
      seen.add(id);
      out.push({ id, on: row.on !== false });
    }
    REV_COL_CATALOG.forEach((c, i) => {
      if (seen.has(c.id)) return;
      // New columns land after their catalog predecessor instead of at the end.
      const prev = i > 0 ? out.findIndex((x) => x.id === REV_COL_CATALOG[i - 1].id) : -1;
      out.splice(prev >= 0 ? prev + 1 : out.length, 0, { id: c.id, on: true });
      seen.add(c.id);
    });
    if (!out.some((x) => x.on)) out[0].on = true;
    return out;
  } catch (e) {
    return REV_COL_DEFAULT.map((x) => ({ ...x }));
  }
}

function saveRevColState(state) {
  localStorage.setItem(REV_COL_STORAGE, JSON.stringify(state));
}

let revColState = loadRevColState();

function revColLabel(id) {
  return (REV_COL_CATALOG.find((c) => c.id === id) || {}).label || id;
}
function revNormSrc(r) {
  return (window.MDReview && MDReview.normSrc) ? MDReview.normSrc(r) : "main";
}
function revSrcTagMeta(src) {
  return (window.MDReview && MDReview.srcTagMeta) ? MDReview.srcTagMeta(src) : null;
}
function revTrendChips(r) {
  return (window.MDReview && MDReview.trendChipsHtml) ? MDReview.trendChipsHtml(r) : "";
}
function revSortValue(r, id) {
  if (id === "date") return String(r.signaled_at || r.trade_date || "");
  if (id === "type") return String(r.signal_type || "");
  if (id === "name") return String(r.name || r.code || "");
  if (id === "code") return String(r.code || "");
  if (id === "price") {
    const n = Number(r.price);
    return Number.isFinite(n) ? n : null;
  }
  if (id === "fill") {
    const n = Number(r.fill_price);
    return Number.isFinite(n) ? n : null;
  }
  if (id === "chase") {
    const v = r.chase_price != null ? r.chase_price : (r.payload && r.payload.chase_price);
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  if (id === "day1") return r.outcome_day1_pct == null ? null : Number(r.outcome_day1_pct);
  if (id === "day3") return r.outcome_day3_pct == null ? null : Number(r.outcome_day3_pct);
  if (id === "excess") return r.excess_d3_pct == null ? null : Number(r.excess_d3_pct);
  if (id === "result") return String(r.outcome_label || "");
  if (id === "vs_ml") return String(r.vs_mainline || "");
  if (id === "phase") return String(r.phase || "");
  if (id === "mainline") return String(r.mainline || "");
  if (id === "board") return String(r.board_text || ((r.boards || []).join("/")) || "");
  if (id === "holder_n") return r.holder_num == null ? null : Number(r.holder_num);
  if (id === "holder_chg") return r.holder_chg_pct == null ? null : Number(r.holder_chg_pct);
  if (id === "holder_avg") return r.holder_avg_wan == null ? null : Number(r.holder_avg_wan);
  if (id === "zt_ytd") return r.zt_ytd == null ? null : Number(r.zt_ytd);
  if (id === "score") {
    const it = revScores.items[String(r.id)];
    return it && it.score != null ? Number(it.score) : null;
  }
  return "";
}
function applyRevSort(rows) {
  const id = revSort.id;
  if (!id || id === "ops") return rows;
  const dir = revSort.dir || 1;
  return [...rows].sort((a, b) => {
    const va = revSortValue(a, id);
    const vb = revSortValue(b, id);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
    return String(va).localeCompare(String(vb), "zh") * dir;
  });
}

function visibleRevCols() {
  return revColState.filter((c) => c.on).map((c) => c.id);
}

function renderRevColPanel() {
  const list = document.getElementById("revColList");
  list.innerHTML = revColState.map((c, i) => `
        <li draggable="true" data-idx="${i}">
          <span class="grip">⋮⋮</span>
          <label>
            <input type="checkbox" data-id="${c.id}" ${c.on ? "checked" : ""} />
            ${revColLabel(c.id)}
          </label>
        </li>`).join("");
}

function revSignalTime(r) {
  const raw = String(r.signaled_at || r.trade_date || "").trim();
  if (!raw) return "—";
  const m = raw.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?/);
  if (m) {
    return `<span title="${raw}">${m[1]}<div class="meta">${m[2]}</div></span>`;
  }
  return raw;
}
function revTradeDefaultPx(r) {
  // Prefer actual fill / live last. Never silently use plan suggest (r.price).
  if (r.fill_price != null && r.fill_price !== "") return r.fill_price;
  if (r.live_last != null && r.live_last !== "") return r.live_last;
  if (r.last != null && r.last !== "") return r.last;
  return "";
}
function revTradeDefaultQty(r) {
  if (r.fill_qty) return r.fill_qty;
  if (r.plan_qty) return r.plan_qty;
  const pq = r.payload && r.payload.qty;
  if (pq) return pq;
  return "";
}
function escAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/'/g, "&#39;");
}
