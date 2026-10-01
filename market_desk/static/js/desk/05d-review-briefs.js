let lastReview = { signals: [], summary: {} };
let reviewViewDate = "";
let reviewVsMlMode = "live";
let reviewOcMode = "classic";
window.__eodMarkdown = "";
window.__tomorrowMarkdown = "";

async function paintTomorrowBrief() {
  const focusEl = document.getElementById("tomorrowFocus");
  const listEl = document.getElementById("tomorrowBullets");
  if (!focusEl || !listEl) return;
  const day = reviewViewDate || ((lastReview.summary || {}).view_date) || "";
  const q = day ? (`?date=${encodeURIComponent(day)}`) : "";
  try {
    const r = await fetch("/api/report/tomorrow" + q);
    const d = await r.json();
    const brief = (d && d.brief) || {};
    focusEl.textContent = brief.focus || "收盘后自动生成次日观察清单";
    const bullets = brief.bullets || [];
    listEl.innerHTML = bullets.length
      ? bullets.map((b) => `<li>${escAttr(String(b))}</li>`).join("")
      : `<li class="meta">暂无要点</li>`;
    window.__tomorrowMarkdown = brief.markdown || (d && d.markdown) || "";
  } catch (e) {
    focusEl.textContent = "明日看点加载失败";
    listEl.innerHTML = `<li class="meta">${escAttr(String(e && e.message || e))}</li>`;
  }
}

async function paintEodBrief(_sum, _today, _dayLabel) {
  const focusEl = document.getElementById("eodFocus");
  const listEl = document.getElementById("eodBullets");
  if (!focusEl || !listEl) return;
  const day = reviewViewDate || ((lastReview.summary || {}).view_date) || "";
  const q = day ? (`?date=${encodeURIComponent(day)}`) : "";
  try {
    const r = await fetch("/api/report/eod" + q);
    const d = await r.json();
    const brief = (d && d.brief) || {};
    focusEl.textContent = brief.focus || "收盘对照：盈亏/持仓与明日看点一张纸看完即可。";
    const bullets = brief.bullets || [];
    listEl.innerHTML = bullets.length
      ? bullets.map((b) => `<li>${escAttr(String(b))}</li>`).join("")
      : `<li class="meta">暂无要点</li>`;
    window.__eodMarkdown = brief.markdown || (d && d.markdown) || "";
  } catch (e) {
    focusEl.textContent = "收盘一页纸加载失败";
    listEl.innerHTML = `<li class="meta">${escAttr(String(e && e.message || e))}</li>`;
  }
}

function isTodayView() {
  const sum = (lastReview && lastReview.summary) || {};
  const view = reviewViewDate || sum.view_date || "";
  const cal = sum.calendar_today || "";
  return !!(view && cal && view === cal);
}

function applyReviewTrendsByCode(by) {
  const src = by || {};
  for (const row of (lastReview.signals || [])) {
    const code = String(row.code || "").padStart(6, "0");
    const hit = src[code] || src[String(row.code || "")];
    if (!hit) {
      row.daily_trend = "";
      row.trend_ok = false;
      row.trend_down = false;
      row.trend_pending = true;
      continue;
    }
    row.daily_trend = hit.label || hit.trend || "";
    row.trend_ok = !!(hit.up || hit.trend_ok);
    row.trend_down = !!(hit.down || hit.trend_down);
    row.trend_pending = hit.quality === "fetch_fail" || hit.quality === "thin" || !!hit.trend_pending;
    if (hit.ma5 != null) row.ma5 = hit.ma5;
    if (hit.ma20 != null) row.ma20 = hit.ma20;
  }
  if (lastReview) lastReview.trends_ready = true;
}

async function loadReviewTrends(date, seq, wantFp) {
  const want = String(date || "").trim();
  if (!want) return;
  if (wantFp && reviewTrendFp === wantFp && reviewTrendBy) {
    applyReviewTrendsByCode(reviewTrendBy);
    setModStamp("revTrendStamp", reviewTrendRefreshedAt || "缓存", {
      label: "日线趋势",
      title: "日线趋势命中本地缓存（同日且信号集未变）",
    });
    paintReview(lastReview);
    return;
  }
  setModStamp("revTrendStamp", null, { loading: true, label: "日线趋势" });
  try {
    const r = await fetch("/api/review/trends?date=" + encodeURIComponent(want));
    const d = await r.json();
    if (seq !== reviewTrendSeq) return;
    const view = lastReview.view_date
      || (lastReview.summary || {}).view_date
      || reviewViewDate
      || "";
    if (view && d.trade_date && view !== d.trade_date) return;
    reviewTrendBy = d.by_code || {};
    reviewTrendFp = d.fingerprint || wantFp || "";
    applyReviewTrendsByCode(reviewTrendBy);
    reviewTrendRefreshedAt = d.refreshed_at || new Date().toTimeString().slice(0, 8);
    setModStamp("revTrendStamp", reviewTrendRefreshedAt, {
      label: "日线趋势",
      title: d.cache_hit ? "日线趋势命中服务端缓存" : "日线趋势已补齐",
    });
    paintReview(lastReview);
  } catch (e) {
    if (seq !== reviewTrendSeq) return;
    setModStamp("revTrendStamp", reviewTrendRefreshedAt || null, {
      label: "日线趋势",
      stale: true,
      title: "日线趋势补齐失败",
    });
    if (lastReview && (lastReview.signals || []).length) {
      paintReview(lastReview);
    }
  }
}

async function loadReviewZtYtd(date, seq) {
  const want = String(date || "").trim();
  if (!want) {
    if (seq === reviewZtSeq) reviewZtLoading = false;
    return;
  }
  setModStamp("revZtStamp", null, { loading: true, label: "年内涨停" });
  try {
    const r = await fetch("/api/review/zt-ytd?date=" + encodeURIComponent(want));
    const d = await r.json();
    if (seq !== reviewZtSeq) return;
    const view = lastReview.view_date
      || (lastReview.summary || {}).view_date
      || reviewViewDate
      || "";
    if (view && d.trade_date && view !== d.trade_date) return;
    const by = d.by_code || {};
    const rows = lastReview.signals || [];
    for (const row of rows) {
      if (row.kind === "etf") {
        row.zt_ytd = null;
        continue;
      }
      const code = String(row.code || "");
      const hit = by[code.padStart(6, "0")] || by[code];
      if (!hit) {
        row.zt_ytd = null;
        continue;
      }
      row.zt_ytd = hit.count;
      row.zt_ytd_year = hit.year;
      row.zt_ytd_note = hit.note;
    }
    reviewZtLoading = false;
    reviewZtRefreshedAt = d.refreshed_at || new Date().toTimeString().slice(0, 8);
    setModStamp("revZtStamp", reviewZtRefreshedAt, {
      label: "年内涨停",
      title: "年内涨停列异步补齐完成",
    });
    paintReview(lastReview);
  } catch (e) {
    if (seq !== reviewZtSeq) return;
    reviewZtLoading = false;
    setModStamp("revZtStamp", reviewZtRefreshedAt || null, {
      label: "年内涨停",
      stale: true,
      title: "年内涨停补齐失败",
    });
    if (lastReview && (lastReview.signals || []).length) {
      paintReview(lastReview);
    }
  }
}
