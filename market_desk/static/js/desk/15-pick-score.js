const PICK_MAX = 6;

function pickManualCodes() {
  const raw = (document.getElementById("revPickCodes") || {}).value || "";
  return raw.split(/[\s,，、;；]+/).map((s) => s.replace(/\D/g, "")).filter(Boolean);
}

function paintPickSel() {
  const el = document.getElementById("revPickSel");
  if (!el) return;
  const n = revPickIds.size + pickManualCodes().length;
  el.textContent = n ? `已选 ${n} 只（最多 ${PICK_MAX}）` : "在名称前勾选 2–5 只";
}

const PICK_KIND = { hard: ["✕", "硬伤"], risk: ["⚠", "风险"], plus: ["✓", "加分"], info: ["·", "参考"] };
const PICK_KIND_ORDER = { hard: 0, risk: 1, plus: 2, info: 3 };

/**
 * Classify one factor row; mirrors pick_score.factor_kind for stored rows without ``kind``.
 */
function pickKind(f) {
  if (f && PICK_KIND[f.kind]) return f.kind;
  const p = Number((f && f.points) || 0);
  const key = String((f && f.key) || "");
  if ((key === "gate" || key === "pct") && p < 0) return "hard";
  if (key === "gap" || key === "lhb" || p < 0) return "risk";
  return p > 0 ? "plus" : "info";
}

function pickSigned(v, digits = 2) {
  const n = Number(v);
  return (n > 0 ? "+" : "") + n.toFixed(digits);
}

/**
 * Render the checklist badge (硬伤 / 风险N / 无风险) with the item lists as tooltip.
 */
function pickCheckHtml(chk, tag = "span", extraCls = "") {
  if (!chk || !chk.label) return "";
  const tip = [
    chk.hard && chk.hard.length ? "硬伤：" + chk.hard.join("、") : "",
    chk.risk && chk.risk.length ? "风险：" + chk.risk.join("、") : "",
    chk.plus && chk.plus.length ? "加分：" + chk.plus.join("、") : "",
  ].filter(Boolean).join("\n") || "没有硬伤、风险或加分项";
  return `<${tag} class="pick-chk chk-${escAttr(chk.tone || "mid")} ${extraCls}" title="${escAttr(tip)}">${escAttr(chk.label)}</${tag}>`;
}

function pickFactorsHtml(factors) {
  const rows = (factors || []).slice().sort((a, b) => PICK_KIND_ORDER[pickKind(a)] - PICK_KIND_ORDER[pickKind(b)]);
  return rows.map((f) => {
    const k = pickKind(f);
    const p = Number(f.points || 0);
    const pts = p ? ` <span class="pts-ref" title="冻结公式的参考分点数">${p > 0 ? "+" : ""}${p}</span>` : "";
    const hist = f.hist ? `<span class="hist">${escAttr(f.hist)}</span>` : "";
    return `<li class="k-${k}"><span class="mk" title="${PICK_KIND[k][1]}">${PICK_KIND[k][0]}</span>`
      + `<span><b>${escAttr(f.label)}</b> ${escAttr(f.detail)}${pts}${hist}</span></li>`;
  }).join("") || `<li class="meta">没有可比项</li>`;
}

function pickPosHtml(pos) {
  if (!pos || !pos.label) return "";
  return `<span class="pick-pos pos-${escAttr(pos.tone || "mid")}" title="${escAttr(pos.detail || "")}（买点位置；过不追 / 到止损算硬伤）">${escAttr(pos.label)}</span>`;
}

/**
 * One-line out-of-sample self-audit of the legacy score.
 */
function pickAuditHtml(a) {
  if (!a || !a.label) return "";
  const tone = ({ works: "good", weak: "mid", fails: "bad", building: "mid" })[a.status] || "mid";
  const extra = [];
  if (a.top_minus_bottom != null) extra.push(`高分 1/3 减低分 1/3 三日 ${pickSigned(a.top_minus_bottom)}%`);
  if (a.clean_minus_flagged != null) extra.push(`无风险减有风险/硬伤 ${pickSigned(a.clean_minus_flagged)}%（${a.clean_days} 天）`);
  return `<div class="meta pick-audit pa-${tone}">参考分自检：${escAttr(a.label)}${extra.length ? "；" + escAttr(extra.join("；")) : ""}</div>`;
}

/**
 * Same-day lit-card float: the day effect the per-stock checklist cannot see.
 */
function pickDayBookHtml(b, isToday) {
  if (!b || !b.n || b.avg_pct == null) return "";
  const avg = Number(b.avg_pct);
  const tone = avg <= -2 ? "bad" : (avg < 0 ? "mid" : "good");
  const lab = isToday ? "今日已亮卡" : "当日已亮卡";
  const tip = "按首次亮灯价算的浮动盈亏，同代码只算一次（历史日期用最后一次刷新价）。"
    + "好坏日扎堆：已亮卡整体在亏的日子，新买点也容易亏；「进场影子·当日熔断」在检验 −1/−2/−3% 档。只提示，不拦截。";
  return `<span class="rev-daybook db-${tone}" title="${escAttr(tip)}">${lab} ${b.n} 只 · 均 ${pickSigned(avg)}% · 最差 ${pickSigned(b.worst_pct)}%</span>`;
}

