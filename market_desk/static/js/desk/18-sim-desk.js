/** Paper sim desk tab: account / open lots / trade log. */

let simDeskCache = null;
let simDeskLoading = false;
/** @type {"today"|"day"|"all"} */
let simLogMode = "today";
/** Selected YYYY-MM-DD when mode is day. */
let simLogDay = "";

function _simNum(v, digits) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const d = digits == null ? 2 : digits;
  return n.toFixed(d);
}

function _simPct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const s = (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
  return s;
}

function _simSideZh(side, mode) {
  if (side === "buy") return "买入";
  if (side === "sell") {
    if (mode === "half") return "卖出·减半";
    if (mode === "clear") return "卖出·清仓";
    return "卖出";
  }
  return String(side || "—");
}

function _simTradeDay(t) {
  const raw = String((t && (t.trade_date || t.filled_at)) || "").trim();
  if (raw.length >= 10) return raw.slice(0, 10);
  return "";
}

function _simTodayKey(data) {
  const fromApi = String((data && data.trade_date) || "").slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(fromApi)) return fromApi;
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function _simTradeDays(trades) {
  const set = new Set();
  for (const t of trades || []) {
    const day = _simTradeDay(t);
    if (day) set.add(day);
  }
  return Array.from(set).sort().reverse();
}

function _filterSimTrades(trades, data) {
  const all = Array.isArray(trades) ? trades : [];
  if (simLogMode === "all") return all;
  const day = simLogMode === "day" && simLogDay
    ? simLogDay
    : _simTodayKey(data);
  return all.filter((t) => _simTradeDay(t) === day);
}

function _syncSimLogFilterUi(data) {
  const trades = (data && data.trades) || [];
  const days = _simTradeDays(trades);
  const today = _simTodayKey(data);
  const sel = document.getElementById("simLogDay");
  if (sel) {
    const prev = sel.value || simLogDay;
    sel.innerHTML = days.map((d) =>
      `<option value="${d}">${d}${d === today ? "（今）" : ""}</option>`
    ).join("") || `<option value="">无成交日</option>`;
    if (simLogMode === "day") {
      const pick = days.includes(prev) ? prev : (days[0] || "");
      simLogDay = pick;
      sel.value = pick;
      sel.hidden = false;
    } else {
      sel.hidden = true;
    }
  }
  const todayBtn = document.getElementById("simLogToday");
  const dayBtn = document.getElementById("simLogDayBtn");
  const allBtn = document.getElementById("simLogAll");
  const mark = (el, on) => {
    if (!el) return;
    el.classList.toggle("ok", !!on);
    el.classList.toggle("gate", true);
  };
  mark(todayBtn, simLogMode === "today");
  mark(dayBtn, simLogMode === "day");
  mark(allBtn, simLogMode === "all");
}

function _paintSimLog(data) {
  const logBody = document.getElementById("simLogBody");
  const meta = document.getElementById("simLogMeta");
  if (!logBody) return;
  const all = (data && data.trades) || [];
  const trades = _filterSimTrades(all, data);
  let buyN = 0;
  let sellN = 0;
  for (const t of trades) {
    const side = String(t.side || "");
    if (side === "buy") buyN += 1;
    else if (side === "sell") sellN += 1;
  }
  const today = _simTodayKey(data);
  let scope = "全部";
  if (simLogMode === "today") scope = `今日 ${today}`;
  else if (simLogMode === "day") scope = simLogDay || "—";
  if (meta) {
    meta.textContent = trades.length
      ? `${scope} · ${trades.length} 笔（买 ${buyN} / 卖 ${sellN}）`
      : `${scope} · 无成交`;
  }
  if (!all.length) {
    logBody.innerHTML = `<tr><td colspan="11" class="meta">暂无成交；等待亮灯可买或管理员 backfill</td></tr>`;
    return;
  }
  if (!trades.length) {
    logBody.innerHTML = `<tr><td colspan="11" class="meta">${scope} 无成交；可切「全部」或其它日期</td></tr>`;
    return;
  }
  logBody.innerHTML = trades.map((t) => {
    const side = String(t.side || "");
    const pnl = t.pnl_cny;
    const cls = Number(pnl) > 0 ? "up" : Number(pnl) < 0 ? "down" : "";
    const sideCls = side === "buy" ? "up" : side === "sell" ? "down" : "";
    const cash = t.cash_after;
    const cashShow = side === "buy" && Number(cash) === 0 ? "—" : _simNum(cash);
    return `<tr>
      <td>${(t.filled_at || t.trade_date || "").toString().slice(0, 19)}</td>
      <td class="${sideCls}"><b>${_simSideZh(side, t.exit_mode)}</b></td>
      <td>${t.code || ""}</td>
      <td>${t.name || ""}</td>
      <td>${t.qty || "—"}</td>
      <td>${_simNum(t.px, 3)}</td>
      <td>${_simNum(t.notional)}</td>
      <td>${_simNum(t.fee)}</td>
      <td class="${cls}">${side === "sell" ? _simNum(pnl) : "—"}</td>
      <td>${cashShow}</td>
      <td class="meta">${
        side === "sell"
          ? (t.role_label || t.note || "")
          : (t.note || t.role_label || "")
      }</td>
    </tr>`;
  }).join("");
}

