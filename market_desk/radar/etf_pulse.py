"""Broad-ETF liquidity pulse: same-minute volume spikes on core index ETFs."""

from __future__ import annotations

from statistics import median
from typing import Any


def _hhmm(point: dict[str, Any]) -> str:
    """Return ``HH:MM`` from a minute point's ``YYYY-MM-DD HH:MM`` time."""
    return str(point.get("time") or "")[11:16]


def minute_baseline(
    days: dict[str, list[dict[str, Any]]] | None,
    *,
    exclude_day: str,
) -> dict[str, float]:
    """Median per-minute volume across prior sessions keyed by ``HH:MM``.

    Args:
        days: ``fetch_minute_days`` output keyed by ``YYYY-MM-DD``.
        exclude_day: Today's date, left out of the baseline.

    Returns:
        ``{"HH:MM": median_volume}`` for minutes with at least two samples.
    """
    buckets: dict[str, list[float]] = {}
    for day, points in (days or {}).items():
        if str(day)[:10] == str(exclude_day)[:10]:
            continue
        for p in points or []:
            try:
                vol = float(p.get("volume") or 0.0)
            except (TypeError, ValueError):
                continue
            if vol > 0:
                buckets.setdefault(_hhmm(p), []).append(vol)
    return {k: float(median(v)) for k, v in buckets.items() if len(v) >= 2}


def scan_pulses(
    points: list[dict[str, Any]] | None,
    baseline: dict[str, float] | None,
    *,
    prev_close: float | None,
    index_weak_now: bool = False,
) -> list[dict[str, Any]]:
    """Find volume-spike lifts in today's minute series.

    A window of ``ETF_PULSE_WINDOW`` bars fires when its volume is at least
    ``ETF_PULSE_VOL_RATIO`` times the same-minute baseline and price lifts by
    ``ETF_PULSE_PX_MIN`` percent. It is a ``rescue`` pulse when the ETF had
    dipped ``ETF_PULSE_DIP_PCT`` below the previous close before the lift (or,
    for the latest window only, the index tape is weak right now); otherwise a
    ``surge``. Consecutive firing windows collapse into their first minute.
    The opening print (before 09:35) and the closing auction (14:57+) are skipped.

    Args:
        points: Today's minute points (``time`` / ``price`` / ``volume``).
        baseline: ``minute_baseline`` output.
        prev_close: Previous close for the dip test.
        index_weak_now: True when the index is weak at evaluation time.

    Returns:
        Hits with ``at`` / ``kind`` / ``ratio`` / ``px_move`` / ``dip``.
    """
    from market_desk.config import (
        ETF_PULSE_DIP_PCT,
        ETF_PULSE_PX_MIN,
        ETF_PULSE_VOL_RATIO,
        ETF_PULSE_WINDOW,
    )

    pts = [p for p in points or [] if p.get("price")]
    base = baseline or {}
    w = int(ETF_PULSE_WINDOW)
    if len(pts) <= w or not base:
        return []
    prices = [float(p["price"]) for p in pts]
    hits: list[dict[str, Any]] = []
    last_fire = -10
    running_low = min(prices[:1]) if prices else 0.0
    for i in range(w, len(pts)):
        running_low = min(running_low, prices[i - w])
        t = _hhmm(pts[i])
        if not ("09:35" <= t <= "14:56"):
            continue
        vol = sum(float(pts[j].get("volume") or 0.0) for j in range(i - w + 1, i + 1))
        ref = sum(base.get(_hhmm(pts[j]), 0.0) for j in range(i - w + 1, i + 1))
        if ref <= 0:
            continue
        ratio = vol / ref
        px_from = prices[i - w]
        px_move = (prices[i] - px_from) / px_from * 100.0 if px_from > 0 else 0.0
        if ratio < float(ETF_PULSE_VOL_RATIO) or px_move < float(ETF_PULSE_PX_MIN):
            continue
        if i - last_fire <= w:
            last_fire = i
            continue
        last_fire = i
        dip = None
        if prev_close and prev_close > 0:
            dip = (float(prev_close) - running_low) / float(prev_close) * 100.0
        weak = (dip is not None and dip >= float(ETF_PULSE_DIP_PCT)) or (
            index_weak_now and i == len(pts) - 1
        )
        hits.append(
            {
                "at": t,
                "kind": "rescue" if weak else "surge",
                "ratio": round(ratio, 1),
                "px_move": round(px_move, 2),
                "dip": None if dip is None else round(dip, 2),
                "price": prices[i],
            }
        )
    return hits


def market_pulse_event(
    hits_by_code: dict[str, list[dict[str, Any]]] | None,
    *,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Collapse per-ETF hits into the day's market-level rescue event.

    The event fires at the first minute where at least ``ETF_PULSE_MIN_COUNT``
    distinct ETFs logged a ``rescue`` hit within ``ETF_PULSE_LOOKBACK_MIN``.

    Args:
        hits_by_code: ``{code: scan_pulses(...)}``.
        names: Optional display names keyed by code.

    Returns:
        ``{"event": bool, "at", "codes", "label", "latest"}``.
    """
    from market_desk.config import ETF_PULSE_LOOKBACK_MIN, ETF_PULSE_MIN_COUNT

    def _mins(t: str) -> int:
        return int(t[:2]) * 60 + int(t[3:5])

    names = names or {}
    rescue = sorted(
        (h["at"], code)
        for code, hits in (hits_by_code or {}).items()
        for h in hits or []
        if h.get("kind") == "rescue"
    )
    latest = None
    flat = sorted(
        ((h["at"], code, h) for code, hits in (hits_by_code or {}).items() for h in hits or []),
        key=lambda x: x[0],
    )
    if flat:
        at, code, h = flat[-1]
        latest = {"at": at, "code": code, "name": names.get(code, code), **h}
    out: dict[str, Any] = {"event": False, "at": None, "codes": [], "label": "", "latest": latest}
    lookback = int(ETF_PULSE_LOOKBACK_MIN)
    for idx, (at, _code) in enumerate(rescue):
        window = {c for t, c in rescue[: idx + 1] if _mins(at) - _mins(t) <= lookback}
        if len(window) >= int(ETF_PULSE_MIN_COUNT):
            codes = sorted(window)
            out.update(
                event=True,
                at=at,
                codes=codes,
                label="宽基托底脉冲：" + "、".join(names.get(c, c) for c in codes),
            )
            break
    return out
