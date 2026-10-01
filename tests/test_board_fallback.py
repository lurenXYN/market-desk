"""Boards / board money-flow / board members: Sina fallback when East Money clist is blocked."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import market_desk.board_fallback as bf
import market_desk.eastmoney as em
from market_desk.eastmoney import boards as em_boards
from market_desk.eastmoney import client as em_client

SINA_INDUSTRY = {
    "new_blhy": "new_blhy,玻璃行业,19,16.5,-0.85,-4.89,574778376,10297458088,sh600293,4.50,3.95,0.17,三峡新材",
    "new_ljhy": "new_ljhy,酿酒行业,30,78.5,0.3,1.25,1000,2000000000,sh601579,7.31,40.8,2.8,会稽山",
}
SINA_CONCEPT = {
    "gn_hwqc": "gn_hwqc,华为汽车,97,24.1,-0.93,-3.72,1079697164,18956078758,sh600418,9.99,25.2,2.29,江淮汽车",
    "gn_zSTg": "gn_zSTg,准ST股,40,7.7,0.1,0.5,1,1,sh600707,5.0,3.0,0.1,彩虹股份",
}
EM_NAMES = {"华为汽车": ("BK1100", "concept"), "酿酒行业": ("BK0477", "industry")}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(bf, "_EM_BY_NAME", dict(EM_NAMES))
    monkeypatch.setattr(bf, "_EM_NAME_BY_BK", {bk: n for n, (bk, _k) in EM_NAMES.items()})
    monkeypatch.setattr(bf, "_NODE_BY_KEY", {})
    monkeypatch.setattr(bf, "_NODE_BY_NAME", {})
    monkeypatch.setattr(bf, "_BOARDS_CACHE", None)
    monkeypatch.setattr(bf, "_FLOW_CACHE", {})
    monkeypatch.setattr(bf, "_MEMBERS_CACHE", {})
    monkeypatch.setattr(em_boards, "_BOARDS_SOURCE", "eastmoney")
    monkeypatch.setattr(em_boards, "_BOARDS_CLIST_PAUSE_UNTIL", 0.0)
    monkeypatch.setattr(em_boards, "_BOARD_MEMBERS_CACHE", {})
    monkeypatch.setattr(em_boards, "_INDUSTRY_ROWS_CACHE", None)
    monkeypatch.setattr(em_boards, "_EM_FLOW_CACHE", {})
    monkeypatch.setattr(em_boards, "_BOARDS_RESTORE_OK", 0)
    monkeypatch.setattr(em_boards, "_BOARDS_FAIL_STREAK", 0)
    monkeypatch.setattr(em_boards, "_BOARDS_SWITCH_DAY", "")
    monkeypatch.setattr(em_boards, "_BOARDS_SWITCH_LOG", [])
    monkeypatch.setattr(em_boards, "in_decision_window", lambda now=None: False)
    monkeypatch.setattr(em_client, "_CLIST_BACKOFF_UNTIL", 0.0)
    monkeypatch.setattr(em_client, "_CLIST_FAIL_STREAK", 0)
    monkeypatch.setattr(em_client, "_CLIST_HOST_PREF", None)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _js(var: str, body: dict) -> bytes:
    return f"var {var} = {json.dumps(body, ensure_ascii=False)};".encode("gbk")


def _handler(*, clist):
    """Route clist and Sina endpoints to canned responses; count hits by kind."""
    hits = {"clist": 0, "sina_list": 0, "sina_flow": 0, "sina_members": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "clist/get" in url:
            hits["clist"] += 1
            return clist(request)
        if "newSinaHy.php" in url:
            hits["sina_list"] += 1
            return httpx.Response(200, content=_js("S_Finance_bankuai_sinaindustry", SINA_INDUSTRY))
        if "newFLJK.php" in url:
            hits["sina_list"] += 1
            return httpx.Response(200, content=_js("S_Finance_bankuai_class", SINA_CONCEPT))
        if "MoneyFlow.ssl_bkzj_bk" in url:
            hits["sina_flow"] += 1
            node = "new_ljhy" if request.url.params.get("fenlei") == "0" else "gn_hwqc"
            name = "酿酒行业" if node == "new_ljhy" else "华为汽车"
            return httpx.Response(200, json=[{
                "category": node, "name": name, "avg_changeratio": "0.0125",
                "netamount": "730276503.83", "ratioamount": "0.110128",
                "ts_symbol": "sh601579", "ts_name": "会稽山", "ts_changeratio": "0.0731",
            }])
        if "getHQNodeData" in url:
            hits["sina_members"] += 1
            assert request.url.params.get("node") in ("gn_hwqc", "new_blhy")
            asc = request.url.params.get("asc") == "1"
            rows = [
                {"code": "600418", "name": "江淮汽车", "changepercent": 9.03, "turnoverratio": 6.7,
                 "amount": 3688045589, "trade": "25.000", "high": "25.22", "low": "22.99",
                 "mktcap": 5635445.49},
                {"code": "300001", "name": "创业板", "changepercent": 5.0},
                {"code": "600733", "name": "北汽蓝谷", "changepercent": 3.1, "mktcap": 0},
            ]
            return httpx.Response(200, json=list(reversed(rows)) if asc else rows)
        return httpx.Response(404)

    return handle, hits


def _run(handler, fn, *args, **kwargs):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await fn(c, *args, **kwargs)

    return asyncio.run(go())


def _blocked(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"rc": 0, "data": None})


def test_blocked_clist_serves_sina_boards_and_pauses_clist() -> None:
    handler, hits = _handler(clist=_blocked)
    rows = _run(handler, em.fetch_hot_boards)
    by_name = {r["name"]: r for r in rows}
    assert "准ST股" not in by_name, "junk boards stay filtered"
    assert by_name["华为汽车"]["bk"] == "BK1100" and by_name["华为汽车"]["kind"] == "concept"
    assert by_name["酿酒行业"]["bk"] == "BK0477" and by_name["酿酒行业"]["kind"] == "industry"
    glass = by_name["玻璃行业"]
    assert glass["bk"] == "SINA:new_blhy" and glass["kind"] == "industry"
    assert glass["pct"] == -4.89 and glass["amount"] == 10297458088
    assert glass["leader_code"] == "600293" and glass["leader_name"] == "三峡新材"
    assert glass["leader_pct"] == 4.5
    assert em_boards._BOARDS_SOURCE == "sina" and em_boards._BOARDS_CLIST_PAUSE_UNTIL > 0
    status = em.clist_runtime_status()
    assert status["boards_source"] == "sina" and status["boards_pause_sec"] > 0

    clist_before = hits["clist"]
    bf._BOARDS_CACHE = None
    _run(handler, em.fetch_hot_boards)
    assert hits["clist"] == clist_before, "board clist must stay paused"


def test_day_flow_falls_back_to_sina_but_week_does_not() -> None:
    handler, hits = _handler(clist=_blocked)
    rows = _run(handler, em.fetch_board_fund_flow, "industry", 80, "day")
    assert len(rows) == 1
    row = rows[0]
    assert row["bk"] == "BK0477" and row["kind"] == "industry" and row["period"] == "day"
    assert row["pct"] == 1.25 and row["main_pct"] == 11.01
    assert row["main_net"] == pytest.approx(730276503.83)
    assert row["super_net"] is None and row["leader_code"] == "601579" and row["leader_pct"] == 7.31

    assert _run(handler, em.fetch_board_fund_flow, "concept", 80, "week") == []
    assert hits["sina_flow"] == 1


def test_members_fall_back_to_sina_node_by_name() -> None:
    em_boards._BOARDS_CLIST_PAUSE_UNTIL = 1e12
    handler, hits = _handler(clist=_blocked)
    strong = _run(handler, em.fetch_board_members, "BK1100")
    assert hits["clist"] == 0 and hits["sina_list"] == 2, "BK resolved to gn_hwqc via the Sina list"
    assert [m["code"] for m in strong] == ["600418", "600733"], "main board only"
    top = strong[0]
    assert top["pct"] == 9.03 and top["turnover"] == 6.7 and top["price"] == 25.0
    assert top["mv_yi"] == 563.54 and strong[1]["mv_yi"] is None

    weak = _run(handler, em.fetch_board_members, "SINA:new_blhy", weakest=True)
    assert [m["code"] for m in weak] == ["600733", "600418"]
    assert _run(handler, em.fetch_board_members, "BK9999") == [], "no Sina counterpart"


def test_healthy_clist_keeps_eastmoney_and_learns_names() -> None:
    def clist(request: httpx.Request) -> httpx.Response:
        kind_t3 = "t:3" in (request.url.params.get("fs") or "")
        diff = [{"f12": "BK0900" if kind_t3 else "BK0901", "f14": "新概念" if kind_t3 else "新行业",
                 "f3": 2.0, "f20": 1.0}]
        return httpx.Response(200, json={"rc": 0, "data": {"total": 1, "diff": diff}})

    em_boards._BOARDS_SOURCE = "sina"
    handler, hits = _handler(clist=clist)
    first = _run(handler, em.fetch_hot_boards)
    assert hits["sina_list"] == 2 and all(r["bk"] != "BK0900" for r in first), "1st success keeps Sina"
    assert em_boards._BOARDS_SOURCE == "sina" and em_boards._BOARDS_RESTORE_OK == 1
    rows = _run(handler, em.fetch_hot_boards)
    assert {r["bk"] for r in rows} == {"BK0900", "BK0901"}
    assert em_boards._BOARDS_SOURCE == "eastmoney" and em_boards._BOARDS_CLIST_PAUSE_UNTIL == 0.0
    assert em.boards_switch_stats()["switches"] == 1
    assert bf._em_boards()["新概念"] == ("BK0900", "concept")
    assert bf._em_boards()["新行业"] == ("BK0901", "industry")


def test_seed_file_maps_known_boards(monkeypatch) -> None:
    monkeypatch.setattr(bf, "_EM_BY_NAME", None)
    monkeypatch.setattr(bf, "_EM_NAME_BY_BK", {})
    names = bf._em_boards()
    assert len(names) >= 900
    assert names["酿酒概念"] == ("BK0477", "concept")
    assert bf._EM_NAME_BY_BK["BK0477"] == "酿酒概念"
