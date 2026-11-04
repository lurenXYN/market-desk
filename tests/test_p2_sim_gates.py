"""P2: absolute gate_ok (trade) vs gate_readonly_ok (relative excess)."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH, TRADE_FEE_CNY
from market_desk.db.sim_desk import (
    apply_sim_sell_fill,
    ensure_sim_desk_tables,
    get_sim_account,
    insert_sim_lot,
    set_sim_account_epoch,
    set_sim_cash,
)
from market_desk.exec.engine import ExecEngine
from market_desk.exec.paper import PaperGateway
from market_desk.exec.risk import require_start_gate
from market_desk.exec.types import Direction, Offset, OrderRequest
from market_desk.sim_desk import portfolio_stats, start_gate_snapshot


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "sim.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def _close_one_lot(*, open_px: float, sell_px: float, qty: int = 100):
    """Open then fully close one lot; leave cash reflecting the round-trip."""
    fee = TRADE_FEE_CNY
    # Pretend buy already deducted cash, matching other sim tests.
    cash_after_buy = SIM_INITIAL_CASH - open_px * qty - fee
    set_sim_cash(cash_after_buy, initial_cash=SIM_INITIAL_CASH)
    lid = insert_sim_lot(
        {
            "code": "600000",
            "name": "浦发",
            "kind": "stock",
            "signal_type": "buy",
            "open_date": "2026-10-28",
            "open_at": "2026-10-28 10:00:00",
            "open_px": open_px,
            "open_weight": 1.0,
            "size_tag": "full",
            "qty": qty,
            "qty_open": qty,
            "buy_fee": fee,
        }
    )
    assert lid
    cash = cash_after_buy + sell_px * qty - fee
    assert apply_sim_sell_fill(
        lid,
        trade_date="2026-10-29",
        filled_at="2026-10-29 14:00:00",
        px=sell_px,
        qty=qty,
        fee=fee,
        cash_after=cash,
        frac=1.0,
        weight=1.0,
        pnl_pct=((sell_px / open_px) - 1.0) * 100.0,
        pnl_cny=(sell_px - open_px) * qty - 2 * fee,
        exit_mode="clear",
        role_label="止盈",
    )
    set_sim_cash(cash, initial_cash=SIM_INITIAL_CASH)
    return cash


def test_relative_green_absolute_red_allows_read_not_trade(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    # Small loss vs initial → gate_ok false; vs falling bench → excess positive.
    _close_one_lot(open_px=10.0, sell_px=9.95, qty=100)
    monkeypatch.setattr(
        "market_desk.dual_score.load_bench_closes",
        lambda limit=240, allow_http=True: [
            ("2026-10-28", 100.0),
            ("2026-10-29", 95.0),
            ("2026-10-30", 94.0),
        ],
    )
    stats = portfolio_stats()
    assert stats["closed_n"] == 1
    assert stats["gate_ok"] is False
    assert stats["cum_pnl_pct"] is not None and stats["cum_pnl_pct"] < 0
    assert stats["excess_cum_pct"] is not None and stats["excess_cum_pct"] > 0
    assert stats["gate_readonly_ok"] is True
    assert "只读" in stats["gate_label"]

    assert require_start_gate(live=True, trade=False).ok is True
    assert require_start_gate(live=True, trade=True).ok is False

    eng = ExecEngine(persist=False)
    got = eng.attach_live(PaperGateway("fake-live"))
    assert got["ok"] is True
    order = eng.send_order(
        OrderRequest(
            code="600000",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=100,
            price=10.0,
        ),
        live=True,
    )
    assert order.status.value in ("REJECTED", "rejected") or "start_gate" in str(
        order.message or ""
    )


def test_absolute_green_opens_both_gates(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _close_one_lot(open_px=10.0, sell_px=11.0, qty=500)
    monkeypatch.setattr(
        "market_desk.dual_score.load_bench_closes",
        lambda limit=240, allow_http=True: [
            ("2026-10-28", 100.0),
            ("2026-10-29", 101.0),
            ("2026-10-30", 102.0),
        ],
    )
    stats = portfolio_stats()
    assert stats["gate_ok"] is True
    assert stats["gate_readonly_ok"] is True
    snap = start_gate_snapshot()
    assert "可开干" in snap["gate_label"]
    assert require_start_gate(live=True, trade=True).ok is True
    assert require_start_gate(live=True, trade=False).ok is True
