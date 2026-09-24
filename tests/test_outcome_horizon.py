"""三日% must use the real day-3 close; partial horizons get refreshed."""

from __future__ import annotations

from datetime import datetime

import market_desk.review as rv


def _sig(**kw):
    base = {"id": 1, "trade_date": "2026-09-14", "signal_type": "buy", "price": 10.0, "payload": {}}
    base.update(kw)
    return base


def test_unfinished_day3_bar_is_ignored(monkeypatch):
    fixed = datetime(2026, 9, 17, 10, 0, tzinfo=rv._CN_TZ)
    monkeypatch.setattr(rv, "_cn_now", lambda: fixed)
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    closes = [10.0, 10.05, 10.1, 12.0]
    out = rv.score_signal_with_closes(_sig(), closes, dates)
    assert out["outcome_day1_pct"] == 0.5
    assert out["outcome_day3_pct"] == 1.0
    assert out["outcome_label"] == "平淡"


def test_outcome_final_date_skips_weekend():
    assert rv.outcome_final_date("2026-09-17") == "2026-09-22"


def test_outcome_is_final_by_checked_time():
    row = {"trade_date": "2026-09-14", "outcome_label": "平淡"}
    assert not rv.outcome_is_final({**row, "outcome_checked_at": "2026-09-16 15:30:00"})
    assert not rv.outcome_is_final({**row, "outcome_checked_at": "2026-09-17 10:00:00"})
    assert rv.outcome_is_final({**row, "outcome_checked_at": "2026-09-17 15:06:00"})
    assert not rv.outcome_is_final({"trade_date": "2026-09-14", "outcome_checked_at": "2026-09-30 10:00:00"})


def test_apply_outcomes_refreshes_partial_horizon(monkeypatch):
    fixed = datetime(2026, 9, 18, 16, 0, tzinfo=rv._CN_TZ)
    monkeypatch.setattr(rv, "_cn_now", lambda: fixed)
    written: list[dict] = []
    monkeypatch.setattr(rv, "mark_signal_outcome", lambda sid, oc: written.append(oc) or True)
    stale = _sig(
        outcome_label="平淡",
        outcome_day1_pct=0.5,
        outcome_day3_pct=0.5,
        outcome_checked_at="2026-09-15 15:30:00",
    )
    dates = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]
    packed = {"000001": (dates, [10.0, 10.05, 10.1, 10.3])}
    stale["code"] = "000001"
    n = rv.apply_outcomes([stale], packed)
    assert n == 1
    assert written[-1]["outcome_day3_pct"] == 3.0
    assert written[-1]["outcome_label"] == "三日红"

    final_row = {**stale, **written[-1], "outcome_checked_at": "2026-09-17 15:30:00"}
    written.clear()
    assert rv.apply_outcomes([final_row], packed) == 0
    assert written == []
