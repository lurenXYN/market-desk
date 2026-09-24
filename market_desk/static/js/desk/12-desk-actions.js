let tickTimer = null;
let tickLive = null;
let tickMs = null;
function scheduleTick(live) {
  const ms = tickIntervalMs(!!live);
  if (tickLive === !!live && tickTimer && tickMs === ms) return;
  tickLive = !!live;
  tickMs = ms;
  if (tickTimer) clearInterval(tickTimer);
  tickTimer = setInterval(() => tick(false), ms);
}
applyMain(currentMain);
scheduleTick(false);
setInterval(paintClock, 1000);
document.getElementById("navTabs").addEventListener("click", (ev) => {
  if (ev.target.closest("#navMoreBtn")) {
    const sheet = document.getElementById("navMoreSheet");
    if (sheet && !sheet.hidden) closeNavMore();
    else openNavMore();
    return;
  }
  const hit = ev.target.closest("button[data-main]");
  if (!hit) return;
  applyMain(hit.getAttribute("data-main"));
});
document.getElementById("navMoreGrid")?.addEventListener("click", (ev) => {
  const hit = ev.target.closest("button[data-main]");
  if (!hit) return;
  applyMain(hit.getAttribute("data-main"));
});
document.getElementById("navMoreClose")?.addEventListener("click", closeNavMore);
document.getElementById("navMoreMask")?.addEventListener("click", closeNavMore);
document.getElementById("panel-auction").addEventListener("click", async (ev) => {
  const pin = ev.target.closest(".auc-pin");
  if (!pin) return;
  const code = (pin.getAttribute("data-code") || "").trim();
  const name = (pin.getAttribute("data-name") || "").trim();
  if (!code) return;
  try {
    const r = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name, note: "竞价策略钉入" }),
    });
    if (!r.ok) {
      alert("钉自选失败");
      return;
    }
    const body = await r.json().catch(() => ({}));
    pin.textContent = "已钉";
    pin.disabled = true;
    if (Array.isArray(body.watchlist)) {
      lastData = Object.assign({}, lastData || {}, { watchlist: body.watchlist });
      paintVisible(lastData);
    }
    await tick();
  } catch (e) {
    alert("钉自选失败");
  }
});
document.getElementById("timeline").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".node");
  if (!btn || btn.disabled) return;
  const i = Number(btn.getAttribute("data-i"));
  if (Number.isNaN(i) || !lastData) return;
  const node = ((lastData.cycle || {}).nodes || []).find((n) => n.i === i);
  cycleSel = node && node.current ? null : i;
  paintTimeline(lastData);
  paintCycleEvents(lastData);
});
async function onDeskRecClick(ev) {
  const lotBtn = ev.target.closest(".batch-lot");
  if (lotBtn) {
    const code = (lotBtn.dataset.code || "").trim();
    const name = (lotBtn.dataset.name || "").trim();
    if (!code) return;
    let px = lotBtn.dataset.last || lotBtn.dataset.price || "";
    const suggest = lotBtn.dataset.suggest || lotBtn.dataset.price || "";
    let qty = Number(lotBtn.dataset.qty || 100) || 100;
    const label = lotBtn.dataset.label || ("第" + (lotBtn.dataset.lot || ""));
    const hint = [
      px ? `默认现价 ${px}` : "请填写实际成交价",
      suggest && String(suggest) !== String(px) ? `计划建议价 ${suggest}` : "",
      `分批档 ${label} · ${qty}股`,
    ].filter(Boolean).join("；");
    const pxIn = prompt(`${name || code}\n成交价（${hint}）`, px || "");
    if (pxIn === null) return;
    if (String(pxIn).trim()) px = String(pxIn).trim();
    const qtyIn = prompt(`成交数量（股，默认本档 ${qty}）`, String(qty));
    if (qtyIn === null) return;
    if (String(qtyIn).trim()) qty = Number(qtyIn);
    qty = Math.max(0, Math.floor(Number(qty) || 0));
    const price = Number(px);
    if (!(price > 0) || !(qty > 0)) {
      alert("请填写有效成交价和数量");
      return;
    }
    if (!confirm(`确认记入独立 lot？\n${name || code} ${code}\n${label} ${price} × ${qty} 股\n（仅本地记账，不会下单）`)) {
      return;
    }
    const fromFav = (ev.currentTarget && ev.currentTarget.id === "favDesk");
    const deskSrc = lotBtn.dataset.deskSource || "";
    let note = fromFav ? `看好板块·分批${label}` : `作战台·分批${label}`;
    if (deskSrc === "independent_pop") note = `独立人气·分批${label}`;
    else if (deskSrc === "emotion_dragon" || deskSrc === "mid_army_dragon" || deskSrc === "dragon")
      note = `龙头排·分批${label}`;
    try {
      const ok = await bookBuyToPosition({ code, name, price, qty, note });
      if (ok) {
        await tick();
        applyMain("pos");
      }
    } catch (e) {
      alert("记入仓位失败");
    }
    return;
  }
  const bookBtn = ev.target.closest(".rec-book");
  if (bookBtn) {
    const code = (bookBtn.dataset.code || "").trim();
    const name = (bookBtn.dataset.name || "").trim();
    if (!code) return;
    // Default to live last; plan suggest is only a hint in the prompt.
    let px = bookBtn.dataset.last || bookBtn.dataset.price || "";
    const suggest = bookBtn.dataset.suggest || bookBtn.dataset.price || "";
    let qty = Number(bookBtn.dataset.qty || 100) || 100;
    const hint = [
      px ? `默认现价 ${px}` : "请填写实际成交价",
      suggest && String(suggest) !== String(px) ? `计划建议价 ${suggest}` : "",
    ].filter(Boolean).join("；");
    const pxIn = prompt(
      `${name || code}\n成交价（${hint}）`,
      px || ""
    );
    if (pxIn === null) return;
    if (String(pxIn).trim()) px = String(pxIn).trim();
    const qtyIn = prompt("成交数量（股，默认风险建议股数）", String(qty));
    if (qtyIn === null) return;
    if (String(qtyIn).trim()) qty = Number(qtyIn);
    qty = Math.max(0, Math.floor(Number(qty) || 0));
    const price = Number(px);
    if (!(price > 0) || !(qty > 0)) {
      alert("请填写有效成交价和数量");
      return;
    }
    if (!confirm(`确认记入仓位？\n${name || code} ${code}\n${price} × ${qty} 股\n（仅本地记账，不会下单）`)) {
      return;
    }
    const fromFav = (ev.currentTarget && ev.currentTarget.id === "favDesk");
    const deskSrc = (bookBtn && bookBtn.dataset.deskSource) || "";
    let note = fromFav ? "看好板块记入" : "作战台记入";
    if (deskSrc === "independent_pop") note = "独立人气·作战台记入";
    else if (deskSrc === "emotion_dragon" || deskSrc === "mid_army_dragon" || deskSrc === "dragon")
      note = "龙头排·作战台记入";
    try {
      const ok = await bookBuyToPosition({
        code,
        name,
        price,
        qty,
        note,
      });
      if (ok) {
        await tick();
        applyMain("pos");
      }
    } catch (e) {
      alert("记入仓位失败");
    }
    return;
  }
  const btn = ev.target.closest(".rec-pos");
  if (btn) {
    const form = document.getElementById("posForm");
    form.code.value = btn.dataset.code || "";
    form.name.value = btn.dataset.name || "";
    form.buy_price.value = btn.dataset.price || "";
    form.qty.value = btn.dataset.qty || form.qty.value || "100";
    applyMain("pos");
    form.qty.focus();
    return;
  }
  const pin = ev.target.closest(".rec-pin");
  if (!pin) return;
  const numOrNull = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  try {
    const r = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: pin.dataset.code || "",
        name: pin.dataset.name || "",
        suggest_price: numOrNull(pin.dataset.suggest),
        stop_price: numOrNull(pin.dataset.stop),
        chase_price: numOrNull(pin.dataset.chase),
        note: "从推荐钉入",
      }),
    });
    if (!r.ok) {
      alert("钉自选失败");
      return;
    }
    const body = await r.json().catch(() => ({}));
    pin.textContent = "已钉";
    pin.disabled = true;
    if (Array.isArray(body.watchlist)) {
      lastData = Object.assign({}, lastData || {}, { watchlist: body.watchlist });
      paintVisible(lastData);
    }
    await tick();
  } catch (e) {
    alert("钉自选失败");
  }
}
["buybox", "dragonbox", "sidebox", "linkbox", "trialbox", "indepbox", "favDesk"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("click", onDeskRecClick);
});

