"""Server酱 quota: by default only the scheduled EOD push leaves market-desk."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import market_desk.db as db
from market_desk import notify
from market_desk.engine import CN_TZ, DeskEngine

EVENTS = [
    ("buy:600000", "可买", "x"),
    ("fly:600001", "将飞", "x"),
    ("sell:stop:600002", "止损", "x"),
    ("lhb:worse:600003", "龙虎变坏", "x"),
    ("ops:backup:2026-09-28", "自动备份失败", "x"),
    ("ops:db:2026-09-28", "数据库损坏", "x"),
]


def test_default_filter_keeps_only_scheduled_pushes() -> None:
    alerts = [*EVENTS, ("eod:2026-09-28", "收盘一页纸", "x"), ("morning:2026-09-28", "早决策", "x")]
    kept = [k for k, _t, _b in notify.filter_serverchan_alerts(alerts)]
    assert kept == ["eod:2026-09-28", "morning:2026-09-28"]


def test_event_fanout_sends_nothing(monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr(db, "load_setting", lambda key: None)
    monkeypatch.setattr(db, "save_setting", lambda key, value: None)
    monkeypatch.setattr(db, "load_user_setting", lambda uid, key: {})
    monkeypatch.setattr(
        db, "list_serverchan_recipients", lambda: [{"id": 1, "serverchan_sendkey": "SCT1"}]
    )
    monkeypatch.setattr(notify, "notify_serverchan", lambda key, title, desp: sent.append(title) or True)
    assert notify.push_serverchan_alerts(list(EVENTS), {"trade_date": "2026-09-28"}) == 0
    assert sent == []


def test_personal_sells_and_morning_brief_are_skipped() -> None:
    now = datetime(2026, 9, 28, 9, 30, tzinfo=CN_TZ)
    stub = SimpleNamespace()
    assert DeskEngine._push_personal_sells(stub, now) == 0
    assert asyncio.run(DeskEngine._maybe_push_morning_brief(stub, now)) is None