function revTopPicksHtml(viewDate, isToday) {
  if (revScores.day !== viewDate) return "";
  const book = pickDayBookHtml(revScores.day_book, isToday);
  const top = revScores.top || [];
  if (!book && !top.length) return "";
  const lab = isToday ? "今日无风险项" : "当日无风险项（信号时）";
  const links = top.map((t) =>
    `<button type="button" class="rev-top-pick" data-id="${escAttr(String(t.id))}" title="${escAttr((t.plus || []).length ? "加分：" + t.plus.join("、") : "没有硬伤和风险项")}">`
    + `${escAttr(t.name || t.code)}${t.plus_n ? ` <b>+${t.plus_n}</b>` : ""}</button>`
  ).join("");
  return `<div class="rev-top-picks">${book}${top.length ? `<span class="lab">${lab}</span>${links}` : ""}</div>`;
}

async function loadReviewScores(date) {
  const want = String(date || "").trim();
  const seq = ++reviewScoreSeq;
  if (revScores.day !== want) revScores = { day: want, live: false, items: {}, top: [], loading: true };
  else revScores.loading = true;
  try {
    const r = await fetch("/api/review/scores" + (want ? "?date=" + encodeURIComponent(want) : ""));
    const d = await r.json();
    if (seq !== reviewScoreSeq || !d || !d.ok) return;
    revScores = {
      day: d.trade_date || want,
      live: !!d.live,
      items: d.items || {},
      top: d.top || [],
      loading: false,
      scored_at: d.scored_at || "",
      history_n: d.history_n || 0,
      base_win3: d.base_win3,
      audit: d.audit || null,
      day_book: d.day_book || null,
    };
  } catch (e) {
    if (seq !== reviewScoreSeq) return;
  } finally {
    if (seq === reviewScoreSeq) {
      revScores.loading = false;
      if (lastReview && (lastReview.signals || []).length) paintReview(lastReview);
    }
  }
}

function closeScorePop() {
  const pop = document.getElementById("revScorePop");
  if (pop) pop.remove();
}

/**
 * Small grey legacy-score line; struck through once the self-audit says it fails.
 */
function pickRefScoreHtml(score, grade, audit) {
  if (score == null) return "";
  const dead = audit && audit.status === "fails";
  return `<span class="pick-ref${dead ? " dead" : ""}" title="冻结公式的 0–100 参考分，只存档供样本外自检，不用于排序">参考分 ${score}${grade ? " " + escAttr(grade) : ""}</span>`;
}

function showScorePop(anchor, id) {
  closeScorePop();
  const it = revScores.items[String(id)];
  if (!it) return;
  const pop = document.createElement("div");
  pop.id = "revScorePop";
  pop.className = "rev-score-pop";
  const when = revScores.live
    ? `实时 ${escAttr(it.at || revScores.scored_at || "")}`
    : `信号当时 ${escAttr(it.at || "")}`;
  const first = it.first && it.first.check_label
    ? ` · 首次「${escAttr(it.first.check_label)}」（${escAttr(it.first.at || "")}）`
    : "";
  const base = revScores.base_win3 == null ? "" : `，整体三日胜率 ${revScores.base_win3}%`;
  pop.innerHTML = `<div class="hd"><span><b>${escAttr(it.name || it.code || "")}</b> <span class="meta">${escAttr(it.code || "")}</span></span>`
    + `${pickCheckHtml(it.check, "span", "big")}</div>`
    + `<div class="meta">${when}${first} · ${pickRefScoreHtml(it.score, it.grade, revScores.audit)}</div>`
    + (it.position ? `<div class="meta">买点位置 ${pickPosHtml(it.position)} ${escAttr(it.position.detail || "")}</div>` : "")
    + `<ul>${pickFactorsHtml(it.factors)}</ul>`
    + `<div class="meta">✕ 硬伤＝今天按计划买不了或闸门没过；⚠ 风险＝回测里偏弱的特征（证据弱，单项别当否决）；✓ 加分；· 只作参考。`
    + (revScores.live ? `历史胜率参考近 ${revScores.history_n || 0} 条已打分买点${base}。` : "")
    + `只作参考，不改信号。<button type="button" class="q" data-term="纠结对比">规则</button></div>`
    + pickAuditHtml(revScores.audit);
  document.body.appendChild(pop);
  const rc = anchor.getBoundingClientRect();
  const w = pop.offsetWidth;
  const left = Math.max(8, Math.min(window.innerWidth - w - 8, rc.left + window.scrollX));
  pop.style.left = left + "px";
  pop.style.top = (rc.bottom + window.scrollY + 4) + "px";
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest(".rev-score-btn, .rev-top-pick");
  if (btn) {
    ev.stopPropagation();
    const open = document.getElementById("revScorePop");
    if (open && open.dataset.id === btn.getAttribute("data-id")) {
      closeScorePop();
      return;
    }
    showScorePop(btn, btn.getAttribute("data-id"));
    const pop = document.getElementById("revScorePop");
    if (pop) pop.dataset.id = btn.getAttribute("data-id") || "";
    return;
  }
  if (!ev.target.closest("#revScorePop")) closeScorePop();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeScorePop();
});

