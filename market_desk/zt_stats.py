"""Year-to-date limit-up counts from daily bars (observe-only).

East Money has no dedicated 「年内涨停次数」 field on the desk's current APIs.
``zttj`` is a short-window n/m stat, not calendar-year. We approximate YTD
limit-ups by counting sessions whose daily pct meets the board threshold.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from market_desk.filters import is_limit_up, normalize_code


def count_limit_ups_ytd(
    dates: list[str],
    closes: list[float],
    *,
    name: str | None = None,
    year: int | None = None,
) -> int | None:
    """Count limit-up sessions from Jan 1 of ``year`` through the bar series.

    Uses close-to-close pct vs ``is_limit_up`` thresholds (≈9.85% / ST ≈4.85%).
    Returns None when the series is too short to judge.
    """
    if not dates or not closes or len(dates) != len(closes) or len(closes) < 2:
        return None
    y = int(year or date.today().year)
    prefix = f"{y}-"
    n = 0
    for i in range(1, len(closes)):
        d = str(dates[i] or "")
        if not d.startswith(prefix):
            continue
        try:
            prev = float(closes[i - 1])
            cur = float(closes[i])
        except (TypeError, ValueError):
            continue
        if prev <= 0:
            continue
        pct = (cur / prev - 1.0) * 100.0
        if is_limit_up(name, pct):
            n += 1
    return n


def count_limit_ups_ytd_from_bars(
    bars: list[dict[str, Any]] | None,
    *,
    name: str | None = None,
    year: int | None = None,
) -> int | None:
    """Count YTD limit-ups from OHLCV rows that may already carry ``pct``."""
    rows = list(bars or [])
    if len(rows) < 2:
        return None
    y = int(year or date.today().year)
    prefix = f"{y}-"
    n = 0
    prev_close: float | None = None
    for row in rows:
        d = str(row.get("date") or row.get("trade_date") or "")
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        pct = row.get("pct")
        try:
            pct_f = float(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct_f = None
        if pct_f is None and prev_close not in (None, 0):
            pct_f = (close / float(prev_close) - 1.0) * 100.0
        prev_close = close
        if not d.startswith(prefix) or pct_f is None:
            continue
        if is_limit_up(name, pct_f):
            n += 1
    return n


def enrich_signals_with_zt_ytd(
    rows: list[dict[str, Any]],
    zt_by_code: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Attach ``zt_ytd`` (calendar-year limit-up count) onto review rows."""
    by_code = zt_by_code or {}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = normalize_code(item.get("code"))
        kind = str(item.get("kind") or "")
        if kind == "etf" or not code:
            item["zt_ytd"] = None
            out.append(item)
            continue
        val = by_code.get(code)
        if isinstance(val, dict):
            item["zt_ytd"] = val.get("count")
            item["zt_ytd_year"] = val.get("year")
            item["zt_ytd_note"] = val.get("note")
        else:
            item["zt_ytd"] = val
        out.append(item)
    return out
