const GLOSSARY_GROUPS = [
  { id: "phase", label: "相位", keys: ["相位", "恐慌", "分歧", "发酵", "高潮", "温度"] },
  { id: "action", label: "结论", keys: ["现金", "可小仓", "可买入", "观望", "观察回踩", "回踩到位", "禁追", "观察"] },
  { id: "board", label: "板块", keys: [
    "实时主线", "观察支线", "板块联动", "主线切换", "主线透明", "主线生命周期", "板块相似度", "题材信誉", "主题链",
    "仓位热度", "仓位合流", "个股信誉", "自适应卖点", "回踩甜区", "漏买情景", "自适应调参", "白盒特征权重", "确认中", "尖峰禁追", "退潮",
    "点火", "一波", "二波", "加速", "滞涨", "A杀", "修复", "反包", "天地",
    "聚集度", "总龙头", "卡位龙", "冰点", "冷冻", "相对最冷", "传染", "传染预警", "看好板块", "看好板块买卖",
  ] },
  { id: "desk", label: "作战/仓位", keys: [
    "推荐买入", "建议卖出", "反悔窗", "卖飞", "卖飞看板", "下一动作", "减半价", "Playbook", "风险股数", "分批计划", "仓位", "T+1",
    "早决策摘要", "收盘一页纸", "今日三词", "盘中分段", "系统通知", "Server酱", "提醒分级", "风控总览", "单日亏损帽", "目标仓位",
    "参数预设", "执行日记", "为何不能现买",
  ] },
  { id: "review", label: "复盘/观察", keys: [
    "信号复盘", "信号回测", "模拟成交", "触达", "计划价带", "理想回踩", "建议价", "不追上限", "买撮合",
    "回测量能", "回测滑点", "回测跳空", "预览匹配数", "仅ready",
    "复盘评测标准", "调参口径", "模拟执行分", "Ready风格", "开盘卖出缓冲",
    "对照主线", "股东户数", "年内涨停", "计划执行分", "漏买清单", "漏买归因", "浅踩将飞", "相位命中", "自选", "异动",
    "相似日", "分时确认", "健康度", "高开低走黑名单", "集合竞价策略", "闸门归因", "卖点闭环",
    "独立人气回踩", "情绪龙", "中军龙", "年内涨停",
  ] },
  { id: "market", label: "大盘指标", keys: [
    "涨停", "跌停", "炸板", "换手", "高度", "广度", "晋级率", "晋级1→2", "晋级2→3", "梯队", "昨停溢价",
    "成交分位", "周期位置", "竞价基调", "主线透明", "集合竞价策略",
    "情绪浪", "季节性日历", "冰点修复", "主线确认", "扩散加速", "高潮拥挤", "退潮切换",
  ] },
  { id: "wave", label: "波浪理论", keys: [
    "波浪情景", "艾略特波浪", "推动浪", "调整浪",
    "第1浪", "第2浪", "第3浪", "第4浪", "第5浪",
    "A浪", "B浪", "C浪",
    "三角整理", "复合调整", "失败第5浪",
    "锯齿枢轴", "波浪契合度", "否决位",
    "斐波那契变盘窗", "MACD背离", "RSI超买超卖",
  ] },
  { id: "funds", label: "资金流向", keys: [
    "大资金流向",
  ] },
];
let glossaryGroup = "phase";
let glossaryKey = "";

function glossaryKeysForGroup(gid) {
  const all = Object.keys(GLOSSARY || {});
  if (gid === "all") return all.slice().sort((a, b) => a.localeCompare(b, "zh"));
  const g = GLOSSARY_GROUPS.find((x) => x.id === gid);
  if (!g) return [];
  return g.keys.filter((k) => GLOSSARY[k]);
}

function paintGlossaryDetail(key) {
  const box = document.getElementById("glossaryDetail");
  const g = GLOSSARY[key];
  if (!g) {
    box.innerHTML = `<p class="meta">点左侧词条查看含义与近似算法。与界面「?」同源。</p>`;
    return;
  }
  glossaryKey = key;
  box.innerHTML = `<h4>${key}</h4><p class="mean">${g.mean || ""}</p><p class="algo">${g.algo || ""}</p>`;
}

