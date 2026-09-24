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
  { id: "result", label: "结果" },
  { id: "ops", label: "操作" },
];
const REV_COL_DEFAULT = REV_COL_CATALOG.map((c) => ({ id: c.id, on: true }));
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
    for (const c of REV_COL_CATALOG) {
      if (!seen.has(c.id)) out.push({ id: c.id, on: true });
    }
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
  if (id === "result") return String(r.outcome_label || "");
  if (id === "vs_ml") return String(r.vs_mainline || "");
  if (id === "phase") return String(r.phase || "");
  if (id === "mainline") return String(r.mainline || "");
  if (id === "board") return String(r.board_text || ((r.boards || []).join("/")) || "");
  if (id === "holder_n") return r.holder_num == null ? null : Number(r.holder_num);
  if (id === "holder_chg") return r.holder_chg_pct == null ? null : Number(r.holder_chg_pct);
  if (id === "holder_avg") return r.holder_avg_wan == null ? null : Number(r.holder_avg_wan);
  if (id === "zt_ytd") return r.zt_ytd == null ? null : Number(r.zt_ytd);
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
function closeOpenFillEditors() {
  document.querySelectorAll("#revBody .rev-fill-ed").forEach((ed) => {
    const cell = ed.closest("td");
    if (cell && cell.dataset.prevHtml != null) {
      cell.innerHTML = cell.dataset.prevHtml;
      delete cell.dataset.prevHtml;
    }
  });
}
function openFillEditor(btn) {
  const cell = btn.closest("td");
  if (!cell) return;
  closeOpenFillEditors();
  const id = btn.getAttribute("data-id") || "";
  const code = btn.getAttribute("data-code") || "";
  const name = btn.getAttribute("data-name") || code;
  const typ = btn.getAttribute("data-type") || "buy";
  const px0 = btn.getAttribute("data-price") || "";
  const suggest0 = btn.getAttribute("data-suggest") || "";
  const qty0 = btn.getAttribute("data-qty") || "100";
  const hasFill = btn.textContent.trim() === "改";
  const hintBits = [
    name ? `${name} ${code}` : code,
    suggest0 ? `建议价 ${suggest0}` : "",
    hasFill ? "改正后可勾选同步仓位" : "填写后可勾选写入/同步仓位",
  ].filter(Boolean);
  cell.dataset.prevHtml = cell.innerHTML;
  cell.innerHTML = `
        <div class="rev-fill-ed" data-id="${escAttr(id)}" data-type="${escAttr(typ)}">
          <p class="hint">${hintBits.map(escAttr).join(" · ")}</p>
          <label class="num">价<input type="number" class="rev-fill-px" step="0.001" min="0.001" value="${escAttr(px0)}" /></label>
          <label class="num">量<input type="number" class="rev-fill-qty" step="1" min="1" value="${escAttr(qty0)}" /></label>
          <label class="sync" title="买：冲正成本/股数；卖：冲正减仓"><input type="checkbox" class="rev-fill-sync" checked />同步仓位</label>
          <button type="button" class="rev-col-btn primary rev-fill-save">保存</button>
          <button type="button" class="rev-col-btn rev-fill-cancel">取消</button>
        </div>`;
  const pxInput = cell.querySelector(".rev-fill-px");
  if (pxInput) {
    pxInput.focus();
    pxInput.select();
  }
  const ed = cell.querySelector(".rev-fill-ed");
  if (ed) {
    ed.addEventListener("keydown", (kev) => {
      if (kev.key === "Enter") {
        kev.preventDefault();
        saveFillEditor(ed);
      } else if (kev.key === "Escape") {
        kev.preventDefault();
        if (cell.dataset.prevHtml != null) {
          cell.innerHTML = cell.dataset.prevHtml;
          delete cell.dataset.prevHtml;
        }
      }
    });
  }
}
async function saveFillEditor(ed) {
  const id = ed.getAttribute("data-id");
  if (!id) return;
  const typ = ed.getAttribute("data-type") || "buy";
  const pxEl = ed.querySelector(".rev-fill-px");
  const qtyEl = ed.querySelector(".rev-fill-qty");
  const syncEl = ed.querySelector(".rev-fill-sync");
  const fillPx = Number(pxEl && pxEl.value);
  const fillQty = Number(qtyEl && qtyEl.value);
  if (!(fillPx > 0) || !(fillQty > 0)) {
    alert("请填写有效成交价和数量");
    return;
  }
  const syncBook = !!(syncEl && syncEl.checked);
  const saveBtn = ed.querySelector(".rev-fill-save");
  if (saveBtn) saveBtn.disabled = true;
  try {
    const r = await fetch("/api/review/" + id, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        traded: true,
        skipped: false,
        fill_price: fillPx,
        fill_qty: fillQty,
        sync_book: syncBook,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(typeof d.detail === "string" ? d.detail : "改成交失败");
      if (saveBtn) saveBtn.disabled = false;
      return;
    }
    if (syncBook && d.book_summary && d.book_summary.message) {
      alert(d.book_summary.message);
    } else if (syncBook) {
      alert(
        String(typ).indexOf("sell") >= 0
          ? "已改成交并同步仓位卖出"
          : "已改成交并同步仓位"
      );
    }
  } catch (e) {
    alert("改成交失败");
    if (saveBtn) saveBtn.disabled = false;
    return;
  }
  reviewLoadedAt = 0;
  await loadReview(true);
  await tick();
}
function revCellHtml(col, r, ctx) {
  if (col === "date") return revSignalTime(r);
  if (col === "type") {
    if (ctx.typ === "卖") {
      return `<span class="rev-type-sell">卖</span>`;
    }
    const srcMeta = revSrcTagMeta(revNormSrc(r));
    if (srcMeta && srcMeta.typ) {
      return `<span class="${srcMeta.typ}">${ctx.typ}</span>`;
    }
    return ctx.typ;
  }
  if (col === "name") {
    const code = r.code || "";
    const histBtn = code
      ? ` <button type="button" class="rev-col-btn rev-hist-btn" data-code="${escAttr(code)}" data-name="${escAttr(r.name || code)}" title="查看该代码历史信号">历史</button>`
      : "";
    return `${tickerHtml(r.name, r.code, "", { signal_at: r.signaled_at || "", rev_id: r.id })}`
      + revTrendChips(r)
      + histBtn
      + `${ctx.liveHtml}${ctx.markHtml}${ctx.cautionHtml}`;
  }
  if (col === "code") return r.code || "";
  if (col === "price") return `${r.price ?? "—"}${ctx.devHtml}`;
  if (col === "fill") {
    const fp = r.fill_price;
    const fq = r.fill_qty;
    const defPx = revTradeDefaultPx(r);
    const typ = r.signal_type || "buy";
    const code = r.code || "";
    const name = r.name || code;
    if (fp == null && fq == null) {
      return `<button type="button" class="rev-col-btn rev-fill-edit" data-id="${r.id}" data-type="${escAttr(typ)}" data-code="${escAttr(code)}" data-name="${escAttr(name)}" data-price="${escAttr(defPx)}" data-suggest="${escAttr(r.price ?? "")}" data-qty="${escAttr(revTradeDefaultQty(r) || 100)}">填成交</button>`;
    }
    return `<div class="rev-fill"><b>${fp ?? "—"}</b> × ${fq ?? "—"}
          <button type="button" class="rev-col-btn rev-fill-edit" data-id="${r.id}" data-type="${escAttr(typ)}" data-code="${escAttr(code)}" data-name="${escAttr(name)}" data-price="${escAttr(fp ?? "")}" data-suggest="${escAttr(r.price ?? "")}" data-qty="${escAttr(fq || 100)}" style="margin-left:4px;padding:1px 6px">改</button></div>`;
  }
  if (col === "chase") {
    const chase = r.chase_price != null ? r.chase_price
      : (r.payload && r.payload.chase_price != null ? r.payload.chase_price : null);
    return `${chase == null ? "—" : chase}${ctx.chaseDevHtml}`;
  }
  if (col === "mainline") return r.mainline || "—";
  if (col === "board") {
    const boards = (r.boards && r.boards.length)
      ? r.boards
      : String(r.board_text || "—").split(/\s*\/\s*/).filter(Boolean);
    if (!boards.length) return "—";
    return `<div class="board-lines">${boards.map((b) => `<span>${b}</span>`).join("")}</div>`;
  }
  if (col === "vs_ml") {
    const tag = r.vs_mainline || "—";
    const match = r.board_match;
    const cls = match === true ? "up" : (match === false ? "down" : "meta");
    const bits = [];
    const role = r.vs_compare_role || r.desk_source || "";
    const roleLab = role === "side" ? "支线" : (role === "link" ? "联动" : (role === "watch_trial" ? "自选试探" : "主线"));
    if (r.vs_mainline_of) bits.push(`对照${roleLab}「${r.vs_mainline_of}」`);
    if (r.vs_sticky_of && r.vs_sticky_of !== r.vs_mainline_of) {
      bits.push(`盘面主线「${r.vs_sticky_of}」${r.vs_sticky ? "·" + r.vs_sticky : ""}`);
    } else if (!r.vs_mainline_of && r.vs_sticky_of) {
      bits.push(`相对盘面主线「${r.vs_sticky_of}」`);
    }
    const of = bits.join("；") || "相对对照主线";
    return `<span class="${cls}" title="${of}">${tag}</span>`;
  }
  if (col === "phase") return r.phase || "—";
  if (col === "holder_n") {
    const tip = [
      r.holder_end ? `统计截止 ${r.holder_end}` : "",
      r.holder_notice ? `公告 ${r.holder_notice}` : "",
      r.holder_prev != null ? `上次 ${fmtHolderNum(r.holder_prev)}` : "",
    ].filter(Boolean).join(" · ");
    return `<span title="${tip || "季度披露，非实时"}">${fmtHolderNum(r.holder_num)}</span>`;
  }
  if (col === "holder_chg") {
    const p = r.holder_chg_pct;
    if (p == null || Number.isNaN(Number(p))) return "—";
    const n = Number(p);
    const tip = r.holder_chg != null
      ? `增减 ${r.holder_chg > 0 ? "+" : ""}${fmtHolderNum(r.holder_chg)} 户`
      : "较上期";
    return `<span class="${n >= 0 ? "up" : "down"}" title="${tip}">${(n > 0 ? "+" : "") + n.toFixed(1)}%</span>`;
  }
  if (col === "holder_avg") {
    const tip = r.holder_end ? `统计截止 ${r.holder_end}` : "户均持股市值";
    return `<span title="${tip}">${fmtHolderAvgWan(r.holder_avg_wan)}</span>`;
  }
  if (col === "zt_ytd") {
    const n = r.zt_ytd;
    if (n == null || Number.isNaN(Number(n))) {
      if (reviewZtLoading && r.kind !== "etf") {
        return `<span class="meta" title="年内涨停加载中">…</span>`;
      }
      return "—";
    }
    const tip = r.zt_ytd_note
      || (r.zt_ytd_year ? `${r.zt_ytd_year}年日线涨停次数（未复权阈值）` : "年内日线涨停次数");
    return `<span title="${tip}">${Number(n)}</span>`;
  }
  if (col === "day1") {
    const d1 = r.outcome_day1_pct;
    return `<td class="col-day1 ${d1 == null ? "" : (d1 >= 0 ? "up" : "down")}">${d1 == null ? "—" : ((d1 > 0 ? "+" : "") + d1 + "%")}</td>`;
  }
  if (col === "day3") {
    const d3 = r.outcome_day3_pct;
    return `<td class="col-day3 ${d3 == null ? "" : (d3 >= 0 ? "up" : "down")}">${d3 == null ? "—" : ((d3 > 0 ? "+" : "") + d3 + "%")}</td>`;
  }
  if (col === "result") return `${ctx.label}${ctx.pend}`;
  if (col === "ops") {
    const t1 = !!ctx.t1Locked;
    const tradeBtn = t1
      ? `<button type="button" class="rev-trade" disabled title="当日买入，隔日才能卖（T+1）">T+1不可卖</button>`
      : `<button type="button" class="rev-trade${ctx.traded ? " on" : ""}" data-id="${r.id}" data-type="${r.signal_type || "buy"}" data-code="${r.code || ""}" data-name="${r.name || ""}" data-price="${revTradeDefaultPx(r)}" data-suggest="${r.price ?? ""}" data-qty="${revTradeDefaultQty(r)}">已交易</button>`;
    return `<div class="rev-actions">
          ${tradeBtn}
          <button type="button" class="rev-skip${ctx.skipped ? " on" : ""}" data-id="${r.id}">未交易</button>
          <button type="button" class="pos-del rev-del" data-id="${r.id}">删除</button>
        </div>`;
  }
  return "";
}

