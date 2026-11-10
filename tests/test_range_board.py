"""Range leaderboard: fold three sources, day3 sort, vs_chase, board roster."""

from __future__ import annotations

from market_desk.review.range_board import (
    _days_ago,
    _fold_by_code,
    _seen_to_scan_row,
    _vs_chase_now_pct,
    _vs_chase_pct,
    build_range_board_agg,
    build_range_leaderboard,
)


def test_scan_row_has_price_and_days_ago():
    row = _seen_to_scan_row(
        {
            "source": "ma_fan",
            "trade_date": "2026-10-20",
            "price": 12.3,
            "pct": 1.5,
            "tag": "初",
            "boards": ["固态电池"],
            "d3": -2.0,
        }
    )
    assert row["close"] == 12.3
    assert row["days_ago"] is not None and row["days_ago"] >= 0
    assert row["d3"] == -2.0
    assert row["board_text"] == "固态电池"
    assert _days_ago("bad") is None


def test_vs_chase_none_without_chase():
    assert _vs_chase_pct(chase=None, entry=10.0, day3_pct=-5.0, last=9.5) is None
    assert _vs_chase_pct(chase=0.0, entry=10.0, day3_pct=-5.0, last=9.5) is None
    assert _vs_chase_now_pct(chase=None, live_last=73.87) is None


def test_vs_chase_from_day3_close():
    # entry 10, day3 -5% → ref 9.5; chase 11 → (9.5/11 - 1)*100
    out = _vs_chase_pct(chase=11.0, entry=10.0, day3_pct=-5.0, last=None)
    assert out == round((9.5 / 11.0 - 1.0) * 100.0, 2)


def test_vs_chase_falls_back_to_last():
    out = _vs_chase_pct(chase=10.0, entry=None, day3_pct=None, last=9.0)
    assert out == -10.0


def test_vs_chase_now_matches_review_deviation():
    # live 73.87 / chase 103 → same as review chase_dev_pct ≈ -28.28
    assert _vs_chase_now_pct(chase=103.0, live_last=73.87) == round(
        (73.87 / 103.0 - 1.0) * 100.0, 2
    )


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


def test_board_agg_groups_by_frozen_tag(monkeypatch):
    events = [
        {
            "trade_date": "2026-11-04",
            "code": "600010",
            "name": "甲",
            "source": "signal",
            "boards": ["半导体", "国产芯片"],
            "outcome_day3_pct": 10.0,
        },
        {
            "trade_date": "2026-11-04",
            "code": "600011",
            "name": "乙",
            "source": "ma_fan",
            "boards": ["半导体"],
            "outcome_day3_pct": 2.0,
        },
        {
            "trade_date": "2026-11-04",
            "code": "600012",
            "name": "丙",
            "source": "signal",
            "boards": ["白酒"],
            "outcome_day3_pct": -5.0,
        },
    ]

    def _fake_collect(*, window="1w"):
        return {
            "window": window,
            "window_days": 5,
            "days": ["2026-11-04"],
            "events": events,
        }

    monkeypatch.setattr(
        "market_desk.review.range_board.collect_range_events",
        _fake_collect,
    )
    best = build_range_board_agg(window="1w", side="best", limit=20, min_n=2)
    assert best["ok"] is True
    boards = [r["board"] for r in best["items"]]
    assert "半导体" in boards
    # 白酒 / 国产芯片 only 1 scored code each → dropped by min_n=2
    assert "白酒" not in boards
    assert "国产芯片" not in boards
    semi = next(r for r in best["items"] if r["board"] == "半导体")
    assert semi["n"] == 2
    # 甲 carries two tags → weight 0.5 in 半导体; 乙 weight 1.0.
    assert semi["avg_raw"] == 6.0
    assert semi["avg_day3"] == 4.67
    assert semi["n_eff"] == 1.5
    assert semi["best_code"] == "600010"

    thin = build_range_board_agg(window="1w", side="worst", limit=20, min_n=1)
    names = {r["board"] for r in thin["items"]}
    assert "白酒" in names
    assert "国产芯片" in names


