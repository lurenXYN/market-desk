"""Pot-bottom detector and its wiring into the nightly MA-fan scan / extras / API."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import market_desk.db as desk_db
from market_desk.ma_fan import extras as mx
from market_desk.ma_fan import job as mf_job
from market_desk.ma_fan import scan as mf_scan
from market_desk.ma_fan import sources as mf_src
from market_desk.pot_bottom import POT_FOCUS_TAG, is_pot_focus, score_pot_bottom


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    monkeypatch.setattr(mf_scan, "MA_FAN_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(mf_src, "MA_FAN_PAGE_DELAY_S", 0.0)
    monkeypatch.setattr(mf_job, "MA_FAN_CACHE_FROM", (24, 0))
    monkeypatch.setattr(mx, "_LAST_RUN", 0.0)
    monkeypatch.setattr(mx, "_RUNNING", False)
    mf_job._PROGRESS.clear()
    mf_job._PROGRESS["running"] = False
    mf_job._SCORE_CACHE.clear()
    yield
    mf_job._PROGRESS.clear()
    mf_job._PROGRESS["running"] = False
    mf_job._SCORE_CACHE.clear()


def _pot_bars(*, lift_vol: float = 2.4e6, last_close: float = 15.8) -> list[dict]:
    """78 flat sessions at 19, a sharp drop, a 15-session quiet base around 15, then a lift-off."""
    closes = [19.0] * 78 + [17.0]
    closes += [15.0 if k % 2 == 0 else 15.2 for k in range(15)]
    closes.append(last_close)
    vols = [2e6] * 79 + [1e6] * 15 + [lift_vol]
    return [
        {"date": f"D{i:03d}", "open": c, "close": c, "high": c + 0.1, "low": c - 0.1, "volume": v}
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def test_detects_quiet_base_lift_off() -> None:
    got = score_pot_bottom(_pot_bars())
    assert got is not None
    assert got["stage"] == "初期" and got["days_since_base"] == 1
    assert got["base_start"] == "D079" and got["base_end"] == "D093" and got["base_len"] == 15
    assert got["vol_shrink"] == 0.5 and 15 <= got["drop_pct"] < 25
    assert got["lift_vol"] == pytest.approx(2.4)
    assert got["pct"] == pytest.approx((15.8 / 15.0 - 1) * 100, abs=0.01)
    assert got["focus"] is True and POT_FOCUS_TAG in got["tags"]


def test_rejects_violent_volume_and_no_breakout() -> None:
    assert score_pot_bottom(_pot_bars(lift_vol=5e6)) is None
    assert score_pot_bottom(_pot_bars(last_close=15.1)) is None
    assert score_pot_bottom(_pot_bars()[:-1]) is None
    assert score_pot_bottom(_pot_bars()[:90]) is None


def test_focus_subset_bounds() -> None:
    assert is_pot_focus(15.0, 2.0) and is_pot_focus(24.9, 3.0)
    assert not is_pot_focus(25.0, 3.0) and not is_pot_focus(20.0, 1.9)


def test_scan_stores_pot_rows_beside_fans(monkeypatch) -> None:
    async def fake_universe(client, **kw):
        return [
            {"code": "600000", "name": "锅底A", "amount": 5e9, "pct": 4.0, "mktcap_wan": 1.2e6},
            {"code": "600001", "name": "平盘B", "amount": 4e9, "pct": 0.0},
        ]

    async def fake_bars(client, code, limit=120):
        if code == "600000":
            return _pot_bars()
        return [{"date": f"D{i:03d}", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1e6}
                for i in range(120)]

    async def no_meta(client, items):
        return items

    monkeypatch.setattr(mf_scan, "load_universe", fake_universe)
    monkeypatch.setattr(mf_scan, "fetch_daily_bars", fake_bars)
    monkeypatch.setattr(mf_scan, "enrich_hits_meta", no_meta)
    out = asyncio.run(mf_scan.run_ma_fan_scan(trade_date="2026-09-24", offset=0, limit=400, slice_key="0-400"))
    assert out["items"] == [] and out["pot_n"] == 1 and out["pot_version"] == 1
    row = out["pot"][0]
    assert row["code"] == "600000" and row["name"] == "锅底A" and row["in_fan"] is False
    assert row["amount_rank"] == 1 and row["mv_yi"] == 120.0 and row["focus"] is True
    stored = desk_db.load_ma_fan_day("2026-09-24")
    assert [x["code"] for x in stored["pot"]] == ["600000"]


def test_merge_pot_puts_focus_first_and_flags_fan_overlap() -> None:
    prev = [{"code": "000001", "focus": False, "base_len": 30}]
    new = [{"code": "000002", "focus": True, "base_len": 9}, {"code": "000001", "focus": False, "base_len": 12}]
    out = mf_scan._merge_pot(prev, new, fan_codes={"000001"})
    assert [x["code"] for x in out] == ["000002", "000001"]
    assert out[1]["base_len"] == 12 and out[1]["in_fan"] is True and out[0]["in_fan"] is False


def test_refresh_fills_pot_outcomes_and_api_summarizes(monkeypatch) -> None:
    from market_desk.routes import ma_fan as route
    import market_desk.ma_fan as mf

    dates = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]
    series = [{"date": d, "close": c} for d, c in zip(dates, [10, 11, 9.9, 12, 12.5, 13])]
    desk_db.save_ma_fan_day("2026-09-22", {
        "items": [],
        "pot": [{"code": "300171", "close": 11.0, "focus": True}, {"code": "600000", "close": 11.0}],
    })

    async def fake_fetch(codes):
        assert codes == ["300171", "600000"]
        return {c: series for c in codes}, [(d, c * 100) for d, c in zip(dates, [10, 10, 10, 10, 10.2, 10])]

    monkeypatch.setattr(mx, "_fetch_series", fake_fetch)
    out = asyncio.run(mx.refresh_ma_fan_extras(now=datetime(2026, 9, 28, 16, 0)))
    assert out["fwd"] == 2
    body = desk_db.load_ma_fan_day("2026-09-22")
    assert all(p["fwd"]["final"] for p in body["pot"]) and "pick" not in body["pot"][0]

    async def fake_refresh(**kw):
        return {"ok": True}

    monkeypatch.setattr(mf, "refresh_ma_fan_extras", fake_refresh)
    api = asyncio.run(route.api_ma_fan(date="2026-09-22", user={}))
    po = api["pot_outcome"]
    assert po["all"]["recent"]["d3"]["n"] == 2 and po["focus"]["recent"]["d3"]["n"] == 1
    assert po["focus"]["recent"]["excess_d3"] == pytest.approx(13.64 - 2.0, abs=0.01)


def test_api_has_no_pot_outcome_for_days_before_pot() -> None:
    from market_desk.routes import ma_fan as route

    desk_db.save_ma_fan_day("2026-09-22", {"items": []})
    assert route._pot_outcome("2026-09-22", {"items": []}, {}, mx.build_outcome_summary) is None
