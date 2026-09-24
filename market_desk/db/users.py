"""Accounts, sessions, per-user settings and Server酱 recipients."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect
from market_desk.db.state import load_setting, save_setting


def user_count() -> int:
    """Return number of registered users."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"] if row else 0)


def create_user(
    username: str,
    password_hash: str,
    *,
    role: str = "user",
    status: str = "pending",
    must_change_password: bool = False,
) -> dict[str, Any]:
    """Insert a user row and return it."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    allowed = 1 if str(role) == "admin" else 0
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO users(
                username, password_hash, role, status, must_change_password, created_at,
                serverchan_on, serverchan_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                str(username).strip(),
                password_hash,
                role,
                status,
                1 if must_change_password else 0,
                now,
                allowed,
            ),
        )
        uid = int(cur.lastrowid)
        if status == "active":
            conn.execute(
                "UPDATE users SET approved_at = ? WHERE id = ?",
                (now, uid),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row)


def update_user_serverchan(
    user_id: int,
    *,
    sendkey: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    """Update one user's ServerChan SendKey and/or on switch."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
        if not row:
            return None
        key = row["serverchan_sendkey"]
        on = int(row["serverchan_on"] or 0)
        if sendkey is not None:
            key = str(sendkey or "").strip() or None
        if enabled is not None:
            on = 1 if enabled else 0
        conn.execute(
            """
            UPDATE users
            SET serverchan_sendkey = ?, serverchan_on = ?
            WHERE id = ?
            """,
            (key, on, int(user_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    return dict(row) if row else None


def set_user_serverchan_allowed(user_id: int, allowed: bool) -> dict[str, Any] | None:
    """Admin grant/revoke ServerChan push permission for one account."""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET serverchan_allowed = ? WHERE id = ?",
            (1 if allowed else 0, int(user_id)),
        )
        if not allowed:
            conn.execute(
                "UPDATE users SET serverchan_on = 0 WHERE id = ?",
                (int(user_id),),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    return dict(row) if row else None


def list_serverchan_recipients() -> list[dict[str, Any]]:
    """Return active users with push allowed, enabled, and a SendKey set."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, username, role, serverchan_sendkey
            FROM users
            WHERE status = 'active'
              AND role != 'guest'
              AND COALESCE(serverchan_allowed, 0) = 1
              AND COALESCE(serverchan_on, 0) = 1
              AND serverchan_sendkey IS NOT NULL
              AND trim(serverchan_sendkey) != ''
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        if str(item.get("username") or "").strip().lower() == "guest":
            continue
        out.append(item)
    return out


def get_user_by_username(username: str) -> dict[str, Any] | None:
    """Load one user by username."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (str(username or "").strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Load one user by id."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    """Return all users, pending first then newest."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM users
            ORDER BY
              CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
              id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def approve_user(user_id: int, admin_id: int) -> dict[str, Any] | None:
    """Mark a user active after admin approval."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET status = 'active', approved_at = ?, approved_by = ?
            WHERE id = ?
            """,
            (now, int(admin_id), int(user_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    return dict(row) if row else None


def reject_user(user_id: int, admin_id: int) -> dict[str, Any] | None:
    """Mark a registration rejected."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET status = 'rejected', approved_at = ?, approved_by = ?
            WHERE id = ?
            """,
            (now, int(admin_id), int(user_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id),)
        ).fetchone()
    return dict(row) if row else None


def update_user_password(
    user_id: int,
    password_hash: str,
    *,
    must_change: bool = False,
) -> None:
    """Replace a user's password hash."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = ?
            WHERE id = ?
            """,
            (password_hash, 1 if must_change else 0, int(user_id)),
        )
        conn.commit()


def delete_user(user_id: int) -> bool:
    """Delete a user row plus sessions and personal book data.

    Returns True when a ``users`` row was removed. Does not cascade shared
    market tables (signals / daily snapshots stay intact).
    """
    uid = int(user_id)
    with _connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM positions WHERE user_id = ?", (uid,))
        try:
            conn.execute("DELETE FROM watchlist WHERE user_id = ?", (uid,))
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("DELETE FROM favorite_boards WHERE user_id = ?", (uid,))
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("DELETE FROM signal_user_meta WHERE user_id = ?", (uid,))
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "DELETE FROM settings WHERE key LIKE ?",
            (f"user:{uid}:%",),
        )
        cur = conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
        return int(cur.rowcount or 0) > 0


def count_active_admins(*, exclude_user_id: int | None = None) -> int:
    """Count active admin accounts, optionally excluding one id."""
    with _connect() as conn:
        if exclude_user_id is None:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM users
                WHERE role = 'admin' AND status = 'active'
                """
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM users
                WHERE role = 'admin' AND status = 'active' AND id != ?
                """,
                (int(exclude_user_id),),
            ).fetchone()
    return int((row["n"] if row else 0) or 0)


def create_session(user_id: int, token: str, expires_at: str) -> None:
    """Persist a login session token."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions(token, user_id, expires_at, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token, int(user_id), expires_at, now, now),
        )
        conn.commit()


def delete_session(token: str) -> None:
    """Remove one session token."""
    with _connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
        conn.commit()


def touch_session(token: str) -> None:
    """Bump last_seen_at for an active session."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ? WHERE token = ?",
            (now, token),
        )
        conn.commit()


def get_session_user(token: str) -> dict[str, Any] | None:
    """Return the user row for a non-expired session token."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT u.*
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at >= ?
            """,
            (token, now),
        ).fetchone()
    return dict(row) if row else None


def load_user_setting(user_id: int, key: str) -> Any | None:
    """Load one JSON setting scoped to a user."""
    return load_setting(f"user:{int(user_id)}:{key}")


def save_user_setting(user_id: int, key: str, value: Any) -> None:
    """Persist one JSON setting scoped to a user."""
    save_setting(f"user:{int(user_id)}:{key}", value)
