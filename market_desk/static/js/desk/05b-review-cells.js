/**
 * Render the gate ledger: each gate / flag vs peers without it (display only).
 * @param {object} gl - summary.gate_ledger from /api/review.
 */
function paintGateLedger(gl) {
  const box = document.getElementById("revGateLedger");
  if (!box) return;
  const rows = (gl && gl.rows) || [];
  if (!gl || !gl.ok || !rows.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const fmt = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
  const body = rows.map((r) => {
    const scope = r.scope === "market" ? "市场" : "个股";
    const basis = r.scope === "market" ? "对无此闸门日" : `同日对照${r.peer_n}`;
    const tip = `${r.polarity > 0 ? "期望更好" : "期望更差"} · ${scope}级 · 依据：${basis}`;
    return `<tr class="gl-${escAttr(r.tone)}" title="${escAttr(tip)}">`
      + `<td>${escAttr(r.key)}</td><td class="num">${r.n}</td><td class="num">${r.days}</td>`
      + `<td class="num">${r.win3}%</td><td class="num">${fmt(r.d3)}</td>`
      + `<td class="num">${fmt(r.metric)}</td><td>${escAttr(r.verdict)}</td></tr>`;
  }).join("");
  box.hidden = false;
  box.innerHTML =
    `<div class="hd">闸门账本<button type="button" class="q" data-term="闸门账本">?</button> · ${escAttr(gl.note || "")}</div>`
    + `<table class="gl-tbl"><thead><tr><th>闸门/标记</th><th>样本</th><th>天数</th>`
    + `<th>三日胜率</th><th>三日均值</th><th>差值</th><th>结论</th></tr></thead>`
    + `<tbody>${body}</tbody></table>`;
}

/**
 * Render the edge shadow ledger: reclaim entry, intraday breaker, reweight (display only).
 * @param {object} es - summary.edge_shadow from /api/review.
 */
function paintEdgeShadow(es) {
  const box = document.getElementById("revEdgeShadow");
  if (!box) return;
  if (!es || !es.ok) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const fmt = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
  const pct = (v) => (v == null ? "—" : `${v}%`);
  const tbl = (head, rows) =>
    `<table class="gl-tbl"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>`
    + `<tbody>${rows.join("")}</tbody></table>`;
  const reclaim = (es.reclaim || []).map((r) =>
    `<tr class="gl-${escAttr(r.tone)}"><td>${escAttr(r.key)}</td><td class="num">${r.n}/${r.days}天</td>`
    + `<td class="num">${fmt(r.touch_d3)} · ${pct(r.touch_win)}</td>`
    + `<td class="num">${r.reclaim_n} · ${fmt(r.reclaim_d3)}</td>`
    + `<td class="num">${r.skip_n} · ${fmt(r.skip_d3)}</td>`
    + `<td class="num">${fmt(r.delta)}</td><td>${escAttr(r.verdict)}</td></tr>`);
  const breaker = (es.breaker || []).map((r) =>
    `<tr class="gl-${escAttr(r.tone)}"><td>已亮均值 ≤ ${r.level}%</td><td class="num">${r.n}/${r.days}天</td>`
    + `<td class="num">${r.lit_n}</td><td class="num">${fmt(r.blocked_d3)} · ${pct(r.blocked_win)}</td>`
    + `<td class="num">${r.kept_n} · ${fmt(r.kept_d3)}</td>`
    + `<td class="num">${fmt(r.diff)}</td><td>${escAttr(r.verdict)}</td></tr>`);
  const reweight = (es.reweight || []).map((r) =>
    `<tr class="gl-${escAttr(r.tone)}"><td>${escAttr(r.key)}</td><td>${r.polarity > 0 ? "加权" : "降权"}</td>`
    + `<td class="num">${r.n}/${r.days}天</td><td class="num">${fmt(r.excess)}</td><td>${escAttr(r.verdict)}</td>`
    + `<td class="num">${r.disc_n} · ${fmt(r.disc_excess)}</td></tr>`);
  box.hidden = false;
  box.innerHTML =
    `<div class="hd">进场影子<button type="button" class="q" data-term="进场影子">?</button> · ${escAttr(es.note || "")}</div>`
    + `<div class="meta">站回再买：触价后等现价站回分时均价再买（没站回 = 不买）</div>`
    + tbl(["口径", "触价卡", "触价即买 三日·胜率", "站回 只·三日", "未站回 只·三日", "每卡差值", "结论"], reclaim)
    + `<div class="meta">当日熔断：当天先亮的卡平均浮亏到阈值后，不再亮新卡</div>`
    + tbl(["阈值", "被拦", "其中亮过", "被拦 三日·胜率", "放行 只·三日", "差值", "结论"], breaker)
    + `<div class="meta">降权：同日对照超额（样本外判定；发现期仅参考）</div>`
    + tbl(["规则", "方向", "样本外", "同日超额", "结论", "发现期 只·超额"], reweight);
}

/**
 * Render the viewer's daily discipline scorecard (chase / stops / rhythm).
 * @param {object} dc - summary.discipline from /api/review.
 */
function paintDiscipline(dc) {
  const box = document.getElementById("revDiscipline");
  if (!box) return;
  if (!dc || !dc.ok) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const fmt = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
  const secs = dc.sections || {};
  const secTxt = [["buy", "买入"], ["stop", "止损"], ["rhythm", "节奏"]]
    .filter(([k]) => secs[k])
    .map(([k, lab]) => `${lab} ${secs[k].pts}/${secs[k].max}`)
    .join(" · ");
  const rows = (dc.items || []).map((it) => {
    const tone = it.credit >= 1 && !(it.flags || []).length ? "good" : it.credit > 0 ? "mid" : "bad";
    if (it.kind === "stop") {
      const cost = it.label === "扛单" && it.vs_signal != null ? ` · 现价较信号 ${fmt(it.vs_signal)}` : "";
      return `<tr class="gl-${tone}"><td>止损</td><td>${escAttr(it.name)}</td>`
        + `<td>${escAttr(it.rule || "")}</td><td class="num">${it.sold}/${it.need}</td>`
        + `<td>${escAttr(it.label + cost)}</td></tr>`;
    }
    const flags = (it.flags || []).join("、");
    return `<tr class="gl-${tone}"><td>买入</td><td>${escAttr(it.name)}</td>`
      + `<td class="num">${it.plan ?? "—"} → ${it.fill ?? "—"}</td><td class="num">${fmt(it.chase)}</td>`
      + `<td>${escAttr(it.label + (flags ? ` · ${flags}` : ""))}</td></tr>`;
  }).join("");
  const hist = (dc.hist || []).slice(-10)
    .map((h) => `<span class="disc-h" title="${escAttr(h.day)} ${escAttr(h.grade || "")}">${escAttr(h.day.slice(5))} <b>${h.score}</b></span>`)
    .join("");
  const score = dc.score == null ? "—" : dc.score;
  box.hidden = false;
  box.innerHTML =
    `<div class="hd">纪律审计卡<button type="button" class="q" data-term="纪律审计卡">?</button> · `
    + `<span class="disc-score disc-${escAttr(dc.tone || "low")}">${score}</span> ${escAttr(dc.grade || "")}`
    + (secTxt ? ` · ${escAttr(secTxt)}` : "") + `</div>`
    + `<div class="meta">${escAttr(dc.line || "")}</div>`
    + (rows
      ? `<table class="gl-tbl"><thead><tr><th>类型</th><th>标的</th><th>计划→成交 / 规则</th>`
        + `<th>追价 / 卖出量</th><th>判定</th></tr></thead><tbody>${rows}</tbody></table>`
      : "")
    + (hist ? `<div class="disc-hist">近期：${hist}</div>` : "");
}

/**
 * Render the viewer's chase cost: buy fills vs plan price (display only).
 * @param {object} cc - summary.chase_cost from /api/review.
 */
function paintChaseCost(cc) {
  const box = document.getElementById("revChaseCost");
  if (!box) return;
  const items = (cc && cc.items) || [];
  if (!cc || !cc.ok || !items.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  const fmt = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`);
  const warn = Number(cc.warn_pct || 1.5);
  const body = items.map((it) => {
    const tone = it.chase >= warn ? "bad" : it.chase >= 0.5 ? "mid" : "good";
    return `<tr class="gl-${tone}">`
      + `<td>${escAttr(it.trade_date)}</td><td>${escAttr(it.name || it.code)}</td>`
      + `<td class="num">${it.plan}</td><td class="num">${it.fill}</td>`
      + `<td class="num">${fmt(it.chase)}</td></tr>`;
  }).join("");
  const cost = cc.cost_d3 == null ? "" : ` · 三日少赚 ${Number(cc.cost_d3).toFixed(2)} 个点（${cc.cost_n} 笔已出结果）`;
  box.hidden = false;
  box.innerHTML =
    `<div class="hd">追价成本<button type="button" class="q" data-term="追价成本">?</button> · `
    + `${escAttr(cc.note || "")} · 中位 ${fmt(cc.median)}${escAttr(cost)}</div>`
    + `<table class="gl-tbl"><thead><tr><th>日期</th><th>标的</th><th>计划价</th>`
    + `<th>成交价</th><th>追价</th></tr></thead><tbody>${body}</tbody></table>`;
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
    const cvChips = (r.cv_tags || []).map((t) =>
      ` <span class="rev-chip cv-${t.tone === "good" ? "good" : "warn"}" title="${escAttr(t.title || "")}">${escAttr(t.label || "")}</span>`
    ).join("");
    const st = String(r.signal_type || "");
    const pickChk = (r.id != null && (st === "buy" || st.startsWith("buy_")))
      ? `<input type="checkbox" class="rev-pick-chk" data-id="${escAttr(String(r.id))}" title="加入纠结对比"${revPickIds.has(String(r.id)) ? " checked" : ""} />`
      : "";
    return pickChk
      + `${tickerHtml(r.name, r.code, "", { signal_at: r.signaled_at || "", rev_id: r.id })}`
      + revTrendChips(r)
      + cvChips
      + histBtn
      + `${ctx.liveHtml}${ctx.markHtml}${ctx.cautionHtml}`;
  }
  if (col === "score") {
    const st = String(r.signal_type || "");
    if (!(st === "buy" || st.startsWith("buy_"))) return "";
    const it = revScores.items[String(r.id)];
    if (!it) return revScores.loading ? `<span class="meta">…</span>` : `<span class="meta">—</span>`;
    const chk = it.check || {};
    const lists = [
      (chk.hard || []).length ? "硬伤：" + chk.hard.join("、") : "",
      (chk.risk || []).length ? "风险：" + chk.risk.join("、") : "",
      (chk.plus || []).length ? "加分：" + chk.plus.join("、") : "",
    ].filter(Boolean).join("\n");
    const tip = (revScores.live ? "实时清单" : "信号当时的清单") + "，点开看明细" + (lists ? "\n" + lists : "");
    return `<button type="button" class="rev-score-btn chk-${escAttr(chk.tone || "mid")}" data-id="${escAttr(String(r.id))}" title="${escAttr(tip)}">`
      + `<b>${escAttr(chk.label || "—")}</b></button>`
      + (revScores.live ? pickPosHtml(it.position) : "");
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
  if (col === "excess") {
    const ex = r.excess_d3_pct;
    if (ex == null) return "—";
    const sgn = (v) => (Number(v) > 0 ? "+" : "") + Number(v).toFixed(2);
    const part = Number(r.bench_sessions || 0) < 3 ? `（未满三日，已过 ${r.bench_sessions || 0} 日）` : "";
    const tip = `三日 ${sgn(r.outcome_day3_pct)}% − 中证1000 同窗口 ${sgn(r.bench_d3_pct)}%${part}`;
    return `<span class="${ex >= 0 ? "up" : "down"}" title="${tip}">${sgn(ex)}%${part ? "*" : ""}</span>`;
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

function paintReviewSession(hint) {
  const el = document.getElementById("revSession");
  if (!el) return;
  const h = hint || {};
  if (!h.ok) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.className = "rev-session" + (h.level === "warn" ? " warn" : "");
  const counts = `今日买点 ${h.buy_n ?? 0}`
    + ` · 高于计划价 ${h.above_plan_n ?? 0}`;
  const tips = (h.tips || []).map((t) => `<li>${escAttr(String(t))}</li>`).join("");
  el.innerHTML = `<div class="hd">${escAttr(h.now || "")} · ${escAttr(h.session || "")}</div>`
    + `<div>${counts}</div>`
    + (tips ? `<ul>${tips}</ul>` : "");
}
