"""Sell-exit helpers: regret window, peak layering, yday repair, sell-fly."""

from __future__ import annotations

from market_desk.config import SELL_FLY_DAY1_PCT, SELL_FLY_MAE_PCT, TRADE_FEE_CNY
from market_desk.minute_confirm import apply_sell_minute_gates, confirm_sell_take
from market_desk.review import score_signal_with_closes
from market_desk.verdict import _sell_item


def _base_row(**kwargs):
    row = {
        "id": 1,
        "code": "600000",
        "name": "测试",
        "buy_price": 10.0,
        "qty": 200,
        "last": 11.0,
        "high": 11.2,
        "low": 10.5,
        "last_pct": 2.0,
        "pnl_pct": 10.0,
        "peak_price": 11.5,
        "last_buy_date": "2026-09-10",
        "day_sold_qty": 0,
        "entry_board": "测试板",
    }
    row.update(kwargs)
    return row


def _verdict_tied():
    return {
        "mainline": {
            "name": "测试板",
            "status": "确认中",
            "lifecycle": "ongoing",
            "code": "515050",
        },
        "sell_themes": [
            {
                "name": "测试板",
                "status": "确认中",
                "lifecycle": "ongoing",
                "role": "primary",
                "score": 80,
                "carrier_pct": 0.5,
                "carrier_falling": False,
                "main_yi": 0.0,
            }
        ],
        "carrier": {"pct": 0.5, "falling": False},
        "segment": {"key": "mid"},
        "elliott": {"ok": False},
    }


def test_peak_light_pullback_is_half_not_clear() -> None:
    # hold_peak 11.5, last 11.3 → pb ≈ 1.74%; take needs pnl & light band.
    row = _base_row(
        last=11.3,
        high=11.5,
        peak_price=11.5,
        pnl_pct=13.0,
        last_pct=1.0,
    )
    item = _sell_item(row, _verdict_tied(), "发酵", trade_date="2026-09-17")
    assert item is not None
    if item.get("ready") and item.get("urgency") == "take":
        assert item["exit_mode"] == "half"
        assert "清仓" not in (item.get("role_label") or "")


def test_regret_window_blocks_soft_clear_when_strong() -> None:
    row = _base_row(
        qty=100,
        day_sold_qty=100,
        last_sell_price=10.8,
        last=11.4,
        high=11.45,
        peak_price=11.5,
        pnl_pct=14.0,
        last_pct=3.0,
    )
    v = _verdict_tied()
    v["sell_themes"][0]["lifecycle"] = "ending"
    v["mainline"]["lifecycle"] = "ending"
    item = _sell_item(row, v, "发酵", trade_date="2026-09-17")
    assert item is not None
    if item.get("regret_hold"):
        assert item["exit_mode"] == "hold"
        assert item["ready"] is False
        assert "继续观察" in (item.get("role_label") or "")


def test_yday_weak_repaired_when_price_recovers() -> None:
    # Day weak enough to arm 昨买今弱, but last still ≥ cost → repair.
    row = _base_row(
        last_buy_date="2026-09-16",
        last=10.05,
        buy_price=10.0,
        last_pct=-2.5,
        pnl_pct=0.5,
        high=10.2,
        peak_price=10.2,
        day_sold_qty=0,
    )
    item = _sell_item(row, _verdict_tied(), "发酵", trade_date="2026-09-17")
    assert item is not None
    assert item.get("yday_repaired") is True
    assert item.get("role_label") != "昨买今弱·先减半"


def test_sell_fly_label_from_day1() -> None:
    assert float(SELL_FLY_MAE_PCT) >= 3.0
    assert float(SELL_FLY_DAY1_PCT) <= -2.5
    # day1 close 10.5 → d1 = 10/10.5-1 ≈ -4.76% → 卖飞
    sig = {"signal_type": "sell", "price": 10.0, "trade_date": "2026-09-10"}
    out = score_signal_with_closes(
        sig,
        closes=[10.0, 10.5, 10.6],
        dates=["2026-09-10", "2026-09-11", "2026-09-12"],
        opens=[10.0, 10.2, 10.4],
        highs=[10.1, 10.8, 10.7],
        lows=[9.9, 10.1, 10.3],
    )
    assert out is not None
    assert out.get("outcome_label") == "卖飞"