function paintReview(payload) {
  lastReview = payload || { signals: [], summary: {} };
  const sum = lastReview.summary || {};
  const today = sum.today || {};
  const viewDate = lastReview.view_date || sum.view_date || today.date || "";
  const calendarToday = lastReview.calendar_today || sum.calendar_today || "";
  reviewViewDate = viewDate;
  const isToday = viewDate && calendarToday && viewDate === calendarToday;
  const dayLabel = isToday ? "今日" : "当日";
  const dates = lastReview.dates || sum.dates || [];
  const dayInput = document.getElementById("revDayInput");
  if (dayInput && viewDate) dayInput.value = viewDate;
  const chips = document.getElementById("revDayChips");
  if (chips) {
    chips.innerHTML = dates.slice(0, 14).map((d) => {
      const short = String(d).slice(5);
      const cls = [
        "rev-day-chip",
        d === viewDate ? "on" : "",
        d === calendarToday ? "today-mark" : "",
      ].filter(Boolean).join(" ");
      return `<button type="button" class="${cls}" data-rev-day="${d}">${short}${d === calendarToday ? "·今" : ""}</button>`;
    }).join("");
  }
  const cell = (lab, val, cls) =>
    `<div class="rev-card"><div class="lab">${lab}</div><div class="val ${cls || ""}">${val ?? "—"}</div></div>`;
  const tcell = (lab, val, cls) =>
    `<div><div class="lab">${lab}</div><div class="val ${cls || ""}">${val ?? "—"}</div></div>`;
  document.getElementById("revToday").innerHTML = [
    `<div class="hd">${dayLabel}摘要 · ${today.date || viewDate || "—"} · 相位 ${today.phase || "—"}</div>`,
    tcell("买入", today.buy_n ?? 0),
    tcell("卖出", today.sell_n ?? 0),
    tcell("未回踩上行", today.miss_pullback_n ?? 0, "mark-miss"),
    tcell("价带内", today.in_band_n ?? 0, "up"),
    tcell("已交易/未交易", `${today.traded_n ?? 0} / ${today.skipped_n ?? 0}`),
    tcell("主线切换", today.switch_n ?? 0),
  ].join("");
  const ex = sum.exec || today.exec || {};
  const by = ex.by_kind || {};
  const etfEx = by.etf || {};
  const stkEx = by.stock || {};
  document.getElementById("revExec").innerHTML =
    `<div class="hd">${dayLabel}计划执行分<button type="button" class="q" data-term="计划执行分">?</button></div>`
    + `执行分 <b>${ex.score == null ? "—" : ex.score}</b> · 已成交买 ${ex.traded_buy_n ?? 0}`
    + (ex.diary_n ? `（含日记 ${ex.diary_n}）` : "")
    + (ex.unplanned_n ? ` · 无计划 ${ex.unplanned_n}` : "")
    + ` · 价带内 ${ex.in_band_n ?? 0} · 追高 ${ex.chase_n ?? 0}`
    + ` · 更低更好 ${ex.below_n ?? 0} · 其他 ${ex.other_n ?? 0}`
    + `<div class="meta" style="margin-top:4px">ETF ${etfEx.score == null ? "—" : etfEx.score}（${etfEx.traded_buy_n ?? 0}）`
    + ` · 个股 ${stkEx.score == null ? "—" : stkEx.score}（${stkEx.traded_buy_n ?? 0}）</div>`
    + (() => {
      const se = sum.sell_exec || {};
      if (se.traded_sell_n == null && se.score == null) return "";
      return `<div class="meta" style="margin-top:6px">卖侧执行 <b>${se.score == null ? "—" : se.score}</b>`
        + ` · 已成交卖 ${se.traded_sell_n ?? 0}`
        + (se.diary_n ? `（含日记 ${se.diary_n}）` : "")
        + ` · 贴计划 ${se.in_band_n ?? 0} · 偏晚 ${se.late_n ?? 0}`
        + ` · 偏早 ${se.early_n ?? 0}</div>`;
    })();
  const missed = sum.missed_buys || [];
  const missAttr = sum.miss_attr || {};
  const missCounts = missAttr.counts || {};
  const missItems = (missAttr.items && missAttr.items.length) ? missAttr.items : missed;
  const missAttrHtml = (missAttr.total > 0)
    ? (`<div class="miss-attr">`
      + `<span class="chip nt">未触达就走 ${missCounts.never_touched || 0}</span>`
      + `<span class="chip tb">触达未买 ${missCounts.touched_not_bought || 0}</span>`
      + `<span class="chip gb">闸门卡死 ${missCounts.gate_blocked || 0}</span>`
      + `</div>`)
    : "";
  const missKindCls = (k) => ({ never_touched: "nt", touched_not_bought: "tb", gate_blocked: "gb" }[k] || "");
  document.getElementById("revMissed").innerHTML = missItems.length
    ? (`<div class="hd">漏买归因<button type="button" class="q" data-term="漏买归因">?</button> · ${missItems.length}</div>`
      + missAttrHtml
      + `<ul style="margin:0;padding-left:18px">` + missItems.map((m) =>
        `<li>`
        + (m.miss_kind_label
          ? `<span class="miss-kind ${missKindCls(m.miss_kind)}">${m.miss_kind_label}</span>`
          : "")
        + `${tickerHtml(m.name, m.code, "", { signal_at: m.signaled_at || "" })}`
        + ` 建议 ${m.price ?? "—"} · 现 ${m.live_last ?? "—"}`
        + `${m.dev_pct == null ? "" : " · 偏离 " + ((m.dev_pct > 0 ? "+" : "") + Number(m.dev_pct).toFixed(2) + "%")}`
        + ` · ${m.skipped ? "已跳过" : "未点已交易"} · ${m.price_mark || "—"}</li>`
      ).join("") + `</ul>`)
    : `<div class="hd">漏买归因<button type="button" class="q" data-term="漏买归因">?</button></div>`
      + `<span class="meta">${dayLabel}暂无未交易漏买项</span>`;
  const weekBox = document.getElementById("revWeekExec");
  if (weekBox) {
    const we = sum.week_exec || {};
    const mc = we.miss_counts || {};
    const ms = we.miss_share || {};
    if (we.ok) {
      weekBox.innerHTML =
        `<div class="hd">近周归因 · 计划遵守<button type="button" class="q" data-term="近周归因">?</button></div>`
        + `<div class="chips">`
        + `<span class="chip">未触达 ${mc.never_touched || 0}${ms.never_touched != null ? `（${ms.never_touched}%）` : ""}</span>`
        + `<span class="chip">触达未买 ${mc.touched_not_bought || 0}${ms.touched_not_bought != null ? `（${ms.touched_not_bought}%）` : ""}</span>`
        + `<span class="chip">闸门 ${mc.gate_blocked || 0}${ms.gate_blocked != null ? `（${ms.gate_blocked}%）` : ""}</span>`
        + `<span class="chip">半仓窗口遵守 ${we.follow_rate == null ? "—" : (we.follow_rate + "%")}（${we.followed_n || 0}/${we.window_n || 0}）</span>`
        + `</div>`
        + `<div class="meta" style="margin-top:6px">${we.note || ""}</div>`;
    } else {
      weekBox.innerHTML = `<div class="hd">近周归因</div><span class="meta">${we.note || "暂无"}</span>`;
    }
  }
  paintEodBrief(sum, today, dayLabel);
  paintTomorrowBrief();
  const vsMode = sum.vs_mainline_mode || reviewVsMlMode || "live";
  reviewVsMlMode = vsMode;
  const vsLiveBtn = document.getElementById("vsMlLive");
  const vsDayBtn = document.getElementById("vsMlDay");
  if (vsLiveBtn) vsLiveBtn.classList.toggle("on", vsMode === "live");
  if (vsDayBtn) vsDayBtn.classList.toggle("on", vsMode === "day");
  const vsHint = document.getElementById("vsMlHint");
  if (vsHint) {
    const liveN = sum.vs_mainline_live || "—";
    const dayN = sum.vs_mainline_day || "—";
    const of = sum.vs_mainline_of || "—";
    vsHint.textContent = vsMode === "day"
      ? `当日主线「${dayN}」· 现对照「${of}」（实时=${liveN}）`
      : `实时主线「${liveN}」· 现对照「${of}」（当日=${dayN}）`;
  }
  const ph = sum.phase_hits || [];
  const kh = sum.kind_hits || [];
  const pkh = sum.phase_kind_hits || [];
  const dh = sum.desk_hits || [];
  const th = sum.theme_hits || [];
  const gates = sum.gate_kills || [];
  const sellBias = sum.sell_bias || {};
  const hints = sum.tune_hints || [];
  const rateCls = (r) => {
    const n = Number(r);
    if (Number.isNaN(n)) return "";
    if (n >= 55) return "hi";
    if (n >= 40) return "mid";
    return "lo";
  };
  const ocBox = document.getElementById("revOcCompare");
  if (ocBox) {
    const oc = sum.outcome_compare || {};
    const rows = oc.rows || [];
    if (rows.length) {
      ocBox.innerHTML =
        `<div class="hd">三标准对照<button type="button" class="q" data-term="三标准对照">?</button>`
        + ` · ${oc.hit_mode === "all" ? "全部已打分" : "仅已交易"}</div>`
        + `<div class="chips">` + rows.map((r) =>
          `<div class="hit-chip${r.low_n ? " low-n" : ""}" title="${r.low_n ? "样本不足 n<8" : ""}">`
          + `<div class="lab">${r.label || r.standard}</div>`
          + `<div class="rate ${rateCls(r.hit_rate)}">${r.hit_rate == null ? "—" : (r.hit_rate + "%")}</div>`
          + `<div class="n">样本 ${r.scored_n ?? 0}${r.low_n ? " · 低" : ""}</div></div>`
        ).join("") + `</div>`
        + `<div class="meta" style="margin-top:6px">${oc.note || ""}</div>`;
    } else {
      ocBox.innerHTML =
        `<div class="hd">三标准对照<button type="button" class="q" data-term="三标准对照">?</button></div>`
        + `<span class="meta">${oc.note || "有隔日打分后并排显示"}</span>`;
    }
  }
  const chip = (lab, rate, n, lowN) =>
    `<div class="hit-chip${lowN ? " low-n" : ""}" title="${lowN ? "样本不足 n<8，灰显参考" : ""}"><div class="lab">${lab}</div>`
    + `<div class="rate ${rateCls(rate)}">${rate == null ? "—" : (rate + "%")}</div>`
    + `<div class="n">样本 ${n ?? 0}${lowN ? " · 低" : ""}</div></div>`;
  const boardHtml = (() => {
    const chips = [];
    dh.forEach((k) => chips.push(chip(k.label || k.source, k.hit_rate, k.scored_n, k.low_n)));
    kh.forEach((k) => chips.push(chip(k.label || k.kind, k.hit_rate, k.scored_n, k.low_n)));
    th.slice(0, 6).forEach((k) => chips.push(chip(k.label || k.theme, k.hit_rate, k.scored_n, k.low_n)));
    ph.slice(0, 4).forEach((p) => chips.push(chip("相位·" + (p.phase || "未标"), p.hit_rate, p.scored_n, p.low_n)));
    if (!chips.length) return "";
    return `<div class="hit-board">${chips.join("")}</div>`;
  })();
  const kindLine = kh.length
    ? ("品种 " + kh.map((k) => `${k.label} ${k.hit_rate}%（${k.scored_n}）`).join(" · "))
    : "";
  const deskLine = (() => {
    if (!dh.length) return "";
    const main = dh.find((k) => k.source === "main");
    const rest = dh.filter((k) => k.source !== "main");
    const mainBit = main
      ? `主线 ${main.hit_rate}%（${main.scored_n}）`
      : "";
    const restBit = rest.length
      ? ("次要 " + rest.map((k) => `${k.label} ${k.hit_rate}%（${k.scored_n}）`).join(" / "))
      : "";
    return "来源 " + [mainBit, restBit].filter(Boolean).join(" · ");
  })();
  const themeLine = th.length
    ? ("题材 " + th.slice(0, 6).map((t) =>
        `${t.label || t.theme} ${t.hit_rate}%（${t.scored_n}）`
      ).join(" · "))
    : "";
  const crossLine = pkh.length
    ? ("交叉 " + pkh.slice(0, 8).map((p) =>
        `${p.phase}·${p.label} ${p.hit_rate}%（${p.scored_n}）`
      ).join(" · "))
    : "";
  const gateLine = gates.length
    ? ("闸门·终态假杀 " + gates.slice(0, 6).map((g) => {
        const fk = g.false_kill_n != null ? `假杀${g.false_kill_n}` : "";
        const note = g.note ? `·${g.note}` : "";
        return `${g.gate}×${g.kill_n}${fk ? `(${fk})` : ""}${note}`;
      }).join(" · "))
    : "";
  const sellParts = [];
  if (sellBias.etf && sellBias.etf.note) sellParts.push(`ETF ${sellBias.etf.note}`);
  if (sellBias.stock && sellBias.stock.note) sellParts.push(`个股 ${sellBias.stock.note}`);
  if (!sellParts.length && sellBias.note) sellParts.push(sellBias.note);
  else if (sellBias.all && sellBias.all.note && sellParts.length) {
    /* keep ETF/stock lines primary */
  }
  const sellLine = sellParts.length
    ? (`卖点闭环 ${sellParts.join(" · ")}`)
    : "";
  const wb = sum.whitebox || {};
  const wbLine = (wb.ok && (wb.weights || []).length)
    ? (`白盒 ${wb.note || ""} · `
      + (wb.weights || []).filter((w) => w.key !== "bias").slice(0, 5).map((w) => {
          const v = Number(w.weight) || 0;
          return `${w.label}${(v > 0 ? "+" : "")}${v.toFixed(2)}`;
        }).join(" / "))
    : (wb.note ? `白盒 ${wb.note}` : "");
  const hintHtml = hints.length
    ? (`<ul class="tune-hints">` + hints.map((h) => `<li>${h}</li>`).join("") + `</ul>`)
    : "";
  document.getElementById("revPhase").innerHTML = (boardHtml || ph.length || kh.length || dh.length || th.length || gates.length || sellLine || wbLine || hints.length)
    ? (`<div class="hd">命中率看板（跨日）<button type="button" class="q" data-term="命中率看板">?</button></div>`
      + (hintHtml ? `<div class="hd" style="margin-top:4px">调参建议</div>${hintHtml}` : "")
      + boardHtml
      + (ph.length
        ? `<div style="margin-top:6px">相位 ${ph.map((p) => `${p.phase || "未标"} ${p.hit_rate == null ? "—" : (p.hit_rate + "%")}（${p.scored_n}）`).join(" · ")}</div>`
        : "")
      + (kindLine ? `<div class="meta" style="margin-top:4px">${kindLine}</div>` : "")
      + (deskLine ? `<div class="meta" style="margin-top:4px">${deskLine}</div>` : "")
      + (themeLine ? `<div class="meta" style="margin-top:4px">${themeLine}</div>` : "")
      + (crossLine ? `<div class="meta" style="margin-top:4px">${crossLine}</div>` : "")
      + (gateLine ? `<div class="meta" style="margin-top:4px"><button type="button" class="q" data-term="闸门归因">?</button> ${gateLine}</div>` : "")
      + (sellLine ? `<div class="meta" style="margin-top:4px"><button type="button" class="q" data-term="卖点闭环">?</button> ${sellLine}</div>` : "")
      + (wbLine ? `<div class="meta" style="margin-top:4px"><button type="button" class="q" data-term="白盒特征权重">?</button> ${wbLine}</div>` : ""))
    : `<div class="hd">命中率看板（跨日）</div><span class="meta">有隔日打分后按来源 / 题材 / 相位汇总</span>`;
  const hist = sum.history || [];
  document.getElementById("revHist").innerHTML = hist.length
    ? `<div class="hd">近 ${hist.length} 日命中率（有打分日）</div>${hitRateSpark(hist, viewDate)}`
    : `<div class="hd">历史日摘要会在每日复盘后累计</div>`;
  const hitMode = sum.hit_rate_mode === "all" ? "全部已打分" : "仅已交易";
  const revHitSel = document.getElementById("revHitMode");
  if (revHitSel && sum.hit_rate_mode) revHitSel.value = sum.hit_rate_mode;
  const revOcSel = document.getElementById("revOcMode");
  const ocNow = payload.outcome_standard || sum.outcome_standard || reviewOcMode || "classic";
  reviewOcMode = ocNow;
  if (revOcSel) revOcSel.value = ocNow;
  document.getElementById("revSum").innerHTML = [
    cell("买入信号（近窗）", sum.buy_total ?? 0),
    cell(`买入命中率·${hitMode}`, sum.buy_hit_rate == null ? "—" : (sum.buy_hit_rate + "%"), "up"),
    cell("卖出命中率", sum.sell_hit_rate == null ? "—" : (sum.sell_hit_rate + "%"), "up"),
    cell("待隔日打分", sum.pending ?? 0),
  ].join("");
  const flyBox = document.getElementById("sellFlyBoard");
  if (flyBox) {
    const fly = sum.sell_fly_board || {};
    const recent = fly.recent_early || [];
    const bySrc = (fly.by_source || []).slice(0, 4);
    const chips = [
      ["卖后回落", fly.hit_n, fly.hit_rate],
      ["续涨/卖飞", fly.early_n, fly.early_rate],
      ["其中卖飞", fly.fly_n, fly.fly_rate],
    ].map(([lab, n, rate]) =>
      `<div class="fly-chip"><div class="lab">${lab}</div>`
      + `<div class="n">${n ?? 0}</div>`
      + `<div class="lab">${rate == null ? "—" : (rate + "%")}</div></div>`
    ).join("");
    const srcLine = bySrc.length
      ? (`来源 ` + bySrc.map((s) =>
          `${s.label} 回落${s.hit_rate == null ? "—" : s.hit_rate + "%"}/卖飞${s.fly_n || 0}（n=${s.n}）`
        ).join(" · "))
      : "";
    const recentHtml = recent.length
      ? (`<ul class="fly-recent">` + recent.map((r) => {
          const lab = r.outcome_label || "";
          const tagCls = lab === "卖后回落" ? "fly-tag hit" : "fly-tag";
          // Sell outcomes store "avoidance" sign (↑ after sell → negative).
          // Show price change after sell so 卖飞 reads as 次日涨 / 留桌上正数.
          let d1Bit = "";
          if (r.outcome_day1_pct != null && r.outcome_day1_pct !== "") {
            const pxChg = -Number(r.outcome_day1_pct);
            d1Bit = ` <span class="dim">· 次日${pxChg > 0 ? "+" : ""}${pxChg.toFixed(1)}%</span>`;
          }
          let maeBit = "";
          if (r.outcome_mae_pct != null && r.outcome_mae_pct !== "") {
            const mae = Number(r.outcome_mae_pct);
            const left = mae <= 0 ? Math.abs(mae) : 0;
            maeBit = left > 0
              ? ` <span class="dim">· 留桌上 +${left.toFixed(1)}%</span>`
              : ` <span class="dim">· 未留桌上</span>`;
          }
          return `<li><span class="${tagCls}">${lab}</span>${(r.trade_date || "").slice(5)} ${r.name || r.code} <span class="dim">${r.code || ""}</span>${d1Bit}${maeBit}</li>`;
        }).join("") + `</ul>`)
      : `<div class="meta">暂无卖飞/续涨样本</div>`;
    flyBox.innerHTML =
      `<div class="hd">卖飞 / 卖后回落看板<button type="button" class="q" data-term="卖飞看板">?</button></div>`
      + `<div class="fly-verdict"><b>${fly.verdict || "—"}</b> · <span class="meta">${fly.note || ""}</span></div>`
      + `<div class="fly-chips">${chips}</div>`
      + (srcLine ? `<div class="fly-src">${srcLine}</div>` : "")
      + recentHtml;
  }
  const type = (document.getElementById("revType") || {}).value || "";
  const kind = (document.getElementById("revKind") || {}).value || "";
  const srcFilter = (document.getElementById("revSource") || {}).value || "";
  const main = ((document.getElementById("revMain") || {}).value || "").trim();
  const hideWait = !!(document.getElementById("revHideWait") || {}).checked;
  const srcOf = (r) => revNormSrc(r);
  const isPaperWait = (r) => {
    const st = String(r.signal_type || "");
    if (!(st === "buy" || st.startsWith("buy_"))) return false;
    if (Number(r.ready || 0) || Number(r.traded || 0) || Number(r.skipped || 0)) return false;
    if (r.outcome_label) return false;
    const flags = r.price_flags || [];
    // Keep actionable / diagnostic rows visible.
    if (flags.some((f) => ["miss_pullback", "chase_hit", "stop_hit", "in_band", "near_wait"].includes(f))) {
      return false;
    }
    const near = !!(r.near_entry || (r.payload && r.payload.near_entry));
    const gated = (r.confirm_fail || (r.payload && r.payload.confirm_fail) || []).length > 0;
    const mark = String(r.price_mark || "");
    if (near || gated) return false;
    if (mark && !mark.includes("回踩") && mark !== "—") return false;
    // Pure 盯回踩 paper: waiting far from band, no gate fail, no live flag.
    return true;
  };
  const rows = applyRevSort((lastReview.signals || []).filter((r) => {
    if (type === "buy") {
      const st = String(r.signal_type || "");
      if (st === "sell" || !(st === "buy" || st.startsWith("buy_"))) return false;
    } else if (type && r.signal_type !== type) return false;
    if (kind && (r.kind || "") !== kind) return false;
    if (srcFilter && srcOf(r) !== srcFilter) return false;
    if (main && !(String(r.mainline || "").includes(main))) return false;
    if (hideWait && isPaperWait(r)) return false;
    return true;
  }));
  const hiddenWaitN = hideWait
    ? (lastReview.signals || []).filter((r) => {
        if (type === "buy") {
          const st = String(r.signal_type || "");
          if (!(st === "buy" || st.startsWith("buy_"))) return false;
        } else if (type === "sell") return false;
        else if (type && r.signal_type !== type) return false;
        if (kind && (r.kind || "") !== kind) return false;
        if (srcFilter && srcOf(r) !== srcFilter) return false;
        if (main && !(String(r.mainline || "").includes(main))) return false;
        return isPaperWait(r);
      }).length
    : 0;
  const hideLab = document.querySelector("#revHideWait")?.parentElement?.querySelector("span");
  if (hideLab) {
    hideLab.textContent = hiddenWaitN
      ? `隐藏纯盯回踩（已藏 ${hiddenWaitN}）`
      : "隐藏纯盯回踩";
  }
  const cols = visibleRevCols();
  document.getElementById("revHead").innerHTML = cols.map((id) => {
    if (id === "ops") return `<th class="col-${id}">${revColLabel(id)}</th>`;
    const on = revSort.id === id;
    const ind = on ? (revSort.dir > 0 ? "▲" : "▼") : "";
    return `<th class="col-${id} rev-sort" data-sort="${id}">${revColLabel(id)}`
      + (ind ? `<span class="sort-ind">${ind}</span>` : "")
      + `</th>`;
  }).join("");
  const colspan = Math.max(cols.length, 1);
  const buyTypeLabel = (st) => ({
    buy: "买",
    buy_side: "买·支线回踩",
    buy_link: "买·联动回踩",
    buy_trial: "买·自选",
    buy_indep: "买·独立人气",
    buy_dragon: "买·龙头",
    sell: "卖",
  })[st] || (st === "sell" ? "卖" : "买");
  document.getElementById("revBody").innerHTML = rows.map((r) => {
    const typ = buyTypeLabel(r.signal_type || "buy");
    const skipped = !!Number(r.skipped || 0);
    const traded = !!Number(r.traded || 0);
    let label = r.outcome_label || "待隔日";
    if (traded) label = "已交易" + (r.outcome_label ? ` · ${r.outcome_label}` : "");
    else if (skipped) label = "未交易";
    const pend = "";
    const flags = r.price_flags || [];
    let markCls = "";
    if (flags.includes("stop_hit")) markCls = "mark-stop";
    else if (flags.includes("chase_hit")) markCls = "mark-chase";
    else if (flags.includes("miss_pullback")) markCls = "mark-miss";
    else if (r.price_mark) markCls = "mark-ok";
    const markHtml = r.price_mark
      ? `<div class="${markCls}">${r.price_mark}</div>`
      : "";
    const cautionHtml = r.buy_caution
      ? `<div class="meta ${markCls}">${r.buy_caution}</div>`
      : "";
    const liveHtml = r.live_last != null
      ? `<div class="meta">现价 ${r.live_last}${liveArrowHtml(r)}${r.live_pct == null ? "" : " " + ((r.live_pct > 0 ? "+" : "") + Number(r.live_pct).toFixed(2) + "%")}</div>`
      : "";
    const dev = r.dev_pct;
    const devHtml = dev == null
      ? ""
      : `<div class="${dev >= 0 ? "dev-up" : "dev-down"}">偏离 ${(dev > 0 ? "+" : "") + Number(dev).toFixed(2)}%</div>`;
    const chaseDev = r.chase_dev_pct;
    const chaseDevHtml = chaseDev == null
      ? ""
      : `<div class="${chaseDev >= 0 ? "dev-up" : "dev-down"}">偏离 ${(chaseDev > 0 ? "+" : "") + Number(chaseDev).toFixed(2)}%</div>`;
    let t1Locked = false;
    if (r.signal_type === "sell") {
      const day = String(r.trade_date || (lastData && lastData.trade_date) || "").slice(0, 10);
      const code = String(r.code || "").replace(/\D/g, "").padStart(6, "0");
      const pos = ((lastData && lastData.positions) || []).find(
        (p) => String(p.code || "").replace(/\D/g, "").padStart(6, "0") === code
      );
      const buyDay = String((pos && (pos.last_buy_date || pos.created_at)) || "").slice(0, 10);
      if (day && buyDay && day === buyDay) t1Locked = true;
      const sameBuy = (lastReview.signals || []).some((s) =>
        (s.signal_type === "buy" || String(s.signal_type || "").startsWith("buy_"))
        && String(s.trade_date || "").slice(0, 10) === day
        && String(s.code || "").replace(/\D/g, "").padStart(6, "0") === code
        && Number(s.traded || 0)
      );
      if (sameBuy) t1Locked = true;
    }
    const ctx = { typ, skipped, traded, label, pend, markHtml, cautionHtml, liveHtml, devHtml, chaseDevHtml, t1Locked };
    const tds = cols.map((id) => {
      const html = revCellHtml(id, r, ctx);
      if (id === "day1" || id === "day3") return html;
      return `<td class="col-${id}">${html}</td>`;
    }).join("");
    return `<tr class="${r.signal_type === "sell" ? "rev-sell" : ""}">${tds}</tr>`;
  }).join("") || `<tr><td colspan="${colspan}" class="meta">${viewDate || "该日"}暂无信号。盘中出现「可买入」或仓位「建议卖」后会自动记录。</td></tr>`;
}