function paintPickResult(d) {
  const out = document.getElementById("revPickOut");
  if (!out) return;
  out.hidden = false;
  if (!d || !d.ok) {
    out.innerHTML = `<div class="meta">${escAttr((d && (d.error || d.detail)) || "打分失败")}</div>`;
    return;
  }
  const cards = (d.items || []).map((it, i) => {
    const pct = it.live_pct == null ? "" : ` ${(it.live_pct > 0 ? "+" : "") + Number(it.live_pct).toFixed(2)}%`;
    const px = it.live_last == null ? "" : `现价 ${it.live_last}${pct}`;
    const plan = it.price == null ? "" : ` · 计划 ${it.price}`;
    const rows = pickFactorsHtml(it.factors);
    const chk = it.check || {};
    const top = i === 0 && !(chk.hard || []).length;
    return `<div class="rev-pick-card${top ? " top" : ""}">`
      + `<div class="hd"><span><b>${escAttr(it.name || it.code)}</b> <span class="meta">${escAttr(it.code || "")} · ${escAttr(it.source_label || "")}</span></span>`
      + `${pickCheckHtml(chk, "span", "big")}</div>`
      + `<div class="meta">${px ? escAttr(px) : ""}${escAttr(plan)} ${pickPosHtml(it.position)} ${pickRefScoreHtml(it.score, it.grade, d.audit)}</div>`
      + `<ul>${rows}</ul></div>`;
  }).join("");
  const base = d.base_win3 == null ? "" : `，整体三日胜率 ${d.base_win3}%`;
  out.innerHTML = `<div class="verdict">${escAttr(d.verdict || "")}</div>`
    + `<div class="rev-pick-cards">${cards}</div>`
    + `<div class="meta" style="margin-top:6px">${escAttr(d.scored_at || "")} · 按硬伤数、风险数、加分数排序，清单相同就是并列；历史胜率参考近 ${d.history_n || 0} 条已打分买点${base}。只作参考，不改信号。</div>`
    + pickAuditHtml(d.audit);
}

async function runPickScore() {
  const ids = Array.from(revPickIds).map((x) => Number(x)).filter((x) => Number.isFinite(x));
  const codes = pickManualCodes();
  if (ids.length + codes.length < 2) {
    alert("至少选 2 只才能对比（勾选复盘行或手输代码）");
    return;
  }
  if (ids.length + codes.length > PICK_MAX) {
    alert(`最多同时对比 ${PICK_MAX} 只`);
    return;
  }
  const out = document.getElementById("revPickOut");
  if (out) {
    out.hidden = false;
    out.innerHTML = `<div class="meta">打分中…（拉行情 / 日线 / 年内涨停）</div>`;
  }
  try {
    const r = await fetch("/api/pick-score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ signal_ids: ids, codes }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      paintPickResult({ ok: false, error: typeof d.detail === "string" ? d.detail : "打分失败" });
      return;
    }
    paintPickResult(d);
  } catch (e) {
    paintPickResult({ ok: false, error: "打分失败：" + String((e && e.message) || e) });
  }
}

document.getElementById("revBody").addEventListener("change", (ev) => {
  const chk = ev.target.closest(".rev-pick-chk");
  if (!chk) return;
  const id = chk.getAttribute("data-id");
  if (!id) return;
  if (chk.checked) {
    if (revPickIds.size + pickManualCodes().length >= PICK_MAX) {
      chk.checked = false;
      alert(`最多同时对比 ${PICK_MAX} 只`);
      return;
    }
    revPickIds.add(id);
  } else {
    revPickIds.delete(id);
  }
  paintPickSel();
});
document.getElementById("revPickCodes")?.addEventListener("input", paintPickSel);
document.getElementById("revPickCodes")?.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") runPickScore();
});
document.getElementById("revPickGo")?.addEventListener("click", runPickScore);
document.getElementById("revPickClear")?.addEventListener("click", () => {
  revPickIds.clear();
  const inp = document.getElementById("revPickCodes");
  if (inp) inp.value = "";
  document.querySelectorAll("#revBody .rev-pick-chk:checked").forEach((el) => { el.checked = false; });
  const out = document.getElementById("revPickOut");
  if (out) { out.hidden = true; out.innerHTML = ""; }
  paintPickSel();
});
