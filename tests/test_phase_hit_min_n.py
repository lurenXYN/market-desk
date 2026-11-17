"""Phase hit-rate may only demote a buy card on a real sample (n >= 30)."""

from __future__ import annotations

from market_desk.verdict.gates import apply_review_bias


def _patch_hits(monkeypatch, *, scored_n: int, rate: float) -> None:
    monkeypatch.setattr("market_desk.db.load_signals", lambda limit=240: [])
    monkeypatch.setattr(
        "market_desk.review.build_phase_hit_rates",
        lambda rows: [{"phase": "发酵", "scored_n": scored_n, "hit_rate": rate}],
    )


def test_thin_sample_does_not_demote(monkeypatch) -> None:
    _patch_hits(monkeypatch, scored_n=11, rate=18.2)
    act, why, hint, blocked, notes = apply_review_bias(
        "可买入", "主线确认", "1成", phase="发酵"
    )
    assert act == "可买入"
    assert blocked is False
    assert "命中" not in hint


def test_real_sample_still_demotes(monkeypatch) -> None:
    _patch_hits(monkeypatch, scored_n=40, rate=18.2)
    act, why, hint, blocked, notes = apply_review_bias(
        "可买入", "主线确认", "1成", phase="发酵"
    )
    assert act == "观察回踩"
    assert "复盘命中" in notes[0]
    assert blocked is False


def test_real_sample_above_threshold_keeps_action(monkeypatch) -> None:
    _patch_hits(monkeypatch, scored_n=40, rate=52.0)
    act, _why, _hint, blocked, _notes = apply_review_bias(
        "可买入", "主线确认", "1成", phase="发酵"
    )
    assert act == "可买入"
    assert blocked is False