let lastReview = { signals: [], summary: {} };
let reviewViewDate = "";
let reviewVsMlMode = "live";
let reviewOcMode = "classic";
window.__eodMarkdown = "";
window.__tomorrowMarkdown = "";

async function paintTomorrowBrief() {
  const focusEl = document.getElementById("tomorrowFocus");
  const listEl = document.getElementById("tomorrowBullets");
  if (!focusEl || !listEl) return;
  const day = reviewViewDate || ((lastReview.summary || {}).view_date) || "";
  const q = day ? (`?date=${encodeURIComponent(day)}`) : "";
  try {
    const r = await fetch("/api/report/tomorrow" + q);
    const d = await r.json();
    const brief = (d && d.brief) || {};
    focusEl.textContent = brief.focus || "收盘后自动生成次日观察清单";
    const bullets = brief.bullets || [];
    listEl.innerHTML = bullets.length
      ? bullets.map((b) => `<li>${escAttr(String(b))}</li>`).join("")
      : `<li class="meta">暂无要点</li>`;
    window.__tomorrowMarkdown = brief.markdown || (d && d.markdown) || "";
  } catch (e) {
    focusEl.textContent = "明日看点加载失败";
    listEl.innerHTML = `<li class="meta">${escAttr(String(e && e.message || e))}</li>`;
  }
}

