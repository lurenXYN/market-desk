"""WeChat buy/fly pushes are deduped per code per trade day."""

from __future__ import annotations

import market_desk.db as db
from market_desk import notify


def _fake_store(monkeypatch):
    store: dict[str, object] = {}
    monkeypatch.setattr(db, "load_setting", lambda key: store.get(key))
    monkeypatch.setattr(db, "save_setting", lambda key, value: store.__setitem__(key, value))
    return store


def test_fly_and_buy_once_per_day(monkeypatch):
    store = _fake_store(monkeypatch)
    alerts = [
        ("fly:159819", "将飞", "b"),
        ("buy:159819", "可买", "b"),
        ("sell:600000", "卖", "b"),
    ]
    kept, marked = notify.drop_daily_repeats(alerts, trade_date="2026-09-24")
    assert [k for k, _, _ in kept] == ["fly:159819", "buy:159819", "sell:600000"]
    notify._mark_daily_once("2026-09-24", marked)

    kept2, marked2 = notify.drop_daily_repeats(alerts, trade_date="2026-09-24")
    assert [k for k, _, _ in kept2] == ["sell:600000"]
    assert marked2 == []

    kept3, _ = notify.drop_daily_repeats(alerts, trade_date="2026-09-25")
    assert len(kept3) == 3
    assert "sc_daily_once:2026-09-24" in store


def test_duplicate_in_same_batch_dropped(monkeypatch):
    _fake_store(monkeypatch)
    alerts = [("fly:1", "a", ""), ("fly:1", "a", "")]
    kept, marked = notify.drop_daily_repeats(alerts, trade_date="2026-09-24")
    assert len(kept) == 1
    assert marked == ["fly:1"]
