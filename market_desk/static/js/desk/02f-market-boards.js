/** Paint lifecycle, similar days, contagion, history, board cards, watch pool, watchlist, and blacklist. */
function renderMarketBoards(d, v) {
  const life = d.mainline_lifecycle || {};
  document.getElementById("lifeNote").textContent = life.note
    || "板块阶段雷达 · 非买卖指令；当前主线会高亮";
  const curLifeName = ((d.verdict || {}).mainline || {}).name || "";
  const sim = d.similar_days || {};
  document.getElementById("similarNote").textContent = sim.note || "近端同相位日对照次日冷热";
  const peers = sim.peers || [];
  document.getElementById("similarBox").innerHTML =
    (sim.bias ? `<div class="bias">${sim.bias}</div>` : `<div class="meta">暂无明确倾向</div>`)
    + (peers.length
      ? `<ul>` + peers.slice(0, 5).map((p) =>
          `<li>${p.date || "—"} 温度${p.temperature ?? "—"} 涨停${p.zt ?? "—"}`
          + (p.next_date
            ? ` → ${p.next_date} ${p.next_phase || ""} 温${p.next_temperature ?? "—"} 涨停${p.next_zt ?? "—"}`
            : " → 次日待记")
          + `</li>`).join("") + `</ul>`
      : `<div class="meta">样本不足时先积累日级快照</div>`);
  const lifeCard = (b) => {
    const spark = (b.spark || []).map(h => `<i style="height:${Math.max(8, h)}%"></i>`).join("");
    const isMain = curLifeName && b.name === curLifeName;
    return `<article class="life-item${isMain ? " is-main" : ""}">
          <div class="hd">
            <div class="nm">${b.name || "—"}</div>
            <span class="tag ${tagClass(b.status)}">${termHtml(b.status || "观察")}</span>
          </div>
          <div class="meta">${b.headline || ""}</div>
          <div class="spark">${spark}</div>
          <div class="meta">${termHtml("聚集度")} ${b.cluster || (b.zt_n ?? 0)} · 热度日 ${b.hot_days ?? 0}</div>
          <div class="meta" style="margin-top:4px">${termHtml("总龙头")} ${b.leader_name || "—"} ${b.leader_boards || 0}板 · ${fmtPct(b.pct)}</div>
          ${b.live_hint ? `<div class="meta" style="margin-top:4px;color:var(--amber, #e0a84a)">⏱ ${b.live_hint}</div>` : ""}
          <div class="note" style="margin-top:6px">${b.note || ""}</div>
        </article>`;
  };
  const freshRows = life.fresh || [];
  const freshHtml = freshRows.length
    ? `<div class="meta" style="margin-top:6px">盘中新苗（未计入，收盘确认）：`
      + freshRows.map((f) => `${f.name || "—"} ${fmtPct(f.pct)}${f.zt_n ? ` · ${f.zt_n}停` : ""}`).join("；")
      + `</div>`
    : "";
  const lifeCol = (cls, title, rows, extra = "") => `
        <div class="life-col ${cls}">
          <h3>${title} · ${(rows || []).length}</h3>
          ${(rows || []).map(lifeCard).join("") || `<div class="meta">暂无</div>`}
          ${extra}
        </div>`;
  document.getElementById("lifeGrid").innerHTML =
    lifeCol("start", "萌芽", life.starting, freshHtml) +
    lifeCol("run", "主升", life.ongoing) +
    lifeCol("end", "衰退", life.ending);
  const cg = d.contagion || {};
  const cgEl = document.getElementById("contagion");
  cgEl.className = "contagion " + (cg.on ? "on" : "off");
  cgEl.textContent = cg.text || "暂无板块级跌停传染";
  document.getElementById("hist").innerHTML = (d.history || []).map(r => `
        <tr${r.breadth_degraded ? ' class="degraded-day"' : ""}>
          <td>${r.trade_date || ""}</td>
          <td>${r.event || ""}</td>
          <td><span class="phase-pill p-${r.phase || ""}">${termHtml(r.phase || "")}</span></td>
          <td>${r.ups != null ? r.ups : "—"} / ${r.downs != null ? r.downs : "—"}</td>
          <td class="up">${r.zt ?? ""}</td>
          <td class="down">${r.dt ?? ""}</td>
          <td>${r.zb_rate ?? ""}</td>
          <td>${r.height ?? ""}</td>
          <td>${r.promotion ?? ""}</td>
          <td>${r.premium ?? ""}</td>
          <td>${r.amount_yi != null ? r.amount_yi : "—"}</td>
        </tr>`).join("");
  const cardHtml = (b, pin) => {
    const ice = !!b.ice;
    const tags = (b.tags || []).map(t =>
      `<span class="${miniClass(t.k, t.on)}">${termHtml(t.k)}</span>`
    ).join("");
    const spark = (b.spark || []).map(h => `<i style="height:${Math.max(8, h)}%"></i>`).join("");
    const lead = ice
      ? `最弱观察 · ${termHtml("跌停")} ${b.dt_n ?? 0} · 涨${b.up_count ?? 0} / 跌${b.down_count ?? 0}`
      : `${termHtml("总龙头")} ${b.leader_name || "—"} ${b.leader_boards || 0}板`
        + (b.slot_name ? ` · ${termHtml("卡位龙")} ${b.slot_name} ${b.slot_boards || 0}板` : "");
    const prefix = pin ? (b.pin_label + " · ") : "";
    const bk = String(b.bk || "");
    const favOn = !!b.in_favorite;
    const favBtn = bk.toUpperCase().startsWith("BK")
      ? (favOn
        ? `<button type="button" class="fav-btn on" data-bk="${bk}" data-id="${b.favorite_id || ""}">取消看好</button>`
        : `<button type="button" class="fav-btn" data-bk="${bk}" data-name="${String(b.name || "").replace(/"/g, "&quot;")}" data-kind="${b.kind || ""}">加入看好</button>`)
      : "";
    return `<article class="board tone-${b.tone || "slate"} ${statusClass(b.status)}${favOn ? " is-fav" : ""}">
          <div class="hd">
            <div>
              <h3${b.sina_name ? ` title="新浪「${String(b.sina_name).replace(/"/g, "&quot;")}」→ 东财${b.alias_approx ? "（近似映射，不计拥挤度）" : ""}"` : ""}>${prefix}${b.name}${b.alias_approx ? "≈" : ""}</h3>
              <div class="meta">${b.headline || b.status || "观察"}${bk ? ` · ${bk}` : ""}</div>
            </div>
            <div>
              <div>${fmtPct(b.pct)}</div>
              <span class="tag ${tagClass(b.status)}">${termHtml(b.status || "观察")}</span>
            </div>
          </div>
          <div class="spark">${spark}</div>
          <div class="meta">${termHtml("聚集度")} ${b.cluster || (b.zt_n ?? 0)}</div>
          <div class="meta" style="margin-top:6px">${lead}</div>
          <div class="tag-row">${tags}</div>
          <div class="meta" style="margin-top:4px">${
        (b.rep_label ? `信誉 ${b.rep_label}${b.rep_adj ? ` (${b.rep_adj > 0 ? "+" : ""}${b.rep_adj})` : ""}${b.rep_thin ? "·薄" : ""}` : "")
        + (b.rep_persist_rate != null
          ? `${b.rep_label ? " · " : ""}续热 ${b.rep_persist_rate}%（n=${b.rep_sample_n || 0}）`
          : "")
        + ((b.similar_peers || []).length
          ? `${(b.rep_label || b.rep_persist_rate != null) ? " · " : ""}相似 ${(b.similar_peers || []).slice(0, 2).map((p) => {
              const tip = (p.why || []).slice(0, 2).join("·");
              return tip ? `${p.name}(${tip})` : p.name;
            }).join("/")}`
          : "")
      }</div>
          <div class="note">${b.note || ""}</div>
          <ul class="members">${(b.members || []).slice(0,4).map(x =>
        `<li><span>${x.name}</span><span>${fmtPct(x.pct)}</span></li>`).join("")}</ul>
          ${favBtn}
        </article>`;
  };
  const favEl = document.getElementById("favBoards");
  if (favEl) {
    favEl.innerHTML = (d.favorite_boards || []).map(b => cardHtml(b, false)).join("")
      || "<div class='meta'>还没有看好板块。在热点/冰点/置顶卡片点「加入看好」。</div>";
  }
  document.getElementById("pins").innerHTML = (d.pin_boards || []).map(b => cardHtml(b, true)).join("") || "<div class='meta'>置顶板块待刷新</div>";
  document.getElementById("ice").innerHTML = (d.ice_boards || []).map(b => cardHtml(b, false)).join("") || "<div class='meta'>冰点板块待刷新</div>";
  document.getElementById("boards").innerHTML = (d.hot_boards || []).map(b => cardHtml(b, false)).join("") || "<div class='meta'>热点待刷新</div>";
  const watchGroups = ["涨停", "炸板", "跌停", "高换手"];
  const watchByGroup = {};
  (d.watch || []).forEach((w) => {
    const g = w.group || "其他";
    (watchByGroup[g] || (watchByGroup[g] = [])).push(w);
  });
  let watchHtml = `<li class="hdrow"><span>名称</span><span>代码</span><span>涨跌</span><span>标签</span><span>原因</span><span>板块</span><span></span></li>`;
  let watchCount = 0;
  watchGroups.concat(Object.keys(watchByGroup).filter((g) => !watchGroups.includes(g))).forEach((g) => {
    const rows = watchByGroup[g] || [];
    if (!rows.length) return;
    watchHtml += `<li class="group-row">${g} · ${rows.length}</li>`;
    rows.forEach((w) => {
      watchCount += 1;
      const mlHit = w.board_match === true;
      const boardBit = w.board_text
        ? `${w.board_text}${w.vs_mainline ? " · " + w.vs_mainline : ""}`
        : "—";
      const wlBtn = w.in_watchlist
        ? `<button type="button" class="wl-add in" disabled>已自选</button>`
        : `<button type="button" class="wl-add" data-code="${w.code || ""}" data-name="${(w.name || "").replace(/"/g, "")}" data-suggest="${w.suggest_price ?? ""}" data-stop="${w.stop_price ?? ""}" data-chase="${w.chase_price ?? ""}" title="带入粗略建议/止损/不追价带">自选</button>`;
      watchHtml += `<li class="${mlHit ? "is-ml" : ""}">
            <span class="wl-name">${tickerHtml(w.name, w.code)}</span>
            <span class="wl-code">${w.code || ""}</span>
            <span class="wl-pct">${fmtPct(w.pct)}</span>
            <span class="wl-tag tag ${tagClass(w.tag)}">${termHtml(w.tag || "观察")}</span>
            <span class="wl-reason">${reasonTerm(w.reason)}</span>
            <span class="wl-board meta">${boardBit}</span>
            <span class="wl-act">${wlBtn}</span>
          </li>`;
    });
  });
  document.getElementById("watchList").innerHTML =
    watchCount
      ? watchHtml
      : `<li class="hdrow"><span>名称</span><span>代码</span><span>涨跌</span><span>标签</span><span>原因</span><span>板块</span><span></span></li><li><span class="meta">暂无异动</span></li>`;
  document.getElementById("wlBody").innerHTML = (d.watchlist || []).map((w) => {
    const t = (w.first_seen_at || "").slice(0, 16);
    const obs = w.observe_status || "观察中";
    const tone = w.observe_tone || "slate";
    const obsCls = tone === "green" ? "obs-ok"
      : tone === "red" ? "obs-bad"
      : tone === "amber" ? "obs-warn"
      : "obs-muted";
    const obsTip = (w.observe_note || "").replace(/"/g, "&quot;");
    return `<tr>
          <td data-label="名称">${tickerHtml(w.name, w.code)}</td>
          <td data-label="代码">${w.code || ""}</td>
          <td data-label="首次">${t || "—"}</td>
          <td data-label="建议价">${w.suggest_price ?? "—"}</td>
          <td data-label="止损">${w.stop_price ?? "—"}</td>
          <td data-label="不追">${w.chase_price ?? "—"}</td>
          <td data-label="现价">${w.last ?? "—"}</td>
          <td data-label="当日%">${fmtPct(w.last_pct)}</td>
          <td data-label="观察" title="${obsTip}"><span class="tag ${obsCls}">${obs}</span></td>
          <td class="m-actions"><button type="button" class="pos-del wl-del" data-id="${w.id}">删除</button></td>
        </tr>`;
  }).join("") || `<tr><td colspan="10" class="meta">暂无自选。上方填代码加入。</td></tr>`;
  const bl = d.stock_blacklist || {};
  const blItems = bl.items || [];
  const blMeta = document.getElementById("blMeta");
  if (blMeta) {
    const bits = [];
    if (bl.flagged_today != null) bits.push(`今日高开低走 ${bl.flagged_today} 只`);
    if (bl.scanned != null) bits.push(`已扫描 ${bl.scanned}`);
    bits.push(`黑名单 ${blItems.length} 只 · 不进个股推荐`);
    blMeta.textContent = bits.join(" · ")
      + "。近10日多次自动拉黑；自动项连续正常约3日解除；手动项需点移除。";
  }
  const blBody = document.getElementById("blBody");
  if (blBody) {
    blBody.innerHTML = blItems.map((b) => {
      const src = b.source === "manual" ? "手动" : "自动";
      return `<tr>
            <td data-label="名称">${tickerHtml(b.name, b.code)}</td>
            <td data-label="代码">${b.code || ""}</td>
            <td data-label="来源">${src}</td>
            <td data-label="原因" class="meta">${b.reason || b.note || "—"}</td>
            <td data-label="打击">${b.strike_n ?? "—"}</td>
            <td data-label="正常日">${b.clean_streak ?? 0}</td>
            <td data-label="加入" class="meta">${(b.blocked_at || "").slice(0, 16) || "—"}</td>
            <td class="m-actions"><button type="button" class="pos-del bl-del" data-code="${b.code || ""}">移除</button></td>
          </tr>`;
    }).join("") || `<tr><td colspan="8" class="meta">暂无黑名单。可手动加入，或等系统识别常高开低走。</td></tr>`;
  }
}