function paintSimDesk(data) {
  simDeskCache = data || null;
  const acct = (data && data.account) || {};
  const gate = (data && data.gate) || {};
  const sum = document.getElementById("simSum");
  if (sum) {
    const ret = acct.return_pct;
    const retCls = Number(ret) > 0 ? "up" : Number(ret) < 0 ? "down" : "";
    sum.innerHTML =
      `<span>初始 <b>${_simNum(acct.initial_cash, 0)}</b></span>`
      + `<span>现金 <b>${_simNum(acct.cash)}</b></span>`
      + `<span>市值 <b>${_simNum(acct.mtm)}</b></span>`
      + `<span>权益 <b>${_simNum(acct.equity)}</b></span>`
      + `<span class="${retCls}">累计 <b>${_simPct(ret)}</b></span>`
      + `<span>已实现 <b>${_simNum(gate.realized_pnl_cny)}</b></span>`
      + `<span>手续费/笔 <b>${_simNum(acct.fee_cny, 0)}</b> 元</span>`;
  }
  const gateEl = document.getElementById("simGate");
  if (gateEl) {
    const tradeOk = !!gate.gate_ok;
    const readonlyOk = !!gate.gate_readonly_ok;
    const toGreen = gate.to_green_cny;
    gateEl.innerHTML =
      `<div><b>开工闸</b> · ${gate.gate_label || "—"}`
      + ` · 平仓${gate.closed_n || 0} · 持仓${gate.open_n || 0}`
      + (toGreen != null && !tradeOk ? ` · 距绝对转正约 ${_simNum(toGreen)} 元` : "")
      + `</div>`
      + `<div class="meta">${gate.rule || ""}</div>`;
    // Three tones: absolute green (trade) / readonly only / neither.
    gateEl.classList.toggle("ok", tradeOk);
    gateEl.classList.toggle("readonly", !tradeOk && readonlyOk);
    gateEl.classList.toggle("warn", !tradeOk && !readonlyOk);
  }
  const attrEl = document.getElementById("simAttr");
  if (attrEl) {
    const attr = (data && data.attribution) || {};
    const buckets = attr.buckets || [];
    if (!buckets.length) {
      attrEl.innerHTML = "";
    } else {
      const bits = buckets.map((b) => {
        const cny = b.cny == null ? "" : ` ${_simNum(b.cny)}`;
        return `${b.label || b.id}×${b.n || 0}${cny}`;
      });
      const worst = (attr.worst_sells || []).slice(0, 3).map((w) =>
        `${w.code || ""} ${w.name || ""} ${_simNum(w.pnl_cny)}`
      );
      attrEl.innerHTML =
        `<div><b>归因</b> · 手续费合计 ${_simNum(attr.fee_total)} · ${bits.join(" · ")}</div>`
        + (worst.length
          ? `<div class="meta">最差平仓：${worst.join("；")}</div>`
          : "");
    }
  }
  const posBody = document.getElementById("simPosBody");
  if (posBody) {
    const lots = (data && data.open_lots) || [];
    if (!lots.length) {
      posBody.innerHTML = `<tr><td colspan="9" class="meta">暂无模拟持仓</td></tr>`;
    } else {
      posBody.innerHTML = lots.map((lot) => {
        const qty = Number(lot.qty || 0);
        const cost = Number(lot.open_px || 0);
        const last = Number(lot.last_px || cost);
        const mtm = qty * last;
        const pnl = cost > 0 ? ((last / cost) - 1) * 100 : 0;
        const cls = pnl > 0 ? "up" : pnl < 0 ? "down" : "";
        return `<tr>
          <td>${lot.code || ""}</td>
          <td>${lot.name || ""}</td>
          <td>${lot.size_tag || ""}</td>
          <td>${qty}</td>
          <td>${_simNum(cost, 3)}</td>
          <td>${_simNum(last, 3)}</td>
          <td>${_simNum(mtm)}</td>
          <td class="${cls}">${_simPct(pnl)}</td>
          <td>${lot.open_date || ""}</td>
        </tr>`;
      }).join("");
    }
  }
  _syncSimLogFilterUi(data);
  _paintSimLog(data);
  const stamp = document.getElementById("simStamp");
  if (stamp) {
    const now = new Date();
    stamp.textContent = now.toTimeString().slice(0, 8);
  }
}

