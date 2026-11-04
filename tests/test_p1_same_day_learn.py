"""P1: adapt learning uses same_day_plan; dual_wins exposes touch column."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.adapt.bundle import (
    _remap_rows_for_adapt_standard,
    build_adapt_bundle,
    closes_map_from_bar_cache,
    set_adapt_bars,
)
from market_desk.adapt.context import _buy_scored
from market_desk.db.bars_cache import ensure_bars_cache_tables, upsert_daily_bars
from market_desk.dual_score import oos_buy_dual_wins


def _init(tmp_path, monkeypatch):
    db = tmp_path / "p1.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()


def _bars_for(code: str, days: list[tuple[str, float, float, float]]):
    """days: (date, low, close, next_close_for_forward) — builds multi-day series."""
    out = []
    for d, lo, cl, _ in days:
        out.append(
            {
                "date": d,
                "open": cl,
                "high": max(cl, lo) + 0.5,
                "low": lo,
                "close": cl,
                "volume": 1e6,
            }
        )
    # Append forward sessions after last signal day for scoring.
    last = days[-1][0]
    y, m, dd = int(last[:4]), int(last[5:7]), int(last[8:10])
    for i in range(1, 4):
        out.append(
            {
                "date": f"{y:04d}-{m:02d}-{dd + i:02d}",
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5 + i * 0.1,
                "volume": 1e6,
            }
        )
    return out


def test_buy_scored_skips_untouched_label():
    rows = [
        {"signal_type": "buy", "traded": 1, "outcome_label": "三日红", "outcome_day3_pct": 2},
        {"signal_type": "buy", "traded": 1, "outcome_label": "当日未触达"},
        {"signal_type": "buy", "traded": 1, "outcome_label": "待隔日"},
    ]
    scored = _buy_scored(rows)
    assert len(scored) == 1
    assert scored[0]["outcome_label"] == "三日红"


def test_remap_same_day_plan_untouched_vs_touched(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    # Day low 10.2 > plan 10.0 → 当日未触达; Day low 9.8 ≤ plan → scores.
    upsert_daily_bars(
        "600001",
        [
            {"date": "2026-11-02", "open": 10.3, "high": 10.8, "low": 10.2, "close": 10.6, "volume": 1},
            {"date": "2026-11-03", "open": 10.6, "high": 11.0, "low": 10.4, "close": 10.9, "volume": 1},
            {"date": "2026-11-04", "open": 10.9, "high": 11.2, "low": 10.5, "close": 11.0, "volume": 1},
            {"date": "2026-11-05", "open": 11.0, "high": 11.3, "low": 10.8, "close": 11.1, "volume": 1},
        ],
        skip_today=False,
    )
    upsert_daily_bars(
        "600002",
        [
            {"date": "2026-11-02", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1},
            {"date": "2026-11-03", "open": 10.2, "high": 10.8, "low": 10.0, "close": 10.6, "volume": 1},
            {"date": "2026-11-04", "open": 10.6, "high": 11.0, "low": 10.3, "close": 10.8, "volume": 1},
            {"date": "2026-11-05", "open": 10.8, "high": 11.2, "low": 10.5, "close": 11.0, "volume": 1},
        ],
        skip_today=False,
    )
    packed = closes_map_from_bar_cache(["600001", "600002"])
    assert set(packed) == {"600001", "600002"}
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "traded": 1,
            "outcome_label": "三日红",
            "outcome_day3_pct": 20.0,  # inflated classic
            "payload": {"plan_price": 10.0},
        },
        {
            "id": 2,
            "code": "600002",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "traded": 1,
            "outcome_label": "三日红",
            "outcome_day3_pct": 5.0,
            "payload": {"plan_price": 10.0},
        },
    ]
    remapped, n = _remap_rows_for_adapt_standard(rows, "same_day_plan", closes_map=packed)
    assert n == 2
    by_id = {r["id"]: r for r in remapped}
    assert by_id[1]["outcome_label"] == "当日未触达"
    assert by_id[1]["outcome_day3_pct"] is None
    assert by_id[2]["outcome_label"] in {"次日红", "三日红", "次日绿", "三日绿", "次日虚红"}
    # Untouched must not enter heat sample.
    assert len(_buy_scored(remapped)) == 1
    assert _buy_scored(remapped)[0]["id"] == 2


def test_build_adapt_bundle_prefers_same_day_plan(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    upsert_daily_bars(
        "600002",
        [
            {"date": "2026-11-02", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 1},
            {"date": "2026-11-03", "open": 10.2, "high": 10.8, "low": 10.0, "close": 10.6, "volume": 1},
            {"date": "2026-11-04", "open": 10.6, "high": 11.0, "low": 10.3, "close": 10.8, "volume": 1},
            {"date": "2026-11-05", "open": 10.8, "high": 11.2, "low": 10.5, "close": 11.0, "volume": 1},
        ],
    )
    packed = closes_map_from_bar_cache(["600002"])
    set_adapt_bars(packed)
    rows = [
        {
            "id": 2,
            "code": "600002",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "price": 10.0,
            "traded": 1,
            "outcome_label": "三日红",
            "outcome_day3_pct": 5.0,
            "payload": {"plan_price": 10.0},
            "signaled_at": "2026-11-02 09:40:00",
        }
    ]
    bundle = build_adapt_bundle(rows=rows, segment_key="open30", trade_date="2026-11-05")
    assert bundle["outcome_basis"] == "same_day_plan"
    assert "可触达" in bundle["outcome_basis_note"] or "plan" in bundle["outcome_basis_note"]


def test_dual_wins_has_touch_key(monkeypatch):
    # Empty rows → classic ok false, but touch key present.
    out = oos_buy_dual_wins([], closes=[("2026-11-01", 100.0), ("2026-11-02", 101.0),
                                        ("2026-11-03", 102.0), ("2026-11-04", 103.0)])
    assert "touch" in out
    assert "classic" in out
