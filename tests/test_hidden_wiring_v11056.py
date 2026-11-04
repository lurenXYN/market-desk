"""v1.10.56: residual fake-wiring fixes (week miss / live_last / pot / gates / …)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import market_desk.db as desk_db
from market_desk.adapt.bundle import _remap_rows_for_adapt_standard, build_adapt_bundle
from market_desk.db.bars_cache import ensure_bars_cache_tables, load_daily_bars
from market_desk.db.signals import set_signal_payload_once, upsert_signal
from market_desk.ma_fan.context import ma_fan_code_set
from market_desk.pick_score import pick0_is_stub, pick0_may_upgrade
from market_desk.review.digest import build_week_exec_board
from market_desk.review.exec_score import classify_miss_kind
from market_desk.review.hit_rates import build_missed_buys
from market_desk.review.payload import enrich_signals_with_live_marks
from market_desk.review.record import record_session_signals


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def test_classify_miss_kind_reads_frozen_np_without_flags():
    row = {
        "signal_type": "buy",
        "traded": 0,
        "price": 10.0,
        "price_flags": [],
        "payload": {"np": {"np": True, "day_low": 10.2, "day_close": 10.5, "plan": 10.0}},
    }
    assert classify_miss_kind(row) == "never_touched"


def test_classify_miss_kind_touched_from_frozen_np():
    row = {
        "signal_type": "buy",
        "traded": 0,
        "price": 10.0,
        "price_flags": [],
        "payload": {"np": {"np": False, "day_low": 9.8, "day_close": 10.5, "plan": 10.0}},
    }
    assert classify_miss_kind(row) == "touched_not_bought"


def test_classify_gate_blocked_uses_frozen_close_not_today_last():
    row = {
        "signal_type": "buy",
        "traded": 0,
        "ready": 0,
        "price": 10.0,
        "last": 20.0,  # today's quote after upsert refresh — must be ignored
        "price_flags": [],
        "payload": {
            "confirm_fail": ["minute"],
            "np": {"np": False, "day_low": 9.5, "day_close": 10.8, "plan": 10.0},
        },
    }
    # frozen day_close 10.8 > plan + fails → gate_blocked (not polluted by last=20)
    assert classify_miss_kind(row) == "gate_blocked"
    # day_close below plan → not gate (touched instead)
    row2 = dict(row)
    row2["payload"] = {
        "confirm_fail": ["minute"],
        "np": {"np": False, "day_low": 9.5, "day_close": 9.9, "plan": 10.0},
    }
    assert classify_miss_kind(row2) == "touched_not_bought"


def test_week_exec_counts_np_on_raw_rows():
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "traded": 0,
            "price": 10.0,
            "payload": {"np": {"np": True, "day_low": 10.2, "day_close": 10.5, "plan": 10.0}},
        },
        {
            "id": 2,
            "code": "600002",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "traded": 0,
            "price": 10.0,
            "payload": {"np": {"np": False, "day_low": 9.8, "day_close": 10.2, "plan": 10.0}},
        },
    ]
    marked = enrich_signals_with_live_marks(rows, {}, today="2026-11-04")
    board = build_week_exec_board(marked, dates=["2026-11-02"], days=1)
    assert board["miss_counts"]["never_touched"] == 1
    assert board["miss_counts"]["touched_not_bought"] == 1


def test_historical_live_last_is_frozen_close_not_today_quote():
    rows = [
        {
            "code": "600001",
            "trade_date": "2026-10-10",
            "signal_type": "buy",
            "price": 10.0,
            "payload": {
                "np": {"np": True, "day_low": 10.2, "day_close": 10.8, "plan": 10.0},
                "stop_price": 9.0,
                "chase_price": 11.0,
                "wait_price": 10.0,
            },
        }
    ]
    quotes = {"600001": {"price": 20.0, "low": 19.0, "pct": 5.0}}
    out = enrich_signals_with_live_marks(rows, quotes, today="2026-11-03")
    assert out[0].get("live_last") is None
    assert out[0]["day_close"] == 10.8
    assert out[0].get("live_pct") is None
    # gate_blocked must not see today's 20
    out[0]["payload"] = {
        **out[0]["payload"],
        "confirm_fail": ["minute"],
    }
    out[0]["ready"] = 0
    out[0]["traded"] = 0
    # day_close 10.8 > plan → would be gate if we used that; miss_pullback wins first
    assert classify_miss_kind(out[0]) == "never_touched"


def test_build_missed_buys_accepts_frozen_np():
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "traded": 0,
            "skipped": 0,
            "price": 10.0,
            "price_flags": [],
            "payload": {"np": {"np": True, "day_low": 10.2, "day_close": 10.5, "plan": 10.0}},
        }
    ]
    missed = build_missed_buys(rows, trade_date="2026-11-02")
    assert len(missed) == 1


def test_ma_fan_code_set_includes_pot(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    desk_db.save_ma_fan_day(
        "2026-11-03",
        {
            "trade_date": "2026-11-03",
            "items": [{"code": "600001", "note": "fan"}],
            "pot": [{"code": "600099", "note": "pot"}],
        },
    )
    codes = ma_fan_code_set("2026-11-03")
    assert "600001" in codes
    assert "600099" in codes


def test_market_regime_fill_once_when_first_insert_empty(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    snap = {
        "trade_date": "2026-10-15",
        "updated_at": "2026-10-15 10:05:00",
        "phase": "发酵",
        # No market_regime on first insert (engine blip).
        "verdict": {
            "action": "观察回踩",
            "mainline": {"name": "电子"},
            "recommend": {
                "items": [
                    {"code": "600001", "name": "A", "plan_price": 10.0, "last": 9.7},
                ]
            },
        },
    }
    assert record_session_signals(snap) == 1
    p = desk_db.load_signals_for_date("2026-10-15")[0]["payload"]
    assert "market_regime" not in p or not p.get("market_regime")

    snap2 = dict(snap)
    snap2["updated_at"] = "2026-10-15 10:10:00"
    snap2["market_regime"] = {
        "ok": True,
        "side": "above",
        "asof": "2026-10-15",
        "close": 110.0,
        "ma": 100.0,
        "ma_n": 60,
        "label": "中证1000",
        "gap_pct": 10.0,
    }
    assert record_session_signals(snap2) == 1
    p2 = desk_db.load_signals_for_date("2026-10-15")[0]["payload"]
    assert p2["market_regime"]["side"] == "above"

    # Later flip must still freeze.
    snap3 = dict(snap2)
    snap3["updated_at"] = "2026-10-15 14:00:00"
    snap3["market_regime"] = {**snap2["market_regime"], "side": "below"}
    assert record_session_signals(snap3) == 1
    p3 = desk_db.load_signals_for_date("2026-10-15")[0]["payload"]
    assert p3["market_regime"]["side"] == "above"


def test_remap_no_bars_strips_classic_not_silent_fallback():
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "outcome_label": "三日红",
            "outcome_day3_pct": 5.0,
        }
    ]
    remapped, n_hit = _remap_rows_for_adapt_standard(rows, "same_day_plan", closes_map={})
    assert n_hit == 0
    assert remapped[0]["outcome_label"] is None
    assert remapped[0]["outcome_day3_pct"] is None


def test_adapt_bundle_basis_same_day_even_without_bars(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "owner_user_id": 0,
            "price": 10.0,
            "outcome_label": "三日红",
            "outcome_day3_pct": 5.0,
            "payload": {},
        }
    ]
    bundle = build_adapt_bundle(rows=rows, trade_date="2026-11-02")
    assert bundle["outcome_basis"] == "same_day_plan"
    assert "不回退" in bundle["outcome_basis_note"] or "清空" in bundle["outcome_basis_note"]


def test_pick0_stub_and_live_last_upgrade(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    upsert_signal(
        {
            "trade_date": "2026-11-04",
            "code": "600001",
            "signal_type": "buy",
            "owner_user_id": 0,
            "signaled_at": "2026-11-04 10:00:00",
            "price": 10.0,
            "ready": 0,
            "payload": {},
        }
    )
    sid = desk_db.load_signals_for_date("2026-11-04")[0]["id"]
    stub = {"score": 50, "grade": "C", "factors": [], "at": "10:01"}
    assert pick0_is_stub(stub)
    n = set_signal_payload_once({sid: ("pick0", stub)})
    assert n == 1
    full = {
        "score": 72,
        "grade": "B",
        "factors": [{"key": "x", "pts": 1}],
        "live_last": 10.5,
        "at": "10:05",
    }
    assert pick0_may_upgrade(stub, full)
    n2 = set_signal_payload_once(
        {sid: ("pick0", full)},
        replace_if=lambda k, old, new: k == "pick0" and pick0_may_upgrade(old, new),
    )
    assert n2 == 1
    p0 = desk_db.load_signals_for_date("2026-11-04")[0]["payload"]["pick0"]
    assert p0["score"] == 72
    assert p0["live_last"] == 10.5
    # Complete freeze must not overwrite score even if live_last patch claimed.
    n3 = set_signal_payload_once(
        {sid: ("pick0", {**full, "score": 99})},
        replace_if=lambda k, old, new: k == "pick0" and pick0_may_upgrade(old, new),
    )
    assert n3 == 0
    # live_last-only patch keeps frozen score/at.
    from market_desk.pick_score import pick0_patch_live_last

    # Clear live_last to simulate first freeze without quote.
    set_signal_payload_once(
        {sid: ("pick0", {**full, "live_last": None})},
        replace_if=lambda *_: True,
    )
    # Force-write without live_last
    with desk_db._connect() as conn:
        import json

        row = conn.execute("SELECT payload FROM signals WHERE id=?", (sid,)).fetchone()
        payload = json.loads(row["payload"])
        payload["pick0"] = {
            "score": 72,
            "grade": "B",
            "factors": [{"key": "x", "pts": 1}],
            "at": "10:05",
        }
        conn.execute(
            "UPDATE signals SET payload=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), sid),
        )
        conn.commit()
    raw = desk_db.load_signals_for_date("2026-11-04")[0]["payload"]["pick0"]
    patched = pick0_patch_live_last(raw, 11.2)
    assert patched is not None and patched["score"] == 72 and patched["live_last"] == 11.2
    assert pick0_may_upgrade(raw, patched)
    # Different score must NOT upgrade.
    bad = {**patched, "score": 99}
    assert not pick0_may_upgrade(raw, bad)


def test_cv_fetch_upserts_market_bar_daily(tmp_path, monkeypatch):
    import asyncio

    _init(tmp_path, monkeypatch)
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()

    payload = {
        "data": {
            "sh600001": {
                "qfqday": [
                    ["2026-11-01", "10", "10.5", "10.8", "9.9", "1000", "0", "1.2"],
                    ["2026-11-02", "10.5", "10.6", "10.9", "10.2", "1100", "0", "1.1"],
                ]
            }
        }
    }
    client = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    client.get = AsyncMock(return_value=resp)

    from market_desk.chip_volume import fetch_bars_with_turnover

    bars = asyncio.run(fetch_bars_with_turnover(client, "600001", limit=10))
    assert len(bars) == 2
    cached = load_daily_bars("600001", limit=10)
    assert len(cached) >= 2
    assert cached[-1]["close"] == 10.6
