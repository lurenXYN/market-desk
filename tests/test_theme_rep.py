"""Theme reputation settle / fade / thin-feed helpers."""

from __future__ import annotations

from market_desk.config import (
    THEME_REP_FORMULA_VERSION,
    THEME_REP_MIN_SAMPLES,
    THEME_REP_SETTLE_MAX,
    THEME_REP_THIN_FEED_MULT,
    THEME_REP_THIN_N,
)
from market_desk.theme_memory import (
    attach_board_affinity,
    classify_theme_next_day,
    compute_rep_adj,
)


def test_formula_version_bumped() -> None:
    assert int(THEME_REP_FORMULA_VERSION) >= 3
    assert int(THEME_REP_MIN_SAMPLES) >= 3
    assert int(THEME_REP_SETTLE_MAX) >= 10
    assert float(THEME_REP_THIN_FEED_MULT) < 0.5


def test_fade_requires_collapse_with_tuichao() -> None:
    prior = {"zt_n": 6, "pct": 4.0}
    # 退潮 but still hot → unclear (stricter fade)
    nxt_hot = {"zt_n": 4, "pct": 2.0, "status": "退潮"}
    assert classify_theme_next_day(prior, nxt_hot) == "unclear"
    # 退潮 + collapsed → fade
    nxt_cold = {"zt_n": 1, "pct": 0.1, "status": "退潮"}
    assert classify_theme_next_day(prior, nxt_cold) == "fade"


def test_missing_board_fade_only_strong() -> None:
    weak = {"zt_n": 3, "pct": 2.0}
    assert classify_theme_next_day(weak, None, was_mainline=False) == "unclear"
    assert classify_theme_next_day(weak, None, was_mainline=True) == "fade"
    strong = {"zt_n": 6, "pct": 3.0}
    assert classify_theme_next_day(strong, None, was_mainline=False) == "fade"


def test_thin_sample_shrinks_mainline_feed() -> None:
    rep = {
        "测试题材": {
            "score_adj": -6.0,
            "auto_adj": -6.0,
            "manual_adj": 0.0,
            "trade_adj": 0.0,
            "fade_n": 1,
            "persist_n": 0,
            "label": "观察中",
        }
    }
    boards = [{"name": "测试题材", "bk": "BK1", "pool": []}]
    attach_board_affinity(boards, rep)
    b = boards[0]
    assert b.get("rep_thin") is True
    assert abs(float(b["rep_adj"]) - (-6.0 * float(THEME_REP_THIN_FEED_MULT))) < 1e-6
    assert float(b["rep_adj_full"]) == -6.0


def test_compute_rep_adj_thin_confidence() -> None:
    # One-sided but thin → much smaller than full-sample extreme.
    thin = compute_rep_adj(1.0, 0.0, sample_n=1.0)
    fat = compute_rep_adj(5.0, 0.0, sample_n=5.0)
    assert abs(thin) < abs(fat)