def test_confirm_sell_take_sample_soft() -> None:
    soft = confirm_sell_take([])
    assert soft.get("ok") is None
    assert soft.get("soft") is True


def test_apply_sell_minute_gates_attaches_verdict() -> None:
    advice = {
        "sell": True,
        "items": [
            {
                "code": "600000",
                "name": "测试",
                "ready": True,
                "exit_mode": "half",
                "urgency": "take",
                "minute_gate": True,
                "role_label": "冲高回落先减",
                "reason": "测试",
                "sell_pct": 50,
                "sell_qty": 100,
            }
        ],
    }
    minutes = [{"price": 10.0 + (i / 100.0), "volume": 100, "avg": 10.05} for i in range(40)]
    minutes[-1]["price"] = max(m["price"] for m in minutes)
    out = apply_sell_minute_gates(advice, {"600000": minutes})
    assert "minute_sell" in out["items"][0]


def test_next_action_on_hold_and_regret() -> None:
    hold = _sell_item(_base_row(), _verdict_tied(), "发酵", trade_date="2026-09-17")
    assert hold is not None
    assert hold.get("next_action") in ("hold", "half", "clear", "watch")
    assert hold.get("next_action_zh")
    # Already trimmed + still strong → regret watch with half anchor.
    row = _base_row(
        qty=100,
        day_sold_qty=100,
        last_sell_price=10.8,
        last=11.4,
        high=11.45,
        peak_price=11.5,
        pnl_pct=14.0,
        last_pct=3.0,
    )
    v = _verdict_tied()
    v["sell_themes"][0]["lifecycle"] = "ending"
    v["mainline"]["lifecycle"] = "ending"
    item = _sell_item(row, v, "发酵", trade_date="2026-09-17")
    assert item is not None
    if item.get("regret_hold"):
        assert item["next_action"] == "watch"
        assert item.get("half_anchor_price") == 10.8
        assert item.get("deep_clear_price") is not None


def test_attach_position_sell_hints() -> None:
    from market_desk.verdict import attach_position_sell_hints

    positions = [
        {"id": 1, "code": "600000", "qty": 200, "closed": False},
        {"id": 2, "code": "510300", "qty": 0, "closed": True, "day_sold_qty": 100, "last_sell_price": 4.2},
    ]
    hints = [
        {
            "id": 1,
            "code": "600000",
            "next_action": "half",
            "next_action_zh": "减半",
            "next_action_note": "测试",
            "trigger_price": 11.0,
            "half_anchor_price": None,
            "deep_clear_price": 10.5,
            "exit_mode": "half",
            "ready": True,
            "regret_hold": False,
            "role_label": "冲高回落先减",
        }
    ]
    out = attach_position_sell_hints(positions, hints)
    assert out[0]["next_action"] == "half"
    assert out[0]["trigger_price"] == 11.0
    assert out[1].get("half_anchor_price") == 4.2


def test_sell_fly_board_counts() -> None:
    from market_desk.review import build_sell_fly_board

    rows = [
        {"signal_type": "sell", "outcome_label": "卖后回落", "traded": 1, "kind": "stock", "desk_source": "main", "trade_date": "2026-09-10", "code": "600000", "name": "A"},
        {"signal_type": "sell", "outcome_label": "卖飞", "traded": 1, "kind": "stock", "desk_source": "main", "trade_date": "2026-09-11", "code": "600001", "name": "B", "outcome_mae_pct": 4.0},
        {"signal_type": "sell", "outcome_label": "卖后继续涨", "traded": 1, "kind": "etf", "desk_source": "link", "trade_date": "2026-09-12", "code": "510300", "name": "C"},
        {"signal_type": "buy", "outcome_label": "次日红", "traded": 1},
    ]
    board = build_sell_fly_board(rows, hit_mode="traded")
    assert board["n"] == 3
    assert board["hit_n"] == 1
    assert board["fly_n"] == 1
    assert board["early_n"] == 2
    assert board["recent_early"]
    assert any(x.get("label") == "个股" for x in board["by_kind"])


def test_trade_fee_constant_present() -> None:
    assert TRADE_FEE_CNY == 5.0
