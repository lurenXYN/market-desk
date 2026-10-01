/** Paint data time, deltas, segments, switches, health banner, phase KPIs, auction, ETFs, and indices. */
function renderDeskStatus(d, v) {
  const phase = d.phase || "—";
  const seg = v.segment || {};
  document.getElementById("dataTime").textContent = (d.updated_at || "").slice(11) || "--:--:--";
  document.getElementById("dataAge").textContent = dataAgeText(d.updated_at);
  const deltasEl = document.getElementById("deltas");
  const deskDeltas = document.getElementById("deskDeltas");
  const deltaRows = d.deltas || [];
  if (deltasEl) deltasEl.innerHTML = deltaRows.map(fmtDelta).join("");
  if (deskDeltas) deskDeltas.hidden = !deltaRows.length;
  document.getElementById("segNote").textContent = seg.note || "";
  document.getElementById("segStrip").innerHTML = (d.session_segments || []).map((s) => {
    const act = s.action || "—";
    const cls = ["seg-card", s.current ? "cur" : "", s.filled ? "" : "empty"].filter(Boolean).join(" ");
    return `<article class="${cls}">
          <div class="k">${s.label || s.segment || ""}${s.current ? " · 今" : ""}</div>
          <div class="act">${act}</div>
          <div class="ml">${s.mainline || "主线未记"}</div>
          <div class="why">${s.size_hint || s.reason || ""}</div>
        </article>`;
  }).join("") || `<div class="meta">分段结论将在竞价/开盘后陆续写入</div>`;
  const switches = d.mainline_switches || [];
  document.getElementById("switchList").innerHTML = switches.length
    ? switches.map((sw) => {
        const t = (sw.switched_at || "").slice(11, 16);
        return `<li><span class="t">${t || "—"}</span>${sw.from_name || "未明"} → <b>${sw.to_name || "—"}</b>
              <span class="meta"> · ${sw.action || ""} · ${sw.phase || ""}</span></li>`;
      }).join("")
    : `<li class="meta">今日尚无确认切换。两板块分差不大时会粘住，短时来回抖不记。</li>`;
  document.getElementById("filter").textContent = d.filter || "";
  document.getElementById("stamp").textContent =
    (d.live ? "盘中 " : "停源 ") + (d.updated_at || "") +
    (d.live ? (" · " + (d.refresh_seconds || 20) + "秒一刷") : " · 午休/收盘不打行情") +
    (d.trading_day === false ? " · 非交易日" : "") +
    (d.warnings && d.warnings.length ? " · 部分源失败" : "");
  document.getElementById("liveDot").className = "dot" + (d.live ? " on" : "");
  const warnEl = document.getElementById("dataBanner");
  const health = d.health || {};
  const warns = [];
  const tips = (health.tips || []).filter(Boolean);
  if (health.score != null && Number(health.score) < 95) warns.push(`健康度 ${health.score}`);
  tips.forEach((t) => warns.push(t));
  if (d.warnings && d.warnings.length && !tips.length) {
    warns.push("行情源异常：" + d.warnings.join("、"));
  }
  if (!d.ok && d.error) warns.push(String(d.error));
  if (d.live && Array.isArray(d.etfs) && d.etfs.length === 0 && !tips.some((t) => t.includes("ETF"))) {
    warns.push("ETF 报价为空，可能腾讯源异常");
  }
  if (d.live && Array.isArray(d.hot_boards) && d.hot_boards.length === 0 && !tips.some((t) => t.includes("热点"))) {
    warns.push("热点板块为空，东财与新浪板块源可能都失败");
  }
  if (warns.length || health.degraded) {
    const lvl = health.degraded && health.level === "ok"
      ? "warn"
      : (health.level || (d.warnings && d.warnings.length ? "bad" : "warn"));
    if (health.degraded && !warns.some((w) => String(w).includes("降级") || String(w).includes("失败率"))) {
      warns.unshift("数据降级");
    }
    warnEl.className = "data-banner " + (lvl === "ok" ? "ok-soft" : (lvl === "warn" ? "warn" : "on"));
    const srcs = Array.isArray(health.sources) ? health.sources : [];
    let drill = "";
    if (srcs.length) {
      const rows = srcs.map((s) => {
        const rate = s.fail_rate;
        const bad = rate != null && Number(rate) >= 30;
        return `<tr class="${bad ? "bad" : ""}">`
          + `<td>${s.name || "—"}</td>`
          + `<td>${s.ok ?? 0}</td>`
          + `<td>${s.fail ?? 0}</td>`
          + `<td>${s.timeout ?? 0}</td>`
          + `<td>${rate == null ? "—" : rate + "%"}</td>`
          + `</tr>`;
      }).join("");
      drill = `<details><summary>源明细 · 点开看 ok/fail/timeout</summary>`
        + `<table class="health-drill"><thead><tr>`
        + `<th>源</th><th>ok</th><th>fail</th><th>timeout</th><th>失败率</th>`
        + `</tr></thead><tbody>${rows}</tbody></table></details>`;
    }
    warnEl.innerHTML = `⚠ ${warns.join(" · ")} <button type="button" class="q" data-term="健康度">?</button>${drill}`;
  } else {
    warnEl.className = "data-banner";
    warnEl.textContent = "";
  }
  paintToastStrip(d.recent_toasts || []);
  document.getElementById("phaseName").innerHTML = termHtml(phase);
  document.getElementById("temp").textContent = (d.temperature ?? "—") + "/100";
  const phaseHelp = document.getElementById("phaseHelp");
  if (phaseHelp) phaseHelp.setAttribute("data-term", GLOSSARY[phase] ? phase : "相位");
  document.getElementById("orb").className = "phase-orb p-" + phase;
  document.getElementById("orb").style.borderColor = phaseColor[phase] || "var(--line)";
  const kpis = d.kpis || [];
  document.getElementById("kpis").innerHTML = kpis.map(k => `
        <div class="kpi">
          <span class="n">${termHtml(k.key)}</span>
          <div class="bar"><i class="hue-${k.hue || "green"}" style="width:${k.fill || 0}%"></i></div>
          <span>${k.value}${k.unit || ""}</span>
        </div>`).join("");
  const m = d.metrics || {};
  document.getElementById("phaseStrip").innerHTML = `
        <div><b>${m.zt ?? 0}</b>${termHtml("涨停")}</div>
        <div><b>${m.zb ?? 0}</b>${termHtml("炸板")}</div>
        <div><b>${m.dt ?? 0}</b>${termHtml("跌停")}</div>
        <div><b>${m.ups ?? 0}/${m.downs ?? 0}</b>涨跌</div>`;
  const cyc = d.cycle || {};
  document.getElementById("cycleNote").textContent = cyc.note || "";
  paintTimeline(d);
  paintCycleEvents(d);
  const a = d.auction || {};
  document.getElementById("aucPremium").innerHTML = fmtPct(m.premium);
  document.getElementById("aucMed").innerHTML = fmtPct(a.median_open);
  document.getElementById("aucHigh").textContent = (a.high_open_share ?? "—") + "%";
  document.getElementById("aucTone").textContent = (a.tone || "—") + (a.locked ? " · 已锁定" : "");
  document.getElementById("etfs").innerHTML = (d.etfs || []).map(e =>
    `<span class="chip${e.stock_no_perm ? " perm" : ""}">${e.name} ${fmtPct(e.pct)}${e.stock_no_perm ? " · ETF可买" : ""}</span>`
  ).join("");
  document.getElementById("idxStrip").innerHTML = (d.indices || []).map((x) => {
    const pct = x.pct;
    const cls = pct == null ? "" : (pct >= 0 ? "up" : "down");
    return `<div class="idx-card">
          <div class="n">${x.name || x.code || ""}</div>
          <div class="p ${cls}">${x.price == null ? "—" : Number(x.price).toFixed(2)}</div>
          <div class="c ${cls}">${pct == null ? "—" : ((pct > 0 ? "+" : "") + Number(pct).toFixed(2) + "%")}</div>
        </div>`;
  }).join("") || `<div class="meta">指数待刷新</div>`;
}
