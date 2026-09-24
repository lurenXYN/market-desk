let maFanViewDate = "";
let maFanLoadedAt = 0;
let lastMaFan = { dates: [], scan: null };
const MAFAN_FILTER_KEY = "md_mafan_filters_v1";
let maFanSort = { id: "score", dir: -1 };
let maFanFilters = {
  stage: "",
  ma60: "",
  band: "",
  theme: "",
  review: false,
  progressive: false,
};

function loadMaFanFilters() {
  try {
    const raw = localStorage.getItem(MAFAN_FILTER_KEY);
    if (!raw) return;
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object") return;
    maFanFilters = {
      stage: String(o.stage || ""),
      ma60: String(o.ma60 || ""),
      band: String(o.band || ""),
      theme: String(o.theme || ""),
      review: !!o.review,
      progressive: !!o.progressive,
    };
  } catch (e) {}
}

function saveMaFanFilters() {
  try {
    localStorage.setItem(MAFAN_FILTER_KEY, JSON.stringify(maFanFilters));
  } catch (e) {}
}

function syncMaFanFilterInputs() {
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === "checkbox") el.checked = !!val;
    else el.value = val == null ? "" : String(val);
  };
  set("maFanFltStage", maFanFilters.stage);
  set("maFanFltMa60", maFanFilters.ma60);
  set("maFanFltBand", maFanFilters.band);
  set("maFanFltTheme", maFanFilters.theme);
  set("maFanFltReview", maFanFilters.review);
  set("maFanFltProgressive", maFanFilters.progressive);
}

function readMaFanFilterInputs() {
  const g = (id) => document.getElementById(id);
  maFanFilters = {
    stage: (g("maFanFltStage") && g("maFanFltStage").value) || "",
    ma60: (g("maFanFltMa60") && g("maFanFltMa60").value) || "",
    band: (g("maFanFltBand") && g("maFanFltBand").value) || "",
    theme: (g("maFanFltTheme") && g("maFanFltTheme").value) || "",
    review: !!(g("maFanFltReview") && g("maFanFltReview").checked),
    progressive: !!(g("maFanFltProgressive") && g("maFanFltProgressive").checked),
  };
  saveMaFanFilters();
}

function maFanRawTags(it) {
  if (Array.isArray(it.tags) && it.tags.length) return it.tags.map(String);
  return [it.stage, it.freshness].filter(Boolean).map(String);
}

function maFanPassFilter(it) {
  const f = maFanFilters;
  const tags = maFanRawTags(it);
  if (f.stage && String(it.stage || "") !== f.stage) return false;
  if (f.ma60 === "weak") {
    if (!tags.some((t) => t === "⚠MA60偏弱" || t === "MA60偏弱")) return false;
  } else if (f.ma60 === "ok") {
    if (tags.some((t) => t === "⚠MA60偏弱" || t === "MA60偏弱")) return false;
    if (!tags.some((t) => t === "MA60稳" || t === "MA60稳升")) return false;
  }
  if (f.band) {
    const band = String(it.amount_band || "");
    if (band !== f.band && !tags.includes(f.band)) return false;
  }
  const hasTheme = !!(it.theme_tag) || tags.some((t) =>
    t === "贴主线" || t === "近主线" || t === "主线同主题"
    || t === "贴支线" || t === "近支线" || t === "支线同主题"
    || t === "贴联动" || t === "近联动" || t === "联动同主题"
  );
  if (f.theme === "yes" && !hasTheme) return false;
  if (f.theme === "no" && hasTheme) return false;
  if (f.review && !it.in_review) return false;
  if (f.progressive) {
    if (!(it.progressive || tags.includes("渐进发散"))) return false;
  }
  return true;
}

function maFanSortValue(it, id) {
  if (id === "code") return String(it.code || "");
  if (id === "name") return String(it.name || "");
  if (id === "stage") {
    const m = { 初期: 1, 中期: 2, 后期: 3 };
    return m[it.stage] || 9;
  }
  if (id === "score") return Number(it.score);
  if (id === "close") return Number(it.close);
  if (id === "pct") return Number(it.pct);
  if (id === "sticky") return Number(it.sticky_spread);
  if (id === "fan") return Number(it.fan_spread);
  if (id === "vol") return Number(it.vol_ratio);
  if (id === "rank") return Number(it.amount_rank);
  if (id === "sector") return String(it.industry || "");
  if (id === "mv") return it.mv_yi == null ? NaN : Number(it.mv_yi);
  if (id === "zt") return it.zt_ytd == null ? NaN : Number(it.zt_ytd);
  if (id === "holder") return it.holder_num == null ? NaN : Number(it.holder_num);
  return 0;
}

