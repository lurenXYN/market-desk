/** Review range leaderboard (1w/2w/1m) + code/board search + board agg. */
let revRangeWin = "1w";
let revRangeSide = "worst";
let revRangeView = "code"; // code | board
let revRangeKind = "industry"; // industry | concept | all (board view only)
let revRangeSrc = "all"; // all | signal | ma_fan | pot
let revRangeBusy = false;
const REV_RANGE_BOARD_COLS = 10;
const REV_RANGE_SRC_ZH = { all: "全部", signal: "复盘", ma_fan: "发散", pot: "锅底" };

function _syncRevRangeKindBar() {
  const bar = document.getElementById("revRangeKind");
  if (bar) bar.hidden = revRangeView !== "board";
}

function _revRangeSrcLabel(sources) {
  const map = { signal: "复盘", ma_fan: "发散", pot: "锅底" };
  const arr = Array.isArray(sources) ? sources : [sources];
  return arr.map((s) => map[s] || s || "—").filter(Boolean).join("+") || "—";
}

function _revRangePct(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const cls = n >= 0 ? "up" : "down";
  return `<span class="${cls}">${(n > 0 ? "+" : "") + n.toFixed(2)}%</span>`;
}

function _paintRevRangeCodeHead() {
  const head = document.getElementById("revRangeHead");
  if (!head) return;
  head.innerHTML = `<tr>
    <th>日期</th><th>代码</th><th>名称</th><th>板块</th><th>来源</th>
    <th>三日%</th>
    <th title="三日收相对冻结不追价">相对不追·三日</th>
    <th title="现价相对冻结不追价，同复盘表不追价下「偏离」">相对不追·现价</th>
    <th></th>
  </tr>`;
}

function _paintRevRangeBoardHead() {
  const head = document.getElementById("revRangeHead");
  if (!head) return;
  head.innerHTML = `<tr>
    <th>板块</th>
    <th>类型</th>
    <th title="窗内已评分的不同票数；括号内为 1/k 分摊后的有效票数">票数</th>
    <th title="每票在所选范围内按 1/k 分摊权重后的加权均值">均三日%</th>
    <th title="不加权均值，对照用">不加权%</th>
    <th>中位%</th>
    <th title="三日%&gt;0 占比">红盘率</th>
    <th>最强票</th>
    <th>最惨票</th>
    <th></th>
  </tr>`;
}

