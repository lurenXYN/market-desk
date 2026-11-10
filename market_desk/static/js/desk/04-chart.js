let lastData = null;
let currentMain = "desk";
let cycleSel = null;
let reviewLoadedAt = 0;
let reviewZtLoading = false;
let reviewZtSeq = 0;
let reviewTrendSeq = 0;
let reviewTrendFp = "";
let reviewTrendBy = null;
let reviewRefreshedAt = "";
let reviewZtRefreshedAt = "";
let reviewTrendRefreshedAt = "";
let chartCode = "";
let chartSignalAt = "";
let chartRevId = "";
let deskChartCtxByCode = {};
let sigHistRows = [];

function sparkSvg(values, opts) {
  const pts = (values || []).map(Number).filter((v) => !Number.isNaN(v));
  if (pts.length < 2) return `<div class="chart-empty">${(opts && opts.empty) || "暂无数据"}</div>`;
  const w = 420, h = 160, pad = 12;
  const overlays = (opts && opts.overlays) || [];
  const levels = ((opts && opts.levels) || []).filter((lv) => {
    const n = Number(lv && lv.price);
    return Number.isFinite(n) && n > 0;
  });
  const all = pts
    .concat(overlays.flatMap((o) => (o.values || []).map(Number).filter((v) => !Number.isNaN(v))))
    .concat(levels.map((lv) => Number(lv.price)));
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const xAt = (i, n) => pad + (i / Math.max(1, n - 1)) * (w - pad * 2);
  const yAt = (v) => pad + (1 - (v - min) / span) * (h - pad * 2);
  const line = (arr, color, width) => {
    const d = arr.map((v, i) => `${i ? "L" : "M"}${xAt(i, arr.length).toFixed(1)},${yAt(v).toFixed(1)}`).join(" ");
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width || 1.6}"/>`;
  };
  let html = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`;
  html += line(pts, opts && opts.color || "#60a5fa", 1.8);
  overlays.forEach((o) => { html += line(o.values, o.color || "#34d399", o.width || 1.2); });
  const mi = opts && opts.markerIndex;
  if (mi != null && mi >= 0 && mi < pts.length) {
    const x = xAt(mi, pts.length).toFixed(1);
    html += `<line x1="${x}" x2="${x}" y1="${pad}" y2="${h - pad}" stroke="#f87171" stroke-width="1.4" stroke-dasharray="3 2"/>`;
    html += `<text x="${x}" y="${pad + 10}" fill="#f87171" font-size="10">信号</text>`;
  }
  // Horizontal key levels (plan / stop / cost). Stagger labels if y-close.
  const placed = [];
  levels.forEach((lv) => {
    const px = Number(lv.price);
    let y = yAt(px);
    for (const py of placed) {
      if (Math.abs(y - py) < 11) y = py - 11;
    }
    placed.push(y);
    const y1 = yAt(px).toFixed(1);
    const color = lv.color || "#94a3b8";
    const lab = String(lv.label || "").slice(0, 4);
    html += `<line x1="${pad}" x2="${w - pad}" y1="${y1}" y2="${y1}" stroke="${color}" stroke-width="1.1" stroke-dasharray="4 3" opacity="0.9"/>`;
    html += `<text x="${w - pad - 2}" y="${(y - 2).toFixed(1)}" fill="${color}" font-size="9" text-anchor="end">${lab} ${px}</text>`;
  });
  html += `</svg>`;
  return html;
}

function resolveChartLevels() {
  // Collect plan / band / stop / cost levels for the open chart code.
  const levels = [];
  const seen = new Set();
  const add = (price, label, color) => {
    const n = Number(price);
    if (!Number.isFinite(n) || n <= 0) return;
    const key = `${label}|${n.toFixed(4)}`;
    if (seen.has(key)) return;
    seen.add(key);
    levels.push({ price: n, label, color });
  };
  const ctx = resolveChartDescContext(chartRevId);
  if (ctx) {
    const payload = (ctx.payload && typeof ctx.payload === "object") ? ctx.payload : {};
    const st = String(ctx.signal_type || "");
    const isSell = st === "sell" || st.startsWith("sell");
    if (isSell) {
      add(ctx.price ?? payload.sell_price, "建议卖", "#fb7185");
    } else {
      add(ctx.price ?? payload.plan_price ?? payload.buy_price, "建议", "#34d399");
    }
    add(ctx.wait_price ?? payload.wait_price, "回踩", "#38bdf8");
    add(ctx.chase_price ?? payload.chase_price, "不追", "#fbbf24");
    add(ctx.stop_price ?? payload.stop_price, "止损", "#f87171");
  }
  const c = String(chartCode || "").replace(/\D/g, "").padStart(6, "0");
  if (c.length === 6) {
    const pos = ((lastData && lastData.positions) || []).find(
      (p) => String(p.code || "").replace(/\D/g, "").padStart(6, "0") === c
        && Number(p.qty || 0) > 0
    );
    if (pos) {
      add(pos.cost, "成本", "#a78bfa");
      if (pos.last_sell_price != null && Number(pos.day_sold_qty || 0) > 0) {
        add(pos.last_sell_price, "已减价", "#c084fc");
      }
    }
    const sa = (lastData || {}).sell_advice || {};
    const sellPool = sa.alert_items || sa.items || [];
    const sellIt = sellPool.find(
      (it) => String(it.code || "").replace(/\D/g, "").padStart(6, "0") === c
    );
    if (sellIt) {
      add(sellIt.sell_price, "建议卖", "#fb7185");
      add(sellIt.stop_price, "止损", "#f87171");
    }
  }
  return levels;
}

function paintChartLevels(levels) {
  const box = document.getElementById("chartLevels");
  if (!box) return;
  if (!levels || !levels.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = `<span class="meta">关键位</span>`
    + levels.map((lv) =>
      `<span class="lv"><span class="sw" style="background:${lv.color}"></span>`
      + `${lv.label} ${lv.price}</span>`
    ).join("");
}

function paintChart(payload, opts) {
  const chrome = !(opts && opts.chrome === false);
  const trend = (payload && payload.trend) || {};
  const sig = payload && payload.signal_at;
  if (chrome) {
    const titleEl = document.getElementById("chartTitle");
    const metaEl = document.getElementById("chartMeta");
    if (titleEl) {
      titleEl.textContent =
        `${payload.name || "—"} · ${payload.symbol || payload.code || ""}`;
    }
    if (metaEl) {
      metaEl.textContent =
        `日线 ${trend.label || "—"}` +
        (trend.ma5 != null ? ` · MA5 ${trend.ma5}` : "") +
        (trend.ma20 != null ? ` · MA20 ${trend.ma20}` : "") +
        (sig ? ` · 信号 ${String(sig).slice(11, 16) || sig}` : "");
    }
    const xq = payload.xueqiu_url || xueqiuUrl(payload.code);
    const a = document.getElementById("chartXq");
    if (a) {
      a.href = xq || "#";
      a.textContent = xq ? `雪球看盘 · ${payload.symbol || ""}` : "雪球看盘";
    }
  }
  const levels = resolveChartLevels();
  paintChartLevels(levels);
  const minuteRows = payload.minute || [];
  const minutes = minuteRows.map((x) => x.price);
  let markerIndex = null;
  if (sig) {
    const m = String(sig).match(/(\d{2}:\d{2})/);
    const hhmm = m ? m[1] : "";
    if (hhmm) {
      for (let i = 0; i < minuteRows.length; i++) {
        const t = String(minuteRows[i].time || "");
        if (t.includes(hhmm)) { markerIndex = i; break; }
      }
    }
  }
  const minuteEl = document.getElementById("chartMinute");
  if (minuteEl) {
    minuteEl.innerHTML = sparkSvg(minutes, {
      color: "#34d399", empty: "分时暂无数据（休市或源失败）", markerIndex, levels,
    });
  }
  const closes = (payload.daily || []).map((x) => x.close);
  const ma = (n) => closes.map((_, i) => {
    if (i + 1 < n) return null;
    const slice = closes.slice(i + 1 - n, i + 1);
    return slice.reduce((s, v) => s + v, 0) / n;
  });
  const ma5 = ma(5).map((v, i) => v == null ? closes[i] : v);
  const ma20 = ma(20).map((v, i) => v == null ? closes[i] : v);
  const dailyEl = document.getElementById("chartDaily");
  if (dailyEl) {
    dailyEl.innerHTML = sparkSvg(closes, {
      color: "#60a5fa",
      empty: "日线暂无数据",
      overlays: [
        { values: ma5, color: "#fbbf24", width: 1.1 },
        { values: ma20, color: "#fb923c", width: 1.1 },
      ],
      levels,
    });
  }
}

function findRevSignal(id) {
  if (id == null || id === "" || !lastReview) return null;
  return (lastReview.signals || []).find((r) => String(r.id) === String(id)) || null;
}

function registerDeskChartContext(v) {
  deskChartCtxByCode = {};
  if (!v) return;
  const pools = [
    v.recommend?.items,
    v.dragon_recommend?.items,
    v.side_recommend?.items,
    v.link_recommend?.items,
    v.watch_trial_recommend?.items,
    v.independent_recommend?.items,
  ];
  for (const items of pools) {
    for (const it of items || []) {
      const c = String(it.code || "").replace(/\D/g, "").padStart(6, "0");
      if (c.length === 6) deskChartCtxByCode[c] = it;
    }
  }
}

function deskItemAsReviewShape(it) {
  return (window.MDReview && MDReview.deskItemAsReviewShape)
    ? MDReview.deskItemAsReviewShape(it)
    : null;
}

function resolveChartDescContext(revId) {
  if (revId) return findRevSignal(revId);
  const c = String(chartCode || "").replace(/\D/g, "").padStart(6, "0");
  if (c.length === 6 && deskChartCtxByCode[c]) {
    return deskItemAsReviewShape(deskChartCtxByCode[c]);
  }
  return null;
}

function revSignalDescRows(r) {
  return (window.MDReview && MDReview.signalDescRows)
    ? MDReview.signalDescRows(r)
    : [];
}

function revSignalDescHtml(r) {
  return (window.MDReview && MDReview.signalDescHtml)
    ? MDReview.signalDescHtml(r)
    : "";
}

function paintChartDesc(r) {
  const box = document.getElementById("chartDesc");
  if (!box) return;
  const html = revSignalDescHtml(r);
  if (!html) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = `<div class="hd">信号描述</div>${html}`;
}

async function openChart(code, name, signalAt, revId) {
  // Shared drawer: charts + signal history + fan/pot appearances.
  return openSigHist(code, name, { signalAt: signalAt || "", revId: revId || "" });
}

function closeChart() {
  const mask = document.getElementById("chartMask");
  if (mask) mask.hidden = true;
  closeSigHist();
}

async function loadSigHistChart(code, name, seq) {
  const minuteEl = document.getElementById("chartMinute");
  const dailyEl = document.getElementById("chartDaily");
  if (minuteEl) minuteEl.innerHTML = `<div class="chart-empty">加载分时…</div>`;
  if (dailyEl) dailyEl.innerHTML = `<div class="chart-empty">加载日线…</div>`;
  paintChartDesc(resolveChartDescContext(chartRevId));
  paintChartLevels([]);
  try {
    const q = chartSignalAt ? ("?signal_at=" + encodeURIComponent(chartSignalAt)) : "";
    const r = await fetch("/api/chart/" + code + q);
    const d = await r.json();
    if (seq != null && seq !== sigHistSeq) return;
    if (!d.ok) throw new Error("bad");
    if (name && !d.name) d.name = name;
    if (chartSignalAt && !d.signal_at) d.signal_at = chartSignalAt;
    paintChart(d, { chrome: false });
  } catch (e) {
    if (seq != null && seq !== sigHistSeq) return;
    if (minuteEl) minuteEl.innerHTML = `<div class="chart-empty">分时拉取失败</div>`;
    if (dailyEl) dailyEl.innerHTML = `<div class="chart-empty">日线拉取失败</div>`;
  }
}

let sigHistCode = "";
let sigHistName = "";
let sigHistSeq = 0;

function closeSigHist() {
  document.getElementById("sigHistMask").hidden = true;
}

function paintSigHist(d) {
  const sum = d.summary || {};
  const tr = d.trend || {};
  const hol = d.holder || {};
  const trendLab = tr.up ? "上升" : (tr.down ? "下降" : (tr.label || "—"));
  const trendCls = tr.up ? "up" : (tr.down ? "down" : "");
  const hitTxt = sum.buy_hit_rate == null
    ? "—"
    : `${sum.buy_hit_rate}%（${sum.buy_hit_n || 0}/${sum.scored_buy_n || 0}）`;
  const holderBits = [];
  if (hol.holder_num != null) {
    holderBits.push(`股东 ${fmtHolderNum(hol.holder_num)}`);
    if (hol.holder_chg_pct != null) {
      const n = Number(hol.holder_chg_pct);
      holderBits.push(`较上期 ${(n > 0 ? "+" : "") + n.toFixed(1)}%`);
    }
    if (hol.holder_avg_wan != null) {
      holderBits.push(`户均 ${fmtHolderAvgWan(hol.holder_avg_wan)}`);
    }
    if (hol.holder_end) holderBits.push(`截止 ${hol.holder_end}`);
  }
  document.getElementById("sigHistTitle").textContent =
    `${d.name || sigHistName || d.code} · ${d.code}`;
  document.getElementById("sigHistMeta").innerHTML =
    `日线 <span class="${trendCls}">${trendLab}</span>`
    + (tr.ma5 != null ? ` · MA5 ${tr.ma5}` : "")
    + (tr.ma20 != null ? ` · MA20 ${tr.ma20}` : "")
    + (holderBits.length ? ` · ${holderBits.join(" · ")}` : (d.kind === "etf" ? " · ETF无股东户数" : " · 股东待取"));
  document.getElementById("sigHistSum").innerHTML =
    `<span>出现 <b>${sum.n ?? 0}</b> 次</span>`
    + `<span>买 ${sum.buy_n ?? 0} / 卖 ${sum.sell_n ?? 0}</span>`
    + `<span>已交易买 ${sum.traded_buy_n ?? 0}</span>`
    + `<span>买命中 ${hitTxt}</span>`
    + `<span>${sum.first_date || "—"} → ${sum.last_date || "—"}</span>`
    + `<span class="meta">趋势/股东为现况快照，非信号当日冻结</span>`;
  const typeLab = (st) => ({
    buy: "买",
    buy_side: "买·支",
    buy_link: "买·联",
    buy_trial: "买·自选",
    sell: "卖",
  })[st] || st || "—";
  const body = document.getElementById("sigHistBody");
  const rows = d.signals || [];
  sigHistRows = rows;
  const descBox = document.getElementById("sigHistDesc");
  if (descBox) {
    descBox.hidden = true;
    descBox.innerHTML = "";
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="10" class="meta">暂无该代码的历史信号</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((r, idx) => {
    const d1 = r.outcome_day1_pct;
    const d1Html = d1 == null
      ? "—"
      : `<span class="${Number(d1) >= 0 ? "up" : "down"}">${(Number(d1) > 0 ? "+" : "") + Number(d1).toFixed(2)}%</span>`;
    const traded = Number(r.traded || 0) ? "已交易" : (Number(r.skipped || 0) ? "跳过" : "—");
    const dayJump = r.trade_date
      ? `<button type="button" class="rev-col-btn sig-hist-day" data-day="${escAttr(r.trade_date)}" style="padding:0 4px;font-size:11px">${escAttr(r.trade_date)}</button>`
      : "—";
    return `<tr class="sig-hist-row" data-row-idx="${idx}" title="点击查看该条信号描述">
          <td>${dayJump}</td>
          <td>${typeLab(r.signal_type)}</td>
          <td>${r.price ?? "—"}</td>
          <td>${r.chase_price ?? "—"}</td>
          <td>${r.wait_price ?? "—"}</td>
          <td>${r.outcome_label || "待隔日"}</td>
          <td>${d1Html}</td>
          <td>${r.phase || "—"}</td>
          <td title="${escAttr(r.board_text || "")}">${escAttr(r.mainline || "—")}</td>
          <td>${traded}${r.fill_price != null ? ` · ${r.fill_price}` : ""}</td>
        </tr>`;
  }).join("");
  if (rows.length) paintSigHistDesc(rows[0], 0);
}

function paintSigHistDesc(row, idx) {
  const box = document.getElementById("sigHistDesc");
  if (!box) return;
  const html = revSignalDescHtml(row);
  if (!html) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  const when = row.signaled_at || row.trade_date || "";
  box.innerHTML = `<div class="hd">信号描述${when ? ` · ${escAttr(String(when).slice(0, 16))}` : ""}</div>${html}`;
  document.querySelectorAll("#sigHistBody tr.sig-hist-row").forEach((tr) => {
    tr.classList.toggle("sel", Number(tr.dataset.rowIdx) === idx);
  });
}

function paintSigHistScans(d) {
  const body = document.getElementById("sigHistScanBody");
  const meta = document.getElementById("sigHistScanMeta");
  if (!body) return;
  const rows = d.scan_rows || [];
  if (meta) {
    meta.textContent =
      `发散 ${d.ma_fan_n ?? 0} · 锅底 ${d.pot_n ?? 0}`
      + ((d.board_set || []).length
        ? ` · 板 ${(d.board_set || []).slice(0, 6).join(" · ")}`
        : "");
  }
  const sum = document.getElementById("sigHistSum");
  if (sum && !sum.querySelector(".sig-hist-scan-tip")) {
    const tip = document.createElement("span");
    tip.className = "meta sig-hist-scan-tip";
    tip.textContent =
      `发散 ${d.ma_fan_n ?? 0} · 锅底 ${d.pot_n ?? 0}`
      + ((d.board_set || []).length
        ? ` · 当时板 ${(d.board_set || []).slice(0, 8).join(" · ")}`
        : "");
    sum.appendChild(tip);
    const cur = (d.current_boards || []).map((b) => b.name).filter(Boolean);
    if (cur.length) {
      const now = document.createElement("span");
      now.className = "meta sig-hist-scan-tip";
      now.title = "今日成分快照，仅供对照，不是当时挂的板";
      now.textContent = `现况板（非当时） ${cur.slice(0, 10).join(" · ")}`;
      sum.appendChild(now);
    }
  }
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8" class="meta">近窗未进发散/锅底名单</td></tr>`;
    return;
  }
  const srcLab = (s) => (s === "pot" ? "锅底" : (s === "ma_fan" ? "发散" : (s || "—")));
  const pctHtml = (v) => {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `<span class="${n >= 0 ? "up" : "down"}">${(n > 0 ? "+" : "") + n.toFixed(2)}%</span>`;
  };
  body.innerHTML = rows.map((r) => {
    const ago = r.days_ago != null ? `${r.days_ago} 天前` : "—";
    const px = r.close != null ? Number(r.close).toFixed(2) : "—";
    return `<tr>
      <td>${escAttr(r.trade_date || "—")}</td>
      <td>${escAttr(srcLab(r.source))}</td>
      <td>${escAttr(px)}</td>
      <td>${escAttr(ago)}</td>
      <td>${pctHtml(r.pct)}</td>
      <td>${pctHtml(r.d3)}</td>
      <td>${escAttr(r.tag || r.stage || "—")}</td>
      <td title="${escAttr(r.board_text || "")}">${escAttr(r.board_text || "—")}</td>
    </tr>`;
  }).join("");
}

