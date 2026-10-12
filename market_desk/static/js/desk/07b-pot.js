const POT_FILTER_KEY = "md_pot_filters_v1";
let potFilters = { focus: false, early: false };
let potSort = { id: "", dir: -1 };
let potViewDate = "";
let potLoadedAt = 0;
let lastPot = { dates: [], scan: null };

/** Load one scan day for the pot tab; pot rows ride on the MA-fan day payload. */
async function loadPot(force, date) {
  const want = (date !== undefined && date !== null) ? String(date).trim() : (potViewDate || "").trim();
  if (!force && date === undefined && Date.now() - potLoadedAt < 60000) return;
  let status = null;
  try {
    setModStamp("potStamp", null, { loading: true, label: "锅底" });
    const r = await fetch("/api/ma-fan" + (want ? "?date=" + encodeURIComponent(want) : ""), {
      credentials: "same-origin",
    });
    status = r.status;
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      throw new Error(typeof d.detail === "string" ? d.detail : `接口返回失败（HTTP ${status}）`);
    }
    potLoadedAt = Date.now();
    potViewDate = d.view_date || want || "";
    lastPot = d;
    paintPot(d);
    setModStamp("potStamp", (d.scan && d.scan.saved_at) || null, { label: "锅底", title: "夜间日线扫描" });
  } catch (e) {
    const msg = (e && e.message) || String(e);
    const body = document.getElementById("potBody");
    if (body) body.innerHTML = `<tr><td colspan="14" class="meta mafan-err">锅底加载失败：${escAttr(msg)}</td></tr>`;
    setModStamp("potStamp", null, { stale: true, label: "锅底" });
    reportClientError("pot-load", msg, status, "");
  }
}

function shiftPotDay(delta) {
  const dates = lastPot.dates || [];
  if (!dates.length) return;
  let idx = dates.indexOf(potViewDate || dates[0]);
  if (idx < 0) idx = 0;
  const next = dates[idx + delta];
  if (next) loadPot(true, next);
}

function paintPotDayBar(d) {
  const dates = (d && d.dates) || [];
  const day = (d && d.view_date) || ((d && d.scan) || {}).trade_date || "";
  const input = document.getElementById("potDayInput");
  if (input && day) input.value = day;
  const chips = document.getElementById("potDayChips");
  if (chips) {
    chips.innerHTML = dates.slice(0, 12).map((x) =>
      `<button type="button" class="rev-col-btn${x === day ? " on" : ""}" data-pot-day="${x}">${String(x).slice(5)}</button>`
    ).join("") || `<span class="meta">尚无扫描日（随均线发散 18/20/22 点扫描）</span>`;
  }
}

function loadPotFilters() {
  try {
    const o = JSON.parse(localStorage.getItem(POT_FILTER_KEY) || "null");
    if (o && typeof o === "object") potFilters = { focus: !!o.focus, early: !!o.early };
  } catch (e) {}
}

function savePotFilters() {
  try {
    localStorage.setItem(POT_FILTER_KEY, JSON.stringify(potFilters));
  } catch (e) {}
}

function potSortValue(it, id) {
  if (id === "code") return String(it.code || "");
  if (id === "sector") return String(it.industry || "");
  if (id === "stage") return it.stage === "初期" ? 1 : 2;
  if (id === "d1" || id === "d3") {
    const f = it.fwd || {};
    return f[id] == null ? NaN : Number(f[id]);
  }
  const num = {
    base: "base_len", shrink: "vol_shrink", drop: "drop_pct", lift: "lift_vol",
    rise: "rise_pct", close: "close", pct: "pct", mv: "mv_yi",
  }[id];
  return num && it[num] != null ? Number(it[num]) : NaN;
}

function potFilteredSorted(items) {
  const rows = (items || []).filter((it) =>
    (!potFilters.focus || it.focus) && (!potFilters.early || it.stage === "初期"));
  const id = potSort.id;
  if (!id) return rows;
  const dir = potSort.dir || 1;
  return rows.slice().sort((a, b) => {
    const va = potSortValue(a, id);
    const vb = potSortValue(b, id);
    if (typeof va === "number" && typeof vb === "number") {
      if (Number.isNaN(va) && Number.isNaN(vb)) return 0;
      if (Number.isNaN(va)) return 1;
      if (Number.isNaN(vb)) return -1;
      return va === vb ? 0 : (va < vb ? -dir : dir);
    }
    const sa = String(va ?? "");
    const sb = String(vb ?? "");
    return sa === sb ? 0 : (sa < sb ? -dir : dir);
  });
}