def _kind_events():
    return [
        {"trade_date": "2026-11-04", "code": "600010", "name": "甲", "source": "signal",
         "boards": ["半导体", "国产芯片", "AI概念"], "outcome_day3_pct": 9.0},
        {"trade_date": "2026-11-04", "code": "600011", "name": "乙", "source": "signal",
         "boards": ["半导体", "AI概念"], "outcome_day3_pct": 3.0},
        {"trade_date": "2026-11-04", "code": "600012", "name": "丙", "source": "signal",
         "boards": ["AI概念", "国产芯片"], "outcome_day3_pct": -6.0},
        {"trade_date": "2026-11-04", "code": "600013", "name": "丁", "source": "signal",
         "boards": ["神秘板"], "outcome_day3_pct": 1.0},
    ]


def _patch_kind(monkeypatch):
    kinds = {"半导体": "industry", "国产芯片": "concept", "AI概念": "concept"}
    monkeypatch.setattr(
        "market_desk.review.range_board.collect_range_events",
        lambda *, window="1w": {
            "window": window, "window_days": 5,
            "days": ["2026-11-04"], "events": _kind_events(),
        },
    )
    monkeypatch.setattr(
        "market_desk.review.range_board.classify_board_kind",
        lambda name: kinds.get(str(name), "other"),
    )


def test_board_agg_kind_industry_filters_and_no_dilution(monkeypatch):
    _patch_kind(monkeypatch)
    out = build_range_board_agg(window="1w", side="best", kind="industry", mainline="半导体设备")
    assert out["kind"] == "industry"
    assert [r["board"] for r in out["items"]] == ["半导体"]
    semi = out["items"][0]
    # Industry scope: each ticker has only one industry tag → full weight.
    assert semi["kind_zh"] == "行业"
    assert semi["n"] == 2 and semi["n_eff"] == 2.0
    assert semi["avg_day3"] == semi["avg_raw"] == 6.0
    assert semi["is_mainline"] is True
    # 丙 / 丁 have no industry tag.
    assert out["no_tag_n"] == 2


def test_board_agg_kind_concept_weights_by_tag_count(monkeypatch):
    _patch_kind(monkeypatch)
    out = build_range_board_agg(window="1w", side="best", kind="concept", min_n=2)
    by = {r["board"]: r for r in out["items"]}
    assert set(by) == {"国产芯片", "AI概念"}
    ai = by["AI概念"]
    assert ai["n"] == 3
    # 甲: 2 concept tags → 0.5, 乙: 1 → 1.0, 丙: 2 → 0.5
    assert ai["n_eff"] == 2.0
    assert ai["avg_day3"] == round((9 * 0.5 + 3 * 1.0 + (-6) * 0.5) / 2.0, 2)
    assert ai["avg_raw"] == 2.0
    assert out["min_n"] == 2
    # default concept threshold is stricter than industry
    dflt = build_range_board_agg(window="1w", side="best", kind="concept")
    assert dflt["min_n"] == 3
    assert [r["board"] for r in dflt["items"]] == ["AI概念"]


def test_board_agg_kind_all_keeps_other_bucket(monkeypatch):
    _patch_kind(monkeypatch)
    out = build_range_board_agg(window="1w", side="best", kind="all", min_n=1)
    by = {r["board"]: r for r in out["items"]}
    assert by["神秘板"]["kind"] == "other"
    assert by["半导体"]["kind"] == "industry"


def test_board_agg_invalid_kind_falls_back_to_all(monkeypatch):
    _patch_kind(monkeypatch)
    assert build_range_board_agg(kind="weird")["kind"] == "all"


def test_classify_board_kind_seed_and_suffix(monkeypatch):
    import market_desk.review.range_board as rb

    monkeypatch.setattr(rb, "_KIND_MAP", {"半导体": "industry", "机器人": "concept"})
    import time

    monkeypatch.setattr(rb, "_KIND_MAP_AT", time.monotonic())
    assert rb.classify_board_kind("半导体") == "industry"
    assert rb.classify_board_kind("机器人概念") == "concept"
    assert rb.classify_board_kind("某某概念") == "concept"
    assert rb.classify_board_kind("某某行业") == "industry"
    assert rb.classify_board_kind("神秘板") == "other"
    assert rb.classify_board_kind("（无板名）") == "other"
    assert rb.classify_board_kind("") == "other"

