"""Stage-3 radar glue: crowding index, broad-ETF pulse and narrative graph (shadow)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from market_desk.calendar import is_trading_day
from market_desk.db import (
    load_board_crowding_history,
    load_narrative_shadow,
    save_board_crowding,
    save_crowd_shadow,
    save_etf_pulse,
    upsert_narrative_shadow,
)
from market_desk.eastmoney import fetch_board_members
from market_desk.microstructure.slippage import session_minutes_elapsed
from market_desk.radar import (
    build_narrative_clusters,
    compute_crowding,
    market_pulse_event,
    market_turnover,
    minute_baseline,
    scan_pulses,
    summarize_leads,
)
from market_desk.tencent import fetch_minute_days, fetch_minute_trends

log = logging.getLogger("market_desk")


def _empty_pulse(note: str = "") -> dict[str, Any]:
    """Return an idle ETF pulse payload."""
    return {"ok": False, "note": note, "event": False, "at": None, "label": "", "etfs": [], "latest": None}


class RadarMixin:
    """Stage-3 radar glue: crowding index, broad-ETF pulse and narrative graph (shadow)."""

    _crowd_hist: tuple[str, dict[str, list[float]]] | None = None
    _crowd_saved_at: float = 0.0
    _pulse_base: tuple[str, dict[str, tuple[dict[str, float], float | None]]] | None = None
    _pulse_cache: tuple[float, dict[str, Any]] | None = None
    _narr_cache: tuple[float, str, dict[str, Any]] | None = None

    def _radar_crowding(
        self,
        boards: list[dict[str, Any]] | None,
        indices: list[dict[str, Any]] | None,
        *,
        now: datetime,
        trade_date_dash: str,
    ) -> dict[str, Any]:
        """Grade board turnover shares and upsert today's shares on a cadence.

        Prior-day history loads once per session date; intraday shares are
        written every ``CROWD_SAVE_EVERY_SEC`` (trading days only) so the
        closing value becomes tomorrow's history.
        """
        from market_desk.config import CROWD_SAVE_EVERY_SEC

        cached = self._crowd_hist
        if not cached or cached[0] != trade_date_dash:
            try:
                hist = load_board_crowding_history(trade_date_dash)
            except Exception:
                log.exception("crowding history load failed")
                hist = {}
            self._crowd_hist = (trade_date_dash, hist)
        history = (self._crowd_hist or ("", {}))[1]
        market = market_turnover(indices)
        # Approx-aliased old Sina industries are far broader than their EM name.
        rows = [b for b in boards or [] if not b.get("alias_approx")]
        crowd = compute_crowding(rows, market, history, now=now)
        if crowd.get("ok") and is_trading_day(now):
            mono = time.monotonic()
            if mono - self._crowd_saved_at >= float(CROWD_SAVE_EVERY_SEC):
                try:
                    save_board_crowding(
                        trade_date_dash, list((crowd.get("boards") or {}).values()), float(market or 0.0)
                    )
                    self._crowd_saved_at = mono
                except Exception:
                    log.exception("crowding save failed")
        return crowd

    async def _pulse_baselines(
        self, client: httpx.AsyncClient, codes: list[str], trade_date_dash: str
    ) -> dict[str, tuple[dict[str, float], float | None]]:
        """Return ``{code: (minute baseline, prev close)}``; fetched once per day."""
        cached = self._pulse_base
        if cached and cached[0] == trade_date_dash and all(c in cached[1] for c in codes):
            return cached[1]
        days_list = await asyncio.gather(
            *(fetch_minute_days(client, c) for c in codes), return_exceptions=True
        )
        out: dict[str, tuple[dict[str, float], float | None]] = {}
        for code, days in zip(codes, days_list):
            if not isinstance(days, dict) or not days:
                continue
            base = minute_baseline(days, exclude_day=trade_date_dash)
            prior = sorted(d for d in days if d < trade_date_dash)
            prev_close = None
            if prior:
                pts = days.get(prior[-1]) or []
                if pts:
                    prev_close = float(pts[-1].get("price") or 0.0) or None
            if base:
                out[code] = (base, prev_close)
        if len(out) == len(codes):
            self._pulse_base = (trade_date_dash, out)
        return out

    async def _radar_etf_pulse(
        self,
        client: httpx.AsyncClient,
        indices: list[dict[str, Any]] | None,
        *,
        now: datetime,
        trade_date_dash: str,
        phase: str,
    ) -> dict[str, Any]:
        """Scan core broad ETFs for same-minute volume pulses (info only).

        Minutes are refetched at most every ``ETF_PULSE_FETCH_SEC``; hits are
        logged idempotently to ``etf_pulse_log``. In a panic phase a market
        rescue event adds a ``phase_hint`` (never changes ready / action).
        """
        from market_desk.config import BROAD_ETF_PULSE, ETF_PULSE_FETCH_SEC, ETF_PULSE_WEAK_INDEX

        if not is_trading_day(now) or session_minutes_elapsed(now) is None:
            return _empty_pulse("非交易时段")
        mono = time.monotonic()
        hit = self._pulse_cache
        if hit and mono - hit[0] < float(ETF_PULSE_FETCH_SEC) and hit[1].get("date") == trade_date_dash:
            return hit[1]
        names = {code: name for code, name in BROAD_ETF_PULSE}
        codes = list(names)
        bases = await self._pulse_baselines(client, codes, trade_date_dash)
        if not bases:
            return _empty_pulse("分时基线缺失")
        series = await asyncio.gather(
            *(fetch_minute_trends(client, c) for c in bases), return_exceptions=True
        )
        idx = {str(r.get("code") or ""): r for r in indices or []}
        try:
            weak_now = float((idx.get("000300") or {}).get("pct") or 0.0) <= float(ETF_PULSE_WEAK_INDEX)
        except (TypeError, ValueError):
            weak_now = False
        hits_by_code: dict[str, list[dict[str, Any]]] = {}
        etfs: list[dict[str, Any]] = []
        log_rows: list[dict[str, Any]] = []
        for code, points in zip(bases, series):
            if not isinstance(points, list) or not points:
                continue
            base, prev_close = bases[code]
            hits = scan_pulses(points, base, prev_close=prev_close, index_weak_now=weak_now)
            hits_by_code[code] = hits
            etfs.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "hits": len(hits),
                    "rescue": sum(1 for h in hits if h["kind"] == "rescue"),
                    "last": hits[-1] if hits else None,
                }
            )
            log_rows.extend({"code": code, "name": names.get(code, code), **h} for h in hits)
        event = market_pulse_event(hits_by_code, names=names)
        if log_rows:
            try:
                save_etf_pulse(trade_date_dash, log_rows)
            except Exception:
                log.exception("etf pulse save failed")
        out = {
            "ok": bool(etfs),
            "note": "" if etfs else "分时缺失",
            "date": trade_date_dash,
            "at": now.strftime("%H:%M"),
            "event": bool(event.get("event")),
            "event_at": event.get("at"),
            "label": event.get("label") or "",
            "codes": event.get("codes") or [],
            "latest": event.get("latest"),
            "etfs": etfs,
            "weak_index": weak_now,
            "phase_hint": "恐慌拐点候选（只提示）" if event.get("event") and phase == "恐慌" else "",
        }
        self._pulse_cache = (mono, out)
        return out

    async def _radar_payload(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
        *,
        boards: list[dict[str, Any]] | None,
        indices: list[dict[str, Any]] | None,
        cards: list[dict[str, Any]],
        zt: list[dict[str, Any]] | None,
        now: datetime,
        trade_date_dash: str,
        phase: str,
    ) -> dict[str, Any]:
        """Assemble the ``radar`` payload; a panic-phase rescue pulse adds an info-only algo note."""
        crowd = verdict.get("crowding") or {}
        if crowd.get("hits") and is_trading_day(now) and session_minutes_elapsed(now) is not None:
            try:
                save_crowd_shadow(trade_date_dash, list(crowd["hits"]), at=now.strftime("%H:%M:%S"))
            except Exception:
                log.exception("crowd shadow save failed")
        out: dict[str, Any] = {
            "crowding": crowd,
            "etf_pulse": _empty_pulse(),
            "narrative": {"ok": False, "shadow": True, "clusters": [], "stats": {}},
        }
        try:
            out["etf_pulse"] = await self._radar_etf_pulse(
                client, indices, now=now, trade_date_dash=trade_date_dash, phase=phase
            )
        except Exception:
            log.exception("etf pulse failed")
        hint = str(out["etf_pulse"].get("phase_hint") or "")
        if hint:
            note = f"宽基托底脉冲：{hint}"
            notes = list(verdict.get("algo_notes") or [])
            if note not in notes:
                notes.append(note)
            verdict["algo_notes"] = notes
        try:
            mainline = str(((verdict.get("mainline") or {}).get("name")) or "")
            out["narrative"] = await self._radar_narrative(
                client,
                boards,
                cards,
                zt,
                now=now,
                trade_date_dash=trade_date_dash,
                mainline=mainline,
            )
        except Exception:
            log.exception("narrative graph failed")
        return out

    async def _narrative_concepts(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]] | None,
        cards: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Concept boards with members: enriched cards plus the top-pct extras."""
        from market_desk.config import NARR_EXTRA_CONCEPTS

        out: dict[str, dict[str, Any]] = {}
        for c in cards:
            name = str(c.get("name") or "")
            if name and c.get("kind") == "concept" and c.get("pool") and name not in out:
                out[name] = c
        extras = sorted(
            (
                b
                for b in boards or []
                if b.get("kind") == "concept" and b.get("bk") and str(b.get("name") or "") not in out
            ),
            key=lambda b: -float(b.get("pct") or 0.0),
        )[: int(NARR_EXTRA_CONCEPTS)]
        sem = asyncio.Semaphore(3)

        async def _one(b: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            async with sem:
                try:
                    return b, await fetch_board_members(client, str(b["bk"]))
                except Exception:
                    return b, []

        for b, members in await asyncio.gather(*(_one(b) for b in extras)):
            if members:
                out[str(b.get("name"))] = {"name": b.get("name"), "kind": "concept", "members": members}
        return list(out.values())

    async def _radar_narrative(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]] | None,
        cards: list[dict[str, Any]],
        zt: list[dict[str, Any]] | None,
        *,
        now: datetime,
        trade_date_dash: str,
        mainline: str,
    ) -> dict[str, Any]:
        """Build shadow narrative clusters every ``NARR_FETCH_EVERY_SEC`` and record sightings.

        Shadow mode: display and log only; nothing feeds mainline selection.
        """
        from market_desk.config import NARR_FETCH_EVERY_SEC

        mono = time.monotonic()
        hit = self._narr_cache
        if hit and hit[1] == trade_date_dash and mono - hit[0] < float(NARR_FETCH_EVERY_SEC):
            return hit[2]
        out: dict[str, Any] = {"ok": False, "shadow": True, "clusters": [], "stats": {}, "at": None}
        if is_trading_day(now) and session_minutes_elapsed(now) is not None and zt:
            concepts = await self._narrative_concepts(client, boards, cards)
            industries = [c for c in cards if c.get("kind") == "industry"]
            clusters = build_narrative_clusters(
                concepts, zt, industry_boards=industries, mainline=mainline
            )
            at = now.strftime("%H:%M:%S")
            if clusters:
                try:
                    upsert_narrative_shadow(trade_date_dash, clusters, at=at, mainline=mainline)
                except Exception:
                    log.exception("narrative shadow save failed")
            out.update(ok=True, clusters=clusters, at=at[:5], concepts=len(concepts))
        try:
            since = (datetime.strptime(trade_date_dash, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
            rows = load_narrative_shadow(since, trade_date_dash)
            first_by_label = {
                r["label"]: r.get("first_seen") for r in rows if r.get("trade_date") == trade_date_dash
            }
            for c in out["clusters"]:
                c["first_seen"] = str(first_by_label.get(c["label"]) or "")[:5]
            out["stats"] = summarize_leads(rows)
        except Exception:
            log.exception("narrative shadow stats failed")
        self._narr_cache = (mono, trade_date_dash, out)
        return out
