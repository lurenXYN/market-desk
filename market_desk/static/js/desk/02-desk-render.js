function render(d) {
  if (d.glossary && typeof d.glossary === "object") {
    // Merge so a later thin payload never wipes a loaded glossary.
    GLOSSARY = Object.assign({}, GLOSSARY, d.glossary);
  }
  const v = d.verdict || {};
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
  // One-line summary only; long algo text stays folded.
  const one = (v.meaning || v.reason || "").split("（")[0].trim();
  document.getElementById("meaning").textContent = one || "";
  const pb = v.playbook || {};
  document.getElementById("pbDo").textContent = pb.do || (pb.lines && pb.lines[0]) || "—";
  document.getElementById("pbDont").textContent = pb.dont || (pb.lines && pb.lines[1]) || "—";
  document.getElementById("pbSize").textContent = pb.size || (pb.lines && pb.lines[2]) || "—";
  paintPosTargetBar(d);
  const seasonDesk = document.getElementById("seasonDesk");
  const seasonHint = ((d.seasonality || {}).desk) || {};
  if (seasonDesk) {
    const show = !!seasonHint.show && !!(seasonHint.line || seasonHint.detail);
    seasonDesk.hidden = !show;
    if (show) {
      document.getElementById("seasonDeskLine").textContent = seasonHint.line || "";
      document.getElementById("seasonDeskDetail").textContent = seasonHint.detail || "";
      setModStamp("seasonDeskStamp", d.updated_at, { label: "刷新" });
    }
  }
  const brief = d.morning_brief || {};
  document.getElementById("morningFocus").textContent = brief.focus || "等待数据";
  document.getElementById("morningSeg").textContent = brief.segment
    ? (`时段 ${brief.segment}`)
    : "";
  document.getElementById("morningBullets").innerHTML = (brief.bullets || [])
    .map((b) => `<li>${b}</li>`).join("");
  const chk = brief.checklist || {};
  document.getElementById("morningChk").innerHTML = [
    chk.do ? `<span><b>能做</b> ${chk.do}</span>` : "",
    chk.dont ? `<span><b>不做</b> ${chk.dont}</span>` : "",
    chk.size ? `<span><b>仓位</b> ${chk.size}</span>` : "",
  ].filter(Boolean).join("");
  window.__morningMarkdown = brief.markdown || "";
  const nrBlock = document.getElementById("newsRadarBlock");
  const nrHd = document.getElementById("newsRadarHd");
  const nrStrip = document.getElementById("newsRadarStrip");
  const nrNote = document.getElementById("newsRadarNote");
  const nrPanel = document.getElementById("newsRadarPanel");
  const nrCheck = document.getElementById("newsRadarCheck");
  if (nrBlock && nrStrip) {
    const nr = d.news_radar || {};
    const enabled = !!nr.enabled;
    const rows = (nr.sectors || []).slice(0, 5);
    if (!enabled) {
      nrBlock.hidden = true;
      if (nrPanel) { nrPanel.style.display = "none"; nrPanel.innerHTML = ""; nrPanel.dataset.open = ""; }
      if (nrCheck) { nrCheck.hidden = true; nrCheck.innerHTML = ""; }
      if (nrNote) { nrNote.hidden = true; nrNote.textContent = ""; }
      if (nrHd) nrHd.innerHTML = "";
    } else {
      nrBlock.hidden = false;
      const st = nr.status_label || nr.status || "";
      const dotCls = nr.status === "online" ? "on"
        : (nr.status === "offline" ? "off" : "warn");
      if (nrHd) {
        nrHd.innerHTML = `<span class="nr-dot ${dotCls}" title="新闻雷达状态"></span>`
          + `<span class="nr-title">新闻雷达</span>`
          + `<span>${st || "—"}</span>`
          + (nr.updated_at ? `<span class="meta">${nr.updated_at}</span>` : "");
      }
      if (rows.length) {
        nrStrip.innerHTML = rows.map((r, i) => {
          const pct = r.board_pct == null ? ""
            : `${Number(r.board_pct) >= 0 ? "+" : ""}${Number(r.board_pct).toFixed(1)}%`;
          const bits = [r.confirm_label, r.tone_label && r.tone_label !== "中性" ? r.tone_label : "", pct]
            .filter(Boolean).join(" · ");
          const clash = (r.mainline_match === false && nr.mainline_name) ? " clash" : "";
          return `<button type="button" class="nr-chip${clash}" data-nr-i="${i}">`
            + `<b>${r.sector || ""}</b>`
            + (bits ? `<span class="sub">${bits}</span>` : "")
            + `</button>`;
        }).join("");
        nrStrip.querySelectorAll("[data-nr-i]").forEach((btn) => {
          btn.onclick = () => {
            const i = Number(btn.getAttribute("data-nr-i") || 0);
            const r = rows[i];
            if (!r || !nrPanel) return;
            nrStrip.querySelectorAll(".nr-chip").forEach((el) => el.classList.remove("open"));
            if (nrPanel.dataset.open === String(i) && nrPanel.style.display !== "none") {
              nrPanel.style.display = "none";
              nrPanel.dataset.open = "";
              return;
            }
            btn.classList.add("open");
            const arts = (r.articles || []).slice(0, 3).map((a) => {
              const t = a.title || "";
              return a.url
                ? `<li><a href="${a.url}" target="_blank" rel="noopener">${t}</a> <span class="meta">${a.source || ""}</span></li>`
                : `<li>${t}</li>`;
            }).join("");
            const etfs = (r.etfs || []).join(" ");
            nrPanel.innerHTML = `<div><b>${r.sector || ""}</b>`
              + (r.confirm_label ? ` · ${r.confirm_label}` : "")
              + (r.tone_label ? ` · ${r.tone_label}` : "")
              + `</div>`
              + (r.mainline_note ? `<div class="meta">${r.mainline_note}</div>` : "")
              + (r.action_hint ? `<div style="margin:4px 0">${r.action_hint}</div>` : "")
              + (etfs ? `<div class="meta">ETF：<code>${etfs}</code> <button type="button" class="copy-btn" id="nrCopyEtf">复制</button></div>` : "")
              + (arts ? `<ul>${arts}</ul>` : "<div class='meta'>暂无新闻链接</div>");
            nrPanel.style.display = "block";
            nrPanel.dataset.open = String(i);
            const cp = document.getElementById("nrCopyEtf");
            if (cp && etfs) {
              cp.onclick = async () => {
                try { await navigator.clipboard.writeText(etfs); cp.textContent = "已复制"; }
                catch (_) { cp.textContent = "复制失败"; }
              };
            }
          };
        });
      } else {
        nrStrip.innerHTML = `<div class="meta">暂无热板块 · ${st || "无数据"}</div>`;
        if (nrPanel) { nrPanel.style.display = "none"; nrPanel.innerHTML = ""; nrPanel.dataset.open = ""; }
      }
      if (nrNote) {
        if (nr.mainline_note) {
          nrNote.hidden = false;
          nrNote.textContent = nr.mainline_note;
        } else {
          nrNote.hidden = true;
          nrNote.textContent = "";
        }
      }
      const chk = (nr.checklist || []).slice(0, 3);
      if (nrCheck) {
        if (chk.length) {
          nrCheck.hidden = false;
          nrCheck.innerHTML = `<div class="nr-check-hd">盘前清单（软）`
            + ` <button type="button" class="copy-btn" id="nrCopyChk">复制</button></div>`
            + chk.map((c) => {
              const pct = c.board_pct == null ? ""
                : ` ${Number(c.board_pct) >= 0 ? "+" : ""}${Number(c.board_pct).toFixed(1)}%`;
              const etf = (c.etfs || [])[0] || "";
              return `<div class="nr-check-item"><b>${c.sector || ""}</b>`
                + (c.confirm_label ? ` · ${c.confirm_label}` : "")
                + pct
                + (etf ? ` · ${etf}` : "")
                + (c.action_hint ? `<div class="meta">${c.action_hint}</div>` : "")
                + `</div>`;
            }).join("");
          const copyChk = document.getElementById("nrCopyChk");
          if (copyChk) {
            copyChk.onclick = async () => {
              const text = chk.map((c) => {
                const etf = (c.etfs || [])[0] || "";
                return `${c.sector || ""} ${c.confirm_label || ""} ${etf} ${c.action_hint || ""}`.trim();
              }).join("\n");
              try { await navigator.clipboard.writeText(text); copyChk.textContent = "已复制"; }
              catch (_) { copyChk.textContent = "复制失败"; }
            };
          }
        } else {
          nrCheck.hidden = true;
          nrCheck.innerHTML = "";
        }
      }
    }
  }
  document.getElementById("reason").textContent = v.reason || "";
  document.getElementById("narrative").textContent = v.narrative || "";
  document.getElementById("detail").textContent = v.detail || "";
  document.getElementById("bans").innerHTML = (v.bans && v.bans.length)
    ? (termHtml("禁追") + " " + v.bans.join(" / ")) : "";
  const more = document.getElementById("deskMore");
  if (more && !(v.reason || v.narrative || v.detail || (v.bans && v.bans.length))) {
    more.hidden = true;
  } else if (more) {
    more.hidden = false;
  }
  const rec = v.recommend || {};
  const box = document.getElementById("buybox");
  const buyMode = (v.action === "可买入" || v.action === "可小仓") && rec.buy;
  const watchMode = v.action === "观察回踩" || v.action === "观察"
    || (!buyMode && (rec.items || []).length && !rec.buy);
  box.className = "buybox " + (buyMode ? "on" : (watchMode ? "watch" : "off"));
  document.getElementById("buyline").textContent = rec.text || rec.title || "暂不买入";
  const riskMeta = rec.risk_meta || {};
  document.getElementById("buymeta").textContent = rec.size_note || "";
  if (riskMeta.account_equity) {
    document.getElementById("buystop").textContent =
      (rec.stop || "")
      + ((rec.stop ? " · " : "") + `账户 ${fmtMoney(riskMeta.account_equity)} · 单笔风险 ${riskMeta.risk_pct_per_trade ?? "—"}%`
        + (riskMeta.size_mult != null && Number(riskMeta.size_mult) !== 1
          ? ` · 合流×${riskMeta.size_mult}` : "")
        + (riskMeta.size_compose && riskMeta.size_compose.clamped ? "（已夹紧）" : "")
        + (riskMeta.cool_note ? ` · ${riskMeta.cool_note}` : ""));
  } else {
    document.getElementById("buystop").textContent = rec.stop || "";
  }
  document.getElementById("buyavoid").textContent = rec.avoid || "";
  document.getElementById("recItems").innerHTML = (rec.items || []).map(recCard).join("");
  const dragonRec = v.dragon_recommend || null;
  const dragonBox = document.getElementById("dragonbox");
  if (dragonBox) {
    const dragonItems = (dragonRec && dragonRec.items) || [];
    const hasMain = !!(ml && ml !== "未明");
    if (dragonItems.length) {
      dragonBox.hidden = false;
      document.getElementById("dragonline").textContent =
        dragonRec.text || dragonRec.title || "龙头排";
      document.getElementById("dragonmeta").textContent =
        dragonRec.size_note || "主线可到位；支线/联动龙头只观察，与回踩副卡并行";
      const dn = document.getElementById("dragonNote");
      if (dn) dn.textContent = `· ${dragonItems.length}只`;
      document.getElementById("dragonItems").innerHTML =
        dragonItemsHtml(dragonItems);
    } else if (hasMain) {
      dragonBox.hidden = false;
      document.getElementById("dragonline").textContent = "暂无龙头排";
      document.getElementById("dragonmeta").textContent =
        "主线板情绪/中军龙未成形，或均在涨停观察区（涨停不进复盘）";
      const dn = document.getElementById("dragonNote");
      if (dn) dn.textContent = "";
      document.getElementById("dragonItems").innerHTML =
        `<div class="meta">等确认异动或趋势回踩后再看；封板只观察</div>`;
    } else {
      dragonBox.hidden = true;
      document.getElementById("dragonItems").innerHTML = "";
    }
  }
  const side = v.side_mainline || null;
  const sideRec = v.side_recommend || {};
  const sideBox = document.getElementById("sidebox");
  if (sideBox) {
    if (side && side.name) {
      sideBox.hidden = false;
      const gap = side.score_gap;
      const lifeMap = { starting: "萌芽", ongoing: "主升", ending: "衰退" };
      const sideLife = lifeMap[side.lifecycle] || "";
      document.getElementById("sideline").textContent =
        sideRec.text || sideRec.title || (`观察 · ${side.name}`);
      document.getElementById("sidemeta").textContent =
        (sideRec.size_note || "仅观察回踩，不当现买")
        + (side.status ? ` · ${side.status}` : "")
        + (side.pct == null ? "" : ` · ${Number(side.pct) > 0 ? "+" : ""}${Number(side.pct).toFixed(2)}%`)
        + (sideLife ? ` · ${sideLife}` : "");
      const gapEl = document.getElementById("sideGap");
      if (gapEl) {
        gapEl.textContent = gap == null ? "" : (`· 与主线分差 ${gap}`);
      }
      document.getElementById("sideItems").innerHTML = (sideRec.items || []).length
        ? (sideRec.items || []).map(recCard).join("")
        : `<div class="meta">暂无合格回踩票，可先盯板块状态</div>`;
    } else {
      sideBox.hidden = true;
      document.getElementById("sideItems").innerHTML = "";
    }
  }
  const link = v.link_mainline || null;
  const linkRec = v.link_recommend || {};
  const linkBox = document.getElementById("linkbox");
  if (linkBox) {
    const linkItems = (linkRec.items || []);
    if (link && link.name && linkItems.length) {
      linkBox.hidden = false;
      const simPct = link.sim == null ? null : Math.round(Number(link.sim) * 100);
      document.getElementById("linkline").textContent =
        linkRec.text || linkRec.title || (`联动 · ${link.name}`);
      document.getElementById("linkmeta").textContent =
        (linkRec.size_note || "主线暂无现买点时，相似板块回踩可小仓")
        + (link.status ? ` · ${link.status}` : "")
        + (link.pct == null ? "" : ` · ${Number(link.pct) > 0 ? "+" : ""}${Number(link.pct).toFixed(2)}%`);
      const simEl = document.getElementById("linkSim");
      if (simEl) {
        simEl.textContent = simPct == null
          ? ""
          : (`· 相对主线相似 ${simPct}%` + (link.mainline_name ? `（${link.mainline_name}）` : ""));
      }
      document.getElementById("linkItems").innerHTML =
        linkItems.map(recCard).join("");
    } else if (link && link.name) {
      linkBox.hidden = false;
      document.getElementById("linkline").textContent = `联动 · ${link.name}`;
      document.getElementById("linkmeta").textContent =
        "已点名相似板，但暂无合格回踩票";
      const simEl = document.getElementById("linkSim");
      if (simEl) {
        const simPct = link.sim == null ? null : Math.round(Number(link.sim) * 100);
        simEl.textContent = simPct == null ? "" : (`· 相似 ${simPct}%`);
      }
      document.getElementById("linkItems").innerHTML =
        `<div class="meta">可先盯联动板状态，先不定价</div>`;
    } else if (ml && ml !== "未明") {
      linkBox.hidden = false;
      document.getElementById("linkline").textContent = "暂无板块联动";
      document.getElementById("linkmeta").textContent =
        "主线仍有现买点，或无达标相似同伴 / 同伴尖峰退潮";
      const simEl = document.getElementById("linkSim");
      if (simEl) simEl.textContent = "";
      document.getElementById("linkItems").innerHTML =
        `<div class="meta">联动仅在主线无现买或过热时软挖相似板</div>`;
    } else {
      linkBox.hidden = true;
      document.getElementById("linkItems").innerHTML = "";
    }
  }
  const trialRec = v.watch_trial_recommend || null;
  const trialBox = document.getElementById("trialbox");
  if (trialBox) {
    if (trialRec && (trialRec.items || []).length) {
      trialBox.hidden = false;
      document.getElementById("trialline").textContent =
        trialRec.text || trialRec.title || "自选可试探";
      document.getElementById("trialmeta").textContent =
        trialRec.size_note || "观察页可试探同步副卡，小仓不改顶栏";
      const tn = document.getElementById("trialNote");
      if (tn) tn.textContent = `· ${(trialRec.items || []).length}只`;
      document.getElementById("trialItems").innerHTML =
        (trialRec.items || []).map(recCard).join("");
    } else {
      trialBox.hidden = true;
      document.getElementById("trialItems").innerHTML = "";
    }
  }
  const indepRec = v.independent_recommend || null;
  const indepBox = document.getElementById("indepbox");
  if (indepBox) {
    const indepItems = (indepRec && indepRec.items) || [];
    if (indepItems.length) {
      indepBox.hidden = false;
      document.getElementById("indepline").textContent =
        indepRec.text || indepRec.title || "独立人气回踩";
      document.getElementById("indepmeta").textContent =
        indepRec.size_note || "主线板内独立行情·近低回踩，不改顶栏";
      const inn = document.getElementById("indepNote");
      if (inn) inn.textContent = `· ${indepItems.length}只`;
      document.getElementById("indepItems").innerHTML =
        indepItems.map(recCard).join("");
    } else if (ml && ml !== "未明") {
      indepBox.hidden = false;
      document.getElementById("indepline").textContent = "暂无独立人气回踩";
      document.getElementById("indepmeta").textContent =
        "主线/支线/联动成分暂无「近低 + 年内涨停」合格票";
      const inn = document.getElementById("indepNote");
      if (inn) inn.textContent = "";
      document.getElementById("indepItems").innerHTML =
        `<div class="meta">排除双龙/卡位后，三线板内近低（优先独立发散）才进此池</div>`;
    } else {
      indepBox.hidden = true;
      document.getElementById("indepItems").innerHTML = "";
    }
  }
  registerDeskChartContext(v);
  const favDesk = d.favorite_desk || {};
  const favDeskEl = document.getElementById("favDesk");
  const favDeskBody = document.getElementById("favDeskBody");
  const favDeskNote = document.getElementById("favDeskNote");
  if (favDeskEl && favDeskBody) {
    const favBoards = favDesk.boards || [];
    if (!favBoards.length) {
      favDeskEl.hidden = true;
      favDeskBody.innerHTML = "";
    } else {
      favDeskEl.hidden = false;
      if (favDeskNote) favDeskNote.textContent = favDesk.size_note ? ("· " + favDesk.size_note) : "";
      const lifeMap = { starting: "萌芽", ongoing: "主升", ending: "衰退" };
      favDeskBody.innerHTML = favBoards.map((b) => {
        const buy = b.buy || {};
        const sell = b.sell || {};
        const life = lifeMap[b.lifecycle] || "";
        const meta = [
          b.status || "",
          b.pct == null ? "" : ((Number(b.pct) > 0 ? "+" : "") + Number(b.pct).toFixed(2) + "%"),
          life,
          b.overlap_mainline ? "与主线重合" : "",
          b.etf_soft ? "近似ETF" : "",
        ].filter(Boolean).join(" · ");
        const buyCards = (buy.items || []).length
          ? (buy.items || []).map(recCard).join("")
          : `<div class="meta">${buy.text || "暂无回踩票"}</div>`;
        const sellCards = (sell.items || []).length
          ? (sell.items || []).map(sellCard).join("")
          : `<div class="meta">${sell.text || "无相关持仓"}</div>`;
        return `<section class="fav-board">
              <div class="fav-hd">
                <span class="nm">${b.name || "—"}</span>
                <span class="meta">${meta}</span>
              </div>
              <div class="buymeta">${buy.size_note || buy.title || ""}</div>
              <div class="fav-cols">
                <div class="fav-col">
                  <div class="lab">买入 · 盯回踩</div>
                  <div class="rec-grid">${buyCards}</div>
                </div>
                <div class="fav-col">
                  <div class="lab">卖出 · 相关持仓</div>
                  <div class="rec-grid">${sellCards}</div>
                </div>
              </div>
            </section>`;
      }).join("");
    }
  }
  syncDeskFoldHints();
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
    warns.push("热点板块为空，东财板块源可能失败");
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
  const life = d.mainline_lifecycle || {};
  document.getElementById("lifeNote").textContent = life.note
    || "板块阶段雷达 · 非买卖指令；当前主线会高亮";
  const curLifeName = ((d.verdict || {}).mainline || {}).name || "";
  const sim = d.similar_days || {};
  document.getElementById("similarNote").textContent = sim.note || "近端同相位日对照次日冷热";
  const peers = sim.peers || [];
  document.getElementById("similarBox").innerHTML =
    (sim.bias ? `<div class="bias">${sim.bias}</div>` : `<div class="meta">暂无明确倾向</div>`)
    + (peers.length
      ? `<ul>` + peers.slice(0, 5).map((p) =>
          `<li>${p.date || "—"} 温度${p.temperature ?? "—"} 涨停${p.zt ?? "—"}`
          + (p.next_date
            ? ` → ${p.next_date} ${p.next_phase || ""} 温${p.next_temperature ?? "—"} 涨停${p.next_zt ?? "—"}`
            : " → 次日待记")
          + `</li>`).join("") + `</ul>`
      : `<div class="meta">样本不足时先积累日级快照</div>`);
  const lifeCard = (b) => {
    const spark = (b.spark || []).map(h => `<i style="height:${Math.max(8, h)}%"></i>`).join("");
    const isMain = curLifeName && b.name === curLifeName;
    return `<article class="life-item${isMain ? " is-main" : ""}">
          <div class="hd">
            <div class="nm">${b.name || "—"}</div>
            <span class="tag ${tagClass(b.status)}">${termHtml(b.status || "观察")}</span>
          </div>
          <div class="meta">${b.headline || ""}</div>
          <div class="spark">${spark}</div>
          <div class="meta">${termHtml("聚集度")} ${b.cluster || (b.zt_n ?? 0)} · 热度日 ${b.hot_days ?? 0}</div>
          <div class="meta" style="margin-top:4px">${termHtml("总龙头")} ${b.leader_name || "—"} ${b.leader_boards || 0}板 · ${fmtPct(b.pct)}</div>
          ${b.live_hint ? `<div class="meta" style="margin-top:4px;color:var(--amber, #e0a84a)">⏱ ${b.live_hint}</div>` : ""}
          <div class="note" style="margin-top:6px">${b.note || ""}</div>
        </article>`;
  };
  const freshRows = life.fresh || [];
  const freshHtml = freshRows.length
    ? `<div class="meta" style="margin-top:6px">盘中新苗（未计入，收盘确认）：`
      + freshRows.map((f) => `${f.name || "—"} ${fmtPct(f.pct)}${f.zt_n ? ` · ${f.zt_n}停` : ""}`).join("；")
      + `</div>`
    : "";
  const lifeCol = (cls, title, rows, extra = "") => `
        <div class="life-col ${cls}">
          <h3>${title} · ${(rows || []).length}</h3>
          ${(rows || []).map(lifeCard).join("") || `<div class="meta">暂无</div>`}
          ${extra}
        </div>`;
  document.getElementById("lifeGrid").innerHTML =
    lifeCol("start", "萌芽", life.starting, freshHtml) +
    lifeCol("run", "主升", life.ongoing) +
    lifeCol("end", "衰退", life.ending);
  const cg = d.contagion || {};
  const cgEl = document.getElementById("contagion");
  cgEl.className = "contagion " + (cg.on ? "on" : "off");
  cgEl.textContent = cg.text || "暂无板块级跌停传染";
  document.getElementById("hist").innerHTML = (d.history || []).map(r => `
        <tr${r.breadth_degraded ? ' class="degraded-day"' : ""}>
          <td>${r.trade_date || ""}</td>
          <td>${r.event || ""}</td>
          <td><span class="phase-pill p-${r.phase || ""}">${termHtml(r.phase || "")}</span></td>
          <td>${r.ups != null ? r.ups : "—"} / ${r.downs != null ? r.downs : "—"}</td>
          <td class="up">${r.zt ?? ""}</td>
          <td class="down">${r.dt ?? ""}</td>
          <td>${r.zb_rate ?? ""}</td>
          <td>${r.height ?? ""}</td>
          <td>${r.promotion ?? ""}</td>
          <td>${r.premium ?? ""}</td>
          <td>${r.amount_yi != null ? r.amount_yi : "—"}</td>
        </tr>`).join("");
  const cardHtml = (b, pin) => {
    const ice = !!b.ice;
    const tags = (b.tags || []).map(t =>
      `<span class="${miniClass(t.k, t.on)}">${termHtml(t.k)}</span>`
    ).join("");
    const spark = (b.spark || []).map(h => `<i style="height:${Math.max(8, h)}%"></i>`).join("");
    const lead = ice
      ? `最弱观察 · ${termHtml("跌停")} ${b.dt_n ?? 0} · 涨${b.up_count ?? 0} / 跌${b.down_count ?? 0}`
      : `${termHtml("总龙头")} ${b.leader_name || "—"} ${b.leader_boards || 0}板`
        + (b.slot_name ? ` · ${termHtml("卡位龙")} ${b.slot_name} ${b.slot_boards || 0}板` : "");
    const prefix = pin ? (b.pin_label + " · ") : "";
    const bk = String(b.bk || "");
    const favOn = !!b.in_favorite;
    const favBtn = bk
      ? (favOn
        ? `<button type="button" class="fav-btn on" data-bk="${bk}" data-id="${b.favorite_id || ""}">取消看好</button>`
        : `<button type="button" class="fav-btn" data-bk="${bk}" data-name="${String(b.name || "").replace(/"/g, "&quot;")}" data-kind="${b.kind || ""}">加入看好</button>`)
      : "";
    return `<article class="board tone-${b.tone || "slate"} ${statusClass(b.status)}${favOn ? " is-fav" : ""}">
          <div class="hd">
            <div>
              <h3>${prefix}${b.name}</h3>
              <div class="meta">${b.headline || b.status || "观察"}${bk ? ` · ${bk}` : ""}</div>
            </div>
            <div>
              <div>${fmtPct(b.pct)}</div>
              <span class="tag ${tagClass(b.status)}">${termHtml(b.status || "观察")}</span>
            </div>
          </div>
          <div class="spark">${spark}</div>
          <div class="meta">${termHtml("聚集度")} ${b.cluster || (b.zt_n ?? 0)}</div>
          <div class="meta" style="margin-top:6px">${lead}</div>
          <div class="tag-row">${tags}</div>
          <div class="meta" style="margin-top:4px">${
        (b.rep_label ? `信誉 ${b.rep_label}${b.rep_adj ? ` (${b.rep_adj > 0 ? "+" : ""}${b.rep_adj})` : ""}${b.rep_thin ? "·薄" : ""}` : "")
        + (b.rep_persist_rate != null
          ? `${b.rep_label ? " · " : ""}续热 ${b.rep_persist_rate}%（n=${b.rep_sample_n || 0}）`
          : "")
        + ((b.similar_peers || []).length
          ? `${(b.rep_label || b.rep_persist_rate != null) ? " · " : ""}相似 ${(b.similar_peers || []).slice(0, 2).map((p) => {
              const tip = (p.why || []).slice(0, 2).join("·");
              return tip ? `${p.name}(${tip})` : p.name;
            }).join("/")}`
          : "")
      }</div>
          <div class="note">${b.note || ""}</div>
          <ul class="members">${(b.members || []).slice(0,4).map(x =>
        `<li><span>${x.name}</span><span>${fmtPct(x.pct)}</span></li>`).join("")}</ul>
          ${favBtn}
        </article>`;
  };
  const favEl = document.getElementById("favBoards");
  if (favEl) {
    favEl.innerHTML = (d.favorite_boards || []).map(b => cardHtml(b, false)).join("")
      || "<div class='meta'>还没有看好板块。在热点/冰点/置顶卡片点「加入看好」。</div>";
  }
  document.getElementById("pins").innerHTML = (d.pin_boards || []).map(b => cardHtml(b, true)).join("") || "<div class='meta'>置顶板块待刷新</div>";
  document.getElementById("ice").innerHTML = (d.ice_boards || []).map(b => cardHtml(b, false)).join("") || "<div class='meta'>冰点板块待刷新</div>";
  document.getElementById("boards").innerHTML = (d.hot_boards || []).map(b => cardHtml(b, false)).join("") || "<div class='meta'>热点待刷新</div>";
  const watchGroups = ["涨停", "炸板", "跌停", "高换手"];
  const watchByGroup = {};
  (d.watch || []).forEach((w) => {
    const g = w.group || "其他";
    (watchByGroup[g] || (watchByGroup[g] = [])).push(w);
  });
  let watchHtml = `<li class="hdrow"><span>名称</span><span>代码</span><span>涨跌</span><span>标签</span><span>原因</span><span>板块</span><span></span></li>`;
  let watchCount = 0;
  watchGroups.concat(Object.keys(watchByGroup).filter((g) => !watchGroups.includes(g))).forEach((g) => {
    const rows = watchByGroup[g] || [];
    if (!rows.length) return;
    watchHtml += `<li class="group-row">${g} · ${rows.length}</li>`;
    rows.forEach((w) => {
      watchCount += 1;
      const mlHit = w.board_match === true;
      const boardBit = w.board_text
        ? `${w.board_text}${w.vs_mainline ? " · " + w.vs_mainline : ""}`
        : "—";
      const wlBtn = w.in_watchlist
        ? `<button type="button" class="wl-add in" disabled>已自选</button>`
        : `<button type="button" class="wl-add" data-code="${w.code || ""}" data-name="${(w.name || "").replace(/"/g, "")}" data-suggest="${w.suggest_price ?? ""}" data-stop="${w.stop_price ?? ""}" data-chase="${w.chase_price ?? ""}" title="带入粗略建议/止损/不追价带">自选</button>`;
      watchHtml += `<li class="${mlHit ? "is-ml" : ""}">
            <span class="wl-name">${tickerHtml(w.name, w.code)}</span>
            <span class="wl-code">${w.code || ""}</span>
            <span class="wl-pct">${fmtPct(w.pct)}</span>
            <span class="wl-tag tag ${tagClass(w.tag)}">${termHtml(w.tag || "观察")}</span>
            <span class="wl-reason">${reasonTerm(w.reason)}</span>
            <span class="wl-board meta">${boardBit}</span>
            <span class="wl-act">${wlBtn}</span>
          </li>`;
    });
  });
  document.getElementById("watchList").innerHTML =
    watchCount
      ? watchHtml
      : `<li class="hdrow"><span>名称</span><span>代码</span><span>涨跌</span><span>标签</span><span>原因</span><span>板块</span><span></span></li><li><span class="meta">暂无异动</span></li>`;
  document.getElementById("wlBody").innerHTML = (d.watchlist || []).map((w) => {
    const t = (w.first_seen_at || "").slice(0, 16);
    const obs = w.observe_status || "观察中";
    const tone = w.observe_tone || "slate";
    const obsCls = tone === "green" ? "obs-ok"
      : tone === "red" ? "obs-bad"
      : tone === "amber" ? "obs-warn"
      : "obs-muted";
    const obsTip = (w.observe_note || "").replace(/"/g, "&quot;");
    return `<tr>
          <td data-label="名称">${tickerHtml(w.name, w.code)}</td>
          <td data-label="代码">${w.code || ""}</td>
          <td data-label="首次">${t || "—"}</td>
          <td data-label="建议价">${w.suggest_price ?? "—"}</td>
          <td data-label="止损">${w.stop_price ?? "—"}</td>
          <td data-label="不追">${w.chase_price ?? "—"}</td>
          <td data-label="现价">${w.last ?? "—"}</td>
          <td data-label="当日%">${fmtPct(w.last_pct)}</td>
          <td data-label="观察" title="${obsTip}"><span class="tag ${obsCls}">${obs}</span></td>
          <td class="m-actions"><button type="button" class="pos-del wl-del" data-id="${w.id}">删除</button></td>
        </tr>`;
  }).join("") || `<tr><td colspan="10" class="meta">暂无自选。上方填代码加入。</td></tr>`;
  const bl = d.stock_blacklist || {};
  const blItems = bl.items || [];
  const blMeta = document.getElementById("blMeta");
  if (blMeta) {
    const bits = [];
    if (bl.flagged_today != null) bits.push(`今日高开低走 ${bl.flagged_today} 只`);
    if (bl.scanned != null) bits.push(`已扫描 ${bl.scanned}`);
    bits.push(`黑名单 ${blItems.length} 只 · 不进个股推荐`);
    blMeta.textContent = bits.join(" · ")
      + "。近10日多次自动拉黑；自动项连续正常约3日解除；手动项需点移除。";
  }
  const blBody = document.getElementById("blBody");
  if (blBody) {
    blBody.innerHTML = blItems.map((b) => {
      const src = b.source === "manual" ? "手动" : "自动";
      return `<tr>
            <td data-label="名称">${tickerHtml(b.name, b.code)}</td>
            <td data-label="代码">${b.code || ""}</td>
            <td data-label="来源">${src}</td>
            <td data-label="原因" class="meta">${b.reason || b.note || "—"}</td>
            <td data-label="打击">${b.strike_n ?? "—"}</td>
            <td data-label="正常日">${b.clean_streak ?? 0}</td>
            <td data-label="加入" class="meta">${(b.blocked_at || "").slice(0, 16) || "—"}</td>
            <td class="m-actions"><button type="button" class="pos-del bl-del" data-code="${b.code || ""}">移除</button></td>
          </tr>`;
    }).join("") || `<tr><td colspan="8" class="meta">暂无黑名单。可手动加入，或等系统识别常高开低走。</td></tr>`;
  }
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
