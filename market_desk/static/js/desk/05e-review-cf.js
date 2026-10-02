/* What-If gate net-value audit panel (lazy: loads when the panel is opened). */
const cfAuditCache = {};

/**
 * Format a signed percent, or an em dash for null.
 * @param {number|null} v - Percent value.
 * @returns {string} Display text.
 */
function cfPct(v) {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
}

/**
 * Render the counterfactual audit payload into #revCfBody.
 * @param {object} d - Response of /api/review/counterfactual.
 */
function paintCfAudit(d) {
  const body = document.getElementById("revCfBody");
  const head = document.getElementById("revCfHead");
  if (!body) return;
  if (!d || !d.ok) {
    body.innerHTML = `<div class="meta">${escAttr((d && d.note) || "审计暂不可用")}</div>`;
    if (head) head.textContent = "不可用";
    return;
  }
  const s = d.summary || {};
  if (head) head.textContent = `${s.first_day || ""} ~ ${s.last_day || ""} · ${d.built_at || ""}`;
  const tiers = s.tiers || {};
  const tmeans = s.tier_means || {};
  const tierTxt = Object.keys(tiers)
    .map((t) => `${t} ${tiers[t]}（均 ${cfPct(tmeans[t])}）`)
    .join(" · ");
  const rel = s.released || {};
  const blk = s.blocked || {};
  const allo = s.all_open || {};
  const sideRow = (label, x, tip) =>
    `<tr title="${escAttr(tip)}"><td>${label}</td><td class="num">${x.n || 0}</td>`
    + `<td class="num">${x.win == null ? "—" : x.win + "%"}</td><td class="num">${cfPct(x.mean)}</td></tr>`;
  const sides =
    `<table class="gl-tbl cf-sides"><thead><tr><th>到价买卡</th><th>张数</th><th>胜率</th><th>三日净值</th></tr></thead><tbody>`
    + sideRow("实际放行（亮灯/试探）", rel, "闸门放行后按计划价成交的模拟结果")
    + sideRow("被拦（若放行）", blk, "到价但闸门未放行；按计划价模拟成交")
    + sideRow("全部放行（无闸门）", allo, "假设所有到价买卡都成交")
    + `</tbody></table>`;
  const gates = d.gates || [];
  const rows = gates.map((g) => {
    const ci = g.ci_lo == null ? "—" : `${cfPct(g.ci_lo)} ~ ${cfPct(g.ci_hi)}`;
    const miss = (g.top_miss || []).map((m) => `${m.name} ${m.day.slice(5)} ${cfPct(m.r)}`).join("；");
    const tip = [
      `类别：${g.kind}`,
      `有效样本 ${g.eff_n}（多闸门同拦按 1/k 分摊）`,
      `独拦 ${g.sole_n} 张 · 独拦净省 ${cfPct(g.sole_net)}`,
      `对同日放行 ${cfPct(g.vs_released)}（${g.vs_released_n} 张有对照；正=被拦的比放行的还好）`,
      `前后半段：${g.stable}`,
      `强证据占比 ${Math.round((g.strong_share || 0) * 100)}% · 实测占比 ${Math.round((g.exact_share || 0) * 100)}%`,
      miss ? `误杀样例：${miss}` : "",
    ].filter(Boolean).join("\n");
    return `<tr class="gl-${escAttr(g.tone)}" title="${escAttr(tip)}">`
      + `<td>${escAttr(g.key)}</td><td class="num">${g.n}</td><td class="num">${g.days}</td>`
      + `<td class="num">${g.saved_n}/${g.miss_n}/${g.flat_n}</td>`
      + `<td class="num">${cfPct(g.cf_mean)}</td><td class="num">${cfPct(g.net)}</td>`
      + `<td class="num">${ci}</td><td class="num">${cfPct(g.vs_released)}</td>`
      + `<td>${escAttr(g.verdict)}</td></tr>`;
  }).join("");
  const gateTbl = gates.length
    ? `<table class="gl-tbl cf-gates"><thead><tr><th>闸门</th><th>被拦</th><th>天数</th>`
      + `<th>避险/误杀/平</th><th>若放行均</th><th>净省(Σ)</th><th>90%区间</th><th>对同日放行</th><th>结论</th></tr></thead>`
      + `<tbody>${rows}</tbody></table>`
    : `<div class="meta">窗口内没有到价被拦的买卡</div>`;
  const notes = [
    `卡片 ${s.cards || 0} · 到价 ${s.touched || 0} · 未到价 ${s.untouched || 0} · 触达未知 ${s.touch_unknown || 0}`
      + (s.weak_dropped ? ` · 剔除早盘推断 ${s.weak_dropped}` : "")
      + ` · 待结算 ${s.pending || 0}`,
    tierTxt ? `触达证据：${tierTxt}` : "",
    `规则：计划价成交 · 次日起止损（无记录止损按个股 -3% / ETF -1.5%，跳空按开盘）· 持有 ${s.hold_days} 日 · 扣往返 ${s.cost_pct}%`,
    s.approx_n ? `被拦原因：实测 ${s.exact_n} 张，其余 ${s.approx_n} 张由历史标记推断（开盘静默/竞价桥等未记录，归入「未明」）` : "",
  ].filter(Boolean);
  body.innerHTML =
    `<div class="cf-headline">${escAttr(d.headline || "")}</div>`
    + sides
    + gateTbl
    + cfSensTable(d.entry_sens)
    + cfStopTable(d.stop_cmp)
    + `<ul class="cf-notes">${notes.map((n) => `<li>${escAttr(n)}</li>`).join("")}</ul>`;
}

