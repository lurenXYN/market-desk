"""Shadow reclaim tracking, buy-family signal matching and chase-cost stats."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.gate_ledger import signal_gate_keys
from market_desk.review import build_chase_cost, chase_pct, signal_plan_price


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _up(at: str, last: float, *, sig_type: str = "buy", **payload) -> None:
    desk_db.upsert_signal({
        "trade_date": "2026-09-29", "signaled_at": at, "signal_type": sig_type,
        "action": "观察回踩", "phase": "发酵", "mainline": "电子", "code": "600001",
        "name": "A", "kind": "stock", "price": 10.0, "last": last, "ready": 0,
        "payload": {"plan_price": 10.0, **payload},
    })


def test_shadow_reclaim_needs_touch_then_above_minute_ma(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _up("2026-09-29 09:40:00", 10.4, minute={"ma": 10.2})
    p = desk_db.load_signals_for_date("2026-09-29")[0]["payload"]
    assert not p.get("touched_plan") and not p.get("shadow_reclaim_at")
    _up("2026-09-29 10:00:00", 9.98, minute={"ma": 10.1})
    p = desk_db.load_signals_for_date("2026-09-29")[0]["payload"]
    assert p["touched_plan"] is True and not p.get("shadow_reclaim_at")
    _up("2026-09-29 10:20:00", 10.12, minute={"ma": 10.08})
    _up("2026-09-29 10:40:00", 10.3, minute={"ma": 10.1})
    p = desk_db.load_signals_for_date("2026-09-29")[0]["payload"]
    assert p["touched_plan"] is True
    assert p["shadow_reclaim_at"] == "2026-09-29 10:20:00"
    assert p["shadow_reclaim_px"] == 10.12
    keys = {k for k, _, _ in signal_gate_keys({"payload": p})}
    assert {"触价过", "影·触价站回均价"} <= keys


def test_find_buy_signal_any_prefers_main_desk(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _up("2026-09-29 09:40:00", 10.4, sig_type="buy_side")
    assert desk_db.find_buy_signal_any("2026-09-29", "600001")["signal_type"] == "buy_side"
    _up("2026-09-29 10:00:00", 10.4, sig_type="buy")
    assert desk_db.find_buy_signal_any("2026-09-29", "600001")["signal_type"] == "buy"
    assert desk_db.find_buy_signal_any("2026-09-29", "600002") is None


def test_chase_cost_summary():
    assert chase_pct(10.0, 10.2) == 2.0
    assert chase_pct(None, 10.2) is None
    assert signal_plan_price({"price": 9.0, "payload": {"plan_price": 10.0}}) == 10.0
    rows = [
        {"trade_date": "2026-09-28", "signal_type": "buy", "traded": 1, "fill_price": 10.2,
         "price": 10.0, "payload": {}, "outcome_day3_pct": 0.0},
        {"trade_date": "2026-09-29", "signal_type": "buy_side", "traded": 1, "fill_price": 9.9,
         "price": 10.0, "payload": {}},
        {"trade_date": "2026-09-29", "signal_type": "buy", "traded": 0, "fill_price": 11.0,
         "price": 10.0, "payload": {}},
        {"trade_date": "2026-09-29", "signal_type": "sell", "traded": 1, "fill_price": 11.0,
         "price": 10.0, "payload": {}},
    ]
    out = build_chase_cost(rows)
    assert out["ok"] and out["n"] == 2 and out["days"] == 2
    assert out["mean"] == 0.5 and out["n_warn"] == 1
    assert out["cost_n"] == 1 and out["cost_d3"] == 2.0
    assert out["items"][0]["trade_date"] == "2026-09-29"
    assert build_chase_cost([])["ok"] is False
