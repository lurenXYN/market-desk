"""Tonghuashun fallback parsing and its place in the minute-source chain."""

from __future__ import annotations

import asyncio

import market_desk.eastmoney.minute as minute_mod
from market_desk.ths import _ths_symbol, _unwrap, fetch_minute_trends, fetch_today_snapshot


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _Client:
    def __init__(self, text):
        self._text = text
        self.urls = []

    async def get(self, url, **kwargs):
        self.urls.append(url)
        return _Resp(self._text)


MINUTE_JS = (
    'quotebridge_v6_time_hs_600000_last({"hs_600000":{"name":"浦发银行","pre":"10.78",'
    '"date":"20261117","data":"0930,10.78,10134817,10.780,940150;'
    '0931,10.75,47378316,10.792,4389143;0932,10.82,11455827,10.790,1062700"}})'
)
TODAY_JS = (
    'quotebridge_v6_line_hs_600000_01_today({"hs_600000":{"1":"20261117","7":"10.78",'
    '"8":"10.88","9":"10.73","11":"10.86","13":92524741,"19":"1001244170.00",'
    '"name":"浦发银行"}})'
)


def test_symbol_and_unwrap():
    assert _ths_symbol("600000") == "hs_600000"
    assert _ths_symbol("sh600000") == "hs_600000"
    payload = _unwrap(MINUTE_JS)
    assert "hs_600000" in payload


def test_minute_points_shape():
    out = asyncio.run(fetch_minute_trends(_Client(MINUTE_JS), "600000"))
    assert [p["time"] for p in out] == ["09:30", "09:31", "09:32"]
    assert out[1]["price"] == 10.75
    assert out[0]["avg"] == 10.78


def test_today_snapshot_fields():
    snap = asyncio.run(fetch_today_snapshot(_Client(TODAY_JS), "600000"))
    assert snap is not None
    assert snap["last"] == 10.86
    assert snap["prev_close"] == 10.78
    assert snap["high"] == 10.88 and snap["low"] == 10.73
    assert snap["pct"] == 0.74  # 10.86 / 10.78 - 1
    assert snap["source"] == "ths"


def test_garbage_payload_returns_empty():
    assert asyncio.run(fetch_minute_trends(_Client("not json"), "600000")) == []
    assert asyncio.run(fetch_today_snapshot(_Client("not json"), "600000")) is None


def test_minute_chain_falls_back_to_ths(monkeypatch):
    async def _none_em(client, c):
        return []

    async def _none_tx(client, c):
        return []

    async def _ths(client, c):
        return [{"time": "09:30", "price": 10.86}]

    monkeypatch.setattr(minute_mod, "_minute_trends_eastmoney", _none_em)
    monkeypatch.setattr(minute_mod.tencent_client, "fetch_minute_trends", _none_tx)
    monkeypatch.setattr(minute_mod, "_minute_em_paused", lambda: False)
    monkeypatch.setattr("market_desk.ths.fetch_minute_trends", _ths)
    out = asyncio.run(minute_mod._fetch_minute_trends_unlocked(_Client(""), "600000"))
    assert out and out[0]["price"] == 10.86
    assert minute_mod._MINUTE_SOURCE == "ths"
