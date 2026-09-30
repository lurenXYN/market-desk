"""Post-scan extras for stored MA-fan days: forward outcomes and the latest-day pick score.

Outcomes use closed daily bars only (entry = scan-day close), so a day's numbers
never move once final. The pick score reuses the review comparison scorer with
the factors that apply to a nightly screen (no signal / gate / ready context).
Observation layer only; nothing here gates a buy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

import httpx

from market_desk.config import CV_CHIP_WINDOW, CV_IVOL_INDEX
from market_desk.filters import normalize_code

log = logging.getLogger("market_desk.ma_fan")

MA_FAN_EXTRAS_DAYS = 20
MA_FAN_EXTRAS_RETRY_S = 900.0
MA_FAN_EXTRAS_BARS = CV_CHIP_WINDOW + 40
MA_FAN_EXTRAS_CONCURRENCY = 3
# Bars dated today count as closed only after this time (HH, MM).
MA_FAN_CLOSE_FINAL = (15, 5)
MA_FAN_FWD_DAYS = (1, 3)

_LAST_RUN = 0.0
_RUNNING = False


def closed_through(now: datetime | None = None) -> str:
    """Return the latest date whose daily bar is final (today after the close)."""
    t = now or datetime.now()
    today = t.strftime("%Y-%m-%d")
    if (t.hour, t.minute) >= MA_FAN_CLOSE_FINAL:
        return today
    return _prev_calendar_day(today)


def _prev_calendar_day(day: str) -> str:
    """Return the calendar day before ``day`` (bars skip non-trading days anyway)."""
    from datetime import timedelta

    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def forward_returns(
    closes: list[tuple[str, float]], day: str, *, through: str
) -> dict[str, Any] | None:
    """Return ``{d1, d3, final}`` in percent from the close on ``day``.

    Only bars dated on/before ``through`` are used. Returns None when ``day`` is
    not in the series (suspended or data gap).
    """
    usable = [(d, c) for d, c in closes if d <= through]
    idx = next((i for i, (d, _) in enumerate(usable) if d == day), None)
    if idx is None or usable[idx][1] <= 0:
        return None
    base = usable[idx][1]
    out: dict[str, Any] = {}
    for n in MA_FAN_FWD_DAYS:
        if idx + n < len(usable):
            out[f"d{n}"] = round((usable[idx + n][1] / base - 1.0) * 100.0, 2)
    out["final"] = f"d{max(MA_FAN_FWD_DAYS)}" in out
    return out


def pick_for_hit(
    hit: dict[str, Any],
    bars: list[dict[str, Any]],
    mkt: dict[str, float] | None,
) -> dict[str, Any] | None:
    """Score one hit with the comparison scorer; ``bars`` end on the scan day.

    Uses: near-limit-up, YTD limit-ups, holder change, the MA-fan bonus and
    chip / volume / idiosyncratic-volatility context.
    """
    from market_desk.chip_volume import build_cv
    from market_desk.pick_score import build_history_stats, score_pick

    close = hit.get("close")
    cv = build_cv(bars, float(close), mkt) if bars and close else None
    row = {
        "code": hit.get("code"),
        "name": hit.get("name"),
        "kind": "stock",
        "live_pct": hit.get("pct"),
        "zt_ytd": hit.get("zt_ytd"),
        "holder_chg_pct": hit.get("holder_chg_pct"),
        "ma_fan": True,
        "cv": cv,
    }
    from market_desk.config import PICK_BASE_SCORE

    res = score_pick(row, build_history_stats([]))
    return {
        "base": float(PICK_BASE_SCORE),
        "score": res["score"],
        "grade": res["grade"],
        "factors": res["factors"],
        "as_of": bars[-1].get("date") if bars else None,
    }


async def _fetch_series(
    codes: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, float]]]:
    """Fetch turnover bars for ``codes`` plus the ivol index closes."""
    from market_desk.chip_volume import fetch_bars_with_turnover, fetch_index_closes

    bars: dict[str, list[dict[str, Any]]] = {}
    sem = asyncio.Semaphore(MA_FAN_EXTRAS_CONCURRENCY)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), trust_env=False) as client:

        async def one(code: str) -> None:
            async with sem:
                got = await fetch_bars_with_turnover(client, code, limit=MA_FAN_EXTRAS_BARS)
                await asyncio.sleep(0.1)
            if got:
                bars[code] = got

        idx_task = asyncio.create_task(fetch_index_closes(client, CV_IVOL_INDEX, MA_FAN_EXTRAS_BARS))
        await asyncio.gather(*(one(c) for c in codes))
        idx = await idx_task
    return bars, idx


def _merge_extras(fresh: dict[str, Any], computed: dict[str, Any]) -> dict[str, Any]:
    """Copy fwd / pick / index_fwd from ``computed`` onto a freshly loaded payload.

    Keeps concurrent edits (e.g. a meta backfill saved meanwhile) instead of
    overwriting the whole day with the copy loaded at the start of the run.
    """
    by_code = {normalize_code(it.get("code")): it for it in computed.get("items") or []}
    out = dict(fresh)
    items = []
    for raw in fresh.get("items") or []:
        it = dict(raw)
        src = by_code.get(normalize_code(it.get("code"))) or {}
        for key in ("fwd", "pick"):
            if src.get(key) is not None:
                it[key] = src[key]
        items.append(it)
    out["items"] = items
    if computed.get("index_fwd") is not None:
        out["index_fwd"] = computed["index_fwd"]
    return out


def _needs_fwd(item: dict[str, Any]) -> bool:
    fwd = item.get("fwd")
    return not (isinstance(fwd, dict) and fwd.get("final"))


async def refresh_ma_fan_extras(*, force: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Fill forward outcomes on recent stored days and pick scores on the latest day.

    Throttled to one run per ``MA_FAN_EXTRAS_RETRY_S`` unless ``force``; skips a
    day while its scan is running. Returns counts of what changed.
    """
    global _LAST_RUN, _RUNNING
    from market_desk.chip_volume import index_pct_map
    from market_desk.db import list_ma_fan_dates, load_ma_fan_day, save_ma_fan_day
    from market_desk.ma_fan.job import scan_progress

    mono = time.monotonic()
    if _RUNNING or (not force and mono - _LAST_RUN < MA_FAN_EXTRAS_RETRY_S):
        return {"ok": False, "skipped": True}
    _RUNNING = True
    _LAST_RUN = mono
    try:
        through = closed_through(now)
        prog = scan_progress() or {}
        busy_day = str(prog.get("trade_date") or "") if prog.get("running") else ""
        dates = [d for d in list_ma_fan_dates(limit=MA_FAN_EXTRAS_DAYS) if d != busy_day]
        if not dates:
            return {"ok": True, "days": 0}
        latest = max(dates)
        bodies = {d: load_ma_fan_day(d) or {} for d in dates}
        codes: set[str] = set()
        for d, body in bodies.items():
            for it in body.get("items") or []:
                code = normalize_code(it.get("code"))
                if not code:
                    continue
                if (d < through and _needs_fwd(it)) or (d == latest and not it.get("pick")):
                    codes.add(code)
        need_index = any(d < through and not (b.get("index_fwd") or {}).get("final") for d, b in bodies.items())
        if not codes and not need_index:
            return {"ok": True, "days": len(dates), "codes": 0}
        bars, idx = await _fetch_series(sorted(codes))
        fwd_n = pick_n = 0
        for d, body in bodies.items():
            changed = False
            if d < through and idx and not (body.get("index_fwd") or {}).get("final"):
                got = forward_returns(idx, d, through=through)
                if got:
                    body["index_fwd"] = {**got, "sym": CV_IVOL_INDEX}
                    changed = True
            mkt = index_pct_map([x for x in idx if x[0] <= d]) if d == latest else None
            for it in body.get("items") or []:
                code = normalize_code(it.get("code"))
                series = bars.get(code) or []
                if not series:
                    continue
                if d < through and _needs_fwd(it):
                    got = forward_returns([(b["date"], b["close"]) for b in series], d, through=through)
                    if got and got != it.get("fwd"):
                        it["fwd"] = got
                        fwd_n += 1
                        changed = True
                if d == latest and not it.get("pick"):
                    upto = [b for b in series if b["date"] <= d]
                    if upto and upto[-1]["date"] == d:
                        it["pick"] = pick_for_hit(it, upto, mkt)
                        pick_n += 1
                        changed = True
            if changed:
                save_ma_fan_day(d, _merge_extras(load_ma_fan_day(d) or body, body))
        log.info("ma_fan extras through=%s codes=%s fwd=%s pick=%s", through, len(codes), fwd_n, pick_n)
        return {"ok": True, "days": len(dates), "codes": len(codes), "fwd": fwd_n, "pick": pick_n}
    except Exception:
        log.exception("ma_fan extras failed")
        return {"ok": False}
    finally:
        _RUNNING = False


