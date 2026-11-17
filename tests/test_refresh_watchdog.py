"""Refresh loop supervision: a crash must restart the loop, not kill polling silently."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from market_desk.engine.core import DeskEngine


def test_supervisor_restarts_after_crash(monkeypatch) -> None:
    eng = DeskEngine()
    calls = {"n": 0}

    async def boom() -> None:
        calls["n"] += 1
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(eng, "_loop", boom)

    async def run() -> None:
        task = asyncio.create_task(eng._supervise_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls["n"] == 1  # first crash observed before the 5s backoff
    assert eng.snapshot["loop_restarts"] == 1
    assert eng.snapshot["ok"] is False
    assert eng.loop_status()["restarts"] == 1


def test_supervisor_restarts_when_loop_returns(monkeypatch) -> None:
    eng = DeskEngine()
    calls = {"n": 0}

    async def returns() -> None:
        calls["n"] += 1

    monkeypatch.setattr(eng, "_loop", returns)

    async def run() -> None:
        task = asyncio.create_task(eng._supervise_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls["n"] == 1
    assert eng.snapshot["loop_restarts"] == 1


def test_stop_awaits_the_task() -> None:
    eng = DeskEngine()

    async def forever() -> None:
        await asyncio.sleep(30)

    async def run() -> None:
        eng._task = asyncio.create_task(forever())
        await eng.stop()
        assert eng._task is None

    asyncio.run(run())


def test_loop_status_reports_heartbeat_age() -> None:
    eng = DeskEngine()
    assert eng.loop_status()["heartbeat_age"] is None
    eng._loop_heartbeat = 0.0
    status = eng.loop_status()
    assert status["alive"] is False
    assert status["heartbeat_age"] is None