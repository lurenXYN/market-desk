async function loadReview(force, date) {
  const now = Date.now();
  const want = (date !== undefined && date !== null)
    ? String(date).trim()
    : (reviewViewDate || "").trim();
  if (!force && date === undefined && now - reviewLoadedAt < 30000) return;
  try {
    const params = new URLSearchParams();
    if (want) params.set("date", want);
    if (reviewVsMlMode) params.set("vs_ml", reviewVsMlMode);
    const ocEl = document.getElementById("revOcMode");
    const oc = (ocEl && ocEl.value) || reviewOcMode || "classic";
    reviewOcMode = oc;
    if (oc) params.set("oc", oc);
    const q = params.toString() ? ("?" + params.toString()) : "";
    setModStamp("revStamp", null, { loading: true, label: "复盘" });
    const r = await fetch("/api/review" + q);
    const d = await r.json();
    const day = d.view_date || (d.summary || {}).view_date || want;
    const seq = ++reviewZtSeq;
    reviewZtLoading = true;
    reviewLoadedAt = Date.now();
    reviewRefreshedAt = new Date().toTimeString().slice(0, 8);
    setModStamp("revStamp", reviewRefreshedAt, {
      label: "复盘",
      title: d.cache_hit ? "命中复盘缓存" : "复盘主表已刷新",
    });
    if (!d.trends_ready) {
      for (const row of (d.signals || [])) {
        if (row.trend_ok || row.trend_down || row.daily_trend) continue;
        row.trend_pending = true;
      }
    }
    paintReview(d);
    const tSeq = ++reviewTrendSeq;
    loadReviewTrends(day, tSeq, d.trends_fp || "");
    loadReviewZtYtd(day, seq);
  } catch (e) {
    const n = Math.max(visibleRevCols().length, 1);
    document.getElementById("revBody").innerHTML =
      `<tr><td colspan="${n}" class="meta">复盘加载失败</td></tr>`;
    setModStamp("revStamp", null, { stale: true, label: "复盘" });
  }
}

function shiftReviewDay(delta) {
  const dates = lastReview.dates || (lastReview.summary || {}).dates || [];
  if (!dates.length) return;
  const cur = reviewViewDate || dates[0];
  let idx = dates.indexOf(cur);
  if (idx < 0) idx = 0;
  // dates newest-first: +1 = older, -1 = newer
  const next = dates[idx + delta];
  if (next) loadReview(true, next);
}

function paintTimeline(d) {
  const cyc = (d && d.cycle) || {};
  const nodes = cyc.nodes || [];
  const active = cycleSel == null
    ? (nodes.find((n) => n.current) || {}).i
    : cycleSel;
  document.getElementById("timeline").innerHTML = nodes.map((n) => {
    const label = n.current ? "今" : ("D" + (n.i >= 0 ? "+" + n.i : n.i));
    const can = n.has_data || n.current;
    const cls = [
      "node",
      n.current ? "now" : "",
      active === n.i ? "sel" : "",
      can ? "" : "muted",
    ].filter(Boolean).join(" ");
    return `<button type="button" class="${cls}" data-i="${n.i}" ${can ? "" : "disabled"}>${label}</button>`;
  }).join("");
}

function paintCycleEvents(d) {
  const cyc = (d && d.cycle) || {};
  const nodes = cyc.nodes || [];
  const liveHtml = (d.events || []).map((e) =>
    `<li class="${e.tone === "warn" ? "warn" : (e.tone === "hot" ? "hot" : (e.tone === "ice" ? "ice" : ""))}">${e.text}</li>`
  ).join("");
  let node = null;
  if (cycleSel != null) {
    node = nodes.find((n) => n.i === cycleSel) || null;
  }
  if (!node || node.current) {
    document.getElementById("cycleNote").textContent = cyc.note || "";
    document.getElementById("events").innerHTML = liveHtml || `<li class="meta">暂无事件</li>`;
    return;
  }
  const det = node.detail;
  if (!det) {
    document.getElementById("cycleNote").textContent =
      (node.label || ("D" + node.i)) + " 尚无日级样本";
    document.getElementById("events").innerHTML =
      `<li class="meta">这一天还没有本地日级数据，先攒几天再回看。</li>`;
    return;
  }
  document.getElementById("cycleNote").textContent =
    `${det.trade_date || ""} · ${node.label || ""} · 点击「今」回到实时`;
  document.getElementById("events").innerHTML = [
    `<li>相位 ${det.phase || "—"} · 温度 ${det.temperature ?? "—"}</li>`,
    `<li class="hot">涨停 ${det.zt ?? "—"} / 跌停 ${det.dt ?? "—"} · 涨跌 ${det.ups ?? "—"}/${det.downs ?? "—"}</li>`,
    `<li>高度 ${det.height ?? "—"} · 晋级% ${det.promotion ?? "—"} · 炸板% ${det.zb_rate ?? "—"}</li>`,
    `<li>1→2 ${det.promo_1_2 ?? "—"}% · 2→3 ${det.promo_2_3 ?? "—"}% · 梯队 ${det.ladder_fill ?? "—"}${det.ladder_gap ? "·断" : ""}</li>`,
    `<li>昨停溢价 ${det.premium ?? "—"} · 成交亿 ${det.amount_yi ?? "—"}</li>`,
    det.event ? `<li class="warn">${det.event}</li>` : "",
  ].filter(Boolean).join("");
}
