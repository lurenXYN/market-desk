/** Flash order-copy text: one-tap clipboard lines for desk buy/sell cards. */

function flashFullBudget() {
  const s = window.__lastSettings || {};
  const n = Number(s.full_position_cny);
  return Number.isFinite(n) && n >= 1000 ? n : 10000;
}

function flashFmtPx(px, kind) {
  const n = Number(px);
  if (!Number.isFinite(n) || n <= 0) return "";
  return n.toFixed(kind === "etf" ? 3 : 2);
}

/**
 * Round a notional budget down to a whole board lot (100 shares).
 * @returns {{qty:number, note:string}}
 */
function flashLotQty(price, budgetCny) {
  const px = Number(price);
  const budget = Number(budgetCny);
  if (!(px > 0) || !(budget > 0)) {
    return { qty: 0, note: "缺价格或满仓金额" };
  }
  const qty = Math.floor(budget / px / 100) * 100;
  if (qty < 100) {
    const need = Math.ceil(px * 100);
    return { qty: 0, note: `不足一手（约需 ≥${need} 元）` };
  }
  return { qty, note: "" };
}

function flashBuySizeMode(it) {
  if (!it) return "half";
  if (it.fly_warn || it.ready_relaxed || it.probe_ok || it.absorb_unconfirmed) return "half";
  if (it.wait || !it.ready) return "half";
  return "full";
}

function flashBuyText(it) {
  const code = String(it.code || "").trim();
  const name = String(it.name || "").trim() || code;
  const kind = it.kind || "stock";
  const buy = it.buy_price != null ? it.buy_price : it.last;
  const stop = it.stop_price;
  const px = flashFmtPx(buy, kind);
  const stopPx = flashFmtPx(stop, kind);
  if (!code || !px) return { text: "", tip: "缺代码或建议价" };
  const mode = flashBuySizeMode(it);
  const budget = flashFullBudget() * (mode === "full" ? 1 : 0.5);
  const { qty, note } = flashLotQty(buy, budget);
  const tag = mode === "full" ? "满" : "半";
  if (!qty) {
    return {
      text: `买入 ${code} ${name} ${px} × 不足一手（${tag}）` + (stopPx ? ` 止损 ${stopPx}` : ""),
      tip: note || "不足一手",
    };
  }
  const text = `买入 ${code} ${name} ${px} × ${qty}股（${tag}）`
    + (stopPx ? ` 止损 ${stopPx}` : "");
  return { text, tip: `${tag}仓 · 满仓额 ${flashFullBudget()} 元` };
}

function flashSellText(it) {
  const code = String(it.code || "").trim();
  const name = String(it.name || "").trim() || code;
  const kind = it.kind || "stock";
  if (!code) return { text: "", tip: "缺代码" };
  const role = String(it.role_label || "");
  const useStop = /止损|清仓|必卖/.test(role)
    || (String(it.exit_mode || "") === "clear" && Number(it.pnl_pct) < 0);
  const pxRaw = useStop
    ? (it.stop_price != null ? it.stop_price : it.sell_price)
    : (it.sell_price != null ? it.sell_price : it.last);
  const px = flashFmtPx(pxRaw, kind);
  if (!px) return { text: "", tip: "缺卖价" };
  const hold = Number(it.qty || 0);
  const mode = it.exit_mode || "hold";
  let qty = 0;
  let tag = "半";
  if (mode === "clear" || hold <= 100) {
    qty = hold > 0 ? hold : Number(it.sell_qty_clear || 0);
    tag = "清";
  } else {
    qty = it.sell_qty_half != null ? Number(it.sell_qty_half) : halfSellQtyClient(hold);
    tag = "半";
  }
  if (!(qty > 0)) return { text: "", tip: "无持仓股数" };
  const stopPx = flashFmtPx(it.stop_price, kind);
  let text = `卖出 ${code} ${name} ${px} × ${qty}股（${tag}）`;
  if (useStop) text += "·止损价";
  else if (stopPx && stopPx !== px) text += ` 止损 ${stopPx}`;
  return { text, tip: useStop ? "按止损价" : "按建议卖" };
}