async function openSigHist(code, name, opts) {
  const c = String(code || "").replace(/\D/g, "").padStart(6, "0");
  if (c.length !== 6) return;
  const o = opts || {};
  sigHistCode = c;
  sigHistName = name || c;
  chartCode = c;
  chartSignalAt = o.signalAt || o.signal_at || "";
  chartRevId = o.revId != null && o.revId !== "" ? o.revId : (o.rev_id || "");
  const seq = ++sigHistSeq;
  // Prefer the shared drawer; keep legacy chart mask closed.
  const chartMask = document.getElementById("chartMask");
  if (chartMask) chartMask.hidden = true;
  const mask = document.getElementById("sigHistMask");
  mask.hidden = false;
  document.getElementById("sigHistTitle").textContent = `${sigHistName} · ${c}`;
  document.getElementById("sigHistMeta").textContent = "加载明细…";
  document.getElementById("sigHistSum").innerHTML = "";
  document.getElementById("sigHistBody").innerHTML =
    `<tr><td colspan="10" class="meta">加载中…</td></tr>`;
  const scanBody = document.getElementById("sigHistScanBody");
  if (scanBody) scanBody.innerHTML = `<tr><td colspan="8" class="meta">加载中…</td></tr>`;
  const scanMeta = document.getElementById("sigHistScanMeta");
  if (scanMeta) scanMeta.textContent = "";
  const xq = xueqiuUrl(c);
  const a = document.getElementById("sigHistXq");
  a.href = xq || "#";
  a.textContent = xq ? `雪球看盘 · ${xq.split("/").pop()}` : "雪球看盘";
  // Charts + history + appearances in parallel (shared comprehensive drawer).
  const chartP = loadSigHistChart(c, sigHistName, seq);
  const histP = fetch("/api/review/history/" + c)
    .then((r) => r.json())
    .then((d) => {
      if (seq !== sigHistSeq) return;
      if (!d.ok && d.ok !== undefined) throw new Error("bad");
      paintSigHist(d);
    })
    .catch(() => {
      if (seq !== sigHistSeq) return;
      document.getElementById("sigHistMeta").textContent = "历史信号拉取失败";
      document.getElementById("sigHistBody").innerHTML =
        `<tr><td colspan="10" class="meta">加载失败</td></tr>`;
    });
  const scanP = fetch("/api/review/appearances/" + c)
    .then((r) => r.json())
    .then((d) => {
      if (seq !== sigHistSeq) return;
      if (!d.ok && d.ok !== undefined) throw new Error("bad");
      paintSigHistScans(d);
    })
    .catch(() => {
      if (seq !== sigHistSeq) return;
      if (scanBody) {
        scanBody.innerHTML = `<tr><td colspan="8" class="meta">发散/锅底列表加载失败</td></tr>`;
      }
    });
  await Promise.all([chartP, histP, scanP]);
}
