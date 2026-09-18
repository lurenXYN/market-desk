"""Multi-user auth: register (pending), admin approve, session cookies."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Any

from market_desk.db import (
    approve_user,
    count_active_admins,
    create_session,
    create_user,
    delete_session,
    delete_user,
    get_session_user,
    get_user_by_id,
    get_user_by_username,
    init_db,
    list_users,
    reject_user,
    touch_session,
    user_count,
)

SESSION_COOKIE = "desk_sid"
SESSION_DAYS = 14
_PBKDF2_ROUNDS = 200_000
GUEST_USERNAME = "guest"


def is_guest(user: dict[str, Any] | None) -> bool:
    """Return True when the session user is the shared guest account."""
    if not user:
        return False
    return str(user.get("role") or "") == "guest" or str(
        user.get("username") or ""
    ).strip().lower() == GUEST_USERNAME


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Return ``salt$hex`` password hash using PBKDF2-HMAC-SHA256."""
    raw = str(password or "")
    if len(raw) < 6:
        raise ValueError("密码至少 6 位")
    if len(raw) > 128:
        raise ValueError("密码过长")
    salt_b = (salt or secrets.token_hex(16)).encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw.encode("utf-8"),
        salt_b,
        _PBKDF2_ROUNDS,
    )
    return f"{salt_b.decode('utf-8')}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        salt, _hex = str(stored or "").split("$", 1)
    except ValueError:
        return False
    try:
        check = hash_password(password, salt=salt)
    except ValueError:
        return False
    return hmac.compare_digest(check, str(stored))


