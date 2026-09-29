"""纠结对比: rule points, history nudges, ranking and verdict."""

from __future__ import annotations

import market_desk.pick_score as pick_score
from market_desk.pick_score import build_history_stats, rank_picks, score_pick


def _hist(signal_type: str, hhmm: str, d3: float, *, day=10, trend_ok=False, trend_down=False, board=None):
    return {
        "signal_type": signal_type,
        "trade_date": f"2026-09-{day:02d}",
        "signaled_at": f"2026-09-{day:02d} {hhmm}:00",
        "outcome_day3_pct": d3,
        "ready": 0,
        "payload": {"trend_ok": trend_ok, "trend_down": trend_down, "board_match": board, "confirm_fail": []},
    }


def _cand(code: str, **extra):
    row = {
        "id": int(code[-2:]),
        "code": code,
        "name": f"票{code[-2:]}",
        "signal_type": "buy",
        "signaled_at": "2026-09-29 10:05:00",
        "kind": "stock",
        "price": 10.0,
        "live_last": 10.0,
        "price_flags": [],
        "payload": {"confirm_fail": []},
    }
    row.update(extra)
    return row


def test_history_stats_buckets_by_trend_and_time():
    rows = [_hist("buy", "10:00", 2.0, day=i + 1, trend_ok=True) for i in range(9)]
    rows += [_hist("buy", "13:10", -3.0, day=i + 1, trend_down=True) for i in range(9)]
    rows.append({"signal_type": "sell", "outcome_day3_pct": 5.0})
    st = build_history_stats(rows)
    assert st["n"] == 18 and st["base_win3"] == 50.0
    assert st["buckets"]["trend:up"] == {"n": 9, "days": 9, "win3": 100.0}
    assert st["buckets"]["pm:weak"] == {"n": 9, "days": 9, "win3": 0.0}


def test_history_win_rate_is_day_balanced():
    # One hot day with 8 winners must not outweigh four cold days.
    rows = [_hist("buy", "10:00", 2.0, day=1, trend_down=True) for _ in range(8)]
    rows += [_hist("buy", "10:00", -2.0, day=d, trend_down=True) for d in (2, 3, 4, 5)]
    st = build_history_stats(rows)
    assert st["buckets"]["trend:down"] == {"n": 12, "days": 5, "win3": 20.0}


def test_history_nudge_off_by_default_shows_note_only():
    rows = [_hist("buy", "10:00", 2.0, day=i + 1, trend_ok=True) for i in range(9)]
    rows += [_hist("buy", "10:00", -3.0, day=i + 1, trend_down=True) for i in range(9)]
    up = score_pick(_cand("600001", trend_ok=True, daily_trend="上升"), build_history_stats(rows))
    trend = next(f for f in up["factors"] if f["key"] == "trend")
    assert trend["points"] == 0.0 and "仅参考不计分" in trend["hist"]


def test_history_nudge_capped_and_ignored_when_thin(monkeypatch):
    monkeypatch.setattr(pick_score, "PICK_HIST_PP_TO_PTS", 0.4)
    rows = [_hist("buy", "10:00", 2.0, day=i + 1, trend_ok=True) for i in range(9)]
    rows += [_hist("buy", "10:00", -3.0, day=i + 1, trend_down=True) for i in range(9)]
    st = build_history_stats(rows)
    up = score_pick(_cand("600001", trend_ok=True, daily_trend="上升"), st)
    trend = next(f for f in up["factors"] if f["key"] == "trend")
    # No rule points for trend; only the capped +8 history nudge (100% vs 50% base).
    assert trend["points"] == 8.0
    few_days = [_hist("buy", "10:00", 2.0, day=1 + i % 3, trend_ok=True) for i in range(9)]
    few_days += [_hist("buy", "10:00", -3.0, day=1 + i % 3, trend_down=True) for i in range(9)]
    thin = build_history_stats(few_days)
    up_thin = score_pick(_cand("600001", trend_ok=True, daily_trend="上升"), thin)
    trend_thin = next(f for f in up_thin["factors"] if f["key"] == "trend")
    assert trend_thin["points"] == 0.0 and "样本少不计" in trend_thin["hist"]


def test_position_factor_prefers_band_over_chasing():
    st = build_history_stats([])
    band = score_pick(_cand("600001", price_flags=["in_band"], live_last=10.05), st)
    chase = score_pick(
        _cand("600002", price_flags=["above_plan"], live_last=10.25, above_plan_pct=2.5), st
    )
    assert band["score"] == 70
    pos = next(f for f in chase["factors"] if f["key"] == "pos")
    assert pos["points"] == -9.0 and chase["waiting"]
    assert band["score"] > chase["score"]


def test_manual_row_skips_plan_and_signal_factors():
    st = build_history_stats([])
    manual = {
        "code": "600003",
        "name": "手输票",
        "signal_type": "manual",
        "manual": True,
        "kind": "stock",
        "live_last": 12.0,
        "live_pct": 7.2,
        "zt_ytd": 0,
        "payload": {},
    }
    res = score_pick(manual, st)
    keys = {f["key"] for f in res["factors"]}
    assert keys == {"zt"}
    assert res["score"] == 60 - 4 and res["source_label"] == "手输"


def test_rank_verdict_names_top_close_gap_and_drops():
    good = _cand("600001", price_flags=["in_band"], trend_ok=True, daily_trend="上升", board_match=True)
    close = _cand("600002", price_flags=["in_band"], trend_ok=True, daily_trend="上升", board_match=True, ma_fan=True)
    bad = _cand("600003", price_flags=["chase_hit"], trend_down=True, daily_trend="下降", live_pct=9.8)
    res = rank_picks([good, bad, close], [])
    assert [it["code"] for it in res["items"]] == ["600002", "600001", "600003"]
    assert res["items"][2]["grade"] == "放弃"
    assert "优先 票02" in res["verdict"] and "只差 5 分" not in res["verdict"]
    assert "建议放弃：票03" in res["verdict"]