async function loadRevRangeBoard() {
  if (revRangeBusy) return;
  revRangeBusy = true;
  const body = document.getElementById("revRangeBody");
  const meta = document.getElementById("revRangeMeta");
  const cols = revRangeView === "board" ? REV_RANGE_BOARD_COLS : 9;
  _syncRevRangeKindBar();
  if (body) body.innerHTML = `<tr><td colspan="${cols}" class="meta">加载中…</td></tr>`;
  try {
    if (revRangeView === "board") {
      _paintRevRangeBoardHead();
      const q = new URLSearchParams({
        window: revRangeWin,
        side: revRangeSide,
        limit: "40",
        kind: revRangeKind,
        source: revRangeSrc,
      });
      const r = await fetch("/api/review/range-board-agg?" + q.toString());
      const d = await r.json();
      if (!d.ok && d.ok !== undefined) throw new Error("bad");
      if (meta) {
        const srcLab = REV_RANGE_SRC_ZH[d.source || revRangeSrc] || "全部";
        meta.textContent =
          `${d.window_days || "—"} 个活动日 · ${srcLab}`
          + ` · 已评分 ${d.scored_n ?? 0}`
          + ` · 板块 ${d.board_n ?? 0}`
          + (d.thin_skipped ? ` · 剔薄板 ${d.thin_skipped}` : "")
          + (d.no_tag_n ? ` · 无此类标签 ${d.no_tag_n}` : "")
          + (d.mainline ? ` · 今日主线 ${d.mainline}` : "");
      }
      const rows = d.items || [];
      if (!body) return;
      if (!rows.length) {
        body.innerHTML = `<tr><td colspan="${REV_RANGE_BOARD_COLS}" class="meta">该窗口暂无足够票数的板块（至少 ${d.min_n ?? 2} 只）；可换 2 周/1 月</td></tr>`;
        return;
      }
      body.innerHTML = rows.map((it) => {
        const board = String(it.board || "");
        const bestCode = String(it.best_code || "");
        const worstCode = String(it.worst_code || "");
        const bestLab = `${escAttr(it.best_name || bestCode)} ${_revRangePct(it.best_day3)}`;
        const worstLab = `${escAttr(it.worst_name || worstCode)} ${_revRangePct(it.worst_day3)}`;
        return `<tr>
          <td><button type="button" class="rev-col-btn rev-range-board" data-board="${escAttr(board)}" title="点开板内名单">${escAttr(board)}</button>${it.is_mainline ? ` <span class="meta" title="与今日主线重合，仅标记">主线</span>` : ""}</td>
          <td>${escAttr(it.kind_zh || "—")}</td>
          <td>${escAttr(String(it.n ?? "—"))}${it.n_eff != null && Number(it.n_eff) !== Number(it.n) ? ` <span class="meta">(${escAttr(String(it.n_eff))})</span>` : ""}</td>
          <td>${_revRangePct(it.avg_day3)}</td>
          <td>${_revRangePct(it.avg_raw)}</td>
          <td>${_revRangePct(it.median_day3)}</td>
          <td>${it.hit_rate != null ? escAttr(String(it.hit_rate)) + "%" : "—"}</td>
          <td><button type="button" class="rev-col-btn rev-range-code" data-code="${escAttr(bestCode)}" data-name="${escAttr(it.best_name || "")}">${bestLab}</button></td>
          <td><button type="button" class="rev-col-btn rev-range-code" data-code="${escAttr(worstCode)}" data-name="${escAttr(it.worst_name || "")}">${worstLab}</button></td>
          <td><button type="button" class="rev-col-btn rev-range-board" data-board="${escAttr(board)}">名单</button></td>
        </tr>`;
      }).join("");
      return;
    }

    _paintRevRangeCodeHead();
    const q = new URLSearchParams({
      window: revRangeWin,
      side: revRangeSide,
      limit: "30",
      source: revRangeSrc,
    });
    const r = await fetch("/api/review/range-board?" + q.toString());
    const d = await r.json();
    if (!d.ok && d.ok !== undefined) throw new Error("bad");
    if (meta) {
      const srcLab = REV_RANGE_SRC_ZH[d.source || revRangeSrc] || "全部";
      meta.textContent =
        `${d.window_days || "—"} 个活动日 · ${srcLab}`
        + ` · 已评分 ${d.scored_n ?? 0}`
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
        <td><button type="button" class="rev-col-btn rev-range-hist js-sig-hist" data-code="${escAttr(code)}" data-name="${escAttr(name)}">明细</button></td>
      </tr>`;
    }).join("");
  } catch (e) {
    if (body) {
      body.innerHTML = `<tr><td colspan="${revRangeView === "board" ? REV_RANGE_BOARD_COLS : 9}" class="meta">区间榜加载失败</td></tr>`;
    }
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
    + ` <button type="button" class="rev-col-btn rev-range-hist js-sig-hist" data-code="${escAttr(d.code || "")}" data-name="${escAttr(d.name || "")}">打开明细</button></div>`
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
      <td><button type="button" class="rev-col-btn rev-range-hist js-sig-hist" data-code="${escAttr(code)}" data-name="${escAttr(it.name || "")}">明细</button></td>
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

async function runRevRangeSearch(forcedQ) {
  const input = document.getElementById("revRangeQ");
  const raw = String(forcedQ != null ? forcedQ : ((input && input.value) || "")).trim();
  const box = document.getElementById("revRangeSearchOut");
  if (!raw) {
    if (box) { box.hidden = true; box.innerHTML = ""; }
    return;
  }
  if (input && forcedQ != null) input.value = raw;
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
  const view = document.getElementById("revRangeView");
  if (view) {
    view.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-view]");
      if (!btn) return;
      revRangeView = btn.getAttribute("data-view") || "code";
      view.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b === btn));
      loadRevRangeBoard();
    });
  }
  const kindBar = document.getElementById("revRangeKind");
  if (kindBar) {
    kindBar.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-kind]");
      if (!btn) return;
      revRangeKind = btn.getAttribute("data-kind") || "industry";
      kindBar.querySelectorAll("[data-kind]").forEach((b) => b.classList.toggle("on", b === btn));
      loadRevRangeBoard();
    });
  }
  const srcBar = document.getElementById("revRangeSrc");
  if (srcBar) {
    srcBar.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-src]");
      if (!btn) return;
      revRangeSrc = btn.getAttribute("data-src") || "all";
      srcBar.querySelectorAll("[data-src]").forEach((b) => b.classList.toggle("on", b === btn));
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
      const boardBtn = ev.target.closest(".rev-range-board");
      if (boardBtn) {
        const board = boardBtn.getAttribute("data-board") || "";
        if (board) runRevRangeSearch(board);
        return;
      }
      // Code / 明细 clicks are handled by the shared document listener in 12-desk-actions.
    });
    // Default collapsed: load only when first opened (or reopen with empty body).
    root.addEventListener("toggle", () => {
      if (!root.open || typeof loadRevRangeBoard !== "function") return;
      const body = document.getElementById("revRangeBody");
      const hasRows = !!(body && body.querySelector("tr .rev-range-code, tr .rev-range-hist, tr .rev-range-board"));
      if (!hasRows) loadRevRangeBoard();
    });
  }
}

bindRevRangeBoard();
