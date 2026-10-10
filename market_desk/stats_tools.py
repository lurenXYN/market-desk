"""Small shared statistics for ledgers, audits and research scripts.

Desk outcomes are multi-day returns measured once per card, so two facts drive
every test here:

* cards on the same day share the market move — the trading day, not the card,
  is the independent unit;
* a 3-day outcome on day t overlaps the one on day t+1 by two sessions, so
  consecutive daily means are autocorrelated (lag-1 ≈ 0.5 on the 2026-09 buys).
  A plain t over daily means overstates significance by roughly 1.5×; use
  ``safe_t`` (min of plain and Newey–West) or ``day_block_bootstrap_ci`` instead.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Callable, Iterable

# Overlap of a 3-session outcome reaches two sessions ahead.
OVERLAP_LAG = 2


def mean(vals: Iterable[float]) -> float | None:
    """Arithmetic mean, or None for an empty input."""
    v = list(vals)
    return sum(v) / len(v) if v else None


def ranks(vals: list[float]) -> list[float]:
    """Zero-based ranks with ties sharing their average rank."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2.0
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float], min_n: int = 3) -> float | None:
    """Spearman rank correlation; None below ``min_n`` points or with no spread."""
    n = len(xs)
    if n < min_n or n != len(ys):
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def plain_t(vals: list[float]) -> float | None:
    """One-sample t of the mean assuming independent values (None below 3 or no spread)."""
    n = len(vals)
    if n < 3:
        return None
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / (n - 1)) ** 0.5
    return m / sd * n ** 0.5 if sd else None


def nw_t(series: list[float], lag: int = OVERLAP_LAG) -> float | None:
    """Newey–West (Bartlett kernel) t of the mean of a time-ordered series.

    Args:
        series: Values in chronological order (e.g. daily mean excess returns).
        lag: Autocovariance lags to include; 2 covers 3-session overlapping outcomes.

    Returns:
        The t statistic, or None below 3 values or with zero long-run variance.
    """
    n = len(series)
    if n < 3:
        return None
    m = sum(series) / n
    dev = [v - m for v in series]
    lrv = sum(d * d for d in dev) / n
    for k in range(1, min(int(lag), n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        cov = sum(dev[i] * dev[i - k] for i in range(k, n)) / n
        lrv += 2.0 * w * cov
    if lrv <= 0:
        return None
    lrv *= n / (n - 1.0)
    return m / (lrv / n) ** 0.5


def safe_t(series: list[float], lag: int = OVERLAP_LAG) -> float | None:
    """Conservative t of a time-ordered series: the smaller of plain and Newey–West.

    With 7–15 trading days the Newey–West variance is itself noisy and can fall
    below the plain one (negative sample autocovariance), which would inflate t.
    Taking the smaller magnitude keeps the overlap correction one-sided.

    Args:
        series: Values in chronological order (e.g. daily mean excess returns).
        lag: Autocovariance lags passed to ``nw_t``.

    Returns:
        The t statistic with the smaller absolute value, or None if neither exists.
    """
    ts = [t for t in (plain_t(series), nw_t(series, lag)) if t is not None]
    return min(ts, key=abs) if ts else None


def day_means(
    rows: Iterable[Any],
    day_of: Callable[[Any], str],
    value_of: Callable[[Any], float | None],
) -> list[tuple[str, float]]:
    """Per-day mean of ``value_of`` (rows with None skipped), sorted by day."""
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = value_of(r)
        if v is not None:
            acc[str(day_of(r))].append(float(v))
    return [(d, sum(v) / len(v)) for d, v in sorted(acc.items()) if v]


def day_block_bootstrap_ci(
    samples: list[tuple[str, float, float]],
    *,
    n: int = 1000,
    block: int = OVERLAP_LAG + 1,
    seed: int = 7,
    lo_q: float = 0.05,
    hi_q: float = 0.95,
) -> tuple[float | None, float | None]:
    """Moving-block bootstrap CI of a weighted mean, resampling trading days.

    Whole days are drawn in runs of ``block`` consecutive sessions, so both the
    shared same-day move and the overlap between neighbouring days stay inside
    each draw. Deterministic for a fixed seed.

    Args:
        samples: ``(day, value, weight)`` rows.
        n: Resample count.
        block: Consecutive trading days per block.
        seed: RNG seed so a panel does not flicker between refreshes.
        lo_q: Lower quantile.
        hi_q: Upper quantile.

    Returns:
        ``(low, high)`` rounded to 2 dp, or ``(None, None)`` below 3 samples or 2 days.
    """
    if len(samples) < 3 or n <= 0:
        return None, None
    by_day: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for day, v, w in samples:
        by_day[str(day)].append((float(v), float(w)))
    days = sorted(by_day)
    k = len(days)
    if k < 2:
        return None, None
    b = max(1, min(int(block), k))
    starts = k - b + 1
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(int(n)):
        picked: list[str] = []
        while len(picked) < k:
            s = rng.randrange(starts)
            picked.extend(days[s:s + b])
        tv = tw = 0.0
        for d in picked[:k]:
            for v, w in by_day[d]:
                tv += v * w
                tw += w
        if tw > 0:
            stats.append(tv / tw)
    if not stats:
        return None, None
    stats.sort()
    lo = stats[max(0, int(lo_q * len(stats)))]
    hi = stats[min(len(stats) - 1, int(hi_q * len(stats)))]
    return round(lo, 2), round(hi, 2)
