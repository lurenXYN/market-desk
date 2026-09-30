"""Post-close board fund-flow snapshot (research data, written once per trade day)."""

from __future__ import annotations

from types import SimpleNamespace

import market_desk.engine as engine_mod
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
