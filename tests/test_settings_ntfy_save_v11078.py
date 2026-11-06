"""Regression: ntfy_* must persist through POST /api/settings."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db.schema import init_db
from market_desk.settings import get_settings_for_user, update_settings


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "settings.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    init_db()


def test_update_settings_persists_ntfy_private_keys(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    out = update_settings(
        {
            "ntfy_on": True,
            "ntfy_topic": "desk-AdCIQ1LbRsexN-_89BYVp2en",
            "ntfy_token": "",
            "ntfy_fly": True,
            "ntfy_near": True,
            "ntfy_prep": False,
            "ntfy_soft_sell": False,
        },
        user_id=1,
    )
    assert out["ntfy_on"] is True
    assert out["ntfy_topic"] == "desk-AdCIQ1LbRsexN-_89BYVp2en"
    assert out["ntfy_prep"] is False
    again = get_settings_for_user(1)
    assert again["ntfy_on"] is True
    assert again["ntfy_topic"] == "desk-AdCIQ1LbRsexN-_89BYVp2en"
    assert again["ntfy_prep"] is False


def test_write_settings_route_keeps_ntfy_fields(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from market_desk.routes.settings import router

    app = FastAPI()
    app.include_router(router)

    def _member():
        return {"id": 7, "role": "member", "username": "u"}

    app.dependency_overrides[
        __import__("market_desk.deps", fromlist=["current_member_required"]).current_member_required
    ] = _member

    client = TestClient(app)
    resp = client.post(
        "/api/settings",
        json={
            "ntfy_on": True,
            "ntfy_topic": "desk-test-topic",
            "ntfy_near": False,
            "account_equity": 20000,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    s = body["settings"]
    assert s["ntfy_on"] is True
    assert s["ntfy_topic"] == "desk-test-topic"
    assert s["ntfy_near"] is False
    assert s["account_equity"] == 20000
