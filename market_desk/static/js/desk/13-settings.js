async function openSettings() {
  const panel = document.getElementById("settingsPanel");
  try {
    const r = await fetch("/api/settings");
    const d = await r.json();
    const s = d.settings || {};
    window.__lastSettings = s;
    document.getElementById("setRefresh").value = s.refresh_seconds ?? 20;
    document.getElementById("setAuctionRefresh").value = s.auction_refresh_seconds ?? 5;
    document.getElementById("setIdle").value = s.idle_seconds ?? 60;
    document.getElementById("setSticky").value = s.sticky_margin ?? 12;
    document.getElementById("setSwitch").value = s.switch_min_seconds ?? 300;
    document.getElementById("setToastCd").value = s.toast_cooldown ?? 180;
    document.getElementById("setToastOn").checked = !!s.toast_enabled;
    document.getElementById("setDecisionOn").checked = s.decision_alerts !== false;
    document.getElementById("setAlertMode").value = s.alert_mode || "traded_watch";
    document.getElementById("setHitMode").value = s.hit_rate_mode || "traded";
    const ocEl = document.getElementById("setOutcomeStd");
    if (ocEl) ocEl.value = s.outcome_standard || "classic";
    const afo = document.getElementById("setAdaptFollowOc");
    if (afo) afo.checked = !!s.adapt_follow_outcome;
    const rsEl = document.getElementById("setReadyStyle");
    if (rsEl) rsEl.value = s.ready_style || "band";
    document.getElementById("setPanicT").value = s.phase_panic_temp ?? 28;
    document.getElementById("setFermentT").value = s.phase_ferment_temp ?? 45;
    document.getElementById("setClimaxT").value = s.phase_climax_temp ?? 72;
    document.getElementById("setOpenMute").value = s.open_mute_minutes ?? 5;
    const sowEl = document.getElementById("setSellOpenWatch");
    if (sowEl) sowEl.value = s.sell_open_watch_minutes ?? 15;
    document.getElementById("setTailMute").value = s.tail_mute_minutes ?? 30;
    document.getElementById("setLossCap").value = s.daily_loss_cap_pct ?? -3;
    document.getElementById("setCoolN").value = s.cool_after_losses ?? 3;
    document.getElementById("setTargetCost").value = s.target_total_cost ?? 50000;
    document.getElementById("setEquity").value = s.account_equity ?? s.target_total_cost ?? 50000;
    document.getElementById("setRiskPct").value = s.risk_pct_per_trade ?? 1;
    document.getElementById("setMinMv").value = s.min_stock_mv_yi ?? 120;
    document.getElementById("setEqualW").checked = s.equal_weight_target !== false;
    document.getElementById("setBatch").checked = s.batch_plan !== false;
    document.getElementById("setAutoBackup").checked = s.auto_backup !== false;
    const keepEl = document.getElementById("setBackupKeep");
    if (keepEl) keepEl.value = s.backup_keep ?? 30;
    const nrOn = document.getElementById("setNewsRadarOn");
    if (nrOn) nrOn.checked = !!s.news_radar_enabled;
    const nrUrl = document.getElementById("setNewsRadarUrl");
    if (nrUrl) nrUrl.value = s.news_radar_url || "http://127.0.0.1:8770";
    const nrProbe = document.getElementById("btnNrProbe");
    if (nrProbe && !nrProbe.dataset.bound) {
      nrProbe.dataset.bound = "1";
      nrProbe.onclick = async () => {
        const msg = document.getElementById("nrProbeMsg");
        const url = (document.getElementById("setNewsRadarUrl")?.value || "http://127.0.0.1:8770").replace(/\/$/, "");
        if (msg) msg.textContent = "检测中…";
        try {
          const resp = await fetch(url + "/api/health", { signal: AbortSignal.timeout(4000) });
          const j = await resp.json();
          if (msg) msg.textContent = resp.ok && j.ok
            ? `在线 · ${j.updated_at || "ok"} · 板块${j.sectors ?? "—"}`
            : "响应异常";
        } catch (e) {
          if (msg) msg.textContent = "离线/超时（确认 :8770 已启动）";
        }
      };
    }
    window.__portableSettings = d.portable || null;
    window.__settingsPresets = d.presets || null;
  } catch (e) {}
  // Per-user Server酱 (separate from shared runtime settings).
  try {
    const scEl = document.getElementById("setScKey");
    const scOn = document.getElementById("setScOn");
    const scClear = document.getElementById("setScClear");
    const scHint = document.getElementById("setScHint");
    const morningEl = document.getElementById("setMorningPush");
    if (scEl) scEl.value = "";
    if (scClear) scClear.checked = false;
    const sr = await fetch("/api/me/serverchan", { credentials: "same-origin" });
    const sd = await sr.json();
    const sc = (sd && sd.serverchan) || {};
    if (scHint) {
      if (!sd.ok) {
        scHint.textContent = sd.detail || "无法加载微信推送配置（游客不可用）";
      } else if (!sc.allowed) {
        scHint.textContent = authUser && authUser.role === "admin"
          ? "本账号已在「账号管理」禁推送；点自己那行「允推送」即可恢复。"
          : "管理员未允许本账号微信推送；可先填 Key，待开通后再开。";
      } else {
        scHint.textContent = sc.configured
          ? `已配置 Key：${sc.sendkey_masked || "SCT***"} · 买卖/LHB/收盘一页纸/早决策；无 Key 不推`
          : "未配置 Key · 填 SCT… 后勾选开启；推买卖、LHB、收盘一页纸、可选早决策";
      }
    }
    if (scOn) {
      scOn.checked = !!sc.on;
      scOn.disabled = !sc.allowed;
    }
    if (morningEl) {
      morningEl.checked = !!s.morning_push;
      morningEl.disabled = !sc.allowed;
    }
    const sellOnlyEl = document.getElementById("setScSellOnly");
    if (sellOnlyEl) {
      sellOnlyEl.checked = !!s.serverchan_sell_only;
      sellOnlyEl.disabled = !sc.allowed;
    }
    if (scEl) scEl.disabled = false;
    if (scHint && sc.allowed) {
      scHint.textContent = sc.configured
        ? `已配置 Key：${sc.sendkey_masked || "SCT***"} · `
          + (s.serverchan_sell_only ? "仅止损/必卖(+EOD/LHB)" : "买卖/LHB/收盘/早决策")
          + "；无 Key 不推"
        : "未配置 Key · 填 SCT… 后勾选开启；可勾「只推止损」静音可买";
    }
  } catch (e) {
    const scHint = document.getElementById("setScHint");
    if (scHint) scHint.textContent = "微信推送配置加载失败";
  }
  try {
    const br = await fetch("/api/backup/auto?limit=6", { credentials: "same-origin" });
    const bd = await br.json().catch(() => ({}));
    const hint = document.getElementById("setBackupHint");
    if (hint) {
      if (br.ok && bd.ok) {
        const items = bd.items || [];
        if (!items.length) {
          hint.innerHTML = `尚无自动备份（收盘后写入；保留 ${bd.keep ?? 30} 份）`;
        } else {
          hint.innerHTML = `最近备份（点名下载）：${items.slice(0, 4).map((x) =>
            `<a href="/api/backup/auto/download?name=${encodeURIComponent(x.name)}" download>${x.name}</a>`
          ).join(" · ")}（保留 ${bd.keep ?? "—"} 份）`
            + ` · <button type="button" class="rev-col-btn" id="btnDbIntegrity">库完整性</button>`;
          document.getElementById("btnDbIntegrity")?.addEventListener("click", async () => {
            try {
              const ir = await fetch("/api/ops/db-integrity", { credentials: "same-origin" });
              const id = await ir.json().catch(() => ({}));
              alert(id.ok === false || id.detail === undefined
                ? "探测失败"
                : (id.ok ? "integrity: ok" : ("损坏: " + (id.detail || "").slice(0, 200))));
            } catch (e) {
              alert("探测请求失败");
            }
          });
        }
      } else {
        hint.textContent = "备份列表仅管理员可见；收盘仍会按全局开关备份。";
      }
    }
  } catch (e) {}
  panel.hidden = false;
}
document.getElementById("settingsBtn").addEventListener("click", (ev) => {
  ev.stopPropagation();
  openSettings();
});
const settingsBtnM = document.getElementById("settingsBtnM");
if (settingsBtnM) {
  settingsBtnM.addEventListener("click", (ev) => {
    ev.stopPropagation();
    openSettings();
  });
}
document.getElementById("settingsClose").addEventListener("click", (ev) => {
  ev.stopPropagation();
  document.getElementById("settingsPanel").hidden = true;
});
document.addEventListener("click", (ev) => {
  const sPanel = document.getElementById("settingsPanel");
  if (!sPanel || sPanel.hidden) return;
  if (
    ev.target.closest("#settingsPanel")
    || ev.target.closest("#settingsBtn")
    || ev.target.closest("#settingsBtnM")
  ) {
    return;
  }
  sPanel.hidden = true;
});
document.getElementById("settingsSave").addEventListener("click", async () => {
  const body = {
    refresh_seconds: Number(document.getElementById("setRefresh").value),
    auction_refresh_seconds: Number(document.getElementById("setAuctionRefresh").value),
    idle_seconds: Number(document.getElementById("setIdle").value),
    sticky_margin: Number(document.getElementById("setSticky").value),
    switch_min_seconds: Number(document.getElementById("setSwitch").value),
    toast_cooldown: Number(document.getElementById("setToastCd").value),
    toast_enabled: !!document.getElementById("setToastOn").checked,
    decision_alerts: !!document.getElementById("setDecisionOn").checked,
    alert_mode: document.getElementById("setAlertMode").value,
    hit_rate_mode: document.getElementById("setHitMode").value,
    outcome_standard: document.getElementById("setOutcomeStd")?.value || "classic",
    adapt_follow_outcome: !!document.getElementById("setAdaptFollowOc")?.checked,
    ready_style: document.getElementById("setReadyStyle")?.value || "band",
    phase_panic_temp: Number(document.getElementById("setPanicT").value),
    phase_ferment_temp: Number(document.getElementById("setFermentT").value),
    phase_climax_temp: Number(document.getElementById("setClimaxT").value),
    open_mute_minutes: Number(document.getElementById("setOpenMute").value),
    sell_open_watch_minutes: Number(document.getElementById("setSellOpenWatch")?.value ?? 15),
    tail_mute_minutes: Number(document.getElementById("setTailMute").value),
    daily_loss_cap_pct: Number(document.getElementById("setLossCap").value),
    cool_after_losses: Number(document.getElementById("setCoolN").value),
    target_total_cost: Number(document.getElementById("setTargetCost").value),
    account_equity: Number(document.getElementById("setEquity").value),
    risk_pct_per_trade: Number(document.getElementById("setRiskPct").value),
    min_stock_mv_yi: Number(document.getElementById("setMinMv").value),
    equal_weight_target: !!document.getElementById("setEqualW").checked,
    batch_plan: !!document.getElementById("setBatch").checked,
    auto_backup: !!document.getElementById("setAutoBackup").checked,
    backup_keep: Number(document.getElementById("setBackupKeep")?.value || 30),
    morning_push: !!document.getElementById("setMorningPush")?.checked,
    serverchan_sell_only: !!document.getElementById("setScSellOnly")?.checked,
    news_radar_enabled: !!document.getElementById("setNewsRadarOn")?.checked,
    news_radar_url: (document.getElementById("setNewsRadarUrl")?.value || "").trim(),
  };
  const r = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    alert("保存失败");
    return;
  }
  // Personal Server酱 (ignore errors for guests).
  try {
    const scBody = {
      on: !!document.getElementById("setScOn").checked,
      clear_key: !!document.getElementById("setScClear").checked,
    };
    const keyVal = (document.getElementById("setScKey").value || "").trim();
    if (keyVal) scBody.sendkey = keyVal;
    const sr = await fetch("/api/me/serverchan", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scBody),
    });
    const sd = await sr.json().catch(() => ({}));
    if (!sr.ok || sd.ok === false) {
      alert("运行参数已保存；微信推送未保存：" + (sd.detail || "请检查是否已允许推送 / SendKey"));
      return;
    }
  } catch (e) {}
  document.getElementById("settingsPanel").hidden = true;
  alert("已保存。刷新间隔下一轮生效。");
});

