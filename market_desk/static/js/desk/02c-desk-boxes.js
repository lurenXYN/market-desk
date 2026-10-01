/** Paint reason / bans and every recommendation box (buy, dragon, side, link, trial, indep, favorites). */
function renderDeskBoxes(d, v) {
  const ml = (v.mainline && v.mainline.name) || "未明";
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
  const buymeta = document.getElementById("buymeta");
  buymeta.textContent = rec.size_note || "";
  buymeta.classList.toggle("absorb-warn", !!(rec.absorb_unconfirmed || []).length);
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
        "主线/支线/联动成分暂无「健康回踩 / 抗跌承接（额≥2.5亿）」合格票";
      const inn = document.getElementById("indepNote");
      if (inn) inn.textContent = "";
      document.getElementById("indepItems").innerHTML =
        `<div class="meta">排除双龙/卡位后，三线板内抗跌承接或日高健康回踩（优先独立发散）才进此池</div>`;
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
}
