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


def test_slice_snapshot_market_includes_global_watch() -> None:
    """Market tab poll must carry 外盘脉搏 (was omitted → UI stuck on 待刷新)."""
    eng = DeskEngine.__new__(DeskEngine)
    gw = {
        "items": [{"name": "恒生", "group": "index", "pct": 0.5}],
        "tone": {"us_fut": "平淡", "commodity": "偏弱", "asia": "平淡"},
    }
    eng.snapshot = {"ok": True, "indices": [], "etfs": [], "global_watch": gw}
    sliced = DeskEngine.slice_snapshot(eng, "market")
    assert sliced.get("global_watch") == gw

