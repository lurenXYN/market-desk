"""Minute trends: Tencent fallback when East Money trends2 / klt=1 come back empty."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest

import market_desk.eastmoney as em
import market_desk.tencent as tx

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_DASH = f"{TODAY[:4]}-{TODAY[4:6]}-{TODAY[6:]}"
TX_ROWS = [
    "0930 9.03 2750 2483250.00",
    "0931 9.04 27812 25138268.00",
    "0932 9.05 45461 41116977.00",
    "1500 9.16 1185417 1081363412.24",
    "1530 9.16 1185434 1081378984.24",
]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(em, "_MINUTE_SOURCE", "eastmoney")
    monkeypatch.setattr(em, "_MINUTE_EM_PAUSE_UNTIL", 0.0)
    monkeypatch.setattr(em, "_MINUTE_SEM", None)
    monkeypatch.setattr(tx, "_MINUTE_DAYS_CACHE", {})


def _handler(*, em_trends=None, em_kline=None):
    """Route East Money and Tencent minute endpoints; count hits by kind."""
    hits = {"em": 0, "tx_minute": 0, "tx_days": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "eastmoney.com" in url:
            hits["em"] += 1
            if "trends2" in url:
                body = {"rc": 0, "data": {"trends": em_trends}} if em_trends else {"rc": 0, "data": None}
            else:
                body = {"rc": 0, "data": {"klines": em_kline}} if em_kline else {"rc": 0, "data": None}
            return httpx.Response(200, json=body)
        sym = request.url.params.get("code")
        if "minute/query" in url:
            hits["tx_minute"] += 1
            return httpx.Response(200, json={"data": {sym: {"data": {"date": TODAY, "data": TX_ROWS}}}})
        if "day/query" in url:
            hits["tx_days"] += 1
            days = [
                {"date": TODAY, "data": TX_ROWS},
                {"date": "20260924", "data": ["0930 8.99 2497 2244803.00", "0931 8.99 12450 11191154.56"]},
            ]
            return httpx.Response(200, json={"data": {sym: {"data": days}}})
        return httpx.Response(404)

    return handle, hits


def _run(handler, fn, *args):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await fn(c, *args)

    return asyncio.run(go())


def test_tencent_rows_map_to_trends2_shape() -> None:
    points = tx._minute_points(TX_ROWS, TODAY)
    assert [p["time"] for p in points] == [
        f"{TODAY_DASH} 09:30", f"{TODAY_DASH} 09:31", f"{TODAY_DASH} 09:32", f"{TODAY_DASH} 15:00",
    ], "after-hours 15:30 row dropped"
    first, second = points[0], points[1]
    assert first["volume"] == 2750 and first["amount"] == 2483250.0
    assert second["volume"] == 27812 - 2750
    assert second["amount"] == pytest.approx(25138268.0 - 2483250.0)
    assert second["avg"] == pytest.approx(25138268.0 / (27812 * 100))
    assert second["price"] == 9.04


def test_empty_trends2_falls_back_to_tencent_and_pauses() -> None:
    handler, hits = _handler()
    rows = _run(handler, em.fetch_minute_trends, "600000")
    assert len(rows) == 4 and rows[0]["time"] == f"{TODAY_DASH} 09:30"
    assert em._MINUTE_SOURCE == "tencent" and em._MINUTE_EM_PAUSE_UNTIL > 0
    status = em.clist_runtime_status()
    assert status["minute_source"] == "tencent" and status["minute_pause_sec"] > 0

    em_before = hits["em"]
    many = _run(handler, em.fetch_minute_trends_many, ["600000", "510300"])
    assert set(many) == {"600000", "510300"} and all(len(v) == 4 for v in many.values())
    assert hits["em"] == em_before, "East Money minute stays paused"


def test_healthy_trends2_keeps_eastmoney() -> None:
    em_rows = [f"{TODAY_DASH} 09:31,9.00,9.04,9.05,9.00,100,90400,9.02"]
    handler, hits = _handler(em_trends=em_rows)
    rows = _run(handler, em.fetch_minute_trends, "600000")
    assert rows == [{"time": f"{TODAY_DASH} 09:31", "price": 9.04, "avg": 9.02, "volume": 100.0, "amount": 90400.0}]
    assert hits["tx_minute"] == 0 and em._MINUTE_SOURCE == "eastmoney" and em._MINUTE_EM_PAUSE_UNTIL == 0.0


def test_past_day_bars_use_tencent_five_day_feed() -> None:
    handler, hits = _handler()
    rows = _run(handler, em.fetch_minute_bars_for_day, "600000", "2026-09-24")
    assert [p["time"] for p in rows] == ["2026-09-24 09:30", "2026-09-24 09:31"]
    assert rows[1]["volume"] == 12450 - 2497
    assert em._MINUTE_SOURCE == "tencent" and em._MINUTE_EM_PAUSE_UNTIL > 0
    assert _run(handler, em.fetch_minute_bars_for_day, "600000", "2026-09-01") == []
    assert hits["tx_days"] == 1, "five-day feed cached per code"
