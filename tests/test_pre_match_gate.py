"""No buy/sell signals before the 09:25 call-auction match."""

from __future__ import annotations

from datetime import datetime

from market_desk import notify
from market_desk.review import is_pre_match_stamp, record_session_signals
from market_desk.sell_open_buffer import hold_sells_before_match


def test_pre_match_stamp_boundary():
    assert is_pre_match_stamp("2026-09-24 08:50:00")
    assert is_pre_match_stamp("2026-09-24 09:24:59")
    assert not is_pre_match_stamp("2026-09-24 09:25:00")
    assert not is_pre_match_stamp("2026-09-24 10:00:00")
    assert not is_pre_match_stamp("")


def test_record_session_signals_skips_before_match():
    snap = {
        "trade_date": "2026-09-24",
        "updated_at": "2026-09-24 09:16:04",
        "verdict": {"recommend": {"items": [{"code": "600000", "buy_price": 10.0}]}},
    }
    assert record_session_signals(snap) == 0


def test_trade_toasts_dropped_before_match():
    alerts = [
        ("sell:stop:600000", "止损", ""),
        ("band:stop:600000", "止损价", ""),
        ("buy:600000", "可买", ""),
        ("ops:backup", "备份", ""),
        ("lhb:worsen:600000", "龙虎", ""),
    ]
    assert notify.is_pre_match_window(datetime(2026, 9, 24, 9, 20))
    assert not notify.is_pre_match_window(datetime(2026, 9, 24, 9, 25))
    kept = notify.filter_alerts_for_policy(alerts, pre_match=True)
    assert [k for k, _, _ in kept] == ["ops:backup", "lhb:worsen:600000"]


def test_ready_sells_demoted_before_match():
    adv = {"items": [{"code": "600000", "ready": True, "role_label": "止损清仓"}, {"code": "1", "ready": False}]}
    held = hold_sells_before_match(adv, datetime(2026, 9, 24, 9, 18))
    first = held["items"][0]
    assert first["ready"] is False and first["pre_match_hold"] is True
    assert first["next_action_zh"] == "9:25 后定"
    assert "9:25" in first["role_label"]
    assert held["items"][1] == {"code": "1", "ready": False}

    live = hold_sells_before_match(adv, datetime(2026, 9, 24, 9, 25))
    assert live["items"][0]["ready"] is True
