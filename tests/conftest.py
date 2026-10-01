"""Pytest configuration and test session fixtures."""

from __future__ import annotations

import pytest

from market_desk.db import init_db
from market_desk.eastmoney import avail as em_avail


@pytest.fixture(scope="session", autouse=True)
def _ensure_db_schema() -> None:
    """Ensure database schema and migrations are applied before running tests."""
    init_db()


@pytest.fixture(autouse=True)
def _isolated_em_avail(monkeypatch) -> None:
    """Give each test empty East Money availability state and no push2 pacing delay."""
    monkeypatch.setattr(em_avail, "EM_PUSH2_MIN_GAP_SEC", 0.0)
    monkeypatch.setattr(em_avail, "_HOURLY", {})
    monkeypatch.setattr(em_avail, "_EVENTS", [])
    monkeypatch.setattr(em_avail, "_STATE", {})
    monkeypatch.setattr(em_avail, "_STATE_DAY", "")
    monkeypatch.setattr(em_avail, "_PACE_NEXT", 0.0)
