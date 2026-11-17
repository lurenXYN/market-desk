"""ready_only backtests must filter on the latched ever_ready, not the volatile flag."""

from __future__ import annotations

import market_desk.backtest as backtest
import market_desk.db as desk_db


def _row(code: str, *, ready: int, ever_ready: bool | None) -> dict:
    payload = {"plan_price": 10.0}
    if ever_ready is not None:
        payload["ever_ready"] = ever_ready
    return {
        "id": int(code[-1]),
        "trade_date": "2026-10-08",
        "signaled_at": "2026-10-08 09:35:00",
        "signal_type": "buy",
        "code": code,
        "name": code,
        "price": 10.0,
        "ready": ready,
        "payload": payload,
    }


def test_ready_only_keeps_cards_that_lit_earlier(monkeypatch) -> None:
    rows = [
        _row("600001", ready=0, ever_ready=True),   # lit earlier, faded by the close
        _row("600002", ready=0, ever_ready=False),  # never lit
        _row("600003", ready=1, ever_ready=None),   # legacy row without the latch
    ]
    monkeypatch.setattr(desk_db, "list_signal_trade_dates", lambda limit=120: ["2026-10-08"])
    monkeypatch.setattr(desk_db, "load_signals_for_date", lambda day: list(rows))

    picked = backtest.collect_backtest_signals(
        date_from="2026-10-08", date_to="2026-10-08", ready_only=True
    )
    assert {r["code"] for r in picked} == {"600001", "600003"}


def test_without_ready_only_every_buy_is_kept(monkeypatch) -> None:
    rows = [_row("600001", ready=0, ever_ready=False), _row("600002", ready=1, ever_ready=True)]
    monkeypatch.setattr(desk_db, "list_signal_trade_dates", lambda limit=120: ["2026-10-08"])
    monkeypatch.setattr(desk_db, "load_signals_for_date", lambda day: list(rows))

    picked = backtest.collect_backtest_signals(
        date_from="2026-10-08", date_to="2026-10-08", ready_only=False
    )
    assert {r["code"] for r in picked} == {"600001", "600002"}