"""Daily discipline scorecard (pure scoring, history store, EOD line)."""

from __future__ import annotations

from datetime import datetime

import pytest

from market_desk.report import build_eod_onepager
from market_desk.review.discipline import build_discipline_card, is_final_day, record_discipline_history

DAY = "2026-10-09"
UID = 7


def _buy(code: str, fill: float, *, lit: bool = True, phase: str = "发酵", sid: int = 1) -> dict:
    return {
        "id": sid, "trade_date": DAY, "code": code, "name": code, "signal_type": "buy",
        "traded": 1, "fill_price": fill, "price": 10.0, "phase": phase,
        "payload": {"plan_price": 10.0, "chase_price": 10.4, "ever_ready": lit},
    }


def _stop(code: str, *, mode: str = "clear", owner: int = UID, qty: int = 1000) -> dict:
    return {
        "trade_date": DAY, "code": code, "name": code, "signal_type": "sell", "owner_user_id": owner,
        "price": 9.5, "live_last": 9.3, "action": "止损清仓",
        "payload": {"urgency": "stop", "exit_mode": mode, "qty": qty},
    }


def _card() -> dict:
    rows = [
        _buy("600001", 10.1, sid=1),
        _buy("600002", 10.2, lit=False, phase="恐慌", sid=2),
        _buy("600004", 10.5, sid=4),
        _stop("600011"),
        _stop("600012", mode="half"),
        _stop("600099", owner=8),
        _stop("600013"),
    ]
    diary = [
        {"id": 5, "trade_date": DAY, "side": "buy", "code": "600003", "name": "c", "qty": 100, "price": 10.0,
         "advice": {"action": "观望", "phase": "发酵"}},
        {"id": 6, "trade_date": DAY, "side": "clear", "code": "600011", "qty": 1000, "price": 9.4, "advice": {}},
    ]
    positions = [{"code": "600013", "qty": 600, "day_sold_qty": 400, "last_sell_date": DAY}]
    return build_discipline_card(rows, diary, day=DAY, user_id=UID, positions=positions)


def test_discipline_scores_buys_stops_and_rhythm() -> None:
    card = _card()
    buys = {i["code"]: i for i in card["items"] if i["kind"] == "buy"}
    assert buys["600001"]["credit"] == 1.0 and buys["600001"]["flags"] == []
    assert buys["600002"]["credit"] == 0.5 and buys["600002"]["flags"] == ["未亮灯就买", "恐慌相位开新仓"]
    assert buys["600003"]["label"] == "计划外买入" and buys["600003"]["credit"] == 0.5
    assert buys["600004"]["credit"] == 0.0 and buys["600004"]["label"] == "越过不追价"
    stops = {i["code"]: i for i in card["items"] if i["kind"] == "stop"}
    assert set(stops) == {"600011", "600012", "600013"}
    assert stops["600011"]["label"] == "已执行"
    assert stops["600012"]["label"] == "扛单" and stops["600012"]["need"] == 500
    assert stops["600012"]["vs_signal"] == pytest.approx(-2.11, abs=0.01)
    assert stops["600013"]["label"] == "部分执行" and stops["600013"]["sold"] == 400
    s = card["sections"]
    assert s["buy"]["pts"] == 20.0 and s["stop"]["pts"] == 20.0 and s["rhythm"]["pts"] == 12.5
    assert card["score"] == 52.5 and card["grade"] == "失守" and card["tone"] == "bad"
    assert card["line"].startswith("纪律分 52.5（失守）") and "扛单 1" in card["line"]


def test_discipline_skips_unlit_flag_before_tracking() -> None:
    old = _buy("600001", 10.1)
    old.update({"trade_date": "2026-09-08", "ready": 0})
    old["payload"]["ever_ready"] = False
    card = build_discipline_card([old], [], day="2026-09-08", user_id=UID)
    assert card["items"][0]["flags"] == [] and card["score"] == 100.0


def test_discipline_empty_day_and_anonymous() -> None:
    card = build_discipline_card([], [], day=DAY, user_id=UID)
    assert card["ok"] and card["score"] is None and "不评分" in card["line"]
    assert build_discipline_card([], [], day=DAY, user_id=None)["ok"] is False
    only_stop = build_discipline_card([_stop("600011")], [], day=DAY, user_id=UID)
    assert list(only_stop["sections"]) == ["stop"] and only_stop["score"] == 0.0


def test_discipline_history_saves_final_days_only(monkeypatch) -> None:
    import market_desk.db as desk_db

    store: dict = {}
    monkeypatch.setattr(desk_db, "load_setting", lambda k: store.get(k))
    monkeypatch.setattr(desk_db, "save_setting", lambda k, v: store.__setitem__(k, v))
    card = {"day": DAY, "score": 80.0, "grade": "良"}
    assert record_discipline_history(UID, card, final=False) == []
    hist = record_discipline_history(UID, card, final=True)
    assert hist == [{"day": DAY, "score": 80.0, "grade": "良"}]
    assert store[f"discipline_hist:{UID}"] == {DAY: {"score": 80.0, "grade": "良"}}
    assert is_final_day("2026-10-08", datetime(2026, 10, 9, 10, 0))
    assert not is_final_day(DAY, datetime(2026, 10, 9, 14, 59))
    assert is_final_day(DAY, datetime(2026, 10, 9, 15, 1))


def test_eod_onepager_puts_discipline_line_after_pnl() -> None:
    card = _card()
    brief = build_eod_onepager(snapshot={}, review={"summary": {"discipline": card}}, diary=[])
    assert brief["bullets"][1] == card["line"]
    assert brief["push_bullets"][1] == card["line"]
    none = build_eod_onepager(snapshot={}, review={"summary": {}}, diary=[])
    assert not any(str(b).startswith("纪律分") for b in none["bullets"])
