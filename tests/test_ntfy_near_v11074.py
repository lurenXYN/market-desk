"""near-entry prep alerts for ntfy (half-size, not full buy)."""

from __future__ import annotations

from market_desk.notify import build_near_entry_alerts, _push_title
from market_desk.ntfy_push import enrich_ntfy_alert, is_ntfy_alert


def test_near_edge_fires_once_shape():
    prev = {
        "ok": True,
        "verdict": {"recommend": {"items": [{"code": "600000", "near_entry": False}]}},
    }
    cur = {
        "ok": True,
        "verdict": {
            "recommend": {
                "items": [
                    {
                        "code": "600000",
                        "name": "浦发",
                        "near_entry": True,
                        "ready": False,
                        "buy_price": 10.0,
                        "last": 10.1,
                    }
                ]
            }
        },
    }
    alerts = build_near_entry_alerts(prev, cur)
    assert len(alerts) == 1
    assert alerts[0][0] == "near:600000"
    assert "接近准备" in alerts[0][2]
    # Already near → no re-fire.
    assert build_near_entry_alerts(cur, cur) == []


def test_near_skips_full_ready_and_fly():
    prev = {"ok": True, "verdict": {"recommend": {"items": []}}}
    cur = {
        "ok": True,
        "verdict": {
            "recommend": {
                "items": [
                    {
                        "code": "600000",
                        "near_entry": True,
                        "ready": True,
                        "ready_relaxed": False,
                    },
                    {
                        "code": "000001",
                        "near_entry": True,
                        "ready": False,
                        "fly_warn": True,
                    },
                ]
            }
        },
    }
    assert build_near_entry_alerts(prev, cur) == []


def test_ntfy_accepts_near_and_enrich_marks_prep():
    assert is_ntfy_alert("near:600000")
    assert is_ntfy_alert("band:entry:600000")
    assert not is_ntfy_alert("near:600000", near=False)
    assert "接近·准备" in _push_title("near", "浦发", "600000")
    _k, _t, body = enrich_ntfy_alert(
        "near:600000",
        "【接近·准备】浦发 600000",
        "现价 10",
        {
            "verdict": {
                "recommend": {
                    "items": [
                        {
                            "code": "600000",
                            "name": "浦发",
                            "buy_price": 10.0,
                            "near_entry": True,
                        }
                    ]
                }
            }
        },
    )
    assert "对齐模拟" in body or "不足一手" in body
    assert "非系统可买" in body
