"""v1.10.81: news_radar placeholder fill-once + shadow bucket hygiene."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db.signals import upsert_signal
from market_desk.news_radar import news_radar_shadow_summary


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _buy_row(*, code: str, day: str, payload: dict) -> dict:
    return {
        "trade_date": day,
        "code": code,
        "name": code,
        "signal_type": "buy",
        "owner_user_id": 0,
        "signaled_at": f"{day} 10:00:00",
        "price": 10.0,
        "ready": 1,
        "payload": payload,
    }


def test_missing_news_radar_fills_once_then_freezes(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    day = "2026-11-06"
    upsert_signal(
        _buy_row(
            code="600001",
            day=day,
            payload={
                "ever_ready": True,
                "news_radar": {
                    "ok": False,
                    "status": "missing",
                    "hit": False,
                    "matches_n": 0,
                },
            },
        )
    )
    p1 = desk_db.load_signals_for_date(day)[0]["payload"]
    assert p1["news_radar"]["status"] == "missing"

    upsert_signal(
        _buy_row(
            code="600001",
            day=day,
            payload={
                "ever_ready": True,
                "news_radar": {
                    "ok": True,
                    "status": "online",
                    "hit": True,
                    "confirm": "resonance",
                    "sector": "半导体",
                    "matches_n": 1,
                },
            },
        )
    )
    p2 = desk_db.load_signals_for_date(day)[0]["payload"]
    assert p2["news_radar"]["status"] == "online"
    assert p2["news_radar"]["hit"] is True

    # Later flip must still freeze the first real capture.
    upsert_signal(
        _buy_row(
            code="600001",
            day=day,
            payload={
                "ever_ready": True,
                "news_radar": {
                    "ok": True,
                    "status": "online",
                    "hit": False,
                    "confirm": "news_only",
                    "sector": "军工",
                    "matches_n": 1,
                },
            },
        )
    )
    p3 = desk_db.load_signals_for_date(day)[0]["payload"]
    assert p3["news_radar"]["hit"] is True
    assert p3["news_radar"]["sector"] == "半导体"


def test_shadow_excludes_placeholder_from_without_bucket(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    monkeypatch.setattr("market_desk.config.NEWS_RADAR_SHADOW_SINCE", "2026-11-01")
    day = "2026-11-05"
    # Lit + real miss → without bucket
    upsert_signal(
        _buy_row(
            code="600010",
            day=day,
            payload={
                "ever_ready": True,
                "news_radar": {
                    "ok": True,
                    "status": "online",
                    "hit": False,
                    "matches_n": 0,
                },
            },
        )
    )
    # Lit + missing placeholder → missing_freeze, not without
    upsert_signal(
        _buy_row(
            code="600011",
            day=day,
            payload={
                "ever_ready": True,
                "news_radar": {
                    "ok": False,
                    "status": "missing",
                    "hit": False,
                    "matches_n": 0,
                },
            },
        )
    )
    # Lit + disabled → placeholder_freeze
    upsert_signal(
        _buy_row(
            code="600012",
            day=day,
            payload={
                "ever_ready": True,
                "news_radar": {
                    "ok": False,
                    "status": "disabled",
                    "hit": False,
                    "matches_n": 0,
                },
            },
        )
    )
    # Patch outcome labels via direct SQL (summary only needs lit + payload).
    with desk_db._connect() as conn:
        conn.execute(
            "UPDATE signals SET outcome_label=?, outcome_day3_pct=? WHERE code=?",
            ("三日红", 2.0, "600010"),
        )
        conn.execute(
            "UPDATE signals SET outcome_label=?, outcome_day3_pct=? WHERE code=?",
            ("三日绿", -1.0, "600011"),
        )
        conn.execute(
            "UPDATE signals SET outcome_label=?, outcome_day3_pct=? WHERE code=?",
            ("三日绿", -1.5, "600012"),
        )
        conn.commit()

    out = news_radar_shadow_summary(days=30)
    assert out["without_news_hit"]["n"] == 1
    assert out["without_news_hit"]["scored"] == 1
    assert out["missing_freeze"] == 1
    assert out["placeholder_freeze"] == 1
    assert out["with_news_hit"]["n"] == 0
    # Control arm must not absorb the two placeholder cards' day3.
    assert out["without_news_hit"]["avg_day3"] == 2.0
