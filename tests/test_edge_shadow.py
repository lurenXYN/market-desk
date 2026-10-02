"""Edge shadow ledger: frozen day book, reclaim / breaker / reweight attribution."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.edge_shadow import build_edge_shadow, reweight_parts, reweight_score
from market_desk.review.record import record_session_signals


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _up(code: str, at: str, last: float, *, ready: int = 0, **payload) -> None:
    desk_db.upsert_signal({
        "trade_date": "2026-10-08", "signaled_at": at, "signal_type": "buy",
        "action": "观察回踩", "phase": "发酵", "mainline": "电子", "code": code,
        "name": code, "kind": "stock", "price": 10.0, "last": last, "ready": ready,
        "payload": {"plan_price": 10.0, **payload},
    })


def _row(day: str, code: str, d3: float, *, at: str = "09:40", sig: str = "buy", **payload) -> dict:
    return {
        "trade_date": day, "signaled_at": f"{day} {at}:00", "signal_type": sig, "code": code,
        "price": 10.0, "outcome_day3_pct": d3, "skipped": 0, "ready": 0,
        "payload": {"plan_price": 10.0, **payload},
    }


def test_day_book_frozen_at_first_sight_and_first_light(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    assert desk_db.day_lit_book("2026-10-08") == {"n": 0, "avg_pct": None, "worst_pct": None}
    _up("600001", "2026-10-08 09:35:00", 10.0, day_book={"n": 0, "avg_pct": None, "worst_pct": None})
    _up("600001", "2026-10-08 09:40:00", 10.1, ready=1, day_book={"n": 1, "avg_pct": -2.0, "worst_pct": -2.0})
    _up("600001", "2026-10-08 09:50:00", 9.8, ready=1, day_book={"n": 1, "avg_pct": -5.0, "worst_pct": -5.0})
    p = desk_db.load_signals_for_date("2026-10-08")[0]["payload"]
    assert p["book_open"]["n"] == 0
    assert p["book_ready"]["avg_pct"] == -2.0
    assert p["first_ready_px"] == 10.1
    assert "day_book" not in p
    book = desk_db.day_lit_book("2026-10-08", {"600001": 9.09})
    assert book["n"] == 1 and book["avg_pct"] == -10.0
    assert desk_db.day_lit_book("2026-10-08")["avg_pct"] == -2.97


def test_record_session_attaches_day_book(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _up("600001", "2026-10-08 09:35:00", 10.0, ready=1)
    snap = {
        "trade_date": "2026-10-08", "updated_at": "2026-10-08 10:05:00", "phase": "发酵",
        "verdict": {"action": "观察回踩", "mainline": {"name": "电子"}, "recommend": {"items": [
            {"code": "600001", "name": "A", "plan_price": 10.0, "last": 9.7, "ready": True},
            {"code": "600002", "name": "B", "plan_price": 20.0, "last": 20.5},
        ]}},
    }
    assert record_session_signals(snap) == 2
    rows = {r["code"]: r["payload"] for r in desk_db.load_signals_for_date("2026-10-08")}
    assert rows["600002"]["book_open"] == {"n": 1, "avg_pct": -3.0, "worst_pct": -3.0}
    assert "book_open" not in rows["600001"]


def test_reclaim_skips_unreclaimed_and_reprices_entry():
    rows = [
        _row("2026-10-08", "600001", 5.0, touched_plan=True, shadow_reclaim_px=10.2),
        _row("2026-10-08", "600002", -4.0, touched_plan=True),
        _row("2026-10-08", "600003", 9.0),
    ]
    rec = build_edge_shadow(rows)["reclaim"][0]
    assert rec["n"] == 2 and rec["reclaim_n"] == 1 and rec["skip_n"] == 1
    assert rec["touch_d3"] == 0.5 and rec["reclaim_d3"] == 2.94 and rec["skip_d3"] == -4.0
    assert rec["delta"] == 0.97 and rec["verdict"] == "样本不足"


def test_breaker_levels_use_ready_book_for_lit_cards():
    rows = [
        _row("2026-10-08", "600001", -3.0, book_open={"n": 2, "avg_pct": -2.5}),
        _row("2026-10-08", "600002", 1.0, book_open={"n": 0, "avg_pct": None}),
        _row("2026-10-08", "600003", -1.0, ever_ready=True,
             book_open={"n": 0, "avg_pct": None}, book_ready={"n": 3, "avg_pct": -1.2}),
        _row("2026-10-08", "600004", 8.0),
    ]
    by = {r["level"]: r for r in build_edge_shadow(rows)["breaker"]}
    assert (by[-1.0]["n"], by[-1.0]["lit_n"], by[-1.0]["diff"]) == (2, 1, -3.0)
    assert (by[-2.0]["n"], by[-2.0]["kept_d3"], by[-2.0]["diff"]) == (1, 0.0, -3.0)
    assert by[-3.0]["n"] == 0 and by[-3.0]["diff"] is None


def test_reweight_parts_and_out_of_sample_split():
    dragon = _row("2026-10-08", "600001", -6.0, at="13:20", sig="buy_dragon", trend_ok=True)
    assert [k for k, _ in reweight_parts(dragon)] == ["龙头卡", "日线上升", "13–14点出卡"]
    assert reweight_score(dragon) == -3
    calm = _row("2026-10-08", "600002", 2.0, at="10:30", trend_down=True)
    assert reweight_score(calm) == 2
    old = _row("2026-09-15", "600003", 4.0, at="13:10")
    peer = _row("2026-09-15", "600004", 1.0)
    out = build_edge_shadow([dragon, calm, old, peer])
    assert out["oos_days"] == 1 and out["oos_n"] == 2 and out["disc_n"] == 2
    down = next(r for r in out["reweight"] if r["key"] == "合计降权")
    assert down["n"] == 1 and down["excess"] == -8.0
    assert down["disc_n"] == 1 and down["disc_excess"] == 3.0


def test_same_code_counts_once_preferring_lit_row():
    rows = [
        _row("2026-10-08", "600001", 3.0, sig="buy_dragon", at="09:31"),
        _row("2026-10-08", "600001", 3.0, sig="buy", at="09:50", ever_ready=True),
    ]
    out = build_edge_shadow(rows)
    assert out["oos_n"] == 1
    assert next(r for r in out["reweight"] if r["key"] == "龙头卡")["n"] == 0