function _paintBrokerReconcile(rec) {
  const box = document.getElementById("simBroker");
  const meta = document.getElementById("simBrokerMeta");
  const diffsEl = document.getElementById("simBrokerDiffs");
  if (!box || !meta || !diffsEl) return;
  if (!rec || rec.reason === "live_gateway_not_attached") {
    box.hidden = false;
    box.classList.remove("ok", "warn");
    meta.textContent = "未挂券商快照 · 管理员 POST /api/ops/exec-broker-ingest";
    diffsEl.textContent = "阶段 1：导入券商持仓/成交 JSON 后对账；Send 仍拒绝。";
    return;
  }
  box.hidden = false;
  const paper = (rec.paper_vs_live || {}).positions || {};
  const desk = rec.desk_vs_live || {};
  const ok = !!rec.ok;
  box.classList.toggle("ok", ok);
  box.classList.toggle("warn", !ok);
  meta.textContent =
    `${rec.gateway || "broker"} · ${rec.gate || ""}`
    + ` · 纸面差异 ${paper.mismatch_n ?? "—"}`
    + (desk && desk.mismatch_n != null ? ` · 记账差异 ${desk.mismatch_n}` : "");
  const rows = []
    .concat(paper.diffs || [])
    .concat((desk && desk.diffs) || []);
  if (!rows.length) {
    diffsEl.textContent = ok ? "纸面/记账与券商持仓一致" : (rec.reason || "对账未完成");
    return;
  }
  diffsEl.innerHTML = rows.slice(0, 12).map((d) =>
    `${d.code || ""} ${d.name || ""} ${d.status || ""} 本地${d.local_volume ?? "—"}/券商${d.remote_volume ?? "—"}`
  ).join("；");
}

async function loadBrokerReconcile() {
  try {
    const res = await fetch("/api/ops/exec-reconcile", { credentials: "same-origin" });
    if (res.status === 401 || res.status === 403) {
      const box = document.getElementById("simBroker");
      if (box) box.hidden = true;
      return;
    }
    const data = await res.json();
    _paintBrokerReconcile(data);
  } catch (e) {
    /* non-admin or offline — leave box as-is */
  }
}

async function loadSimDesk(force) {
  if (simDeskLoading) return;
  if (!force && simDeskCache && currentMain !== "sim") return;
  simDeskLoading = true;
  try {
    const res = await fetch("/api/sim-desk", { credentials: "same-origin" });
    if (res.status === 401 || res.status === 403) {
      const sum = document.getElementById("simSum");
      if (sum) sum.innerHTML = `<span class="meta">需登录后查看模拟账本</span>`;
      return;
    }
    const data = await res.json();
    if (!data || !data.ok) {
      const sum = document.getElementById("simSum");
      if (sum) sum.innerHTML = `<span class="meta">加载失败</span>`;
      return;
    }
    paintSimDesk(data);
    loadBrokerReconcile();
  } catch (e) {
    const sum = document.getElementById("simSum");
    if (sum) sum.innerHTML = `<span class="meta">网络错误</span>`;
  } finally {
    simDeskLoading = false;
  }
}

(function bindSimLogFilters() {
  const root = document.getElementById("simLogFilters");
  if (!root || root.dataset.bound) return;
  root.dataset.bound = "1";
  root.addEventListener("click", (ev) => {
    const btn = ev.target.closest && ev.target.closest("[data-sim-log]");
    if (!btn) return;
    const mode = btn.getAttribute("data-sim-log");
    if (mode !== "today" && mode !== "day" && mode !== "all") return;
    simLogMode = mode;
    if (mode === "day" && !simLogDay && simDeskCache) {
      const days = _simTradeDays((simDeskCache.trades) || []);
      simLogDay = days[0] || _simTodayKey(simDeskCache);
    }
    if (simDeskCache) {
      _syncSimLogFilterUi(simDeskCache);
      _paintSimLog(simDeskCache);
    }
  });
  const sel = document.getElementById("simLogDay");
  if (sel && !sel.dataset.bound) {
    sel.dataset.bound = "1";
    sel.addEventListener("change", () => {
      simLogDay = sel.value || "";
      simLogMode = "day";
      if (simDeskCache) {
        _syncSimLogFilterUi(simDeskCache);
        _paintSimLog(simDeskCache);
      }
    });
  }
})();