function maFanFilteredSorted(items) {
  const rows = (items || []).filter(maFanPassFilter);
  const id = maFanSort.id;
  if (!id) return rows;
  const dir = maFanSort.dir || 1;
  return rows.slice().sort((a, b) => {
    const va = maFanSortValue(a, id);
    const vb = maFanSortValue(b, id);
    const na = typeof va === "number" && !Number.isNaN(va);
    const nb = typeof vb === "number" && !Number.isNaN(vb);
    if (na && nb) {
      if (va === vb) return 0;
      if (Number.isNaN(va)) return 1;
      if (Number.isNaN(vb)) return -1;
      return va < vb ? -dir : dir;
    }
    const sa = String(va ?? "");
    const sb = String(vb ?? "");
    if (sa === sb) return 0;
    return sa < sb ? -dir : dir;
  });
}

function paintMaFanHead() {
  const head = document.getElementById("maFanHead");
  if (!head) return;
  const cols = [
    ["code", "股票"],
    ["sector", "板块"],
    ["stage", "阶段"],
    ["score", "分"],
    ["close", "收盘"],
    ["pct", "涨跌%"],
    ["mv", "市值"],
    ["zt", "年内涨停"],
    ["holder", "股东户数"],
    ["sticky", "粘连%"],
    ["fan", "发散%"],
    ["vol", "量比"],
    ["", "标签"],
    ["", "备注"],
  ];
  head.innerHTML = cols.map(([id, label]) => {
    if (!id) return `<th>${label}</th>`;
    const on = maFanSort.id === id;
    const ind = on ? (maFanSort.dir > 0 ? "▲" : "▼") : "";
    return `<th class="rev-sort" data-sort="${id}">${label}<span class="sort-ind">${ind}</span></th>`;
  }).join("");
}

async function loadMaFan(force, date) {
  const now = Date.now();
  const want = (date !== undefined && date !== null)
    ? String(date).trim()
    : (maFanViewDate || "").trim();
  if (!force && date === undefined && now - maFanLoadedAt < 60000) return;
  let stage = "请求";
  let status = null;
  try {
    const params = new URLSearchParams();
    if (want) params.set("date", want);
    const q = params.toString() ? ("?" + params.toString()) : "";
    setModStamp("maFanStamp", null, { loading: true, label: "发散" });
    const r = await fetch("/api/ma-fan" + q, { credentials: "same-origin" });
    status = r.status;
    stage = "解析";
    const text = await r.text();
    let d;
    try {
      d = JSON.parse(text);
    } catch (_) {
      throw new Error(`返回非 JSON：${text.slice(0, 160) || "(空)"}`);
    }
    if (!r.ok || d.ok === false) {
      throw new Error(typeof d.detail === "string" ? d.detail : "接口返回失败");
    }
    maFanLoadedAt = Date.now();
    maFanViewDate = d.view_date || want || "";
    lastMaFan = d;
    stage = "渲染";
    paintMaFan(d);
    if (d.warn) {
      document.getElementById("maFanSummary")?.insertAdjacentHTML(
        "beforeend", ` <span class="meta" style="color:#fca5a5">· ${escAttr(d.warn)}</span>`,
      );
    }
    setModStamp("maFanStamp", (d.scan && d.scan.saved_at) || null, {
      label: "发散",
      title: d.note || "夜间日线扫描",
    });
  } catch (e) {
    const msg = (e && e.message) || String(e);
    const where = `${stage}${status != null ? " · HTTP " + status : ""}`;
    const isAdmin = !!(authUser && authUser.role === "admin");
    document.getElementById("maFanBody").innerHTML =
      `<tr><td colspan="14" class="meta mafan-err">均线发散加载失败（${escAttr(where)}）：${escAttr(msg)}`
      + (isAdmin
        ? ` · <a href="/api/ops/logs?level=warning&lines=300" target="_blank" rel="noopener">看错误日志</a>`
        : "")
      + `</td></tr>`;
    setModStamp("maFanStamp", null, { stale: true, label: "发散" });
    console.error("loadMaFan failed", where, e);
    reportClientError("mafan-load", msg, status, where + (e && e.stack ? " | " + String(e.stack).slice(0, 300) : ""));
  }
}