async function bookBuyToPosition({ code, name, price, qty, note }) {
  // Book a desk buy into local positions; sync today's buy signal when present.
  const norm = String(code || "").replace(/\D/g, "").padStart(6, "0");
  // Prefer review trade path so signal + position stay in sync.
  try {
    await loadReview(true, "");
  } catch (e) { /* ignore */ }
  const day = (lastData && lastData.trade_date) || (lastReview && lastReview.view_date) || "";
  const buyCands = ((lastReview && lastReview.signals) || []).filter((s) => {
    const st = String(s.signal_type || "");
    return (st === "buy" || st.startsWith("buy_"))
      && String(s.code || "").replace(/\D/g, "").padStart(6, "0") === norm
      && (!day || String(s.trade_date || "").slice(0, 10) === String(day).slice(0, 10))
      && !Number(s.traded || 0);
  });
  const sig = buyCands.find((s) => s.signal_type === "buy") || buyCands[0];
  if (sig && sig.id) {
    const r = await fetch("/api/review/" + sig.id + "/trade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book: true,
        price,
        qty,
        note: note || "作战台记入",
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(typeof d.detail === "string" ? d.detail : "记入仓位失败");
      return false;
    }
    alert((d.book_summary && d.book_summary.message)
      || `已记买入 ${name || code} ${qty}股 @ ${price}`);
    reviewLoadedAt = 0;
    return true;
  }
  const r = await fetch("/api/positions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code: norm,
      name: name || "",
      buy_price: price,
      qty,
      note: note || "作战台记入",
    }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    alert(typeof d.detail === "string" ? d.detail : "记入仓位失败");
    return false;
  }
  const hold = ((d.positions || []).find((p) =>
    String(p.code || "").padStart(6, "0") === norm && Number(p.qty || 0) > 0
  ) || {}).qty;
  alert(`已记买入 ${name || code} ${qty}股 @ ${price}`
    + (hold != null ? `；持仓现 ${hold}股` : ""));
  return true;
}
document.getElementById("sellbox").addEventListener("click", onDeskSellClick);
const favDeskSell = document.getElementById("favDesk");
if (favDeskSell) favDeskSell.addEventListener("click", onDeskSellClick);
document.getElementById("themeMemBody")?.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-theme-adj], [data-theme-clear]");
  if (btn) {
    if (btn.disabled) return;
    if (!(authUser && authUser.role === "admin")) {
      alert("题材手调仅管理员可用");
      return;
    }
    const card = btn.closest("[data-theme]");
    const theme = card && card.getAttribute("data-theme");
    if (!theme) return;
    const body = { theme_key: theme };
    if (btn.hasAttribute("data-theme-clear")) body.clear = true;
    else body.delta = Number(btn.getAttribute("data-theme-adj") || 0);
    try {
      const r = await fetch("/api/theme-reputation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) {
        alert(typeof d.detail === "string" ? d.detail : (d.error || "手调失败"));
        return;
      }
      if (lastData && d.theme_memory) {
        lastData.theme_memory = d.theme_memory;
        paintVisible(lastData);
      } else {
        tick(true);
      }
    } catch (e) {
      alert("手调请求失败");
    }
    return;
  }
  const card = ev.target.closest(".theme-mem-item[data-theme]");
  if (!card) return;
  const theme = card.getAttribute("data-theme");
  if (theme) loadThemeChain(theme);
});
document.getElementById("themeChainClose")?.addEventListener("click", () => {
  const el = document.getElementById("themeChain");
  if (el) el.hidden = true;
  document.querySelectorAll(".theme-mem-item.active").forEach((n) => n.classList.remove("active"));
});
async function loadThemeChain(theme) {
  const box = document.getElementById("themeChain");
  const body = document.getElementById("themeChainBody");
  const meta = document.getElementById("themeChainMeta");
  if (!box || !body) return;
  document.querySelectorAll(".theme-mem-item.active").forEach((n) => n.classList.remove("active"));
  const active = document.querySelector(`.theme-mem-item[data-theme="${CSS.escape(theme)}"]`);
  if (active) active.classList.add("active");
  box.hidden = false;
  if (meta) meta.textContent = "加载中…";
  body.innerHTML = `<div class="meta">拉取 ${theme} …</div>`;
  try {
    const r = await fetch("/api/theme-chain?theme=" + encodeURIComponent(theme) + "&limit=14");
    const d = await r.json();
    if (!r.ok || !d.ok) {
      body.innerHTML = `<div class="meta">加载失败</div>`;
      if (meta) meta.textContent = "";
      return;
    }
    const items = d.items || [];
    if (meta) meta.textContent = `${d.theme_key || theme} · ${items.length} 日`;
    if (!items.length) {
      body.innerHTML = `<div class="meta">暂无次日结算样本</div>`;
      return;
    }
    const outLabel = { persist: "续热", fade: "熄火", unclear: "不明" };
    body.innerHTML = items.map((it) => {
      const oc = String(it.outcome || "unclear");
      const day = String(it.trade_date || "").slice(5) || "—";
      const zt = it.zt_n != null ? `昨${it.zt_n}` : "";
      const nzt = it.next_zt_n != null ? `次${it.next_zt_n}` : "";
      const heat = [zt, nzt].filter(Boolean).join("/");
      return `<div class="node ${oc}" title="${(it.mainline || it.theme_key || "")}">
            <div class="d">${day}</div>
            <div class="o">${outLabel[oc] || oc}</div>
            <div class="d">${heat || "—"}</div>
          </div>`;
    }).join("");
  } catch (e) {
    body.innerHTML = `<div class="meta">请求失败</div>`;
    if (meta) meta.textContent = "";
  }
}
async function onDeskSellClick(ev) {
  if (ev.target.closest(".sell-go")) {
    applyMain("pos");
    return;
  }
  const half = ev.target.closest(".sell-half");
  const clear = ev.target.closest(".sell-clear");
  const btn = half || clear;
  if (!btn) return;
  const id = btn.getAttribute("data-id");
  const qty = Number(btn.getAttribute("data-qty") || 0);
  const last = btn.getAttribute("data-last") || "";
  if (!id || !qty) {
    alert("无仓位 id，请到仓位页操作");
    applyMain("pos");
    return;
  }
  const label = half ? "减半" : "清仓";
  if (!confirm(`${label} ${qty} 股？`)) return;
  const pxRaw = prompt(`卖出价（回车用现价 ${last || "买价"}）`, last || "");
  if (pxRaw == null) return;
  const body = { qty };
  const sellPx = Number(pxRaw);
  if (sellPx > 0) body.sell_price = sellPx;
  else if (Number(last) > 0) body.sell_price = Number(last);
  const r = await fetch("/api/positions/" + id + "/trim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    alert(label + "失败");
    return;
  }
  reviewLoadedAt = 0;
  if (d.signal_fill && d.signal_fill.fill_price != null) {
    alert(`${label}完成 · 复盘已填成交 ${d.signal_fill.fill_price}×${d.signal_fill.fill_qty || ""}`);
  }
  await tick();
}

