/** Paint the one-line meaning, playbook, seasonality hint, morning brief, and news radar. */
function renderDeskBrief(d, v) {
  // One-line summary only; long algo text stays folded.
  const one = (v.meaning || v.reason || "").split("（")[0].trim();
  document.getElementById("meaning").textContent = one || "";
  const pb = v.playbook || {};
  document.getElementById("pbDo").textContent = pb.do || (pb.lines && pb.lines[0]) || "—";
  document.getElementById("pbDont").textContent = pb.dont || (pb.lines && pb.lines[1]) || "—";
  document.getElementById("pbSize").textContent = pb.size || (pb.lines && pb.lines[2]) || "—";
  paintPosTargetBar(d);
  const seasonDesk = document.getElementById("seasonDesk");
  const seasonHint = ((d.seasonality || {}).desk) || {};
  if (seasonDesk) {
    const show = !!seasonHint.show && !!(seasonHint.line || seasonHint.detail);
    seasonDesk.hidden = !show;
    if (show) {
      document.getElementById("seasonDeskLine").textContent = seasonHint.line || "";
      document.getElementById("seasonDeskDetail").textContent = seasonHint.detail || "";
      setModStamp("seasonDeskStamp", d.updated_at, { label: "刷新" });
    }
  }
  const brief = d.morning_brief || {};
  document.getElementById("morningFocus").textContent = brief.focus || "等待数据";
  document.getElementById("morningSeg").textContent = brief.segment
    ? (`时段 ${brief.segment}`)
    : "";
  document.getElementById("morningBullets").innerHTML = (brief.bullets || [])
    .map((b) => `<li>${b}</li>`).join("");
  const chk = brief.checklist || {};
  document.getElementById("morningChk").innerHTML = [
    chk.do ? `<span><b>能做</b> ${chk.do}</span>` : "",
    chk.dont ? `<span><b>不做</b> ${chk.dont}</span>` : "",
    chk.size ? `<span><b>仓位</b> ${chk.size}</span>` : "",
  ].filter(Boolean).join("");
  window.__morningMarkdown = brief.markdown || "";
  const nrBlock = document.getElementById("newsRadarBlock");
  const nrHd = document.getElementById("newsRadarHd");
  const nrStrip = document.getElementById("newsRadarStrip");
  const nrNote = document.getElementById("newsRadarNote");
  const nrPanel = document.getElementById("newsRadarPanel");
  const nrCheck = document.getElementById("newsRadarCheck");
  if (nrBlock && nrStrip) {
    const nr = d.news_radar || {};
    const enabled = !!nr.enabled;
    const rows = (nr.sectors || []).slice(0, 5);
    if (!enabled) {
      nrBlock.hidden = true;
      if (nrPanel) { nrPanel.style.display = "none"; nrPanel.innerHTML = ""; nrPanel.dataset.open = ""; }
      if (nrCheck) { nrCheck.hidden = true; nrCheck.innerHTML = ""; }
      if (nrNote) { nrNote.hidden = true; nrNote.textContent = ""; }
      if (nrHd) nrHd.innerHTML = "";
    } else {
      nrBlock.hidden = false;
      const st = nr.status_label || nr.status || "";
      const dotCls = nr.status === "online" ? "on"
        : (nr.status === "offline" ? "off" : "warn");
      if (nrHd) {
        nrHd.innerHTML = `<span class="nr-dot ${dotCls}" title="新闻雷达状态"></span>`
          + `<span class="nr-title">新闻雷达</span>`
          + `<span>${st || "—"}</span>`
          + (nr.updated_at ? `<span class="meta">${nr.updated_at}</span>` : "");
      }
      if (rows.length) {
        nrStrip.innerHTML = rows.map((r, i) => {
          const pct = r.board_pct == null ? ""
            : `${Number(r.board_pct) >= 0 ? "+" : ""}${Number(r.board_pct).toFixed(1)}%`;
          const bits = [r.confirm_label, r.tone_label && r.tone_label !== "中性" ? r.tone_label : "", pct]
            .filter(Boolean).join(" · ");
          const clash = (r.mainline_match === false && nr.mainline_name) ? " clash" : "";
          return `<button type="button" class="nr-chip${clash}" data-nr-i="${i}">`
            + `<b>${r.sector || ""}</b>`
            + (bits ? `<span class="sub">${bits}</span>` : "")
            + `</button>`;
        }).join("");
        nrStrip.querySelectorAll("[data-nr-i]").forEach((btn) => {
          btn.onclick = () => {
            const i = Number(btn.getAttribute("data-nr-i") || 0);
            const r = rows[i];
            if (!r || !nrPanel) return;
            nrStrip.querySelectorAll(".nr-chip").forEach((el) => el.classList.remove("open"));
            if (nrPanel.dataset.open === String(i) && nrPanel.style.display !== "none") {
              nrPanel.style.display = "none";
              nrPanel.dataset.open = "";
              return;
            }
            btn.classList.add("open");
            const arts = (r.articles || []).slice(0, 3).map((a) => {
              const t = a.title || "";
              return a.url
                ? `<li><a href="${a.url}" target="_blank" rel="noopener">${t}</a> <span class="meta">${a.source || ""}</span></li>`
                : `<li>${t}</li>`;
            }).join("");
            const etfs = (r.etfs || []).join(" ");
            nrPanel.innerHTML = `<div><b>${r.sector || ""}</b>`
              + (r.confirm_label ? ` · ${r.confirm_label}` : "")
              + (r.tone_label ? ` · ${r.tone_label}` : "")
              + `</div>`
              + (r.mainline_note ? `<div class="meta">${r.mainline_note}</div>` : "")
              + (r.action_hint ? `<div style="margin:4px 0">${r.action_hint}</div>` : "")
              + (etfs ? `<div class="meta">ETF：<code>${etfs}</code> <button type="button" class="copy-btn" id="nrCopyEtf">复制</button></div>` : "")
              + (arts ? `<ul>${arts}</ul>` : "<div class='meta'>暂无新闻链接</div>");
            nrPanel.style.display = "block";
            nrPanel.dataset.open = String(i);
            const cp = document.getElementById("nrCopyEtf");
            if (cp && etfs) {
              cp.onclick = async () => {
                try { await navigator.clipboard.writeText(etfs); cp.textContent = "已复制"; }
                catch (_) { cp.textContent = "复制失败"; }
              };
            }
          };
        });
      } else {
        nrStrip.innerHTML = `<div class="meta">暂无热板块 · ${st || "无数据"}</div>`;
        if (nrPanel) { nrPanel.style.display = "none"; nrPanel.innerHTML = ""; nrPanel.dataset.open = ""; }
      }
      if (nrNote) {
        if (nr.mainline_note) {
          nrNote.hidden = false;
          nrNote.textContent = nr.mainline_note;
        } else {
          nrNote.hidden = true;
          nrNote.textContent = "";
        }
      }
      const chk = (nr.checklist || []).slice(0, 3);
      if (nrCheck) {
        if (chk.length) {
          nrCheck.hidden = false;
          nrCheck.innerHTML = `<div class="nr-check-hd">盘前清单（软）`
            + ` <button type="button" class="copy-btn" id="nrCopyChk">复制</button></div>`
            + chk.map((c) => {
              const pct = c.board_pct == null ? ""
                : ` ${Number(c.board_pct) >= 0 ? "+" : ""}${Number(c.board_pct).toFixed(1)}%`;
              const etf = (c.etfs || [])[0] || "";
              return `<div class="nr-check-item"><b>${c.sector || ""}</b>`
                + (c.confirm_label ? ` · ${c.confirm_label}` : "")
                + pct
                + (etf ? ` · ${etf}` : "")
                + (c.action_hint ? `<div class="meta">${c.action_hint}</div>` : "")
                + `</div>`;
            }).join("");
          const copyChk = document.getElementById("nrCopyChk");
          if (copyChk) {
            copyChk.onclick = async () => {
              const text = chk.map((c) => {
                const etf = (c.etfs || [])[0] || "";
                return `${c.sector || ""} ${c.confirm_label || ""} ${etf} ${c.action_hint || ""}`.trim();
              }).join("\n");
              try { await navigator.clipboard.writeText(text); copyChk.textContent = "已复制"; }
              catch (_) { copyChk.textContent = "复制失败"; }
            };
          }
        } else {
          nrCheck.hidden = true;
          nrCheck.innerHTML = "";
        }
      }
    }
  }
}