async function applySettingsPreset(name) {
  const zh = { defensive: "防守", balanced: "平衡", aggressive: "进攻" };
  if (!confirm(`应用「${zh[name] || name}」个人风控预设？\n将覆盖风险%/亏损帽/目标仓/静音等个人项。`)) return;
  try {
    const r = await fetch("/api/settings/preset?name=" + encodeURIComponent(name), {
      method: "POST",
      credentials: "same-origin",
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(typeof d.detail === "string" ? d.detail : "预设应用失败");
      return;
    }
    await openSettings();
    alert(`已应用「${zh[name] || name}」预设，可再微调后点保存。`);
  } catch (e) {
    alert("预设请求失败");
  }
}
document.getElementById("setPresetDef")?.addEventListener("click", () => applySettingsPreset("defensive"));
document.getElementById("setPresetBal")?.addEventListener("click", () => applySettingsPreset("balanced"));
document.getElementById("setPresetAgg")?.addEventListener("click", () => applySettingsPreset("aggressive"));
document.getElementById("settingsExport")?.addEventListener("click", async () => {
  try {
    const r = await fetch("/api/settings");
    const d = await r.json();
    const portable = d.portable || window.__portableSettings || {};
    const blob = new Blob([JSON.stringify({ settings: portable, exported_at: new Date().toISOString() }, null, 2)], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "market-desk-settings.json";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) {
    alert("导出失败");
  }
});
document.getElementById("settingsImport")?.addEventListener("click", () => {
  document.getElementById("settingsImportFile")?.click();
});
document.getElementById("settingsImportFile")?.addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  try {
    const text = await file.text();
    const raw = JSON.parse(text);
    const payload = (raw && typeof raw.settings === "object") ? raw : { settings: raw };
    if (!confirm("导入可移植参数并覆盖当前个人项？")) return;
    const r = await fetch("/api/settings/import", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      alert(typeof d.detail === "string" ? d.detail : "导入失败");
      return;
    }
    await openSettings();
    alert("已导入：" + ((d.imported || []).join("、") || "完成"));
  } catch (e) {
    alert("导入失败：JSON 无效或网络错误");
  }
});
