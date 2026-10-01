function render(d) {
  const v = d.verdict || {};
  renderDeskHead(d, v);
  renderDeskBrief(d, v);
  renderDeskBoxes(d, v);
  renderDeskInsights(d, v);
  renderDeskStatus(d, v);
  renderMarketBoards(d, v);
  renderPositionsPanel(d, v);
}

/** Paint the banner, action pills, gate hint, mainline trio, meter, and no-ETF notice. */
function renderDeskHead(d, v) {
  if (d.glossary && typeof d.glossary === "object") {
    // Merge so a later thin payload never wipes a loaded glossary.
    GLOSSARY = Object.assign({}, GLOSSARY, d.glossary);
  }
  const phase = d.phase || "—";
  const seg = v.segment || {};
  document.getElementById("banner").className = "banner " + bannerClass(v.action);
  const ml = (v.mainline && v.mainline.name) || "未明";
  const act = v.action || "观望";
  document.getElementById("actionPill").textContent = act;
  (() => {
    const strip = document.getElementById("opStrip");
    const tags = document.getElementById("opStripTags");
    if (!strip || !tags) return;
    const items = ((v.recommend || {}).items) || [];
    const bits = [];
    const hasFly = items.some((it) => it.fly_warn);
    const hasHalf = items.some((it) => it.ready_relaxed);
    const hasFull = items.some((it) => it.ready && !it.ready_relaxed);
    const hasProbe = items.some((it) => it.probe_ok);
    if (hasFly) bits.push(`<span class="op-tag fly">将飞·半仓</span>`);
    if (hasFull) bits.push(`<span class="op-tag">可买·满</span>`);
    else if (hasHalf) bits.push(`<span class="op-tag half">可买·半</span>`);
    if (hasProbe && !hasHalf && !hasFull) bits.push(`<span class="op-tag probe">可试探</span>`);
    else if (hasProbe && (hasHalf || hasFull)) bits.push(`<span class="op-tag probe">另有试探</span>`);
    const stale = d.ready_cross_day || {};
    if (stale.line) {
      bits.push(
        `<span class="op-tag stale" title="${String(stale.line).replace(/"/g, "&quot;")}">昨ready作废·${stale.count || ""}</span>`
      );
    }
    strip.hidden = !bits.length;
    tags.innerHTML = bits.join("");
  })();
  document.getElementById("segPill").textContent = seg.label ? ("时段 " + seg.label) : "";
  document.getElementById("phasePill").innerHTML = phase && phase !== "—"
    ? (`相位 ${termHtml(phase)}`) : "";
  const lifePill = document.getElementById("lifePill");
  const lifeStage = (v.mainline && v.mainline.lifecycle) || "";
  const mlStatus = String((v.mainline && v.mainline.status) || "");
  const tipPeak = lifeStage === "ongoing" && mlStatus === "尖峰禁追";
  const lifeLabel = ({ starting: "萌芽", ongoing: "主升", ending: "衰退" })[lifeStage] || "";
  if (lifePill) {
    if (tipPeak) {
      lifePill.textContent = "阶段 主升·尖峰仅观察";
      lifePill.title = "生命周期落在主升，但板块状态为尖峰禁追：只描述扩散强度，不等于可追可买";
    } else {
      lifePill.textContent = lifeLabel ? (`阶段 ${lifeLabel}`) : "";
      lifePill.title = "";
    }
    lifePill.className = "meta " + ({
      starting: "life-start",
      ongoing: tipPeak ? "life-run life-tip" : "life-run",
      ending: "life-end",
    }[lifeStage] || "");
  }
  const gateEl = document.getElementById("gateHint");
  const gate = d.desk_gate_summary || {};
  if (gateEl) {
    const hint = gate.hint || "";
    const reasons = (gate.reasons || []).filter(Boolean);
    if (hint || reasons.length) {
      gateEl.hidden = false;
      gateEl.className = "gate-hint"
        + (gate.can_buy ? " on-buy" : (gate.can_probe ? " on-probe" : ""));
      if (gate.can_buy) {
        gateEl.innerHTML = `<b>可现买</b> · ${hint || act}`;
      } else if (gate.can_probe) {
        gateEl.innerHTML = `<b>可小仓试探</b> · ${hint || reasons[0] || act}`
          + (gate.short_miss
            ? `<span class="short-miss">${gate.short_miss}</span>`
            : (reasons.length > 1
              ? `<span class="meta">（${reasons.slice(0, 3).join(" · ")}）</span>`
              : ""));
      } else {
        const missLine = gate.short_miss
          || ((gate.progress && gate.progress.missing && gate.progress.missing.length)
            ? `还差：${gate.progress.missing.slice(0, 3).join(" · ")}`
            : "");
        gateEl.innerHTML = `<b>${(hint || "").startsWith("靠近买点") ? "靠近买点" : "为何不能现买"}</b> · ${hint || reasons[0] || act}`
          + (missLine
            ? `<span class="short-miss">${missLine}</span>`
            : (reasons.length > 1
              ? `<span class="meta">（${reasons.slice(0, 3).join(" · ")}）</span>`
              : ""));
      }
    } else {
      gateEl.hidden = true;
      gateEl.innerHTML = "";
    }
  }
  const trioPhase = document.getElementById("trioPhase");
  const trioMain = document.getElementById("trioMain");
  const trioStage = document.getElementById("trioStage");
  if (trioPhase) trioPhase.textContent = (phase && phase !== "—") ? phase : "相位未明";
  if (trioMain) trioMain.textContent = ml || "主线未明";
  if (trioStage) {
    trioStage.textContent = tipPeak
      ? "主升·尖峰仅观察"
      : (lifeLabel || "阶段未明");
    trioStage.title = tipPeak
      ? "尖峰禁追仍可落在主升桶：只看扩散，不当现买"
      : "";
    trioStage.className = "w stage " + ({
      starting: "life-start",
      ongoing: tipPeak ? "life-run life-tip" : "life-run",
      ending: "life-end",
    }[lifeStage] || "");
  }
  const softEtf = !!(v.mainline && v.mainline.etf_soft);
  const noExactEtf = !!(v.mainline && v.mainline.etf_mapped === false && !softEtf);
  document.getElementById("headline").innerHTML =
    `${termHtml("实时主线")} · ${ml}`
    + (softEtf
      ? `<span class="soft-etf-badge">近似ETF</span>`
      : (noExactEtf ? `<span class="no-etf-badge">无ETF映射</span>` : ""));
  const meterEl = document.getElementById("mlMeter");
  if (meterEl) {
    const why = (v.mainline && v.mainline.why) || {};
    if (why.reason || why.theme || why.gap != null) {
      meterEl.hidden = false;
      const heldSec = why.held_seconds;
      let heldTxt = "—";
      if (heldSec != null) {
        const hs = Number(heldSec);
        heldTxt = hs >= 60
          ? `${Math.floor(hs / 60)}分${hs % 60 ? (hs % 60) + "秒" : ""}`
          : `${hs}秒`;
        if (why.hold_min_seconds) {
          const needHold = Number(why.hold_min_seconds);
          heldTxt += needHold >= 60
            ? `/${Math.floor(needHold / 60)}分`
            : `/${needHold}秒`;
        }
      }
      const gap = why.gap;
      const need = why.need;
      let gapCls = "";
      let gapTxt = "分差 —";
      if (gap != null && need != null) {
        const remain = Number(need) - Number(gap);
        gapTxt = `分差 ${gap > 0 ? "+" : ""}${gap}/${need}`;
        if (why.kept && remain > 0) {
          gapTxt += ` · 差${remain.toFixed(1)}换防`;
          gapCls = " warn";
        } else if (!why.kept) {
          gapCls = " switch";
        } else {
          gapCls = " ok";
        }
      } else if (gap != null) {
        gapTxt = `分差 ${gap > 0 ? "+" : ""}${gap}`;
      }
      const shortWhy = String(why.reason || "")
        .replace(/分差[^；。]*/g, "")
        .replace(/[；。]\s*$/, "")
        .slice(0, 36);
      meterEl.innerHTML =
        `<span class="chip">主题 ${why.theme || "—"}</span>`
        + `<span class="chip${gapCls}">${gapTxt}</span>`
        + `<span class="chip${why.in_hold ? " warn" : ""}">持有 ${heldTxt}</span>`
        + (why.kept === false
          ? `<span class="chip switch">已换防</span>`
          : (why.kept ? `<span class="chip ok">粘滞</span>` : ""))
        + (shortWhy
          ? `<span class="why-short" title="${(why.reason || "").replace(/"/g, "&quot;")}">${shortWhy}${(why.reason || "").length > 36 ? "…" : ""} <button type="button" class="q" data-term="主线质量">?</button></span>`
          : `<button type="button" class="q" data-term="主线质量">?</button>`);
    } else {
      meterEl.hidden = true;
      meterEl.innerHTML = "";
    }
  }
  const whyEl = document.getElementById("mlWhy");
  if (whyEl) {
    const why = (v.mainline && v.mainline.why) || {};
    if (why.reason) {
      whyEl.hidden = false;
      const held = why.held_seconds != null
        ? `${why.held_seconds}s/${why.hold_min_seconds || 0}s`
        : "—";
      const gapTxt = why.gap == null ? "—" : ((why.gap > 0 ? "+" : "") + why.gap);
      const top = (why.top || []).slice(0, 3).map((t) =>
        `${t.name} ${t.score}${t.zt_n != null ? "(" + t.zt_n + "停)" : ""}`
      ).join(" · ");
      whyEl.innerHTML =
        `<div><b>主线为什么是它</b> <button type="button" class="q" data-term="主线透明">?</button></div>`
        + `<div style="margin-top:4px">${why.reason}</div>`
        + (why.kept === false && why.sticky_name
          ? `<div class="top" style="margin-top:4px">换防旁注：${why.sticky_name} → ${why.name || ml || "—"}`
            + (why.gap == null ? "" : ` · 分差 ${why.gap > 0 ? "+" : ""}${why.gap} / 需 ${why.need ?? "—"}`)
            + (why.same_theme_challenge ? " · 同主题" : "")
            + (why.in_hold ? " · 持有期满后切换" : "")
            + `</div>`
          : (why.kept && why.challenger_name
            ? `<div class="top" style="margin-top:4px">换防旁注：保留「${why.name || ml}」，挑战者 ${why.challenger_name}`
              + (why.gap == null ? "" : ` 分差 ${why.gap > 0 ? "+" : ""}${why.gap} &lt; 需 ${why.need ?? "—"}`)
              + `</div>`
            : ""))
        + `<div style="margin-top:6px">`
        + `<span class="chip">主题 ${why.theme || "—"}</span>`
        + `<span class="chip">分 ${why.score ?? "—"}</span>`
        + `<span class="chip">分差 ${gapTxt} / 需 ${why.need ?? "—"}</span>`
        + `<span class="chip">持有 ${held}</span>`
        + (why.kept === false ? `<span class="chip">已换防</span>` : (why.kept ? `<span class="chip">粘滞保留</span>` : ""))
        + (why.etf_exact ? `<span class="chip">精确ETF</span>` : `<span class="chip">无精确ETF</span>`)
        + (why.rep_label ? `<span class="chip">${why.rep_label}${why.rep_adj != null && why.rep_adj !== 0 ? " " + (why.rep_adj > 0 ? "+" : "") + why.rep_adj : ""}</span>` : "")
        + `</div>`
        + (top ? `<div class="top">池内前三：${top}</div>` : "")
        + ((why.similar_peers || []).length
          ? `<div class="top">相似板块：${(why.similar_peers || []).slice(0, 3).map((p) => {
              const whyBits = (p.why || []).slice(0, 2).join("·");
              return `${p.name} ${Math.round((p.sim || 0) * 100)}%`
                + (whyBits ? `<span class="meta">（${whyBits}）</span>` : "");
            }).join(" · ")} <button type="button" class="q" data-term="板块相似度">?</button></div>`
          : "");
    } else {
      whyEl.hidden = true;
      whyEl.innerHTML = "";
    }
  }
  const noEtf = document.getElementById("noEtfBanner");
  if (noEtf) {
    if (softEtf && ml && ml !== "未明") {
      noEtf.hidden = false;
      noEtf.className = "no-etf-banner soft";
      const veh = (v.carrier && v.carrier.name) || (v.vehicle && v.vehicle.name) || "行业ETF";
      noEtf.textContent = `主线无精确ETF映射，载体为近似「${veh}」：仓位宜更小；创业板/科创个股仍不推荐。`;
    } else if (noExactEtf && ml && ml !== "未明") {
      noEtf.hidden = false;
      noEtf.className = "no-etf-banner";
      noEtf.textContent = "主线暂无映射 ETF：优先盯下方主板回踩票；创业板/科创个股仍不推荐。";
    } else {
      noEtf.hidden = true;
    }
  }
}
