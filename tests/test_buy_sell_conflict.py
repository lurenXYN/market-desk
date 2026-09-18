"""Buy/sell conflict: soft sell → hold; held codes demote buy ready."""

from __future__ import annotations

from market_desk.verdict import (
    _sell_item,
    reconcile_buy_sell_conflict,
)


def _base_row(**kwargs):
    row = {
        "id": 1,
        "code": "002655",
        "name": "共达电声",
        "buy_price": 40.35,
        "qty": 100,
        "last": 41.27,
        "open": 42.3,
        "prev": 40.8,
        "high": 42.5,
        "low": 41.0,
        "last_pct": 1.15,
        "pnl_pct": 2.28,
        "peak_price": 42.5,
        "last_buy_date": "2026-09-10",
        "day_sold_qty": 0,
        "entry_board": "半导体",
    }
    row.update(kwargs)
    return row


def _verdict_buyable():
    return {
        "action": "可买入",
        "mainline": {
            "name": "半导体",
            "status": "确认中",
            "lifecycle": "ongoing",
            "code": "512480",
        },
        "sell_themes": [
            {
                "name": "半导体",
                "status": "确认中",
                "lifecycle": "ongoing",
                "role": "primary",
                "score": 80,
                "carrier_pct": 3.2,
                "carrier_falling": False,
                "main_yi": 1.0,
            }
        ],
        "carrier": {"pct": 3.2, "falling": False, "code": "512480"},
        "recommend": {
            "buy": True,
            "items": [
                {
                    "code": "002655",
                    "name": "共达电声",
                    "kind": "stock",
                    "ready": True,
                    "role_label": "个股可买",
                    "buy_price": 41.0,
                }
            ],
        },
        "segment": {"key": "mid"},
        "elliott": {"ok": False},
    }


def test_gap_fade_skips_when_mainline_strong() -> None:
    # Strong vs carrier + daily up → do not fire gap-fade half.
    row = _base_row(last_pct=4.0, open=42.3, prev=40.8, last=41.0)
    # open_gap ≈ 3.7%, from_open ≈ -3.1%
    v = _verdict_buyable()
    v["sell_themes"][0]["carrier_pct"] = 1.0  # day 4.0 - 1.0 = +3pt → strong
    item = _sell_item(
        row,
        v,
        "发酵",
        trade_date="2026-09-18",
        trend={"up": True, "down": False, "label": "上升", "ma20": 39.0, "quality": "ok"},
    )
    assert item is not None
    if item.get("ready"):
        assert "高开低走" not in (item.get("role_label") or "")


def test_soft_sell_demotes_when_same_code_ready_buy() -> None:
    sell_advice = {
        "sell": True,
        "items": [
            {
                "code": "002655",
                "name": "共达电声",
                "ready": True,
                "exit_mode": "half",
                "urgency": "trim",
                "role_label": "隔夜高开低走先减",
                "reason": "高开低走",
                "on_sell_theme": True,
                "pnl_pct": 2.28,
                "sell_price": 41.27,
                "kind": "stock",
                "hold_peak": 42.5,
            }
        ],
        "all_items": [
            {
                "code": "002655",
                "name": "共达电声",
                "ready": True,
                "exit_mode": "half",
                "urgency": "trim",
                "role_label": "隔夜高开低走先减",
                "reason": "高开低走",
                "on_sell_theme": True,
                "pnl_pct": 2.28,
                "sell_price": 41.27,
                "kind": "stock",
                "hold_peak": 42.5,
            }
        ],
    }
    v, advice = reconcile_buy_sell_conflict(
        _verdict_buyable(),
        sell_advice,
        [{"code": "002655", "qty": 100}],
    )
    item = (advice.get("all_items") or advice.get("items") or [])[0]
    assert item.get("ready") is False
    assert item.get("exit_mode") == "hold"
    assert item.get("buy_conflict_hold") is True
    buy = ((v.get("recommend") or {}).get("items") or [])[0]
    assert buy.get("ready") is False
    assert buy.get("held_block") is True


def test_hard_stop_not_demoted_by_buy() -> None:
    sell_advice = {
        "items": [
            {
                "code": "002655",
                "name": "共达电声",
                "ready": True,
                "exit_mode": "clear",
                "urgency": "stop",
                "role_label": "止损清仓",
                "reason": "止损",
                "on_sell_theme": True,
                "pnl_pct": -5.0,
                "sell_price": 38.0,
                "kind": "stock",
                "hold_peak": 42.5,
            }
        ],
        "all_items": [
            {
                "code": "002655",
                "name": "共达电声",
                "ready": True,
                "exit_mode": "clear",
                "urgency": "stop",
                "role_label": "止损清仓",
                "reason": "止损",
                "on_sell_theme": True,
                "pnl_pct": -5.0,
                "sell_price": 38.0,
                "kind": "stock",
                "hold_peak": 42.5,
            }
        ],
    }
    v, advice = reconcile_buy_sell_conflict(
        _verdict_buyable(),
        sell_advice,
        [{"code": "002655", "qty": 100}],
    )
    item = (advice.get("all_items") or [])[0]
    assert item.get("ready") is True
    assert item.get("exit_mode") == "clear"
    buy = ((v.get("recommend") or {}).get("items") or [])[0]
    assert buy.get("ready") is False
    assert buy.get("sell_conflict_block") is True
