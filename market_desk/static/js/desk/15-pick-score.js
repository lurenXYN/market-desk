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

function pickScoreClass(grade) {
  return ({ 优先: "g-hi", 可以考虑: "g-mid", 谨慎: "g-lo", 放弃: "g-drop" })[grade] || "g-mid";
}

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
    const rows = (it.factors || []).map((f) => {
      const p = Number(f.points || 0);
      const cls = p > 0 ? "plus" : (p < 0 ? "minus" : "");
      const hist = f.hist ? `<span class="hist">${escAttr(f.hist)}</span>` : "";
      return `<li><span class="pts ${cls}">${p > 0 ? "+" : ""}${p}</span>`
        + `<span><b>${escAttr(f.label)}</b> ${escAttr(f.detail)}${hist}</span></li>`;
    }).join("") || `<li class="meta">没有可比的加减分项</li>`;
    return `<div class="rev-pick-card${i === 0 ? " top" : ""}">`
      + `<div class="hd"><span><b>${escAttr(it.name || it.code)}</b> <span class="meta">${escAttr(it.code || "")} · ${escAttr(it.source_label || "")}</span></span>`
      + `<span class="score ${pickScoreClass(it.grade)}">${it.score}</span></div>`
      + `<div class="meta">${escAttr(it.grade)}${px ? " · " + escAttr(px) : ""}${escAttr(plan)}</div>`
      + `<ul>${rows}</ul></div>`;
  }).join("");
  const base = d.base_win3 == null ? "" : `，整体三日胜率 ${d.base_win3}%`;
  out.innerHTML = `<div class="verdict">${escAttr(d.verdict || "")}</div>`
    + `<div class="rev-pick-cards">${cards}</div>`
    + `<div class="meta" style="margin-top:6px">${escAttr(d.scored_at || "")} 打分 · 基础分 60，按各项加减；历史微调参考近 ${d.history_n || 0} 条已打分买点${base}。只作参考，不改信号。</div>`;
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
