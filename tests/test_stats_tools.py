"""Shared statistics: ranks, Newey-West t, day means, day-block bootstrap."""

from __future__ import annotations

import pytest

from market_desk.stats_tools import (
    day_block_bootstrap_ci,
    day_means,
    nw_t,
    plain_t,
    ranks,
    safe_t,
    spearman,
)


def test_ranks_and_spearman_handle_ties():
    assert ranks([3.0, 1.0, 1.0, 2.0]) == [3.0, 0.5, 0.5, 2.0]
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [1, 2]) is None


def test_nw_t_shrinks_t_for_overlapping_series():
    # Smoothed (overlapping 3-day sums of) noise: positively autocorrelated.
    raw = [0.9, -0.4, 1.3, 0.2, -0.8, 1.1, 0.6, -0.1, 0.7, 1.0, -0.6, 0.4, 0.8, -0.2, 0.5, 0.9]
    over = [sum(raw[i:i + 3]) for i in range(len(raw) - 2)]
    assert nw_t(over) < plain_t(over)
    assert nw_t([1.0, 1.0]) is None
    flat = [0.5, -0.5] * 6
    assert nw_t(flat) is None or abs(nw_t(flat)) < 1e-9


def test_safe_t_never_exceeds_plain_t():
    # Alternating noise around a positive mean: negative autocovariance makes NW larger.
    alt = [1.5, -0.3, 1.4, -0.2, 1.6, -0.4, 1.3, -0.1, 1.5]
    assert abs(nw_t(alt)) > abs(plain_t(alt))
    assert safe_t(alt) == pytest.approx(plain_t(alt))
    raw = [0.9, -0.4, 1.3, 0.2, -0.8, 1.1, 0.6, -0.1, 0.7, 1.0, -0.6, 0.4, 0.8, -0.2, 0.5, 0.9]
    over = [sum(raw[i:i + 3]) for i in range(len(raw) - 2)]
    assert safe_t(over) == pytest.approx(nw_t(over))
    assert safe_t([1.0]) is None


def test_day_means_sorted_and_skip_none():
    rows = [("b", 1.0), ("a", 2.0), ("a", 4.0), ("b", None)]
    assert day_means(rows, lambda r: r[0], lambda r: r[1]) == [("a", 3.0), ("b", 1.0)]


def test_day_block_bootstrap_ci_covers_mean():
    rows = [(f"d{d:02d}", 1.0 + 0.1 * (d % 3), 1.0) for d in range(12)]
    lo, hi = day_block_bootstrap_ci(rows)
    assert 1.0 <= lo <= hi <= 1.2
