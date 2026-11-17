"""Daily-trend gating must ignore the in-progress session bar."""

from __future__ import annotations

import asyncio

from market_desk.engine.core import DeskEngine


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _engine_with_stub(monkeypatch, dates, closes):
    eng = DeskEngine()

    async def fake_fetch(client, codes, limit=60):
        return {c: (list(dates), list(closes), {"high": list(closes), "low": list(closes)}) for c in codes}

    monkeypatch.setattr("market_desk.engine.recommend.fetch_daily_klines_many", fake_fetch)
    return eng


def test_today_bar_is_excluded_from_trend_cache(monkeypatch):
    # 21 个已完成交易日稳步上行 + 今日盘中暴跌一根
    dates = [f"2026-10-{d:02d}" for d in range(1, 22)] + ["2026-11-17"]
    completed = [round(10.0 + 0.1 * i, 2) for i in range(21)]
    closes = completed + [8.0]
    eng = _engine_with_stub(monkeypatch, dates, closes)

    got, ok = asyncio.run(eng._resolve_daily_closes(None, ["600001"], "2026-11-17"))

    assert ok["600001"] is True
    # 今日 8.0 不参与趋势与 ATR：缓存里只有 21 个已完成交易日
    assert got["600001"] == completed
    assert len(got["600001"]) == 21
    trend = eng._cached_trends_for(["600001"])["600001"]
    assert trend.get("up") is True
