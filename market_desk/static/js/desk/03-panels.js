function paintVisible(d) {
  // Always refresh banner/desk chrome from merged snapshot.
  render(d);
}

function snapshotViewForTab(tab) {
  const t = tab || currentMain || "desk";
  if (t === "review" || t === "backtest") return "desk";
  return t;
}

async function ensureFundFlowFull() {
  try {
    const cur = (lastData && lastData.fund_flow) || {};
    if (cur.full_ready && lastFundFlowFullAt && Date.now() - lastFundFlowFullAt < 280000) {
      paintFundFlow({ fund_flow: cur, updated_at: (lastData && lastData.updated_at) || "" });
      return;
    }
    setModStamp("fundStamp", null, { loading: true, label: "近5/10日" });
    const r = await fetch("/api/fund-flow");
    const box = await r.json();
    if (!box || box.ok === false && !box.periods) {
      setModStamp("fundStamp", cur.refreshed_at, { label: "刷新", stale: true });
      return;
    }
    lastFundFlow = box;
    lastFundFlowFullAt = Date.now();
    if (lastData) lastData.fund_flow = box;
    paintFundFlow({ fund_flow: box, updated_at: (lastData && lastData.updated_at) || "" });
  } catch (e) {
    setModStamp("fundStamp", null, { loading: false });
  }
}

function paintEmotionWave(d) {
  const box = d.emotion_wave || {};
  const note = document.getElementById("emotionNote");
  const sum = document.getElementById("emotionSum");
  const body = document.getElementById("emotionBody");
  if (!note || !sum || !body) return;
  setModStamp("emotionStamp", box.refreshed_at || d.updated_at, { label: "刷新" });
  note.textContent = (box.disclaimer || "") + (box.note ? (" " + box.note) : "");
  const pri = box.primary || {};
  sum.innerHTML =
    `<span>相位 <b>${box.phase || "—"}</b></span>`
    + `<span>温度 <b>${box.temperature ?? "—"}</b></span>`
    + (pri.title ? `<span>当前偏 <b>${pri.title}</b>（契合 ${pri.fit ?? "—"}）</span>` : "");
  const stages = box.stages || [];
  if (!stages.length) {
    body.innerHTML = `<div class="meta">暂无情绪浪</div>`;
    return;
  }
  body.innerHTML = `<div class="emo-strip">` + stages.map((st) => {
    const on = pri.id && st.id === pri.id;
    const term = st.title || "";
    const tip = GLOSSARY[term]
      ? ` <button type="button" class="q" data-term="${term}">?</button>`
      : "";
    return `<article class="emo-card${on ? " on" : ""}">
          <div class="emo-step">第${st.step || "—"}浪 · 契合 ${st.fit ?? "—"}</div>
          <b>${term}${tip}</b>
          <div class="meta">${st.fit_note || ""}</div>
          <div class="emo-path">${st.path || ""}</div>
          <div class="emo-risk">否决：${st.invalid || ""}</div>
        </article>`;
  }).join("") + `</div>`;
}

function paintSeasonality(d) {
  const box = d.seasonality || {};
  const note = document.getElementById("seasonNote");
  const sum = document.getElementById("seasonSum");
  const body = document.getElementById("seasonBody");
  if (!note || !sum || !body) return;
  setModStamp("seasonStamp", box.refreshed_at || d.updated_at, { label: "刷新" });
  note.textContent = (box.disclaimer || "") + (box.note ? (" " + box.note) : "");
  const active = box.active || [];
  sum.innerHTML =
    `<span>${box.weekday_note || ""}</span>`
    + `<span>激活 <b>${active.length}</b> 窗</span>`
    + (box.history_note ? `<span class="meta">${box.history_note}</span>` : "");
  const wins = box.windows || [];
  if (!wins.length) {
    body.innerHTML = `<div class="meta">暂无日历窗</div>`;
    return;
  }
  // Active first, then the rest.
  const ordered = wins.slice().sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0));
  body.innerHTML = `<div class="season-grid">` + ordered.map((w) => {
    return `<article class="season-card${w.active ? " on" : ""}">
          <b>${w.active ? "● " : ""}${w.title || ""}</b>
          <div class="when">${w.when || ""}</div>
          <div class="emo-path">${w.path || ""}</div>
          <div class="emo-risk">${w.watch || ""}</div>
        </article>`;
  }).join("") + `</div>`;
}

