"""Main-board quotes: edge probe on clist failure, clist pause and Tencent fallback."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import market_desk.db as desk_db
import market_desk.eastmoney as em
from market_desk.eastmoney import client as em_client
from market_desk.eastmoney import quotes as em_quotes
import market_desk.quotes_fallback as qf


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    monkeypatch.setattr(em_quotes, "_MAIN_QUOTES_CACHE", None)
    monkeypatch.setattr(em_quotes, "_QUOTE_PROBE", (0.0, ""))
    monkeypatch.setattr(em_client, "_CLIST_BACKOFF_UNTIL", 0.0)
    monkeypatch.setattr(em_quotes, "_QUOTES_CLIST_PAUSE_UNTIL", 0.0)
    monkeypatch.setattr(em_quotes, "_MAIN_QUOTES_SOURCE", "eastmoney")
    monkeypatch.setattr(qf, "_UNIVERSE", None)
    monkeypatch.setattr(qf, "SINA_PAGE_DELAY_S", 0.0)
    monkeypatch.setattr(qf, "TENCENT_CHUNK_DELAY_S", 0.0)
    monkeypatch.setattr(qf, "UNIVERSE_MIN_CODES", 3)
    monkeypatch.setattr(qf, "UNIVERSE_FULL_CODES", 4)
    monkeypatch.setattr(em_quotes, "MAIN_QUOTES_BREADTH_MIN", 3)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _tencent_line(code: str, pct: float) -> str:
    sym = ("sh" if code.startswith("6") else "sz") + code
    f = ["1", f"N{code}", code, "10.50", "10.00", "10.10"] + ["0"] * 26
    f += [f"{pct:.2f}", "10.80", "9.90", "0", "0", "12345", "1.2"] + ["0"] * 6 + ["321.5"]
    return f'v_{sym}="' + "~".join(f) + '";'


UNIVERSE = sorted(["600000", "600001", "000001", "000002"])


def _handler(*, clist, sina=True):
    """Route clist / Sina / Tencent requests to canned responses; count by kind."""
    hits = {"clist": 0, "sina": 0, "tencent": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "clist/get" in url:
            hits["clist"] += 1
            return clist(request)
        if "sina" in url:
            hits["sina"] += 1
            if not sina:
                return httpx.Response(503)
            node = request.url.params.get("node")
            page = int(request.url.params.get("page"))
            codes = {"sh_a": ["600000", "600001", "688001"], "sz_a": ["000001", "000002", "300001"]}
            rows = [{"code": c} for c in codes[node]] if page == 1 else []
            return httpx.Response(200, json=rows)
        if "qt.gtimg.cn" in url:
            hits["tencent"] += 1
            syms = url.split("q=", 1)[1].split(",")
            body = "".join(_tencent_line(s[2:], 1.5) for s in syms)
            return httpx.Response(200, content=body.encode("gbk"))
        return httpx.Response(404)

    return handle, hits


def _fetch(handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await em.fetch_main_quotes(c)

    return asyncio.run(go())


def test_blocked_clist_falls_back_to_tencent_and_pauses_clist() -> None:
    handler, hits = _handler(clist=lambda r: httpx.Response(200, json={"rc": 0, "data": None}))
    rows = _fetch(handler)
    assert sorted(r["code"] for r in rows) == UNIVERSE
    row = next(r for r in rows if r["code"] == "600000")
    assert row["pct"] == 1.5 and row["amount"] == 12345 * 1e4 and row["mv_yi"] == 321.5
    assert round(row["open_pct"], 2) == 1.0
    assert em_quotes._MAIN_QUOTES_SOURCE == "tencent" and em_quotes._QUOTES_CLIST_PAUSE_UNTIL > 0
    assert "push2delay=200 rc=0 data=null" in em_quotes._QUOTE_PROBE[1]
    assert em.clist_runtime_status()["quotes_source"] == "tencent"
    assert (desk_db.load_setting(qf.UNIVERSE_SETTING_KEY) or {}).get("codes") == UNIVERSE

    clist_before = hits["clist"]
    em_quotes._MAIN_QUOTES_CACHE = None
    qf._UNIVERSE = None
    _fetch(handler)
    assert hits["clist"] == clist_before, "clist must stay paused"
    assert hits["sina"] == 2, "universe reused from the stored copy"


def test_stored_universe_used_when_sina_fails() -> None:
    desk_db.save_setting(qf.UNIVERSE_SETTING_KEY, {"day": "2020-01-01", "codes": UNIVERSE})
    handler, _ = _handler(clist=lambda r: httpx.Response(403, text="Forbidden"), sina=False)
    rows = _fetch(handler)
    assert sorted(r["code"] for r in rows) == UNIVERSE


def test_total_failure_reports_probe_and_fallback_reason() -> None:
    handler, _ = _handler(clist=lambda r: httpx.Response(403, text="<html>Forbidden</html>"), sina=False)
    with pytest.raises(RuntimeError) as ei:
        _fetch(handler)
    msg = str(ei.value)
    assert msg.startswith("main-board quotes empty [")
    assert "push2=403 <html>Forbidden</html>" in msg
    assert "fallback: fallback universe empty" in msg


def test_partial_list_is_not_remembered_and_longer_list_wins() -> None:
    qf.remember_universe(["600000", "600001", "000001"])
    assert qf._UNIVERSE is None and desk_db.load_setting(qf.UNIVERSE_SETTING_KEY) is None
    qf.remember_universe(UNIVERSE)
    assert qf._UNIVERSE[1] == UNIVERSE
    longer = sorted(UNIVERSE + ["600002"])
    qf.remember_universe(longer)
    assert qf._UNIVERSE[1] == longer
    qf.remember_universe(UNIVERSE)
    assert qf._UNIVERSE[1] == longer
    assert desk_db.load_setting(qf.UNIVERSE_SETTING_KEY)["codes"] == longer


def test_healthy_clist_seeds_universe_and_skips_fallback(monkeypatch) -> None:
    def clist(request: httpx.Request) -> httpx.Response:
        fs = request.url.params.get("fs") or ""
        codes = ["600000", "600001"] if fs.startswith("m:1") else ["000001", "000002"]
        diff = [{"f12": c, "f14": c, "f2": 10.0, "f3": 1.0, "f17": 10.0, "f18": 9.9} for c in codes]
        return httpx.Response(200, json={"rc": 0, "data": {"total": len(diff), "diff": diff}})

    monkeypatch.setattr(em_quotes, "_QUOTES_CLIST_MIN_ROWS", 3)
    em_quotes._MAIN_QUOTES_SOURCE = "tencent"
    handler, hits = _handler(clist=clist)
    rows = _fetch(handler)
    assert sorted(r["code"] for r in rows) == UNIVERSE
    assert hits["tencent"] == 0 and hits["sina"] == 0
    assert em_quotes._MAIN_QUOTES_SOURCE == "eastmoney" and em_quotes._QUOTES_CLIST_PAUSE_UNTIL == 0.0
    assert (desk_db.load_setting(qf.UNIVERSE_SETTING_KEY) or {}).get("codes") == UNIVERSE
