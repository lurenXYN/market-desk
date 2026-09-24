function syncDeskFoldHints() {
  const fold = document.getElementById("deskObserveFold");
  if (!fold) return;
  const sum = fold.querySelector("summary");
  if (!sum) return;
  const ids = ["dragonbox", "sidebox", "linkbox", "trialbox", "indepbox", "favDesk"];
  const n = ids.filter((id) => {
    const el = document.getElementById(id);
    return el && !el.hidden;
  }).length;
  sum.textContent = n
    ? `扩展观察（龙头 / 支线 / 联动…）· ${n} 块有内容`
    : "扩展观察（龙头 / 支线 / 联动…）";
}
function applyMain(tab) {
  let next = tab || "desk";
  if (next === "pos" && typeof canPersonal === "function" && !canPersonal()) {
    showAuth("仓位需要正式账号（可先游客看盘，再注册）");
    next = currentMain && currentMain !== "pos" ? currentMain : "desk";
  }
  if (next === "backtest" && !(authUser && authUser.role === "admin")) {
    alert("回测仅管理员可用");
    next = currentMain && currentMain !== "backtest" ? currentMain : "desk";
  }
  currentMain = next;
  const moreTabs = new Set(["market", "boards", "funds", "watch", "auction", "mafan", "backtest"]);
  const moreLabels = {
    market: "大盘", boards: "板块", funds: "资金",
    watch: "观察", auction: "竞价", mafan: "发散", backtest: "回测",
  };
  document.querySelectorAll("#navTabs button[data-main]").forEach((el) =>
    el.classList.toggle("on", el.getAttribute("data-main") === currentMain)
  );
  document.querySelectorAll("#navMoreGrid button[data-main]").forEach((el) =>
    el.classList.toggle("on", el.getAttribute("data-main") === currentMain)
  );
  const moreBtn = document.getElementById("navMoreBtn");
  if (moreBtn) {
    const inMore = moreTabs.has(currentMain);
    moreBtn.classList.toggle("on", inMore);
    moreBtn.textContent = inMore ? (moreLabels[currentMain] || "更多") : "更多";
  }
  closeNavMore();
  ["desk", "market", "boards", "funds", "watch", "auction", "pos", "review", "mafan", "backtest"].forEach((name) => {
    const el = document.getElementById("panel-" + name);
    if (el) el.hidden = name !== currentMain;
  });
  if (currentMain === "review") loadReview(false);
  if (currentMain === "mafan") {
    loadMaFan(false);
    if (!maFanPollTimer) pollMaFanProgress();
  }
  if (currentMain === "backtest" && typeof ensureBacktestDefaults === "function") ensureBacktestDefaults();
  if (currentMain === "funds") ensureFundFlowFull();
  if (currentMain === "pos" && typeof ensurePosLhb === "function") ensurePosLhb(false);
  if (currentMain === "pos" && typeof ensurePosDiary === "function") ensurePosDiary(false);
  if (currentMain === "pos" && typeof ensurePosCal === "function") ensurePosCal(false);
  try { localStorage.setItem("desk-main-tab", currentMain); } catch (e) {}
  // Reschedule poll cadence for the active tab.
  if (lastData) scheduleTick(!!lastData.live);
  else scheduleTick(false);
  // Pull a sliced snapshot for the newly visible tab.
  tick(true);
}
function openNavMore() {
  const sheet = document.getElementById("navMoreSheet");
  const mask = document.getElementById("navMoreMask");
  const btn = document.getElementById("navMoreBtn");
  if (sheet) { sheet.hidden = false; sheet.classList.add("on"); }
  if (mask) { mask.hidden = false; mask.classList.add("on"); }
  if (btn) btn.setAttribute("aria-expanded", "true");
}
function closeNavMore() {
  const sheet = document.getElementById("navMoreSheet");
  const mask = document.getElementById("navMoreMask");
  const btn = document.getElementById("navMoreBtn");
  if (sheet) { sheet.hidden = true; sheet.classList.remove("on"); }
  if (mask) { mask.hidden = true; mask.classList.remove("on"); }
  if (btn) btn.setAttribute("aria-expanded", "false");
}
function paintClock() {
  const el = document.getElementById("pageClock");
  if (el) el.textContent = "本机 " + new Date().toTimeString().slice(0, 8);
  if (lastData) {
    document.getElementById("dataAge").textContent = dataAgeText(lastData.updated_at);
  }
  // Re-arm when auction cadence flips at 09:15 / 09:30 without waiting for next tick.
  if ((currentMain || "desk") === "auction" && tickLive != null) {
    scheduleTick(tickLive);
  }
}
function tickIntervalMs(live) {
  const tab = currentMain || "desk";
  if (tab === "review") return live ? 60000 : 120000;
  if (tab === "funds") return live ? 45000 : 90000;
  if (tab === "market" || tab === "boards") return live ? 30000 : 90000;
  // Auction tab: fast only in 09:15–09:30; after open, 1.5min is enough
  // (auction summary locks at 09:25; strategy is follow-through only).
  if (tab === "auction") {
    const mins = new Date().getHours() * 60 + new Date().getMinutes();
    const inCallAuction = mins >= 9 * 60 + 15 && mins < 9 * 60 + 30;
    if (live && inCallAuction) {
      const snapSec = lastData && Number(lastData.refresh_seconds);
      if (snapSec > 0) return Math.max(3000, Math.round(snapSec * 1000));
      return 5000;
    }
    return live ? 90000 : 180000;
  }
  const snapSec = lastData && Number(lastData.refresh_seconds);
  if (live && snapSec > 0) {
    return Math.max(5000, Math.round(snapSec * 1000));
  }
  return live ? 20000 : 60000;
}
async function tick(force) {
  if (!authUser) return;
  try {
    const view = snapshotViewForTab(currentMain);
    const r = await fetch("/api/snapshot?view=" + encodeURIComponent(view), {
      credentials: "same-origin",
    });
    if (r.status === 401) {
      paintAuthUser(null);
      return;
    }
    const d = await r.json();
    if (!d.ok && !d.updated_at) {
      document.getElementById("stamp").textContent = d.error || "等待首包";
      document.getElementById("dataAge").textContent = d.error || "等待首包";
      return;
    }
    const prevUid = authUser && authUser.id;
    const nextUid = d.auth_user && d.auth_user.id;
    // Account switch / first pack: replace cache so another user's books never linger.
    // Tab switches use force=true only to refetch; still merge so market.elliott
    // (and other tab slices) are not wiped when leaving/re-entering a tab.
    const accountSwitch = prevUid != null && nextUid != null && prevUid !== nextUid;
    if (!lastData || accountSwitch) {
      lastData = d;
    } else {
      lastData = Object.assign({}, lastData || {}, d);
    }
    if (d.glossary && typeof d.glossary === "object") {
      // Merge so a later thin payload never wipes a loaded glossary.
      GLOSSARY = Object.assign({}, GLOSSARY, d.glossary);
    }
    if (d.auth_user !== undefined) paintAuthUser(d.auth_user);
    // Always replace personal books when the slice carries them (keep marks fresh).
    if (Array.isArray(d.positions)) lastData.positions = d.positions;
    if (Array.isArray(d.watchlist)) lastData.watchlist = d.watchlist;
    if (Array.isArray(d.favorite_boards)) lastData.favorite_boards = d.favorite_boards;
    if (d.position_summary) lastData.position_summary = d.position_summary;
    if (d.risk_overview) lastData.risk_overview = d.risk_overview;
    if (d.sell_advice) lastData.sell_advice = d.sell_advice;
    // Guest / locked: never keep stale personal rows.
    if (!d.auth_user || d.personal_locked) {
      lastData.positions = Array.isArray(d.positions) ? d.positions : [];
      lastData.watchlist = Array.isArray(d.watchlist) ? d.watchlist : [];
      lastData.favorite_boards = Array.isArray(d.favorite_boards) ? d.favorite_boards : [];
      lastData.position_summary = d.position_summary || {};
      lastData.risk_overview = d.risk_overview || lastData.position_summary || {};
      lastData.sell_advice = d.sell_advice || { items: [], text: "" };
      if (lastData.verdict) {
        lastData.verdict = Object.assign({}, lastData.verdict, {
          watch_trial_recommend: (d.verdict && d.verdict.watch_trial_recommend) || null,
          independent_recommend: (d.verdict && d.verdict.independent_recommend) || null,
          dragon_recommend: (d.verdict && d.verdict.dragon_recommend) || null,
        });
      }
    }
    paintVisible(lastData);
    paintClock();
    scheduleTick(!!lastData.live);
    if (currentMain === "funds") ensureFundFlowFull();
  } catch (e) {
    document.getElementById("stamp").textContent = "数据源中断";
    document.getElementById("dataAge").textContent = "数据源中断";
  }
}
