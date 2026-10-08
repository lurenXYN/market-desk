"""Review tab payloads: signals, trends, pick scores, history, and ZT YTD."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from datetime import timedelta as _timedelta
from typing import Any
import httpx
from market_desk.db import load_signals_for_date
from market_desk.review import (
    apply_outcomes,
    build_code_signal_history,
    build_review_payload,
    note_quote_ticks,
)
from market_desk.zt_stats import count_limit_ups_ytd_from_bars
from market_desk.eastmoney import (
    fetch_daily_bars,
    fetch_daily_klines_many,
    fetch_holder_stats_many,
)
from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.tencent import fetch_quotes
from market_desk.trend import classify_daily_trend, classify_many

from market_desk.engine.util import _is_session

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))
log = logging.getLogger("market_desk")


class ReviewMixin:
    """Review tab payloads: signals, trends, pick scores, history, and ZT YTD."""

    async def _review_klines(
        self,
        client: httpx.AsyncClient,
        codes: list[str],
        limit: int = 40,
    ) -> dict[str, Any]:
        """Return daily klines for review compare, re-fetching only stale codes.

        Entries live for ``REVIEW_HEAVY_REFRESH_SEC``; empty fetches are not cached
        so a flaky source is retried on the next review build.
        """
        import time

        from market_desk.config import REVIEW_HEAVY_REFRESH_SEC

        ttl = float(REVIEW_HEAVY_REFRESH_SEC)
        now_m = time.monotonic()
        out: dict[str, Any] = {}
        want: list[str] = []
        for raw in codes:
            code = normalize_code(raw)
            if not code or code in out or code in want:
                continue
            hit = self._review_kline_cache.get(code)
            if hit and now_m - hit[0] < ttl:
                out[code] = hit[1]
            else:
                want.append(code)
        if want:
            fresh = await fetch_daily_klines_many(client, want, limit=limit)
            for code, packed in (fresh or {}).items():
                out[code] = packed
                if packed and packed[0]:
                    self._review_kline_cache[code] = (now_m, packed)
        if len(self._review_kline_cache) > 600:
            self._review_kline_cache = {
                c: v for c, v in self._review_kline_cache.items() if now_m - v[0] < ttl
            }
        return out

    def _review_klines_cached(self, codes: list[str]) -> dict[str, Any]:
        """Return cached review klines for ``codes`` and refresh missing / stale ones in the background."""
        from market_desk.config import REVIEW_HEAVY_REFRESH_SEC

        now_m = time.monotonic()
        ttl = float(REVIEW_HEAVY_REFRESH_SEC)
        out: dict[str, Any] = {}
        stale = False
        for raw in codes:
            code = normalize_code(raw)
            hit = self._review_kline_cache.get(code) if code else None
            if hit:
                out[code] = hit[1]
            if code and (not hit or now_m - hit[0] >= ttl):
                stale = True
        task = self._review_kline_task
        if stale and (task is None or task.done()):
            self._review_kline_task = asyncio.create_task(self._warm_review_klines(list(codes)))
        return out

    async def _warm_review_klines(self, codes: list[str]) -> None:
        """Fetch review klines into the cache, then drop cached review payloads."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                await self._review_klines(client, codes)
        except Exception:
            log.exception("review kline warm-up failed")
        finally:
            self._review_cache.clear()

    async def _score_review_backlog(self, pending: list[dict[str, Any]], today: str) -> None:
        """Score pending outcomes and run the one-off formula rescore off the request path.

        Outcomes are written to the DB; the review cache is dropped afterwards so the
        next review build shows them.
        """
        from market_desk.config import OUTCOME_FORMULA_VERSION
        from market_desk.db import load_setting, load_signals, save_setting

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                if pending:
                    codes = [str(r.get("code") or "") for r in pending]
                    packed = await fetch_daily_klines_many(client, codes, limit=40)
                    apply_outcomes(pending, packed)
                # Re-score labeled rows once when the OHLC fake-red formula bumps.
                ver = int(OUTCOME_FORMULA_VERSION)
                try:
                    cur = int(load_setting("outcome_formula_v") or 0)
                except (TypeError, ValueError):
                    cur = 0
                if cur < ver:
                    rows = [
                        r
                        for r in load_signals(limit=800)
                        if r.get("outcome_label") and str(r.get("trade_date") or "") < today
                    ]
                    if rows:
                        codes = list(dict.fromkeys(str(r.get("code") or "") for r in rows))
                        packed = await fetch_daily_klines_many(client, codes, limit=60)
                        n = apply_outcomes(rows, packed, overwrite=True)
                        log.info("outcome formula v%s rescore updated %s / %s rows", ver, n, len(rows))
                    save_setting("outcome_formula_v", ver)
        except Exception:
            log.exception("review backlog scoring failed")
        finally:
            self._review_cache.clear()

    async def build_review(
        self,
        limit: int = 180,
        view_date: str | None = None,
        vs_mainline_mode: str | None = None,
        user_id: int | None = None,
        outcome_standard: str | None = None,
    ) -> dict[str, Any]:
        """Score pending historical signals then return one trade-date review payload."""
        import time

        from market_desk.config import (
            OUTCOME_STANDARDS,
            REVIEW_HEAVY_REFRESH_SEC,
            REVIEW_TODAY_CACHE_SEC,
        )
        from market_desk.settings import setting

        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        day = str(view_date or today).strip()[:10] or today
        mode_key = str(vs_mainline_mode or "").strip().lower() or "auto"
        uid_key = "none" if user_id is None else str(int(user_id))
        std = str(outcome_standard or setting("outcome_standard", "classic") or "classic").strip().lower()
        if std not in OUTCOME_STANDARDS:
            std = "classic"
        cache_key = f"{day}|{mode_key}|{limit}|u:{uid_key}|oc:{std}"
        now_m = time.monotonic()
        hit = self._review_cache.get(cache_key)
        ttl = float(REVIEW_TODAY_CACHE_SEC) if day == today else 3600.0
        if hit and now_m - hit[0] < ttl:
            cached = dict(hit[1])
            cached["cache_hit"] = True
            if not cached.get("trends_fp"):
                from market_desk.review import review_trends_fingerprint

                cached["trends_fp"] = review_trends_fingerprint(
                    day, load_signals_for_date(day), calendar_day=today
                )
            cached["trends_ready"] = False
            return cached

        from market_desk.review import load_pending_outcomes

        since = (datetime.now(CN_TZ) - timedelta(days=12)).strftime("%Y-%m-%d")
        # Outcomes persist to DB, so scoring more often than the heavy cadence adds nothing.
        # Scoring runs in the background: after a holiday the backlog plus a throttled
        # East Money can take a minute, and the review page must not wait on it.
        run_scoring = now_m - self._review_scored_at >= float(REVIEW_HEAVY_REFRESH_SEC)
        task = self._review_scoring_task
        if run_scoring and (task is None or task.done()):
            pending = load_pending_outcomes(today, since=since, cap=80)
            self._review_scored_at = now_m
            self._review_scoring_task = asyncio.create_task(self._score_review_backlog(pending, today))
        quotes: dict[str, dict[str, Any]] = {}
        holders: dict[str, dict[str, Any]] = {}
        day_rows: list[dict[str, Any]] = []
        live_codes: list[str] = []
        packed_overlay: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                day_rows = load_signals_for_date(day)
                live_codes = [str(r.get("code") or "") for r in day_rows]
                stock_codes = [
                    str(r.get("code") or "")
                    for r in day_rows
                    if str(r.get("kind") or "") != "etf"
                ]
                # Extra codes for three-standard compare (recent scored buys).
                try:
                    from market_desk.db import load_signals as _load_signals
                    from market_desk.review import is_buy_signal as _is_buy

                    recent_buys = [
                        str(r.get("code") or "")
                        for r in _load_signals(limit=160)
                        if _is_buy(r.get("signal_type"))
                        and (r.get("outcome_label") or int(r.get("traded") or 0))
                    ]
                    compare_codes = list(
                        dict.fromkeys([c for c in (live_codes + recent_buys) if c])
                    )[:72]
                except Exception:
                    compare_codes = list(dict.fromkeys([c for c in live_codes if c]))

                async def _quotes() -> dict[str, dict[str, Any]]:
                    return await fetch_quotes(client, live_codes)

                async def _holders() -> dict[str, dict[str, Any]]:
                    return await fetch_holder_stats_many(client, stock_codes)

                async def _overlay_klines() -> dict[str, Any]:
                    # outcome_compare reads cached bars only; missing / stale codes are
                    # fetched in the background so a throttled source cannot stall the page.
                    codes = compare_codes or live_codes
                    if not codes:
                        return {}
                    return self._review_klines_cached(codes)

                # zt_ytd / daily-trend chips load async (see /api/review/zt-ytd, /trends).
                quotes, holders, packed_overlay = await asyncio.gather(
                    _quotes(),
                    _holders(),
                    _overlay_klines(),
                )
                note_quote_ticks(quotes)
        except Exception:
            log.exception("signal scoring / live marks failed")
        phase = None
        if day == today and self.snapshot:
            phase = self.snapshot.get("phase")
        from market_desk.review import (
            build_outcome_compare,
            overlay_outcomes_for_standard,
            review_trends_fingerprint,
            summarize_signals,
        )

        payload = build_review_payload(
            limit=limit,
            quotes=quotes,
            trade_date=day,
            phase=phase,
            boards=list((self.snapshot or {}).get("hot_boards") or [])
            + list((self.snapshot or {}).get("pin_boards") or []),
            live_mainline=(
                ((self.snapshot or {}).get("verdict") or {}).get("mainline") or {}
            ).get("name"),
            vs_mainline_mode=vs_mainline_mode,
            holders=holders,
            user_id=user_id,
            trends=None,
        )
        payload["outcome_standard"] = std
        if packed_overlay:
            try:
                from market_desk.db import load_signals as _load_signals
                from market_desk.db import filter_signals_for_viewer

                wide = filter_signals_for_viewer(_load_signals(limit=160), user_id)
                summary = dict(payload.get("summary") or {})
                summary["outcome_compare"] = build_outcome_compare(wide, packed_overlay)
                payload["summary"] = summary
            except Exception:
                log.exception("outcome_compare failed")
                try:
                    summary = dict(payload.get("summary") or {})
                    summary["outcome_compare"] = build_outcome_compare(
                        list(payload.get("signals") or []),
                        packed_overlay,
                    )
                    payload["summary"] = summary
                except Exception:
                    pass
        if std != "classic" and packed_overlay:
            sigs = overlay_outcomes_for_standard(
                list(payload.get("signals") or []),
                packed_overlay,
                standard=std,
            )
            payload["signals"] = sigs
            day_sum = summarize_signals(sigs)
            summary = dict(payload.get("summary") or {})
            summary["outcome_standard"] = std
            summary["buy_hit_rate"] = day_sum.get("buy_hit_rate")
            summary["buy_hit_rate_all"] = day_sum.get("buy_hit_rate_all")
            summary["buy_avg_day1"] = day_sum.get("buy_avg_day1")
            summary["buy_scored"] = day_sum.get("buy_scored")
            summary["buy_scored_all"] = day_sum.get("buy_scored_all")
            payload["summary"] = summary
        # Classic path: still compare using stored labels + klines for other stds.
        elif not packed_overlay:
            try:
                summary = dict(payload.get("summary") or {})
                if not summary.get("outcome_compare", {}).get("ok"):
                    summary["outcome_compare"] = build_outcome_compare(
                        list(payload.get("signals") or []),
                        None,
                    )
                    payload["summary"] = summary
            except Exception:
                pass
        payload["trends_fp"] = review_trends_fingerprint(
            day, day_rows or load_signals_for_date(day), calendar_day=today
        )
        payload["trends_ready"] = False
        payload["cache_hit"] = False
        self._review_cache[cache_key] = (now_m, payload)
        return payload

    def clear_review_cache(self) -> None:
        """Drop cached review payloads after personal trade annotations change."""
        self._review_cache.clear()

    async def build_review_trends(
        self,
        view_date: str | None = None,
    ) -> dict[str, Any]:
        """Classify daily trends for one review day's codes (async chips).

        Cached by calendar day + signal-id fingerprint: refetch only when the
        session day rolls or that view day's signal set changes.
        """
        from market_desk.review import review_trends_fingerprint

        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        day = str(view_date or today).strip()[:10] or today
        day_rows = load_signals_for_date(day)
        fp = review_trends_fingerprint(day, day_rows, calendar_day=today)
        hit = self._review_trend_cache.get(fp)
        if hit:
            out = dict(hit)
            out["cache_hit"] = True
            return out

        codes = [str(r.get("code") or "") for r in day_rows if r.get("code")]
        by_code: dict[str, dict[str, Any]] = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                if codes:
                    closes_by_code, ok_by_code = await self._resolve_daily_closes(
                        client, codes, today
                    )
                    by_code = classify_many(closes_by_code, ok_by_code)
        except Exception:
            log.exception("review trends fetch failed")

        payload = {
            "trade_date": day,
            "calendar_day": today,
            "fingerprint": fp,
            "by_code": by_code,
            "cache_hit": False,
            "refreshed_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._review_trend_cache[fp] = dict(payload)
        # Drop stale fingerprints for other calendar days to bound memory.
        stale = [
            k
            for k in self._review_trend_cache
            if not str(k).startswith(f"{today}|")
        ]
        for k in stale:
            self._review_trend_cache.pop(k, None)
        return payload

    async def score_picks(
        self,
        signal_ids: list[int],
        codes: list[str],
    ) -> dict[str, Any]:
        """Score review signals and/or manual tickers side by side (display only).

        Review rows keep their plan price, source and gates; manual codes are
        scored on live quote, daily trend, board fit and stock traits only.
        """
        from market_desk.config import PICK_MAX_ITEMS
        from market_desk.db import load_signal, load_signals
        from market_desk.pick_score import rank_picks
        from market_desk.review import _flatten_signal_prices

        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sid in signal_ids:
            row = load_signal(int(sid))
            code = normalize_code((row or {}).get("code"))
            if not row or not code or code in seen:
                continue
            seen.add(code)
            rows.append(_flatten_signal_prices(row))
        for raw in codes:
            code = normalize_code(raw)
            if not code or code in seen:
                continue
            seen.add(code)
            rows.append(
                {
                    "code": code,
                    "name": code,
                    "signal_type": "manual",
                    "kind": "etf" if code.startswith(("1", "5")) else "stock",
                    "manual": True,
                    "payload": {},
                }
            )
        rows = rows[: int(PICK_MAX_ITEMS)]
        if not rows:
            return {"ok": False, "error": "请至少选择一只票", "items": []}
        rows = await self._enrich_pick_rows(rows, today)
        try:
            history = load_signals(limit=800)
        except Exception:
            history = []
        out = rank_picks(rows, history)
        out["scored_at"] = datetime.now(CN_TZ).strftime("%H:%M:%S")
        return out

    async def _enrich_pick_rows(
        self, rows: list[dict[str, Any]], today: str
    ) -> list[dict[str, Any]]:
        """Attach live quote, trend, board fit, holders, zt count, ma-fan and cv for scoring."""
        from market_desk.chip_volume import (
            add_ivol,
            bars_before_many,
            build_cv,
            index_returns_before,
        )
        from market_desk.config import PICK_LHB_LOOKBACK_DAYS
        from market_desk.lhb import recent_billboard_map
        from market_desk.ma_fan import enrich_signals_with_ma_fan
        from market_desk.review import (
            enrich_signals_with_boards,
            enrich_signals_with_holders,
            enrich_signals_with_live_marks,
            enrich_signals_with_trends,
        )
        from market_desk.zt_stats import enrich_signals_with_zt_ytd

        all_codes = list(dict.fromkeys(str(r["code"]) for r in rows))
        stock_codes = list(dict.fromkeys(str(r["code"]) for r in rows if r.get("kind") != "etf"))
        names = {str(r["code"]): str(r.get("name") or "") for r in rows}
        quotes: dict[str, dict[str, Any]] = {}
        trends: dict[str, dict[str, Any]] = {}
        holders: dict[str, dict[str, Any]] = {}
        zt_map: dict[str, dict[str, Any]] = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:

                async def _trends() -> dict[str, dict[str, Any]]:
                    closes, ok = await self._resolve_daily_closes(client, all_codes, today)
                    return classify_many(closes, ok)

                async def _zt() -> dict[str, dict[str, Any]]:
                    if not stock_codes:
                        return {}
                    return await self._zt_ytd_for_codes(
                        client, stock_codes, names, trade_date=today
                    )

                async def _holders() -> dict[str, dict[str, Any]]:
                    if not stock_codes:
                        return {}
                    return await fetch_holder_stats_many(client, stock_codes)

                async def _cv_bars() -> dict[str, list[dict[str, Any]]]:
                    if not stock_codes:
                        return {}
                    return await bars_before_many(client, stock_codes, today)

                async def _cv_mkt() -> dict[str, float]:
                    if not stock_codes:
                        return {}
                    return await index_returns_before(client, today)

                async def _lhb() -> dict[str, dict[str, Any]]:
                    if not stock_codes:
                        return {}
                    return await recent_billboard_map(client, today, int(PICK_LHB_LOOKBACK_DAYS))

                results = await asyncio.gather(
                    fetch_quotes(client, all_codes),
                    _trends(),
                    _zt(),
                    _holders(),
                    _cv_bars(),
                    _cv_mkt(),
                    _lhb(),
                    return_exceptions=True,
                )
                quotes, trends, zt_map, holders, cv_bars, cv_mkt, lhb_map = [
                    r if isinstance(r, dict) else {} for r in results
                ]
        except Exception:
            cv_bars, cv_mkt, lhb_map = {}, {}, {}
            log.exception("pick score enrich failed")
        for r in rows:
            code = str(r["code"])
            q = quotes.get(code) or {}
            if r.get("manual") and q.get("name"):
                r["name"] = q["name"]
            if r.get("kind") != "etf":
                o, prev = num(q.get("open")), num(q.get("prev"))
                if o and prev:
                    r["open_gap_pct"] = round((o / prev - 1.0) * 100.0, 2)
                if code in lhb_map:
                    r["lhb_recent"] = lhb_map[code]
        snap = self.snapshot or {}
        rows = enrich_signals_with_live_marks(rows, quotes) if quotes else rows
        rows = enrich_signals_with_boards(
            rows,
            list(snap.get("hot_boards") or []) + list(snap.get("pin_boards") or []),
            live_mainline=((snap.get("verdict") or {}).get("mainline") or {}).get("name"),
        )
        if trends:
            rows = enrich_signals_with_trends(rows, trends)
        if holders:
            rows = enrich_signals_with_holders(rows, holders)
        if zt_map:
            rows = enrich_signals_with_zt_ytd(rows, zt_map)
        try:
            rows = enrich_signals_with_ma_fan(rows, today)
        except Exception:
            pass
        for r in rows:
            stored_today = isinstance(r.get("cv"), dict) and str(r.get("trade_date") or "")[:10] == today
            bars = cv_bars.get(str(r["code"]))
            if not bars:
                continue
            if stored_today:
                r["cv"] = dict(r["cv"])
                add_ivol(r["cv"], bars, cv_mkt, code=str(r["code"]))
                continue
            price = num(r.get("plan_price")) or num(r.get("price")) or num(r.get("live_last"))
            r["cv"] = build_cv(bars, price, cv_mkt, code=str(r["code"]))
        return rows

    async def build_review_scores(self, view_date: str | None = None) -> dict[str, Any]:
        """Per-row pick scores for one review day's buy signals.

        Today is live-scored (briefly cached) and the first score per signal is
        kept in ``payload.pick0``; past days only show that stored score, since
        live price factors would be meaningless there.
        """
        from market_desk.config import REVIEW_SCORE_CACHE_SEC
        from market_desk.db import load_signals, load_signals_for_date, set_signal_payload_once
        from market_desk.pick_score import build_history_stats, pick_top, score_pick
        from market_desk.review import _flatten_signal_prices, is_buy_signal

        now = datetime.now(CN_TZ)
        today = now.strftime("%Y-%m-%d")
        day = str(view_date or today)[:10]
        raw = [r for r in load_signals_for_date(day) if is_buy_signal(r.get("signal_type"))]

        def _stored(r: dict[str, Any]) -> dict[str, Any] | None:
            p0 = (r.get("payload") or {}).get("pick0") if isinstance(r.get("payload"), dict) else None
            return p0 if isinstance(p0, dict) and p0.get("score") is not None else None

        if day != today:
            items = {
                str(r["id"]): {**p0, "code": r.get("code"), "name": r.get("name"), "stored": True}
                for r in raw
                if (p0 := _stored(r))
            }
            return {"ok": True, "trade_date": day, "live": False, "items": items, "top": pick_top(items)}

        key = (day, tuple(sorted(int(r["id"]) for r in raw)))
        cached = getattr(self, "_review_score_cache", None)
        if cached and cached[0] == key and time.time() - cached[1] < float(REVIEW_SCORE_CACHE_SEC):
            return {**cached[2], "cache_hit": True}
        lock = getattr(self, "_review_score_lock", None)
        if lock is None:
            lock = self._review_score_lock = asyncio.Lock()
        async with lock:
            cached = getattr(self, "_review_score_cache", None)
            if cached and cached[0] == key and time.time() - cached[1] < float(REVIEW_SCORE_CACHE_SEC):
                return {**cached[2], "cache_hit": True}
            rows = await self._enrich_pick_rows([_flatten_signal_prices(r) for r in raw], today)
            try:
                stats = build_history_stats(load_signals(limit=800))
            except Exception:
                stats = build_history_stats([])
            at = datetime.now(CN_TZ).strftime("%H:%M")
            items: dict[str, dict[str, Any]] = {}
            fresh: dict[int, tuple[str, Any]] = {}
            for r in rows:
                s = score_pick(r, stats)
                item = {
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "score": s["score"],
                    "grade": s["grade"],
                    "factors": s["factors"],
                    "waiting": s["waiting"],
                    "position": s["position"],
                    "blocked": s["blocked"],
                    "live_last": r.get("live_last"),
                    "at": at,
                }
                first = _stored(r)
                if first:
                    item["first"] = {"score": first.get("score"), "grade": first.get("grade"), "at": first.get("at")}
                else:
                    fresh[int(r["id"])] = (
                        "pick0",
                        {k: item[k] for k in ("score", "grade", "factors", "position", "live_last", "at")},
                    )
                items[str(r["id"])] = item
            if fresh:
                try:
                    set_signal_payload_once(fresh)
                except Exception:
                    log.exception("store first pick scores failed")
            out = {
                "ok": True,
                "trade_date": day,
                "live": True,
                "items": items,
                "top": pick_top(items),
                "scored_at": datetime.now(CN_TZ).strftime("%H:%M:%S"),
                "history_n": stats["n"],
                "base_win3": stats["base_win3"],
            }
            self._review_score_cache = (key, time.time(), out)
            return out

    def _maybe_capture_pick_scores(self, now: datetime) -> None:
        """Score today's buys in the background so ``pick0`` lands near signal time."""
        from market_desk.config import REVIEW_SCORE_BG_SEC

        if not _is_session(now):
            return
        task = getattr(self, "_pick_bg_task", None)
        if task is not None and not task.done():
            return
        if time.time() - float(getattr(self, "_pick_bg_at", 0.0)) < float(REVIEW_SCORE_BG_SEC):
            return
        self._pick_bg_at = time.time()

        async def _run() -> None:
            try:
                await self.build_review_scores()
            except Exception:
                log.exception("background pick scores failed")

        self._pick_bg_task = asyncio.create_task(_run())

    async def build_review_code_history(
        self,
        code: str,
        *,
        user_id: int | None = None,
        limit: int = 120,
    ) -> dict[str, Any]:
        """Return signal history plus current trend / holder snapshot for one code."""
        c = normalize_code(code)
        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        holders: dict[str, dict[str, Any]] = {}
        trend: dict[str, Any] | None = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
                closes_task = self._resolve_daily_closes(client, [c], today)
                # ETF / funds skip shareholder stats.
                want_holder = bool(c) and not c.startswith(("1", "5"))
                if want_holder:
                    holders_task = fetch_holder_stats_many(client, [c])
                    (closes_by_code, ok_by_code), holders = await asyncio.gather(
                        closes_task, holders_task
                    )
                else:
                    closes_by_code, ok_by_code = await closes_task
                series = list(closes_by_code.get(c) or [])
                trend = classify_daily_trend(
                    series, fetch_ok=bool(ok_by_code.get(c, bool(series)))
                )
        except Exception:
            log.exception("review code history enrich failed for %s", c)
        return build_code_signal_history(
            c,
            limit=limit,
            holders=holders or None,
            trend=trend,
            user_id=user_id,
        )

    async def build_review_zt_ytd(
        self,
        view_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetch calendar-year limit-up counts for one review day (async column)."""
        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        day = str(view_date or today).strip()[:10] or today
        day_rows = load_signals_for_date(day)
        stock_rows = [r for r in day_rows if str(r.get("kind") or "") != "etf"]
        stock_codes = [str(r.get("code") or "") for r in stock_rows]
        name_by_code = {
            normalize_code(r.get("code")): str(r.get("name") or "")
            for r in stock_rows
        }
        by_code: dict[str, dict[str, Any]] = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
                by_code = await self._zt_ytd_for_codes(
                    client, stock_codes, name_by_code, trade_date=day
                )
        except Exception:
            log.exception("review zt_ytd fetch failed")
        return {
            "trade_date": day,
            "by_code": by_code,
            "refreshed_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def _zt_ytd_for_codes(
        self,
        client: httpx.AsyncClient,
        codes: list[str],
        names: dict[str, str],
        *,
        trade_date: str,
        concurrency: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """Resolve calendar-year limit-up counts (cached once per code+year)."""
        year = int(str(trade_date or "")[:4] or datetime.now(CN_TZ).year)
        day = str(trade_date or "")[:10]
        uniq = []
        seen: set[str] = set()
        for raw in codes:
            c = normalize_code(raw)
            if not c or c in seen:
                continue
            seen.add(c)
            uniq.append(c)
        out: dict[str, dict[str, Any]] = {}
        need: list[str] = []
        for c in uniq:
            key = f"{year}:{c}"
            hit = self._zt_ytd_cache.get(key) or self._zt_ytd_cache.get(c)
            if hit and int(hit.get("year") or 0) == year:
                # Year-scoped hit: reuse across trade days; refresh day stamp.
                row = dict(hit)
                row["day"] = day
                self._zt_ytd_cache[key] = row
                out[c] = row
            else:
                need.append(c)
        if need:
            # Prefer unadjusted EM history (beg/end disables Tencent-qfq shortcut).
            beg = f"{year - 1}-12-01"
            sem = asyncio.Semaphore(max(1, int(concurrency or 5)))

            async def _one(code: str) -> list[dict[str, Any]]:
                async with sem:
                    return await fetch_daily_bars(
                        client, code, limit=320, adjust=0, beg=beg, end=day
                    )

            bar_lists = await asyncio.gather(*[_one(c) for c in need])
            for c, bars in zip(need, bar_lists):
                cnt = count_limit_ups_ytd_from_bars(
                    bars, name=names.get(c) or "", year=year, code=c
                )
                row = {
                    "day": day,
                    "year": year,
                    "count": cnt,
                    "note": f"{year}年日线涨停次数（未复权收盘涨幅阈值，非官方字段）",
                }
                self._zt_ytd_cache[f"{year}:{c}"] = row
                self._zt_ytd_cache[c] = row  # legacy key for older callers
                out[c] = row
        return out
