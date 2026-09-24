"""Personal sell alerts go only to the owner, once per code/urgency per day."""

from __future__ import annotations

from datetime import datetime

import market_desk.db as db
from market_desk import notify


def _setup(monkeypatch, *, sell_only: bool = False):
    store: dict[str, object] = {}
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(db, "load_setting", lambda key: store.get(key))
    monkeypatch.setattr(db, "save_setting", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(db, "load_user_setting", lambda uid, key: {"serverchan_sell_only": sell_only})
    monkeypatch.setattr(notify, "notify_serverchan", lambda key, title, desp: sent.append((key, title)) or True)
    return store, sent


def _snap(*items):
    return {"trade_date": "2026-09-24", "sell_advice": {"items": list(items)}, "verdict": {}}


STOP = {"ready": True, "urgency": "stop", "code": "001399", "name": "惠科股份", "sell_price": 24.31, "pnl_pct": -6.2}
TAKE = {"ready": True, "urgency": "take", "code": "600641", "name": "先导基电", "sell_price": 48.6, "pnl_pct": 12.4}
WATCH = {"ready": False, "urgency": "trim", "code": "600000", "name": "浦发银行"}


def test_pushes_to_owner_once_per_day(monkeypatch):
    store, sent = _setup(monkeypatch)
    user = {"id": 7, "username": "a", "serverchan_sendkey": "SCT7"}
    assert notify.push_user_sell_alerts(user, _snap(STOP, TAKE, WATCH), trade_date="2026-09-24") == 2
    assert [k for k, _ in sent] == ["SCT7", "SCT7"]
    assert sent[0][1] == "【止损】惠科股份 001399"
    assert notify.push_user_sell_alerts(user, _snap(STOP, TAKE), trade_date="2026-09-24") == 0
    assert set(store["sc_sell_once:2026-09-24:7"]) == {"sell:stop:001399", "sell:take:600641"}


def test_sell_only_keeps_stop(monkeypatch):
    _, sent = _setup(monkeypatch, sell_only=True)
    user = {"id": 8, "serverchan_sendkey": "SCT8"}
    assert notify.push_user_sell_alerts(user, _snap(STOP, TAKE), trade_date="2026-09-24") == 1
    assert "止损" in sent[0][1]


def test_sell_push_window():
    assert not notify.is_sell_push_window(datetime(2026, 9, 24, 9, 20), trading_day=True)
    assert notify.is_sell_push_window(datetime(2026, 9, 24, 9, 25), trading_day=True)
    assert not notify.is_sell_push_window(datetime(2026, 9, 24, 12, 0), trading_day=True)
    assert notify.is_sell_push_window(datetime(2026, 9, 24, 14, 59), trading_day=True)
    assert not notify.is_sell_push_window(datetime(2026, 9, 24, 15, 0), trading_day=True)
    assert not notify.is_sell_push_window(datetime(2026, 9, 26, 10, 0), trading_day=False)
