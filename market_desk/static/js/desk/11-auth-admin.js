let authUser = null;
function isGuestUser(u) {
  return !!(u && (u.is_guest || u.role === "guest" || u.can_personal === false));
}
function canPersonal() {
  return !!(authUser && !isGuestUser(authUser));
}
function paintAuthUser(user) {
  authUser = user || null;
  const label = document.getElementById("authUserLabel");
  const switchBtn = document.getElementById("authSwitchBtn");
  const logoutBtn = document.getElementById("authLogoutBtn");
  const adminBtn = document.getElementById("authAdminBtn");
  const pwdBtn = document.getElementById("authPwdBtn");
  if (!label) return;
  if (authUser) {
    const tag = isGuestUser(authUser)
      ? " · 游客"
      : (authUser.role === "admin" ? " · 管理员" : "");
    label.innerHTML = `<b>${authUser.username}</b>${tag}`;
    if (switchBtn) switchBtn.hidden = false;
    if (logoutBtn) logoutBtn.hidden = false;
    if (adminBtn) adminBtn.hidden = authUser.role !== "admin";
    if (pwdBtn) pwdBtn.hidden = isGuestUser(authUser);
    document.getElementById("authMask").hidden = true;
    applyGuestUi();
    if (authUser.must_change_password && !isGuestUser(authUser)) {
      showPasswordPanel("请先修改默认密码后再继续使用");
    }
    ensureGlossary(false);
  } else {
    label.textContent = "未登录";
    if (switchBtn) switchBtn.hidden = true;
    if (logoutBtn) logoutBtn.hidden = true;
    if (adminBtn) adminBtn.hidden = true;
    if (pwdBtn) pwdBtn.hidden = true;
    showAuth("请登录或游客进入");
  }
}
function applyGuestUi() {
  const locked = !canPersonal();
  document.querySelectorAll('#navTabs [data-main="pos"]').forEach((el) => {
    // Keep visible so mobile bottom bar stays 4-up; click still gates via applyMain.
    el.hidden = false;
    el.classList.toggle("nav-locked", locked);
    el.title = locked ? "游客不可用仓位，请注册正式账号" : "";
  });
  // Hide personal write affordances on review when guest.
  document.body.classList.toggle("guest-mode", locked);
  const isAdmin = !!(authUser && authUser.role === "admin");
  document.querySelectorAll('[data-main="backtest"]').forEach((el) => {
    el.hidden = !isAdmin;
  });
  if (!isAdmin && currentMain === "backtest") {
    applyMain("desk");
  }
}
function showAuth(msg) {
  const mask = document.getElementById("authMask");
  if (!mask) return;
  mask.hidden = false;
  const m = document.getElementById("authMsg");
  if (m) m.textContent = msg || "";
  document.getElementById("adminUsers").hidden = true;
  const pwd = document.getElementById("pwdPanel");
  if (pwd) pwd.hidden = true;
}
function hideAuthMask() {
  /** Dismiss the auth overlay when the session already allows browsing. */
  if (authUser && authUser.must_change_password && !isGuestUser(authUser)) {
    const m = document.getElementById("authMsg");
    if (m) m.textContent = "请先修改默认密码后再继续使用";
    return false;
  }
  if (!authUser) return false;
  const mask = document.getElementById("authMask");
  if (mask) mask.hidden = true;
  const box = document.getElementById("adminUsers");
  if (box) box.hidden = true;
  const pwd = document.getElementById("pwdPanel");
  if (pwd) pwd.hidden = true;
  return true;
}
function showAdminPanel() {
  /** Open or toggle-close the admin users / patches panel. */
  const mask = document.getElementById("authMask");
  const box = document.getElementById("adminUsers");
  if (mask && !mask.hidden && box && !box.hidden) {
    hideAuthMask();
    return;
  }
  showAuth("账号管理 · 同意 / 拒绝 / 删除 · 日级补丁");
  refreshAdminUsers();
}
function showPasswordPanel(msg) {
  const mask = document.getElementById("authMask");
  if (!mask) return;
  mask.hidden = false;
  document.getElementById("adminUsers").hidden = true;
  const pwd = document.getElementById("pwdPanel");
  if (pwd) pwd.hidden = false;
  const m = document.getElementById("authMsg");
  if (m) m.textContent = msg || "修改密码";
  ["pwdOld", "pwdNew", "pwdNew2"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
}
async function loadOpsCheck() {
  const meta = document.getElementById("adminOpsMeta");
  const list = document.getElementById("adminOpsList");
  if (!list) return;
  if (meta) meta.textContent = "自检中…";
  try {
    const r = await fetch("/api/ops/check", { credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      if (meta) meta.textContent = d.detail || "自检失败";
      return;
    }
    if (meta) {
      meta.textContent = `总体 ${d.overall || "—"} · pid ${d.pid || "—"} · ${d.host || ""}`;
    }
    const lvlColor = { ok: "#4ade80", warn: "#fbbf24", bad: "#f87171" };
    list.innerHTML = (d.checks || []).map((c) => {
      const color = lvlColor[c.level] || "var(--muted)";
      return `<div class="row"><span style="color:${color}">[${c.level}] ${c.title}</span>`
        + `<span class="meta">${c.detail || ""}</span></div>`;
    }).join("") || `<div class="meta">无检查项</div>`;
  } catch (e) {
    if (meta) meta.textContent = "自检请求失败";
  }
}
async function refreshAdminUsers() {
  const box = document.getElementById("adminUsers");
  if (!box || !authUser || authUser.role !== "admin") return;
  box.hidden = false;
  const r = await fetch("/api/admin/users", { credentials: "same-origin" });
  const d = await r.json();
  if (!d.ok) {
    box.innerHTML = `<div class="meta">${d.detail || "加载失败"}</div>`;
    return;
  }
  const selfId = authUser && authUser.id;
  let html = `<div class="patch-hd" style="margin-top:0;border-top:0;padding-top:0">`
    + `<b>账号管理</b>`
    + `<button type="button" id="adminUsersClose">关闭</button></div>`;
  html += (d.users || []).map((u) => {
    const pending = u.status === "pending";
    const guest = u.is_guest || u.role === "guest";
    const isSelf = selfId != null && Number(u.id) === Number(selfId);
    const canDelete = !guest && !isSelf;
    return `<div class="row"><span>${u.username} · ${u.status}${u.role === "admin" ? " · admin" : ""}${guest ? " · 游客" : ""}${isSelf ? " · 当前" : ""}${u.serverchan_configured ? " · 已配Key" : ""}${u.serverchan_on ? " · 推送开" : ""}</span>`
      + `<span>`
      + (pending
        ? `<button type="button" data-approve="${u.id}">同意</button> `
          + `<button type="button" data-reject="${u.id}">拒绝</button> `
        : "")
      + (!guest
        ? `<button type="button" data-push="${u.id}" data-allowed="${u.serverchan_allowed ? "1" : "0"}">${u.serverchan_allowed ? "禁推送" : "允推送"}</button> `
        : "")
      + (canDelete
        ? `<button type="button" class="danger" data-delete="${u.id}" data-name="${String(u.username || "").replace(/"/g, "&quot;")}">删除</button>`
        : "")
      + `</span></div>`;
  }).join("") || `<div class="meta">暂无用户</div>`;

  html += `<div class="patch-hd"><b>日级补丁</b>`
    + `<button type="button" id="adminPatchApply">再打一次</button></div>`;
  html += `<div class="patch-meta" id="adminPatchMeta">加载中…</div>`;
  html += `<div id="adminPatchList"></div>`;
  html += `<div class="patch-hd"><b>运维自检</b>`
    + `<button type="button" id="adminOpsCheck">刷新自检</button></div>`;
  html += `<div class="patch-meta" id="adminOpsMeta">点刷新查看 VPS/库/clist/备份</div>`;
  html += `<div id="adminOpsList"></div>`;
  box.innerHTML = html;

  document.getElementById("adminUsersClose")?.addEventListener("click", () => hideAuthMask());
  document.getElementById("adminOpsCheck")?.addEventListener("click", () => loadOpsCheck());
  loadOpsCheck();

  box.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.onclick = async () => {
      await fetch("/api/admin/users/" + btn.dataset.approve + "/approve", {
        method: "POST", credentials: "same-origin",
      });
      refreshAdminUsers();
    };
  });
  box.querySelectorAll("[data-reject]").forEach((btn) => {
    btn.onclick = async () => {
      await fetch("/api/admin/users/" + btn.dataset.reject + "/reject", {
        method: "POST", credentials: "same-origin",
      });
      refreshAdminUsers();
    };
  });
  box.querySelectorAll("[data-push]").forEach((btn) => {
    btn.onclick = async () => {
      const next = btn.dataset.allowed !== "1";
      await fetch("/api/admin/users/" + btn.dataset.push + "/push", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allowed: next }),
      });
      refreshAdminUsers();
    };
  });
  box.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.onclick = async () => {
      const name = btn.dataset.name || btn.dataset.delete;
      if (!confirm(`确认删除账号「${name}」？其仓位/自选/成交标记也会一并清除，且不可恢复。`)) {
        return;
      }
      const resp = await fetch("/api/admin/users/" + btn.dataset.delete, {
        method: "DELETE", credentials: "same-origin",
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok || body.ok === false) {
        alert(body.detail || body.message || "删除失败");
        return;
      }
      refreshAdminUsers();
    };
  });
  const applyBtn = document.getElementById("adminPatchApply");
  if (applyBtn) {
    applyBtn.onclick = async () => {
      applyBtn.disabled = true;
      try {
        const resp = await fetch("/api/admin/daily-patches/apply?force=true", {
          method: "POST", credentials: "same-origin",
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok || body.ok === false) {
          alert(body.detail || "补丁应用失败");
        } else {
          const day = body.day_2026_09_21 || {};
          alert(`已应用 ${((body.applied) || []).join(", ") || "（无新文件）"}\n09-21 ups=${day.ups}`);
        }
      } finally {
        applyBtn.disabled = false;
        refreshAdminPatches();
      }
    };
  }
  refreshAdminPatches();
}
async function refreshAdminPatches() {
  const listEl = document.getElementById("adminPatchList");
  const metaEl = document.getElementById("adminPatchMeta");
  if (!listEl) return;
  try {
    const r = await fetch("/api/admin/daily-patches", { credentials: "same-origin" });
    const d = await r.json();
    if (!d.ok) {
      if (metaEl) metaEl.textContent = d.detail || "补丁列表失败";
      return;
    }
    const day = d.day_2026_09_21 || {};
    if (metaEl) {
      metaEl.textContent = day.missing
        ? "库中尚无 2026-09-21 行"
        : `校验 2026-09-21 · ups=${day.ups} downs=${day.downs} amt=${day.amount_yi} · ${day.phase || ""}`;
    }
    listEl.innerHTML = (d.items || []).map((p) => {
      const st = p.applied ? "已应用" : "待应用";
      return `<div class="row"><span>${p.id} · ${p.trade_date || "—"} · ${st}`
        + (p.note ? ` · ${String(p.note).slice(0, 40)}` : "")
        + `</span></div>`;
    }).join("") || `<div class="meta">无补丁文件</div>`;
  } catch (e) {
    if (metaEl) metaEl.textContent = "补丁列表加载失败";
  }
}
async function authRequest(path) {
  const username = (document.getElementById("authUser").value || "").trim();
  const password = document.getElementById("authPass").value || "";
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.ok === false) {
    throw new Error(d.detail || d.message || ("HTTP " + r.status));
  }
  return d;
}
async function refreshAfterAuth(user, okMsg) {
  /** Clear cached desk state and reload snapshot / review for the new session. */
  const msg = document.getElementById("authMsg");
  paintAuthUser(user);
  if (msg && okMsg && !(user && user.must_change_password)) msg.textContent = okMsg;
  await ensureGlossary(true);
  lastData = null;
  if (currentMain === "pos" && !canPersonal()) {
    applyMain("desk");
  } else {
    await tick(true);
  }
  if (currentMain === "review") {
    try { await loadReview(true, ""); } catch (e) {}
  }
  if (currentMain === "pos") {
    try { await ensurePosLhb(true); } catch (e) {}
    try { await ensurePosDiary(true); } catch (e) {}
    try { await ensurePosCal(false); } catch (e) {}
  }
  const settingsPanel = document.getElementById("settingsPanel");
  if (settingsPanel && !settingsPanel.hidden) {
    try { await openSettings(); } catch (e) {}
  }
}
async function enterAsGuest() {
  const msg = document.getElementById("authMsg");
  try {
    const r = await fetch("/api/auth/guest", {
      method: "POST", credentials: "same-origin",
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.detail || "游客登录失败");
    await refreshAfterAuth(d.user, "已以游客进入");
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
}
document.getElementById("authSwitchBtn")?.addEventListener("click", () => showAuth("切换账号"));
document.getElementById("authPwdBtn")?.addEventListener("click", () => {
  if (!authUser || isGuestUser(authUser)) return;
  showPasswordPanel("修改密码");
});
document.getElementById("pwdCancelBtn")?.addEventListener("click", () => {
  const pwd = document.getElementById("pwdPanel");
  if (pwd) pwd.hidden = true;
  if (!hideAuthMask()) {
    if (authUser && authUser.must_change_password) {
      const m = document.getElementById("authMsg");
      if (m) m.textContent = "请先修改默认密码后再继续使用";
    } else {
      showAuth("请登录或游客进入");
    }
  }
});
document.getElementById("pwdSubmitBtn")?.addEventListener("click", async () => {
  const msg = document.getElementById("authMsg");
  const oldP = (document.getElementById("pwdOld") || {}).value || "";
  const newP = (document.getElementById("pwdNew") || {}).value || "";
  const newP2 = (document.getElementById("pwdNew2") || {}).value || "";
  if (newP.length < 6) {
    if (msg) msg.textContent = "新密码至少 6 位";
    return;
  }
  if (newP !== newP2) {
    if (msg) msg.textContent = "两次输入的新密码不一致";
    return;
  }
  try {
    const r = await fetch("/api/auth/password", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: oldP, new_password: newP }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.detail || d.message || "改密失败");
    if (authUser) authUser.must_change_password = false;
    const pwd = document.getElementById("pwdPanel");
    if (pwd) pwd.hidden = true;
    document.getElementById("authMask").hidden = true;
    if (msg) msg.textContent = "";
    alert("密码已更新");
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
});
document.getElementById("authGuestBtn")?.addEventListener("click", () => enterAsGuest());
document.getElementById("authLoginBtn")?.addEventListener("click", async () => {
  const msg = document.getElementById("authMsg");
  try {
    const d = await authRequest("/api/auth/login");
    await refreshAfterAuth(d.user, "登录成功");
  } catch (e) {
    msg.textContent = String(e.message || e);
  }
});
document.getElementById("authRegisterBtn")?.addEventListener("click", async () => {
  const msg = document.getElementById("authMsg");
  try {
    const d = await authRequest("/api/auth/register");
    msg.textContent = d.message || "已提交，等待管理员同意";
  } catch (e) {
    msg.textContent = String(e.message || e);
  }
});
document.getElementById("authLogoutBtn")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  lastData = null;
  paintAuthUser(null);
});
document.getElementById("authAdminBtn")?.addEventListener("click", () => {
  showAdminPanel();
});
document.getElementById("authMask")?.addEventListener("click", (ev) => {
  // Click dimmed backdrop to dismiss when already logged in (admin / switch / pwd).
  if (ev.target === ev.currentTarget) hideAuthMask();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  const mask = document.getElementById("authMask");
  if (mask && !mask.hidden) hideAuthMask();
});
// Soft-gate: 仓位 / 个人写入对游客拦截。
const _posTab = document.querySelector('#navTabs [data-main="pos"]');
_posTab?.addEventListener("click", (ev) => {
  if (!canPersonal()) {
    ev.preventDefault();
    ev.stopPropagation();
    showAuth("仓位需要正式账号（可先游客看盘，再注册）");
  }
});

// Boot: require session before polling.
(async function bootAuth() {
  try {
    const r = await fetch("/api/auth/me", { credentials: "same-origin" });
    const d = await r.json().catch(() => ({}));
    if (d.user) {
      paintAuthUser(d.user);
      return;
    }
  } catch (e) {}
  paintAuthUser(null);
})();

try {
  const saved = localStorage.getItem("desk-main-tab");
  if (saved) currentMain = saved;
} catch (e) {}