/** Best-effort: write a browser-side failure into the server log. */
function reportClientError(where, message, status, detail) {
  try {
    fetch("/api/ops/client-error", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        where: String(where || "").slice(0, 60),
        message: String(message || "").slice(0, 500),
        status: status == null ? null : Number(status),
        detail: String(detail || "").slice(0, 500),
      }),
    }).catch(() => {});
  } catch (_) {}
}

let maFanPollTimer = null;

function fmtSecs(s) {
  const n = Math.max(0, Math.round(Number(s) || 0));
  return n >= 60 ? `${Math.floor(n / 60)}分${String(n % 60).padStart(2, "0")}秒` : `${n}秒`;
}

/** Render the MA-fan rescan progress bar; returns true while a scan runs. */
function paintMaFanProgress(p) {
  const box = document.getElementById("maFanProgress");
  const fill = document.getElementById("maFanProgressFill");
  const txt = document.getElementById("maFanProgressText");
  const btn = document.getElementById("maFanForceRun");
  if (!box || !p) return false;
  const running = !!p.running;
  const cool = Number(p.cooldown_s || 0);
  if (btn) {
    btn.disabled = running || cool > 0;
    btn.title = running
      ? "扫描进行中"
      : (cool > 0 ? `冷却中，约 ${Math.ceil(cool / 60)} 分钟后可再扫（防数据源封禁）` : "管理员：强制重扫今日 0–1000");
  }
  if (!running && !p.error && p.phase !== "done") {
    box.hidden = true;
    return false;
  }
  box.hidden = false;
  box.classList.toggle("err", !!p.error);
  const pct = Math.max(0, Math.min(100, Number(p.pct || 0)));
  if (fill) fill.style.width = (running ? pct : (p.error ? pct : 100)) + "%";
  const phaseZh = { queued: "排队", universe: "拉成交额榜", bars: "拉日线", done: "完成", error: "失败" }[p.phase] || p.phase || "";
  const parts = [];
  if (running) {
    parts.push(`${p.kind === "force" ? "重扫" : "定时档"} · ${phaseZh}`);
    if (p.slice) parts.push(`档 ${p.slice}（${p.slice_idx || 1}/${p.slice_n || 1}）`);
    if (p.total) parts.push(`${p.done || 0}/${p.total} · ${pct}%`);
    parts.push(`命中 ${p.hits || 0}`);
    if (p.fails) parts.push(`失败 ${p.fails}`);
    parts.push(`已用 ${fmtSecs(p.elapsed_s)}`);
    if (p.eta_s != null) parts.push(`预计还要 ${fmtSecs(p.eta_s)}`);
  } else if (p.error) {
    parts.push(`上次扫描失败：${p.error}`);
    if (p.finished_at) parts.push(p.finished_at);
  } else {
    parts.push(`上次扫描完成 · 扫 ${p.done || 0} 只 · 命中 ${p.hits || 0} · 用时 ${fmtSecs(p.elapsed_s)}`);
    if (p.finished_at) parts.push(p.finished_at);
    if (cool > 0) parts.push(`冷却 ${fmtSecs(cool)}`);
  }
  if (txt) {
    txt.innerHTML = parts.map(escAttr).join(" · ")
      + (p.error ? ` · <a href="/api/ops/logs?q=ma_fan&lines=300" target="_blank" rel="noopener">看日志</a>` : "");
  }
  return running;
}

/** Poll scan progress (admin only) until the scan ends, then reload the list. */
async function pollMaFanProgress() {
  if (!(authUser && authUser.role === "admin")) return;
  if (maFanPollTimer) {
    clearTimeout(maFanPollTimer);
    maFanPollTimer = null;
  }
  let wasRunning = false;
  const tick = async () => {
    let p = null;
    try {
      const r = await fetch("/api/ma-fan/progress", { credentials: "same-origin" });
      const d = await r.json().catch(() => ({}));
      if (r.ok) p = d.progress || null;
    } catch (_) {}
    const running = p ? paintMaFanProgress(p) : wasRunning;
    if (running) {
      wasRunning = true;
      maFanPollTimer = setTimeout(tick, 2000);
      return;
    }
    maFanPollTimer = null;
    if (wasRunning) {
      await loadMaFan(true, (p && p.trade_date) || maFanViewDate);
    }
  };
  await tick();
}