async function trimPositionById(id, sellQty, last, label) {
  if (!id || !sellQty) return false;
  if (!confirm(`${label} ${sellQty} 股？`)) return false;
  const pxRaw = prompt(`卖出价（回车用现价 ${last || "买价"}）`, last || "");
  if (pxRaw == null) return false;
  const body = { qty: sellQty };
  const sellPx = Number(pxRaw);
  if (sellPx > 0) body.sell_price = sellPx;
  else if (Number(last) > 0) body.sell_price = Number(last);
  const r = await fetch("/api/positions/" + id + "/trim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    alert(label + "失败");
    return false;
  }
  reviewLoadedAt = 0;
  if (d.signal_fill && d.signal_fill.fill_price != null) {
    alert(`${label}完成 · 复盘已填成交 ${d.signal_fill.fill_price}×${d.signal_fill.fill_qty || ""}`);
  }
  return true;
}

function halfSellQtyClient(hold) {
  hold = Number(hold || 0);
  if (hold <= 0) return 0;
  if (hold <= 100) return hold;
  const rounded = Math.floor((hold / 2) / 100 + 0.5) * 100;
  return Math.min(hold, Math.max(100, rounded || 100));
}
document.getElementById("posForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const payload = {
    code: f.code.value.trim(),
    name: f.name.value.trim(),
    buy_price: Number(f.buy_price.value),
    qty: Number(f.qty.value),
    note: f.note.value.trim(),
    match_signal: !!(f.match_signal && f.match_signal.checked),
  };
  try {
    const r = await fetch("/api/positions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const t = await r.text();
      alert("记账失败: " + t);
      return;
    }
    const d = await r.json().catch(() => ({}));
    f.reset();
    if (f.match_signal) f.match_signal.checked = true;
    await tick();
    if (typeof _posDiaryAt !== "undefined") _posDiaryAt = 0;
    if (typeof _posCalAt !== "undefined") _posCalAt = 0;
    applyMain("pos");
    if (d.matched_signal && d.matched_signal.matched) {
      pushPageToast("已匹配买信号", `${payload.code} 已标复盘已交易`);
    }
  } catch (e) {
    alert("记账失败");
  }
});
document.getElementById("wlForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  const numOrNull = (v) => {
    const s = String(v || "").trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const payload = {
    code: f.code.value.trim(),
    name: f.name.value.trim(),
    note: f.note.value.trim(),
    suggest_price: numOrNull(f.suggest_price.value),
    stop_price: numOrNull(f.stop_price.value),
    chase_price: numOrNull(f.chase_price.value),
  };
  try {
    const r = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(typeof body.detail === "string" ? body.detail : "加入自选失败");
      return;
    }
    f.reset();
    // Immediate list feedback from API payload (do not wait for next slice tick).
    if (Array.isArray(body.watchlist)) {
      lastData = Object.assign({}, lastData || {}, { watchlist: body.watchlist });
      paintVisible(lastData);
    }
    applyMain("watch");
    await tick();
  } catch (e) {
    alert("加入自选失败");
  }
});
document.getElementById("wlBody").addEventListener("click", async (ev) => {
  const del = ev.target.closest(".wl-del");
  if (!del) return;
  const id = del.getAttribute("data-id");
  if (!id) return;
  if (!confirm("删除这条自选？")) return;
  const r = await fetch("/api/watchlist/" + id, { method: "DELETE" });
  const body = await r.json().catch(() => ({}));
  if (Array.isArray(body.watchlist)) {
    lastData = Object.assign({}, lastData || {}, { watchlist: body.watchlist });
    paintVisible(lastData);
  }
  await tick();
});
const blForm = document.getElementById("blForm");
if (blForm) {
  blForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target;
    try {
      const r = await fetch("/api/blacklist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: f.code.value.trim(),
          name: f.name.value.trim(),
          note: f.note.value.trim(),
          reason: "手动加入",
        }),
      });
      if (!r.ok) {
        alert("加入黑名单失败");
        return;
      }
      f.reset();
      await tick();
      applyMain("watch");
    } catch (e) {
      alert("加入黑名单失败");
    }
  });
}
const blBodyEl = document.getElementById("blBody");
if (blBodyEl) {
  blBodyEl.addEventListener("click", async (ev) => {
    const del = ev.target.closest(".bl-del");
    if (!del) return;
    const code = (del.getAttribute("data-code") || "").trim();
    if (!code) return;
    if (!confirm("从黑名单移除 " + code + "？")) return;
    const r = await fetch("/api/blacklist/" + code, { method: "DELETE" });
    if (!r.ok) {
      alert("移除失败");
      return;
    }
    await tick();
  });
}
document.getElementById("panel-boards").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".fav-btn");
  if (!btn) return;
  const bk = (btn.getAttribute("data-bk") || "").trim();
  if (!bk) return;
  try {
    if (btn.classList.contains("on")) {
      const id = (btn.getAttribute("data-id") || "").trim();
      const url = id
        ? ("/api/favorite-boards/" + id)
        : ("/api/favorite-boards/by-bk/" + encodeURIComponent(bk));
      const r = await fetch(url, { method: "DELETE" });
      if (!r.ok) {
        alert("取消看好失败");
        return;
      }
    } else {
      const r = await fetch("/api/favorite-boards", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bk,
          name: btn.getAttribute("data-name") || "",
          kind: btn.getAttribute("data-kind") || "",
          note: "",
        }),
      });
      if (!r.ok) {
        alert("加入看好失败");
        return;
      }
    }
    await tick();
    applyMain("boards");
  } catch (e) {
    alert("看好板块操作失败");
  }
});
document.getElementById("watchList").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".wl-add");
  if (!btn || btn.disabled || btn.classList.contains("in")) return;
  const code = (btn.getAttribute("data-code") || "").trim();
  const name = (btn.getAttribute("data-name") || "").trim();
  if (!code) return;
  const suggestRaw = btn.getAttribute("data-suggest");
  const stopRaw = btn.getAttribute("data-stop");
  const chaseRaw = btn.getAttribute("data-chase");
  const suggest = suggestRaw ? Number(suggestRaw) : undefined;
  const stop = stopRaw ? Number(stopRaw) : undefined;
  const chase = chaseRaw ? Number(chaseRaw) : undefined;
  try {
    const r = await fetch("/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code,
        name,
        note: "异动池加入",
        suggest_price: Number.isFinite(suggest) && suggest > 0 ? suggest : undefined,
        stop_price: Number.isFinite(stop) && stop > 0 ? stop : undefined,
        chase_price: Number.isFinite(chase) && chase > 0 ? chase : undefined,
      }),
    });
    if (!r.ok) {
      alert("加入自选失败");
      return;
    }
    const body = await r.json().catch(() => ({}));
    btn.textContent = "已自选";
    btn.disabled = true;
    btn.classList.add("in");
    if (Array.isArray(body.watchlist)) {
      lastData = Object.assign({}, lastData || {}, { watchlist: body.watchlist });
      paintVisible(lastData);
    }
    applyMain("watch");
    await tick();
  } catch (e) {
    alert("加入自选失败");
  }
});
document.getElementById("posBody").addEventListener("click", async (ev) => {
  const addBtn = ev.target.closest(".pos-add");
  if (addBtn) {
    const form = document.getElementById("posForm");
    form.code.value = addBtn.dataset.code || "";
    form.name.value = addBtn.dataset.name || "";
    form.buy_price.focus();
    return;
  }
  const halfBtn = ev.target.closest(".pos-half");
  if (halfBtn) {
    const id = halfBtn.getAttribute("data-id");
    const qty = Number(halfBtn.getAttribute("data-qty") || 0);
    const last = halfBtn.getAttribute("data-last") || "";
    const sellQty = halfSellQtyClient(qty);
    if (await trimPositionById(id, sellQty, last, "减半卖出")) {
      await tick();
      applyMain("pos");
    }
    return;
  }
  const clearBtn = ev.target.closest(".pos-clear");
  if (clearBtn) {
    const id = clearBtn.getAttribute("data-id");
    const qty = Number(clearBtn.getAttribute("data-qty") || 0);
    const last = clearBtn.getAttribute("data-last") || "";
    if (await trimPositionById(id, qty, last, "清仓")) {
      await tick();
      applyMain("pos");
    }
    return;
  }
  const trimBtn = ev.target.closest(".pos-trim");
  if (trimBtn) {
    const id = trimBtn.getAttribute("data-id");
    const qty = Number(trimBtn.getAttribute("data-qty") || 0);
    const last = trimBtn.getAttribute("data-last") || "";
    if (!id || !qty) return;
    const raw = prompt(`自定义减仓股数（当前 ${qty}，一手=100）`, String(halfSellQtyClient(qty)));
    if (raw == null) return;
    const sellQty = Number(raw);
    if (!sellQty || sellQty <= 0) return;
    if (await trimPositionById(id, sellQty, last, "自定义减仓")) {
      await tick();
      applyMain("pos");
    }
    return;
  }
  const btn = ev.target.closest(".pos-del");
  if (!btn || btn.classList.contains("pos-add") || btn.classList.contains("pos-trim")
      || btn.classList.contains("pos-half") || btn.classList.contains("pos-clear")) return;
  const id = btn.getAttribute("data-id");
  if (!id) return;
  await fetch("/api/positions/" + id, { method: "DELETE" });
  await tick();
  applyMain("pos");
});

