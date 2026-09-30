"""Recent billboard map used by the pick score's watch-only tag."""

from __future__ import annotations

import asyncio

import market_desk.lhb as lhb


def test_prior_sessions_skip_weekend_and_holiday():
    assert lhb.prior_sessions("2026-09-29", 3) == ["2026-09-23", "2026-09-24", "2026-09-28"]


def test_recent_billboard_map_keeps_latest_and_caches(monkeypatch):
    lhb._RECENT_CACHE.clear()
    calls: list[tuple[str, int]] = []

    async def fake_rows(client, *, report, filt, sort_columns=None, sort_types=None, page_size=50, page_number=1):
        calls.append((filt, page_number))
        return [
            {"SECURITY_CODE": "600001", "TRADE_DATE": "2026-09-24 00:00:00", "BILLBOARD_NET_AMT": 1e8},
            {"SECURITY_CODE": "600001", "TRADE_DATE": "2026-09-28 00:00:00", "BILLBOARD_NET_AMT": -5e7},
            {"SECURITY_CODE": "2", "TRADE_DATE": "2026-09-23 00:00:00", "BILLBOARD_NET_AMT": None},
            {"SECURITY_CODE": "", "TRADE_DATE": "2026-09-23 00:00:00"},
        ]

    monkeypatch.setattr(lhb, "_dc_rows", fake_rows)
    out = asyncio.run(lhb.recent_billboard_map(None, "2026-09-29", 3))
    assert out["600001"] == {"date": "2026-09-28", "net_yi": -0.5}
    assert out["000002"]["date"] == "2026-09-23" and "000000" not in out
    assert calls == [("(TRADE_DATE>='2026-09-23')(TRADE_DATE<='2026-09-28')", 1)]
    asyncio.run(lhb.recent_billboard_map(None, "2026-09-29", 3))
    assert len(calls) == 1


def test_recent_billboard_map_failure_not_cached(monkeypatch):
    lhb._RECENT_CACHE.clear()
    calls: list[int] = []

    async def empty_rows(client, **kw):
        calls.append(1)
        return []

    monkeypatch.setattr(lhb, "_dc_rows", empty_rows)
    assert asyncio.run(lhb.recent_billboard_map(None, "2026-09-29", 5)) == {}
    asyncio.run(lhb.recent_billboard_map(None, "2026-09-29", 5))
    assert len(calls) == 2
