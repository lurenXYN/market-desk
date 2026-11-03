/** Review range leaderboard (1w/2w/1m) + code/board search. */
let revRangeWin = "1w";
let revRangeSide = "worst";
let revRangeBusy = false;

function _revRangeSrcLabel(sources) {
  const map = { signal: "信号", ma_fan: "发散", pot: "锅底" };
  const arr = Array.isArray(sources) ? sources : [sources];
  return arr.map((s) => map[s] || s || "—").filter(Boolean).join("+") || "—";
}

function _revRangePct(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const cls = n >= 0 ? "up" : "down";
  return `<span class="${cls}">${(n > 0 ? "+" : "") + n.toFixed(2)}%</span>`;
}

async function loadRevRangeBoard() {
  if (revRangeBusy) return;
  revRangeBusy = true;
  const body = document.getElementById("revRangeBody");
  const meta = document.getElementById("revRangeMeta");
  if (body) body.innerHTML = `<tr><td colspan="9" class="meta">加载中…</td></tr>`;
  try {
    const q = new URLSearchParams({
      window: revRangeWin,
      side: revRangeSide,
      limit: "30",
    });
    const r = await fetch("/api/review/range-board?" + q.toString());
    const d = await r.json();
    if (!d.ok && d.ok !== undefined) throw new Error("bad");
    if (meta) {
      meta.textContent =
        `${d.window_days || "—"} 个活动日 · 已评分 ${d.scored_n ?? 0}`
        + (d.unscored_n ? ` · 无三日 ${d.unscored_n}` : "");
    }
    const rows = d.items || [];
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="9" class="meta">该窗口暂无已评分样本</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((it) => {
      const code = String(it.code || "");
      const name = String(it.name || "");
      const boards = escAttr(it.board_text || "—");
      const nowTip = it.live_last != null && it.chase_price != null
        ? `现价 ${it.live_last} / 不追 ${it.chase_price}`
        : "无现价或不追价";
      return `<tr>
        <td>${escAttr(it.trade_date || "—")}</td>
        <td><button type="button" class="rev-col-btn rev-range-code" data-code="${escAttr(code)}" data-name="${escAttr(name)}" style="padding:0 4px">${escAttr(code)}</button></td>
        <td>${escAttr(name || "—")}</td>
        <td title="${boards}">${boards}</td>
        <td>${escAttr(_revRangeSrcLabel(it.sources))}${it.appear_n > 1 ? ` ·${it.appear_n}` : ""}</td>
        <td>${_revRangePct(it.outcome_day3_pct)}</td>
        <td title="三日收相对不追">${_revRangePct(it.vs_chase_pct)}</td>
        <td title="${escAttr(nowTip)}">${_revRangePct(it.vs_chase_now_pct)}</td>
        <td><button type="button" class="rev-col-btn rev-range-hist" data-code="${escAttr(code)}" data-name="${escAttr(name)}">历史</button></td>
      </tr>`;
    }).join("");
  } catch (e) {
    if (body) body.innerHTML = `<tr><td colspan="9" class="meta">区间榜加载失败</td></tr>`;
    if (meta) meta.textContent = "";
  } finally {
    revRangeBusy = false;
  }
}

function paintRevRangeAppearances(d) {
  const box = document.getElementById("revRangeSearchOut");
  if (!box) return;
  box.hidden = false;
  const boards = (d.board_set || []).slice(0, 12).map((b) => escAttr(b)).join(" · ") || "—";
  const curBoards = (d.current_boards || []).slice(0, 12).map((b) => escAttr(b.name)).join(" · ");
  const fan = (d.ma_fan_days || []).slice(0, 8).map((x) =>
    `${escAttr(x.trade_date || "")}${x.d3 != null ? `(${Number(x.d3) >= 0 ? "+" : ""}${Number(x.d3).toFixed(1)}%)` : ""}`
  ).join(" · ") || "—";
  const pot = (d.pot_days || []).slice(0, 8).map((x) =>
    `${escAttr(x.trade_date || "")}${x.focus ? "·浅跌" : ""}`
  ).join(" · ") || "—";
  box.innerHTML =
    `<div class="hd">${escAttr(d.name || "")} · ${escAttr(d.code || "")}`
    + ` <button type="button" class="rev-col-btn rev-range-hist" data-code="${escAttr(d.code || "")}" data-name="${escAttr(d.name || "")}">打开历史</button></div>`
    + `<div class="meta">信号 ${d.signal_n ?? 0} · 发散 ${d.ma_fan_n ?? 0} · 锅底 ${d.pot_n ?? 0}</div>`
    + `<div class="meta">当时板：${boards}</div>`
    + (curBoards ? `<div class="meta" title="今日成分快照，仅供对照">现况板（非当时）：${curBoards}</div>` : "")
    + `<div class="meta">发散日：${fan}</div>`
    + `<div class="meta">锅底日：${pot}</div>`;
}

