"""After the close, lifecycle_close is sealed once and not overwritten."""

from __future__ import annotations

from datetime import datetime

import market_desk.lifecycle as lc
from market_desk.engine import boards as boards_mod


def _no_bias(monkeypatch):
    monkeypatch.setattr(lc, "_review_lifecycle_bias", lambda: {"strict": False, "hit_rate": None, "n": 0})


def test_close_map_sealed_once(monkeypatch):
    _no_bias(monkeypatch)
    store: dict[str, dict] = {}

    def load_setting(key: str):
        return store.get(key)

    def save_setting(key: str, value):
        store[key] = value

    monkeypatch.setattr("market_desk.db.load_setting", load_setting)
    monkeypatch.setattr("market_desk.db.save_setting", save_setting)
    monkeypatch.setattr(boards_mod, "is_trading_day", lambda _now: True)

    live_a = {
        "bk": "BK1",
        "name": "BK1",
        "status": "退潮",
        "zt_n": 0,
        "pct": -2.0,
        "hist": [{"trade_date": "2026-09-23", "zt_n": 5, "pct": 3.0, "status": "确认中"}],
        "tags": [],
    }
    now = datetime(2026, 9, 24, 15, 1, 0)
    first = boards_mod._stable_mainline_lifecycle([live_a], [], {}, now=now)
    assert first.get("sealed") is True
    sealed = store["lifecycle_close:2026-09-24"]
    assert sealed.get("BK1") == "ending"
    assert sealed.get(lc.lifecycle_stable_key(live_a)) == "ending"

    live_b = dict(live_a)
    live_b["status"] = "确认中"
    live_b["zt_n"] = 4
    live_b["pct"] = 2.0
    # Would classify as ongoing/starting if re-finalized — seal must keep ending.
    second = boards_mod._stable_mainline_lifecycle([live_b], [], {}, now=now)
    assert second.get("sealed") is True
    assert second["mode"] == "close"
    assert store["lifecycle_close:2026-09-24"].get("BK1") == "ending"
    assert second["stage_map"].get("BK1") == "ending"
