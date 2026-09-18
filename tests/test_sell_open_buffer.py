"""Unit tests for open-auction sell buffer (must vs watch)."""

from __future__ import annotations

from datetime import datetime

from market_desk.sell_open_buffer import (
    apply_sell_open_buffer,
    classify_sell_open_track,
    evaluate_watch_still_weak,
    sell_open_clock,
)


def test_classify_must_stop() -> None:
    assert classify_sell_open_track({"ready": True, "urgency": "stop", "exit_mode": "clear"}) == "must"


def test_classify_watch_half() -> None:
    assert classify_sell_open_track({"ready": True, "urgency": "trim", "exit_mode": "half"}) == "watch"


def test_classify_skip_not_ready() -> None:
    assert classify_sell_open_track({"ready": False, "urgency": "trim", "exit_mode": "half"}) is None


def test_clock_in_watch() -> None:
    now = datetime(2026, 9, 18, 9, 35, 0)  # Friday
    c = sell_open_clock(now, watch_minutes=15)
    assert c["in_watch"] is True
    assert c["deadline_hhmm"] == "09:45"


def test_defer_watch_during_buffer() -> None:
    now = datetime(2026, 9, 18, 9, 32, 0)
    advice = {
        "all_items": [
            {
                "code": "600000",
                "kind": "stock",
                "ready": True,
                "urgency": "trim",
                "exit_mode": "half",
                "role_label": "建议先减",
                "sell_price": 10.0,
                "sell_pct": 50,
                "sell_qty": 100,
                "last": 10.0,
                "open": 10.2,
                "stop_price": 9.5,
                "pct": -1.0,
                "reason": "软减",
            }
        ],
        "items": [],
        "size_note": "",
    }
    out = apply_sell_open_buffer(advice, now=now, watch_minutes=15)
    item = out["all_items"][0]
    assert item["open_buffer_track"] == "watch"
    assert item["open_buffer_phase"] == "watching"
    assert item["ready"] is False
    assert out.get("open_buffer", {}).get("deferred_n") == 1


def test_must_stays_ready_in_buffer() -> None:
    now = datetime(2026, 9, 18, 9, 32, 0)
    advice = {
        "all_items": [
            {
                "code": "600000",
                "kind": "stock",
                "ready": True,
                "urgency": "stop",
                "exit_mode": "clear",
                "role_label": "止损清仓",
                "sell_price": 9.0,
                "sell_pct": 100,
                "sell_qty": 200,
                "last": 9.0,
                "stop_price": 9.1,
                "reason": "止损",
            }
        ],
        "items": [],
        "size_note": "",
    }
    out = apply_sell_open_buffer(advice, now=now, watch_minutes=15)
    item = out["all_items"][0]
    assert item["open_buffer_track"] == "must"
    assert item["ready"] is True


def test_after_watch_recover_to_hold() -> None:
    now = datetime(2026, 9, 18, 9, 50, 0)
    item = {
        "ready": True,
        "urgency": "trim",
        "exit_mode": "half",
        "last": 10.3,
        "open": 10.2,
        "stop_price": 9.5,
        "pct": 0.5,
    }
    j = evaluate_watch_still_weak(item)
    assert j["recovered"] is True
    advice = {"all_items": [{**item, "kind": "stock", "role_label": "先减", "reason": "x"}], "items": [], "size_note": ""}
    out = apply_sell_open_buffer(advice, now=now, watch_minutes=15)
    assert out["all_items"][0]["open_buffer_phase"] == "released"
    assert out["all_items"][0]["ready"] is False


def test_after_watch_still_weak() -> None:
    item = {
        "last": 10.0,
        "open": 10.3,
        "stop_price": 9.5,
        "pct": -1.2,
    }
    j = evaluate_watch_still_weak(item)
    assert j["still_weak"] is True


def test_minute_broke_open_still_weak() -> None:
    from market_desk.sell_open_buffer import summarize_open_buffer_minutes

    minutes = [
        {"time": "2026-09-18 09:31", "price": 10.0, "avg": 10.0, "volume": 100},
        {"time": "2026-09-18 09:32", "price": 9.95, "avg": 9.98, "volume": 120},
        {"time": "2026-09-18 09:35", "price": 9.90, "avg": 9.95, "volume": 150},
        {"time": "2026-09-18 09:40", "price": 9.88, "avg": 9.93, "volume": 200},
        {"time": "2026-09-18 09:45", "price": 9.85, "avg": 9.92, "volume": 180},
    ]
    feat = summarize_open_buffer_minutes(minutes, open_px=10.0, deadline_hhmm="09:45")
    assert feat["broke_open"] is True
    assert feat["below_vwap"] is True
    item = {"last": 9.85, "open": 10.0, "stop_price": 9.0, "pct": -1.5}
    j = evaluate_watch_still_weak(item, minutes, deadline_hhmm="09:45")
    assert j["source"] == "minute"
    assert j["still_weak"] is True
    assert "分时" in j["note"]


def test_minute_recover_above_vwap() -> None:
    minutes = [
        {"time": "09:31", "price": 10.0, "avg": 10.0, "volume": 100},
        {"time": "09:33", "price": 10.01, "avg": 10.005, "volume": 80},
        {"time": "09:36", "price": 10.03, "avg": 10.01, "volume": 90},
        {"time": "09:40", "price": 10.05, "avg": 10.02, "volume": 70},
        {"time": "09:45", "price": 10.08, "avg": 10.03, "volume": 60},
    ]
    item = {"last": 10.08, "open": 10.0, "stop_price": 9.5, "pct": 0.8, "code": "600000"}
    j = evaluate_watch_still_weak(item, minutes, deadline_hhmm="09:45")
    assert j["source"] == "minute"
    assert j["recovered"] is True


def test_reapply_after_defer_restores_pending() -> None:
    """Minute re-apply after deferral must re-arm from pending."""
    now_watch = datetime(2026, 9, 18, 9, 32, 0)
    now_after = datetime(2026, 9, 18, 9, 50, 0)
    advice = {
        "all_items": [
            {
                "code": "600000",
                "kind": "stock",
                "ready": True,
                "urgency": "trim",
                "exit_mode": "half",
                "role_label": "建议先减",
                "sell_price": 10.0,
                "sell_pct": 50,
                "sell_qty": 100,
                "last": 9.8,
                "open": 10.2,
                "stop_price": 9.0,
                "pct": -2.0,
                "reason": "软减",
            }
        ],
        "items": [],
        "size_note": "",
    }
    deferred = apply_sell_open_buffer(advice, now=now_watch, watch_minutes=15)
    assert deferred["all_items"][0]["ready"] is False
    minutes = {
        "600000": [
            {"time": "09:31", "price": 10.1, "avg": 10.1, "volume": 100},
            {"time": "09:35", "price": 10.0, "avg": 10.05, "volume": 200},
            {"time": "09:40", "price": 9.9, "avg": 10.0, "volume": 300},
            {"time": "09:45", "price": 9.85, "avg": 9.98, "volume": 400},
        ]
    }
    armed = apply_sell_open_buffer(
        deferred, now=now_after, watch_minutes=15, minutes_by_code=minutes
    )
    item = armed["all_items"][0]
    assert item["open_buffer_phase"] == "armed"
    assert item["ready"] is True
    assert item["exit_mode"] == "half"
