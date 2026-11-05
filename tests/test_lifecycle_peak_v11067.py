"""Lifecycle peak column decoupled from same-day tip-chase (v1.10.67)."""

from __future__ import annotations

from datetime import datetime

import market_desk.lifecycle as lc
from market_desk.engine.boards import force_reseal_lifecycle


def _no_bias(monkeypatch):
    monkeypatch.setattr(lc, "_review_lifecycle_bias", lambda: {"strict": False, "hit_rate": None, "n": 0})


def _hist(bk: str, rows: list[tuple[str, int, float, str]]):
    return [
        {"trade_date": d, "bk": bk, "name": bk, "zt_n": z, "pct": p, "status": s, "leader_boards": 1}
        for d, z, p, s in rows
    ]


def test_leader_boards_tip_after_heat_is_peak(monkeypatch):
    _no_bias(monkeypatch)
    hist = _hist("BK3", [("2026-09-23", 3, 2.0, "确认中")])
    card = {
        "bk": "BK3",
        "name": "BK3",
        "status": "确认中",
        "zt_n": 5,
        "pct": 3.0,
        "leader_boards": 3,
        "hist": hist,
        "tags": [],
    }
    assert lc.classify_lifecycle(card) == "peak"


def test_force_reseal_overwrites_prev_day_before_close(monkeypatch):
    _no_bias(monkeypatch)
    saved: dict[str, dict] = {}

    def _save(key, value):
        saved[key] = value

    monkeypatch.setattr("market_desk.db.save_setting", _save)
    monkeypatch.setattr(
        "market_desk.engine.boards.is_trading_day",
        lambda _d: True,
    )
    monkeypatch.setattr(
        "market_desk.engine.boards._prev_trading_day",
        lambda _now: "2026-11-04",
    )
    # 10:00 — before close → reseal prev day.
    now = datetime(2026, 11, 5, 10, 0)
    card = {
        "bk": "BK_NEW",
        "name": "新题材",
        "status": "尖峰禁追",
        "zt_n": 5,
        "pct": 5.0,
        "leader_boards": 3,
        "hist": [],
        "tags": [],
    }
    result = force_reseal_lifecycle([card], [], {}, now=now)
    assert result["ok"] is True
    assert result["seal_day"] == "2026-11-04"
    assert result["after_close"] is False
    assert "lifecycle_close:2026-11-04" in saved
    assert saved["lifecycle_close:2026-11-04"].get("BK_NEW") == "starting"
    life = result["lifecycle"]
    assert life.get("resealed") is True
    assert [r["bk"] for r in life.get("starting") or []] == ["BK_NEW"]
