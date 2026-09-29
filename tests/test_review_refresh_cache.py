"""Review auto-refresh: daily klines for outcome compare are cached per code."""

from __future__ import annotations

import asyncio

import market_desk.engine as engine_mod
from market_desk.engine import DeskEngine

BARS = (["2026-09-25", "2026-09-28"], [10.0, 10.2], {"open": [], "high": [], "low": [], "volume": []})


def _engine() -> DeskEngine:
    eng = DeskEngine.__new__(DeskEngine)
    eng._review_kline_cache = {}
    eng._review_scored_at = 0.0
    return eng


def test_review_klines_fetch_only_stale_codes(monkeypatch):
    calls: list[list[str]] = []

    async def fake_many(client, codes, limit=60, **_kw):
        calls.append(list(codes))
        return {c: (BARS if c != "600003" else ([], [], {})) for c in codes}

    monkeypatch.setattr(engine_mod, "fetch_daily_klines_many", fake_many)
    eng = _engine()
    first = asyncio.run(eng._review_klines(None, ["600001", "600002", "600003", "600001"]))
    assert calls == [["600001", "600002", "600003"]]
    assert set(first) == {"600001", "600002", "600003"}
    second = asyncio.run(eng._review_klines(None, ["600001", "600002", "600003", "600004"]))
    # Cached codes are reused; the empty fetch (600003) is retried.
    assert calls[1] == ["600003", "600004"]
    assert second["600001"] == BARS


def test_review_klines_expire_after_heavy_ttl(monkeypatch):
    calls: list[list[str]] = []

    async def fake_many(client, codes, limit=60, **_kw):
        calls.append(list(codes))
        return {c: BARS for c in codes}

    monkeypatch.setattr(engine_mod, "fetch_daily_klines_many", fake_many)
    eng = _engine()
    asyncio.run(eng._review_klines(None, ["600001"]))
    ts, packed = eng._review_kline_cache["600001"]
    eng._review_kline_cache["600001"] = (ts - 10_000.0, packed)
    asyncio.run(eng._review_klines(None, ["600001"]))
    assert calls == [["600001"], ["600001"]]
