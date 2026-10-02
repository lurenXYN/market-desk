// Sound alerts: Web Audio tones on stop / risk / buy edges. Preferences live in
// localStorage (per device); browsers only start audio after a user gesture.
const SOUND_PREF_KEY = "desk_sound_prefs";
const SOUND_ACK_PREFIX = "desk_sound_ack:";
const SOUND_REPEAT_MS = 60000;
const SOUND_DEFAULTS = { on: false, vol: 0.6, stop: true, risk: true, buy: false, repeat: true };
const SOUND_RANK = { "": 0, buy: 1, risk: 2, stop: 3 };
// [offset s, frequency Hz, duration s, oscillator type]
const SOUND_PATTERNS = {
  stop: [[0, 988, 0.11, "square"], [0.15, 988, 0.11, "square"], [0.30, 988, 0.11, "square"]],
  risk: [[0, 660, 0.18, "triangle"], [0.24, 880, 0.22, "triangle"]],
  buy: [[0, 1318, 0.16, "sine"]],
};
let soundCtx = null;
let soundPrefs = soundLoadPrefs();
let soundSeenToasts = null;
let soundStopIds = null;
let soundStopsNow = [];
let soundLastStopPlay = 0;
let soundUid = null;
let soundDay = "";

/** Load device-local sound preferences merged over the defaults. */
function soundLoadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(SOUND_PREF_KEY) || "{}");
    return Object.assign({}, SOUND_DEFAULTS, raw && typeof raw === "object" ? raw : {});
  } catch (e) {
    console.warn("sound prefs unreadable, using defaults", e);
    return Object.assign({}, SOUND_DEFAULTS);
  }
}

/** Persist the current sound preferences for this device. */
function soundSavePrefs() {
  try {
    localStorage.setItem(SOUND_PREF_KEY, JSON.stringify(soundPrefs));
  } catch (e) {
    console.warn("sound prefs not saved", e);
  }
}

/** Create or resume the shared AudioContext; returns null when Web Audio is unavailable. */
function soundUnlock() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!soundCtx) soundCtx = new AC();
  if (soundCtx.state === "suspended") {
    soundCtx.resume().then(paintSoundBtn).catch(() => {});
  }
  return soundCtx;
}

/** Return true once the browser has allowed audio output on this page. */
function soundReady() {
  return !!(soundCtx && soundCtx.state === "running");
}

/** Schedule one enveloped oscillator tone on the audio graph. */
function soundTone(ctx, t0, freq, dur, type, vol) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  gain.gain.setValueAtTime(0.0001, t0);
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, vol), t0 + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

/**
 * Play the pattern for ``kind`` ("stop" | "risk" | "buy").
 * Without ``force`` it honours the master switch and the per-kind toggle.
 * Returns true when the tones were scheduled.
 */
function soundPlay(kind, force) {
  const p = soundPrefs;
  const pattern = SOUND_PATTERNS[kind];
  if (!pattern) return false;
  if (!force && (!p.on || !p[kind])) return false;
  const vol = Math.max(0, Math.min(1, Number(p.vol) || 0));
  if (vol <= 0) return false;
  const ctx = soundUnlock();
  if (!ctx || ctx.state !== "running") return false;
  const t = ctx.currentTime + 0.02;
  pattern.forEach(([dt, freq, dur, type]) => {
    // Square waves sound far louder than sines at equal gain.
    soundTone(ctx, t + dt, freq, dur, type, vol * (type === "square" ? 0.35 : 0.8));
  });
  return true;
}

/** Map a toast dedupe key to its sound kind, or "" when it stays silent. */
function soundKindForToast(key) {
  const k = String(key || "");
  if (k.startsWith("sell:stop:") || k.startsWith("band:stop:") || k.startsWith("wl:stop:")) return "stop";
  if (k === "phase:恐慌") return "stop";
  if (k.startsWith("exit:") || k.startsWith("decline:") || k.startsWith("crowd:hard:")) return "risk";
  if (k.startsWith("buy:") || k.startsWith("fly:")) return "buy";
  return "";
}

/** Return the localStorage key holding today's acknowledged stop ids for the viewer. */
function soundAckKey() {
  return SOUND_ACK_PREFIX + (soundUid == null ? "anon" : soundUid) + ":" + soundDay;
}

