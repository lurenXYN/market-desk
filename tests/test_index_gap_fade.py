"""高开低走 statistical definition."""

from __future__ import annotations

import asyncio

from market_desk.engine.eod import EodMixin
from market_desk.index_gap_fade import index_gap_fade_line, index_gap_fade_stats


def _bar(day, o, low, close):
    return {"date": day, "open": o, "low": low, "close": close}


BARS = [
    _bar("2026-11-02", 100.0, 99.0, 100.0),
    _bar("2026-11-03", 102.0, 100.5, 101.0),   # gap +2.0%, low < open -> broke
    _bar("2026-11-04", 101.5, 101.6, 102.0),   # gap +0.5% -> not counted
    _bar("2026-11-05", 104.0, 104.0, 102.5),   # gap +2.97%, low == open -> miss
]


def test_counts_gap_days_and_breaks():
    out = index_gap_fade_stats(BARS, threshold=1.0, label="创业板指")
    assert out["ok"] and out["days"] == 4
    assert out["gap_days"] == 2 and out["broke_days"] == 1
    assert out["miss_days"] == 1 and out["zero_miss"] is False
    assert out["hit_rate"] == 50.0
    assert out["misses"][0]["date"] == "2026-11-05"
    assert out["last_gap"]["date"] == "2026-11-03"


def test_zero_miss_only_when_every_gap_breaks():
    bars = BARS[:3]
    out = index_gap_fade_stats(bars, threshold=1.0)
    assert out["gap_days"] == 1 and out["broke_days"] == 1 and out["zero_miss"] is True


def test_threshold_is_strict_and_configurable():
    out = index_gap_fade_stats(BARS, threshold=3.0)
    assert out["gap_days"] == 0 and out["zero_miss"] is False and out["hit_rate"] is None
    out2 = index_gap_fade_stats(BARS, threshold=0.4)
    assert out2["gap_days"] == 3  # 0.495%, 2.0% and 2.97% all strictly above 0.4


def test_lookback_keeps_most_recent_sessions():
    out = index_gap_fade_stats(BARS, threshold=1.0, lookback=2)
    assert out["days"] == 2 and out["gap_days"] == 1
    assert out["start"] == "2026-11-04"


def test_insufficient_bars_is_not_ok():
    out = index_gap_fade_stats([_bar("2026-11-04", 1.0, 1.0, 1.0)])
    assert out["ok"] is False and out.get("detail")


def test_summary_line_shows_real_rate_instead_of_zero_miss():
    out = index_gap_fade_stats(BARS, threshold=1.0, label="创业板指")
    line = index_gap_fade_line(out)
    assert "高开>1.0% 2 次" in line and "跌破开盘 1 次" in line and "50.0%" in line
    assert index_gap_fade_line(None) == ""


def test_engine_caches_the_statistic_per_session(monkeypatch):
    """The engine fetches once per trade day and keeps the parsed result."""
    calls = []

    async def fake_fetch(client, symbol="sz399006", limit=320):
        calls.append(symbol)
        return [
            {"date": "2026-11-03", "open": 100.0, "low": 99.0, "close": 100.0},
            {"date": "2026-11-04", "open": 102.5, "low": 101.0, "close": 102.0},
        ]

    import market_desk.tencent as tx

    monkeypatch.setattr(tx, "fetch_index_daily_bars", fake_fetch)

    class _Host(EodMixin):
        pass

    host = _Host()
    asyncio.run(host._ensure_gap_fade(None, "2026-11-04"))
    assert host._gap_fade["ok"] and host._gap_fade["gap_days"] == 1
    assert host._gap_fade["broke_days"] == 1 and host._gap_fade_day == "2026-11-04"
    asyncio.run(host._ensure_gap_fade(None, "2026-11-04"))
    assert len(calls) == 1, "same trade day must not refetch"
