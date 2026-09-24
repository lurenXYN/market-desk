/**
 * Review / chart-drawer helpers extracted from the main SPA.
 * Exposes window.MDReview for index.html to reuse.
 */
(function (global) {
  "use strict";

  const FILTER_STORAGE = "md_rev_filters_v1";

  function escAttr(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/'/g, "&#39;");
  }

  function normSrc(r) {
    const payload = (r && r.payload && typeof r.payload === "object") ? r.payload : {};
    let src = String((r && r.desk_source) || payload.desk_source || "").trim().toLowerCase();
    if (src === "emotion_dragon" || src === "mid_army_dragon") src = "dragon";
    if (!src) {
      const st = String((r && r.signal_type) || "");
      if (st === "buy_side") src = "side";
      else if (st === "buy_link") src = "link";
      else if (st === "buy_trial") src = "watch_trial";
      else if (st === "buy_indep") src = "independent_pop";
      else if (st === "buy_dragon") src = "dragon";
      else if (st === "sell") src = "sell";
      else src = "main";
    }
    return src;
  }

  function srcTagMeta(src) {
    const table = {
      dragon: {
        chip: "龙头",
        title: "龙头排 · 情绪龙/中军龙",
        cls: "src-dragon",
        typ: "rev-type-dragon",
      },
      side: {
        chip: "支线回踩",
        title: "观察支线 · 板块回踩副卡",
        cls: "src-side",
        typ: "rev-type-side",
      },
      link: {
        chip: "联动回踩",
        title: "板块联动 · 相似板回踩副卡",
        cls: "src-link",
        typ: "rev-type-link",
      },
      independent_pop: {
        chip: "独立人气",
        title: "独立人气回踩 · 主线板内独立行情",
        cls: "src-indep",
        typ: "rev-type-indep",
      },
      watch_trial: {
        chip: "自选试探",
        title: "自选可试探副卡",
        cls: "src-trial",
        typ: "",
      },
    };
    return table[src] || null;
  }

  function trendChipsHtml(r) {
    const chips = [];
    if (r.trend_ok) {
      chips.push(`<span class="rev-chip up" title="日线上升">上升</span>`);
    } else if (r.trend_down) {
      chips.push(`<span class="rev-chip down" title="日线下降">下降</span>`);
    } else if (r.trend_pending) {
      const tip = r.daily_trend || "日线样本不足或未取到";
      chips.push(`<span class="rev-chip" title="${escAttr(tip)}">待验</span>`);
    } else if (r.daily_trend) {
      chips.push(`<span class="rev-chip" title="${escAttr(r.daily_trend)}">震荡</span>`);
    }
    if (r.ma_fan) {
      const tip = r.ma_fan_note || "均线粘连后向上发散（观察层）";
      const stage = r.ma_fan_stage ? `·${r.ma_fan_stage}` : "";
      chips.push(`<span class="rev-chip up" title="${escAttr(tip)}">均线发散${stage}</span>`);
      const extra = Array.isArray(r.ma_fan_tags) ? r.ma_fan_tags : [];
      for (const t of extra.slice(0, 4)) {
        if (!t || t === r.ma_fan_stage) continue;
        chips.push(`<span class="rev-chip" title="${escAttr(tip)}">${escAttr(String(t))}</span>`);
      }
    }
    return chips.length ? ` <span class="rev-chips">${chips.join("")}</span>` : "";
  }

  function deskItemAsReviewShape(it) {
    if (!it) return null;
    return {
      role_label: it.role_label,
      reason: it.reason,
      minute: it.minute,
      confirm_fail: it.confirm_fail,
      confirm_soft: it.confirm_soft,
      trend_ok: it.trend_ok,
      trend_down: it.trend_down,
      trend_pending: it.trend_pending,
      daily_trend: it.trend,
      desk_source: it.desk_source,
      dragon_row: it.dragon_row,
      independent_pop: it.independent_pop,
      signal_type: it.dragon_row
        ? "buy_dragon"
        : (it.independent_pop ? "buy_indep" : null),
      price: it.buy_price,
      wait_price: it.wait_price,
      chase_price: it.chase_price,
      stop_price: it.stop_price,
      pct: it.pct,
      payload: {
        reason: it.reason,
        confirm_fail: it.confirm_fail,
        confirm_soft: it.confirm_soft,
        minute: it.minute,
        role_label: it.role_label,
        desk_source: it.desk_source,
      },
    };
  }

  function signalDescRows(r) {
    if (!r) return [];
    const payload = (r.payload && typeof r.payload === "object") ? r.payload : {};
    const rows = [];
    if (r.price != null) rows.push({ k: "建议价", v: String(r.price) });
    if (r.wait_price != null) rows.push({ k: "回踩", v: String(r.wait_price) });
    if (r.chase_price != null) rows.push({ k: "不追", v: String(r.chase_price) });
    if (r.stop_price != null) rows.push({ k: "止损", v: String(r.stop_price) });
    const srcMeta = srcTagMeta(normSrc(r));
    const role = String(r.role_label || payload.role_label || "").trim();
    if (srcMeta || role) {
      let v = srcMeta ? srcMeta.chip : "";
      if (role) v = v ? `${v} · ${role}` : role;
      rows.push({ k: "来源", v });
    }
    const vs = r.vs_mainline;
    if (vs && vs !== "—") {
      let v = vs;
      if (r.vs_mainline_of) v += `（对照「${r.vs_mainline_of}」）`;
      rows.push({
        k: "对照主线",
        v,
        cls: r.board_match === true ? "up" : (r.board_match === false ? "down" : ""),
      });
    }
    if (r.trend_ok) {
      rows.push({ k: "日线", v: "上升趋势" + (r.daily_trend ? ` · ${r.daily_trend}` : "") });
    } else if (r.trend_down) {
      rows.push({ k: "日线", v: "下降趋势" + (r.daily_trend ? ` · ${r.daily_trend}` : "") });
    } else if (r.trend_pending) {
      rows.push({ k: "日线", v: r.daily_trend || "样本不足或未取到" });
    } else if (r.daily_trend) {
      rows.push({ k: "日线", v: r.daily_trend });
    }
    const minute = (r.minute && typeof r.minute === "object")
      ? r.minute
      : ((payload.minute && typeof payload.minute === "object") ? payload.minute : {});
    const fails = [].concat(r.confirm_fail || payload.confirm_fail || []).filter(Boolean);
    const softs = [].concat(r.confirm_soft || payload.confirm_soft || []).filter(Boolean);
    let minTxt = "";
    if (minute.label) {
      minTxt = minute.label;
    } else if (minute.ok === true) {
      minTxt = "分时已过";
    } else if (minute.ok === false) {
      minTxt = "分时未过";
    } else {
      const hitFail = fails.find((f) => String(f).startsWith("分时"));
      const hitSoft = softs.find((f) => String(f).startsWith("分时"));
      minTxt = hitFail ? String(hitFail) : (hitSoft ? String(hitSoft) : "");
    }
    if (minTxt) rows.push({ k: "分时", v: minTxt });
    const why = String(r.reason || payload.reason || "").trim();
    if (why) rows.push({ k: "原因", v: why });
    const gates = fails.filter((f) => !String(f).startsWith("分时"))
      .concat(softs.filter((f) => !String(f).startsWith("分时")));
    if (gates.length) rows.push({ k: "闸门", v: gates.join(" · ") });
    if (r.price_mark) rows.push({ k: "价带", v: String(r.price_mark) });
    if (r.buy_caution) rows.push({ k: "当日", v: String(r.buy_caution) });
    return rows;
  }

  function signalDescHtml(r) {
    const rows = signalDescRows(r);
    if (!rows.length) return "";
    return "<dl>"
      + rows.map(({ k, v, cls }) =>
        `<dt>${escAttr(k)}</dt><dd class="${cls || ""}">${escAttr(v)}</dd>`
      ).join("")
      + "</dl>";
  }

  function saveFilters() {
    try {
      const payload = {
        type: (document.getElementById("revType") || {}).value || "",
        kind: (document.getElementById("revKind") || {}).value || "",
        source: (document.getElementById("revSource") || {}).value || "",
        main: ((document.getElementById("revMain") || {}).value || "").trim(),
        hideWait: !!(document.getElementById("revHideWait") || {}).checked,
      };
      localStorage.setItem(FILTER_STORAGE, JSON.stringify(payload));
    } catch (e) { /* ignore quota / private mode */ }
  }

  function loadFilters() {
    try {
      const raw = JSON.parse(localStorage.getItem(FILTER_STORAGE) || "null");
      if (!raw || typeof raw !== "object") return;
      const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (!el || v == null) return;
        if (el.type === "checkbox") el.checked = !!v;
        else el.value = String(v);
      };
      setVal("revType", raw.type);
      setVal("revKind", raw.kind);
      setVal("revSource", raw.source);
      setVal("revMain", raw.main || "");
      if (typeof raw.hideWait === "boolean") {
        setVal("revHideWait", raw.hideWait);
      }
    } catch (e) { /* ignore */ }
  }

  global.MDReview = {
    FILTER_STORAGE,
    escAttr,
    normSrc,
    srcTagMeta,
    trendChipsHtml,
    deskItemAsReviewShape,
    signalDescRows,
    signalDescHtml,
    saveFilters,
    loadFilters,
  };
})(typeof window !== "undefined" ? window : globalThis);
