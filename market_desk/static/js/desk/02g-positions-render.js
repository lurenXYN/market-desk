/** Paint position summary, risk box, position rows, and the trailing per-panel painters. */
function renderPositionsPanel(d, v) {
  const sum = d.position_summary || {};
  const risk = d.risk_overview || sum;
  const floatPnl = sum.floating_pnl != null ? sum.floating_pnl : sum.pnl;
  const floatPct = sum.floating_pct != null ? sum.floating_pct : sum.pnl_pct;
  const realized = sum.realized_pnl ?? 0;
  // Always prefer sum of row session P&L so a stale summary cannot show floating as 当日盈亏.
  let dayPnl = null;
  let dayBasis = 0;
  let dayAny = false;
  for (const p of (d.positions || [])) {
    if (p.day_pnl == null || p.day_pnl === "") continue;
    dayPnl = (dayPnl == null ? 0 : dayPnl) + (Number(p.day_pnl) || 0);
    dayAny = true;
    const qty = Number(p.qty || 0);
    const sold = Number(p.day_sold_qty || 0);
    const buy = Number(p.buy_price || 0);
    const prev = Number(p.prev || 0);
    if (qty > 0) {
      if (p.bought_today && buy > 0) dayBasis += buy * qty;
      else if (prev > 0) dayBasis += prev * qty;
      else if (buy > 0) dayBasis += buy * qty;
    } else if (sold > 0 && buy > 0) {
      dayBasis += buy * sold;
    }
  }
  if (!dayAny) dayPnl = sum.day_pnl != null ? sum.day_pnl : null;
  else dayPnl = Math.round(dayPnl * 100) / 100;
  let dayPct = null;
  if (dayPnl != null && dayBasis > 0) dayPct = Math.round((dayPnl / dayBasis) * 10000) / 100;
  else if (sum.day_pnl_pct != null && !dayAny) dayPct = sum.day_pnl_pct;
  const pnlCls = (dayPnl || 0) >= 0 ? "up" : "down";
  const floatCls = (floatPnl || 0) >= 0 ? "up" : "down";
  const realCls = (realized || 0) >= 0 ? "up" : "down";
  const riskNote = sum.risk_note ? `<span class="bans">${sum.risk_note}</span>` : "";
  const closedBit = sum.closed_count ? ` · 今日已平 ${sum.closed_count}` : "";
  document.getElementById("posSum").innerHTML =
    `<span class="day-primary">当日盈亏（相对昨收） <b class="${pnlCls}">${fmtMoney(dayPnl)}${dayPct == null ? "" : " (" + (dayPct > 0 ? "+" : "") + dayPct + "%)"}</b></span>` +
    `<span>持仓 <b>${sum.count ?? 0}</b>${closedBit}</span>` +
    `<span>成本 <b>${fmtMoney(sum.cost)}</b></span>` +
    `<span>市值 <b>${fmtMoney(sum.market)}</b></span>` +
    `<span class="muted-sec">浮盈 <b class="${floatCls}">${fmtMoney(floatPnl)}${floatPct == null ? "" : " (" + (floatPct > 0 ? "+" : "") + floatPct + "%)"}</b></span>` +
    `<span class="muted-sec">已实现 <b class="${realCls}">${fmtMoney(realized)}</b></span>` +
    riskNote;
  const caps = risk.caps || {};
  const tipHtml = (risk.tips || []).length
    ? `<div class="risk-tips">${(risk.tips || []).map((t) => `· ${t}`).join("<br/>")}</div>`
    : "";
  const targetBit = risk.target_total_cost != null
    ? ` · 目标成本 ${fmtMoney(risk.target_total_cost)}${risk.target_dev_pct == null ? "" : "（偏差 " + (risk.target_dev_pct > 0 ? "+" : "") + risk.target_dev_pct + "%）"}`
    : "";
  document.getElementById("riskBox").innerHTML =
    `<div class="hd">风控总览<button type="button" class="q" data-term="风控总览">?</button> · `
    + `盈 ${risk.winners ?? 0} / 亏 ${risk.losers ?? 0} / 平 ${risk.flat ?? 0}`
    + ` · 软上限 只数≤${caps.max_names ?? "—"} 单票≤${caps.max_single_pct ?? "—"}%`
    + ` 题材≤${caps.max_theme_pct ?? "—"}% 总成本≤${caps.max_total_cost ?? "—"}`
    + `${targetBit}</div>`
    + tipHtml
    + ((risk.themes || []).length
      ? (`<div class="risk-themes" title="题材集中度">`
        + (risk.themes || []).slice(0, 5).map((t) =>
          `<span class="tchip${t.over_theme ? " warn" : ""}">${t.theme || "—"} ${t.weight_pct == null ? "—" : (t.weight_pct + "%")}`
          + ` · ${t.n || 0}只</span>`
        ).join("")
        + `</div>`)
      : "")
    + ((risk.items || []).length
      ? `<table><thead><tr><th>名称</th><th>占比</th><th>等权偏</th><th>浮盈%</th><th>市值</th></tr></thead><tbody>`
        + (risk.items || []).map((it) =>
          `<tr class="${it.over_weight ? "warn" : ""}">
                <td>${it.name || it.code}</td>
                <td class="${it.over_weight ? "warn" : ""}">${it.weight_pct == null ? "—" : it.weight_pct + "%"}</td>
                <td>${it.weight_dev_pct == null ? "—" : ((it.weight_dev_pct > 0 ? "+" : "") + it.weight_dev_pct + "%")}</td>
                <td class="${(it.pnl_pct || 0) >= 0 ? "up" : "down"}">${it.pnl_pct == null ? "—" : ((it.pnl_pct > 0 ? "+" : "") + it.pnl_pct + "%")}</td>
                <td>${fmtMoney(it.market)}</td>
              </tr>`).join("")
        + `</tbody></table>`
      : `<div class="meta">暂无持仓</div>`);
  document.getElementById("posBody").innerHTML = (d.positions || []).map(p => {
    const closed = !!p.closed || Number(p.qty || 0) <= 0;
    const realizedRow = Number(p.day_realized_pnl || 0);
    const dayRow = p.day_pnl;
    const qtyLabel = closed
      ? `0（卖 ${p.day_sold_qty || "—"}）`
      : (p.day_sold_qty ? `${p.qty}（已卖 ${p.day_sold_qty}）` : (p.qty ?? ""));
    const actions = closed
      ? `<button type="button" class="pos-del" data-id="${p.id}">删除</button>`
      : `<button type="button" class="pos-del pos-add" data-id="${p.id}" data-code="${p.code}" data-name="${p.name || ""}">加仓</button>
            <button type="button" class="pos-del pos-half" data-id="${p.id}" data-qty="${p.qty}" data-last="${p.last ?? ""}" title="对半减仓，按100股四舍五入；≤100股则清仓">减半</button>
            <button type="button" class="pos-del pos-clear" data-id="${p.id}" data-qty="${p.qty}" data-last="${p.last ?? ""}">清仓</button>
            <button type="button" class="pos-del pos-trim" data-id="${p.id}" data-qty="${p.qty}" data-last="${p.last ?? ""}">自定义</button>
            <button type="button" class="pos-del" data-id="${p.id}">删除</button>`;
    const dayCls = dayRow == null ? "" : ((Number(dayRow) || 0) >= 0 ? "up" : "down");
    let trendCell = "—";
    if (p.trend_ok) trendCell = `<span class="up">上升</span>`;
    else if (p.trend_down) trendCell = `<span class="down">下降</span>`;
    else if (p.trend_pending) trendCell = `<span class="meta">未取到</span>`;
    else if (p.daily_trend || p.trend) trendCell = `<span class="meta">${p.daily_trend || p.trend}</span>`;
    const halfPx = p.half_anchor_price != null ? p.half_anchor_price
      : (p.last_sell_price != null && p.day_sold_qty ? p.last_sell_price : null);
    const halfCell = halfPx != null
      ? `<span title="今日已减锚价；破此价可再清">${halfPx}</span>`
      : "—";
    let nextCell = "—";
    if (!closed && (p.next_action_zh || p.next_action)) {
      const act = p.next_action || "";
      const cls = act === "clear" ? "na-clear"
        : act === "half" ? "na-half"
        : act === "watch" ? "na-watch"
        : "na-hold";
      const tip = (p.next_action_note || "").replace(/"/g, "&quot;");
      const trig = p.trigger_price != null ? ` · ${p.trigger_price}` : "";
      nextCell = `<span class="next-act ${cls}" title="${tip}">${p.next_action_zh || act}${trig}</span>`;
      if (p.deep_clear_price != null && (act === "watch" || act === "hold") && p.half_anchor_price != null) {
        nextCell += `<div class="meta na-sub">深回撤 ${p.deep_clear_price}</div>`;
      }
    }
    const lots = Array.isArray(p.lots) ? p.lots : [];
    let lotsCell = "—";
    if (lots.length > 1) {
      lotsCell = `<div class="pos-lots" title="FIFO 分批">${lots.map((l) =>
        `<div class="lot-line">${l.buy_date || "—"} · ${l.buy_price}×${l.qty}</div>`
      ).join("")}</div>`;
    } else if (lots.length === 1) {
      lotsCell = `<span class="meta">1批</span>`;
    } else if (!closed && Number(p.lot_count || 0) > 1) {
      lotsCell = `<span class="meta">${p.lot_count}批</span>`;
    }
    return `
        <tr class="${closed ? "pos-closed" : ""}">
          <td data-label="状态">${p.status || (closed ? "今日已平" : "持仓")}</td>
          <td data-label="名称">${tickerHtml(p.name, p.code)}</td>
          <td data-label="代码">${p.code || ""}</td>
          <td data-label="买价">${p.buy_price ?? ""}</td>
          <td data-label="分批">${lotsCell}</td>
          <td data-label="股数">${qtyLabel}</td>
          <td data-label="成本">${fmtMoney(p.cost)}</td>
          <td data-label="现价">${p.last ?? "—"}${closed ? "" : (p.last_pct == null ? "" : " " + fmtPct(p.last_pct))}</td>
          <td data-label="已减价">${halfCell}</td>
          <td data-label="下一动作">${nextCell}</td>
          <td data-label="市值">${fmtMoney(p.market)}</td>
          <td data-label="日线">${closed ? "—" : trendCell}</td>
          <td data-label="当日" class="${dayCls}">${dayRow == null ? "—" : fmtMoney(dayRow)}</td>
          <td data-label="浮盈" class="${(p.pnl || 0) >= 0 ? "up" : "down"}">${closed ? "—" : fmtMoney(p.pnl)}${closed || p.pnl_pct == null ? "" : ` (${(p.pnl_pct > 0 ? "+" : "") + p.pnl_pct}%)`}</td>
          <td data-label="已实现" class="${realizedRow >= 0 ? "up" : "down"}">${realizedRow ? fmtMoney(realizedRow) : "—"}</td>
          <td class="m-actions">${actions}</td>
        </tr>`;
  }).join("") || `<tr><td colspan="16" class="meta">暂无仓位，先记账</td></tr>`;
  if (currentMain === "pos" && typeof ensurePosLhb === "function") ensurePosLhb(false);
  if (currentMain === "pos" && typeof ensurePosDiary === "function") ensurePosDiary(false);
  if (currentMain === "pos" && typeof ensurePosCal === "function") ensurePosCal(false);
  paintAuctionStrategy(d);
  paintEmotionWave(d);
  paintSeasonality(d);
  paintElliott(d);
  if (d.fund_flow) paintFundFlow(d);
}
