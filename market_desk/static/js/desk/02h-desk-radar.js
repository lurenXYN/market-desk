/** Paint the stage-3 radar strip: crowding, broad-ETF rescue pulse, narrative shadow clusters. */
function renderDeskRadar(d) {
  const strip = document.getElementById("radarStrip");
  const tags = document.getElementById("radarStripTags");
  if (!strip || !tags) return;
  const radar = d.radar || {};
  const crowd = radar.crowding || {};
  const pulse = radar.etf_pulse || {};
  const narr = radar.narrative || {};
  const attr = (s) => String(s || "").replace(/"/g, "&quot;");
  const shares = crowd.shares || {};
  const modes = crowd.modes || {};
  const confirm = crowd.confirm || {};
  const touched = new Set([...(crowd.banned || []), ...(crowd.soft || [])]);
  const bits = [];
  (crowd.extreme || []).forEach((name) => {
    const share = shares[name] != null ? ` ${shares[name]}%` : "";
    const hard = modes[name] !== "soft";
    const why = (confirm[name] || []).join("、");
    const tip = hard
      ? `${name} 成交占比${share}：极端拥挤且${why || "见高潮"}，高潮禁开新仓，警惕主升鱼尾`
      : `${name} 成交占比${share}：极端拥挤，未见高潮确认（相位高潮 / 滞涨 / A杀 / 天地 / 翻绿 / 炸板率高 任一出现即升级为禁开）\n只做完全确认的 ready，仓位减半`;
    const label = hard ? "极端拥挤·禁开" : "极端拥挤·半仓";
    bits.push(
      `<span class="radar-chip ${hard ? "crowd-x" : "crowd-xs"}" title="${attr(tip + (touched.has(name) ? "\n（已作用于该板块推荐卡）" : ""))}">⚠ ${label} ${name}${share}</span>`
    );
  });
  (crowd.warn || []).slice(0, 3).forEach((name) => {
    const share = shares[name] != null ? ` ${shares[name]}%` : "";
    bits.push(`<span class="radar-chip crowd-w" title="板块拥挤：ready 仓位 ×0.8">拥挤 ${name}${share}</span>`);
  });
  if (crowd.ok && !(crowd.extreme || []).length && !(crowd.warn || []).length) {
    const top = (crowd.top || [])[0];
    if (top) {
      bits.push(
        `<span class="radar-chip" title="全市场成交 ${crowd.market_yi || "—"} 亿">成交最集中 ${top.name} ${top.share}%</span>`
      );
    }
  }
  if (pulse.event) {
    const tip = `${pulse.label || ""}${pulse.phase_hint ? "\n" + pulse.phase_hint : ""}\n只提示，不改 ready / 动作`;
    bits.push(
      `<span class="radar-chip pulse-on" title="${attr(tip)}">宽基托底脉冲 ${pulse.event_at || ""}${pulse.phase_hint ? " · 恐慌拐点候选" : ""}</span>`
    );
  } else if (pulse.latest && pulse.latest.at) {
    const l = pulse.latest;
    bits.push(
      `<span class="radar-chip" title="量比 ${l.ratio}× · 拉升 ${l.px_move}%">ETF放量 ${l.name || l.code} ${l.at}</span>`
    );
  }
  (narr.clusters || []).filter((c) => c.hidden).slice(0, 2).forEach((c) => {
    const tip = `影子模式（只展示与记录）\n概念：${(c.concepts || []).join("、")}`
      + `\n行业：${(c.industry_names || []).join("、")}\n龙头：${(c.leaders || []).join("、")}`;
    bits.push(
      `<span class="radar-chip narr" title="${attr(tip)}">图谱·影子 ${c.label} ${c.zt_n}停/${c.industries}行业`
      + `${c.first_seen ? " 首见" + c.first_seen : ""}</span>`
    );
  });
  const stats = narr.stats || {};
  if (bits.length && stats.days) {
    const lead = stats.median_lead_min != null ? `中位提前 ${stats.median_lead_min} 分` : "尚无命中";
    bits.push(
      `<span class="radar-chip meta" title="影子账本：${stats.days} 天 · 隐形簇 ${stats.sightings || 0} · 后成主线 ${stats.hits || 0}">${lead}</span>`
    );
  }
  strip.hidden = !bits.length;
  strip.classList.toggle("crowd-alert", (crowd.extreme || []).length > 0);
  tags.innerHTML = bits.join("");
}
