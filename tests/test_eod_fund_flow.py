"""Post-close board fund-flow snapshot (research data, written once per trade day)."""

from __future__ import annotations

from types import SimpleNamespace

import market_desk.engine.eod as engine_mod
from market_desk.engine import DeskEngine


def test_saves_fresh_rows_for_today(monkeypatch):
    saved: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(engine_mod, "save_fund_flow_daily", lambda day, rows: saved.append((day, rows)) or len(rows))
    fake = SimpleNamespace(_day_flow_fresh=("2026-09-30", [{"bk": "BK1", "kind": "industry"}],
                                            [{"bk": "BK2", "kind": "concept"}]))
    assert DeskEngine._save_eod_fund_flow(fake, "2026-09-30") == 2
    assert saved == [("2026-09-30", [{"bk": "BK1", "kind": "industry"}, {"bk": "BK2", "kind": "concept"}])]


def test_skips_stale_or_empty_rows(monkeypatch):
    saved: list[str] = []
    monkeypatch.setattr(engine_mod, "save_fund_flow_daily", lambda day, rows: saved.append(day) or 0)
    assert DeskEngine._save_eod_fund_flow(SimpleNamespace(_day_flow_fresh=None), "2026-09-30") == 0
    stale = SimpleNamespace(_day_flow_fresh=("2026-09-29", [{"bk": "BK1"}], []))
    assert DeskEngine._save_eod_fund_flow(stale, "2026-09-30") == 0
    empty = SimpleNamespace(_day_flow_fresh=("2026-09-30", [], []))
    assert DeskEngine._save_eod_fund_flow(empty, "2026-09-30") == 0
    assert saved == []


def _fake_flow(sizes: dict[str, int]):
    async def fake(client, kind, limit, period):
        return [{"bk": f"{kind}{i}", "kind": kind} for i in range(sizes[kind])]

    return fake


def test_refetch_replaces_top80_with_fuller_pull(monkeypatch):
    import asyncio

    monkeypatch.setattr(engine_mod, "fetch_board_fund_flow", _fake_flow({"industry": 90, "concept": 400}))
    fake = SimpleNamespace(_day_flow_fresh=("2026-09-30", [{"bk": "a"}] * 48, [{"bk": "b"}] * 77))
    assert asyncio.run(DeskEngine._refetch_eod_fund_flow(fake, "2026-09-30")) == 490
    assert len(fake._day_flow_fresh[1]) == 90 and len(fake._day_flow_fresh[2]) == 400


def test_refetch_keeps_hot_rows_when_full_pull_is_smaller(monkeypatch):
    import asyncio

    monkeypatch.setattr(engine_mod, "fetch_board_fund_flow", _fake_flow({"industry": 0, "concept": 5}))
    fake = SimpleNamespace(_day_flow_fresh=("2026-09-30", [{"bk": "a"}] * 48, [{"bk": "b"}] * 77))
    assert asyncio.run(DeskEngine._refetch_eod_fund_flow(fake, "2026-09-30")) == 125
    assert len(fake._day_flow_fresh[1]) == 48