def build_outcome_summary(
    day: str, body: dict[str, Any] | None, recent: dict[str, dict[str, Any]] | None
) -> dict[str, Any]:
    """Summarize forward outcomes for the viewed day and the recent stored days.

    Win = close up after N days from the scan-day close. ``recent`` maps
    date → payload; only final outcomes are pooled there.
    """

    def agg(vals: list[float]) -> dict[str, Any]:
        if not vals:
            return {"n": 0, "win": None, "avg": None}
        return {
            "n": len(vals),
            "win": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1),
            "avg": round(sum(vals) / len(vals), 2),
        }

    def collect(items: list[dict[str, Any]], key: str, *, final_only: bool) -> list[float]:
        out = []
        for it in items:
            fwd = it.get("fwd") if isinstance(it.get("fwd"), dict) else {}
            if final_only and not fwd.get("final"):
                continue
            if fwd.get(key) is not None:
                out.append(float(fwd[key]))
        return out

    items = list((body or {}).get("items") or [])
    idx = (body or {}).get("index_fwd") or {}
    out: dict[str, Any] = {
        "day": {
            "d1": agg(collect(items, "d1", final_only=False)),
            "d3": agg(collect(items, "d3", final_only=False)),
            "index_d1": idx.get("d1"),
            "index_d3": idx.get("d3"),
        }
    }
    pool1: list[float] = []
    pool3: list[float] = []
    ex3: list[float] = []
    days = 0
    by_grade: dict[str, list[float]] = {}
    for d, b in sorted((recent or {}).items()):
        its = list((b or {}).get("items") or [])
        v3 = collect(its, "d3", final_only=True)
        if not v3:
            continue
        days += 1
        pool1 += collect(its, "d1", final_only=True)
        pool3 += v3
        i3 = ((b or {}).get("index_fwd") or {}).get("d3")
        if i3 is not None:
            ex3 += [v - float(i3) for v in v3]
        for it in its:
            fwd = it.get("fwd") if isinstance(it.get("fwd"), dict) else {}
            grade = str((it.get("pick") or {}).get("grade") or "")
            if grade and fwd.get("final") and fwd.get("d3") is not None:
                by_grade.setdefault(grade, []).append(float(fwd["d3"]))
    out["recent"] = {
        "days": days,
        "d1": agg(pool1),
        "d3": agg(pool3),
        "excess_d3": round(sum(ex3) / len(ex3), 2) if ex3 else None,
        "by_grade": {g: agg(v) for g, v in by_grade.items()},
    }
    return out