/** Return the set of stop ids acknowledged today. */
function soundAckSet() {
  try {
    return new Set(JSON.parse(localStorage.getItem(soundAckKey()) || "[]"));
  } catch (e) {
    return new Set();
  }
}

/** Mark the currently alarming stops as acknowledged for today. */
function soundAckStops() {
  const acked = soundAckSet();
  soundStopsNow.forEach((s) => acked.add(s.id));
  try {
    localStorage.setItem(soundAckKey(), JSON.stringify(Array.from(acked)));
  } catch (e) {
    console.warn("stop ack not saved", e);
  }
  soundStopsNow = [];
  paintStopAlarm();
}

/** Drop acknowledgement keys left over from earlier days. */
function soundPruneAcks() {
  try {
    const stale = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(SOUND_ACK_PREFIX) && !k.endsWith(":" + soundDay)) stale.push(k);
    }
    stale.forEach((k) => localStorage.removeItem(k));
  } catch (e) {
    console.warn("stop ack prune failed", e);
  }
}

/**
 * Diff the snapshot's ``alert_feed`` against the previous round and sound the
 * most urgent new edge. The first feed only sets the toast baseline, but ready
 * stops not yet acknowledged today still alarm (the page may have been closed).
 */
function soundOnSnapshot(d) {
  if (!d) return;
  const feed = d.alert_feed;
  if (!feed || typeof feed !== "object") return;
  const uid = d.auth_user ? d.auth_user.id : null;
  const day = String(d.trade_date || "");
  if (uid !== soundUid || day !== soundDay) {
    soundUid = uid;
    soundDay = day;
    soundSeenToasts = null;
    soundStopIds = null;
    soundPruneAcks();
  }
  let best = "";
  const toasts = Array.isArray(feed.toasts) ? feed.toasts : [];
  const ids = toasts.map((t) => String(t.ts || "") + "|" + String(t.key || ""));
  if (soundSeenToasts) {
    toasts.forEach((t, i) => {
      if (soundSeenToasts.has(ids[i])) return;
      const kind = soundKindForToast(t.key);
      if (SOUND_RANK[kind] > SOUND_RANK[best]) best = kind;
    });
  }
  soundSeenToasts = new Set(ids);

  const stops = (Array.isArray(feed.stops) ? feed.stops : []).map((s) =>
    Object.assign({ id: String(s.code || "") + ":" + String(s.exit_mode || "") }, s));
  const acked = soundAckSet();
  const unacked = stops.filter((s) => !acked.has(s.id));
  const fresh = unacked.filter((s) => !soundStopIds || !soundStopIds.has(s.id));
  soundStopIds = new Set(stops.map((s) => s.id));
  soundStopsNow = unacked;
  if (fresh.length) best = "stop";
  paintStopAlarm();
  if (best && soundPlay(best) && best === "stop") soundLastStopPlay = Date.now();
  paintSoundBtn();
}

/** Paint the red stop-alarm strip for unacknowledged ready stops. */
function paintStopAlarm() {
  const el = document.getElementById("stopAlarm");
  if (!el) return;
  if (!soundStopsNow.length) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML = "<b>止损警报</b>"
    + soundStopsNow.map((s) =>
      `<span class="sa-item">${escAttr(s.name || s.code)} ${escAttr(s.code)} · ${escAttr(s.role_label || "止损")}`
      + ` · 建议卖 ${escAttr(s.sell_price ?? "—")}${s.sell_qty ? ` × ${escAttr(s.sell_qty)} 股` : ""}</span>`
    ).join("")
    + `<button type="button" id="stopAlarmAck" title="今日不再为这些止损响铃">知道了</button>`;
}

/** Reflect off / waiting-for-gesture / on states on the header buttons. */
function paintSoundBtn() {
  const p = soundPrefs;
  const locked = !!p.on && !soundReady();
  document.querySelectorAll(".sound-btn").forEach((b) => {
    const mobile = b.id === "soundBtnM";
    if (!p.on) b.textContent = mobile ? "声音关" : "声音·关";
    else if (locked) b.textContent = mobile ? "点我激活" : "声音·点页面激活";
    else b.textContent = mobile ? "声音开" : "声音·开";
    b.classList.toggle("on", !!p.on && !locked);
    b.classList.toggle("locked", locked);
  });
}

