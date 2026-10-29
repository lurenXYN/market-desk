"""Fill-rule parity helpers and daily bar cache."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.backtest import DEFAULT_SLIP_PCT, simulate_sell_fill
from market_desk.db.bars_cache import (
    bars_cache_stats,
    ensure_bars_cache_tables,
    find_bar_gaps,
    load_daily_bars,
    upsert_daily_bars,
)
from market_desk.fill_rules import flat_fee_as_pct, fill_rules_parity, roundtrip_flat_fee_pct


def _init(tmp_path, monkeypatch):
    db = tmp_path / "bars.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()


def test_fill_rules_parity_shape():
    rep = fill_rules_parity()
    assert rep["ok"] is True
    assert rep["fees"]["backtest_default_slip_pct"] == DEFAULT_SLIP_PCT
    assert abs(flat_fee_as_pct(px=10.0, qty=100) - 0.5) < 1e-9
    assert abs(roundtrip_flat_fee_pct(entry=10.0, exit_px=10.0, qty=100) - 1.0) < 1e-9


def test_simulate_sell_respects_t1_open_date():
    bars = {
        "2026-10-28": {"open": 10, "high": 11, "low": 9, "close": 10.5},
        "2026-10-29": {"open": 10.5, "high": 10.6, "low": 9.5, "close": 9.8},
    }
    # Signal same day as open — must roll to next session when open_date set.
    hit = simulate_sell_fill(
        trade_date="2026-10-28",
        bars_by_day=bars,
        sell=None,
        stop=9.9,
        open_date="2026-10-28",
    )
    assert hit is not None and hit["fill_date"] == "2026-10-29"


def test_bars_cache_upsert_and_gaps(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    n = upsert_daily_bars(
        "600000",
        [
            {
                "date": "2026-10-27",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1e6,
            },
            {
                "date": "2026-10-29",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.2,
                "volume": 1e6,
            },
        ],
        source="unit",
    )
    assert n == 2
    rows = load_daily_bars("600000")
    assert len(rows) == 2
    gaps = find_bar_gaps("600000", ["2026-10-27", "2026-10-28", "2026-10-29"])
    assert gaps["missing"] == ["2026-10-28"]
    assert bars_cache_stats()["codes"] == 1
