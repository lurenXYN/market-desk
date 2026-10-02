"""Mainline-decline toast edge and the cross-tab sound alert feed."""

from __future__ import annotations

from datetime import datetime

from market_desk.notify import (
    build_alert_feed,
    build_damped_toast_alerts,
    build_toast_alerts,
    cooldown_for_key,
    is_decision_toast,
    toast_priority,
)


def _snap(ml: str, *, life: str = "ongoing", status: str = "确认中") -> dict:
    return {
        "ok": True,
        "phase": "分歧",
        "verdict": {"action": "观望", "mainline": {"name": ml, "lifecycle": life, "status": status}},
    }


def _keys(alerts: list[tuple[str, str, str]]) -> list[str]:
    return [a[0] for a in alerts]


def test_decline_fires_once_when_same_mainline_turns_fading() -> None:
    alerts = build_toast_alerts(_snap("半导体"), _snap("半导体", life="ending"))
    hit = [a for a in alerts if a[0] == "decline:半导体"]
    assert hit and "生命周期转衰退" in hit[0][2]
    assert "decline:半导体" in _keys(build_toast_alerts(_snap("半导体"), _snap("半导体", status="退潮")))
    # Already fading last round, or a switch to a new name: no decline edge.
    assert "decline:半导体" not in _keys(
        build_toast_alerts(_snap("半导体", status="退潮"), _snap("半导体", life="ending"))
    )
    switched = _keys(build_toast_alerts(_snap("白酒"), _snap("半导体", life="ending")))
    assert "mainline:半导体" in switched and not any(k.startswith("decline:") for k in switched)


def test_decline_policy_cooldown_and_damping() -> None:
    assert is_decision_toast("decline:半导体")
    assert toast_priority("decline:半导体") == toast_priority("exit:观望:半导体")
    assert cooldown_for_key("decline:半导体", 180) == 3600.0
    assert cooldown_for_key("decline:半导体", 7200) == 7200.0
    damped = build_damped_toast_alerts(_snap("半导体"), _snap("半导体", life="ending"), damping=True)
    assert not any(k.startswith("decline:") for k in _keys(damped))


def _sell_snap() -> dict:
    return {
        "trading_day": True,
        "recent_toasts": [
            {"ts": "10:01:00", "key": "phase:恐慌", "title": "相位 · 恐慌", "body": "long body"},
            {"ts": "09:58:00", "title": "no key"},
        ],
        "sell_advice": {
            "items": [
                {"code": "601579", "name": "会稽山", "ready": True, "urgency": "stop",
                 "role_label": "止损清仓", "exit_mode": "clear", "sell_price": 9.1, "sell_qty": 500},
                {"code": "600519", "name": "茅台", "ready": False, "urgency": "stop"},
                {"code": "300750", "name": "宁德", "ready": True, "urgency": "take"},
            ]
        },
    }


def test_alert_feed_lists_own_ready_stops_in_session_only() -> None:
    feed = build_alert_feed(_sell_snap(), now=datetime(2026, 10, 9, 10, 5))
    assert feed["toasts"] == [{"ts": "10:01:00", "key": "phase:恐慌", "title": "相位 · 恐慌"}]
    assert [s["code"] for s in feed["stops"]] == ["601579"]
    assert feed["stops"][0]["exit_mode"] == "clear" and feed["stops"][0]["sell_qty"] == 500
    for now in (datetime(2026, 10, 9, 9, 20), datetime(2026, 10, 9, 12, 0), datetime(2026, 10, 9, 15, 30)):
        assert build_alert_feed(_sell_snap(), now=now)["stops"] == []
    closed = dict(_sell_snap(), trading_day=False)
    assert build_alert_feed(closed, now=datetime(2026, 10, 9, 10, 5))["stops"] == []
    assert build_alert_feed(None, now=datetime(2026, 10, 9, 10, 5)) == {"toasts": [], "stops": []}


def test_every_tab_slice_carries_alert_feed() -> None:
    from market_desk.engine.core import engine

    prev = engine.snapshot
    engine.snapshot = dict(_sell_snap(), ok=True)
    try:
        for view in ("desk", "market", "funds", "watch", "auction", "pos", "review", "boards"):
            sliced = engine.slice_snapshot(view)
            assert set(sliced["alert_feed"]) == {"toasts", "stops"}, view
    finally:
        engine.snapshot = prev