function paintPotHead() {
  const head = document.getElementById("potHead");
  if (!head) return;
  const cols = [
    ["code", "股票"],
    ["sector", "板块"],
    ["stage", "阶段", "初期：收盘离平台中枢 ≤15%"],
    ["base", "平台", "横盘天数；悬停看起止日期与收盘振幅"],
    ["shrink", "缩量", "平台成交量中位数 / 平台前 40 日中位数，越小越缩"],
    ["drop", "前跌%", "平台中枢离平台前 60 日最高价的跌幅"],
    ["lift", "放量×", "出平台以来最大成交量 / 平台成交量中位数"],
    ["rise", "离平台%", "收盘离平台中枢的涨幅"],
    ["close", "收盘"],
    ["pct", "涨跌%"],
    ["d1", "次日%"],
    ["d3", "三日%"],
    ["mv", "市值"],
    ["", "备注"],
  ];
  head.innerHTML = cols.map(([id, label, tip]) => {
    if (!id) return `<th>${label}</th>`;
    const ind = potSort.id === id ? (potSort.dir > 0 ? "▲" : "▼") : "";
    const title = tip ? ` title="${escAttr(tip)}"` : "";
    return `<th class="rev-sort" data-sort="${id}"${title}>${label}<span class="sort-ind">${ind}</span></th>`;
  }).join("");
}

/** Recent forward-outcome line for pot rows; same index and windows as the MA-fan line. */
function potOutcomeHtml(oc) {
  if (!oc) return "";
  const fmt = (a) => (a && a.n ? `胜 ${a.win}%（均 ${a.avg > 0 ? "+" : ""}${a.avg}%，${a.n} 只）` : "—");
  const line = (lab, o) => {
    const rec = (o && o.recent) || {};
    if (!rec.days) return `${lab}：还没有三日结果`;
    return `${lab}：近 ${rec.days} 个扫描日三日 ${fmt(rec.d3)}`
      + (rec.excess_d3 != null ? ` · 比创业板指 ${rec.excess_d3 > 0 ? "+" : ""}${rec.excess_d3}%` : "");
  };
  return `<div class="meta mafan-outcome">${escAttr(line("全部", oc.all))}<br>${escAttr(line("浅跌放量", oc.focus))}</div>`;
}

function potRemark(it) {
  const parts = [];
  if (it.in_fan) parts.push(`<span class="rev-chip up" title="同日也在均线发散名单">同时发散</span>`);
  if (it.focus) parts.push(`<span class="rev-chip" title="前跌 15–25% 且放量 ≥2×：发现期最好，样本外与全体无差别">浅跌放量</span>`);
  const txt = [];
  if (it.days_since_base) txt.push(`出平台第 ${it.days_since_base} 日`);
  if (it.zt_ytd) txt.push(`年内涨停 ${it.zt_ytd}`);
  if (it.amount_yi != null) {
    txt.push(`额 ${Number(it.amount_yi).toFixed(1)}亿` + (it.amount_rank ? `（第 ${it.amount_rank}）` : ""));
  }
  return parts.join("") + (txt.length ? `<span class="meta">${escAttr(txt.join("；"))}</span>` : "");
}