async function copyFlashText(text, btn) {
  if (!text) return;
  const label = btn && (btn.textContent || "").trim();
  try {
    await navigator.clipboard.writeText(text);
    if (btn) {
      btn.textContent = "已复制";
      setTimeout(() => { if (btn) btn.textContent = label || "复制"; }, 1200);
    }
  } catch (_) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      if (btn) {
        btn.textContent = "已复制";
        setTimeout(() => { if (btn) btn.textContent = label || "复制"; }, 1200);
      }
    } catch (e2) {
      prompt("复制失败，请手动全选复制：", text);
    }
  }
}

async function ensureFlashSettings() {
  if (window.__lastSettings && window.__lastSettings.full_position_cny != null) {
    return window.__lastSettings;
  }
  try {
    const r = await fetch("/api/settings", { credentials: "same-origin" });
    const d = await r.json();
    if (d && d.settings) window.__lastSettings = d.settings;
  } catch (e) {}
  return window.__lastSettings || {};
}

function flashItemFromBuyBtn(btn) {
  return {
    code: btn.dataset.code || "",
    name: btn.dataset.name || "",
    kind: btn.dataset.kind || "stock",
    buy_price: Number(btn.dataset.price) || null,
    stop_price: Number(btn.dataset.stop) || null,
    ready: btn.dataset.ready === "1",
    wait: btn.dataset.ready !== "1",
    fly_warn: btn.dataset.fly === "1",
    ready_relaxed: btn.dataset.relaxed === "1",
    probe_ok: btn.dataset.probe === "1",
    absorb_unconfirmed: btn.dataset.absorb === "1",
  };
}

function flashItemFromSellBtn(btn) {
  return {
    code: btn.dataset.code || "",
    name: btn.dataset.name || "",
    kind: btn.dataset.kind || "stock",
    qty: Number(btn.dataset.qty) || 0,
    sell_price: Number(btn.dataset.sell) || null,
    stop_price: Number(btn.dataset.stop) || null,
    last: Number(btn.dataset.last) || null,
    exit_mode: btn.dataset.exit || "hold",
    role_label: btn.dataset.role || "",
    pnl_pct: Number(btn.dataset.pnl),
    sell_qty_half: Number(btn.dataset.half) || null,
    sell_qty_clear: Number(btn.dataset.clear) || null,
  };
}

function flashBuyBtnHtml(it) {
  const { text, tip } = flashBuyText(it);
  if (!text) return "";
  const buy = it.buy_price != null ? it.buy_price : it.last;
  return `<button type="button" class="pos-btn rec-flash"
            data-flash="buy"
            data-code="${escAttr(it.code || "")}"
            data-name="${escAttr(it.name || "")}"
            data-kind="${escAttr(it.kind || "stock")}"
            data-price="${buy ?? ""}"
            data-stop="${it.stop_price ?? ""}"
            data-ready="${it.ready ? "1" : "0"}"
            data-fly="${it.fly_warn ? "1" : "0"}"
            data-relaxed="${it.ready_relaxed ? "1" : "0"}"
            data-probe="${it.probe_ok ? "1" : "0"}"
            data-absorb="${it.absorb_unconfirmed ? "1" : "0"}"
            title="${escAttr(tip + " · " + text)}">复制</button>`;
}

function flashSellBtnHtml(it) {
  const { text, tip } = flashSellText(it);
  if (!text) return "";
  return `<button type="button" class="pos-btn rec-flash"
            data-flash="sell"
            data-code="${escAttr(it.code || "")}"
            data-name="${escAttr(it.name || "")}"
            data-kind="${escAttr(it.kind || "stock")}"
            data-qty="${it.qty ?? ""}"
            data-sell="${it.sell_price ?? it.last ?? ""}"
            data-stop="${it.stop_price ?? ""}"
            data-last="${it.last ?? ""}"
            data-exit="${escAttr(it.exit_mode || "hold")}"
            data-role="${escAttr(it.role_label || "")}"
            data-pnl="${it.pnl_pct ?? ""}"
            data-half="${it.sell_qty_half ?? ""}"
            data-clear="${it.sell_qty_clear ?? ""}"
            title="${escAttr(tip + " · " + text)}">复制</button>`;
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest && ev.target.closest(".rec-flash");
  if (!btn) return;
  ev.preventDefault();
  await ensureFlashSettings();
  const kind = btn.getAttribute("data-flash");
  const built = kind === "sell"
    ? flashSellText(flashItemFromSellBtn(btn))
    : flashBuyText(flashItemFromBuyBtn(btn));
  await copyFlashText(built.text || "", btn);
});

(async function bootFlashSettings() {
  await ensureFlashSettings();
})();
