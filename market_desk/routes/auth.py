"""Login / register / session, per-user Server酱 and admin user management."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from market_desk.auth import (
    admin_approve,
    admin_delete_user,
    admin_list_users,
    admin_reject,
    admin_set_push_allowed,
    change_password,
    get_my_serverchan,
    login_guest,
    login_user,
    logout_token,
    register_user,
    SESSION_COOKIE,
    update_my_serverchan,
)
from market_desk.deps import (
    current_admin_required,
    current_member_required,
    current_user_optional,
)

router = APIRouter()


class AuthCredIn(BaseModel):
    """Login / register payload."""

    username: str
    password: str


class PasswordChangeIn(BaseModel):
    """Change-password payload."""

    old_password: str
    new_password: str


class ServerChanIn(BaseModel):
    """Per-user ServerChan SendKey / enable switch."""

    sendkey: str | None = None
    on: bool | None = None
    clear_key: bool = False


class AdminPushAllowIn(BaseModel):
    """Admin grant/revoke WeChat push for one account."""

    allowed: bool


@router.post("/api/auth/register")
def auth_register(body: AuthCredIn) -> dict:
    """Self-register; account stays pending until an admin approves."""
    try:
        user = register_user(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "user": user,
        "message": "已提交注册，等待管理员同意后方可登录",
    }


@router.post("/api/auth/login")
def auth_login(body: AuthCredIn, response: Response) -> dict:
    """Log in and set an HttpOnly session cookie."""
    try:
        user, token, expires = login_user(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=14 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "user": user, "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


@router.post("/api/auth/guest")
def auth_guest(response: Response) -> dict:
    """Enter as the shared guest account (view-only personal layer)."""
    try:
        user, token, expires = login_guest()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=14 * 24 * 3600,
        path="/",
    )
    return {"ok": True, "user": user, "expires_at": expires.strftime("%Y-%m-%d %H:%M:%S")}


@router.post("/api/auth/logout")
def auth_logout(
    response: Response,
    desk_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    """Clear the session cookie and invalidate the token."""
    logout_token(desk_sid)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def auth_me(user: dict | None = Depends(current_user_optional)) -> dict:
    """Return the current session user (or null)."""
    return {"ok": True, "user": user}


@router.post("/api/auth/password")
def auth_password(
    body: PasswordChangeIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Change the logged-in user's password."""
    try:
        change_password(int(user["id"]), body.old_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.get("/api/me/serverchan")
def me_serverchan_get(user: dict = Depends(current_member_required)) -> dict:
    """Return masked ServerChan settings for the logged-in user."""
    try:
        row = get_my_serverchan(int(user["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "serverchan": row}


@router.post("/api/me/serverchan")
def me_serverchan_save(
    body: ServerChanIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Save the logged-in user's SendKey and/or enable switch."""
    try:
        row = update_my_serverchan(
            int(user["id"]),
            sendkey=body.sendkey,
            enabled=body.on,
            clear_key=bool(body.clear_key),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "serverchan": row}


@router.post("/api/admin/users/{uid}/push")
def admin_user_push(
    uid: int,
    body: AdminPushAllowIn,
    admin: dict = Depends(current_admin_required),
) -> dict:
    """Grant or revoke ServerChan push for one account."""
    try:
        row = admin_set_push_allowed(uid, int(admin["id"]), bool(body.allowed))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}


@router.get("/api/admin/users")
def admin_users(admin: dict = Depends(current_admin_required)) -> dict:
    """List users for admin approval."""
    del admin
    return {"ok": True, "users": admin_list_users()}


@router.post("/api/admin/users/{uid}/approve")
def admin_user_approve(uid: int, admin: dict = Depends(current_admin_required)) -> dict:
    """Approve a pending registration."""
    try:
        row = admin_approve(uid, int(admin["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}


@router.post("/api/admin/users/{uid}/reject")
def admin_user_reject(uid: int, admin: dict = Depends(current_admin_required)) -> dict:
    """Reject a pending registration."""
    try:
        row = admin_reject(uid, int(admin["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}


@router.delete("/api/admin/users/{uid}")
def admin_user_delete(uid: int, admin: dict = Depends(current_admin_required)) -> dict:
    """Permanently delete another account (admin only)."""
    try:
        row = admin_delete_user(uid, int(admin["id"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "user": row}