function paintPot(d) {
  const scan = (d && d.scan) || {};
  const sum = document.getElementById("potSummary");
  const body = document.getElementById("potBody");
  if (!body) return;
  paintPotDayBar(d);
  const ff = document.getElementById("potFltFocus");
  const fe = document.getElementById("potFltEarly");
  if (ff) ff.checked = potFilters.focus;
  if (fe) fe.checked = potFilters.early;
  paintPotHead();
  const has = Array.isArray(scan.pot);
  const all = has ? scan.pot : [];
  if (sum) {
    sum.innerHTML = !scan.trade_date
      ? `<span class="meta">暂无落盘结果</span>`
      : !has
      ? `<span class="meta">该日扫描早于锅底上线，没有锅底结果（下一次夜间扫描或在「发散」页重扫后才有）</span>`
      : `<b>${escAttr(scan.trade_date || "")}</b><span class="meta"> · 锅底 ${all.length}`
        + ` · 浅跌放量 ${all.filter((x) => x.focus).length}`
        + ` · 同时发散 ${all.filter((x) => x.in_fan).length}</span>`
        + potOutcomeHtml(d.pot_outcome);
  }
  const rows = potFilteredSorted(all);
  const cnt = document.getElementById("potFltCount");
  if (cnt) cnt.textContent = all.length ? `显示 ${rows.length} / ${all.length}` : "";
  if (!all.length) {
    body.innerHTML = `<tr><td colspan="14" class="meta">${has ? "该日无锅底命中" : "—"}</td></tr>`;
    return;
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="14" class="meta">无匹配行（放宽筛选条件）</td></tr>`;
    return;
  }
  const signed = (v, digits) => (v == null ? "—" : (Number(v) > 0 ? "+" : "") + Number(v).toFixed(digits));
  body.innerHTML = rows.map((it) => {
    const code = it.code || "";
    const baseTip = `${String(it.base_start || "").slice(5)} → ${String(it.base_end || "").slice(5)}，`
      + `收盘振幅 ${it.base_range ?? "—"}%，日涨跌中位 ${it.base_body ?? "—"}%`;
    const stageCls = it.stage === "初期" ? "rev-chip up" : "rev-chip gate soft";
    return `<tr>
          <td>${tickerHtml(it.name || code, code)}</td>
          <td class="col-wrap col-sector">${maFanSectorHtml(it)}</td>
          <td><span class="${stageCls}">${escAttr(it.stage || "—")}</span></td>
          <td title="${escAttr(baseTip)}">${it.base_len ?? "—"} 日</td>
          <td>${it.vol_shrink ?? "—"}</td>
          <td>${it.drop_pct != null ? Number(it.drop_pct).toFixed(0) : "—"}</td>
          <td>${it.lift_vol != null ? Number(it.lift_vol).toFixed(1) : "—"}</td>
          <td>${signed(it.rise_pct, 1)}</td>
          <td>${it.close ?? "—"}</td>
          <td class="${Number(it.pct) > 0 ? "up" : (Number(it.pct) < 0 ? "down" : "")}">${signed(it.pct, 1)}</td>
          ${maFanFwdCell(it.fwd, "d1")}
          ${maFanFwdCell(it.fwd, "d3")}
          <td>${it.mv_yi != null ? fmtMaFanMv(it.mv_yi) : "—"}</td>
          <td class="col-wrap col-note">${potRemark(it)}</td>
        </tr>`;
  }).join("");
}

loadPotFilters();
["potFltFocus", "potFltEarly"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", () => {
    potFilters = {
      focus: !!document.getElementById("potFltFocus")?.checked,
      early: !!document.getElementById("potFltEarly")?.checked,
    };
    savePotFilters();
    paintPot(lastPot);
  });
});
document.getElementById("potDayPrev")?.addEventListener("click", () => shiftPotDay(1));
document.getElementById("potDayNext")?.addEventListener("click", () => shiftPotDay(-1));
document.getElementById("potDayLatest")?.addEventListener("click", () => {
  potViewDate = "";
  loadPot(true, "");
});
document.getElementById("potDayInput")?.addEventListener("change", (ev) => {
  if (ev.target.value) loadPot(true, ev.target.value);
});
document.getElementById("potDayChips")?.addEventListener("click", (ev) => {
  const btn = ev.target.closest && ev.target.closest("[data-pot-day]");
  if (btn) loadPot(true, btn.getAttribute("data-pot-day"));
});
document.addEventListener("click", (ev) => {
  const link = ev.target.closest && ev.target.closest("[data-goto-pot]");
  if (!link) return;
  ev.preventDefault();
  potViewDate = link.getAttribute("data-goto-pot") || "";
  potLoadedAt = 0;
  applyMain("pot");
});
document.getElementById("potHead")?.addEventListener("click", (ev) => {
  const th = ev.target && ev.target.closest ? ev.target.closest("th.rev-sort") : null;
  const col = th && th.getAttribute("data-sort");
  if (!col) return;
  if (potSort.id === col) potSort.dir = -potSort.dir;
  else potSort = { id: col, dir: -1 };
  paintPot(lastPot);
});
