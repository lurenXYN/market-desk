"""ntfy helpers: filter, sim-aligned text, prep brief (no network)."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH
from market_desk.db.schema import init_db
from market_desk.db.sim_desk import (
    ensure_sim_desk_tables,
    get_sim_account,
    insert_sim_lot,
    set_sim_account_epoch,
    set_sim_cash,
)
from market_desk.ntfy_push import (
    build_ntfy_prep_brief,
    filter_ntfy_alerts,
    format_simday_trade_block,
    is_ntfy_alert,
    resolve_ntfy_endpoint,
    sim_buy_plan_text,
    sim_sell_plan_text,
)


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "ntfy.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    init_db()
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def test_resolve_endpoint():
    assert resolve_ntfy_endpoint("desk-abc") == "https://ntfy.sh/desk-abc"
    assert resolve_ntfy_endpoint("https://ntfy.example/t") == "https://ntfy.example/t"
    assert resolve_ntfy_endpoint("") is None


def test_filter_defaults():
    assert is_ntfy_alert("buy:600000")
    assert is_ntfy_alert("sell:stop:600000")
    assert not is_ntfy_alert("sell:take:600000")
    assert is_ntfy_alert("sell:take:600000", soft_sell=True)
    assert is_ntfy_alert("fly:600000", fly=True)
    assert not is_ntfy_alert("fly:600000", fly=False)
    assert not is_ntfy_alert("eod:2026-11-05")
    kept = filter_ntfy_alerts(
        [
            ("buy:1", "t", "b"),
            ("sell:trim:1", "t", "b"),
            ("ops:health:x", "t", "b"),
        ]
    )
    assert [k for k, _, _ in kept] == ["buy:1"]


def test_sim_buy_and_sell_text(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    buy = sim_buy_plan_text(
        code="600000",
        name="浦发",
        price=10.0,
        payload={},
        ready=True,
    )
    assert "对齐模拟" in buy
    assert "股" in buy or "不足一手" in buy

    insert_sim_lot(
        {
            "code": "600000",
            "name": "浦发",
            "qty": 200,
            "open_px": 10.0,
            "open_date": "2026-11-04",
            "open_at": "2026-11-04 10:00:00",
            "size_tag": "full",
            "open_weight": 1.0,
            "note": "unit",
        }
    )
    set_sim_cash(SIM_INITIAL_CASH - 2000)
    sell = sim_sell_plan_text(
        code="600000",
        name="浦发",
        exit_mode="clear",
        sell_price=9.5,
    )
    assert "对齐模拟仓" in sell
    assert "200股" in sell


def test_format_simday_trade_block_buy_fields():
    body = format_simday_trade_block(
        {
            "side": "buy",
            "code": "510880",
            "name": "红利ETF",
            "qty": 700,
            "px": 3.348,
            "filled_at": "2026-11-05 13:38:34",
            "size_tag": "half",
            "notional": 2343.6,
            "signal_type": "buy",
            "note": "主线回踩 · 反事实到价可成交 · 计划3.337 · 成交3.348 · 半仓 · 主线电力设备",
            "lot_payload": {
                "mainline": "电力设备",
                "fill_reason": "cf_touch",
                "plan_price": 3.337,
            },
        },
        index=1,
        total=1,
    )
    assert "买入  红利ETF（510880）" in body
    assert "仓位  半仓 · 700股" in body
    assert "成交  3.348（计划 3.337）" in body
    assert "时间  13:38:34" in body
    assert "主线  电力设备" in body
    assert "金额  约 2344" in body
    # Dense note dump should not reappear as a single blob line.
    assert "计划3.337 · 成交3.348 · 半仓 · 主线电力设备" not in body


def test_prep_brief_has_sections(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    snap = {
        "trade_date": "2026-11-05",
        "verdict": {
            "recommend": {
                "items": [
                    {
                        "code": "600000",
                        "name": "浦发",
                        "ready": False,
                        "buy_price": 10.0,
                    }
                ]
            }
        },
    }
    got = build_ntfy_prep_brief(snap)
    assert got is not None
    title, body = got
    assert "盘前准备" in title
    assert "关注" in body
    assert "模拟持仓" in body