function paintGlossaryList() {
  const q = (document.getElementById("glossarySearch").value || "").trim().toLowerCase();
  let keys = glossaryKeysForGroup(glossaryGroup);
  if (q) {
    keys = Object.keys(GLOSSARY || {}).filter((k) => {
      const g = GLOSSARY[k] || {};
      const blob = `${k} ${g.mean || ""} ${g.algo || ""}`.toLowerCase();
      return blob.includes(q);
    }).sort((a, b) => a.localeCompare(b, "zh"));
  }
  const list = document.getElementById("glossaryList");
  if (!keys.length) {
    list.innerHTML = `<li class="meta">无匹配</li>`;
    paintGlossaryDetail("");
    return;
  }
  if (!keys.includes(glossaryKey)) glossaryKey = keys[0];
  list.innerHTML = keys.map((k) =>
    `<li class="${k === glossaryKey ? "on" : ""}" data-gkey="${k}">${k}</li>`
  ).join("");
  paintGlossaryDetail(glossaryKey);
}

function paintGlossaryChips() {
  const chips = [{ id: "all", label: "全部" }].concat(
    GLOSSARY_GROUPS.map((g) => ({ id: g.id, label: g.label }))
  );
  document.getElementById("glossaryChips").innerHTML = chips.map((c) =>
    `<button type="button" class="g-chip${c.id === glossaryGroup ? " on" : ""}" data-gg="${c.id}">${c.label}</button>`
  ).join("");
}

function openGlossary(preferKey) {
  document.getElementById("settingsPanel").hidden = true;
  if (preferKey && GLOSSARY[preferKey]) {
    glossaryKey = preferKey;
    const hit = GLOSSARY_GROUPS.find((g) => g.keys.includes(preferKey));
    glossaryGroup = hit ? hit.id : "all";
  }
  paintGlossaryChips();
  paintGlossaryList();
  document.getElementById("glossaryPanel").hidden = false;
  document.getElementById("glossarySearch").focus();
}

