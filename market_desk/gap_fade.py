"""Detect repeated gap-and-fade names and maintain a stock blacklist."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_desk import config as cfg
from market_desk.db import (
    add_stock_blacklist,
    bump_blacklist_clean_streak,
    count_gap_fade_strikes,
    delete_stock_blacklist,
    load_stock_blacklist,
    upsert_gap_fade_strike,
)
from market_desk.filters import is_main_board, normalize_code


def classify_gap_fade(quote: dict[str, Any] | None) -> dict[str, Any] | None:
    """Judge whether a quote looks like a high-open then fade day.

    Uses last vs previous close for the open gap, and last vs open for the fade.
    """
    q = quote or {}
    code = normalize_code(q.get("code"))
    if not code or not is_main_board(code):
        return None
    try:
        last = float(q.get("price"))
        open_px = float(q.get("open"))
        pct = float(q.get("pct"))
    except (TypeError, ValueError):
        return None
    if last <= 0 or open_px <= 0:
        return None
    # Recover previous close from last and day pct.
    prev = last / (1.0 + pct / 100.0)
    if prev <= 0:
        return None
    open_gap = (open_px / prev - 1.0) * 100.0
    from_open = (last / open_px - 1.0) * 100.0
    open_need = float(getattr(cfg, "GAP_FADE_OPEN_PCT", 2.5))
    drop_need = float(getattr(cfg, "GAP_FADE_DROP_PCT", 1.5))
    flagged = open_gap >= open_need and from_open <= -drop_need
    return {
        "code": code,
        "name": q.get("name") or "",
        "open_gap_pct": round(open_gap, 2),
        "from_open_pct": round(from_open, 2),
        "flagged": flagged,
    }


def sync_gap_fade_blacklist(
    quotes: list[dict[str, Any]] | None,
    *,
    trade_date: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scan quotes, record strikes, auto-block chronic names, and auto-release.

    Only scores after GAP_FADE_MIN_HHMM to reduce early-session noise.
    Manual blacklist rows are never auto-deleted by clean streak; only auto rows are.
    """
    now = now or datetime.now()
    day = str(trade_date or now.strftime("%Y-%m-%d"))[:10]
    hhmm = now.hour * 100 + now.minute
    min_hhmm = int(getattr(cfg, "GAP_FADE_MIN_HHMM", 1000))
    window = int(getattr(cfg, "GAP_FADE_STRIKE_WINDOW", 10))
    need = int(getattr(cfg, "GAP_FADE_STRIKE_NEED", 3))
    clean_need = int(getattr(cfg, "GAP_FADE_CLEAN_DAYS", 3))
    since = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=window * 2)).strftime("%Y-%m-%d")

    scanned = 0
    flagged_n = 0
    auto_added: list[str] = []
    auto_removed: list[str] = []
    seen: set[str] = set()

    if hhmm >= min_hhmm:
        for raw in quotes or []:
            verdict = classify_gap_fade(raw)
            if not verdict:
                continue
            code = verdict["code"]
            scanned += 1
            seen.add(code)
            if verdict["flagged"]:
                flagged_n += 1
                upsert_gap_fade_strike(
                    code,
                    day,
                    name=str(verdict.get("name") or ""),
                    open_gap_pct=verdict.get("open_gap_pct"),
                    from_open_pct=verdict.get("from_open_pct"),
                    flagged=True,
                )
                strikes = count_gap_fade_strikes(code, since_date=since)
                # Prefer exact window by reading recent flagged count; approximate with since.
                if strikes >= need:
                    add_stock_blacklist(
                        code,
                        str(verdict.get("name") or ""),
                        reason=f"近{window}日高开低走约{strikes}次",
                        source="auto",
                        strike_n=strikes,
                    )
                    auto_added.append(code)

    # Clean-streak / release for existing blacklist.
    for row in load_stock_blacklist():
        code = normalize_code(row.get("code"))
        if not code or code not in seen:
            continue
        # Re-evaluate today's shape for streak.
        q = next((x for x in (quotes or []) if normalize_code(x.get("code")) == code), None)
        verdict = classify_gap_fade(q) if q else None
        normal = bool(verdict) and not bool(verdict.get("flagged"))
        if hhmm < min_hhmm:
            continue
        if not normal and verdict and verdict.get("flagged"):
            updated = bump_blacklist_clean_streak(code, normal=False)
            # Keep auto reason fresh while still fading.
            if updated and str(row.get("source") or "") == "auto":
                strikes = count_gap_fade_strikes(code, since_date=since)
                add_stock_blacklist(
                    code,
                    str(row.get("name") or verdict.get("name") or ""),
                    reason=f"近{window}日高开低走约{strikes}次",
                    source="auto",
                    strike_n=strikes,
                    note=str(row.get("note") or ""),
                )
            continue
        updated = bump_blacklist_clean_streak(code, normal=True)
        if not updated:
            continue
        # Only auto-sourced rows auto-release; manual stays until user removes.
        if str(updated.get("source") or "") != "auto":
            continue
        if int(updated.get("clean_streak") or 0) >= clean_need:
            if delete_stock_blacklist(code):
                auto_removed.append(code)

    rows = load_stock_blacklist()
    return {
        "ok": True,
        "trade_date": day,
        "scanned": scanned,
        "flagged_today": flagged_n,
        "auto_added": auto_added,
        "auto_removed": auto_removed,
        "items": rows,
        "codes": [str(r.get("code") or "").zfill(6) for r in rows if r.get("code")],
    }
