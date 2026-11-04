"""v1.10.57–58: adapt_effect strip, all-buy miss, pack cold rebuild, today cards."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import market_desk.db as desk_db
from market_desk.adapt_effect import (
    build_adapt_effect_panel,
    build_size_gate_scorecard,
    build_today_buy_cards,
)
from market_desk.agent_pack import build_adapt_live_snapshot
from market_desk.db.bars_cache import ensure_bars_cache_tables
from market_desk.review.digest import build_week_exec_board
from market_desk.review.hit_rates import build_miss_attribution, build_missed_buys
from market_desk.review.payload import enrich_signals_with_live_marks


def _init(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    with desk_db._connect() as conn:
        ensure_bars_cache_tables(conn)
        conn.commit()


def test_adapt_effect_no_bars_strips_classic_keeps_same_day_basis(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "skipped": 0,
            "price": 10.0,
            "outcome_label": "三日红",
            "outcome_day3_pct": 5.0,
            "payload": {"size_mult": 1.0},
        }
    ]
    panel = build_adapt_effect_panel(rows)
    assert panel["basis"] == "same_day_plan"
    assert "清空" in (panel.get("basis_note") or "") or "不回退" in (panel.get("basis_note") or "")
    # Scorecard must not count stripped classic labels.
    assert panel["scorecard"]["n"] == 0


def test_scorecard_skips_untouched_label():
    rows = [
        {
            "signal_type": "buy",
            "skipped": 0,
            "outcome_label": "当日未触达",
            "outcome_day3_pct": None,
            "payload": {"size_mult": 0.7},
        },
        {
            "signal_type": "buy",
            "skipped": 0,
            "outcome_label": "三日红",
            "outcome_day3_pct": 2.0,
            "payload": {"size_mult": 1.0},
        },
    ]
    card = build_size_gate_scorecard(rows)
    assert card["n"] == 1


def test_miss_boards_include_buy_side():
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy_side",
            "traded": 0,
            "skipped": 0,
            "price": 10.0,
            "price_flags": [],
            "payload": {"np": {"np": True, "day_low": 10.2, "day_close": 10.5, "plan": 10.0}},
        },
        {
            "id": 2,
            "code": "600002",
            "trade_date": "2026-11-02",
            "signal_type": "buy_dragon",
            "traded": 0,
            "skipped": 0,
            "price": 10.0,
            "price_flags": [],
            "payload": {"np": {"np": False, "day_low": 9.8, "day_close": 10.2, "plan": 10.0}},
        },
    ]
    missed = build_missed_buys(rows, trade_date="2026-11-02")
    assert [m["id"] for m in missed] == [1]
    attr = build_miss_attribution(rows, trade_date="2026-11-02")
    assert attr["counts"]["never_touched"] == 1
    assert attr["counts"]["touched_not_bought"] == 1


def test_miss_attr_folds_same_code_multi_desk():
    rows = [
        {
            "id": 1,
            "code": "002048",
            "name": "宁波华翔",
            "trade_date": "2026-11-04",
            "signal_type": "buy_side",
            "traded": 0,
            "skipped": 0,
            "price": 17.23,
            "live_last": 17.76,
            "dev_pct": 3.08,
            "price_mark": "高于计划价 +3.1%",
            "price_flags": ["in_band"],
            "payload": {},
            "signaled_at": "2026-11-04 10:05:00",
        },
        {
            "id": 2,
            "code": "002048",
            "name": "宁波华翔",
            "trade_date": "2026-11-04",
            "signal_type": "buy",
            "traded": 0,
            "skipped": 0,
            "price": 17.23,
            "live_last": 17.76,
            "dev_pct": 3.08,
            "price_mark": "高于计划价 +3.1%",
            "price_flags": ["in_band"],
            "payload": {},
            "signaled_at": "2026-11-04 09:40:00",
        },
        {
            "id": 3,
            "code": "600699",
            "name": "均胜电子",
            "trade_date": "2026-11-04",
            "signal_type": "buy_link",
            "traded": 0,
            "skipped": 0,
            "price": 10.0,
            "live_last": 10.5,
            "price_flags": ["near_wait"],
            "payload": {},
        },
    ]
    attr = build_miss_attribution(rows, trade_date="2026-11-04")
    assert attr["total"] == 2
    assert attr["counts"]["touched_not_bought"] == 2
    by_code = {m["code"]: m for m in attr["items"]}
    assert by_code["002048"]["id"] == 2  # mainline buy wins over side
    assert by_code["002048"]["channels_n"] == 2
    missed = build_missed_buys(
        [
            {
                **rows[0],
                "price_flags": ["miss_pullback"],
                "price_mark": "未回踩",
            },
            {
                **rows[1],
                "price_flags": ["miss_pullback"],
                "price_mark": "未回踩",
            },
        ],
        trade_date="2026-11-04",
    )
    assert len(missed) == 1
    assert missed[0]["code"] == "002048"
    assert missed[0]["channels_n"] == 2


def test_week_exec_half_window_counts_side_buy():
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy_side",
            "traded": 1,
            "ready": 1,
            "payload": {"ready_relaxed": True},
        }
    ]
    marked = enrich_signals_with_live_marks(rows, {}, today="2026-11-04")
    board = build_week_exec_board(marked, dates=["2026-11-02"], days=1)
    assert board["window_n"] == 1
    assert board["followed_n"] == 1


def test_agent_pack_cold_rebuild_same_day_basis(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    rows = [
        {
            "id": 1,
            "code": "600001",
            "trade_date": "2026-11-02",
            "signal_type": "buy",
            "skipped": 0,
            "traded": 1,
            "price": 10.0,
            "outcome_label": "三日红",
            "outcome_day3_pct": 5.0,
            "payload": {},
        }
    ]
    fake_engine = MagicMock()
    fake_engine.snapshot = {"verdict": {}}  # no warm adapt → cold rebuild
    with patch("market_desk.engine.engine", fake_engine):
        snap = build_adapt_live_snapshot(buy_rows=rows)
    assert snap["warm_source"] == "rebuilt"
    assert snap["outcome_basis"] == "same_day_plan"
    assert "same_day_plan" in (snap.get("outcome_basis_note") or "")
    assert "清空" in (snap.get("outcome_basis_note") or "") or "重算" in (
        snap.get("outcome_basis_note") or ""
    )


def test_today_buy_cards_merge_side_and_dragon():
    fake_engine = MagicMock()
    fake_engine.snapshot = {
        "verdict": {
            "action": "观察回踩",
            "mainline": {"name": "电子"},
            "recommend": {
                "items": [
                    {"code": "600001", "name": "主", "ready": True, "plan_price": 10},
                ]
            },
            "side_recommend": {
                "items": [
                    {"code": "600002", "name": "支", "ready": True, "plan_price": 11},
                ]
            },
            "dragon_recommend": {
                "items": [
                    {"code": "600003", "name": "龙", "probe_ok": True, "plan_price": 12},
                ]
            },
            "link_recommend": {"items": []},
            "watch_trial_recommend": {"items": []},
            "independent_recommend": {"items": []},
        }
    }
    with patch("market_desk.engine.engine", fake_engine):
        out = build_today_buy_cards(limit=15)
    codes = {it["code"] for it in out["items"]}
    assert codes == {"600001", "600002", "600003"}
    by = {it["code"]: it for it in out["items"]}
    assert by["600002"]["desk_source"] == "side"
    assert by["600003"]["desk_source"] == "dragon"
