"""Unit tests for LHB seat risk reasons and edge alerts."""

from __future__ import annotations

from market_desk.lhb import explain_seat_risk_change, seat_risk_reason
from market_desk.notify import lhb_seat_edge_alerts


def test_seat_risk_reason_bad() -> None:
    s = seat_risk_reason("bad", ["smash_sell", "inst_net_sell"])
    assert "偏坏" in s
    assert "砸盘王" in s
    assert "机构净卖" in s


def test_seat_risk_reason_ok_with_buy_hint() -> None:
    s = seat_risk_reason("ok", [], ["机构净买 1.2 亿"])
    assert "偏稳" in s
    assert "净买" in s


def test_explain_worsen() -> None:
    ch = explain_seat_risk_change(
        prev_risk="ok",
        prev_flags=[],
        risk="bad",
        risk_flags=["smash_sell"],
        hints=["砸盘王席卖出 2.0 亿"],
        list_reason="日涨幅偏离值达7%",
    )
    assert ch["direction"] == "worsen"
    assert ch["title"] == "龙虎席位变坏"
    assert "新增" in ch["reason"]
    assert "砸盘王" in ch["reason"]


def test_explain_improve() -> None:
    ch = explain_seat_risk_change(
        prev_risk="bad",
        prev_flags=["smash_sell", "inst_net_sell"],
        risk="ok",
        risk_flags=[],
        hints=["机构净买 0.8 亿"],
    )
    assert ch["direction"] == "improve"
    assert ch["title"] == "龙虎席位变好"
    assert "消退" in ch["reason"]


def test_edge_alerts_worsen_and_improve() -> None:
    items = [
        {
            "code": "600000",
            "name": "浦发银行",
            "on_list": True,
            "trade_date": "2026-09-17",
            "reason": "涨幅偏离",
            "summary": {
                "seat_risk": "bad",
                "risk_flags": ["smash_sell"],
                "hints": ["砸盘王席卖出 1.0 亿"],
                "risk_reason": "偏坏：砸盘王席卖出",
            },
        },
        {
            "code": "000001",
            "name": "平安银行",
            "on_list": True,
            "trade_date": "2026-09-17",
            "summary": {
                "seat_risk": "ok",
                "risk_flags": [],
                "hints": ["机构净买 0.5 亿"],
                "risk_reason": "偏稳：机构净买 0.5 亿",
            },
        },
    ]
    prev = {
        "600000": "2026-09-16|ok|",
        "000001": "2026-09-16|bad|smash_sell,inst_net_sell",
    }
    alerts, next_fp = lhb_seat_edge_alerts(items, prev_fp=prev)
    titles = [a[1] for a in alerts]
    assert "龙虎席位变坏" in titles
    assert "龙虎席位变好" in titles
    bad_body = next(a[2] for a in alerts if "变坏" in a[1])
    good_body = next(a[2] for a in alerts if "变好" in a[1])
    assert "砸盘王" in bad_body or "新增" in bad_body
    assert "消退" in good_body or "偏稳" in good_body
    assert next_fp["600000"].startswith("2026-09-17|bad|")


def test_edge_seed_no_alert() -> None:
    items = [
        {
            "code": "600000",
            "name": "x",
            "on_list": True,
            "trade_date": "2026-09-17",
            "summary": {"seat_risk": "bad", "risk_flags": ["smash_sell"], "hints": []},
        }
    ]
    alerts, next_fp = lhb_seat_edge_alerts(items, prev_fp={})
    assert alerts == []
    assert "600000" in next_fp