def public_user(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip secrets from a user row for API responses."""
    if not row:
        return None
    role = str(row.get("role") or "user")
    username = str(row.get("username") or "")
    guest = role == "guest" or username.strip().lower() == GUEST_USERNAME
    key = str(row.get("serverchan_sendkey") or "").strip()
    return {
        "id": int(row["id"]),
        "username": username,
        "role": "guest" if guest else role,
        "status": str(row.get("status") or "pending"),
        "created_at": row.get("created_at"),
        "approved_at": row.get("approved_at"),
        "must_change_password": bool(row.get("must_change_password")),
        "is_guest": guest,
        # Guest may view market / paper review; personal books stay locked.
        "can_personal": not guest,
        "serverchan_allowed": bool(int(row.get("serverchan_allowed") or 0)),
        "serverchan_on": bool(int(row.get("serverchan_on") or 0)),
        "serverchan_configured": bool(key) and not guest,
    }


def ensure_guest_user() -> dict[str, Any] | None:
    """Ensure the shared read-only guest account exists."""
    init_db()
    existing = get_user_by_username(GUEST_USERNAME)
    if existing:
        # Repair legacy rows that used role=user for the guest name.
        if str(existing.get("role") or "") != "guest" or str(
            existing.get("status") or ""
        ) != "active":
            from market_desk.db import _connect

            with _connect() as conn:
                conn.execute(
                    "UPDATE users SET role = 'guest', status = 'active' WHERE id = ?",
                    (int(existing["id"]),),
                )
                conn.commit()
            existing = get_user_by_id(int(existing["id"]))
        return public_user(existing)
    password = os.environ.get("MARKET_DESK_GUEST_PASSWORD") or "guest123"
    row = create_user(
        GUEST_USERNAME,
        hash_password(password),
        role="guest",
        status="active",
        must_change_password=False,
    )
    return public_user(row)


def ensure_bootstrap_admin() -> dict[str, Any] | None:
    """Create the first admin when needed, and always ensure guest exists."""
    init_db()
    created: dict[str, Any] | None = None
    if user_count() == 0:
        username = (os.environ.get("MARKET_DESK_ADMIN_USER") or "admin").strip() or "admin"
        password = os.environ.get("MARKET_DESK_ADMIN_PASSWORD") or "admin123"
        row = create_user(
            username,
            hash_password(password),
            role="admin",
            status="active",
            must_change_password=password == "admin123",
        )
        created = public_user(row)
    ensure_guest_user()
    return created


def register_user(username: str, password: str) -> dict[str, Any]:
    """Self-register a pending user (needs admin approval before login)."""
    ensure_bootstrap_admin()
    name = str(username or "").strip()
    if not name or len(name) < 2 or len(name) > 32:
        raise ValueError("用户名长度 2–32")
    if name.lower() == GUEST_USERNAME:
        raise ValueError("不能注册游客账号名")
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("用户名仅允许字母数字与 _-")
    if get_user_by_username(name):
        raise ValueError("用户名已存在")
    row = create_user(
        name,
        hash_password(password),
        role="user",
        status="pending",
        must_change_password=False,
    )
    return public_user(row) or {}


def _issue_session(row: dict[str, Any]) -> tuple[dict[str, Any], str, datetime]:
    """Create a session cookie token for an already-validated user row."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(days=SESSION_DAYS)
    create_session(int(row["id"]), token, expires.strftime("%Y-%m-%d %H:%M:%S"))
    return public_user(row) or {}, token, expires


def login_user(username: str, password: str) -> tuple[dict[str, Any], str, datetime]:
    """Validate credentials and create a session. Returns user, token, expiry."""
    ensure_bootstrap_admin()
    row = get_user_by_username(str(username or "").strip())
    if not row or not verify_password(password, str(row.get("password_hash") or "")):
        raise ValueError("用户名或密码错误")
    status = str(row.get("status") or "")
    if status == "pending":
        raise ValueError("账号待管理员同意后方可登录")
    if status == "rejected":
        raise ValueError("注册未通过，请联系管理员")
    if status != "active":
        raise ValueError("账号已停用")
    return _issue_session(row)


def login_guest() -> tuple[dict[str, Any], str, datetime]:
    """Enter as the shared guest account (no password required)."""
    ensure_bootstrap_admin()
    row = get_user_by_username(GUEST_USERNAME)
    if not row or str(row.get("status") or "") != "active":
        ensure_guest_user()
        row = get_user_by_username(GUEST_USERNAME)
    if not row:
        raise ValueError("游客账号不可用")
    return _issue_session(row)


def logout_token(token: str | None) -> None:
    """Invalidate one session token."""
    if token:
        delete_session(token)


def user_from_token(token: str | None) -> dict[str, Any] | None:
    """Resolve a session cookie to a public user dict."""
    if not token:
        return None
    row = get_session_user(token)
    if not row:
        return None
    if str(row.get("status") or "") != "active":
        delete_session(token)
        return None
    touch_session(token)
    return public_user(row)


def require_admin(user: dict[str, Any] | None) -> dict[str, Any]:
    """Raise ValueError unless the user is an active admin."""
    if not user or user.get("role") != "admin" or user.get("status") != "active":
        raise ValueError("需要管理员权限")
    return user


def mask_serverchan_key(sendkey: str | None) -> str | None:
    """Return a masked SendKey for UI (never echo the full secret)."""
    key = str(sendkey or "").strip()
    if not key:
        return None
    if len(key) <= 8:
        return key[:2] + "***"
    return f"{key[:3]}***{key[-4:]}"


def get_my_serverchan(user_id: int) -> dict[str, Any]:
    """Return ServerChan settings for the logged-in user (key masked)."""
    row = get_user_by_id(int(user_id))
    if not row:
        raise ValueError("用户不存在")
    pub = public_user(row) or {}
    if is_guest(pub):
        raise ValueError("游客不能配置推送")
    return {
        "allowed": bool(pub.get("serverchan_allowed")),
        "on": bool(pub.get("serverchan_on")),
        "configured": bool(pub.get("serverchan_configured")),
        "sendkey_masked": mask_serverchan_key(row.get("serverchan_sendkey")),
    }


def update_my_serverchan(
    user_id: int,
    *,
    sendkey: str | None = None,
    enabled: bool | None = None,
    clear_key: bool = False,
) -> dict[str, Any]:
    """Let a user set their own SendKey / on switch.

    SendKey may be saved before admin grants push. Turning ``on`` requires
    ``serverchan_allowed`` and a stored key.
    """
    from market_desk.db import update_user_serverchan

    row = get_user_by_id(int(user_id))
    if not row:
        raise ValueError("用户不存在")
    pub = public_user(row) or {}
    if is_guest(pub):
        raise ValueError("游客不能配置推送")
    allowed = bool(pub.get("serverchan_allowed"))
    key_arg: str | None
    if clear_key:
        key_arg = ""
    elif sendkey is not None:
        key_arg = str(sendkey).strip()
        if key_arg and not (
            key_arg.upper().startswith("SCT") or key_arg.upper().startswith("SCU")
        ):
            raise ValueError("SendKey 格式不正确（应以 SCT 开头）")
    else:
        key_arg = None
    on_arg = enabled
    if enabled is True:
        if not allowed:
            raise ValueError("管理员未允许此账号开启微信推送")
        has_key = bool(
            (key_arg if key_arg is not None else str(row.get("serverchan_sendkey") or "").strip())
        )
        if not has_key:
            raise ValueError("请先填写 SendKey 再开启推送")
    if not allowed and enabled is None:
        # Saving only a key while not allowed: force on=0.
        on_arg = False
    updated = update_user_serverchan(
        int(user_id),
        sendkey=key_arg,
        enabled=on_arg,
    )
    if not updated:
        raise ValueError("保存失败")
    return get_my_serverchan(int(user_id))


def admin_set_push_allowed(user_id: int, admin_id: int, allowed: bool) -> dict[str, Any]:
    """Admin grant/revoke ServerChan permission for another account."""
    del admin_id
    from market_desk.db import set_user_serverchan_allowed

    target = get_user_by_id(int(user_id))
    if not target:
        raise ValueError("用户不存在")
    if is_guest(public_user(target)):
        raise ValueError("不能给游客开推送")
    row = set_user_serverchan_allowed(int(user_id), bool(allowed))
    if not row:
        raise ValueError("保存失败")
    return public_user(row) or {}


def admin_list_users() -> list[dict[str, Any]]:
    """Return all users for the admin panel."""
    return [public_user(r) or {} for r in list_users()]


def admin_approve(user_id: int, admin_id: int) -> dict[str, Any]:
    """Approve a pending registration."""
    target = get_user_by_id(int(user_id))
    if target and is_guest(public_user(target)):
        raise ValueError("不能审批游客账号")
    row = approve_user(int(user_id), int(admin_id))
    if not row:
        raise ValueError("用户不存在")
    return public_user(row) or {}


def admin_reject(user_id: int, admin_id: int) -> dict[str, Any]:
    """Reject a pending registration."""
    target = get_user_by_id(int(user_id))
    if target and is_guest(public_user(target)):
        raise ValueError("不能拒绝游客账号")
    row = reject_user(int(user_id), int(admin_id))
    if not row:
        raise ValueError("用户不存在")
    return public_user(row) or {}


def change_password(user_id: int, old_password: str, new_password: str) -> None:
    """Change password after verifying the old one."""
    row = get_user_by_id(int(user_id))
    if not row:
        raise ValueError("用户不存在")
    if is_guest(public_user(row)):
        raise ValueError("游客账号不能改密码")
    if not verify_password(old_password, str(row.get("password_hash") or "")):
        raise ValueError("原密码错误")
    from market_desk.db import update_user_password

    update_user_password(int(user_id), hash_password(new_password), must_change=False)


def admin_delete_user(user_id: int, admin_id: int) -> dict[str, Any]:
    """Delete another account (not self / guest / last admin)."""
    uid = int(user_id)
    aid = int(admin_id)
    if uid == aid:
        raise ValueError("不能删除当前登录的管理员账号")
    target = get_user_by_id(uid)
    if not target:
        raise ValueError("用户不存在")
    pub = public_user(target) or {}
    if is_guest(pub):
        raise ValueError("不能删除游客账号")
    if str(target.get("role") or "") == "admin" and count_active_admins(
        exclude_user_id=uid
    ) < 1:
        raise ValueError("不能删除最后一个管理员")
    snapshot = dict(pub)
    if not delete_user(uid):
        raise ValueError("删除失败")
    return snapshot
