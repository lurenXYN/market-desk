let GLOSSARY = {};
let _glossaryLoading = null;
async function ensureGlossary(force) {
  if (!force && Object.keys(GLOSSARY || {}).length > 40) return true;
  if (_glossaryLoading) return _glossaryLoading;
  _glossaryLoading = (async () => {
    try {
      const r = await fetch("/api/glossary", { credentials: "same-origin" });
      if (!r.ok) return false;
      const g = await r.json();
      if (g && typeof g === "object" && !Array.isArray(g) && !g.detail) {
        GLOSSARY = Object.assign({}, GLOSSARY, g);
        return Object.keys(GLOSSARY).length > 0;
      }
    } catch (e) {
      return false;
    } finally {
      _glossaryLoading = null;
    }
    return Object.keys(GLOSSARY || {}).length > 0;
  })();
  return _glossaryLoading;
}
function showTermPop(btn, key, g) {
  let pop = document.getElementById("termPop");
  if (!pop) {
    pop = document.createElement("div");
    pop.id = "termPop";
    pop.className = "term-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-live", "polite");
    document.body.appendChild(pop);
  }
  if (!btn) return;
  const mean = (g && g.mean) ? g.mean : "（暂无摘要）";
  const algo = (g && g.algo) ? g.algo : "（暂无算法说明）";
  pop.innerHTML = `<h4>${key || "术语"}</h4>`
    + `<p class="mean">${mean}</p>`
    + `<p class="algo">${algo}</p>`
    + `<p class="meta" style="margin-top:8px">Alt+点击 ? 打开术语表</p>`;
  pop.hidden = false;
  const r = btn.getBoundingClientRect();
  const width = Math.min(380, window.innerWidth - 16);
  let left = Math.min(r.left, window.innerWidth - width - 8);
  left = Math.max(8, left);
  pop.style.left = left + "px";
  pop.style.top = "0px";
  const h = pop.offsetHeight || 160;
  let top = r.bottom + 8;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - 8 - h);
  pop.style.top = top + "px";
}
const phaseColor = { "恐慌": "var(--fear)", "分歧": "var(--div)", "发酵": "var(--ferm)", "高潮": "var(--climax)" };
const fmtPct = (v) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Number(v);
  const s = (n > 0 ? "+" : "") + n.toFixed(2) + "%";
  return `<span class="${n >= 0 ? "up" : "down"}">${s}</span>`;
};
const fmtMoney = (v) => {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(2);
};
const fmtPx = (v, kind) => {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(kind === "etf" ? 3 : 2);
};
const xueqiuUrl = (code) => {
  const c = String(code || "").replace(/\D/g, "").padStart(6, "0");
  if (c.length !== 6) return "";
  const prefix = /^[569]/.test(c) ? "SH" : "SZ";
  return `https://xueqiu.com/S/${prefix}${c}`;
};
const tickerHtml = (name, code, extra, opts) => {
  const c = String(code || "").replace(/\D/g, "");
  const label = name || c || "—";
  if (!c) return label;
  const xq = xueqiuUrl(c);
  const sig = opts && opts.signal_at
    ? ` data-signal-at="${String(opts.signal_at).replace(/"/g, "")}"`
    : "";
  const revId = opts && opts.rev_id != null && opts.rev_id !== ""
    ? ` data-rev-id="${String(opts.rev_id).replace(/"/g, "")}"`
    : "";
  return `<a class="ticker js-chart" href="${xq}" data-code="${c}" data-name="${label}"${sig}${revId} title="点开看图与信号描述">${label}</a>`
    + `<span class="meta"> ${c}${extra || ""}</span>`
    + (xq ? ` <a class="xq-link" href="${xq}" target="_blank" rel="noopener noreferrer" title="雪球">雪球</a>` : "");
};
const dragonItemsHtml = (items) => {
  const order = ["main", "side", "link"];
  const labelOf = (scope, board) => {
    if (scope === "side") return `支线${board ? ` · ${board}` : ""}`;
    if (scope === "link") return `联动${board ? ` · ${board}` : ""}`;
    return `主线${board ? ` · ${board}` : ""}`;
  };
  const groups = new Map();
  (items || []).forEach((it) => {
    const scope = String(it.dragon_scope || "main");
    const board = String(it.source_board || "").trim();
    const key = `${scope}|${board}`;
    if (!groups.has(key)) {
      groups.set(key, { scope, board, items: [] });
    }
    groups.get(key).items.push(it);
  });
  const sorted = Array.from(groups.values()).sort((a, b) => {
    const ia = order.indexOf(a.scope);
    const ib = order.indexOf(b.scope);
    return (ia < 0 ? 9 : ia) - (ib < 0 ? 9 : ib);
  });
  if (sorted.length <= 1) {
    return (items || []).map(recCard).join("");
  }
  return sorted.map((g) => {
    const tip = g.scope === "main" ? "可到位" : "只观察";
    return `<div class="dragon-group">`
      + `<div class="dg-hd">${escAttr(labelOf(g.scope, g.board))}`
      + `<span class="meta"> · ${g.items.length}只 · ${tip}</span></div>`
      + `<div class="rec-grid">${g.items.map(recCard).join("")}</div>`
      + `</div>`;
  }).join("");
};
const recCard = (it) => {
  const kind = it.kind || "stock";
  const wait = !it.ready;
  const near = !!it.near_entry;
  const probe = !!it.probe_ok;
  const buy = it.buy_price != null ? it.buy_price : it.last;
  let trendHtml = "";
  if (it.trend_ok) {
    trendHtml = `<div class="trend-ok">日线 · 上升趋势（MA5 ${it.ma5 ?? "—"} / MA20 ${it.ma20 ?? "—"}）</div>`;
  } else if (it.trend_pending) {
    trendHtml = `<div class="trend-warn">日线 · ${it.trend || "暂未取到"}（不据此否决）</div>`;
  } else if (it.trend_down) {
    trendHtml = `<div class="trend-warn">日线 · 下降趋势</div>`;
  } else if (it.trend) {
    trendHtml = `<div class="trend-warn">日线 · ${it.trend}（软减分，不关现买）</div>`;
  }
  const batch = (it.batch_plan || []).map((b) => {
    const qty = Number(b.qty) || 100;
    const lotPx = buy != null ? buy : (it.last != null ? it.last : "");
    const cost = b.approx_cost != null ? `≈${b.approx_cost}` : "";
    return `<button type="button" class="batch-lot"
          data-code="${it.code || ""}"
          data-name="${it.name || ""}"
          data-price="${lotPx ?? ""}"
          data-suggest="${buy ?? ""}"
          data-last="${it.last ?? ""}"
          data-qty="${qty}"
          data-lot="${b.lot || ""}"
          data-label="${b.label || ""}"
          data-desk-source="${it.desk_source || (it.independent_pop ? "independent_pop" : "")}"
          title="一键记入独立 lot：${b.label || ""} ${qty}股">第${b.lot}·${b.label}${qty}股${cost}</button>`;
  }).join(" ");
  const batchHtml = batch
    ? `<div class="rec-batch"><button type="button" class="q" data-term="分批计划">?</button> 分批 ${batch}</div>`
    : "";
  const rp = it.risk_plan || {};
  const riskHtml = rp.qty != null
    ? `<div class="rec-risk"><button type="button" class="q" data-term="风险股数">?</button> 建议 ${rp.qty} 股（${rp.lots || 0}手）· 约 ${rp.approx_cost ?? "—"} · ${rp.note || ""}</div>`
    : "";
  const qtyForBook = (rp.qty && rp.qty > 0) ? rp.qty : (it.qty || 100);
  const pinBtn = `<button type="button" class="pos-btn rec-pin"
            data-code="${it.code || ""}"
            data-name="${it.name || ""}"
            data-suggest="${buy ?? ""}"
            data-stop="${it.stop_price ?? ""}"
            data-chase="${it.chase_price ?? ""}">钉自选</button>`;
  const actionHtml = `<div class="rec-actions">
            <button type="button" class="pos-btn rec-book"
              data-code="${it.code || ""}"
              data-name="${it.name || ""}"
              data-price="${buy ?? ""}"
              data-suggest="${buy ?? ""}"
              data-last="${it.last ?? ""}"
              data-qty="${qtyForBook}"
              data-desk-source="${it.desk_source || (it.independent_pop ? "independent_pop" : "")}"
              title="确认后写入本地仓位；若有当日买入信号会同步记已交易">记入仓位</button>
            <button type="button" class="pos-btn rec-pos"
              data-code="${it.code || ""}"
              data-name="${it.name || ""}"
              data-price="${it.last ?? buy ?? ""}"
              data-qty="${qtyForBook}"
              title="只带到仓位页表单，不自动保存">填到仓位页</button>
            ${pinBtn}
          </div>`;
  const cardCls = [
    "rec-card",
    wait ? "wait" : "buy-ready",
    probe ? "probe-ok" : "",
    near && !wait ? "near-entry" : (near ? "near-touch" : ""),
    it.fly_warn ? "fly-warn" : "",
  ].filter(Boolean).join(" ");
  const relaxed = !!it.ready_relaxed;
  const fly = !!it.fly_warn;
  const entryBadge = (() => {
    if (fly) {
      return `<span class="entry-badge entry-badge-fly" title="${it.fly_note || "半仓试探窗口，再等可能飞"}">将飞·半仓</span>`;
    }
    if (!wait && relaxed) {
      return `<span class="entry-badge entry-badge-probe" title="价带放松：未到不追半仓 ready">可买·半</span>`;
    }
    if (!wait && near) {
      return `<span class="entry-badge" title="确认充分可现买">可买·满</span>`;
    }
    if (!wait) {
      return `<span class="entry-badge">可买·满</span>`;
    }
    if (probe) {
      return `<span class="entry-badge entry-badge-probe">可小仓试探</span>`;
    }
    if (near) {
      return `<span class="entry-badge entry-badge-soft" title="到位但未 ready：观察">观察·到位</span>`;
    }
    return "";
  })();
  const fails = (it.confirm_fail || []).filter(Boolean);
  const softFails = (it.confirm_soft || []).filter(Boolean);
  const minute = it.minute || {};
  const gateBits = [];
  if (it.minute_pending || (minute.ok == null && (it.ready || near))) {
    gateBits.push("分时未验·软");
  } else if (minute.label && minute.ok === false) {
    gateBits.push(minute.label);
  } else if (minute.label && minute.ok === true) {
    gateBits.push(minute.label);
  }
  if (it.block_ready) gateBits.push("禁现买");
  fails.forEach((f) => { if (!gateBits.includes(f)) gateBits.push(f); });
  softFails.forEach((f) => {
    const lab = `${f}·软`;
    if (!gateBits.includes(f) && !gateBits.includes(lab)) gateBits.push(lab);
  });
  const gateHtml = gateBits.length
    ? `<div class="rec-gate">${gateBits.map((g) => `<span class="gate-chip">${g}</span>`).join("")}</div>`
    : "";
  const bp = it.buy_progress || {};
  const bpSteps = (bp.steps || []).map((s) => {
    let cls = "bp-step";
    if (s.ok === true) cls += " ok";
    else if (s.ok === false) cls += " bad";
    else if (s.soft) cls += " soft";
    const tip = s.detail ? ` title="${String(s.detail).replace(/"/g, "")}"` : "";
    return `<span class="${cls}"${tip}>${s.label || ""}</span>`;
  }).join("");
  const progressHtml = (bp.summary || bpSteps)
    ? `<div class="buy-progress"><span class="bp-sum">${bp.summary || "距可买"}</span>`
      + (bpSteps ? `<span class="bp-steps">${bpSteps}</span>` : "")
      + `</div>`
    : (!wait && (it.confirm_fail || []).length
      ? `<div class="buy-progress"><span class="bp-sum">还差：${(it.confirm_fail || []).slice(0, 3).join(" · ")}</span></div>`
      : "");
  return `<article class="${cardCls}">
        <div class="role"><span>${it.role_label || (kind === "etf" ? "ETF" : "个股")}</span>${entryBadge}${fmtPct(it.pct)}</div>
        <div class="nm">${tickerHtml(it.name, it.code)}</div>
        ${trendHtml}
        ${(() => {
      if (kind === "etf") return "";
      if (it.holder_num == null && it.holder_chg_pct == null) return "";
      const tip = [
        it.holder_end ? `统计截止 ${it.holder_end}` : "",
        it.holder_notice ? `公告 ${it.holder_notice}` : "",
        it.holder_prev != null ? `上次 ${fmtHolderNum(it.holder_prev)}` : "",
        "季度披露，非实时",
      ].filter(Boolean).join(" · ");
      const chg = it.holder_chg_pct;
      let chgHtml = "";
      if (chg != null && !Number.isNaN(Number(chg))) {
        const n = Number(chg);
        const cls = n < 0 ? "up" : (n > 0 ? "down" : "");
        chgHtml = ` <span class="${cls}">${n > 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
      }
      return `<div class="rec-holder" title="${tip}"><button type="button" class="q" data-term="股东户数">?</button> 股东 ${fmtHolderNum(it.holder_num)}${chgHtml}</div>`;
    })()}
        ${progressHtml}
        ${gateHtml}
        <div class="rec-prices">
          <div><div class="lab">现价</div><div class="val">${fmtPx(it.last, kind)}</div></div>
          <div class="buy"><div class="lab">${!wait ? "建议买·到位" : (probe ? "建议买·试探" : "建议买")}</div><div class="val">${fmtPx(buy, kind)}</div></div>
          <div><div class="lab">回踩</div><div class="val">${fmtPx(it.wait_price, kind)}</div></div>
          <div class="stop"><div class="lab">止损</div><div class="val">${fmtPx(it.stop_price, kind)}</div></div>
          <div class="chase"><div class="lab">不追</div><div class="val">${fmtPx(it.chase_price, kind)}</div></div>
        </div>
        <div class="rec-why">${(() => {
      const why = String(it.dragon_why || "").trim();
      const rest = String(it.reason || "").trim();
      if (why && rest.startsWith(why)) {
        const tail = rest.slice(why.length).replace(/^[。；;\s]+/, "");
        return `<div class="dragon-why">${why}</div>`
          + (tail ? `<div class="dragon-timing">${tail}</div>` : "");
      }
      if (why) {
        return `<div class="dragon-why">${why}</div>`
          + (rest && rest !== why ? `<div class="dragon-timing">${rest}</div>` : "");
      }
      return rest;
    })()}</div>
        ${(() => {
      const wb = it.whitebox || {};
      const contribs = wb.contribs || [];
      if (!contribs.length && wb.score_adj == null) return "";
      const adj = Number(wb.score_adj);
      const adjTxt = Number.isFinite(adj)
        ? `白盒 ${adj >= 0 ? "+" : ""}${adj.toFixed(1)}`
        : "白盒";
      const chips = contribs.slice(0, 4).map((c) => {
        const v = Number(c.value) || 0;
        const pct = Math.min(100, Math.abs(v) / 0.6 * 100);
        const cls = v < 0 ? "neg" : "pos";
        return `<span class="chip">${c.label || c.key}<span class="bar"><i class="${cls}" style="width:${pct.toFixed(0)}%"></i></span>${v >= 0 ? "+" : ""}${v.toFixed(2)}</span>`;
      }).join("");
      return `<div class="rec-wb">${adjTxt}${chips ? `<div class="wb-mini">${chips}</div>` : ""}</div>`;
    })()}
        ${riskHtml}
        ${batchHtml}
        ${actionHtml}
      </article>`;
};
const sellCard = (it) => {
  const kind = it.kind || "stock";
  const sellNow = !!it.ready;
  const t1 = !!it.t1_locked;
  const sell = it.sell_price != null ? it.sell_price : it.last;
  const pnl = it.pnl_pct;
  const pnlHtml = pnl == null ? "—" : `<span class="${pnl >= 0 ? "up" : "down"}">${(pnl > 0 ? "+" : "") + Number(pnl).toFixed(2)}%</span>`;
  const mode = it.exit_mode || "hold";
  const modeTip = mode === "clear" ? "建议清仓" : (mode === "half" ? "建议先减一半" : "");
  const sellPct = it.sell_pct != null && it.sell_pct > 0
    ? ` · ${modeTip || ("建议卖 " + it.sell_pct + "%")}`
    : "";
  const halfQ = it.sell_qty_half != null ? it.sell_qty_half : 0;
  const clearQ = it.sell_qty_clear != null ? it.sell_qty_clear : (it.qty || 0);
  const halfCls = !t1 && mode === "half" ? " pos-btn exit-pref" : " pos-btn";
  const clearCls = !t1 && mode === "clear" ? " pos-btn exit-pref clear" : " pos-btn";
  const actions = t1
    ? `<button type="button" class="pos-btn sell-go" disabled>T+1 隔日可卖</button>`
    : `<button type="button" class="${halfCls.trim()} sell-half" data-id="${it.id || ""}" data-qty="${halfQ}" data-last="${it.last ?? ""}">减半 ${halfQ}股</button>
           <button type="button" class="${clearCls.trim()} sell-clear" data-id="${it.id || ""}" data-qty="${clearQ}" data-last="${it.last ?? ""}">清仓 ${clearQ}股</button>
           <button type="button" class="pos-btn sell-go">去仓位</button>`;
  let trendHtml = "";
  if (it.trend_ok) {
    trendHtml = `<div class="trend-ok">日线 · 上升趋势（MA5 ${it.ma5 ?? "—"} / MA20 ${it.ma20 ?? "—"}）</div>`;
  } else if (it.trend_pending) {
    trendHtml = `<div class="trend-warn">日线 · ${it.trend || it.daily_trend || "暂未取到"}（不据此否决）</div>`;
  } else if (it.trend_down) {
    trendHtml = `<div class="trend-warn">日线 · 下降趋势</div>`;
  } else if (it.trend || it.daily_trend) {
    trendHtml = `<div class="trend-warn">日线 · ${it.trend || it.daily_trend}</div>`;
  }
  let vsHtml = "";
  if (it.vs_carrier != null) {
    const cls = Number(it.vs_carrier) >= 0 ? "up" : "down";
    vsHtml = `<div class="meta">相对主线载体 <span class="${cls}">${Number(it.vs_carrier) > 0 ? "+" : ""}${Number(it.vs_carrier).toFixed(1)}pt</span>${it.carrier_falling ? " · 载体走弱" : ""}</div>`;
  } else if (it.carrier_falling) {
    vsHtml = `<div class="meta">主线载体走弱</div>`;
  } else if (it.carrier_compare_absent) {
    vsHtml = `<div class="meta">无载体对比（主线无精确ETF/报价）</div>`;
  }
  if (it.partial_done && !sellNow) {
    vsHtml += `<div class="meta">今日已减 · 余仓盯止损</div>`;
  }
  if (it.regret_hold) {
    vsHtml += `<div class="meta">反悔窗 · 继续观察</div>`;
  }
  if (it.half_anchor_price != null) {
    vsHtml += `<div class="meta">减半价 ${fmtPx(it.half_anchor_price, kind)}${it.deep_clear_price != null ? ` · 深回撤线 ${fmtPx(it.deep_clear_price, kind)}` : ""}</div>`;
  }
  if (it.next_action_zh) {
    const naCls = it.next_action === "clear" ? "na-clear"
      : it.next_action === "half" ? "na-half"
      : it.next_action === "watch" ? "na-watch"
      : "na-hold";
    vsHtml += `<div class="meta">下一动作 <span class="next-act ${naCls}">${it.next_action_zh}</span>${it.trigger_price != null ? ` · 触发 ${fmtPx(it.trigger_price, kind)}` : ""}${it.next_action_note && !sellNow ? ` · ${it.next_action_note}` : ""}</div>`;
  }
  let tuneHtml = "";
  const tags = Array.isArray(it.tune_tags) ? it.tune_tags.slice(0, 4) : [];
  const st = it.sell_tune || {};
  if (tags.length || st.review_note || st.mfe_note) {
    const chips = tags.map((t) => `<span class="chip">${t}</span>`).join("");
    const note = [st.review_note, st.mfe_note].filter(Boolean).slice(0, 1).join(" · ");
    tuneHtml = `<div class="meta tune-tags">${chips}${note ? ` <span class="dim">${note}</span>` : ""}</div>`;
  }
  if (it.minute_sell && it.minute_sell.label) {
    tuneHtml += `<div class="meta">分时卖 · ${it.minute_sell.label}${it.minute_pending ? "（软）" : ""}</div>`;
  } else if (it.minute_gate) {
    tuneHtml += `<div class="meta">分时卖 · 分时未验（软）</div>`;
  }
  let bufHtml = "";
  const track = it.open_buffer_track;
  const phase = it.open_buffer_phase;
  if (track === "must") {
    bufHtml = `<div class="meta"><span class="entry-badge" style="border-color:#f87171;color:#fecaca">开盘必卖</span>${phase === "auction_preview" ? " · 竞价预告" : ""}</div>`;
  } else if (track === "watch" && phase === "watching") {
    bufHtml = `<div class="meta"><span class="entry-badge entry-badge-soft">开盘观察中</span> · 缓冲窗内不催软卖</div>`;
  } else if (track === "watch" && phase === "released") {
    bufHtml = `<div class="meta"><span class="entry-badge entry-badge-probe">缓冲后持有</span></div>`;
  } else if (track === "watch" && phase === "armed") {
    bufHtml = `<div class="meta"><span class="entry-badge">缓冲后仍卖</span></div>`;
  }
  const minBuf = it.open_buffer_minute || (it.open_buffer_judge && it.open_buffer_judge.minute);
  if (minBuf && minBuf.note) {
    bufHtml += `<div class="meta">分时缓冲 · ${minBuf.note}${minBuf.sample_n ? `（n=${minBuf.sample_n}）` : ""}</div>`;
  }
  return `<article class="rec-card${sellNow ? " sell-now" : " wait"}${t1 ? " t1-lock" : ""}${phase === "watching" ? " near-touch" : ""}">
        <div class="role"><span>${it.role_label || "仓位"}</span>${fmtPct(it.pct)}</div>
        <div class="nm">${tickerHtml(it.name, it.code, ` · ${it.qty || 0}股${sellPct}`)}</div>
        ${bufHtml}
        <div class="rec-prices">
          <div><div class="lab">现价</div><div class="val">${fmtPx(it.last, kind)}</div></div>
          <div class="sell"><div class="lab">建议卖</div><div class="val">${fmtPx(sell, kind)}</div></div>
          <div><div class="lab">成本</div><div class="val">${fmtPx(it.buy_price, kind)}</div></div>
          <div class="stop"><div class="lab">止损</div><div class="val">${fmtPx(it.stop_price, kind)}</div></div>
          <div><div class="lab">浮盈</div><div class="val">${pnlHtml}</div></div>
        </div>
        ${trendHtml}
        ${vsHtml}
        ${tuneHtml}
        <div class="rec-why">${it.reason || ""}</div>
        <div class="rev-actions" style="margin-top:8px">${actions}</div>
      </article>`;
};
const termHtml = (name) => {
  if (!name) return "";
  const key = ({
    "炸板%": "炸板",
    "晋级%": "晋级率",
    "1→2%": "晋级1→2",
    "2→3%": "晋级2→3",
    "溢价%": "昨停溢价",
  })[name] || name;
  if (!GLOSSARY[key]) return name;
  return `${name}<button type="button" class="q" data-term="${key}">?</button>`;
};
function paintToastStrip(rows) {
  const el = document.getElementById("toastStrip");
  if (!el) return;
  const list = Array.isArray(rows) ? rows.slice(0, 8) : [];
  if (!list.length) {
    el.className = "toast-strip";
    el.innerHTML = "";
    return;
  }
  el.className = "toast-strip on";
  el.innerHTML = `<div class="hd">最近提醒<button type="button" class="q" data-term="系统通知">?</button></div>`
    + list.map((t) =>
      `<div class="row"><span class="t">${t.ts || ""}</span>`
      + `<span class="title">${t.title || ""}</span>`
      + `<span>${t.body || ""}</span></div>`
    ).join("");
}
function pushPageToast(title, body) {
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);
  const row = { ts, title: title || "提醒", body: body || "" };
  const cur = (lastData && Array.isArray(lastData.recent_toasts))
    ? lastData.recent_toasts.slice()
    : [];
  const next = [row, ...cur].slice(0, 12);
  if (lastData) lastData.recent_toasts = next;
  paintToastStrip(next);
}
const tagClass = (s) => {
  if (s === "尖峰禁追" || s === "禁追") return "ban";
  if (s === "退潮" || s === "相对最冷" || s === "冷冻") return "fade";
  if (s === "冰点") return "ice";
  if (s === "传染预警") return "contagion";
  return "";
};
const statusClass = (s) => ({
  "尖峰禁追": "st-ban", "禁追": "st-ban", "确认中": "st-ok",
  "退潮": "st-fade", "观察": "st-watch",
  "冰点": "st-ice", "冷冻": "st-ice", "相对最冷": "st-cold",
  "传染预警": "st-contagion",
}[s] || "st-watch");
const miniClass = (k, on) => {
  if (!on) return "mini";
  if (k === "传染" || k === "加速" || k === "A杀" || k === "天地") return "mini on warn";
  if (k === "修复" || k === "冰点") return "mini on";
  if (k === "点火" || k === "一波" || k === "二波" || k === "反包") return "mini on hot";
  return "mini on warn";
};
const reasonTerm = (reason) => {
  const raw = reason || "";
  if (raw.includes("炸板")) return termHtml("炸板");
  if (raw.includes("跌停")) return termHtml("跌停");
  if (raw.includes("换手")) return termHtml("换手") + " · " + raw;
  if (raw.includes("涨停")) return termHtml("涨停") + " · " + raw;
  return raw;
};

const arrowClass = (dir) => dir === "up" ? "arrow-up" : (dir === "down" ? "arrow-down" : "arrow-flat");
const bannerClass = (action) =>
  (action === "可买入" || action === "可小仓") ? "buy"
    : (action === "观察回踩" || action === "观察") ? "watch" : "cash";

function fmtDelta(item) {
  const v = item.value;
  const shown = (v === null || v === undefined) ? "—" : v;
  const unit = item.unit || "";
  return `<div class="delta">
        <div class="lab">${termHtml(item.label)}</div>
        <div class="val ${arrowClass(item.dir)}">${item.arrow || "→"} ${shown}${unit}</div>
        <div class="chg ${arrowClass(item.dir)}">${item.text || "较上轮 —"}</div>
      </div>`;
}

function dataAgeText(updatedAt) {
  if (!updatedAt) return "数据尚未刷新";
  const t = updatedAt.replace(" ", "T");
  const then = new Date(t);
  if (Number.isNaN(then.getTime())) return "刷新时间 " + updatedAt;
  const sec = Math.max(0, Math.round((Date.now() - then.getTime()) / 1000));
  return "数据时间 " + updatedAt + " · 已过 " + sec + " 秒";
}

function fmtModClock(ts) {
  const s = String(ts || "").trim();
  if (!s) return "";
  if (s.length >= 19) return s.slice(11, 19);
  if (s.length >= 8 && s.indexOf(":") >= 0) return s.slice(-8);
  return s;
}

function setModStamp(elOrId, ts, opts) {
  const el = typeof elOrId === "string" ? document.getElementById(elOrId) : elOrId;
  if (!el) return;
  const o = opts || {};
  if (o.loading) {
    el.className = "mod-stamp loading";
    el.textContent = o.label ? (`${o.label}刷新中…`) : "刷新中…";
    el.title = o.title || "异步加载中";
    return;
  }
  const clock = fmtModClock(ts);
  if (!clock) {
    el.className = "mod-stamp";
    el.textContent = "";
    el.title = "";
    return;
  }
  const kind = o.kind || "";
  const label = o.label || "刷新";
  el.className = "mod-stamp" + (o.stale ? " stale" : "");
  el.textContent = kind ? `${label} ${clock}（${kind}）` : `${label} ${clock}`;
  el.title = o.title || (String(ts || "") + (kind ? ` · ${kind}` : ""));
}

function paintPosTargetBar(d) {
  // Paint target-vs-actual cost bar + concentration chips on desk + positions.
  const risk = d.risk_overview || d.position_summary || {};
  const sum = d.position_summary || {};
  const target = Number(risk.target_total_cost);
  const cost = Number(sum.cost != null ? sum.cost : 0);
  const hasTarget = Number.isFinite(target) && target > 0;
  const pct = hasTarget ? Math.round((cost / target) * 1000) / 10 : 0;
  const fillPct = hasTarget ? Math.min(100, pct) : 0;
  const dev = risk.target_dev_pct;
  const over = hasTarget && pct > 100;
  const hot = hasTarget && pct >= 115;
  const used = risk.size_used_pct;
  const meta = hasTarget
    ? `实际 ${fmtMoney(cost)} / 目标 ${fmtMoney(target)} · ${pct}%`
      + (dev == null ? "" : `（偏差 ${dev > 0 ? "+" : ""}${dev}%）`)
      + (used == null ? "" : ` · 占账户 ${used}%`)
    : (used == null ? "未设目标总成本" : `占账户 ${used}%`);
  const fillCls = "fill" + (hot ? " hot" : (over ? " over" : ""));
  const showBar = hasTarget || (used != null);
  [
    ["posTargetBar", "posTargetMeta", "posTargetFill"],
    ["posTargetBarPos", "posTargetMetaPos", "posTargetFillPos"],
  ].forEach(([barId, metaId, fillId]) => {
    const bar = document.getElementById(barId);
    const metaEl = document.getElementById(metaId);
    const fill = document.getElementById(fillId);
    if (!bar || !metaEl || !fill) return;
    bar.hidden = !showBar;
    metaEl.textContent = meta;
    fill.className = fillCls;
    fill.style.width = hasTarget ? (fillPct + "%") : (used != null ? Math.min(100, used) + "%" : "0%");
  });
  const topT = risk.top_theme || ((risk.themes || [])[0]);
  const topS = risk.top_single || ((risk.items || [])[0]);
  const themeCap = (risk.caps && risk.caps.max_theme_pct) || 50;
  const singleCap = (risk.caps && risk.caps.max_single_pct) || 35;
  const chips = [];
  if (topT && topT.weight_pct != null) {
    const cls = topT.over_theme ? "chip hot" : (topT.weight_pct >= themeCap * 0.8 ? "chip warn" : "chip");
    chips.push(`<span class="${cls}" title="题材集中度">题材 ${topT.theme || "—"} ${topT.weight_pct}%</span>`);
  }
  if (topS && topS.weight_pct != null) {
    const cls = topS.over_weight ? "chip hot" : (topS.weight_pct >= singleCap * 0.8 ? "chip warn" : "chip");
    chips.push(`<span class="${cls}" title="单票集中度">单票 ${topS.name || topS.code || "—"} ${topS.weight_pct}%</span>`);
  }
  ["posConc", "posConcPos"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (!chips.length) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    el.innerHTML = chips.join("");
  });
}
