"""Pytest configuration and test session fixtures."""

from __future__ import annotations

import pytest

from market_desk.db import init_db


@pytest.fixture(scope="session", autouse=True)
def _ensure_db_schema() -> None:
    """Ensure database schema and migrations are applied before running tests."""
    init_db()