async function paintEodBrief(_sum, _today, _dayLabel) {
  const focusEl = document.getElementById("eodFocus");
  const listEl = document.getElementById("eodBullets");
  if (!focusEl || !listEl) return;
  const day = reviewViewDate || ((lastReview.summary || {}).view_date) || "";
  const q = day ? (`?date=${encodeURIComponent(day)}`) : "";
  try {
    const r = await fetch("/api/report/eod" + q);
    const d = await r.json();
    const brief = (d && d.brief) || {};
    focusEl.textContent = brief.focus || "收盘对照：盈亏/持仓与明日看点一张纸看完即可。";
    const bullets = brief.bullets || [];
    listEl.innerHTML = bullets.length
      ? bullets.map((b) => `<li>${escAttr(String(b))}</li>`).join("")
      : `<li class="meta">暂无要点</li>`;
    window.__eodMarkdown = brief.markdown || (d && d.markdown) || "";
  } catch (e) {
    focusEl.textContent = "收盘一页纸加载失败";
    listEl.innerHTML = `<li class="meta">${escAttr(String(e && e.message || e))}</li>`;
  }
}

function isTodayView() {
  const sum = (lastReview && lastReview.summary) || {};
  const view = reviewViewDate || sum.view_date || "";
  const cal = sum.calendar_today || "";
  return !!(view && cal && view === cal);
}

