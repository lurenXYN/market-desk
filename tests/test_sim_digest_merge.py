"""Sim desk digest folds into EOD WeChat; sim: alone is not Server酱-pushed."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH
from market_desk.db.sim_desk import (
    ensure_sim_desk_tables,
    get_sim_account,
    set_sim_account_epoch,
)
from market_desk.notify import filter_serverchan_alerts, is_serverchan_alert
from market_desk.report import build_eod_onepager
from market_desk.sim_desk import format_sim_digest_bullets


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "sim_merge.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def test_sim_key_not_serverchan() -> None:
    assert is_serverchan_alert("sim:digest:2026-10-29") is False
    kept = filter_serverchan_alerts(
        [
            ("sim:digest:2026-10-29", "模拟操盘日终", "x"),
            ("eod:2026-10-29", "收盘一页纸", "x"),
        ]
    )
    assert [k for k, _t, _b in kept] == ["eod:2026-10-29"]


def test_format_sim_digest_empty_unused_book(tmp_path, monkeypatch) -> None:
    _init_db(tmp_path, monkeypatch)
    assert format_sim_digest_bullets(trade_date="2026-10-29") == []


def test_eod_onepager_pins_sim_bullets(tmp_path, monkeypatch) -> None:
    _init_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "market_desk.sim_desk.format_sim_digest_bullets",
        lambda **_kw: [
            "模拟账本 · 平仓2 · 持仓1 · 累计-0.32% · 未过线·权益未转正",
            "模拟卖出 600000 浦发 · 清仓 · 100股 · +1.20%",
        ],
    )
    brief = build_eod_onepager(
        snapshot={
            "trade_date": "2026-10-29",
            "phase": "退潮",
            "position_summary": {"day_pnl": 0, "floating_pnl": 0, "realized_pnl": 0},
            "verdict": {"action": "观望", "mainline": {"name": "半导体", "status": "退潮"}},
        },
        review={"summary": {"today": {"date": "2026-10-29", "buy_n": 0, "sell_n": 0}}},
    )
    assert any(str(b).startswith("模拟账本") for b in brief["bullets"])
    assert any(str(b).startswith("模拟账本") for b in brief["push_bullets"])
    # Pinned after P&L (and optional discipline), before secondary noise.
    push = list(brief["push_bullets"])
    sim_i = next(i for i, b in enumerate(push) if str(b).startswith("模拟账本"))
    assert sim_i <= 2
