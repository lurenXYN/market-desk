let _posLhbKey = "";
let _posLhbLoading = false;
function _fmtLhbAmt(yi, wan) {
  if (yi != null && Math.abs(Number(yi)) >= 0.01) return Number(yi).toFixed(2) + "亿";
  if (wan != null) return Number(wan).toFixed(0) + "万";
  return "—";
}
function _seatLi(s) {
  const tag = s.style_label
    ? `<span class="lhb-tag ${s.style || ""}" title="${(s.style_note || "").replace(/"/g, "&quot;")}">${s.style_label}</span>`
    : "";
  const sideAmt = s.side === "buy"
    ? _fmtLhbAmt(s.buy_yi, s.buy_wan)
    : _fmtLhbAmt(s.sell_yi, s.sell_wan);
  const short = (s.name || "").replace(/证券股份有限公司|股份有限公司|有限责任公司|证券营业部/g, "");
  return `<li>${tag}<span class="seat-name" title="${s.name || ""}">${short || s.name || "—"}</span>`
    + `<span class="meta">${sideAmt}</span></li>`;
}
function paintPosLhb(payload) {
  const body = document.getElementById("posLhbBody");
  const meta = document.getElementById("posLhbMeta");
  const note = document.getElementById("posLhbNote");
  if (!body) return;
  if (meta) meta.textContent = payload.note || "";
  if (note) note.textContent = payload.disclaimer || "席位风格为网络常见口碑标签，非官方分类。";
  const items = payload.items || [];
  maybeToastLhbSeatEdge(items);
  if (!items.length) {
    body.innerHTML = `<div class="meta">${payload.note || "暂无持仓龙虎数据"}</div>`;
    return;
  }
  body.innerHTML = items.map((it) => {
    if (!it.on_list) {
      return `<div class="pos-lhb-card"><div class="title"><b>${it.name || ""}</b>`
        + `<span class="meta">${it.code || ""}</span></div>`
        + `<div class="meta">${it.note || "近端未上榜"}</div></div>`;
    }
    const netCls = (Number(it.net_yi) || 0) >= 0 ? "up" : "down";
    const chg = it.change_pct == null ? "" : ` · ${fmtPct(it.change_pct)}`;
    const hints = (it.summary && it.summary.hints || []).join(" · ");
    const styleLine = (it.summary && it.summary.style_line) || "—";
    const risk = (it.summary && it.summary.seat_risk) || "ok";
    const riskBadge = risk === "bad"
      ? `<span class="lhb-tag smash">席位偏坏</span>`
      : (risk === "warn" ? `<span class="lhb-tag quant">席位偏弱</span>`
        : (risk === "ok" ? `<span class="lhb-tag institution">席位偏稳</span>` : ""));
    const riskWhy = (it.summary && it.summary.risk_reason) || "";
    const buys = (it.buys || []).map(_seatLi).join("") || `<li class="meta">无买入席位</li>`;
    const sells = (it.sells || []).map(_seatLi).join("") || `<li class="meta">无卖出席位</li>`;
    return `<div class="pos-lhb-card">`
      + `<div class="title"><b>${it.name || ""}</b><span class="meta">${it.code || ""}</span>`
      + `<span class="meta">${it.trade_date || ""}</span>`
      + `<span class="${netCls}">净买 ${it.net_yi == null ? "—" : it.net_yi + "亿"}</span>${chg}${riskBadge}</div>`
      + `<div class="meta">${it.reason || "—"} · 风格 ${styleLine}</div>`
      + (riskWhy ? `<div class="hints">${riskWhy}</div>` : "")
      + (hints ? `<div class="hints">${hints}</div>` : "")
      + `<div class="pos-lhb-seats"><div><h4>买入席位</h4><ul>${buys}</ul></div>`
      + `<div><h4>卖出席位</h4><ul>${sells}</ul></div></div></div>`;
  }).join("");
}
function maybeToastLhbSeatEdge(items) {
  const STORAGE = "desk-lhb-seat-edge";
  const FLAG_ZH = {
    smash_sell: "砸盘王席卖出",
    inst_net_sell: "机构净卖",
    nb_net_sell: "北向净卖",
    quant_net_sell: "量化席净卖偏多",
  };
  const rank = { ok: 0, warn: 1, bad: 2 };
  let prev = {};
  try { prev = JSON.parse(localStorage.getItem(STORAGE) || "{}") || {}; } catch (e) { prev = {}; }
  const next = {};
  const alerts = [];
  const seeded = Object.keys(prev).length === 0;
  (items || []).forEach((it) => {
    if (!it || !it.on_list) return;
    const code = String(it.code || "").padStart(6, "0");
    const flags = ((it.summary && it.summary.risk_flags) || []).slice().filter(Boolean).sort();
    const risk = (it.summary && it.summary.seat_risk) || "ok";
    const sig = `${it.trade_date || ""}|${risk}|${flags.join(",")}`;
    next[code] = sig;
    if (seeded) return;
    if (prev[code] === sig) return;
    const parts = String(prev[code] || "").split("|");
    const prevRisk = parts[1] || "ok";
    const prevFlags = (parts[2] ? parts[2].split(",") : []).filter(Boolean);
    const prevSet = new Set(prevFlags);
    const curSet = new Set(flags);
    const added = flags.filter((f) => !prevSet.has(f));
    const cleared = prevFlags.filter((f) => !curSet.has(f));
    const curRank = rank[risk] || 0;
    const prevRank = rank[prevRisk] || 0;
    let title = "";
    if (curRank > prevRank) title = "龙虎席位变坏";
    else if (curRank < prevRank) title = "龙虎席位变好";
    else if (added.length || cleared.length) title = "龙虎席位结构变化";
    else return;
    const bits = [];
    if (added.length) bits.push("新增 " + added.map((f) => FLAG_ZH[f] || f).join("、"));
    if (cleared.length) bits.push("消退 " + cleared.map((f) => FLAG_ZH[f] || f).join("、"));
    const riskWhy = (it.summary && it.summary.risk_reason) || "";
    if (riskWhy) bits.push(riskWhy);
    alerts.push({
      title,
      body: `${it.name || ""} ${code}${bits.length ? " · " + bits.join("；") : ""}`,
    });
  });
  try { localStorage.setItem(STORAGE, JSON.stringify(next)); } catch (e) {}
  alerts.slice(0, 3).forEach((a) => pushPageToast(a.title, a.body));
}
let _posDiaryAt = 0;
let _posCalAt = 0;
function paintPosDiary(payload) {
  const body = document.getElementById("posDiaryBody");
  const meta = document.getElementById("posDiaryMeta");
  if (!body) return;
  const items = (payload && payload.items) || [];
  if (meta) meta.textContent = items.length ? `近 ${items.length} 笔` : "";
  if (!items.length) {
    body.innerHTML = `<div class="meta">暂无执行日记；记账买/减/清后自动出现</div>`;
    return;
  }
  const sideZh = { buy: "买", half: "减", clear: "清", sell: "卖" };
  body.innerHTML = items.map((it) => {
    const side = String(it.side || "");
    const cls = side === "buy" ? "side-buy"
      : (side === "clear" ? "side-clear" : "side-half");
    const adv = it.advice || {};
    const bits = [];
    if (adv.source === "manual") bits.push("手动");
    if (adv.action) bits.push(adv.action);
    if (adv.phase) bits.push("相位 " + adv.phase);
    if (adv.mainline) bits.push("主线 " + adv.mainline);
    if (adv.sell && adv.sell.next_action_zh) bits.push(adv.sell.next_action_zh);
    else if (adv.buy && adv.buy.buy_price != null) bits.push("建议买 " + adv.buy.buy_price);
    return `<div class="diary-row">`
      + `<span class="${cls}">${sideZh[side] || side}</span> `
      + `<b>${it.name || ""}</b> <span class="meta">${it.code || ""}</span> `
      + `${it.qty != null ? it.qty + "股" : ""}`
      + `${it.price != null ? " @ " + it.price : ""}`
      + `<div class="meta">${(it.created_at || "").slice(11, 19) || (it.trade_date || "")}`
      + `${bits.length ? " · " + bits.join(" · ") : ""}`
      + `${it.note ? " · " + it.note : ""}</div></div>`;
  }).join("");
}
function paintPosCal(payload) {
  const body = document.getElementById("posCalBody");
  const meta = document.getElementById("posCalMeta");
  if (!body) return;
  const items = (payload && payload.items) || [];
  const dayRate = payload && payload.day_win_rate;
  const weekRate = payload && payload.week_win_rate;
  const bits = [];
  if (dayRate != null) bits.push(`日胜率 ${dayRate}%（${payload.day_wins || 0}/${payload.day_n || 0}）`);
  if (weekRate != null) bits.push(`近周 ${weekRate}%（${payload.week_wins || 0}/${payload.week_n || 0}）`);
  if (meta) meta.textContent = bits.join(" · ") || "暂无盈亏样本";
  if (!items.length) {
    body.innerHTML = `<div class="meta">暂无日记/日终盈亏记录</div>`;
    return;
  }
  body.innerHTML = `<div class="pos-cal-grid">` + items.map((it) => {
    const pnl = it.day_pnl;
    const cls = pnl == null ? "flat" : (Number(pnl) > 0 ? "up" : (Number(pnl) < 0 ? "down" : "flat"));
    const tip = (it.notes || []).join("；").replace(/"/g, "&quot;");
    const v = pnl == null ? "—" : ((Number(pnl) > 0 ? "+" : "") + Number(pnl).toFixed(0));
    const sub = [];
    if (it.buy_n) sub.push(`买${it.buy_n}`);
    if (it.sell_n) sub.push(`卖${it.sell_n}`);
    return `<div class="pos-cal-cell ${cls}" title="${tip || (it.date || "")}">`
      + `<div class="d">${String(it.date || "").slice(5)}</div>`
      + `<div class="v">${v}</div>`
      + (sub.length ? `<div class="n">${sub.join(" ")}</div>` : "")
      + `</div>`;
  }).join("") + `</div>`;
}
async function ensurePosCal(force) {
  const box = document.getElementById("posCal");
  if (!box) return;
  if (typeof canPersonal === "function" && !canPersonal()) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (!box.open && !force) return;
  if (!force && _posCalAt && Date.now() - _posCalAt < 60000) return;
  try {
    const r = await fetch("/api/positions/stats?days=20", { credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error("cal");
    _posCalAt = Date.now();
    paintPosCal(d);
  } catch (e) {
    const body = document.getElementById("posCalBody");
    if (body) body.innerHTML = `<div class="meta">盈亏日历加载失败</div>`;
  }
}
document.getElementById("posCal")?.addEventListener("toggle", () => {
  if (document.getElementById("posCal")?.open) ensurePosCal(true);
});
async function ensurePosDiary(force) {
  const box = document.getElementById("posDiary");
  if (!box) return;
  if (typeof canPersonal === "function" && !canPersonal()) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (!force && _posDiaryAt && Date.now() - _posDiaryAt < 45000) return;
  try {
    const r = await fetch("/api/exec-diary?limit=30", { credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error("diary");
    _posDiaryAt = Date.now();
    paintPosDiary(d);
  } catch (e) {
    const body = document.getElementById("posDiaryBody");
    if (body) body.innerHTML = `<div class="meta">执行日记加载失败</div>`;
  }
}
document.getElementById("posDiaryRefresh")?.addEventListener("click", () => {
  _posDiaryAt = 0;
  ensurePosDiary(true);
});
async function ensurePosLhb(force) {
  const box = document.getElementById("posLhb");
  if (!box) return;
  if (typeof canPersonal === "function" && !canPersonal()) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const openCodes = (lastData && lastData.positions || [])
    .filter((p) => Number(p.qty || 0) > 0)
    .map((p) => String(p.code || "").padStart(6, "0"))
    .sort()
    .join(",");
  if (!force && openCodes === _posLhbKey && document.getElementById("posLhbBody")?.dataset.ready === "1") {
    return;
  }
  if (_posLhbLoading) return;
  _posLhbLoading = true;
  const body = document.getElementById("posLhbBody");
  if (body && force) body.innerHTML = `<div class="meta">拉取龙虎榜…</div>`;
  try {
    const r = await fetch("/api/positions/lhb" + (force ? "?force=1" : ""), {
      credentials: "same-origin",
    });
    if (r.status === 401 || r.status === 403) {
      box.hidden = true;
      return;
    }
    const d = await r.json().catch(() => ({}));
    if (!d.ok) {
      if (body) body.innerHTML = `<div class="meta">${d.detail || "龙虎榜暂不可用"}</div>`;
      return;
    }
    _posLhbKey = openCodes;
    paintPosLhb(d);
    if (body) body.dataset.ready = "1";
  } catch (e) {
    if (body) body.innerHTML = `<div class="meta">龙虎榜拉取失败</div>`;
  } finally {
    _posLhbLoading = false;
  }
}
document.getElementById("posLhbRefresh")?.addEventListener("click", () => ensurePosLhb(true));