function applyReviewTrendsByCode(by) {
  const src = by || {};
  for (const row of (lastReview.signals || [])) {
    const code = String(row.code || "").padStart(6, "0");
    const hit = src[code] || src[String(row.code || "")];
    if (!hit) {
      row.daily_trend = "";
      row.trend_ok = false;
      row.trend_down = false;
      row.trend_pending = true;
      continue;
    }
    row.daily_trend = hit.label || hit.trend || "";
    row.trend_ok = !!(hit.up || hit.trend_ok);
    row.trend_down = !!(hit.down || hit.trend_down);
    row.trend_pending = hit.quality === "fetch_fail" || hit.quality === "thin" || !!hit.trend_pending;
    if (hit.ma5 != null) row.ma5 = hit.ma5;
    if (hit.ma20 != null) row.ma20 = hit.ma20;
  }
  if (lastReview) lastReview.trends_ready = true;
}

async function loadReviewTrends(date, seq, wantFp) {
  const want = String(date || "").trim();
  if (!want) return;
  if (wantFp && reviewTrendFp === wantFp && reviewTrendBy) {
    applyReviewTrendsByCode(reviewTrendBy);
    setModStamp("revTrendStamp", reviewTrendRefreshedAt || "缓存", {
      label: "日线趋势",
      title: "日线趋势命中本地缓存（同日且信号集未变）",
    });
    paintReview(lastReview);
    return;
  }
  setModStamp("revTrendStamp", null, { loading: true, label: "日线趋势" });
  try {
    const r = await fetch("/api/review/trends?date=" + encodeURIComponent(want));
    const d = await r.json();
    if (seq !== reviewTrendSeq) return;
    const view = lastReview.view_date
      || (lastReview.summary || {}).view_date
      || reviewViewDate
      || "";
    if (view && d.trade_date && view !== d.trade_date) return;
    reviewTrendBy = d.by_code || {};
    reviewTrendFp = d.fingerprint || wantFp || "";
    applyReviewTrendsByCode(reviewTrendBy);
    reviewTrendRefreshedAt = d.refreshed_at || new Date().toTimeString().slice(0, 8);
    setModStamp("revTrendStamp", reviewTrendRefreshedAt, {
      label: "日线趋势",
      title: d.cache_hit ? "日线趋势命中服务端缓存" : "日线趋势已补齐",
    });
    paintReview(lastReview);
  } catch (e) {
    if (seq !== reviewTrendSeq) return;
    setModStamp("revTrendStamp", reviewTrendRefreshedAt || null, {
      label: "日线趋势",
      stale: true,
      title: "日线趋势补齐失败",
    });
    if (lastReview && (lastReview.signals || []).length) {
      paintReview(lastReview);
    }
  }
}