document.addEventListener("click", (ev) => {
  const xqOnly = ev.target.closest("a.xq-link");
  if (xqOnly) return; // let browser open Xueqiu
  const hit = ev.target.closest(".js-chart");
  if (!hit) return;
  ev.preventDefault();
  openChart(
    hit.dataset.code,
    hit.dataset.name,
    hit.dataset.signalAt || hit.getAttribute("data-signal-at") || "",
    hit.dataset.revId || hit.getAttribute("data-rev-id") || ""
  );
});
document.getElementById("sigHistBody").addEventListener("click", (ev) => {
  if (ev.target.closest(".sig-hist-day")) return;
  const tr = ev.target.closest("tr.sig-hist-row");
  if (!tr) return;
  const idx = Number(tr.dataset.rowIdx);
  if (!Number.isFinite(idx) || !sigHistRows[idx]) return;
  paintSigHistDesc(sigHistRows[idx], idx);
});
document.getElementById("chartClose").addEventListener("click", closeChart);
document.getElementById("chartMask").addEventListener("click", (ev) => {
  if (ev.target.id === "chartMask") closeChart();
});
document.getElementById("sigHistClose").addEventListener("click", closeSigHist);
document.getElementById("sigHistMask").addEventListener("click", (ev) => {
  if (ev.target.id === "sigHistMask") closeSigHist();
});
document.getElementById("sigHistRefresh").addEventListener("click", () => {
  if (sigHistCode) openSigHist(sigHistCode, sigHistName);
});
document.getElementById("sigHistBody").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".sig-hist-day");
  if (!btn) return;
  const day = btn.getAttribute("data-day");
  if (!day) return;
  closeSigHist();
  loadReview(true, day);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    closeChart();
    closeSigHist();
    const gPanel = document.getElementById("glossaryPanel");
    if (gPanel && !gPanel.hidden) gPanel.hidden = true;
    const sPanel = document.getElementById("settingsPanel");
    if (sPanel && !sPanel.hidden) sPanel.hidden = true;
    return;
  }
  const tag = ((ev.target && ev.target.tagName) || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (ev.target && ev.target.isContentEditable)) {
    return;
  }
  const tabMap = { "1": "desk", "2": "market", "3": "boards", "4": "funds", "5": "watch", "6": "auction", "7": "pos", "8": "review" };
  if (tabMap[ev.key]) {
    applyMain(tabMap[ev.key]);
    return;
  }
  if ((ev.key === "t" || ev.key === "T") && currentMain === "review") {
    const btn = document.querySelector("#revBody .rev-trade:not(.on)");
    if (btn) btn.click();
  }
});
document.getElementById("chartRefresh").addEventListener("click", () => {
  if (chartCode) openChart(chartCode, "", chartSignalAt, chartRevId);
});
function deskFileDate() {
  const d = (lastData && lastData.trade_date)
    || (lastReview && lastReview.summary && lastReview.summary.today && lastReview.summary.today.date)
    || new Date().toISOString().slice(0, 10);
  return String(d).replace(/[^\d-]/g, "") || new Date().toISOString().slice(0, 10);
}
document.getElementById("revApply").addEventListener("click", () => {
  saveRevFilters();
  paintReview(lastReview);
});
["revSource", "revHideWait", "revType", "revKind"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", () => {
    saveRevFilters();
    paintReview(lastReview);
  });
});
const revMainEl = document.getElementById("revMain");
if (revMainEl) {
  revMainEl.addEventListener("change", () => {
    saveRevFilters();
    paintReview(lastReview);
  });
  revMainEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      saveRevFilters();
      paintReview(lastReview);
    }
  });
}
const revHeadEl = document.getElementById("revHead");
if (revHeadEl) {
  revHeadEl.addEventListener("click", (ev) => {
    const th = ev.target && ev.target.closest ? ev.target.closest("th.rev-sort") : null;
    if (!th) return;
    const col = th.getAttribute("data-sort");
    if (!col) return;
    if (revSort.id === col) {
      revSort.dir = -revSort.dir;
    } else {
      revSort.id = col;
      revSort.dir = -1; // first click: descending
    }
    paintReview(lastReview);
  });
}
document.getElementById("revHitMode").addEventListener("change", async () => {
  const mode = document.getElementById("revHitMode").value || "traded";
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hit_rate_mode: mode }),
    });
  } catch (e) {}
  loadReview(true, reviewViewDate || "");
});
document.getElementById("revOcMode")?.addEventListener("change", async () => {
  const oc = document.getElementById("revOcMode").value || "classic";
  reviewOcMode = oc;
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ outcome_standard: oc }),
    });
  } catch (e) {}
  loadReview(true, reviewViewDate || "");
});
document.getElementById("revDayPrev").addEventListener("click", () => shiftReviewDay(1));
document.getElementById("revDayNext").addEventListener("click", () => shiftReviewDay(-1));
document.getElementById("revDayToday").addEventListener("click", () => {
  reviewViewDate = "";
  loadReview(true, "");
});
document.getElementById("revDayInput").addEventListener("change", () => {
  const v = document.getElementById("revDayInput").value;
  if (v) loadReview(true, v);
});
document.getElementById("panel-review").addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-rev-day]");
  if (!btn) return;
  const d = btn.getAttribute("data-rev-day");
  if (d) loadReview(true, d);
});
document.getElementById("maFanDayPrev")?.addEventListener("click", () => shiftMaFanDay(1));
document.getElementById("maFanDayNext")?.addEventListener("click", () => shiftMaFanDay(-1));
document.getElementById("maFanDayLatest")?.addEventListener("click", () => {
  maFanViewDate = "";
  loadMaFan(true, "");
});
document.getElementById("maFanDayInput")?.addEventListener("change", () => {
  const v = document.getElementById("maFanDayInput").value;
  if (v) loadMaFan(true, v);
});
loadMaFanFilters();
const maFanFilterApply = () => {
  readMaFanFilterInputs();
  paintMaFan(lastMaFan);
};
document.getElementById("maFanFltApply")?.addEventListener("click", maFanFilterApply);
document.getElementById("maFanFltReset")?.addEventListener("click", () => {
  maFanFilters = {
    stage: "", ma60: "", band: "", theme: "",
    review: false, progressive: false,
  };
  saveMaFanFilters();
  syncMaFanFilterInputs();
  paintMaFan(lastMaFan);
});
["maFanFltStage", "maFanFltMa60", "maFanFltBand", "maFanFltTheme"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", maFanFilterApply);
});
["maFanFltReview", "maFanFltProgressive"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", maFanFilterApply);
});
document.getElementById("maFanHead")?.addEventListener("click", (ev) => {
  const th = ev.target && ev.target.closest ? ev.target.closest("th.rev-sort") : null;
  if (!th) return;
  const col = th.getAttribute("data-sort");
  if (!col) return;
  if (maFanSort.id === col) maFanSort.dir = -maFanSort.dir;
  else {
    maFanSort.id = col;
    maFanSort.dir = -1;
  }
  paintMaFan(lastMaFan);
});
document.getElementById("panel-mafan")?.addEventListener("click", (ev) => {
  const dayBtn = ev.target.closest("[data-mafan-day]");
  if (dayBtn) {
    const d = dayBtn.getAttribute("data-mafan-day");
    if (d) loadMaFan(true, d);
  }
});
document.getElementById("maFanForceRun")?.addEventListener("click", async () => {
  if (!(authUser && authUser.role === "admin")) return;
  if (!confirm("重扫今日成交额榜前 1000 只（限速拉日线，约 3–4 分钟），会覆盖今日已有结果。继续？")) return;
  const btn = document.getElementById("maFanForceRun");
  if (btn) btn.disabled = true;
  try {
    const r = await fetch("/api/ma-fan/run?force=true", {
      method: "POST", credentials: "same-origin",
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      alert(typeof d.detail === "string" ? d.detail : `重扫启动失败（HTTP ${r.status}）`);
      if (r.status >= 500) reportClientError("mafan-rescan", d.detail || "start failed", r.status, "");
      await pollMaFanProgress();
      return;
    }
    maFanViewDate = d.view_date || "";
    if (d.progress) paintMaFanProgress(d.progress);
    await pollMaFanProgress();
  } catch (e) {
    alert("重扫请求失败：" + ((e && e.message) || e));
    reportClientError("mafan-rescan", (e && e.message) || String(e), null, "");
    if (btn) btn.disabled = false;
  }
});
document.getElementById("revColsBtn").addEventListener("click", (ev) => {
  ev.stopPropagation();
  const panel = document.getElementById("revColPanel");
  const open = panel.hidden;
  panel.hidden = !open;
  if (open) renderRevColPanel();
});