/**
 * Render the entry-premium sensitivity table (same cards, entry = plan + x%).
 * @param {object|null} sens - ``entry_sens`` block of the audit payload.
 * @returns {string} HTML, or an empty string when there are no rows.
 */
function cfSensTable(sens) {
  const rows = (sens && sens.rows) || [];
  if (!rows.length) return "";
  const tone = (r) => (r.off === 0 ? "" : r.delta <= -1.0 ? "gl-bad" : r.delta <= -0.5 ? "gl-mid" : "gl-good");
  const body = rows.map((r) =>
    `<tr class="${tone(r)}"><td>${r.off === 0 ? "计划价" : `+${r.off}%`}</td>`
    + `<td class="num">${r.n}</td><td class="num">${r.win}%</td><td class="num">${cfPct(r.mean)}</td>`
    + `<td class="num">${r.off === 0 ? "—" : cfPct(r.delta)}</td><td class="num">${r.stop_rate}%</td>`
    + `<td class="num">${r.rel_mean == null ? "—" : `${cfPct(r.rel_mean)}（${r.rel_n}）`}</td></tr>`,
  ).join("");
  return `<div class="cf-sens-title" title="${escAttr(sens.note || "")}">入场偏差敏感度：多付几个点，三日净值掉多少</div>`
    + `<table class="gl-tbl cf-sens"><thead><tr><th>入场</th><th>张数</th><th>胜率</th><th>三日净值</th>`
    + `<th>较计划价</th><th>止损率</th><th>其中放行卡</th></tr></thead><tbody>${body}</tbody></table>`;
}

/**
 * Render the live vs shadow-ATR stop comparison on the same touched cards.
 * @param {object|null} cmp - ``stop_cmp`` block of the audit payload.
 * @returns {string} HTML, or an empty string when there are no rows.
 */
function cfStopTable(cmp) {
  const rows = (cmp && cmp.rows) || [];
  if (!rows.length) return "";
  const body = rows.map((r) =>
    `<tr><td>${escAttr(r.label)}</td><td class="num">${r.n}</td><td class="num">${r.gap_med}%</td>`
    + `<td class="num">${r.stop_rate}%</td><td class="num">${r.win}%</td><td class="num">${cfPct(r.mean)}</td>`
    + `<td class="num">${Number(r.r_mult_med).toFixed(2)}R</td></tr>`,
  ).join("");
  const tip = `${cmp.note || ""}\n实测记录 ${cmp.recorded_n || 0} 张，其余按日线现算`;
  return `<div class="cf-sens-title" title="${escAttr(tip)}">止损口径对照：日低止损 vs 影子 ATR 止损（只对照，不改卡片）</div>`
    + `<table class="gl-tbl cf-sens"><thead><tr><th>口径</th><th>张数</th><th>止损距离</th><th>止损率</th>`
    + `<th>胜率</th><th>三日净值</th><th>R 倍数(中位)</th></tr></thead><tbody>${body}</tbody></table>`;
}

/**
 * Fetch and paint the audit for the current controls (cached per window).
 * @param {boolean} force - Bypass the page-level cache.
 */
async function loadCfAudit(force) {
  const body = document.getElementById("revCfBody");
  if (!body) return;
  const days = Number((document.getElementById("cfDays") || {}).value || 20);
  const loose = !!(document.getElementById("cfLoose") || {}).checked;
  const key = `${days}|${loose ? 1 : 0}`;
  if (!force && cfAuditCache[key]) {
    paintCfAudit(cfAuditCache[key]);
    return;
  }
  body.innerHTML = `<div class="meta">加载中（首次需拉日线，约数十秒）…</div>`;
  try {
    const r = await fetch(`/api/review/counterfactual?days=${days}&strict=${loose ? "false" : "true"}`);
    const d = await r.json();
    cfAuditCache[key] = d;
    paintCfAudit(d);
  } catch (e) {
    body.innerHTML = `<div class="meta">加载失败：${escAttr(String((e && e.message) || e))}</div>`;
  }
}

(function bindCfAudit() {
  const box = document.getElementById("revCfAudit");
  if (!box) return;
  box.addEventListener("toggle", () => {
    if (box.open) loadCfAudit(false);
  });
  const reload = document.getElementById("cfReload");
  if (reload) reload.addEventListener("click", (ev) => { ev.preventDefault(); loadCfAudit(true); });
  ["cfDays", "cfLoose"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => loadCfAudit(false));
  });
})();
