/** Paint theme memory, adapt note, enrich hint, whitebox panel, and the sell box. */
function renderDeskInsights(d, v) {
  const themeMemEl = document.getElementById("themeMem");
  const themeMemBody = document.getElementById("themeMemBody");
  const themeMemNote = document.getElementById("themeMemNote");
  if (themeMemEl && themeMemBody) {
    const tm = d.theme_memory || {};
    const reps = (tm.reputation || []).slice().sort((a, b) =>
      (Number(a.score_adj) || 0) - (Number(b.score_adj) || 0)
    );
    const settle = tm.settle || {};
    if (!reps.length) {
      themeMemEl.hidden = true;
      themeMemBody.innerHTML = "";
      if (themeMemNote) themeMemNote.textContent = "";
    } else {
      themeMemEl.hidden = false;
      const bits = [];
      if (settle.prior_date) bits.push(`结算 ${settle.prior_date}→${settle.trade_date || ""}`);
      if (settle.fades != null) bits.push(`熄火 ${settle.fades}`);
      if (settle.persists != null) bits.push(`续热 ${settle.persists}`);
      if (settle.mainline) bits.push(`昨主线 ${settle.mainline}`);
      if (themeMemNote) {
        const canAdj = !!(authUser && authUser.role === "admin");
        themeMemNote.textContent = (bits.join(" · ") || "按主题记忆软调主线分")
          + " · 点卡片看主题链"
          + (canAdj ? " · admin 可手动 ±1 / 清零" : " · 手调仅管理员");
      }
      setModStamp("themeMemStamp", d.updated_at, { label: "刷新" });
      const canAdj = !!(authUser && authUser.role === "admin");
      themeMemBody.innerHTML = reps.slice(0, 8).map((r) => {
        const adj = Number(r.score_adj) || 0;
        const auto = Number(r.auto_adj != null ? r.auto_adj : adj) || 0;
        const man = Number(r.manual_adj) || 0;
        const trade = Number(r.trade_adj) || 0;
        const cls = adj <= -2 ? "bad" : (adj >= 2 ? "good" : "");
        const fmt = (v) => (v === 0 ? "0" : ((v > 0 ? "+" : "") + v));
        const key = String(r.theme_key || "").replace(/"/g, "&quot;");
        const thin = (Number(r.fade_n) || 0) + (Number(r.persist_n) || 0) < 3;
        const adjRow = canAdj
          ? `<div class="adj-row">
                <button type="button" class="adj-btn" data-theme-adj="-1" title="手动 −1">−1</button>
                <button type="button" class="adj-btn" data-theme-adj="1" title="手动 +1">+1</button>
                <button type="button" class="adj-btn clear" data-theme-clear="1" title="清除手调" ${man === 0 ? "disabled" : ""}>清手调</button>
              </div>`
          : "";
        return `<div class="theme-mem-item ${cls}" data-theme="${key}">
              <div class="name">${r.theme_key || "—"}</div>
              <div class="lab">${r.label || "中性"} · 合计 ${fmt(adj)}${thin ? " · 薄样本弱进主线" : ""}</div>
              <div class="meta">自动 ${fmt(auto)} · 成交 ${fmt(trade)} · 手调 ${fmt(man)}</div>
              <div class="meta">fade ${r.fade_n ?? 0} / persist ${r.persist_n ?? 0}</div>
              ${adjRow}
            </div>`;
      }).join("");
    }
  }
  const adaptNote = document.getElementById("adaptNote");
  if (adaptNote) {
    const ad = (d.verdict || {}).adapt || {};
    const bits = [];
    if (ad.context && ad.context.label) bits.push(`情景 ${ad.context.label}`);
    if (ad.size_compose && ad.size_compose.note) bits.push(ad.size_compose.note);
    else if (ad.size_mult != null && Number(ad.size_mult) !== 1) bits.push(`仓位×${ad.size_mult}`);
    if (ad.auto_tune && ad.auto_tune.note) bits.push(ad.auto_tune.note);
    if (ad.auto_tune && ad.auto_tune.clamp != null) {
      const cp = Math.round(Number(ad.auto_tune.clamp) * 100);
      const mult = ad.auto_tune.pb_min_mult;
      if (Number.isFinite(cp) && cp > 0) {
        bits.push(
          mult != null && Number(mult) !== 1
            ? `本桶已夹紧 ±${cp}%（回踩×${Number(mult).toFixed(2)}）`
            : `本桶夹紧 ±${cp}%`
        );
      }
    } else if (ad.size_heat && ad.size_heat.note) bits.push(ad.size_heat.note);
    if (ad.exec_size && ad.exec_size.note && Number(ad.exec_size.size_mult) !== 1) bits.push(ad.exec_size.note);
    if (ad.context_gate && ad.context_gate.ok && ad.context_gate.note) bits.push(ad.context_gate.note);
    if (ad.sticky_margin && ad.sticky_margin.ok && ad.sticky_margin.note) bits.push(ad.sticky_margin.note);
    if (ad.sell_mfe && ad.sell_mfe.ok && ad.sell_mfe.note) bits.push(ad.sell_mfe.note);
    if (ad.segment_sell && ad.segment_sell.note) bits.push(ad.segment_sell.note);
    if (ad.pullback_sweet && ad.pullback_sweet.ok && ad.pullback_sweet.note) bits.push(ad.pullback_sweet.note);
    if (ad.desk_source_bias && ad.desk_source_bias.ok && ad.desk_source_bias.note) bits.push(ad.desk_source_bias.note);
    if (ad.whitebox && ad.whitebox.ok && ad.whitebox.note) bits.push(ad.whitebox.note);
    adaptNote.textContent = bits.join(" · ");
    adaptNote.hidden = !bits.length;
  }
  const enrichHint = document.getElementById("enrichHint");
  if (enrichHint) {
    enrichHint.hidden = !d.enrich_pending;
    enrichHint.textContent = d.enrich_pending ? "股东/日线补齐中…" : "";
  }
  const wbPanel = document.getElementById("whiteboxPanel");
  const wbBody = document.getElementById("whiteboxBody");
  const wbNote = document.getElementById("whiteboxNote");
  const wbDelta = document.getElementById("whiteboxDelta");
  if (wbPanel && wbBody) {
    const wb = ((d.verdict || {}).adapt || {}).whitebox || {};
    const weights = (wb.weights || []).filter((w) => w.key !== "bias").slice(0, 8);
    if (!wb.ok || !weights.length) {
      wbPanel.hidden = true;
      wbBody.innerHTML = "";
      if (wbNote) wbNote.textContent = "";
      if (wbDelta) wbDelta.textContent = "";
    } else {
      wbPanel.hidden = false;
      if (wbNote) {
        const ho = wb.holdout || {};
        wbNote.textContent = `n=${wb.n} · 命中 ${wb.hit_rate ?? "—"}%`
          + (ho.ok ? ` · 样本外 ${ho.accuracy ?? "—"}%（lift ${ho.lift_pp ?? "—"}pp）` : "");
      }
      const maxAbs = Math.max(0.2, ...weights.map((w) => Math.abs(Number(w.weight) || 0)));
      wbBody.innerHTML = weights.map((w) => {
        const v = Number(w.weight) || 0;
        const cls = v < 0 ? "w neg" : "w";
        const txt = (v > 0 ? "+" : "") + v.toFixed(2);
        const pct = Math.min(100, Math.abs(v) / maxAbs * 100);
        const fillCls = v < 0 ? "wb-bar-fill neg" : "wb-bar-fill pos";
        return `<div class="wb-bar-row"><span>${w.label || w.key}</span>`
          + `<span class="wb-bar-track"><span class="${fillCls}" style="width:${(pct/2).toFixed(1)}%"></span></span>`
          + `<span class="${cls}">${txt}</span></div>`;
      }).join("");
      if (wbDelta) {
        const deltas = wb.deltas || [];
        wbDelta.textContent = deltas.length
          ? ("近期变化 " + deltas.slice(0, 3).map((x) =>
              `${x.label} ${x.from}→${x.to}`
            ).join(" · "))
          : (wb.holdout && wb.holdout.note ? wb.holdout.note : "");
      }
    }
  }
  const sell = d.sell_advice || {};
  const sellBox = document.getElementById("sellbox");
  sellBox.className = "sellbox " + (sell.sell ? "on" : "off");
  document.getElementById("sellline").textContent = sell.text || sell.title || "暂无仓位";
  document.getElementById("sellmeta").textContent = sell.size_note || "";
  const sb = sell.sell_bias || {};
  if (sb.note || (sb.etf && sb.etf.note) || (sb.stock && sb.stock.note)) {
    const parts = [];
    if (sb.etf && sb.etf.ok) parts.push(`ETF闭环 ${sb.etf.hit_rate}%`);
    if (sb.stock && sb.stock.ok) parts.push(`个股闭环 ${sb.stock.hit_rate}%`);
    if (parts.length) {
      const el = document.getElementById("sellmeta");
      el.textContent = (el.textContent ? el.textContent + " · " : "") + parts.join(" · ");
    }
  }
  document.getElementById("sellItems").innerHTML = (sell.items || []).map(sellCard).join("");
}
