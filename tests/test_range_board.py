"""Range leaderboard: fold three sources, day3 sort, vs_chase, board roster."""

from __future__ import annotations

from market_desk.review.range_board import (
    _fold_by_code,
    _vs_chase_pct,
    build_board_roster,
    build_range_leaderboard,
)


def test_vs_chase_none_without_chase():
    assert _vs_chase_pct(chase=None, entry=10.0, day3_pct=-5.0, last=9.5) is None
    assert _vs_chase_pct(chase=0.0, entry=10.0, day3_pct=-5.0, last=9.5) is None


def test_vs_chase_from_day3_close():
    # entry 10, day3 -5% → ref 9.5; chase 11 → (9.5/11 - 1)*100
    out = _vs_chase_pct(chase=11.0, entry=10.0, day3_pct=-5.0, last=None)
    assert out == round((9.5 / 11.0 - 1.0) * 100.0, 2)


def test_vs_chase_falls_back_to_last():
    out = _vs_chase_pct(chase=10.0, entry=None, day3_pct=None, last=9.0)
    assert out == -10.0


def test_fold_keeps_worst_day3_and_sources():
    events = [
        {
            "trade_date": "2026-10-20",
            "code": "600001",
            "name": "甲",
            "source": "signal",
            "boards": ["固态电池"],
            "outcome_day3_pct": -2.0,
            "chase_price": 12.0,
            "vs_chase_pct": -8.0,
        },
        {
            "trade_date": "2026-10-21",
            "code": "600001",
            "name": "甲",
            "source": "ma_fan",
            "boards": ["新能源"],
            "outcome_day3_pct": -6.5,
            "chase_price": None,
            "vs_chase_pct": None,
        },
        {
            "trade_date": "2026-10-22",
            "code": "600001",
            "name": "甲",
            "source": "pot",
            "boards": [],
            "outcome_day3_pct": None,
            "chase_price": None,
            "vs_chase_pct": None,
        },
        {
            "trade_date": "2026-10-20",
            "code": "600002",
            "name": "乙",
            "source": "signal",
            "boards": ["半导体"],
            "outcome_day3_pct": 4.0,
            "chase_price": 20.0,
            "vs_chase_pct": 1.0,
        },
    ]
    worst = _fold_by_code(events, side="worst")
    by = {r["code"]: r for r in worst}
    assert by["600001"]["outcome_day3_pct"] == -6.5
    assert set(by["600001"]["sources"]) == {"signal", "ma_fan", "pot"}
    assert by["600001"]["appear_n"] == 3
    assert "固态电池" in by["600001"]["boards"]
    assert "新能源" in by["600001"]["boards"]

    best = _fold_by_code(events, side="best")
    by_b = {r["code"]: r for r in best}
    assert by_b["600001"]["outcome_day3_pct"] == -2.0  # only scored extremes for best among -2 and -6.5
    # best side keeps max day3 among scored → -2.0 > -6.5
    assert by_b["600002"]["outcome_day3_pct"] == 4.0


def test_leaderboard_sort_and_skip_unscored(monkeypatch):
    events = [
        {
            "trade_date": "2026-10-20",
            "code": "600010",
            "name": "惨",
            "source": "signal",
            "boards": ["A板"],
            "outcome_day3_pct": -9.0,
            "chase_price": 15.0,
            "vs_chase_pct": -12.0,
            "kind": "stock",
        },
        {
            "trade_date": "2026-10-21",
            "code": "600011",
            "name": "强",
            "source": "ma_fan",
            "boards": [],
            "outcome_day3_pct": 8.0,
            "chase_price": None,
            "vs_chase_pct": None,
            "kind": "stock",
        },
        {
            "trade_date": "2026-10-22",
            "code": "600012",
            "name": "无三日",
            "source": "pot",
            "boards": ["B板"],
            "outcome_day3_pct": None,
            "chase_price": None,
            "vs_chase_pct": None,
            "kind": "stock",
        },
    ]

    def _fake_collect(*, window="1w"):
        return {
            "window": window,
            "window_days": 5,
            "days": ["2026-10-22", "2026-10-21", "2026-10-20"],
            "events": events,
        }

    monkeypatch.setattr(
        "market_desk.review.range_board.collect_range_events",
        _fake_collect,
    )
    worst = build_range_leaderboard(window="1w", side="worst", limit=10)
    assert worst["ok"] is True
    assert worst["scored_n"] == 2
    assert worst["unscored_n"] == 1
    assert [r["code"] for r in worst["items"]] == ["600010", "600011"]
    assert worst["items"][0]["vs_chase_pct"] == -12.0
    assert worst["items"][1]["vs_chase_pct"] is None

    best = build_range_leaderboard(window="1w", side="best", limit=10)
    assert [r["code"] for r in best["items"]] == ["600011", "600010"]


def test_board_roster_matches_frozen_names(monkeypatch):
    events = [
        {
            "trade_date": "2026-10-20",
            "code": "600100",
            "name": "甲",
            "source": "signal",
            "boards": ["固态电池", "新能源"],
            "mainline": "主线甲",
            "outcome_day3_pct": 1.0,
        },
        {
            "trade_date": "2026-10-21",
            "code": "600101",
            "name": "乙",
            "source": "ma_fan",
            "boards": ["半导体"],
            "mainline": None,
            "outcome_day3_pct": -2.0,
        },
        {
            "trade_date": "2026-10-22",
            "code": "600102",
            "name": "丙",
            "source": "pot",
            "boards": ["固态电池概念"],
            "mainline": None,
            "outcome_day3_pct": None,
        },
    ]

    def _fake_collect(*, window="1w"):
        return {
            "window": "1m",
            "window_days": 20,
            "days": ["2026-10-22", "2026-10-21", "2026-10-20"],
            "events": events,
        }

    monkeypatch.setattr(
        "market_desk.review.range_board.collect_range_events",
        _fake_collect,
    )
    hit = build_board_roster("固态", days=20, limit=80)
    assert hit["ok"] is True
    codes = {r["code"] for r in hit["items"]}
    assert "600100" in codes
    assert "600102" in codes
    assert "600101" not in codes
    assert all("固态" in str(r.get("board_hit") or "") for r in hit["items"])
