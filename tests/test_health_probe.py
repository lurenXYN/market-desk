"""Health probe must fail when the refresh loop stops or the snapshot goes stale."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from market_desk.engine import engine
from market_desk.engine.core import CN_TZ
from market_desk.routes.market import health


def _body(resp) -> dict:
    return json.loads(resp.body.decode("utf-8"))


def _patch(monkeypatch, *, updated_at: str | None, age: float | None, alive: bool = True) -> None:
    monkeypatch.setattr(engine, "snapshot", {"ok": True, "updated_at": updated_at, "refresh_seconds": 20})
    monkeypatch.setattr(
        engine,
        "loop_status",
        lambda: {"alive": alive, "restarts": 0, "heartbeat_age": age},
    )


def _stamp(minutes_ago: int) -> str:
    return (datetime.now(CN_TZ).replace(tzinfo=None) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def test_fresh_snapshot_and_live_loop_is_ok(monkeypatch) -> None:
    _patch(monkeypatch, updated_at=_stamp(1), age=5.0)
    resp = health()
    body = _body(resp)
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["loop"]["alive"] is True


def test_stale_snapshot_returns_503(monkeypatch) -> None:
    _patch(monkeypatch, updated_at=_stamp(60), age=5.0)
    resp = health()
    body = _body(resp)
    assert resp.status_code == 503
    assert body["ok"] is False
    assert body["stale_seconds"] and body["stale_seconds"] > 300


def test_dead_loop_returns_503_even_when_snapshot_is_fresh(monkeypatch) -> None:
    _patch(monkeypatch, updated_at=_stamp(1), age=5.0, alive=False)
    resp = health()
    assert resp.status_code == 503
    assert _body(resp)["ok"] is False


def test_never_published_snapshot_is_not_healthy(monkeypatch) -> None:
    _patch(monkeypatch, updated_at=None, age=None)
    resp = health()
    assert resp.status_code == 503
    assert _body(resp)["stale_seconds"] is None