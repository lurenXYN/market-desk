"""FastAPI dependencies for multi-user auth."""

from __future__ import annotations

from typing import Any

from fastapi import Cookie, HTTPException, Request

from market_desk.auth import (
    SESSION_COOKIE,
    ensure_bootstrap_admin,
    is_guest,
    user_from_token,
)


def current_user_optional(
    desk_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any] | None:
    """Return the logged-in user or None."""
    ensure_bootstrap_admin()
    return user_from_token(desk_sid)


def current_user_required(
    desk_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    """Require any active session (including guest)."""
    user = current_user_optional(desk_sid)
    if not user:
        raise HTTPException(401, "请先登录（可用游客账号进入）")
    return user


def current_member_required(
    desk_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    """Require a non-guest account for personal books and trade annotations."""
    user = current_user_required(desk_sid)
    if is_guest(user) or not user.get("can_personal", True):
        raise HTTPException(
            403,
            "游客只能看盘；仓位 / 自选 / 成交记账请注册正式账号（管理员同意后可用）",
        )
    return user


def current_admin_required(
    desk_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    """Require an active admin user."""
    user = current_user_required(desk_sid)
    if user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def request_user(request: Request) -> dict[str, Any] | None:
    """Resolve user from a raw Request (for non-Depends paths)."""
    token = request.cookies.get(SESSION_COOKIE)
    ensure_bootstrap_admin()
    return user_from_token(token)
