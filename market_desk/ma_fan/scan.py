"""Slice scheduling and the scan pipeline (universe → bars → score → merge → meta)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from market_desk.eastmoney import fetch_daily_bars
from market_desk.filters import is_chinext_or_star, is_main_board, is_st, normalize_code
from market_desk.ma_fan.context import (
    _buy_signal_codes_for_day,
    annotate_hits_with_desk_context,
)
from market_desk.ma_fan.job import (
    MA_FAN_BARS_LIMIT,
    MA_FAN_CONCURRENCY,
    MA_FAN_MAX_CONSECUTIVE_FAIL,
    MA_FAN_MIN_INTERVAL_S,
    MA_FAN_SCORE_BARS,
    _Pacer,
    _ScanStats,
    _score_cache_get,
    _score_cache_put,
    release_scan,
    scan_progress,
    try_claim_scan,
)
from market_desk.ma_fan.pattern import (
    MA_FAN_FORMULA_VERSION,
    amount_band_for_rank,
    score_pattern,
)
from market_desk.ma_fan.sources import (
    _board_ok,
    _wan_to_yi,
    enrich_hits_meta,
    load_universe,
)
from market_desk.zt_stats import count_limit_ups_ytd_from_bars

log = logging.getLogger("market_desk.ma_fan")


# Nightly staggered slices: (earliest minute-of-day, amount-rank offset, count, key).
# 18:00 → 0–400；20:00 → 400–800；22:00 → 800–1000（末段流动性更薄，只扫 200）.
MA_FAN_SCHEDULE: tuple[tuple[int, int, int, str], ...] = (
    (18 * 60, 0, 400, "0-400"),
    (20 * 60, 400, 400, "400-800"),
    (22 * 60, 800, 200, "800-1000"),
)


async def _score_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    row: dict[str, Any],
    boards: str,
    *,
    min_price: float = 0.0,
    prefer_main: bool = False,
    pacer: _Pacer | None = None,
    stats: _ScanStats | None = None,
) -> dict[str, Any] | None:
    """Fetch bars and score one quote row into a hit dict."""
    code = normalize_code(row.get("code")) or ""
    name = str(row.get("name") or "")
    if not code or not _board_ok(code, boards) or is_st(name):
        return None
    cached = _score_cache_get(code)
    if cached is not None:
        got, zt_ytd = cached
        if stats is not None:
            stats.record_fetch(True)
    else:
        async with sem:
            if stats is not None and stats.aborted:
                return None
            if pacer is not None:
                await pacer.wait()
            try:
                bars = await fetch_daily_bars(client, code, limit=MA_FAN_BARS_LIMIT)
            except Exception as exc:
                log.debug("ma_fan bars failed code=%s: %r", code, exc)
                bars = []
            if pacer is None:
                await asyncio.sleep(0.04)
        if stats is not None:
            stats.record_fetch(bool(bars))
        got = score_pattern((bars or [])[-MA_FAN_SCORE_BARS:])
        zt_ytd = (
            count_limit_ups_ytd_from_bars(bars, name=name, code=code, year=datetime.now().year)
            if got
            else None
        )
        if bars:
            _score_cache_put(code, got, zt_ytd)
    if not got:
        return None
    close = float(got["close"])
    if min_price > 0 and close < float(min_price):
        return None
    amt = row.get("amount")
    amt_yi = round(float(amt) / 1e8, 2) if amt not in (None, "") else None
    try:
        amount_rank = int(row.get("_rank") or 0) or None
    except (TypeError, ValueError):
        amount_rank = None
    band = amount_band_for_rank(amount_rank)
    tags = list(got.get("tags") or [])
    if band and band not in tags:
        tags.append(band)
    score_base = float(got["score"])
    if prefer_main and is_main_board(code):
        score_base += 3.0
        if "主板" not in tags:
            tags.append("主板")
    elif prefer_main and is_chinext_or_star(code):
        if "成长板" not in tags:
            tags.append("成长板")
    return {
        "code": code,
        "name": name,
        "score": score_base,
        "score_base": score_base,
        "close": close,
        "pct": None if got.get("pct") is None else float(got["pct"]),
        "amount_yi": amt_yi,
        "amount_rank": amount_rank,
        "amount_band": band,
        "sticky_end": str(got["sticky_end"]),
        "sticky_spread": float(got["sticky_spread"]),
        "sticky_amp": got.get("sticky_amp"),
        "fan_spread": float(got["fan_spread"]),
        "fan_ratio": float(got["fan_ratio"]),
        "vol_ratio": float(got["vol_ratio"]),
        "ma_order": str(got["ma_order"]),
        "note": " · ".join(tags) if tags else str(got["note"]),
        "ext_pct": got.get("ext_pct"),
        "stage": got.get("stage") or "",
        "tags": tags,
        "freshness": got.get("freshness") or "",
        "slopes": got.get("slopes"),
        "progressive": bool(got.get("progressive")),
        "ma60_ok": bool(got.get("ma60_ok", True)),
        "zt_ytd": zt_ytd,
        "mv_yi": _wan_to_yi(row.get("mktcap_wan")),
    }


def _merge_hits(
    prev_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    *,
    keep: int,
) -> list[dict[str, Any]]:
    """Merge hits by code, keeping the higher pattern score, then trim to ``keep``."""
    by_code: dict[str, dict[str, Any]] = {}
    for raw in list(prev_items or []) + list(new_items or []):
        item = dict(raw or {})
        code = normalize_code(item.get("code")) or ""
        if not code:
            continue
        try:
            base = float(item.get("score_base") or item.get("score") or 0)
        except (TypeError, ValueError):
            base = 0.0
        item["score_base"] = base
        old = by_code.get(code)
        old_base = float((old or {}).get("score_base") or (old or {}).get("score") or 0) if old else -1e9
        if old is None or base >= old_base:
            by_code[code] = item
    out = list(by_code.values())
    out.sort(key=lambda h: float(h.get("score_base") or h.get("score") or 0), reverse=True)
    return out[: max(10, keep)]


def slices_done_for_day(trade_date: str) -> set[str]:
    """Return slice keys already persisted for ``trade_date``."""
    from market_desk.db import load_ma_fan_day

    body = load_ma_fan_day(str(trade_date or "")[:10]) or {}
    return {str(x) for x in (body.get("slices_done") or []) if x}


def next_due_ma_fan_slice(
    *,
    trade_date: str,
    minutes: int,
) -> tuple[int, int, str] | None:
    """Return ``(offset, count, key)`` for the next due unfinished slice, or None."""
    done = slices_done_for_day(trade_date)
    for start_min, offset, count, key in MA_FAN_SCHEDULE:
        if int(minutes) < int(start_min):
            continue
        if key in done:
            continue
        return int(offset), int(count), str(key)
    return None


async def run_ma_fan_scan(
    *,
    trade_date: str,
    offset: int = 0,
    limit: int = 400,
    slice_key: str = "0-400",
    top: int = 80,
    min_amount_yi: float = 1.2,
    boards: str = "all",
    persist: bool = True,
    replace: bool = False,
    snapshot: dict[str, Any] | None = None,
    min_price: float = 0.0,
    prefer_main: bool = False,
    universe: list[dict[str, Any]] | None = None,
    stats: _ScanStats | None = None,
) -> dict[str, Any]:
    """Scan one amount-rank slice and merge into the day payload.

    ``offset``/``limit`` select the liquidity band (e.g. 400–800). Results merge
    into ``ma_fan_day`` unless ``replace=True`` (admin full rebuild of this slice
    still merges by code; pass replace to clear prior slices_done for a fresh day).
    Pass a preloaded ``universe`` to skip re-paging the amount ranking.

    Raises:
        RuntimeError: The ranking came back empty or the bar source kept
            failing; the stored day payload is left untouched.
    """
    day = str(trade_date or "")[:10]
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")
    off = max(0, int(offset))
    lim = max(20, min(int(limit or 400), 500))
    key = str(slice_key or f"{off}-{off + lim}")
    need = off + lim
    min_px = max(0.0, float(min_price or 0))
    prefer = bool(prefer_main)
    st = stats or _ScanStats(live=False)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False) as client:
        if universe is None:
            st.phase("universe")
            universe = await load_universe(
                client, boards=boards, need=need, min_amount_yi=min_amount_yi
            )
        if not universe:
            raise RuntimeError("成交额榜为空（新浪无响应或限流），未改动已有结果")
        pool = universe[off : off + lim]
        ranked: list[dict[str, Any]] = []
        for i, row in enumerate(pool):
            item = dict(row or {})
            item["_rank"] = off + i + 1
            ranked.append(item)
        st.add_total(len(ranked))
        st.phase("bars")
        sem = asyncio.Semaphore(MA_FAN_CONCURRENCY)
        pacer = _Pacer(MA_FAN_MIN_INTERVAL_S)

        async def _one(row: dict[str, Any]) -> dict[str, Any] | None:
            hit = await _score_one(
                client,
                sem,
                row,
                boards,
                min_price=min_px,
                prefer_main=prefer,
                pacer=pacer,
                stats=st,
            )
            if hit:
                st.record_hit()
            return hit

        raw = await asyncio.gather(*[_one(row) for row in ranked])
    if st.aborted:
        raise RuntimeError(
            f"日线源连续失败 {MA_FAN_MAX_CONSECUTIVE_FAIL} 次，疑似被限流，已中止（档 {key}）"
        )
    chunk = [h for h in raw if h]
    chunk.sort(key=lambda h: float(h.get("score_base") or h.get("score") or 0), reverse=True)

    from market_desk.db import load_ma_fan_day, save_ma_fan_day

    prev = {} if replace else (load_ma_fan_day(day) or {})
    prev_items = [] if replace else list(prev.get("items") or [])
    keep = max(20, min(int(top or 80), 150))
    merged = _merge_hits(prev_items, chunk, keep=keep)
    signal_codes = _buy_signal_codes_for_day(day)
    for h in merged:
        code = normalize_code(h.get("code")) or ""
        h["in_review"] = bool(code and code in signal_codes)
    merged = annotate_hits_with_desk_context(
        merged, trade_date=day, snapshot=snapshot
    )
    # Re-trim after theme / review score nudges.
    merged = merged[:keep]
    st.phase("meta")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False) as client:
        merged = await enrich_hits_meta(client, merged)

    done = set() if replace else {str(x) for x in (prev.get("slices_done") or [])}
    done.add(key)
    scanned_total = int(prev.get("scanned") or 0) + len(pool)
    if replace:
        scanned_total = len(pool)

    payload = {
        "ok": True,
        "trade_date": day,
        "scanned": scanned_total,
        "hit_n": len(merged),
        "boards": boards,
        "min_amount_yi": min_amount_yi,
        "min_price": min_px,
        "prefer_main": prefer,
        "formula_version": MA_FAN_FORMULA_VERSION,
        "items": merged,
        "slices_done": sorted(done, key=lambda s: (len(s), s)),
        "last_slice": key,
        "last_slice_scanned": len(pool),
        "last_slice_hits": len(chunk),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": (
            "观察层：粘连→渐进发散；18/20/22 分档扫成交额榜；"
            "标签含额档/主线同主题旁注；复盘仅打交集标不加分；不进 ready。"
        ),
    }
    if persist:
        save_ma_fan_day(day, payload)
        log.info(
            "ma_fan slice %s day=%s pool=%s chunk_hits=%s merged=%s done=%s",
            key,
            day,
            len(pool),
            len(chunk),
            len(merged),
            sorted(done),
        )
    return payload


async def run_ma_fan_all_due_slices(
    *,
    trade_date: str,
    minutes: int,
    top: int = 80,
    min_amount_yi: float = 1.2,
    boards: str = "all",
    force_all: bool = False,
    snapshot: dict[str, Any] | None = None,
    min_price: float = 0.0,
    prefer_main: bool = False,
    slice_spec: tuple[int, int, str] | None = None,
    claimed: bool = False,
) -> dict[str, Any]:
    """Run the next due slice (or all slices when ``force_all``).

    Only one scan runs at a time. Pass ``claimed=True`` when the caller already
    holds the slot via :func:`try_claim_scan`; otherwise a busy slot returns
    ``{"ok": False, "busy": True}`` without scanning.
    """
    day = str(trade_date or "")[:10]
    due: tuple[int, int, str] | None = None
    if not force_all:
        due = slice_spec or next_due_ma_fan_slice(trade_date=day, minutes=minutes)
        if not due:
            from market_desk.db import load_ma_fan_day

            body = load_ma_fan_day(day) or {}
            return {"ok": True, "skipped": True, "scan": body, "view_date": day}
    kind = "force" if force_all else "slice"
    if not claimed and not try_claim_scan(kind, day):
        return {
            "ok": False,
            "busy": True,
            "detail": "已有均线发散扫描在跑",
            "progress": scan_progress(),
        }
    stats = _ScanStats(live=True)
    common = {
        "trade_date": day,
        "top": top,
        "min_amount_yi": min_amount_yi,
        "boards": boards,
        "persist": True,
        "snapshot": snapshot,
        "min_price": min_price,
        "prefer_main": prefer_main,
        "stats": stats,
    }
    try:
        if force_all:
            out = await _run_all_slices(common, stats=stats)
        else:
            offset, count, key = due  # type: ignore[misc]
            stats.begin_slice(key, 1, 1)
            out = await run_ma_fan_scan(
                offset=offset, limit=count, slice_key=key, replace=False, **common
            )
    except Exception as exc:
        log.exception("ma_fan %s scan failed day=%s", kind, day)
        release_scan(error=str(exc) or exc.__class__.__name__)
        raise
    release_scan()
    return out


async def _run_all_slices(common: dict[str, Any], *, stats: _ScanStats) -> dict[str, Any]:
    """Load the amount ranking once, then rebuild every scheduled slice."""
    need = max(off + cnt for _s, off, cnt, _k in MA_FAN_SCHEDULE)
    stats.phase("universe")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False) as client:
        universe = await load_universe(
            client,
            boards=common["boards"],
            need=need,
            min_amount_yi=common["min_amount_yi"],
        )
    if not universe:
        raise RuntimeError("成交额榜为空（新浪无响应或限流），未改动已有结果")
    stats.set_total(
        sum(len(universe[off : off + cnt]) for _s, off, cnt, _k in MA_FAN_SCHEDULE)
    )
    out: dict[str, Any] = {}
    n = len(MA_FAN_SCHEDULE)
    for idx, (_start, offset, count, key) in enumerate(MA_FAN_SCHEDULE, start=1):
        stats.begin_slice(key, idx, n)
        out = await run_ma_fan_scan(
            offset=offset,
            limit=count,
            slice_key=key,
            replace=idx == 1,
            universe=universe,
            **common,
        )
    return out or {"ok": False, "detail": "no slices"}
