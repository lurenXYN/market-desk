"""Slice must be a pure function: no temporary swap of the shared snapshot."""

from __future__ import annotations

from market_desk.engine.core import DeskEngine


def _engine_with(**extra) -> DeskEngine:
    eng = DeskEngine()
    eng.snapshot = {"ok": True, "updated_at": "2026-11-17 10:00:00", **extra}
    return eng


def test_slice_uses_passed_payload_and_keeps_global_untouched() -> None:
    eng = _engine_with(positions=[{"code": "GLOBAL"}], hot_boards=[{"name": "global-board"}])
    before = eng.snapshot
    payload = {
        "ok": True,
        "updated_at": "2026-11-17 10:00:00",
        "positions": [{"code": "MINE"}],
        "hot_boards": [{"name": "mine-board"}],
    }
    sliced = eng.slice_snapshot("pos", snapshot=payload)
    # Same object identity: nothing was rebound or restored.
    assert eng.snapshot is before
    assert eng.snapshot["positions"] == [{"code": "GLOBAL"}]
    assert sliced["positions"] == [{"code": "MINE"}]
    assert "hot_boards" not in sliced  # pos view does not carry boards


def test_slice_defaults_to_engine_snapshot() -> None:
    eng = _engine_with(positions=[{"code": "GLOBAL"}])
    sliced = eng.slice_snapshot("pos")
    assert sliced["positions"] == [{"code": "GLOBAL"}]


def test_slice_full_view_returns_payload_as_is() -> None:
    eng = _engine_with()
    payload = {"ok": True, "updated_at": "2026-11-17 10:00:00", "positions": []}
    assert eng.slice_snapshot("full", snapshot=payload) is payload