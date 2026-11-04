"""v1.10.62–63: shared sell finalize, alert_items, favorite reuse, soft demote."""

from __future__ import annotations

from market_desk.sell_finalize import demote_soft_sells_on_gate_error, finalize_sell_advice
from market_desk.verdict.desk import _main_sell_by_code, _sell_plan_from_main_items, build_favorite_desk_plans


def test_demote_soft_keeps_hard_stop():
    advice = {
        "all_items": [
            {
                "code": "600000",
                "ready": True,
                "urgency": "stop",
                "exit_mode": "clear",
                "role_label": "止损清仓",
            },
            {
                "code": "600001",
                "ready": True,
                "urgency": "take",
                "exit_mode": "half",
                "role_label": "止盈半仓",
            },
        ],
        "items": [],
    }
    out = demote_soft_sells_on_gate_error(advice)
    by = {it["code"]: it for it in out["all_items"]}
    assert by["600000"]["ready"] is True
    assert by["600001"]["ready"] is False
    assert by["600001"].get("gate_error_hold") is True
    assert out.get("gate_error_demoted") == 1


def test_finalize_sets_alert_items_and_truncates_ui(monkeypatch):
    monkeypatch.setattr(
        "market_desk.minute_confirm.apply_sell_minute_gates",
        lambda advice, minutes: advice,
    )
    monkeypatch.setattr(
        "market_desk.sell_open_buffer.apply_sell_open_buffer",
        lambda advice, **kw: advice,
    )
    monkeypatch.setattr(
        "market_desk.sell_open_buffer.hold_sells_before_match",
        lambda advice: advice,
    )

    items = [
        {"code": f"60000{i}", "ready": True, "urgency": "take", "exit_mode": "half"}
        for i in range(6)
    ]
    advice = {"all_items": items, "items": items[:4]}
    out, reconciled = finalize_sell_advice(
        advice, reconcile=False, hold_before_match=True, ui_top_n=4
    )
    assert reconciled is None
    assert len(out["alert_items"]) == 6
    assert len(out["items"]) == 4
    assert out["alert_items"][5]["code"] == "600005"


def test_favorite_sell_reuses_main_advice():
    main = {
        "alert_items": [
            {
                "code": "600519",
                "ready": True,
                "urgency": "take",
                "exit_mode": "half",
                "role_label": "主台止盈半仓",
                "reason": "回撤触轨",
            }
        ]
    }
    by = _main_sell_by_code(main)
    assert "600519" in by
    plan = _sell_plan_from_main_items([dict(by["600519"])], board_name="白酒")
    assert plan["from_main"] is True
    assert plan["sell"] is True
    assert plan["items"][0]["role_label"] == "主台止盈半仓"

    desk = build_favorite_desk_plans(
        favorite_boards=[{"name": "白酒", "bk": "BK0481", "status": "hot", "pool": [{"code": "600519"}]}],
        etfs=[],
        zt=[],
        zb=[],
        positions=[{"code": "600519", "qty": 100, "name": "茅台"}],
        phase="continuous",
        main_sell_advice=main,
    )
    boards = desk.get("boards") or []
    assert boards
    sell = boards[0].get("sell") or {}
    assert sell.get("from_main") is True
    assert any(it.get("code") == "600519" for it in (sell.get("items") or []))
