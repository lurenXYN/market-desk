"""Admins can revoke their own ServerChan push and it survives init_db re-runs."""

from __future__ import annotations

import market_desk.db as desk_db


def _use_tmp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)


def test_new_admin_is_allowed_by_default(monkeypatch, tmp_path) -> None:
    _use_tmp_db(monkeypatch, tmp_path)
    desk_db.init_db()
    row = desk_db.create_user("boss", "x", role="admin", status="active")
    assert int(row["serverchan_allowed"]) == 1


def test_admin_revoke_survives_init_db(monkeypatch, tmp_path) -> None:
    _use_tmp_db(monkeypatch, tmp_path)
    desk_db.init_db()
    row = desk_db.create_user("boss", "x", role="admin", status="active")
    uid = int(row["id"])
    desk_db.update_user_serverchan(uid, sendkey="SCT123456789", enabled=True)
    assert [r["id"] for r in desk_db.list_serverchan_recipients()] == [uid]

    desk_db.set_user_serverchan_allowed(uid, False)
    desk_db.init_db()
    desk_db.init_db()

    after = desk_db.get_user_by_id(uid)
    assert int(after["serverchan_allowed"]) == 0
    assert int(after["serverchan_on"]) == 0
    assert desk_db.list_serverchan_recipients() == []


def test_admin_turning_off_own_switch_stops_push(monkeypatch, tmp_path) -> None:
    _use_tmp_db(monkeypatch, tmp_path)
    desk_db.init_db()
    row = desk_db.create_user("boss", "x", role="admin", status="active")
    uid = int(row["id"])
    desk_db.update_user_serverchan(uid, sendkey="SCT123456789", enabled=True)
    desk_db.update_user_serverchan(uid, enabled=False)
    desk_db.init_db()
    assert desk_db.list_serverchan_recipients() == []
