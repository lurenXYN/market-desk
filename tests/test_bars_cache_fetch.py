"""Daily bar fetch must write ``market_bar_daily`` even on early-return paths."""

from __future__ import annotations

import asyncio

import market_desk.db as desk_db
from market_desk.db.bars_cache import ensure_bars_cache_tables, load_daily_bars
from market_desk.eastmoney import bars as em_bars


def _init(tmp_path, monkeypatch):
    db = tmp_path / "bars.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()


def test_fetch_daily_bars_caches_on_tencent_early_return(tmp_path, monkeypatch):
    """≥20 bars from Tencent used to return without upsert; cache must fill."""
    _init(tmp_path, monkeypatch)
    fake = [
        {
            "date": f"2026-09-{i + 1:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.2,
            "volume": 1e6,
            "pct": 0.5,
        }
        for i in range(25)
    ]

    async def _tx(_client, _code, limit: int = 60):
        return fake[:limit]

    async def _em(*_a, **_k):
        raise AssertionError("eastmoney should not be called after Tencent hit")

    monkeypatch.setattr("market_desk.tencent.fetch_daily_bars", _tx)
    monkeypatch.setattr(em_bars, "_fetch_daily_bars_eastmoney", _em)

    class _Client:
        pass

    out = asyncio.run(em_bars.fetch_daily_bars(_Client(), "600000", limit=25))
    assert len(out) == 25
    cached = load_daily_bars("600000", limit=40)
    assert len(cached) == 25
    assert cached[0]["trade_date"] == "2026-09-01"
    assert cached[-1]["close"] == 10.2
