"""Glossary must ship with every tab slice (review included)."""

from __future__ import annotations

from market_desk.engine import DeskEngine
from market_desk.glossary import GLOSSARY


def test_slice_snapshot_includes_glossary_for_review() -> None:
    eng = DeskEngine.__new__(DeskEngine)
    eng.snapshot = {"ok": True, "phase": "发酵", "glossary": GLOSSARY}
    sliced = DeskEngine.slice_snapshot(eng, "review")
    assert "glossary" in sliced
    assert "信号复盘" in (sliced.get("glossary") or {})
    assert "漏买归因" in sliced["glossary"]


def test_slice_snapshot_falls_back_to_module_glossary() -> None:
    eng = DeskEngine.__new__(DeskEngine)
    eng.snapshot = {"ok": True}
    sliced = DeskEngine.slice_snapshot(eng, "pos")
    assert sliced.get("glossary") is GLOSSARY or "仓位" in (sliced.get("glossary") or {})