function paintMaFan(d) {
  const scan = (d && d.scan) || {};
  const dates = (d && d.dates) || [];
  const day = d.view_date || scan.trade_date || "";
  const input = document.getElementById("maFanDayInput");
  if (input && day) input.value = day;
  const chips = document.getElementById("maFanDayChips");
  if (chips) {
    chips.innerHTML = dates.slice(0, 12).map((x) =>
      `<button type="button" class="rev-col-btn${x === day ? " on" : ""}" data-mafan-day="${x}">${String(x).slice(5)}</button>`
    ).join("") || `<span class="meta">尚无扫描日（18/20/22 点分档自动跑，或管理员点重扫）</span>`;
  }
  const sum = document.getElementById("maFanSummary");
  if (sum) {
    if (!scan || !scan.trade_date) {
      sum.innerHTML = `<span class="meta">暂无落盘结果</span>`;
    } else {
      const overlap = scan.review_overlap_n != null
        ? scan.review_overlap_n
        : ((scan.items || []).filter((x) => x.in_review).length);
      const slices = (scan.slices_done || []).join(",") || "—";
      const stages = { 初期: 0, 中期: 0, 后期: 0 };
      for (const it of (scan.items || [])) {
        const s = it.stage || "";
        if (s in stages) stages[s] += 1;
      }
      const themeN = scan.theme_overlap_n != null
        ? scan.theme_overlap_n
        : ((scan.items || []).filter((x) => x.theme_tag).length);
      sum.innerHTML = [
        `<b>${scan.trade_date}</b>`,
        `扫描累加 ${scan.scanned ?? "—"}`,
        `命中 ${scan.hit_n ?? ((scan.items || []).length)}`,
        `档位 ${slices}`,
        `初/中/后 ${stages["初期"]}/${stages["中期"]}/${stages["后期"]}`,
        `复盘交集 ${overlap}`,
        `主线相关 ${themeN}`,
        scan.saved_at ? `落盘 ${scan.saved_at}` : "",
        scan.formula_version != null ? `公式 v${scan.formula_version}` : "",
      ].filter(Boolean).map((t, i) => (i ? `<span class="meta"> · ${t}</span>` : t)).join("");
    }
  }
  const forceBtn = document.getElementById("maFanForceRun");
  if (forceBtn) forceBtn.hidden = !(authUser && authUser.role === "admin");
  syncMaFanFilterInputs();
  paintMaFanHead();
  const all = (scan.items || []);
  const items = maFanFilteredSorted(all);
  const cnt = document.getElementById("maFanFltCount");
  if (cnt) cnt.textContent = all.length ? `显示 ${items.length} / ${all.length}` : "";
  const body = document.getElementById("maFanBody");
  if (!body) return;
  if (!all.length) {
    body.innerHTML = `<tr><td colspan="14" class="meta">该日无命中（或尚未扫描）</td></tr>`;
    return;
  }
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="14" class="meta">无匹配行（放宽筛选条件）</td></tr>`;
    return;
  }
  body.innerHTML = items.map((it) => {
    const code = it.code || "";
    const pct = it.pct == null ? "—" : (Number(it.pct) >= 0 ? "+" : "") + Number(it.pct).toFixed(1);
    const stage = it.stage || "—";
    const tags = [];
    if (it.in_review) tags.push(`<span class="rev-chip up" title="当日复盘有买信号">复盘交集</span>`);
    const rawTags = maFanRawTags(it).filter((t) => t !== it.stage);
    const chipClass = (t) => {
      if (t === "初期" || t === "刚发散" || t === "量能温和" || t === "粘连很紧" || t === "多头排列" || t === "坡度强") return "rev-chip up";
      if (t === "渐进发散" || t === "振幅小" || t === "MA60稳升" || t === "MA60稳" || t === "主板") return "rev-chip up";
      if (t === "贴主线" || t === "近主线" || t === "主线同主题") return "rev-chip up";
      if (t === "贴支线" || t === "近支线" || t === "支线同主题" || t === "贴联动" || t === "近联动" || t === "联动同主题") return "rev-chip";
      if (String(t).startsWith("额档·")) return "rev-chip";
      if (t === "⚠MA60偏弱" || t === "MA60偏弱") return "rev-chip down";
      if (t === "后期" || t === "放量过猛" || t === "单日放量" || t === "坡度弱" || t === "一日拉开" || String(t).startsWith("已拉")) return "rev-chip gate soft";
      return "rev-chip";
    };
    for (const t of rawTags) {
      if (!t) continue;
      tags.push(`<span class="${chipClass(t)}">${t}</span>`);
    }
    return `<tr>
          <td>${tickerHtml(it.name || code, code)}</td>
          <td class="col-wrap col-sector">${maFanSectorHtml(it)}</td>
          <td>${stage}</td>
          <td>${it.score != null ? it.score : "—"}</td>
          <td>${it.close != null ? it.close : "—"}</td>
          <td class="${Number(it.pct) > 0 ? "up" : (Number(it.pct) < 0 ? "down" : "")}">${pct}</td>
          <td>${it.mv_yi != null ? fmtMaFanMv(it.mv_yi) : "—"}</td>
          <td>${it.zt_ytd != null ? it.zt_ytd : "—"}</td>
          <td>${maFanHolderHtml(it)}</td>
          <td>${it.sticky_spread != null ? it.sticky_spread : "—"}</td>
          <td>${it.fan_spread != null ? it.fan_spread : "—"}</td>
          <td>${it.vol_ratio != null ? it.vol_ratio : "—"}</td>
          <td class="col-wrap col-tags">${tags.join("") || "—"}</td>
          <td class="col-wrap col-note meta">${maFanRemark(it, rawTags)}</td>
        </tr>`;
  }).join("");
}

function fmtMaFanMv(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  return n >= 100 ? n.toFixed(0) + "亿" : n.toFixed(1) + "亿";
}

/** Industry on top; desk board pools (热点/钉住) below when known. */
function maFanSectorHtml(it) {
  const ind = String(it.industry || "").trim();
  const pools = (Array.isArray(it.boards) ? it.boards : [])
    .map(String).filter((b) => b && b !== ind).slice(0, 2);
  if (!ind && !pools.length) return "—";
  return (ind ? escAttr(ind) : "")
    + (pools.length ? `<span class="meta" title="作战台板块池">${pools.map(escAttr).join(" · ")}</span>` : "");
}

/** Holder count with quarter-over-quarter change (fewer holders = concentrating). */
function maFanHolderHtml(it) {
  if (it.holder_num == null) return "—";
  const chg = it.holder_chg_pct == null ? null : Number(it.holder_chg_pct);
  const title = [
    it.holder_end ? `截至 ${it.holder_end}` : "",
    it.holder_avg_wan != null ? `户均 ${fmtHolderAvgWan(it.holder_avg_wan)}` : "",
  ].filter(Boolean).join(" · ");
  const chgHtml = chg == null || Number.isNaN(chg)
    ? ""
    : ` <span class="${chg < 0 ? "up" : (chg > 0 ? "down" : "")}">${chg > 0 ? "+" : ""}${chg.toFixed(1)}%</span>`;
  return `<span title="${escAttr(title)}">${fmtHolderNum(it.holder_num)}</span>${chgHtml}`;
}

/** Facts not already shown as columns or chips: sticky window, fan ratio, distance, turnover. */
function maFanRemark(it, rawTags) {
  const parts = [];
  const end = String(it.sticky_end || "").slice(5, 10);
  if (end) {
    parts.push(`粘连止于 ${end}` + (it.sticky_amp != null ? `（振幅 ${Number(it.sticky_amp).toFixed(0)}%）` : ""));
  }
  if (it.fan_ratio != null) parts.push(`价差放大 ×${Number(it.fan_ratio).toFixed(1)}`);
  const hasExtTag = (rawTags || []).some((t) => /^(已拉|离开)/.test(String(t)));
  if (it.ext_pct != null && !hasExtTag) parts.push(`离粘连 ${Number(it.ext_pct).toFixed(0)}%`);
  if (it.amount_yi != null) {
    parts.push(`额 ${Number(it.amount_yi).toFixed(1)}亿` + (it.amount_rank ? `（第 ${it.amount_rank}）` : ""));
  }
  const text = parts.join("；") || "—";
  return it.ma_order
    ? `<span title="MA5>10>20>30>60：${escAttr(it.ma_order)}">${escAttr(text)}</span>`
    : escAttr(text);
}

function shiftMaFanDay(delta) {
  const dates = lastMaFan.dates || [];
  if (!dates.length) return;
  const cur = maFanViewDate || dates[0];
  let idx = dates.indexOf(cur);
  if (idx < 0) idx = 0;
  const next = dates[idx + delta];
  if (next) loadMaFan(true, next);
}
