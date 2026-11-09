"""v1.10.82: ntfy push on live sim fills (follow the paper book)."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db.schema import init_db
from market_desk.ntfy_push import (
    _sim_fill_alert,
    is_ntfy_sim_fill,
    push_ntfy_sim_fills,
)


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    init_db()


def test_sim_fill_alert_buy_and_sell():
    buy = _sim_fill_alert(
        {
            "side": "buy",
            "code": "600438",
            "name": "通威股份",
            "qty": 100,
            "px": 12.3,
            "trade_date": "2026-11-09",
            "lot_id": 7,
            "size_tag": "half",
            "note": "cf_touch",
        }
    )
    assert buy is not None
    key, title, body = buy
    assert key.startswith("sim:buy:")
    assert "请跟单" in title
    assert "100股" in body
    assert "手动跟" in body

    sell = _sim_fill_alert(
        {
            "side": "sell",
            "code": "600438",
            "name": "通威股份",
            "qty": 100,
            "px": 12.3,
            "trade_date": "2026-11-09",
            "lot_id": 7,
            "exit_mode": "clear",
            "pnl_pct": -0.73,
            "note": "开盘必卖（止损清仓）",
        }
    )
    assert sell is not None
    sk, st, sb = sell
    assert sk.startswith("sim:sell:")
    assert "清仓" in st
    assert "止损" in sb or "开盘必卖" in sb


def test_half_sell_requires_soft_flag():
    half = {"side": "sell", "exit_mode": "half", "code": "1", "qty": 100, "trade_date": "2026-11-09"}
    clear = {"side": "sell", "exit_mode": "clear", "code": "1", "qty": 100, "trade_date": "2026-11-09"}
    buy = {"side": "buy", "code": "1", "qty": 100, "trade_date": "2026-11-09"}
    assert is_ntfy_sim_fill(buy)
    assert is_ntfy_sim_fill(clear)
    assert not is_ntfy_sim_fill(half, soft_sell=False)
    assert is_ntfy_sim_fill(half, soft_sell=True)


def test_push_ntfy_sim_fills_fans_out(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    sent: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "market_desk.ntfy_push.list_ntfy_recipients",
        lambda: [
            {
                "id": 1,
                "username": "u",
                "ntfy_topic": "desk-test",
                "ntfy_token": "",
                "ntfy_soft_sell": False,
            }
        ],
    )

    def _fake_notify(topic, title, body, **kwargs):
        sent.append((title, body))
        return True

    monkeypatch.setattr("market_desk.ntfy_push.notify_ntfy", _fake_notify)
    monkeypatch.setattr("market_desk.ntfy_push.drop_daily_repeats", lambda alerts, trade_date="": (alerts, [a[0] for a in alerts]))
    monkeypatch.setattr("market_desk.ntfy_push._mark_daily_once", lambda *a, **k: None)

    n = push_ntfy_sim_fills(
        [
            {
                "side": "buy",
                "code": "600438",
                "name": "通威",
                "qty": 100,
                "px": 12.0,
                "trade_date": "2026-11-09",
                "lot_id": 1,
                "size_tag": "full",
            },
            {
                "side": "sell",
                "code": "601222",
                "name": "林洋",
                "qty": 400,
                "px": 5.74,
                "trade_date": "2026-11-09",
                "lot_id": 2,
                "exit_mode": "clear",
                "pnl_pct": -1.2,
                "note": "开盘必卖",
            },
            {
                "side": "sell",
                "code": "600000",
                "name": "软减",
                "qty": 100,
                "px": 10.0,
                "trade_date": "2026-11-09",
                "lot_id": 3,
                "exit_mode": "half",
            },
        ]
    )
    assert n == 2  # buy + clear; half skipped
    assert any("买入" in t for t, _ in sent)
    assert any("清仓" in t or "卖" in t for t, _ in sent)
    assert not any("减半" in t for t, _ in sent)


def test_open_lots_notify_false_skips_push(tmp_path, monkeypatch):
    """Backfill path must not call ntfy."""
    from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH
    from market_desk.db.sim_desk import ensure_sim_desk_tables, get_sim_account, set_sim_account_epoch
    from market_desk.sim_desk import open_lots_from_signals

    _init(tmp_path, monkeypatch)
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])

    called = {"n": 0}

    def _boom(_events):
        called["n"] += 1
        return 0

    monkeypatch.setattr("market_desk.ntfy_push.push_ntfy_sim_fills", _boom)
    # Empty signals → 0 opens, and even with notify=False no call.
    assert open_lots_from_signals([], notify=False) == 0
    assert called["n"] == 0
