"""PTrade bridge stage-1: token, normalize, intents (Send stays off)."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db.schema import init_db
from market_desk.exec.ptrade_bridge import (
    build_bridge_ingest_payload,
    build_exec_intents,
    get_bridge_token,
    intents_etag,
    normalize_ptrade_positions,
    set_bridge_token,
    token_configured,
    verify_bridge_token,
)


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "ptrade_bridge.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    init_db()


def test_token_roundtrip(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    assert token_configured() is False
    assert verify_bridge_token("x") is False
    out = set_bridge_token("desk-test-token-aa11")
    assert out["ok"] is True
    assert out["token"] == "desk-test-token-aa11"
    assert get_bridge_token() == "desk-test-token-aa11"
    assert token_configured() is True
    assert verify_bridge_token("desk-test-token-aa11") is True
    assert verify_bridge_token("wrong") is False


def test_normalize_ptrade_positions_xshg():
    rows = normalize_ptrade_positions(
        [
            {
                "stock_code": "600000.XSHG",
                "stock_name": "浦发银行",
                "current_amount": 200,
                "enable_amount": 200,
                "cost_basis": 10.5,
                "last_sale_price": 10.8,
            },
            {"stock_code": "000001.XSHE", "amount": 0},  # skip empty
        ]
    )
    assert len(rows) == 1
    assert rows[0]["code"] == "600000"
    assert rows[0]["volume"] == 200
    assert rows[0]["price"] == 10.5
    assert rows[0]["last"] == 10.8
    assert rows[0]["gateway"] == "ptrade"


def test_build_ingest_from_raw_ptrade():
    payload = build_bridge_ingest_payload(
        {
            "account": {"total_value": 50000, "cash": 12000, "frozen_cash": 100},
            "positions": [
                {
                    "stock_code": "000001.XSHE",
                    "enable_amount": 100,
                    "current_amount": 100,
                    "cost_basis": 11.0,
                }
            ],
            "trades": [
                {
                    "stock_code": "000001.XSHE",
                    "business_name": "买入",
                    "amount": 100,
                    "business_price": 11.0,
                    "business_id": "t1",
                }
            ],
        }
    )
    assert payload["gateway"] == "ptrade"
    assert payload["account"]["balance"] == 50000
    assert payload["account"]["available"] == 12000
    assert payload["positions"][0]["code"] == "000001"
    assert payload["trades"][0]["direction"] == "LONG"


def test_intents_stage1_never_armed(monkeypatch):
    monkeypatch.setattr(
        "market_desk.exec.ptrade_bridge.portfolio_stats",
        lambda: {
            "gate_ok": True,
            "gate_readonly_ok": True,
            "closed_n": 2,
            "equity": 20300.0,
        },
    )
    snap = {
        "verdict": {
            "recommend": [
                {"code": "600000", "name": "浦发", "ready": True, "plan_price": 10.0},
                {"code": "000002", "name": "万科", "ready": False, "ever_ready": True},
                {"code": "300001", "name": "特锐德", "ready": False},  # skip
            ]
        },
        "sell_advice": {
            "alert_items": [
                {"code": "600519", "name": "茅台", "exit_mode": "stop", "qty": 100}
            ]
        },
    }
    out = build_exec_intents(snap, send_allowed=False)
    assert out["stage"] == "1_readonly"
    assert out["send_allowed"] is False
    assert len(out["buys"]) == 2
    assert all(b["armed"] is False for b in out["buys"])
    assert all(b["note"] == "stage1_watch_only" for b in out["buys"])
    assert out["sells"][0]["armed"] is False
    assert intents_etag(out)

    armed = build_exec_intents(snap, send_allowed=True)
    assert armed["stage"] == "2_click"
    assert armed["send_allowed"] is True
    assert any(b["armed"] for b in armed["buys"] if b["ready"])