/** Render the sound settings panel from the current preferences. */
function paintSoundPanel() {
  const el = document.getElementById("soundPanel");
  if (!el) return;
  const p = soundPrefs;
  const chk = (k) => (p[k] ? "checked" : "");
  el.innerHTML = `
    <div class="sp-row"><label><input type="checkbox" data-sp="on" ${chk("on")} /> 启用声音警报（只存在这台设备）</label><button type="button" class="q" data-term="声音警报">?</button></div>
    <div class="sp-row">音量 <input type="range" min="0" max="100" step="5" data-sp="vol" value="${Math.round((Number(p.vol) || 0) * 100)}" /></div>
    <div class="sp-row"><label><input type="checkbox" data-sp="stop" ${chk("stop")} /> 急促三连：本人止损亮灯 · 恐慌相位</label><button type="button" data-sp-test="stop">试听</button></div>
    <div class="sp-row"><label><input type="checkbox" data-sp="risk" ${chk("risk")} /> 双音：退出可买 · 主线衰退 · 极端拥挤禁开</label><button type="button" data-sp-test="risk">试听</button></div>
    <div class="sp-row"><label><input type="checkbox" data-sp="buy" ${chk("buy")} /> 单声：可买 · 将飞</label><button type="button" data-sp-test="buy">试听</button></div>
    <div class="sp-row"><label><input type="checkbox" data-sp="repeat" ${chk("repeat")} /> 止损没点「知道了」就每 60 秒再响</label></div>
    <div class="sp-note">浏览器不允许网页自己出声：每次打开页面后点一下页面任意处才会激活。止损只看本人持仓、只在 9:25–11:30 / 13:00–15:00 响；其余提醒跟随页面「最近提醒」。</div>
    <div class="sp-row"><button type="button" data-sp-close>收起</button></div>`;
}

/** Open or close the settings panel; opening also unlocks audio (user gesture). */
function toggleSoundPanel(force) {
  const el = document.getElementById("soundPanel");
  if (!el) return;
  soundUnlock();
  const open = force != null ? !!force : el.hidden;
  if (open) paintSoundPanel();
  el.hidden = !open;
  paintSoundBtn();
}

document.querySelectorAll(".sound-btn").forEach((b) => {
  b.addEventListener("click", () => {
    if (!soundPrefs.on) {
      soundPrefs.on = true;
      soundSavePrefs();
    }
    toggleSoundPanel();
  });
});
document.getElementById("soundPanel")?.addEventListener("change", (ev) => {
  const key = ev.target && ev.target.getAttribute("data-sp");
  if (!key) return;
  if (key === "vol") soundPrefs.vol = Math.max(0, Math.min(100, Number(ev.target.value) || 0)) / 100;
  else soundPrefs[key] = !!ev.target.checked;
  soundSavePrefs();
  paintSoundBtn();
});
document.getElementById("soundPanel")?.addEventListener("click", (ev) => {
  const test = ev.target.closest("[data-sp-test]");
  if (test) {
    const kind = test.getAttribute("data-sp-test");
    const ctx = soundUnlock();
    if (ctx && ctx.state !== "running") ctx.resume().then(() => soundPlay(kind, true)).catch(() => {});
    else soundPlay(kind, true);
    return;
  }
  if (ev.target.closest("[data-sp-close]")) toggleSoundPanel(false);
});
document.getElementById("stopAlarm")?.addEventListener("click", (ev) => {
  if (ev.target.closest("#stopAlarmAck")) soundAckStops();
});
// Any first interaction unlocks audio; replay a pending stop alarm right away.
["pointerdown", "keydown"].forEach((type) => {
  document.addEventListener(type, () => {
    if (!soundPrefs.on || soundReady()) return;
    const ctx = soundUnlock();
    if (!ctx) return;
    ctx.resume().then(() => {
      paintSoundBtn();
      if (soundStopsNow.length && soundPlay("stop")) soundLastStopPlay = Date.now();
    }).catch(() => {});
  }, true);
});
setInterval(() => {
  if (!soundPrefs.on || !soundPrefs.repeat || !soundStopsNow.length) return;
  if (Date.now() - soundLastStopPlay < SOUND_REPEAT_MS) return;
  if (soundPlay("stop")) soundLastStopPlay = Date.now();
}, 5000);
paintSoundBtn();
