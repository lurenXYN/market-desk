"""Tests for shallow-fly window tagging and toast edges."""

from __future__ import annotations

from market_desk.notify import build_fly_window_alerts, is_decision_toast, is_serverchan_alert
from market_desk.verdict import finalize_recommend_buy_ux, tag_fly_window_items


def test_tag_fly_window_on_band_ready(monkeypatch) -> None:
    from market_desk import settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "setting", lambda k, d=None: "band" if k == "ready_style" else d
    )
    rec = {
        "items": [
            {
                "code": "512480",
                "kind": "etf",
                "near_entry": True,
                "ready": False,
                "last": 1.02,
                "chase_price": 1.08,
                "confirm_fail": ["分时贴近近期高点"],
                "qty": 200,
            }
        ]
    }
    out = finalize_recommend_buy_ux(rec)
    item = out["items"][0]
    assert item.get("ready_relaxed") is True
    assert item.get("fly_warn") is True
    assert "再等可能飞" in str(item.get("fly_note") or "")


def test_fly_toast_edge_only() -> None:
    item = {
        "code": "600000",
        "name": "测试",
        "fly_warn": True,
        "ready_relaxed": True,
        "buy_price": 10.0,
    }
    prev = {"ok": True, "verdict": {"recommend": {"items": []}}}
    cur = {"ok": True, "verdict": {"recommend": {"items": [item]}}}
    alerts = build_fly_window_alerts(prev, cur)
    assert len(alerts) == 1
    assert alerts[0][0] == "fly:600000"
    assert "半仓" in alerts[0][1]
    # Second round with same fly should not re-fire.
    again = build_fly_window_alerts(cur, cur)
    assert again == []
    assert is_decision_toast("fly:600000")
    assert is_serverchan_alert("fly:600000")


def test_tag_fly_skips_without_half_window() -> None:
    rec = {
        "items": [
            {
                "code": "600000",
                "near_entry": False,
                "ready": False,
                "probe_ok": False,
                "confirm_fail": ["分时贴近近期高点"],
            }
        ]
    }
    out = tag_fly_window_items(rec)
    assert not out["items"][0].get("fly_warn")