function elliottChartSvg(chart) {
  const closes = (chart && chart.closes) || [];
  if (closes.length < 2) {
    return `<div class="meta">暂无日线，无法画波浪示意</div>`;
  }
  const w = 720, h = 230, padL = 44, padR = 12, padT = 28, padB = 28;
  const pivots = chart.pivots || [];
  const fine = chart.fine_pivots || [];
  const levels = chart.levels || [];
  const prices = closes.slice();
  pivots.forEach((p) => { if (p.price != null) prices.push(Number(p.price)); });
  fine.forEach((p) => { if (p.price != null) prices.push(Number(p.price)); });
  levels.forEach((lv) => { if (lv.price != null) prices.push(Number(lv.price)); });
  if (chart.invalidation != null) prices.push(Number(chart.invalidation));
  if (chart.last != null) prices.push(Number(chart.last));
  const min = Math.min(...prices.map(Number).filter((v) => !Number.isNaN(v)));
  const max = Math.max(...prices.map(Number).filter((v) => !Number.isNaN(v)));
  const span = max - min || 1;
  const n = closes.length;
  const xAt = (i) => padL + (i / Math.max(1, n - 1)) * (w - padL - padR);
  const yAt = (v) => padT + (1 - (Number(v) - min) / span) * (h - padT - padB);
  const closePath = closes.map((v, i) =>
    `${i ? "L" : "M"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`
  ).join(" ");
  let zig = "";
  if (pivots.length >= 2) {
    zig = pivots.map((p, i) =>
      `${i ? "L" : "M"}${xAt(p.i).toFixed(1)},${yAt(p.price).toFixed(1)}`
    ).join(" ");
  }
  let fineZig = "";
  if (fine.length >= 2) {
    fineZig = fine.map((p, i) =>
      `${i ? "L" : "M"}${xAt(p.i).toFixed(1)},${yAt(p.price).toFixed(1)}`
    ).join(" ");
  }
  let html = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`;
  // Guide levels (Top1 structure).
  levels.forEach((lv) => {
    const y = yAt(lv.price).toFixed(1);
    html += `<line x1="${padL}" x2="${w - padR}" y1="${y}" y2="${y}" stroke="#3a4a60" stroke-width="1" stroke-dasharray="2 3"/>`;
    html += `<text x="${padL + 2}" y="${(Number(y) - 3).toFixed(1)}" fill="#7a8aa0" font-size="10">${escAttr(String(lv.tag || lv.price))}</text>`;
  });
  if (chart.invalidation != null) {
    const y = yAt(chart.invalidation).toFixed(1);
    html += `<line x1="${padL}" x2="${w - padR}" y1="${y}" y2="${y}" stroke="#f87171" stroke-width="1.2" stroke-dasharray="4 3"/>`;
    html += `<text x="${w - padR - 4}" y="${(Number(y) - 3).toFixed(1)}" text-anchor="end" fill="#f87171" font-size="10">否决 ${chart.invalidation}</text>`;
  }
  html += `<path d="${closePath}" fill="none" stroke="#334155" stroke-width="1.2"/>`;
  if (fineZig) {
    html += `<path d="${fineZig}" fill="none" stroke="#64748b" stroke-width="1" stroke-dasharray="3 2" opacity="0.7"/>`;
  }
  if (zig) {
    html += `<path d="${zig}" fill="none" stroke="#38bdf8" stroke-width="2"/>`;
  }
  // Pivot dots only (wave numbers drawn below when available).
  pivots.forEach((p) => {
    const x = xAt(p.i).toFixed(1);
    const y = yAt(p.price).toFixed(1);
    const isHigh = p.kind === "high";
    const col = isHigh ? "#f87171" : "#4ade80";
    const tip = `${isHigh ? "高点" : "低点"} ${p.price ?? ""}${p.date ? " · " + p.date : ""}`;
    html += `<circle cx="${x}" cy="${y}" r="3.2" fill="${col}"><title>${escAttr(tip)}</title></circle>`;
  });
  // Wave numbers from Top1 (1/2/3 or A/B/C) at end of each wave.
  const marks = chart.wave_marks || [];
  marks.forEach((m) => {
    if (m.i == null || m.price == null) return;
    const x = xAt(m.i);
    const y = yAt(m.price);
    const cur = !!m.current;
    const lab = String(m.label || "");
    const fill = cur ? "#fbbf24" : "#f8fafc";
    const fs = cur ? 18 : 15;
    const tip = (cur ? "现在更像 · " : "")
      + (/^\d+$/.test(lab) ? `第${lab}浪` : `${lab}浪`)
      + (m.in_progress ? "（进行中）" : "")
      + (m.date ? " · " + m.date : "");
    // Halo behind current wave number.
    if (cur) {
      html += `<circle cx="${x.toFixed(1)}" cy="${(y - 14).toFixed(1)}" r="11" fill="#0f172a" stroke="#fbbf24" stroke-width="1.5"/>`;
    }
    html += `<text x="${x.toFixed(1)}" y="${(y - 9).toFixed(1)}" text-anchor="middle" `
      + `dominant-baseline="middle" fill="${fill}" font-size="${fs}" font-weight="${cur ? 700 : 600}">`
      + `<title>${escAttr(tip)}</title>${escAttr(lab)}</text>`;
  });
  // Last price tick.
  if (chart.last != null) {
    const y = yAt(chart.last).toFixed(1);
    html += `<line x1="${w - padR - 18}" x2="${w - padR}" y1="${y}" y2="${y}" stroke="#e2e8f0" stroke-width="1.5"/>`;
    html += `<text x="${w - padR - 20}" y="${(Number(y) + 3).toFixed(1)}" text-anchor="end" fill="#e2e8f0" font-size="10">${chart.last}</text>`;
  }
  // Date ends.
  const d0 = (chart.dates && chart.dates[0]) || "";
  const d1 = (chart.dates && chart.dates[chart.dates.length - 1]) || "";
  html += `<text x="${padL}" y="${h - 8}" fill="#64748b" font-size="10">${escAttr(d0)}</text>`;
  html += `<text x="${w - padR}" y="${h - 8}" text-anchor="end" fill="#64748b" font-size="10">${escAttr(d1)}</text>`;
  html += `</svg>`;
  const how = marks.length
    ? "怎么看：蓝折线上的大号 1/2/3（或 A/B/C）就是按「当前主情景」标的浪序；"
      + "黄色加粗带圈的是「现在更像」的这一浪。灰线是收盘价；红虚线是否决参考价。多解，仅观察。"
    : "怎么看：蓝折线是高低点摆动；下方卡片标题才是浪型假设。红虚线是否决参考价。";
  const extra = [];
  if (chart.wave_marks_note) extra.push(chart.wave_marks_note);
  else if (chart.wave_marks_from) extra.push(`浪序来源：${chart.wave_marks_from}`);
  else if (chart.primary_title) extra.push(`当前主情景：${chart.primary_title}`);
  if (chart.subwave_label) extra.push(`内部草稿：${chart.subwave_label}`);
  html += `<div class="ew-chart-cap">${escAttr(how)}`
    + (extra.length ? `<div style="margin-top:4px">${escAttr(extra.join(" · "))}</div>` : "")
    + `</div>`;
  return html;
}

let elliottChartState = { chart: null, selectedId: "" };

function elliottApplyScenarioMarks(chart, scenarioId) {
  const base = chart ? { ...chart } : null;
  if (!base) return null;
  const pack = (base.scenario_marks || {})[scenarioId];
  if (pack && (pack.marks || []).length) {
    base.wave_marks = pack.marks;
    base.wave_marks_note = pack.note || `图上浪序按「${pack.title || scenarioId}」标注`;
    base.wave_marks_from = pack.title || scenarioId;
    base.wave_marks_id = scenarioId;
  }
  return base;
}

function selectElliottScenario(id) {
  if (!id || !elliottChartState.chart) return;
  elliottChartState.selectedId = id;
  const chartEl = document.getElementById("elliottChart");
  const body = document.getElementById("elliottBody");
  const painted = elliottApplyScenarioMarks(elliottChartState.chart, id);
  if (chartEl && painted) chartEl.innerHTML = elliottChartSvg(painted);
  if (body) {
    body.querySelectorAll(".ew-card").forEach((el) => {
      const on = el.getAttribute("data-ew-id") === id;
      el.classList.toggle("selected", on);
      const tip = el.querySelector(".ew-pick");
      if (tip) tip.hidden = !on;
    });
  }
}

function paintElliott(d) {
  const box = d.elliott || {};
  const note = document.getElementById("elliottNote");
  const sum = document.getElementById("elliottSum");
  const body = document.getElementById("elliottBody");
  const chartEl = document.getElementById("elliottChart");
  if (!note || !sum || !body) return;
  setModStamp("elliottStamp", box.refreshed_at || d.updated_at, { label: "刷新" });
  note.textContent = (box.disclaimer || "波浪多解，仅观察。")
    + (box.note ? (" " + box.note) : "")
    + " 点击下方情景卡片可切换图上浪序号。";
  elliottChartState.chart = box.chart || null;
  const primary = box.primary || {};
  const defaultId = (box.chart && box.chart.wave_marks_id)
    || primary.id
    || ((box.scenarios || [])[0] || {}).id
    || "";
  elliottChartState.selectedId = defaultId;
  if (chartEl) {
    const painted = elliottApplyScenarioMarks(box.chart, defaultId) || box.chart;
    chartEl.innerHTML = painted
      ? elliottChartSvg(painted)
      : `<div class="meta">${box.ok === false ? (box.note || "暂无图") : "暂无图数据"}</div>`;
  }
  const st = box.structure || {};
  const ind = box.indicators || {};
  const macd = ind.macd || {};
  const rsi = ind.rsi || {};
  sum.innerHTML =
    `<span>现价 <b>${box.last ?? "—"}</b></span>`
    + `<span>样本 <b>${box.bars ?? "—"}</b> 日`
    + (box.bar_from || box.bar_to
      ? ` <span class="meta">（${box.bar_from || "?"} → ${box.bar_to || "?"}）</span>`
      : "")
    + `</span>`
    + `<span>枢轴 <b>${box.pivot_n ?? "—"}</b></span>`
    + `<span class="meta">趋势 ${({ up: "偏多", down: "偏空", side: "震荡" })[st.trend] || "—"}</span>`
    + `<span>MACD <b>${macd.cross || "—"}</b> DIF ${macd.dif ?? "—"}</span>`
    + `<span>RSI <b>${rsi.value ?? "—"}</b> ${rsi.zone || ""}</span>`
    + (ind.divergence && ind.divergence.note
      ? `<span class="meta">${ind.divergence.note}</span>`
      : "")
    + (primary.title
      ? `<span>Top1 <b>${primary.title}</b>（${primary.fit ?? "—"}）`
        + (primary.subwave ? ` · 子浪 <b>${primary.subwave}</b>` : "")
        + `</span>`
      : "");
  const rows = box.scenarios || [];
  if (!rows.length) {
    body.innerHTML = `<div class="meta">${box.note || "暂无波浪情景"}</div>`;
    return;
  }
  const topId = primary.id;
  const selId = elliottChartState.selectedId;
  const waveTermMap = {
    "第1浪": "第1浪", "第2浪": "第2浪", "第3浪": "第3浪", "第4浪": "第4浪", "第5浪": "第5浪",
    "A浪": "A浪", "B浪": "B浪", "C浪": "C浪",
    "三角/平台": "三角整理", "复合调整": "复合调整",
  };
  body.innerHTML = `<div class="ew-grid">` + rows.map((sc, idx) => {
    const fit = Number(sc.fit || 0);
    const fitCls = fit >= 60 ? "hi" : (fit >= 40 ? "mid" : "");
    const bias = sc.bias || "neutral";
    const isPri = sc.id === topId;
    const isSel = sc.id === selId;
    const wTerm = waveTermMap[String(sc.wave || "")] || "";
    const tip = wTerm && GLOSSARY[wTerm]
      ? ` <button type="button" class="q" data-term="${wTerm}">?</button>`
      : "";
    const turns = sc.turns || [];
    const levels = sc.levels || [];
    const timing = sc.timing || {};
    const sub = sc.subwaves || null;
    let subHtml = "";
    if (idx < 2 && sub) {
      if (sub.ok && sub.primary) {
        const pri = sub.primary;
        const alts = sub.alts || [];
        const altHtml = alts.length
          ? `<ul class="ew-sub-alts">`
            + alts.map((a) =>
              `<li>${a.label || ""} · 契合 ${a.fit ?? "—"}`
              + (a.fit_note ? ` · ${a.fit_note}` : "")
              + `</li>`
            ).join("")
            + `</ul>`
          : "";
        subHtml = `<details class="ew-sub">
              <summary>内部结构草稿 · ${pri.label || "—"}（契合 ${pri.fit ?? "—"}）
                <button type="button" class="q" data-term="子浪草稿">?</button></summary>
              <div class="ew-sub-pri">
                <b>${pri.label || ""}</b>
                <span class="meta"> · ${pri.fit_note || ""}</span>
                <div class="ew-path"><b>路径：</b>${pri.path || "—"}</div>
                <div class="ew-risk"><b>否决：</b>${pri.invalidation || pri.watch || "—"}</div>
                <div class="meta">${sub.note || ""} · 细枢轴 ${sub.fine_pivot_n ?? "—"}</div>
                ${altHtml}
              </div>
            </details>`;
      } else if (sub.note) {
        subHtml = `<div class="meta" style="margin-top:8px">子浪：${sub.note}</div>`;
      }
    }
    const turnHtml = turns.length
      ? `<div class="ew-turns"><div class="meta" style="margin-bottom:2px">变盘时间窗（仅日期，按本浪时钟）
              <button type="button" class="q" data-term="斐波那契变盘窗">?</button></div>`
        + turns.map((t) => `
              <div class="ew-turn">
                <b>${t.date || "—"}</b>
                <div>
                  ${t.fib || ""} <span class="meta">· ${t.source || ""}</span>
                  <div class="meta">置信 ${t.confidence ?? "—"}
                  ${t.note ? " · " + t.note : ""}</div>
                </div>
              </div>`).join("")
        + `</div>`
      : `<div class="meta" style="margin-top:6px">暂无未来变盘窗</div>`;
    const lvlHtml = levels.length
      ? `<div class="ew-turns"><div class="meta" style="margin-bottom:2px">结构点位（上=阻力 · 下=支撑，非该日必达）</div>`
        + levels.map((lv) => {
          const vs = lv.vs_last_pct;
          const vsTxt = vs == null ? "" : ` · 距现价 ${(vs > 0 ? "+" : "") + vs}%`;
          const vsCls = vs == null ? "" : (vs >= 0 ? "up" : "down");
          const side = vs == null ? "" : (vs > 0 ? "↑" : (vs < 0 ? "↓" : "·"));
          return `<div class="ew-turn">
                <b class="${vsCls}">${side} ${lv.price ?? "—"}</b>
                <div>${lv.tag || ""} · ${lv.role || ""}<span class="meta">${vsTxt}</span></div>
              </div>`;
        }).join("")
        + `<div class="meta">${sc.levels_note || ""}</div></div>`
      : "";
    const timingHtml = `<div class="ew-timing">指标：${(timing.notes || []).join("；") || "—"}</div>`;
    return `<article class="ew-card${isPri ? " primary" : ""}${isSel ? " selected" : ""}" data-ew-id="${escAttr(sc.id || "")}" title="点击：图上按此情景标浪">
          <div class="ew-top">
            <b class="ew-bias-${bias}">${sc.title || ""}${tip}</b>
            <span class="ew-fit ${fitCls}">契合 ${fit}</span>
          </div>
          <div class="meta">${sc.family || ""} · ${sc.wave || ""} · ${sc.fit_note || ""}</div>
          <div class="ew-pick" ${isSel ? "" : "hidden"}>正在用于上图浪序</div>
          <div class="ew-path"><b>后市路径：</b>${sc.path || sc.next || "—"}</div>
          <div class="ew-risk"><b>否决/风险：</b>${sc.invalidation || "—"}</div>
          ${subHtml}
          ${timingHtml}
          ${turnHtml}
          ${lvlHtml}
        </article>`;
  }).join("") + `</div>`;
  body.onclick = (ev) => {
    if (ev.target.closest("button, details, a, input, select")) return;
    const card = ev.target.closest("[data-ew-id]");
    if (!card) return;
    selectElliottScenario(card.getAttribute("data-ew-id"));
  };
}

let fundPeriod = "em_day";
let lastFundFlow = null;
let lastFundFlowFullAt = 0;

function paintFundFlow(d) {
  const box = d.fund_flow || {};
  lastFundFlow = box;
  const note = document.getElementById("fundNote");
  const chips = document.getElementById("fundPeriodChips");
  const sum = document.getElementById("fundSum");
  const statsEl = document.getElementById("fundStats");
  const spot = document.getElementById("fundSpot");
  const body = document.getElementById("fundBody");
  if (!note || !sum || !spot || !body) return;
  note.textContent = (box.disclaimer || "") + (box.note ? (" " + box.note) : "");
  const stampKind = box.full_ready
    ? "全日"
    : (box.refreshed_kind === "day" ? "当日" : "");
  setModStamp("fundStamp", box.refreshed_at || d.updated_at, {
    label: "刷新",
    kind: stampKind,
    title: box.full_ready
      ? "含近5/10日东财榜"
      : "热路径仅当日；打开本页会异步补齐近5/10日",
    stale: !box.full_ready,
  });
  const meta = box.period_meta || [];
  if (!meta.some((m) => m.id === fundPeriod)) fundPeriod = box.default_period || "em_day";
  if (chips) {
    chips.innerHTML = meta.map((m) => {
      return `<button type="button" class="g-chip${m.id === fundPeriod ? " on" : ""}" data-fund-period="${m.id}" title="${m.hint || ""}">${m.label}</button>`;
    }).join("");
  }
  const periodBox = ((box.periods || {})[fundPeriod]) || box;
  const tot = periodBox.totals || {};
  const st = periodBox.stats || {};
  const cmp = box.compare || {};
  sum.innerHTML =
    `<span>周期 <b>${periodBox.label || fundPeriod}</b></span>`
    + `<span class="meta">${periodBox.hint || ""}</span>`
    + `<span>行业 <b>${tot.industry_n ?? "—"}</b></span>`
    + `<span>概念 <b>${tot.concept_n ?? "—"}</b></span>`
    + `<span>流入前部 <b class="up">${tot.industry_in_yi ?? "—"}</b> 亿</span>`
    + `<span>流出尾部 <b class="down">${tot.industry_out_yi ?? "—"}</b> 亿</span>`;
  if (statsEl) {
    statsEl.innerHTML =
      `<span>净额(前+尾) <b class="${(st.industry_net_yi || 0) >= 0 ? "up" : "down"}">${st.industry_net_yi ?? "—"}</b> 亿</span>`
      + `<span>多空 <b class="up">${st.pos_n ?? "—"}</b>/<b class="down">${st.neg_n ?? "—"}</b></span>`
      + `<span>广度 <b>${st.breadth ?? "—"}%</b></span>`
      + `<span>Top5 占正流入 <b>${st.top5_share_pct ?? "—"}%</b></span>`
      + `<span>大资金 行业${st.big_money_industry_n ?? "—"} / 概念${st.big_money_concept_n ?? "—"}</span>`
      + `<span class="meta">${cmp.note || ""}</span>`;
  }
  const lights = periodBox.spotlight || [];
  spot.innerHTML = lights.length
    ? lights.map((r) => {
      const yi = r.super_yi != null ? r.super_yi : r.main_yi;
      const cls = (Number(yi) || 0) >= 0 ? "up" : "down";
      return `<span class="chip"><b>${r.name || ""}</b>
            <span class="${cls}">主力 ${r.main_yi ?? "—"}亿</span>
            ${r.super_yi != null ? ` · 超大 ${r.super_yi}亿` : ""}
            <span class="fund-big">大资金</span></span>`;
    }).join("")
    : `<span class="meta">暂无超大单主导板块</span>`;
  const table = (title, rows, tone) => {
    const list = rows || [];
    if (!list.length) {
      return `<section class="card mid" style="margin:0"><h2 style="text-transform:none">${title}</h2>
            <div class="meta">暂无数据</div></section>`;
    }
    const head = `<tr><th>#</th><th>板块</th><th>涨跌%</th><th>主力亿</th><th>超大亿</th><th>主力%</th><th>龙头</th></tr>`;
    const bodyRows = list.map((r, i) => {
      const mainCls = (Number(r.main_yi) || 0) >= 0 ? "up" : "down";
      const pctCls = (Number(r.pct) || 0) >= 0 ? "up" : "down";
      return `<tr>
            <td>${i + 1}</td>
            <td>${r.name || ""}${r.big_money ? `<span class="fund-big">大</span>` : ""}</td>
            <td class="${pctCls}">${r.pct ?? "—"}</td>
            <td class="${mainCls}">${r.main_yi ?? "—"}</td>
            <td class="${(Number(r.super_yi) || 0) >= 0 ? "up" : "down"}">${r.super_yi ?? "—"}</td>
            <td>${r.main_pct ?? "—"}</td>
            <td>${r.leader_name || "—"}</td>
          </tr>`;
    }).join("");
    return `<section class="card mid" style="margin:0;border-color:${tone === "in" ? "#2f5a3a" : "#5a2f2f"}">
          <h2 style="text-transform:none">${title}</h2>
          <table class="fund-table"><thead>${head}</thead><tbody>${bodyRows}</tbody></table>
        </section>`;
  };
  body.innerHTML =
    table("行业 · 主力流入 Top", periodBox.industry_in, "in")
    + table("行业 · 主力流出 Top", periodBox.industry_out, "out")
    + table("概念 · 主力流入 Top", periodBox.concept_in, "in")
    + table("概念 · 主力流出 Top", periodBox.concept_out, "out");
}

