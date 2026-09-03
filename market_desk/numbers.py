"""Numeric helpers for East Money payloads."""

from __future__ import annotations


def num(value: object, default: float | None = None) -> float | None:
    """Parse a numeric field, treating dashes as missing."""
    if value is None or value == "-" or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def median(values: list[float]) -> float | None:
    """Return the median of a non-empty numeric list."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