async function loadReviewZtYtd(date, seq) {
  const want = String(date || "").trim();
  if (!want) {
    if (seq === reviewZtSeq) reviewZtLoading = false;
    return;
  }
  setModStamp("revZtStamp", null, { loading: true, label: "年内涨停" });
  try {
    const r = await fetch("/api/review/zt-ytd?date=" + encodeURIComponent(want));
    const d = await r.json();
    if (seq !== reviewZtSeq) return;
    const view = lastReview.view_date
      || (lastReview.summary || {}).view_date
      || reviewViewDate
      || "";
    if (view && d.trade_date && view !== d.trade_date) return;
    const by = d.by_code || {};
    const rows = lastReview.signals || [];
    for (const row of rows) {
      if (row.kind === "etf") {
        row.zt_ytd = null;
        continue;
      }
      const code = String(row.code || "");
      const hit = by[code.padStart(6, "0")] || by[code];
      if (!hit) {
        row.zt_ytd = null;
        continue;
      }
      row.zt_ytd = hit.count;
      row.zt_ytd_year = hit.year;
      row.zt_ytd_note = hit.note;
    }
    reviewZtLoading = false;
    reviewZtRefreshedAt = d.refreshed_at || new Date().toTimeString().slice(0, 8);
    setModStamp("revZtStamp", reviewZtRefreshedAt, {
      label: "年内涨停",
      title: "年内涨停列异步补齐完成",
    });
    paintReview(lastReview);
  } catch (e) {
    if (seq !== reviewZtSeq) return;
    reviewZtLoading = false;
    setModStamp("revZtStamp", reviewZtRefreshedAt || null, {
      label: "年内涨停",
      stale: true,
      title: "年内涨停补齐失败",
    });
    if (lastReview && (lastReview.signals || []).length) {
      paintReview(lastReview);
    }
  }
}