document.getElementById("fundPeriodChips")?.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-fund-period]");
  if (!btn) return;
  fundPeriod = btn.getAttribute("data-fund-period") || "em_day";
  if (lastFundFlow) paintFundFlow({ fund_flow: lastFundFlow });
  else if (typeof lastData !== "undefined" && lastData) paintFundFlow(lastData);
});

function paintAuctionStrategy(d) {
  const box = d.auction_strategy || {};
  const note = document.getElementById("aucStratNote");
  const sum = document.getElementById("aucStratSum");
  const body = document.getElementById("aucStratBody");
  if (!note || !sum || !body) return;
  const running = box.run_state === "竞价中" || box.run_state === "已开盘";
  note.textContent = (box.disclaimer || "独立页：不改作战台买卖结论。")
    + (box.run_note ? (" " + box.run_note) : "")
    + (box.enrich_pending ? " 股东/年内涨停补齐中…" : "");
  sum.innerHTML =
    `<span class="auc-run${running ? " on" : ""}">${box.run_state || "未运行"}</span>`
    + `<span>昨停 <b>${box.yzt_n ?? "—"}</b></span>`
    + `<span>入选 <b>${box.scan_n ?? 0}</b></span>`
    + `<span class="meta">时段 ${box.segment || "—"}</span>`
    + (box.enrich_pending ? `<span class="meta">补齐中…</span>` : "");
  const tiers = box.tiers || [];
  if (!tiers.length) {
    body.innerHTML = `<div class="meta">暂无竞价策略数据（等昨停池与开盘价）</div>`;
    return;
  }
  const fmtMv = (v) => {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    return n >= 100 ? n.toFixed(0) + "亿" : n.toFixed(1) + "亿";
  };
  const fmtChg = (v) => {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    const cls = n < 0 ? "up" : (n > 0 ? "down" : "");
    return `<span class="${cls}">${n > 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
  };
  body.innerHTML = tiers.map((tier) => {
    const items = tier.items || [];
    const rows = items.map((it) => {
      const adv = it.advice || "";
      const advCls = adv === "抢筹" ? "auc-adv-grab"
        : (adv === "关注" ? "auc-adv-watch" : "auc-adv-no");
      const seal = it.seal_ratio == null ? "—" : it.seal_ratio;
      const openPct = it.open_pct == null ? "—" : ((it.open_pct > 0 ? "+" : "") + it.open_pct + "%");
      const holderTip = [
        it.holder_end ? `截止 ${it.holder_end}` : "",
        it.holder_avg_wan != null ? `户均 ${it.holder_avg_wan}万` : "",
        "季度披露",
      ].filter(Boolean).join(" · ");
      const theme = it.theme_text || it.industry || "—";
      return `<tr>
            <td>${tickerHtml(it.name, it.code)}</td>
            <td class="${(it.open_pct || 0) >= 0 ? "up" : "down"}">${openPct}</td>
            <td>${it.open_type || "—"}</td>
            <td class="down">${seal}</td>
            <td class="${advCls}">${adv}</td>
            <td>${it.judge || "—"}</td>
            <td class="meta">${it.boards_yesterday ?? "—"}板${it.in_zt_today ? " ·今封" : ""}</td>
            <td title="${holderTip}">${it.holder_num != null ? fmtHolderNum(it.holder_num) : "—"}</td>
            <td>${fmtChg(it.holder_chg_pct)}</td>
            <td>${it.zt_ytd != null ? it.zt_ytd : "—"}</td>
            <td>${fmtMv(it.mv_yi)}</td>
            <td class="meta" title="${theme}">${theme}</td>
            <td><button type="button" class="rev-col-btn auc-pin" data-code="${it.code || ""}" data-name="${it.name || ""}">钉自选</button></td>
          </tr>`;
    }).join("") || `<tr><td colspan="13" class="meta">本档暂无</td></tr>`;
    return `<div class="auc-tier" data-tier="${tier.id || ""}">
          <div class="auc-hd">
            <b>${tier.label || ""}</b>
            <span class="cnt">${items.length}</span>
            <span class="meta">${tier.hint || ""}</span>
          </div>
          <div class="auc-table-wrap">
          <table>
            <thead><tr>
              <th>名称</th><th>开幅%</th><th>开盘型</th><th>封成比</th><th>建议</th><th>判</th><th>昨连</th>
              <th>股东户数</th><th>户数增减</th><th>年内涨停</th><th>总市值</th><th>题材</th><th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
          </div>
        </div>`;
  }).join("");
}
