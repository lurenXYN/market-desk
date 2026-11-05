"""v1.10.64–65: user book cool, reconcile held soft-sell, exec armed triggers."""

from __future__ import annotations

from market_desk.exec.condition import sell_item_to_trigger
from market_desk.settings import current_open_positions, current_user_id, use_user_settings
from market_desk.verdict.sell_advice import reconcile_buy_sell_conflict


def test_current_user_id_under_overlay():
    assert current_user_id() is None
    with use_user_settings(None):
        assert current_user_id() is None
    # user_id=1 may lack DB rows; ContextVar must still stamp.
    with use_user_settings(1):
        assert current_user_id() == 1
        # Empty book is fine when fixture user has no positions.
        assert isinstance(current_open_positions(), list)
    assert current_user_id() is None


def test_reconcile_keeps_soft_sell_on_held_code():
    verdict = {
        "recommend": {
            "items": [
                {"code": "600519", "ready": True, "kind": "stock", "role_label": "可买"},
            ]
        },
        "action": "可买入",
    }
    advice = {
        "all_items": [
            {
                "code": "600519",
                "ready": True,
                "urgency": "take",
                "exit_mode": "half",
                "role_label": "止盈半仓",
                "kind": "stock",
            }
        ],
        "items": [],
    }
    positions = [{"code": "600519", "qty": 100}]
    v2, a2 = reconcile_buy_sell_conflict(verdict, advice, positions)
    sell = (a2.get("all_items") or a2.get("items") or [])[0]
    assert sell.get("ready") is True, "held soft sell must survive buy-conflict"
    buy = (v2.get("recommend") or {}).get("items") or []
    assert buy and buy[0].get("ready") is False
    assert buy[0].get("held_block") is True


def test_exec_armed_ready_is_executable():
    trig = sell_item_to_trigger(
        {
            "code": "600000",
            "ready": True,
            "urgency": "take",
            "exit_mode": "half",
            "open_buffer_track": "watch",
            "open_buffer_phase": "armed",
            "sell_price": 10.5,
            "trigger_price": 10.5,
        }
    )
    assert trig.executable is True
    assert trig.reason == "open_buffer_armed"


def test_exec_watching_not_executable():
    trig = sell_item_to_trigger(
        {
            "code": "600000",
            "ready": False,
            "urgency": "take",
            "exit_mode": "half",
            "open_buffer_track": "watch",
            "open_buffer_phase": "watching",
            "sell_price": 10.5,
        }
    )
    assert trig.executable is False
    assert "watching" in trig.reason


def test_sell_triggers_requires_advice():
    from market_desk.exec.engine import ExecEngine

    eng = ExecEngine(persist=False)
    assert eng.sell_triggers() == []
    assert eng.sell_triggers({}) == []
    out = eng.sell_triggers(
        {
            "alert_items": [
                {
                    "code": "600000",
                    "ready": True,
                    "urgency": "stop",
                    "exit_mode": "clear",
                    "sell_price": 9.0,
                }
            ]
        }
    )
    assert len(out) == 1
    assert out[0].get("executable") is True
