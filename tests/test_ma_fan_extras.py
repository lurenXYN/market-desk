"""MA-fan extras: forward outcomes, latest-day pick score and the outcome summary."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import market_desk.db as desk_db
from market_desk.ma_fan import extras as mx
from market_desk.ma_fan import job as mf_job


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    mf_job._PROGRESS.clear()
    mf_job._PROGRESS["running"] = False
    monkeypatch.setattr(mx, "_LAST_RUN", 0.0)
    monkeypatch.setattr(mx, "_RUNNING", False)
    yield


DATES = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]


def _bars(closes: list[float], dates: list[str] | None = None) -> list[dict]:
    ds = dates or DATES
    out, prev = [], None
    for d, c in zip(ds, closes):
        out.append({"date": d, "open": c, "close": c, "high": c, "low": c, "volume": 1e6, "turnover": 2.0,
                    "pct": None if prev is None else (c / prev - 1) * 100})
        prev = c
    return out


def test_closed_through_respects_close_time() -> None:
    assert mx.closed_through(datetime(2026, 9, 30, 10, 0)) == "2026-09-29"
    assert mx.closed_through(datetime(2026, 9, 30, 15, 10)) == "2026-09-30"


def test_forward_returns_partial_then_final() -> None:
    closes = [(d, c) for d, c in zip(DATES, [10, 11, 9.9, 12, 12.5, 13])]
    got = mx.forward_returns(closes, "2026-09-22", through="2026-09-23")
    assert got == {"d1": -10.0, "final": False}
    got = mx.forward_returns(closes, "2026-09-22", through="2026-09-28")
    assert got["d1"] == -10.0 and got["d3"] == pytest.approx(13.64) and got["final"] is True
    assert mx.forward_returns(closes, "2026-09-26", through="2026-09-28") is None


def test_pick_for_hit_applies_screen_factors() -> None:
    hit = {"code": "300171", "name": "东富龙", "close": 13.0, "pct": 9.8, "zt_ytd": 2, "holder_chg_pct": -6.0}
    res = mx.pick_for_hit(hit, _bars([10, 11, 9.9, 12, 12.5, 13]), None)
    keys = {f["key"]: f["points"] for f in res["factors"]}
    assert keys["mafan"] == 5 and keys["holder"] == 4 and keys["pct"] == -12
    assert res["score"] == 57 and res["base"] == 60.0 and res["as_of"] == "2026-09-28"


def test_refresh_fills_outcomes_and_latest_pick(monkeypatch) -> None:
    desk_db.save_ma_fan_day("2026-09-22", {"items": [{"code": "300171", "close": 11.0}]})
    desk_db.save_ma_fan_day("2026-09-28", {"items": [{"code": "300171", "close": 13.0, "pct": 1.0}]})
    series = _bars([10, 11, 9.9, 12, 12.5, 13])

    async def fake_fetch(codes):
        assert codes == ["300171"]
        return {"300171": series}, [(b["date"], b["close"] * 100) for b in series]

    monkeypatch.setattr(mx, "_fetch_series", fake_fetch)
    out = asyncio.run(mx.refresh_ma_fan_extras(now=datetime(2026, 9, 28, 16, 0)))
    assert out["ok"] and out["fwd"] == 1 and out["pick"] == 1
    old = desk_db.load_ma_fan_day("2026-09-22")
    assert old["items"][0]["fwd"]["final"] is True and old["index_fwd"]["final"] is True
    assert "pick" not in old["items"][0]
    new = desk_db.load_ma_fan_day("2026-09-28")
    assert new["items"][0]["pick"]["score"] is not None and "fwd" not in new["items"][0]
    again = asyncio.run(mx.refresh_ma_fan_extras(now=datetime(2026, 9, 28, 16, 0)))
    assert again.get("skipped") is True


def test_refresh_keeps_concurrent_meta_backfill(monkeypatch) -> None:
    desk_db.save_ma_fan_day("2026-09-22", {"items": [{"code": "300171", "close": 11.0, "industry": ""}]})
    series = _bars([10, 11, 9.9, 12, 12.5, 13])

    async def fake_fetch(codes):
        desk_db.save_ma_fan_day("2026-09-22", {"items": [{"code": "300171", "close": 11.0, "industry": "医疗器械"}]})
        return {"300171": series}, []

    monkeypatch.setattr(mx, "_fetch_series", fake_fetch)
    asyncio.run(mx.refresh_ma_fan_extras(force=True, now=datetime(2026, 9, 29, 16, 0)))
    body = desk_db.load_ma_fan_day("2026-09-22")
    assert body["items"][0]["industry"] == "医疗器械" and body["items"][0]["fwd"]["final"] is True


def test_api_ma_fan_returns_outcome_and_board(monkeypatch) -> None:
    import market_desk.ma_fan as mf
    from market_desk.routes import ma_fan as route

    desk_db.save_ma_fan_day("2026-09-22", {
        "items": [{"code": "300171", "close": 11.0, "industry": "", "fwd": {"d1": 1.0, "d3": 2.0, "final": True}}],
        "index_fwd": {"d1": 0.5, "d3": 1.0, "final": True},
    })
    calls: list[str] = []

    async def fake_backfill(day, body):
        calls.append(day)
        body["items"][0]["industry"] = "医疗器械"
        return True

    async def fake_refresh(**kw):
        return {"ok": True}

    monkeypatch.setattr(mf, "backfill_day_meta", fake_backfill)
    monkeypatch.setattr(mf, "refresh_ma_fan_extras", fake_refresh)
    out = asyncio.run(route.api_ma_fan(date="2026-09-22", user={}))
    item = out["scan"]["items"][0]
    assert calls == ["2026-09-22"] and item["industry"] == "医疗器械" and item["market_board"] == "创业"
    assert desk_db.load_ma_fan_day("2026-09-22")["items"][0]["industry"] == "医疗器械"
    assert out["outcome"]["day"]["d3"] == {"n": 1, "win": 100.0, "avg": 2.0}
    assert out["outcome"]["recent"]["excess_d3"] == 1.0


def test_outcome_summary_day_and_recent() -> None:
    day_body = {
        "items": [
            {"code": "a", "fwd": {"d1": 1.0, "d3": 3.0, "final": True}, "pick": {"grade": "可以考虑"}},
            {"code": "b", "fwd": {"d1": -2.0, "d3": -1.0, "final": True}, "pick": {"grade": "谨慎"}},
            {"code": "c", "fwd": {"d1": 0.5, "final": False}},
        ],
        "index_fwd": {"d1": 0.2, "d3": 1.0, "final": True},
    }
    recent = {"2026-09-22": day_body, "2026-09-25": {"items": [{"code": "x", "fwd": {"d1": 1.0, "final": False}}]}}
    out = mx.build_outcome_summary("2026-09-22", day_body, recent)
    assert out["day"]["d1"] == {"n": 3, "win": 66.7, "avg": -0.17}
    assert out["day"]["d3"]["n"] == 2 and out["day"]["index_d3"] == 1.0
    rec = out["recent"]
    assert rec["days"] == 1 and rec["d3"] == {"n": 2, "win": 50.0, "avg": 1.0}
    assert rec["excess_d3"] == 0.0
    assert rec["by_grade"]["可以考虑"]["win"] == 100.0