function paintRevRangeRoster(d) {
  const box = document.getElementById("revRangeSearchOut");
  if (!box) return;
  box.hidden = false;
  const rows = d.items || [];
  if (!rows.length) {
    box.innerHTML = `<div class="meta">板块「${escAttr(d.q || "")}」近窗无出现记录</div>`;
    return;
  }
  const lines = rows.slice(0, 40).map((it) => {
    const code = String(it.code || "");
    return `<tr>
      <td>${escAttr(it.trade_date || "—")}</td>
      <td><button type="button" class="rev-col-btn rev-range-code" data-code="${escAttr(code)}" data-name="${escAttr(it.name || "")}" style="padding:0 4px">${escAttr(code)}</button></td>
      <td>${escAttr(it.name || "—")}</td>
      <td>${escAttr(_revRangeSrcLabel(it.source))}</td>
      <td>${escAttr(it.board_hit || "—")}</td>
      <td>${it.price != null ? escAttr(Number(it.price).toFixed(2)) : "—"}</td>
      <td>${it.days_ago != null ? escAttr(it.days_ago + " 天前") : "—"}</td>
      <td>${_revRangePct(it.outcome_day3_pct)}</td>
      <td><button type="button" class="rev-col-btn rev-range-hist" data-code="${escAttr(code)}" data-name="${escAttr(it.name || "")}">历史</button></td>
    </tr>`;
  }).join("");
  box.innerHTML =
    `<div class="hd">板块「${escAttr(d.q || "")}」· ${d.n ?? rows.length} 条</div>`
    + `<div class="table-wrap"><table class="rev-table compact"><thead><tr>`
    + `<th>日期</th><th>代码</th><th>名称</th><th>来源</th><th>命中板</th><th>当时价</th><th>距今</th><th>三日%</th><th></th>`
    + `</tr></thead><tbody>${lines}</tbody></table></div>`;
}

function _revealRevRangeSearch() {
  const box = document.getElementById("revRangeSearchOut");
  if (!box) return;
  box.hidden = false;
  try {
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    /* ignore */
  }
}

async function runRevRangeSearch() {
  const input = document.getElementById("revRangeQ");
  const raw = String((input && input.value) || "").trim();
  const box = document.getElementById("revRangeSearchOut");
  if (!raw) {
    if (box) { box.hidden = true; box.innerHTML = ""; }
    return;
  }
  const code = raw.replace(/\D/g, "");
  if (box) {
    box.hidden = false;
    box.innerHTML = `<div class="meta">检索中…</div>`;
  }
  _revealRevRangeSearch();
  try {
    if (/^\d{6}$/.test(code) && raw.replace(/\s/g, "").length <= 8) {
      const r = await fetch("/api/review/appearances/" + code);
      const d = await r.json();
      if (!d.ok && d.ok !== undefined) throw new Error("bad");
      paintRevRangeAppearances(d);
      _revealRevRangeSearch();
      return;
    }
    const q = new URLSearchParams({ q: raw, days: "20", limit: "80" });
    const r = await fetch("/api/review/board-roster?" + q.toString());
    const d = await r.json();
    if (!d.ok && d.ok !== undefined) throw new Error("bad");
    paintRevRangeRoster(d);
    _revealRevRangeSearch();
  } catch (e) {
    if (box) box.innerHTML = `<div class="meta">检索失败</div>`;
  }
}

function bindRevRangeBoard() {
  const win = document.getElementById("revRangeWin");
  if (win) {
    win.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-win]");
      if (!btn) return;
      revRangeWin = btn.getAttribute("data-win") || "1w";
      win.querySelectorAll("[data-win]").forEach((b) => b.classList.toggle("on", b === btn));
      loadRevRangeBoard();
    });
  }
  const side = document.getElementById("revRangeSide");
  if (side) {
    side.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-side]");
      if (!btn) return;
      revRangeSide = btn.getAttribute("data-side") || "worst";
      side.querySelectorAll("[data-side]").forEach((b) => b.classList.toggle("on", b === btn));
      loadRevRangeBoard();
    });
  }
  const searchBtn = document.getElementById("revRangeSearchBtn");
  if (searchBtn) searchBtn.addEventListener("click", () => runRevRangeSearch());
  const qEl = document.getElementById("revRangeQ");
  if (qEl) {
    qEl.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        runRevRangeSearch();
      }
    });
  }
  const root = document.getElementById("revRange");
  if (root) {
    const sum = root.querySelector(":scope > summary");
    if (sum) {
      sum.addEventListener("click", (ev) => {
        if (ev.target.closest("button.q")) ev.preventDefault();
      });
    }
    root.addEventListener("click", (ev) => {
      const hist = ev.target.closest(".rev-range-hist, .rev-range-code");
      if (!hist) return;
      const code = hist.getAttribute("data-code");
      const name = hist.getAttribute("data-name") || "";
      if (code && typeof openSigHist === "function") openSigHist(code, name);
    });
    // Default collapsed: load only when first opened (or reopen with empty body).
    root.addEventListener("toggle", () => {
      if (!root.open || typeof loadRevRangeBoard !== "function") return;
      const body = document.getElementById("revRangeBody");
      const hasRows = !!(body && body.querySelector("tr .rev-range-code, tr .rev-range-hist"));
      if (!hasRows) loadRevRangeBoard();
    });
  }
}

bindRevRangeBoard();
