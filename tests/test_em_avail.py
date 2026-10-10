"""East Money availability accounting, push2 pacing, request-volume caches."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest

import market_desk.db as desk_db
from market_desk import board_fallback as bf
from market_desk.eastmoney import avail
from market_desk.eastmoney import boards as em_boards

CLIST = "https://push2.eastmoney.com/api/qt/clist/get?fs=m:90+t:3"


def test_classify_splits_host_and_path_kind() -> None:
    assert avail.classify(CLIST) == "push2/clist"
    assert avail.classify("https://push2delay.eastmoney.com/api/qt/clist/get") == "push2delay/clist"
    assert avail.classify("https://push2his.eastmoney.com/api/qt/stock/kline/get") == "push2his/kline"
    assert avail.classify("https://push2.eastmoney.com/api/qt/ulist.np/get") == "push2/ulist"
    assert avail.classify("https://push2delay.eastmoney.com/api/qt/stock/trends2/get") == "push2delay/trends"
    assert avail.classify("https://push2ex.eastmoney.com/getTopicZTPool") == "push2ex"
    assert avail.classify("https://datacenter-web.eastmoney.com/api/data/v1/get") == "datacenter-web"


def test_block_and_recover_transitions_and_hourly_drain() -> None:
    t = datetime(2026, 10, 2, 0, 36, 0)
    for _ in range(2):
        avail.note("push2/clist", False, "RemoteProtocolError", now=t)
    assert avail.blocked_families() == [], "two failures are not a block yet"
    avail.note("push2/clist", False, "RemoteProtocolError", now=t)
    assert avail.blocked_families() == ["push2/clist"]
    avail.note("push2ex", True, now=t)
    later = datetime(2026, 10, 2, 1, 5, 0)
    avail.note("push2/clist", True, now=later)
    assert avail.blocked_families() == []
    rows, events = avail.drain()
    assert [(e["family"], e["state"]) for e in events] == [("push2/clist", "blocked"), ("push2/clist", "ok")]
    by_key = {(r["hour"], r["family"]): r for r in rows}
    assert by_key[(0, "push2/clist")]["fail"] == 3 and by_key[(0, "push2/clist")]["ok"] == 0
    assert by_key[(1, "push2/clist")]["ok"] == 1 and by_key[(0, "push2ex")]["ok"] == 1
    assert avail.drain() == ([], []), "drain clears pending deltas"


def test_stale_block_is_not_reported_within_window() -> None:
    old = datetime(2020, 1, 1, 23, 0, 0)
    for _ in range(3):
        avail.note("push2/clist", False, "x", now=old)
    assert avail.blocked_families() == ["push2/clist"]
    assert avail.blocked_families(within_sec=900.0) == [], "no traffic since → re-probe allowed"


def test_kline_skips_recently_blocked_hosts_then_reprobes(monkeypatch) -> None:
    from market_desk.eastmoney import bars

    monkeypatch.setattr(avail, "_pace", lambda url: asyncio.sleep(0))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json={"data": {"klines": []}})

    def fetch() -> list:
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                return await bars._fetch_daily_bars_eastmoney(c, "600519", limit=5)

        return asyncio.run(go())

    now = avail._cn_now().replace(tzinfo=None)
    for fam in ("push2his/kline", "push2delay/kline"):
        for _ in range(3):
            avail.note(fam, False, "RemoteProtocolError", now=now)
    assert avail.recently_blocked("https://push2his.eastmoney.com/api/qt/stock/kline/get")
    assert fetch() == [] and seen == [], "known-dead kline hosts are not hit"

    old = datetime(2020, 1, 1, 9, 0, 0)
    for fam in ("push2his/kline", "push2delay/kline"):
        avail.note(fam, False, "RemoteProtocolError", now=old)
    fetch()
    assert seen == ["push2his.eastmoney.com", "push2delay.eastmoney.com"], "stale block re-probes"


def test_push2_skipped_outside_open_hours_only(monkeypatch) -> None:
    monkeypatch.setattr(avail, "EM_PUSH2_OPEN", ((9 * 60, 11 * 60 + 35), (13 * 60, 16 * 60)))
    kline = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    zt = "https://push2ex.eastmoney.com/getTopicZTPool"
    night, lunch, morning = (datetime(2026, 10, 12, h, m) for h, m in ((20, 0), (12, 10), (10, 0)))
    assert avail.em_skip(kline, night) and avail.em_skip(kline, lunch), "push2 refuses off-hours"
    assert not avail.em_skip(kline, morning)
    assert not avail.em_skip(zt, night), "push2ex answers all day"
    monkeypatch.setattr(avail, "EM_PUSH2_OPEN", ())
    assert not avail.em_skip(kline, night), "empty window = always try"


def test_em_snapshot_window_follows_push2_hours(monkeypatch) -> None:
    from market_desk.engine import alias_learn

    monkeypatch.setattr(avail, "EM_PUSH2_OPEN", ((9 * 60, 11 * 60 + 35), (13 * 60, 16 * 60)))
    mon, sat = datetime(2026, 10, 12, 0, 0), datetime(2026, 10, 10, 0, 0)

    def at(day: datetime, h: int, m: int) -> bool:
        return alias_learn._em_snap_window(day.replace(hour=h, minute=m))

    assert at(mon, 10, 0) and at(mon, 15, 30)
    assert not at(mon, 9, 20), "trading day: skip the open rush"
    assert not at(mon, 12, 0) and not at(mon, 20, 0)
    assert at(sat, 9, 10) and not at(sat, 21, 0), "weekends follow the clock window only"
    assert alias_learn._alias_window(mon.replace(hour=20)) and not alias_learn._alias_window(mon.replace(hour=10))


def _run(handler, url: str):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await avail.em_get(c, url)

    return asyncio.run(go())


def test_em_get_records_disconnects_and_http_errors() -> None:
    def drop(_request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    with pytest.raises(httpx.RemoteProtocolError):
        _run(drop, CLIST)
    assert "RemoteProtocolError" in avail.avail_state()["push2/clist"]["err"]
    resp = _run(lambda _r: httpx.Response(502), CLIST)
    assert resp.status_code == 502, "status errors are returned to the caller"
    _run(lambda _r: httpx.Response(200, json={}), "https://push2ex.eastmoney.com/getTopicZTPool")
    st = avail.avail_state()
    assert st["push2/clist"]["fail"] == 2 and st["push2/clist"]["err"] == "HTTP 502"
    assert st["push2ex"]["ok"] == 1


def test_push2_pacing_spaces_request_starts(monkeypatch) -> None:
    monkeypatch.setattr(avail, "EM_PUSH2_MIN_GAP_SEC", 0.15)
    monkeypatch.setattr(avail.time, "monotonic", lambda: 100.0)
    waits: list[float] = []

    async def fake_sleep(sec):
        waits.append(round(sec, 3))

    monkeypatch.setattr(avail.asyncio, "sleep", fake_sleep)

    async def go():
        await avail._pace(CLIST)
        await avail._pace("https://push2his.eastmoney.com/api/qt/stock/kline/get")
        await avail._pace("https://push2ex.eastmoney.com/getTopicZTPool")
        await avail._pace(CLIST)

    asyncio.run(go())
    assert waits == [0.15, 0.3], "push2 family shares one pacer; push2ex is not paced"


def test_em_avail_db_roundtrip_and_hour_folding(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    day = datetime.now().strftime("%Y-%m-%d")
    row = {"day": day, "hour": 23, "family": "push2/clist", "ok": 2, "fail": 1, "empty": 0, "last_err": "E1"}
    desk_db.save_em_avail([row], [{"at": f"{day} 23:45:00", "family": "push2/clist", "state": "blocked"}])
    desk_db.save_em_avail([dict(row, ok=1, fail=3, last_err="")], [])
    data = desk_db.load_em_avail(3)
    assert len(data["hourly"]) == 1
    cell = data["hourly"][0]
    assert (cell["ok"], cell["fail"], cell["last_err"]) == (3, 4, "E1"), "deltas add; blank err keeps last"
    assert data["events"][0]["state"] == "blocked"
    folded = desk_db.summarize_em_avail(data["hourly"])
    assert folded == [{"family": "push2/clist", "hour": 23, "ok": 3, "fail": 4, "empty": 0,
                       "days": 1, "fail_rate": 0.571}]


@pytest.fixture()
def boards_state(monkeypatch):
    monkeypatch.setattr(em_boards, "_BOARDS_SOURCE", "eastmoney")
    monkeypatch.setattr(em_boards, "_BOARDS_CLIST_PAUSE_UNTIL", 0.0)
    monkeypatch.setattr(em_boards, "_INDUSTRY_ROWS_CACHE", None)
    monkeypatch.setattr(em_boards, "_EM_FLOW_CACHE", {})
    monkeypatch.setattr(em_boards, "in_decision_window", lambda now=None: False)
    monkeypatch.setattr(bf, "remember_em_boards", lambda rows: None)


def test_industry_universe_cached_only_while_concepts_answer(monkeypatch, boards_state) -> None:
    calls = {"concept": 0, "industry": 0}
    concept_ok = {"v": True}

    async def fake_json(_client, fs, **_k):
        calls["concept"] += 1
        if not concept_ok["v"]:
            raise httpx.RemoteProtocolError("drop")
        return {"data": {"diff": [{"f12": "BK0900", "f14": "新概念", "f3": 1.0}]}}, "push2"

    async def fake_pages(_client, fs, **_k):
        calls["industry"] += 1
        return [{"f12": "BK0901", "f14": "新行业", "f3": 2.0}]

    monkeypatch.setattr(em_boards, "_get_clist_json", fake_json)
    monkeypatch.setattr(em_boards, "_fetch_clist_pages", fake_pages)
    first = asyncio.run(em_boards._hot_boards_from_clist(None))
    second = asyncio.run(em_boards._hot_boards_from_clist(None))
    assert {r["bk"] for r in first} == {r["bk"] for r in second} == {"BK0900", "BK0901"}
    assert calls == {"concept": 2, "industry": 1}, "industry pages reused inside the TTL"
    concept_ok["v"] = False
    asyncio.run(em_boards._hot_boards_from_clist(None))
    assert calls["industry"] == 2, "concept failure → no cache, re-check the edge"


def test_board_flow_cached_per_period(monkeypatch, boards_state) -> None:
    calls = {"n": 0}

    async def fake_flow(_client, kind, limit, period):
        calls["n"] += 1
        return [{"bk": "BK0477", "name": "酿酒行业", "kind": kind, "period": period}]

    monkeypatch.setattr(em_boards, "_board_fund_flow_clist", fake_flow)
    for _ in range(3):
        asyncio.run(em_boards.fetch_board_fund_flow(None, "industry", 80, "day"))
    asyncio.run(em_boards.fetch_board_fund_flow(None, "industry", 200, "day"))
    asyncio.run(em_boards.fetch_board_fund_flow(None, "industry", 80, "week"))
    assert calls["n"] == 3, "one pull per (kind, limit, period) inside the TTL"


def test_member_ttl_is_staggered_and_stable() -> None:
    keys = [f"BK{n:04d}:s" for n in range(40)]
    ttls = [em_boards._members_ttl(k) for k in keys]
    assert all(90.0 <= t <= 135.0 for t in ttls)
    assert len(set(ttls)) > 10, "boards spread over the jitter window"
    assert ttls == [em_boards._members_ttl(k) for k in keys], "deterministic per board"
