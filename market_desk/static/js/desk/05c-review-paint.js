function paintReview(payload) {
  lastReview = payload || { signals: [], summary: {} };
  const sum = lastReview.summary || {};
  const today = sum.today || {};
  const viewDate = lastReview.view_date || sum.view_date || today.date || "";
  const calendarToday = lastReview.calendar_today || sum.calendar_today || "";
  reviewViewDate = viewDate;
  const isToday = viewDate && calendarToday && viewDate === calendarToday;
  const dayLabel = isToday ? "今日" : "当日";
  const dates = lastReview.dates || sum.dates || [];
  const dayInput = document.getElementById("revDayInput");
  if (dayInput && viewDate) dayInput.value = viewDate;
  const chips = document.getElementById("revDayChips");
  if (chips) {
    chips.innerHTML = dates.slice(0, 14).map((d) => {
      const short = String(d).slice(5);
      const cls = [
        "rev-day-chip",
        d === viewDate ? "on" : "",
        d === calendarToday ? "today-mark" : "",
      ].filter(Boolean).join(" ");
      return `<button type="button" class="${cls}" data-rev-day="${d}">${short}${d === calendarToday ? "·今" : ""}</button>`;
    }).join("");
  }
  paintReviewSession(sum.session_hint);
  const cell = (lab, val, cls) =>
    `<div class="rev-card"><div class="lab">${lab}</div><div class="val ${cls || ""}">${val ?? "—"}</div></div>`;
  const tcell = (lab, val, cls) =>
    `<div><div class="lab">${lab}</div><div class="val ${cls || ""}">${val ?? "—"}</div></div>`;
  document.getElementById("revToday").innerHTML = [
    `<div class="hd">${dayLabel}摘要 · ${today.date || viewDate || "—"} · 相位 ${today.phase || "—"}</div>`,
    tcell("买入", today.buy_n ?? 0),
    tcell("卖出", today.sell_n ?? 0),
    tcell("未回踩上行", today.miss_pullback_n ?? 0, "mark-miss"),
    tcell("价带内", today.in_band_n ?? 0, "up"),
    tcell("已交易/未交易", `${today.traded_n ?? 0} / ${today.skipped_n ?? 0}`),
    tcell("主线切换", today.switch_n ?? 0),
    revTopPicksHtml(viewDate, isToday),
  ].join("");
  const ex = sum.exec || today.exec || {};
  const by = ex.by_kind || {};
  const etfEx = by.etf || {};
  const stkEx = by.stock || {};
  document.getElementById("revExec").innerHTML =
    `<div class="hd">${dayLabel}计划执行分<button type="button" class="q" data-term="计划执行分">?</button></div>`
    + `执行分 <b>${ex.score == null ? "—" : ex.score}</b> · 已成交买 ${ex.traded_buy_n ?? 0}`
    + (ex.diary_n ? `（含日记 ${ex.diary_n}）` : "")
    + (ex.unplanned_n ? ` · 无计划 ${ex.unplanned_n}` : "")
    + ` · 价带内 ${ex.in_band_n ?? 0} · 追高 ${ex.chase_n ?? 0}`
    + ` · 更低更好 ${ex.below_n ?? 0} · 其他 ${ex.other_n ?? 0}`
    + `<div class="meta" style="margin-top:4px">ETF ${etfEx.score == null ? "—" : etfEx.score}（${etfEx.traded_buy_n ?? 0}）`
    + ` · 个股 ${stkEx.score == null ? "—" : stkEx.score}（${stkEx.traded_buy_n ?? 0}）</div>`
    + (() => {
      const se = sum.sell_exec || {};
      if (se.traded_sell_n == null && se.score == null) return "";
      return `<div class="meta" style="margin-top:6px">卖侧执行 <b>${se.score == null ? "—" : se.score}</b>`
        + ` · 已成交卖 ${se.traded_sell_n ?? 0}`
        + (se.diary_n ? `（含日记 ${se.diary_n}）` : "")
        + ` · 贴计划 ${se.in_band_n ?? 0} · 偏晚 ${se.late_n ?? 0}`
        + ` · 偏早 ${se.early_n ?? 0}</div>`;
    })();
  const missed = sum.missed_buys || [];
  const missAttr = sum.miss_attr || {};
  const missCounts = missAttr.counts || {};
  const missItems = (missAttr.items && missAttr.items.length) ? missAttr.items : missed;
  const missAttrHtml = (missAttr.total > 0)
    ? (`<div class="miss-attr">`
      + `<span class="chip nt">未触达就走 ${missCounts.never_touched || 0}</span>`
      + `<span class="chip tb">触达未买 ${missCounts.touched_not_bought || 0}</span>`
      + `<span class="chip gb">闸门卡死 ${missCounts.gate_blocked || 0}</span>`
      + `</div>`)
    : "";
  const missKindCls = (k) => ({ never_touched: "nt", touched_not_bought: "tb", gate_blocked: "gb" }[k] || "");
  document.getElementById("revMissed").innerHTML = missItems.length
    ? (`<div class="hd">漏买归因<button type="button" class="q" data-term="漏买归因">?</button> · ${missItems.length}</div>`
      + missAttrHtml
      + `<ul style="margin:0;padding-left:18px">` + missItems.map((m) =>
        `<li>`
        + (m.miss_kind_label
          ? `<span class="miss-kind ${missKindCls(m.miss_kind)}">${m.miss_kind_label}</span>`
          : "")
        + `${tickerHtml(m.name, m.code, "", { signal_at: m.signaled_at || "" })}`
        + ` 建议 ${m.price ?? "—"} · 现 ${m.live_last ?? "—"}`
        + `${m.dev_pct == null ? "" : " · 偏离 " + ((m.dev_pct > 0 ? "+" : "") + Number(m.dev_pct).toFixed(2) + "%")}`
        + ` · ${m.skipped ? "已跳过" : "未点已交易"} · ${m.price_mark || "—"}</li>`
      ).join("") + `</ul>`)
    : `<div class="hd">漏买归因<button type="button" class="q" data-term="漏买归因">?</button></div>`
      + `<span class="meta">${dayLabel}暂无未交易漏买项</span>`;
  const weekBox = document.getElementById("revWeekExec");
  if (weekBox) {
    const we = sum.week_exec || {};
    const mc = we.miss_counts || {};
    const ms = we.miss_share || {};
    if (we.ok) {
      weekBox.innerHTML =
        `<div class="hd">近周归因 · 计划遵守<button type="button" class="q" data-term="近周归因">?</button></div>`
        + `<div class="chips">`
        + `<span class="chip">未触达 ${mc.never_touched || 0}${ms.never_touched != null ? `（${ms.never_touched}%）` : ""}</span>`
        + `<span class="chip">触达未买 ${mc.touched_not_bought || 0}${ms.touched_not_bought != null ? `（${ms.touched_not_bought}%）` : ""}</span>`
        + `<span class="chip">闸门 ${mc.gate_blocked || 0}${ms.gate_blocked != null ? `（${ms.gate_blocked}%）` : ""}</span>`
        + `<span class="chip">半仓窗口遵守 ${we.follow_rate == null ? "—" : (we.follow_rate + "%")}（${we.followed_n || 0}/${we.window_n || 0}）</span>`
        + `</div>`
        + `<div class="meta" style="margin-top:6px">${we.note || ""}</div>`;
    } else {
      weekBox.innerHTML = `<div class="hd">近周归因</div><span class="meta">${we.note || "暂无"}</span>`;
    }
  }
  paintEodBrief(sum, today, dayLabel);
  paintTomorrowBrief();
  const vsMode = sum.vs_mainline_mode || reviewVsMlMode || "live";
  reviewVsMlMode = vsMode;
  const vsLiveBtn = document.getElementById("vsMlLive");
  const vsDayBtn = document.getElementById("vsMlDay");
  if (vsLiveBtn) vsLiveBtn.classList.toggle("on", vsMode === "live");
  if (vsDayBtn) vsDayBtn.classList.toggle("on", vsMode === "day");
  const vsHint = document.getElementById("vsMlHint");
  if (vsHint) {
    const liveN = sum.vs_mainline_live || "—";
    const dayN = sum.vs_mainline_day || "—";
    const of = sum.vs_mainline_of || "—";
    vsHint.textContent = vsMode === "day"
      ? `当日主线「${dayN}」· 现对照「${of}」（实时=${liveN}）`
      : `实时主线「${liveN}」· 现对照「${of}」（当日=${dayN}）`;
  }
  const ph = sum.phase_hits || [];
  const kh = sum.kind_hits || [];
  const pkh = sum.phase_kind_hits || [];
  const dh = sum.desk_hits || [];
  const th = sum.theme_hits || [];
  const gates = sum.gate_kills || [];
  const sellBias = sum.sell_bias || {};
  const hints = sum.tune_hints || [];
  const rateCls = (r) => {
    const n = Number(r);
    if (Number.isNaN(n)) return "";
    if (n >= 55) return "hi";
    if (n >= 40) return "mid";
    return "lo";
  };
  const ocBox = document.getElementById("revOcCompare");
  if (ocBox) {
    const oc = sum.outcome_compare || {};
    const rows = oc.rows || [];
    if (rows.length) {
      ocBox.innerHTML =
        `<div class="hd">三标准对照<button type="button" class="q" data-term="三标准对照">?</button>`
        + ` · ${oc.hit_mode === "all" ? "全部已打分" : "仅已交易"}</div>`
        + `<div class="chips">` + rows.map((r) =>
          `<div class="hit-chip${r.low_n ? " low-n" : ""}" title="${r.low_n ? "样本不足 n<8" : ""}">`
          + `<div class="lab">${r.label || r.standard}</div>`
          + `<div class="rate ${rateCls(r.hit_rate)}">${r.hit_rate == null ? "—" : (r.hit_rate + "%")}</div>`
          + `<div class="n">样本 ${r.scored_n ?? 0}${r.low_n ? " · 低" : ""}</div></div>`
        ).join("") + `</div>`
        + `<div class="meta" style="margin-top:6px">${oc.note || ""}</div>`;
    } else {
      ocBox.innerHTML =
        `<div class="hd">三标准对照<button type="button" class="q" data-term="三标准对照">?</button></div>`
        + `<span class="meta">${oc.note || "有隔日打分后并排显示"}</span>`;
    }
  }
  paintGateLedger(sum.gate_ledger || {});
  paintEdgeShadow(sum.edge_shadow || {});
  paintDiscipline(sum.discipline || {});
  paintChaseCost(sum.chase_cost || {});
  const chip = (lab, rate, n, lowN) =>
    `<div class="hit-chip${lowN ? " low-n" : ""}" title="${lowN ? "样本不足 n<8，灰显参考" : ""}"><div class="lab">${lab}</div>`
    + `<div class="rate ${rateCls(rate)}">${rate == null ? "—" : (rate + "%")}</div>`
    + `<div class="n">样本 ${n ?? 0}${lowN ? " · 低" : ""}</div></div>`;
  const boardHtml = (() => {
    const chips = [];
    dh.forEach((k) => chips.push(chip(k.label || k.source, k.hit_rate, k.scored_n, k.low_n)));
    kh.forEach((k) => chips.push(chip(k.label || k.kind, k.hit_rate, k.scored_n, k.low_n)));
    th.slice(0, 6).forEach((k) => chips.push(chip(k.label || k.theme, k.hit_rate, k.scored_n, k.low_n)));
    ph.slice(0, 4).forEach((p) => chips.push(chip("相位·" + (p.phase || "未标"), p.hit_rate, p.scored_n, p.low_n)));
    if (!chips.length) return "";
    return `<div class="hit-board">${chips.join("")}</div>`;
  })();
  const kindLine = kh.length
    ? ("品种 " + kh.map((k) => `${k.label} ${k.hit_rate}%（${k.scored_n}）`).join(" · "))
    : "";
  const deskLine = (() => {
    if (!dh.length) return "";
    const main = dh.find((k) => k.source === "main");
    const rest = dh.filter((k) => k.source !== "main");
    const mainBit = main
      ? `主线 ${main.hit_rate}%（${main.scored_n}）`
      : "";
    const restBit = rest.length
      ? ("次要 " + rest.map((k) => `${k.label} ${k.hit_rate}%（${k.scored_n}）`).join(" / "))
      : "";
    return "来源 " + [mainBit, restBit].filter(Boolean).join(" · ");
  })();
  const themeLine = th.length
    ? ("题材 " + th.slice(0, 6).map((t) =>
        `${t.label || t.theme} ${t.hit_rate}%（${t.scored_n}）`
      ).join(" · "))
    : "";
  const crossLine = pkh.length
    ? ("交叉 " + pkh.slice(0, 8).map((p) =>
        `${p.phase}·${p.label} ${p.hit_rate}%（${p.scored_n}）`
      ).join(" · "))
    : "";
  const gateLine = gates.length
    ? ("闸门·终态假杀 " + gates.slice(0, 6).map((g) => {
        const fk = g.false_kill_n != null ? `假杀${g.false_kill_n}` : "";
        const note = g.note ? `·${g.note}` : "";
        return `${g.gate}×${g.kill_n}${fk ? `(${fk})` : ""}${note}`;
      }).join(" · "))
    : "";
  const sellParts = [];
  if (sellBias.etf && sellBias.etf.note) sellParts.push(`ETF ${sellBias.etf.note}`);
  if (sellBias.stock && sellBias.stock.note) sellParts.push(`个股 ${sellBias.stock.note}`);
  if (!sellParts.length && sellBias.note) sellParts.push(sellBias.note);
  else if (sellBias.all && sellBias.all.note && sellParts.length) {
    /* keep ETF/stock lines primary */
  }
  const sellLine = sellParts.length
    ? (`卖点闭环 ${sellParts.join(" · ")}`)
    : "";
  const wb = sum.whitebox || {};
  const wbLine = (wb.ok && (wb.weights || []).length)
    ? (`白盒 ${wb.note || ""} · `
      + (wb.weights || []).filter((w) => w.key !== "bias").slice(0, 5).map((w) => {
          const v = Number(w.weight) || 0;
          return `${w.label}${(v > 0 ? "+" : "")}${v.toFixed(2)}`;
        }).join(" / "))
    : (wb.note ? `白盒 ${wb.note}` : "");
  const hintHtml = hints.length
    ? (`<ul class="tune-hints">` + hints.map((h) => `<li>${h}</li>`).join("") + `</ul>`)
    : "";
  const rm = sum.ready_monitor || {};
  const rmTitle = (rm.by_day || []).map((d) =>
    `${d.date} 亮${d.n}只 胜${d.win} 均${d.d3}% · 当日全部${d.day_n}只 均${d.day_d3}%`
  ).join("\n");
  const readyLine = rm.note
    ? (`<div class="ready-mon rm-${escAttr(rm.tone || "low")}" style="margin-top:4px" title="${escAttr(rmTitle)}">`
      + `<button type="button" class="q" data-term="可买入监控">?</button> 可买入监控 ${escAttr(rm.note)}</div>`)
    : "";
  document.getElementById("revPhase").innerHTML = (boardHtml || ph.length || kh.length || dh.length || th.length || gates.length || sellLine || wbLine || hints.length || readyLine)
    ? (`<div class="hd">命中率看板（跨日）<button type="button" class="q" data-term="命中率看板">?</button></div>`
      + (hintHtml ? `<div class="hd" style="margin-top:4px">调参建议</div>${hintHtml}` : "")
      + readyLine
      + boardHtml
      + (ph.length
        ? `<div style="margin-top:6px">相位 ${ph.map((p) => `${p.phase || "未标"} ${p.hit_rate == null ? "—" : (p.hit_rate + "%")}（${p.scored_n}）`).join(" · ")}</div>`
        : "")
      + (kindLine ? `<div class="meta" style="margin-top:4px">${kindLine}</div>` : "")
      + (deskLine ? `<div class="meta" style="margin-top:4px">${deskLine}</div>` : "")
      + (themeLine ? `<div class="meta" style="margin-top:4px">${themeLine}</div>` : "")
      + (crossLine ? `<div class="meta" style="margin-top:4px">${crossLine}</div>` : "")
      + (gateLine ? `<div class="meta" style="margin-top:4px"><button type="button" class="q" data-term="闸门归因">?</button> ${gateLine}</div>` : "")
      + (sellLine ? `<div class="meta" style="margin-top:4px"><button type="button" class="q" data-term="卖点闭环">?</button> ${sellLine}</div>` : "")
      + (wbLine ? `<div class="meta" style="margin-top:4px"><button type="button" class="q" data-term="白盒特征权重">?</button> ${wbLine}</div>` : ""))
    : `<div class="hd">命中率看板（跨日）</div><span class="meta">有隔日打分后按来源 / 题材 / 相位汇总</span>`;
  const hist = sum.history || [];
  document.getElementById("revHist").innerHTML = hist.length
    ? `<div class="hd">近 ${hist.length} 日命中率（有打分日）</div>${hitRateSpark(hist, viewDate)}`
    : `<div class="hd">历史日摘要会在每日复盘后累计</div>`;
  const hitMode = sum.hit_rate_mode === "all" ? "全部已打分" : "仅已交易";
  const revHitSel = document.getElementById("revHitMode");
  if (revHitSel && sum.hit_rate_mode) revHitSel.value = sum.hit_rate_mode;
  const revOcSel = document.getElementById("revOcMode");
  const ocNow = payload.outcome_standard || sum.outcome_standard || reviewOcMode || "classic";
  reviewOcMode = ocNow;
  if (revOcSel) revOcSel.value = ocNow;
  document.getElementById("revSum").innerHTML = [
    cell("买入信号（近窗）", sum.buy_total ?? 0),
    cell(`买入命中率·${hitMode}`, sum.buy_hit_rate == null ? "—" : (sum.buy_hit_rate + "%"), "up"),
    cell("卖出命中率", sum.sell_hit_rate == null ? "—" : (sum.sell_hit_rate + "%"), "up"),
    cell("待隔日打分", sum.pending ?? 0),
  ].join("");
  const flyBox = document.getElementById("sellFlyBoard");
  if (flyBox) {
    const fly = sum.sell_fly_board || {};
    const recent = fly.recent_early || [];
    const bySrc = (fly.by_source || []).slice(0, 4);
    const chips = [
      ["卖后回落", fly.hit_n, fly.hit_rate],
      ["续涨/卖飞", fly.early_n, fly.early_rate],
      ["其中卖飞", fly.fly_n, fly.fly_rate],
    ].map(([lab, n, rate]) =>
      `<div class="fly-chip"><div class="lab">${lab}</div>`
      + `<div class="n">${n ?? 0}</div>`
      + `<div class="lab">${rate == null ? "—" : (rate + "%")}</div></div>`
    ).join("");
    const srcLine = bySrc.length
      ? (`来源 ` + bySrc.map((s) =>
          `${s.label} 回落${s.hit_rate == null ? "—" : s.hit_rate + "%"}/卖飞${s.fly_n || 0}（n=${s.n}）`
        ).join(" · "))
      : "";
    // avg_saved_d3 > 0 = price fell within 3 sessions after the exit (drawdown avoided).
    const ruleBit = (s) => {
      const saved = s.avg_saved_d3 == null ? "—" : `${s.avg_saved_d3 > 0 ? "+" : ""}${s.avg_saved_d3}%`;
      const dim = (s.n || 0) < 5 ? ' style="opacity:.55"' : "";
      return `<span${dim}>${s.label} 回落${s.hit_rate == null ? "—" : s.hit_rate + "%"}`
        + `/卖飞${s.fly_rate == null ? "—" : s.fly_rate + "%"}/三日省${saved}（n=${s.n}）</span>`;
    };
    const byRule = fly.by_rule || [];
    const ruleLine = byRule.length
      ? `<button type="button" class="q" data-term="卖点类型复盘">?</button> 按卖点类型 ` + byRule.map(ruleBit).join(" · ")
      : "";
    const byAtr = fly.by_atr || [];
    const atrLine = byAtr.length > 1 ? `按波动档 ` + byAtr.map(ruleBit).join(" · ") : "";
    const recentHtml = recent.length
      ? (`<ul class="fly-recent">` + recent.map((r) => {
          const lab = r.outcome_label || "";
          const tagCls = lab === "卖后回落" ? "fly-tag hit" : "fly-tag";
          // Sell outcomes store "avoidance" sign (↑ after sell → negative).
          // Show price change after sell so 卖飞 reads as 次日涨 / 留桌上正数.
          let d1Bit = "";
          if (r.outcome_day1_pct != null && r.outcome_day1_pct !== "") {
            const pxChg = -Number(r.outcome_day1_pct);
            d1Bit = ` <span class="dim">· 次日${pxChg > 0 ? "+" : ""}${pxChg.toFixed(1)}%</span>`;
          }
          let maeBit = "";
          if (r.outcome_mae_pct != null && r.outcome_mae_pct !== "") {
            const mae = Number(r.outcome_mae_pct);
            const left = mae <= 0 ? Math.abs(mae) : 0;
            maeBit = left > 0
              ? ` <span class="dim">· 留桌上 +${left.toFixed(1)}%</span>`
              : ` <span class="dim">· 未留桌上</span>`;
          }
          return `<li><span class="${tagCls}">${lab}</span>${(r.trade_date || "").slice(5)} ${r.name || r.code} <span class="dim">${r.code || ""}</span>${d1Bit}${maeBit}</li>`;
        }).join("") + `</ul>`)
      : `<div class="meta">暂无卖飞/续涨样本</div>`;
    flyBox.innerHTML =
      `<div class="hd">卖飞 / 卖后回落看板<button type="button" class="q" data-term="卖飞看板">?</button></div>`
      + `<div class="fly-verdict"><b>${fly.verdict || "—"}</b> · <span class="meta">${fly.note || ""}</span></div>`
      + `<div class="fly-chips">${chips}</div>`
      + (srcLine ? `<div class="fly-src">${srcLine}</div>` : "")
      + (ruleLine ? `<div class="fly-src">${ruleLine}</div>` : "")
      + (atrLine ? `<div class="fly-src">${atrLine}</div>` : "")
      + recentHtml;
  }
  const type = (document.getElementById("revType") || {}).value || "";
  const kind = (document.getElementById("revKind") || {}).value || "";
  const srcFilter = (document.getElementById("revSource") || {}).value || "";
  const main = ((document.getElementById("revMain") || {}).value || "").trim();
  const hideWait = !!(document.getElementById("revHideWait") || {}).checked;
  const srcOf = (r) => revNormSrc(r);
  const isPaperWait = (r) => {
    const st = String(r.signal_type || "");
    if (!(st === "buy" || st.startsWith("buy_"))) return false;
    if (Number(r.ready || 0) || Number(r.traded || 0) || Number(r.skipped || 0)) return false;
    if (r.outcome_label) return false;
    const flags = r.price_flags || [];
    // Keep actionable / diagnostic rows visible.
    if (flags.some((f) => ["miss_pullback", "chase_hit", "above_plan", "stop_hit", "in_band", "near_wait"].includes(f))) {
      return false;
    }
    const near = !!(r.near_entry || (r.payload && r.payload.near_entry));
    const gated = (r.confirm_fail || (r.payload && r.payload.confirm_fail) || []).length > 0;
    const mark = String(r.price_mark || "");
    if (near || gated) return false;
    if (mark && !mark.includes("回踩") && mark !== "—") return false;
    // Pure 盯回踩 paper: waiting far from band, no gate fail, no live flag.
    return true;
  };
  const rows = applyRevSort((lastReview.signals || []).filter((r) => {
    if (type === "buy") {
      const st = String(r.signal_type || "");
      if (st === "sell" || !(st === "buy" || st.startsWith("buy_"))) return false;
    } else if (type && r.signal_type !== type) return false;
    if (kind && (r.kind || "") !== kind) return false;
    if (srcFilter && srcOf(r) !== srcFilter) return false;
    if (main && !(String(r.mainline || "").includes(main))) return false;
    if (hideWait && isPaperWait(r)) return false;
    return true;
  }));
  const hiddenWaitN = hideWait
    ? (lastReview.signals || []).filter((r) => {
        if (type === "buy") {
          const st = String(r.signal_type || "");
          if (!(st === "buy" || st.startsWith("buy_"))) return false;
        } else if (type === "sell") return false;
        else if (type && r.signal_type !== type) return false;
        if (kind && (r.kind || "") !== kind) return false;
        if (srcFilter && srcOf(r) !== srcFilter) return false;
        if (main && !(String(r.mainline || "").includes(main))) return false;
        return isPaperWait(r);
      }).length
    : 0;
  const hideLab = document.querySelector("#revHideWait")?.parentElement?.querySelector("span");
  if (hideLab) {
    hideLab.textContent = hiddenWaitN
      ? `隐藏纯盯回踩（已藏 ${hiddenWaitN}）`
      : "隐藏纯盯回踩";
  }
  const cols = visibleRevCols();
  document.getElementById("revHead").innerHTML = cols.map((id) => {
    if (id === "ops") return `<th class="col-${id}">${revColLabel(id)}</th>`;
    const on = revSort.id === id;
    const ind = on ? (revSort.dir > 0 ? "▲" : "▼") : "";
    return `<th class="col-${id} rev-sort" data-sort="${id}">${revColLabel(id)}`
      + (ind ? `<span class="sort-ind">${ind}</span>` : "")
      + `</th>`;
  }).join("");
  const colspan = Math.max(cols.length, 1);
  const buyTypeLabel = (st) => ({
    buy: "买",
    buy_side: "买·支线回踩",
    buy_link: "买·联动回踩",
    buy_trial: "买·自选",
    buy_indep: "买·独立人气",
    buy_dragon: "买·龙头",
    sell: "卖",
  })[st] || (st === "sell" ? "卖" : "买");
  document.getElementById("revBody").innerHTML = rows.map((r) => {
    const typ = buyTypeLabel(r.signal_type || "buy");
    const skipped = !!Number(r.skipped || 0);
    const traded = !!Number(r.traded || 0);
    let label = r.outcome_label || "待隔日";
    if (traded) label = "已交易" + (r.outcome_label ? ` · ${r.outcome_label}` : "");
    else if (skipped) label = "未交易";
    const pend = "";
    const flags = r.price_flags || [];
    let markCls = "";
    if (flags.includes("stop_hit")) markCls = "mark-stop";
    else if (flags.includes("chase_hit")) markCls = "mark-chase";
    else if (flags.includes("above_plan")) markCls = "mark-chase";
    else if (flags.includes("miss_pullback")) markCls = "mark-miss";
    else if (r.price_mark) markCls = "mark-ok";
    const markHtml = r.price_mark
      ? `<div class="${markCls}">${r.price_mark}</div>`
      : "";
    const cautionHtml = r.buy_caution
      ? `<div class="meta ${markCls}" title="${escAttr(r.buy_caution_tip || "")}">${r.buy_caution}</div>`
      : "";
    const liveHtml = r.live_last != null
      ? `<div class="meta">现价 ${r.live_last}${liveArrowHtml(r)}${r.live_pct == null ? "" : " " + ((r.live_pct > 0 ? "+" : "") + Number(r.live_pct).toFixed(2) + "%")}</div>`
      : "";
    const dev = r.dev_pct;
    const devHtml = dev == null
      ? ""
      : `<div class="${dev >= 0 ? "dev-up" : "dev-down"}">偏离 ${(dev > 0 ? "+" : "") + Number(dev).toFixed(2)}%</div>`;
    const chaseDev = r.chase_dev_pct;
    const chaseDevHtml = chaseDev == null
      ? ""
      : `<div class="${chaseDev >= 0 ? "dev-up" : "dev-down"}">偏离 ${(chaseDev > 0 ? "+" : "") + Number(chaseDev).toFixed(2)}%</div>`;
    let t1Locked = false;
    if (r.signal_type === "sell") {
      const day = String(r.trade_date || (lastData && lastData.trade_date) || "").slice(0, 10);
      const code = String(r.code || "").replace(/\D/g, "").padStart(6, "0");
      const pos = ((lastData && lastData.positions) || []).find(
        (p) => String(p.code || "").replace(/\D/g, "").padStart(6, "0") === code
      );
      const buyDay = String((pos && (pos.last_buy_date || pos.created_at)) || "").slice(0, 10);
      if (day && buyDay && day === buyDay) t1Locked = true;
      const sameBuy = (lastReview.signals || []).some((s) =>
        (s.signal_type === "buy" || String(s.signal_type || "").startsWith("buy_"))
        && String(s.trade_date || "").slice(0, 10) === day
        && String(s.code || "").replace(/\D/g, "").padStart(6, "0") === code
        && Number(s.traded || 0)
      );
      if (sameBuy) t1Locked = true;
    }
    const ctx = { typ, skipped, traded, label, pend, markHtml, cautionHtml, liveHtml, devHtml, chaseDevHtml, t1Locked };
    const tds = cols.map((id) => {
      const html = revCellHtml(id, r, ctx);
      if (id === "day1" || id === "day3") return html;
      return `<td class="col-${id}">${html}</td>`;
    }).join("");
    return `<tr class="${r.signal_type === "sell" ? "rev-sell" : ""}">${tds}</tr>`;
  }).join("") || `<tr><td colspan="${colspan}" class="meta">${viewDate || "该日"}暂无信号。盘中出现「可买入」或仓位「建议卖」后会自动记录。</td></tr>`;
}