document.getElementById("glossaryBtn").addEventListener("click", (ev) => {
  ev.stopPropagation();
  const panel = document.getElementById("glossaryPanel");
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  openGlossary();
});
(function wireCommuteMode() {
  const KEY = "desk-commute-mode";
  function apply(on) {
    document.body.classList.toggle("commute-mode", !!on);
    ["commuteBtn", "commuteBtnM"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("on", !!on);
    });
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) {}
  }
  let on = false;
  try { on = localStorage.getItem(KEY) === "1"; } catch (e) {}
  apply(on);
  const toggle = () => apply(!document.body.classList.contains("commute-mode"));
  const a = document.getElementById("commuteBtn");
  const b = document.getElementById("commuteBtnM");
  if (a) a.addEventListener("click", (ev) => { ev.stopPropagation(); toggle(); });
  if (b) b.addEventListener("click", (ev) => { ev.stopPropagation(); toggle(); });
})();
const glossaryBtnM = document.getElementById("glossaryBtnM");
if (glossaryBtnM) {
  glossaryBtnM.addEventListener("click", (ev) => {
    ev.stopPropagation();
    const panel = document.getElementById("glossaryPanel");
    if (!panel.hidden) {
      panel.hidden = true;
      return;
    }
    openGlossary();
  });
}
document.getElementById("glossaryClose").addEventListener("click", () => {
  document.getElementById("glossaryPanel").hidden = true;
});
document.getElementById("glossarySearch").addEventListener("input", () => paintGlossaryList());
document.getElementById("glossaryChips").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-gg]");
  if (!btn) return;
  ev.stopPropagation();
  glossaryGroup = btn.getAttribute("data-gg") || "all";
  document.getElementById("glossarySearch").value = "";
  paintGlossaryChips();
  paintGlossaryList();
});
document.getElementById("glossaryPanel").addEventListener("click", (ev) => {
  // Keep outside-click from seeing panel-internal clicks (esp. after DOM rebuild).
  ev.stopPropagation();
});
document.getElementById("glossaryList").addEventListener("click", (ev) => {
  const li = ev.target.closest("[data-gkey]");
  if (!li) return;
  glossaryKey = li.getAttribute("data-gkey") || "";
  document.querySelectorAll("#glossaryList li").forEach((el) => {
    el.classList.toggle("on", el.getAttribute("data-gkey") === glossaryKey);
  });
  paintGlossaryDetail(glossaryKey);
});
document.getElementById("settingsBtn").addEventListener("click", () => {
  document.getElementById("glossaryPanel").hidden = true;
}, true);
const settingsBtnMCap = document.getElementById("settingsBtnM");
if (settingsBtnMCap) {
  settingsBtnMCap.addEventListener("click", () => {
    document.getElementById("glossaryPanel").hidden = true;
  }, true);
}
document.getElementById("revColClose").addEventListener("click", () => {
  document.getElementById("revColPanel").hidden = true;
});
document.getElementById("revColReset").addEventListener("click", () => {
  revColState = REV_COL_DEFAULT.map((x) => ({ ...x }));
  saveRevColState(revColState);
  renderRevColPanel();
  paintReview(lastReview);
});
document.getElementById("revColList").addEventListener("change", (ev) => {
  const box = ev.target.closest("input[type=checkbox][data-id]");
  if (!box) return;
  const id = box.getAttribute("data-id");
  const row = revColState.find((c) => c.id === id);
  if (!row) return;
  row.on = !!box.checked;
  if (!revColState.some((c) => c.on)) {
    row.on = true;
    box.checked = true;
  }
  saveRevColState(revColState);
  paintReview(lastReview);
});
(() => {
  const list = document.getElementById("revColList");
  let dragIdx = null;
  list.addEventListener("dragstart", (ev) => {
    const li = ev.target.closest("li[data-idx]");
    if (!li) return;
    dragIdx = Number(li.dataset.idx);
    li.classList.add("dragging");
    ev.dataTransfer.effectAllowed = "move";
    try { ev.dataTransfer.setData("text/plain", String(dragIdx)); } catch (e) {}
  });
  list.addEventListener("dragend", (ev) => {
    const li = ev.target.closest("li");
    if (li) li.classList.remove("dragging");
    dragIdx = null;
  });
  list.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "move";
  });
  list.addEventListener("drop", (ev) => {
    ev.preventDefault();
    const li = ev.target.closest("li[data-idx]");
    if (!li || dragIdx == null) return;
    const to = Number(li.dataset.idx);
    if (to === dragIdx) return;
    const moved = revColState.splice(dragIdx, 1)[0];
    revColState.splice(to, 0, moved);
    saveRevColState(revColState);
    renderRevColPanel();
    paintReview(lastReview);
    dragIdx = null;
  });
})();
document.addEventListener("click", (ev) => {
  const panel = document.getElementById("revColPanel");
  if (!panel || panel.hidden) return;
  if (ev.target.closest("#revColPanel") || ev.target.closest("#revColsBtn")) return;
  panel.hidden = true;
});
document.addEventListener("click", (ev) => {
  const gPanel = document.getElementById("glossaryPanel");
  if (!gPanel || gPanel.hidden) return;
  const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
  if (
    path.includes(gPanel)
    || ev.target.closest("#glossaryPanel")
    || ev.target.closest("#glossaryBtn")
    || ev.target.closest("#glossaryBtnM")
    || ev.target.closest(".q")
  ) {
    return;
  }
  gPanel.hidden = true;
});
document.getElementById("revExport").addEventListener("click", () => {
  const rows = lastReview.signals || [];
  const cols = visibleRevCols().filter((id) => id !== "ops");
  const header = cols.map(revColLabel);
  const lines = [header.join(",")].concat(rows.map((r) => cols.map((id) => {
    if (id === "date") return r.signaled_at || r.trade_date;
    if (id === "type") return r.signal_type;
    if (id === "name") return r.name;
    if (id === "code") return r.code;
    if (id === "price") return r.price;
    if (id === "fill") return (r.fill_price == null && r.fill_qty == null) ? "" : `${r.fill_price ?? ""}x${r.fill_qty ?? ""}`;
    if (id === "chase") return r.chase_price ?? (r.payload && r.payload.chase_price);
    if (id === "mainline") return r.mainline;
    if (id === "board") return r.board_text || ((r.boards || []).join("/") );
    if (id === "vs_ml") return r.vs_mainline || "";
    if (id === "phase") return r.phase;
    if (id === "holder_n") return r.holder_num ?? "";
    if (id === "holder_chg") return r.holder_chg_pct ?? "";
    if (id === "holder_avg") return r.holder_avg_wan ?? "";
    if (id === "zt_ytd") return r.zt_ytd ?? "";
    if (id === "day1") return r.outcome_day1_pct;
    if (id === "day3") return r.outcome_day3_pct;
    if (id === "result") return r.outcome_label;
    return "";
  }).map((x) => `"${String(x ?? "").replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `market-desk-signals-${deskFileDate()}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
});
document.getElementById("revReport").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/report/today");
    const d = await r.json();
    if (!r.ok || !d.markdown) {
      alert("日报生成失败");
      return;
    }
    const blob = new Blob([d.markdown], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-daily-${deskFileDate()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    try {
      await navigator.clipboard.writeText(d.markdown);
      alert("日报已下载，并已复制到剪贴板");
    } catch (e) {
      alert("日报已下载");
    }
  } catch (e) {
    alert("日报生成失败");
  }
});
async function copyMorningBrief() {
  let text = window.__morningMarkdown || "";
  if (!text) {
    try {
      const r = await fetch("/api/report/morning");
      const d = await r.json();
      text = (d && d.markdown) || "";
    } catch (e) {
      text = "";
    }
  }
  if (!text) {
    alert("早摘要尚未生成");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    alert("早决策摘要已复制");
  } catch (e) {
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-morning-${deskFileDate()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    alert("已下载早决策摘要");
  }
}
document.getElementById("morningCopy").addEventListener("click", (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  copyMorningBrief();
});
document.getElementById("eodCopy").addEventListener("click", async () => {
  let text = window.__eodMarkdown || "";
  if (!text) {
    try {
      const params = new URLSearchParams();
      if (reviewViewDate) params.set("date", reviewViewDate);
      const q = params.toString() ? ("?" + params.toString()) : "";
      const r = await fetch("/api/report/eod" + q);
      const d = await r.json();
      text = (d && d.markdown) || "";
    } catch (e) {
      text = "";
    }
  }
  if (!text) {
    alert("收盘一页纸尚未生成");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    alert("收盘一页纸已复制");
  } catch (e) {
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-eod-${deskFileDate()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    alert("已下载收盘一页纸（剪贴板不可用）");
  }
});
document.getElementById("tomorrowCopy").addEventListener("click", async () => {
  let text = window.__tomorrowMarkdown || "";
  if (!text) {
    try {
      const params = new URLSearchParams();
      if (reviewViewDate) params.set("date", reviewViewDate);
      const q = params.toString() ? ("?" + params.toString()) : "";
      const r = await fetch("/api/report/tomorrow" + q);
      const d = await r.json();
      text = (d && d.markdown) || "";
    } catch (e) {
      text = "";
    }
  }
  if (!text) {
    alert("明日看点尚未生成");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    alert("明日看点已复制");
  } catch (e) {
    const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-tomorrow-${deskFileDate()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    alert("已下载明日看点（剪贴板不可用）");
  }
});
document.getElementById("revTomorrow").addEventListener("click", async () => {
  try {
    const params = new URLSearchParams();
    if (reviewViewDate) params.set("date", reviewViewDate);
    const q = params.toString() ? ("?" + params.toString()) : "";
    const r = await fetch("/api/report/tomorrow" + q);
    const d = await r.json();
    if (!r.ok || !d.markdown) {
      alert("明日看点生成失败");
      return;
    }
    const blob = new Blob([d.markdown], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-tomorrow-${deskFileDate()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    try {
      await navigator.clipboard.writeText(d.markdown);
      alert("明日看点已下载，并已复制到剪贴板");
    } catch (e) {
      alert("明日看点已下载");
    }
  } catch (e) {
    alert("明日看点生成失败");
  }
});
document.getElementById("revVsMl").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".vsml-btn");
  if (!btn) return;
  const mode = btn.getAttribute("data-mode") || "live";
  if (mode === reviewVsMlMode) return;
  reviewVsMlMode = mode;
  loadReview(true, reviewViewDate || "");
});
document.getElementById("revMorning").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/report/morning");
    const d = await r.json();
    if (!r.ok || !d.markdown) {
      alert("早摘要生成失败");
      return;
    }
    const blob = new Blob([d.markdown], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-morning-${deskFileDate()}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
    try {
      await navigator.clipboard.writeText(d.markdown);
      alert("早摘要已下载，并已复制到剪贴板");
    } catch (e) {
      alert("早摘要已下载");
    }
  } catch (e) {
    alert("早摘要生成失败");
  }
});
document.getElementById("revBackup").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/backup");
    const d = await r.json();
    const blob = new Blob([JSON.stringify(d.backup || {}, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `market-desk-backup-${deskFileDate()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    alert("备份失败");
  }
});
document.getElementById("revRestore").addEventListener("click", () => {
  document.getElementById("revRestoreFile").click();
});
document.getElementById("revRestoreFile").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const replace = confirm("恢复备份：确定=清空后覆盖，取消=合并导入");
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    const r = await fetch("/api/backup/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replace, payload }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert("恢复失败");
      return;
    }
    alert("恢复完成：" + JSON.stringify(d.counts || {}));
    reviewLoadedAt = 0;
    await loadReview(true);
    await tick();
  } catch (e) {
    alert("恢复失败：文件不是有效 JSON");
  }
});
document.getElementById("revBody").addEventListener("click", async (ev) => {
  const histBtn = ev.target.closest(".rev-hist-btn");
  if (histBtn) {
    ev.preventDefault();
    openSigHist(histBtn.getAttribute("data-code"), histBtn.getAttribute("data-name"));
    return;
  }
  const trade = ev.target.closest(".rev-trade");
  if (trade) {
    const id = trade.getAttribute("data-id");
    if (!id) return;
    const typ = trade.getAttribute("data-type") || "buy";
    const code = trade.getAttribute("data-code") || "";
    const name = trade.getAttribute("data-name") || code;
    let px = trade.getAttribute("data-price") || "";
    const suggest = trade.getAttribute("data-suggest") || "";
    const prefQty = trade.getAttribute("data-qty") || "";
    let book = true;
    let qty = typ === "buy" ? (prefQty ? Number(prefQty) : 100) : undefined;
    if (typ === "sell") {
      const holdPos = ((lastData && lastData.positions) || []).find(
        (p) => String(p.code || "").padStart(6, "0") === String(code || "").padStart(6, "0")
          && Number(p.qty || 0) > 0
      );
      const hold = holdPos ? Number(holdPos.qty || 0) : 0;
      const halfQ = halfSellQtyClient(hold);
      const adviceItem = ((((lastData || {}).sell_advice || {}).items) || []).find(
        (x) => String(x.code || "").padStart(6, "0") === String(code || "").padStart(6, "0")
      );
      const exitMode = (adviceItem && adviceItem.exit_mode) || "half";
      const defAns = exitMode === "clear" ? "2" : "1";
      const modeHint = exitMode === "clear" ? "作战台建议清仓" : (exitMode === "half" ? "作战台建议先减一半" : "");
      const ans = prompt(
        `${name} ${code}\n本地持仓 ${hold || "?"} 股${modeHint ? ` · ${modeHint}` : ""}\n输入 1 = 减半（${halfQ || "?"}股，100股四舍五入）\n输入 2 = 清仓（${hold || "?"}股）\n点取消 = 只标记已交易、不改仓位`,
        defAns
      );
      if (ans === null) {
        book = false;
      } else {
        book = true;
        const mode = String(ans).trim() === "2" ? "clear" : "half";
        const pxIn = prompt(`卖出价（默认现价 ${px || "请手填"}）`, px || "");
        if (pxIn === null) return;
        if (String(pxIn).trim()) px = String(pxIn).trim();
        try {
          const r = await fetch("/api/review/" + id + "/trade", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              book: true,
              sell_mode: mode,
              price: px ? Number(px) : undefined,
            }),
          });
          const d = await r.json().catch(() => ({}));
          if (!r.ok) {
            const detail = typeof d.detail === "string" ? d.detail : "记账失败";
            alert(detail);
          } else if (d.book_summary && d.book_summary.message) {
            alert(d.book_summary.message);
          } else if (d.booked) {
            const left = d.booked.qty;
            const sold = d.booked.trimmed;
            alert(`已记卖出 ${sold != null ? sold + "股" : ""}，剩余 ${left != null ? left : "?"}股`);
          }
        } catch (e) {
          alert("操作失败");
        }
        reviewLoadedAt = 0;
        await loadReview(true);
        await tick();
        return;
      }
    } else {
      const hint = [
        px ? `默认现价 ${px}` : "请填写实际成交价",
        suggest ? `计划建议价 ${suggest}` : "",
      ].filter(Boolean).join("；");
      const pxIn = prompt(`${name} ${code}\n成交价（${hint}）`, px || "");
      if (pxIn === null) return;
      if (String(pxIn).trim()) px = String(pxIn).trim();
      if (!String(px).trim()) {
        alert("请填写实际成交价（不要用空的建议价默认）");
        return;
      }
      const qtyDef = String(qty || 100);
      const qtyIn = prompt("成交数量（默认 " + qtyDef + "）", qtyDef);
      if (qtyIn === null) return;
      if (String(qtyIn).trim()) qty = Number(qtyIn);
      book = confirm(`按 ${px} × ${qty} 写入仓位？\n取消=只标记成交，不改仓位`);
    }
    try {
      const r = await fetch("/api/review/" + id + "/trade", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book,
          qty: typ === "buy" ? qty : undefined,
          price: typ === "buy" && px ? Number(px) : undefined,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof d.detail === "string" ? d.detail : "记账失败";
        alert(detail);
        // Do not force-mark traded on T+1 or other sell booking failures.
        if (typ === "buy" && !(String(detail).includes("T+1"))) {
          await fetch("/api/review/" + id, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              traded: true,
              skipped: false,
              fill_price: px ? Number(px) : undefined,
              fill_qty: qty,
            }),
          });
        }
      } else if (book && d.book_summary && d.book_summary.message) {
        alert(d.book_summary.message);
      } else if (book && typ === "buy" && d.booked) {
        alert(`已记买入 ${name || code}，持仓现 ${d.booked.qty || qty}股`);
      } else if (!book) {
        alert("已标记成交（未改仓位）");
      }
    } catch (e) {
      alert("操作失败");
    }
    reviewLoadedAt = 0;
    await loadReview(true);
    await tick();
    return;
  }
  const fillEdit = ev.target.closest(".rev-fill-edit");
  if (fillEdit) {
    openFillEditor(fillEdit);
    return;
  }
  const fillCancel = ev.target.closest(".rev-fill-cancel");
  if (fillCancel) {
    const ed = fillCancel.closest(".rev-fill-ed");
    const cell = ed && ed.closest("td");
    if (cell && cell.dataset.prevHtml != null) {
      cell.innerHTML = cell.dataset.prevHtml;
      delete cell.dataset.prevHtml;
    }
    return;
  }
  const fillSave = ev.target.closest(".rev-fill-save");
  if (fillSave) {
    const ed = fillSave.closest(".rev-fill-ed");
    if (ed) await saveFillEditor(ed);
    return;
  }
  const skip = ev.target.closest(".rev-skip");
  if (skip) {
    const id = skip.getAttribute("data-id");
    if (!id) return;
    await fetch("/api/review/" + id, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skipped: true, traded: false }),
    });
    reviewLoadedAt = 0;
    await loadReview(true);
    return;
  }
  const del = ev.target.closest(".rev-del");
  if (del) {
    const id = del.getAttribute("data-id");
    if (!id) return;
    if (!confirm("删除这条复盘信号？")) return;
    await fetch("/api/review/" + id, { method: "DELETE" });
    reviewLoadedAt = 0;
    await loadReview(true);
    return;
  }
});

const pop = document.getElementById("termPop");
document.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".q");
  if (!btn) {
    const tip = document.getElementById("termPop");
    if (tip && !ev.target.closest("#termPop")) tip.hidden = true;
    return;
  }
  ev.preventDefault();
  ev.stopPropagation();
  const key = btn.getAttribute("data-term");
  const open = () => {
    const g = GLOSSARY[key];
    if (ev.altKey) {
      pop.hidden = true;
      openGlossary(key);
      return;
    }
    if (!g) {
      showTermPop(btn, key, {
        mean: "暂无词条说明（术语表加载中或未收录）。",
        algo: "可点右上「术语」搜索；或等数据刷新后再点一次 ?。",
      });
      ensureGlossary(true).then((ok) => {
        if (ok && GLOSSARY[key]) showTermPop(btn, key, GLOSSARY[key]);
      });
      return;
    }
    showTermPop(btn, key, g);
  };
  if (!GLOSSARY[key] && Object.keys(GLOSSARY).length < 40) {
    ensureGlossary(true).then(open);
  } else {
    open();
  }
});
