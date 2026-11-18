"""Prior-session turnover curve parsing and merge."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from market_desk import volume_baseline as vb


def _payload(date: str, base: float, n: int = 40) -> dict:
    # 09:30..10:09 stamps with cumulative amounts rising by one unit per minute.
    stamps = []
    for i in range(n):
        minute = 9 * 60 + 30 + i
        stamps.append(f"{minute // 60:02d}{minute % 60:02d} 1.0 100 {base + i}")
    return {"data": {"__SYM__": {"data": [{"date": date, "data": stamps}]}}}


def _handler(payloads: dict[str, dict | int]):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for sym, body in payloads.items():
            if f"code={sym}" in url:
                if isinstance(body, int):
                    return httpx.Response(body, json={})
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})

    return handle


def _run(handler, coro_fn):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await coro_fn(c)

    return asyncio.run(go())


def test_parse_days_reads_cumulative_amounts():
    body = _payload("20261117", 1000.0)
    payload = {"data": {"sh000001": body["data"]["__SYM__"]}}
    days = vb._parse_days(payload, "sh000001")
    assert list(days) == ["20261117"]
    assert days["20261117"]["09:30"] == 1000.0
    assert days["20261117"]["10:09"] == 1039.0


def test_merge_curve_sums_shared_stamps_only():
    sh = {"09:30": 10.0, "09:31": 20.0}
    sz = {"09:30": 5.0, "09:32": 9.0}
    assert vb.merge_curve(sh, sz) == [{"time": "09:30", "cum": 15.0}]


def test_prior_session_curve_picks_latest_completed_day():
    vb._cache.clear()
    sh = _payload("20261117", 1000.0)
    sz = _payload("20261117", 500.0)
    handler = _handler({
        "sh000001": {"data": {"sh000001": sh["data"]["__SYM__"]}},
        "sz399001": {"data": {"sz399001": sz["data"]["__SYM__"]}},
    })
    curve = _run(handler, lambda c: vb.prior_session_curve(c, "2026-11-18"))
    assert curve and len(curve) == 40
    assert curve[0]["cum"] == 1500.0 and curve[-1]["cum"] == 1500.0 + 2 * 39
    assert vb.curve_total(curve) == pytest.approx(curve[-1]["cum"])


def test_prior_session_curve_ignores_today_and_future():
    vb._cache.clear()
    sh = _payload("20261118", 100.0)
    sz = _payload("20261118", 100.0)
    handler = _handler({
        "sh000001": {"data": {"sh000001": sh["data"]["__SYM__"]}},
        "sz399001": {"data": {"sz399001": sz["data"]["__SYM__"]}},
    })
    assert _run(handler, lambda c: vb.prior_session_curve(c, "2026-11-18")) is None


def test_prior_session_curve_returns_none_when_a_leg_fails():
    vb._cache.clear()
    sh = _payload("20261117", 1000.0)
    handler = _handler({
        "sh000001": {"data": {"sh000001": sh["data"]["__SYM__"]}},
        "sz399001": 500,
    })
    assert _run(handler, lambda c: vb.prior_session_curve(c, "2026-11-18")) is None