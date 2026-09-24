"""Lifecycle panel columns stay fixed intraday and move only after the close."""

from __future__ import annotations

import market_desk.lifecycle as lc


def _no_bias(monkeypatch):
    monkeypatch.setattr(lc, "_review_lifecycle_bias", lambda: {"strict": False, "hit_rate": None, "n": 0})


def _hist(bk: str, rows: list[tuple[str, int, float, str]]):
    return [
        {"trade_date": d, "bk": bk, "name": bk, "zt_n": z, "pct": p, "status": s, "leader_boards": 1}
        for d, z, p, s in rows
    ]


def test_frozen_stage_ignores_intraday_flip(monkeypatch):
    _no_bias(monkeypatch)
    hist_map = {
        "BK1": _hist("BK1", [
            ("2026-09-21", 3, 2.0, "确认中"),
            ("2026-09-22", 4, 2.5, "确认中"),
            ("2026-09-23", 5, 3.0, "确认中"),
        ]),
    }
    live_ending = {"bk": "BK1", "name": "BK1", "status": "退潮", "zt_n": 0, "pct": -2.0,
                   "hist": hist_map["BK1"], "tags": []}
    out = lc.build_mainline_lifecycle([live_ending], [], hist_map=hist_map, frozen=None, final=False)
    assert out["mode"] == "frozen"
    assert [r["bk"] for r in out["ongoing"]] == ["BK1"]
    assert out["ending"] == []
    assert out["ongoing"][0]["live_stage"] == "ending"
    assert "收盘复核" in out["ongoing"][0]["live_hint"]


def test_frozen_map_wins_and_fresh_not_counted(monkeypatch):
    _no_bias(monkeypatch)
    live_new = {"bk": "BK9", "name": "新题材", "status": "观察", "zt_n": 3, "pct": 2.0,
                "hist": [], "tags": [{"k": "点火", "on": True}]}
    out = lc.build_mainline_lifecycle(
        [live_new], [], hist_map={}, frozen={"BK1": "ending"}, final=False
    )
    assert out["starting"] == []
    assert [r["bk"] for r in out["fresh"]] == ["BK9"]


def test_after_close_uses_live_and_exports_stage_map(monkeypatch):
    _no_bias(monkeypatch)
    live = {"bk": "BK1", "name": "BK1", "status": "退潮", "zt_n": 0, "pct": -2.0,
            "hist": _hist("BK1", [("2026-09-23", 5, 3.0, "确认中")]), "tags": []}
    out = lc.build_mainline_lifecycle([live], [], final=True)
    assert out["mode"] == "close"
    assert out["stage_map"] == {"BK1": "ending"}
    assert [r["bk"] for r in out["ending"]] == ["BK1"]


def test_weekend_rows_are_skipped(monkeypatch):
    _no_bias(monkeypatch)
    series = _hist("BK1", [
        ("2026-09-18", 1, 0.5, ""),
        ("2026-09-19", 1, 0.5, ""),
    ])
    closed = lc._closed_series_map({"BK1": series})
    assert [r["trade_date"] for r in closed["BK1"]] == ["2026-09-18"]
