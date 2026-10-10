"""Review score column: live scoring today, stored first score on past days."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import market_desk.db as desk_db
from market_desk.engine import CN_TZ, DeskEngine
from market_desk.pick_score import pick_top


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _signal(day: str, code: str, sig_type: str = "buy", payload: dict | None = None) -> int:
    desk_db.upsert_signal({
        "trade_date": day, "signaled_at": f"{day} 10:00:00", "signal_type": sig_type,
        "code": code, "name": f"N{code}", "kind": "stock", "price": 10.0, "ready": 0,
        "payload": payload or {"plan_price": 10.0},
    })
    return int(next(r["id"] for r in desk_db.load_signals_for_date(day) if r["code"] == code and r["signal_type"] == sig_type))


def _engine(monkeypatch) -> DeskEngine:
    eng = DeskEngine.__new__(DeskEngine)

    async def fake_enrich(rows, today):
        return rows

    monkeypatch.setattr(eng, "_enrich_pick_rows", fake_enrich)
    return eng


def test_pick_top_lists_clean_codes_once():
    plus = {"key": "cap", "label": "流通市值", "points": 3}
    risk = {"key": "chip", "label": "筹码位置", "points": -2}
    hard = {"key": "gate", "label": "确认闸门", "points": -10}
    items = {
        "1": {"code": "600001", "name": "A", "factors": []},
        "2": {"code": "600001", "name": "A", "factors": [plus]},
        "3": {"code": "600002", "name": "B", "factors": [risk]},
        "4": {"code": "600003", "name": "C", "factors": []},
        "5": {"code": "600004", "name": "D", "factors": [hard, plus]},
        "6": {"code": "600005", "name": "E", "factors": [], "blocked": True},
    }
    top = pick_top(items)
    assert [(t["code"], t["plus_n"], t["id"]) for t in top] == [("600001", 1, "2"), ("600003", 0, "4")]


def test_set_signal_payload_once_skips_existing_key():
    day = "2026-09-25"
    a = _signal(day, "600001")
    b = _signal(day, "600002", payload={"plan_price": 10.0, "pick0": {"score": 50}})
    n = desk_db.set_signal_payload_once({a: ("pick0", {"score": 70}), b: ("pick0", {"score": 99})})
    assert n == 1
    by = {r["id"]: r["payload"] for r in desk_db.load_signals_for_date(day)}
    assert by[a]["pick0"] == {"score": 70} and by[b]["pick0"] == {"score": 50}


def test_today_scores_live_and_store_first(monkeypatch):
    today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    sid = _signal(today, "600001")
    _signal(today, "600009", sig_type="sell")
    eng = _engine(monkeypatch)
    out = asyncio.run(eng.build_review_scores())
    assert out["live"] is True and list(out["items"]) == [str(sid)]
    item = out["items"][str(sid)]
    assert item["score"] == 60 and "first" not in item and item["check"]["label"] == "无风险"
    assert out["audit"]["status"] == "building" and out["day_book"]["n"] == 0
    stored = desk_db.load_signal(sid)["payload"]["pick0"]
    assert stored["score"] == 60 and stored["at"] == item["at"] and stored["check"]["label"] == "无风险"
    again = asyncio.run(eng.build_review_scores())
    assert again.get("cache_hit") is True
    eng._review_score_cache = None
    third = asyncio.run(eng.build_review_scores())
    assert third["items"][str(sid)]["first"]["score"] == 60


def test_past_day_shows_stored_score_only(monkeypatch):
    day = "2026-09-24"
    sid = _signal(day, "600001", payload={"plan_price": 10.0, "pick0": {"score": 81, "grade": "优先", "factors": [], "at": "10:05"}})
    _signal(day, "600002")
    eng = _engine(monkeypatch)
    out = asyncio.run(eng.build_review_scores(day))
    assert out["live"] is False
    assert list(out["items"]) == [str(sid)]
    assert out["items"][str(sid)]["stored"] is True
    assert out["items"][str(sid)]["check"]["label"] == "无风险"
    assert out["top"][0]["code"] == "600001"


def test_audit_rows_read_pick0_since_date(monkeypatch):
    early = _signal("2026-10-07", "600001", payload={"plan_price": 10.0, "pick0": {"score": 70}})
    sid = _signal("2026-10-08", "600002", payload={"plan_price": 10.0, "pick0": {"score": 55}})
    _signal("2026-10-08", "600003")
    for x in (early, sid):
        desk_db.mark_signal_outcome(x, {"outcome_day3_pct": 1.5, "outcome_label": "平淡"})
    rows = desk_db.load_pick_audit_rows("2026-10-08")
    assert [(r["code"], r["payload"].get("pick0", {}).get("score")) for r in rows] == [("600002", 55)]
