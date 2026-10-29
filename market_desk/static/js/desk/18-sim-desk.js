/** Paper sim desk tab: account / open lots / trade log. */

let simDeskCache = null;
let simDeskLoading = false;

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
    const ok = !!gate.gate_ok;
    const toGreen = gate.to_green_cny;
    gateEl.innerHTML =
      `<div><b>开工闸</b> · ${gate.gate_label || "—"}`
      + ` · 平仓${gate.closed_n || 0} · 持仓${gate.open_n || 0}`
      + (toGreen != null && !ok ? ` · 距转正约 ${_simNum(toGreen)} 元` : "")
      + `</div>`
      + `<div class="meta">${gate.rule || ""}</div>`;
    gateEl.classList.toggle("ok", ok);
    gateEl.classList.toggle("warn", !ok);
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
  const logBody = document.getElementById("simLogBody");
  if (logBody) {
    const trades = (data && data.trades) || [];
    if (!trades.length) {
      logBody.innerHTML = `<tr><td colspan="11" class="meta">暂无成交；等待亮灯可买或管理员 backfill</td></tr>`;
    } else {
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
          <td class="meta">${t.note || t.role_label || ""}</td>
        </tr>`;
      }).join("");
    }
  }
  const stamp = document.getElementById("simStamp");
  if (stamp) {
    const now = new Date();
    stamp.textContent = now.toTimeString().slice(0, 8);
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
  } catch (e) {
    const sum = document.getElementById("simSum");
    if (sum) sum.innerHTML = `<span class="meta">网络错误</span>`;
  } finally {
    simDeskLoading = false;
  }
}
