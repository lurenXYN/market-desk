function ensureBacktestDefaults() {
  const toEl = document.getElementById("btTo");
  const fromEl = document.getElementById("btFrom");
  const modeEl = document.getElementById("btMode");
  if (!toEl || !fromEl) return;
  const today = (lastData && lastData.trade_date)
    || new Date().toISOString().slice(0, 10);
  if (!toEl.value) toEl.value = today;
  if (!fromEl.value) {
    const d = new Date(today + "T12:00:00");
    d.setDate(d.getDate() - 14);
    fromEl.value = d.toISOString().slice(0, 10);
  }
  // Default to plan (human ops); only set when untouched.
  if (modeEl && !modeEl.dataset.userSet) {
    modeEl.value = "plan";
  }
  if (typeof loadBacktestArchives === "function") loadBacktestArchives();
}
function paintBacktest(payload) {
  window.__lastBacktest = payload || null;
  const sum = (payload && payload.summary) || {};
  const note = document.getElementById("btNote");
  const sumEl = document.getElementById("btSum");
  const body = document.getElementById("btBody");
  const disc = document.getElementById("btDisclaimer");
  if (disc && payload && payload.disclaimer) {
    disc.textContent = "⚠ " + payload.disclaimer
      + (payload.max_span_days ? ` 单次跨度最多 ${payload.max_span_days} 天。` : "");
  }
  if (note) note.textContent = payload.note || payload.disclaimer || "";
  if (sumEl) {
    if (payload && payload.dry_run) {
      sumEl.innerHTML = [
        `<span>预览匹配 <b>${payload.matched_n ?? sum.matched_n ?? 0}</b></span>`,
        `<span>买 ${payload.buy_n ?? sum.buy_n ?? 0}</span>`,
        `<span>卖 ${payload.sell_n ?? sum.sell_n ?? 0}</span>`,
      ].join("");
    } else {
      const bits = [
        `买 ${sum.buy_filled_n ?? 0}/${sum.buy_n ?? 0}`
          + (sum.buy_fill_rate != null ? `（触达 ${sum.buy_fill_rate}%）` : ""),
        sum.buy_hit_rate != null
          ? `买命中 ${sum.buy_hit_rate}%（${sum.buy_hit_n ?? 0}/${sum.buy_scored_n ?? 0}）`
          : "买命中 —",
        sum.buy_avg_day1 != null
          ? `买次日均 ${sum.buy_avg_day1 > 0 ? "+" : ""}${sum.buy_avg_day1}%`
          : "",
        `卖 ${sum.sell_filled_n ?? 0}/${sum.sell_n ?? 0}`,
        sum.sell_hit_rate != null
          ? `卖命中 ${sum.sell_hit_rate}%`
          : "",
        sum.liq_skip_n ? `量能跳过 ${sum.liq_skip_n}` : "",
        sum.gap_fill_n ? `缺口成交 ${sum.gap_fill_n}` : "",
        (sum.sim_exec && sum.sim_exec.score != null)
          ? `模拟执行分 ${sum.sim_exec.score}`
            + (sum.sim_exec.chase_n ? `·追高${sum.sim_exec.chase_n}` : "")
          : "",
      ].filter(Boolean);
      sumEl.innerHTML = bits.map((b) => `<span>${b}</span>`).join("");
    }
  }
  const items = (payload && payload.items) || [];
  if (!body) return;
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="10" class="meta">该区间无信号或未触达</td></tr>`;
    return;
  }
  if (payload && payload.dry_run) {
    body.innerHTML = items.map((it) => {
      const side = it.side === "sell" ? "卖" : "买";
      const nm = (typeof tickerHtml === "function")
        ? tickerHtml(it.name, it.code)
        : `${it.name || ""} ${it.code || ""}`;
      return `<tr>
            <td data-label="日">${(it.trade_date || "").slice(5)}</td>
            <td data-label="向">${side}</td>
            <td data-label="名称">${nm}</td>
            <td data-label="代码">${it.code || ""}</td>
            <td data-label="计划">—</td>
            <td data-label="模拟成交">预览</td>
            <td data-label="触达">ready ${it.ready ? "是" : "否"}</td>
            <td data-label="标签">—</td>
            <td data-label="次日%">—</td>
            <td data-label="三日%">—</td>
          </tr>`;
    }).join("");
    return;
  }
  body.innerHTML = items.map((it) => {
    const side = it.side === "sell" ? "卖" : "买";
    const plan = it.plan_price != null ? it.plan_price
      : (it.wait_price != null ? it.wait_price : "—");
    const fill = it.sim_filled
      ? `${it.sim_fill_price ?? "—"}<div class="meta">${it.sim_fill_date || ""}</div>`
      : "—";
    const d1 = it.outcome_day1_pct;
    const d3 = it.outcome_day3_pct;
    const d1c = d1 == null ? "" : (d1 >= 0 ? "up" : "down");
    const d3c = d3 == null ? "" : (d3 >= 0 ? "up" : "down");
    const nm = (typeof tickerHtml === "function")
      ? tickerHtml(it.name, it.code)
      : `${it.name || ""} ${it.code || ""}`;
    return `<tr>
          <td data-label="日">${(it.trade_date || "").slice(5)}</td>
          <td data-label="向">${side}</td>
          <td data-label="名称">${nm}</td>
          <td data-label="代码">${it.code || ""}</td>
          <td data-label="计划">${plan}</td>
          <td data-label="模拟成交">${fill}</td>
          <td data-label="触达">${it.note || (it.sim_filled ? "是" : "否")}</td>
          <td data-label="标签">${it.outcome_label || "—"}</td>
          <td data-label="次日%" class="${d1c}">${d1 == null ? "—" : ((d1 > 0 ? "+" : "") + d1)}</td>
          <td data-label="三日%" class="${d3c}">${d3 == null ? "—" : ((d3 > 0 ? "+" : "") + d3)}</td>
        </tr>`;
  }).join("");
}
function btPayload(extra) {
  return Object.assign({
    date_from: document.getElementById("btFrom").value,
    date_to: document.getElementById("btTo").value,
    mode: document.getElementById("btMode").value || "plan",
    ready_only: !!document.getElementById("btReady").checked,
    include_sells: !!document.getElementById("btSells").checked,
    limit: Number(document.getElementById("btLimit").value) || 120,
    vol_min_ratio: Number(document.getElementById("btVolRatio")?.value ?? 0.4),
    slip_pct: Number(document.getElementById("btSlip")?.value ?? 0.15),
    gap_pct: Number(document.getElementById("btGap")?.value ?? 1),
    fidelity: document.getElementById("btFidelity")?.value || "daily",
    async_job: !!document.getElementById("btAsync")?.checked,
    persist: !!document.getElementById("btPersist")?.checked,
    label: (document.getElementById("btLabel")?.value || "").trim(),
  }, extra || {});
}
async function pollBacktestJob(jobId, bodyEl) {
  const maxTries = 120;
  for (let i = 0; i < maxTries; i++) {
    await new Promise((r) => setTimeout(r, i < 3 ? 800 : 1500));
    const r = await fetch("/api/backtest/jobs/" + encodeURIComponent(jobId), {
      credentials: "same-origin",
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">${d.detail || "任务查询失败"}</td></tr>`;
      return null;
    }
    const job = d.job || {};
    const prog = job.progress || {};
    if (bodyEl) {
      const pct = prog.pct != null ? prog.pct : "…";
      bodyEl.innerHTML = `<tr><td colspan="10" class="meta">异步回测 ${job.status || "…"} · ${pct}%`
        + (prog.done != null ? `（${prog.done}/${prog.total || "?"}）` : "")
        + ` · #${jobId}</td></tr>`;
    }
    if (job.status === "done" && job.result) return job.result;
    if (job.status === "error") {
      if (bodyEl) {
        bodyEl.innerHTML = `<tr><td colspan="10" class="meta">异步失败：${job.error || "unknown"}</td></tr>`;
      }
      return null;
    }
  }
  if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">异步超时，可稍后刷新存档或重试</td></tr>`;
  return null;
}
async function loadBacktestArchives() {
  const box = document.getElementById("btArchiveList");
  if (!box) return;
  try {
    const r = await fetch("/api/backtest/runs?limit=40", { credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      box.textContent = "存档列表不可用（需 admin）";
      return;
    }
    const runs = d.runs || [];
    if (!runs.length) {
      box.innerHTML = `<span class="meta">暂无存档。勾选「存档本次」再跑回测即可。</span>`;
      return;
    }
    box.innerHTML = `<div style="display:flex;flex-direction:column;gap:6px">${
      runs.map((run) => {
        const hit = run.buy_hit_rate != null ? `买命中 ${run.buy_hit_rate}%` : "买命中 —";
        const fill = run.buy_fill_rate != null ? `触达 ${run.buy_fill_rate}%` : "";
        const exec = run.sim_exec_score != null ? `执行分 ${run.sim_exec_score}` : "";
        return `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-bottom:1px dashed var(--line);padding:4px 0">
              <label class="settings-check" style="margin:0"><input type="checkbox" class="bt-run-pick" value="${run.id}" /> #${run.id}</label>
              <button type="button" class="rev-col-btn bt-run-load" data-id="${run.id}">加载</button>
              <button type="button" class="pos-del bt-run-del" data-id="${run.id}">删</button>
              <span><b>${run.label || "未命名"}</b></span>
              <span class="meta">${run.date_from}~${run.date_to} · ${run.mode}
                · 量${run.vol_min_ratio ?? "—"}/滑${run.slip_pct ?? "—"}/跳${run.gap_pct ?? "—"}</span>
              <span class="meta">${hit}${fill ? " · " + fill : ""}${exec ? " · " + exec : ""}</span>
              <span class="meta">${(run.created_at || "").slice(5, 16)}</span>
            </div>`;
      }).join("")
    }</div>`;
  } catch (e) {
    box.textContent = "存档列表加载失败";
  }
}
function paintBacktestCompare(payload) {
  const el = document.getElementById("btCompare");
  if (!el) return;
  const runs = (payload && payload.runs) || [];
  const metrics = (payload && payload.metrics) || [];
  if (!runs.length) {
    el.innerHTML = `<span class="meta">请至少勾选 1～6 条存档</span>`;
    return;
  }
  const head = `<tr><th>指标</th>${runs.map((r) => `<th>#${r.id}<div class="meta">${r.label || ""}</div></th>`).join("")}</tr>`;
  const body = metrics.map((m) => {
    const cells = runs.map((r) => {
      let v = r[m.key];
      if (v == null && r.summary && r.summary[m.key] != null) v = r.summary[m.key];
      if (typeof v === "number") v = Number.isInteger(v) ? v : Math.round(v * 100) / 100;
      return `<td>${v == null || v === "" ? "—" : v}</td>`;
    }).join("");
    return `<tr><td>${m.title || m.key}</td>${cells}</tr>`;
  }).join("");
  el.innerHTML = `<div class="rev-table-wrap"><table class="rev-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}
document.getElementById("btArchiveRefresh")?.addEventListener("click", () => loadBacktestArchives());
document.getElementById("btArchiveCompare")?.addEventListener("click", async () => {
  const ids = [...document.querySelectorAll(".bt-run-pick:checked")].map((el) => el.value);
  if (!ids.length) {
    paintBacktestCompare({ runs: [], metrics: [] });
    return;
  }
  try {
    const r = await fetch("/api/backtest/compare?ids=" + encodeURIComponent(ids.join(",")), {
      credentials: "same-origin",
    });
    const d = await r.json().catch(() => ({}));
    paintBacktestCompare(d);
  } catch (e) {
    paintBacktestCompare({ runs: [], metrics: [] });
  }
});
document.getElementById("btArchiveClear")?.addEventListener("click", async () => {
  if (!confirm("清空全部回测存档？不可恢复。")) return;
  try {
    const r = await fetch("/api/backtest/runs/clear?keep=0", {
      method: "POST",
      credentials: "same-origin",
    });
    if (r.ok) {
      document.getElementById("btCompare").innerHTML = "";
      await loadBacktestArchives();
    } else {
      alert("清空失败");
    }
  } catch (e) {
    alert("清空请求失败");
  }
});
document.getElementById("btArchiveList")?.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (!(t instanceof HTMLElement)) return;
  const loadBtn = t.closest(".bt-run-load");
  const delBtn = t.closest(".bt-run-del");
  if (loadBtn) {
    const id = loadBtn.getAttribute("data-id");
    const bodyEl = document.getElementById("btBody");
    if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">加载存档 #${id}…</td></tr>`;
    try {
      const r = await fetch("/api/backtest/runs/" + id, { credentials: "same-origin" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">${d.detail || "加载失败"}</td></tr>`;
        return;
      }
      if (d.date_from) document.getElementById("btFrom").value = d.date_from;
      if (d.date_to) document.getElementById("btTo").value = d.date_to;
      if (d.mode) document.getElementById("btMode").value = d.mode;
      const ready = document.getElementById("btReady");
      if (ready) ready.checked = !!d.ready_only;
      const sells = document.getElementById("btSells");
      if (sells) sells.checked = d.include_sells !== false;
      const realism = d.realism || {};
      if (document.getElementById("btVolRatio") && realism.vol_min_ratio != null) {
        document.getElementById("btVolRatio").value = realism.vol_min_ratio;
      }
      if (document.getElementById("btSlip") && realism.slip_pct != null) {
        document.getElementById("btSlip").value = realism.slip_pct;
      }
      if (document.getElementById("btGap") && realism.gap_pct != null) {
        document.getElementById("btGap").value = realism.gap_pct;
      }
      paintBacktest(d);
    } catch (e) {
      if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">加载请求失败</td></tr>`;
    }
    return;
  }
  if (delBtn) {
    const id = delBtn.getAttribute("data-id");
    if (!confirm("删除存档 #" + id + "？")) return;
    try {
      const r = await fetch("/api/backtest/runs/" + id, {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (r.ok) await loadBacktestArchives();
      else alert("删除失败");
    } catch (e) {
      alert("删除请求失败");
    }
  }
});
document.getElementById("btPreview")?.addEventListener("click", async () => {
  const bodyEl = document.getElementById("btBody");
  if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">预览匹配中…</td></tr>`;
  try {
    const r = await fetch("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(btPayload({ dry_run: true, persist: false })),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      if (bodyEl) {
        bodyEl.innerHTML = `<tr><td colspan="10" class="meta">${d.detail || "预览失败"}</td></tr>`;
      }
      return;
    }
    paintBacktest(d);
  } catch (e) {
    if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">预览请求失败</td></tr>`;
  }
});
document.getElementById("btGrid")?.addEventListener("click", async () => {
  const bodyEl = document.getElementById("btBody");
  if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">参数组网格跑测中…</td></tr>`;
  try {
    const base = btPayload({});
    const r = await fetch("/api/backtest/grid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        date_from: base.date_from,
        date_to: base.date_to,
        include_sells: base.include_sells,
        ready_only: base.ready_only,
        limit: base.limit,
        variants: [
          { mode: base.mode || "plan", vol_min_ratio: base.vol_min_ratio, slip_pct: base.slip_pct, gap_pct: base.gap_pct, label: "当前表单" },
          { mode: "wait", vol_min_ratio: base.vol_min_ratio, slip_pct: base.slip_pct, gap_pct: base.gap_pct, label: "wait·同量能" },
          { mode: "plan", vol_min_ratio: Math.min(1, (base.vol_min_ratio || 0.4) + 0.2), slip_pct: (base.slip_pct || 0.15) + 0.1, gap_pct: base.gap_pct, label: "plan·更严量能/滑点" },
        ],
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">${d.detail || "网格失败"}</td></tr>`;
      return;
    }
    if (bodyEl) {
      bodyEl.innerHTML = `<tr><td colspan="10" class="meta">已存档 ${(d.runs || []).length} 组，见下方对比</td></tr>`;
    }
    await loadBacktestArchives();
    const ids = (d.runs || []).map((x) => x.run_id).filter(Boolean);
    if (ids.length) {
      const cr = await fetch("/api/backtest/compare?ids=" + encodeURIComponent(ids.join(",")), {
        credentials: "same-origin",
      });
      paintBacktestCompare(await cr.json().catch(() => ({})));
    }
  } catch (e) {
    if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">网格请求失败</td></tr>`;
  }
});
document.getElementById("btVsFilled")?.addEventListener("click", async () => {
  const box = document.getElementById("btVsFilledBox");
  const last = window.__lastBacktest;
  if (!last || last.dry_run || !(last.items || []).length) {
    if (box) box.textContent = "请先跑一遍回测（非预览）再对照实盘";
    return;
  }
  if (box) box.textContent = "对照实盘中…";
  try {
    const r = await fetch("/api/backtest/vs-filled", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        items: last.items,
        date_from: last.date_from,
        date_to: last.date_to,
        run_id: last.run_id || null,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      if (box) box.textContent = d.detail || "对照失败";
      return;
    }
    const s = d.summary || {};
    const head = `对照 ${s.paired_n ?? 0} 笔 · 双命中 ${s.both_hit ?? 0} · 双未中 ${s.both_miss ?? 0}`
      + ` · 仅模拟命中 ${s.sim_only_hit ?? 0} · 仅实盘命中 ${s.fill_only_hit ?? 0}`
      + (s.avg_price_gap_pct != null ? ` · 均价差 ${s.avg_price_gap_pct}%` : "");
    const rows = (d.pairs || []).slice(0, 40).map((p) => {
      const gap = p.price_gap_pct != null ? `差${p.price_gap_pct}%` : "";
      return `<div>${(p.trade_date || "").slice(5)} ${p.name || ""} ${p.code || ""}`
        + ` · 模拟 ${p.sim_fill_price ?? "—"}/${p.sim_label || "—"}`
        + ` · 实盘 ${p.fill_price ?? "—"}/${p.fill_label || "—"}`
        + (gap ? ` · ${gap}` : "")
        + `</div>`;
    }).join("");
    if (box) box.innerHTML = `<div><b>${head}</b></div>${rows || "<div class='meta'>区间内无已交易对照</div>"}`;
  } catch (e) {
    if (box) box.textContent = "对照请求失败";
  }
});
document.getElementById("btForm")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const bodyEl = document.getElementById("btBody");
  if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">拉取日线并撮合中…</td></tr>`;
  try {
    const r = await fetch("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(btPayload({ dry_run: false })),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      if (bodyEl) {
        bodyEl.innerHTML = `<tr><td colspan="10" class="meta">${d.detail || "回测失败"}</td></tr>`;
      }
      return;
    }
    if (d.async && d.job_id) {
      if (bodyEl) {
        bodyEl.innerHTML = `<tr><td colspan="10" class="meta">已提交异步 #${d.job_id}，轮询中…</td></tr>`;
      }
      const result = await pollBacktestJob(d.job_id, bodyEl);
      if (!result) return;
      paintBacktest(result);
      if (result.persisted) await loadBacktestArchives();
      return;
    }
    paintBacktest(d);
    if (d.persisted) await loadBacktestArchives();
  } catch (e) {
    if (bodyEl) bodyEl.innerHTML = `<tr><td colspan="10" class="meta">回测请求失败</td></tr>`;
  }
});
