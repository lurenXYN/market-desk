"""Recommendation enrichment: trends, holders, minutes, CV, and filters."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timedelta as _timedelta
from typing import Any
import httpx
from market_desk.eastmoney import (
    fetch_daily_closes_many,
    fetch_daily_klines_many,
    fetch_holder_stats_many,
    fetch_minute_trends_many,
)
from market_desk.filters import normalize_code
from market_desk.minute_confirm import apply_minute_confirmations
from market_desk.trend import classify_many
from market_desk.verdict import apply_stock_daily_trends, mark_pullback_entries

from market_desk.engine.watch import _attach_holders_to_items

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))
log = logging.getLogger("market_desk")


class RecommendMixin:
    """Recommendation enrichment: trends, holders, minutes, CV, and filters."""

    async def _resolve_daily_closes(
        self,
        client: httpx.AsyncClient,
        codes: list[str],
        trade_date: str,
    ) -> tuple[dict[str, list[float]], dict[str, bool]]:
        """Resolve daily closes once per code per trade date (shared day cache)."""
        day = str(trade_date or "")[:10]
        if day and day != self._kline_day:
            self._kline_day = day
            self._kline_cache.clear()
            self._kline_ok.clear()
        uniq = list(
            dict.fromkeys(
                normalize_code(c) for c in codes if normalize_code(c)
            )
        )
        need = [c for c in uniq if c not in self._kline_cache]
        if need:
            fetched = await fetch_daily_closes_many(client, need, limit=60)
            for code in need:
                closes = list(fetched.get(code) or [])
                self._kline_cache[code] = closes
                self._kline_ok[code] = bool(closes)
        closes_by_code = {c: list(self._kline_cache.get(c) or []) for c in uniq}
        ok_by_code = {c: bool(self._kline_ok.get(c, bool(closes_by_code.get(c)))) for c in uniq}
        return closes_by_code, ok_by_code

    def _cached_trends_for(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """Classify trends from day-cached closes without network I/O."""
        closes: dict[str, list[float]] = {}
        ok: dict[str, bool] = {}
        for raw in codes:
            code = normalize_code(raw)
            if not code or code not in self._kline_cache:
                continue
            closes[code] = list(self._kline_cache.get(code) or [])
            ok[code] = bool(self._kline_ok.get(code, bool(closes[code])))
        return classify_many(closes, ok)

    async def _apply_recommend_trends(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
        trade_date: str,
    ) -> None:
        """Fetch daily closes for recommended stocks/ETFs and mark non-uptrends."""
        rec = verdict.get("recommend") or {}
        side_rec = verdict.get("side_recommend") or {}
        link_rec = verdict.get("link_recommend") or {}
        dragon_rec = verdict.get("dragon_recommend") or {}
        indep_rec = verdict.get("independent_recommend") or {}
        codes = [
            str(x.get("code") or "")
            for x in list(rec.get("items") or [])
            + list(side_rec.get("items") or [])
            + list(link_rec.get("items") or [])
            + list(dragon_rec.get("items") or [])
            + list(indep_rec.get("items") or [])
            if x.get("kind") in ("stock", "etf") and x.get("code")
        ]
        codes = list(dict.fromkeys(codes))
        if not codes:
            return
        closes_by_code, fetch_ok_by_code = await self._resolve_daily_closes(
            client, codes, trade_date
        )
        verdict["recommend"] = apply_stock_daily_trends(
            rec, closes_by_code, fetch_ok_by_code
        )
        if side_rec.get("items"):
            verdict["side_recommend"] = apply_stock_daily_trends(
                side_rec, closes_by_code, fetch_ok_by_code
            )
            # Side branch stays observation-only even if trend looks up.
            for item in (verdict["side_recommend"].get("items") or []):
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
            verdict["side_recommend"]["buy"] = False
        if link_rec.get("items"):
            verdict["link_recommend"] = apply_stock_daily_trends(
                link_rec, closes_by_code, fetch_ok_by_code
            )
            for item in (verdict["link_recommend"].get("items") or []):
                item["ready"] = False
                item["link_board"] = True
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
            verdict["link_recommend"]["buy"] = False
        if dragon_rec.get("items"):
            verdict["dragon_recommend"] = apply_stock_daily_trends(
                dragon_rec, closes_by_code, fetch_ok_by_code
            )
        if indep_rec.get("items"):
            verdict["independent_recommend"] = apply_stock_daily_trends(
                indep_rec, closes_by_code, fetch_ok_by_code
            )
            for item in (verdict["independent_recommend"].get("items") or []):
                item["ready"] = False
                item["independent_pop"] = True
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
            verdict["independent_recommend"]["buy"] = False
        await self._attach_recommend_cv(client, verdict, trade_date)

    async def _attach_recommend_cv(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
        trade_date: str,
    ) -> None:
        """Attach prior-day chip / volume context to recommended stocks (time-boxed, soft)."""
        from market_desk.chip_volume import attach_cv
        from market_desk.config import CV_TICK_TIMEOUT_S

        items = [
            it
            for key in (
                "recommend",
                "side_recommend",
                "link_recommend",
                "dragon_recommend",
                "independent_recommend",
                "watch_trial_recommend",
            )
            for it in ((verdict.get(key) or {}).get("items") or [])
            if isinstance(it, dict)
        ]
        if not items:
            return
        try:
            await asyncio.wait_for(
                attach_cv(client, items, trade_date), timeout=float(CV_TICK_TIMEOUT_S)
            )
        except Exception as exc:
            log.debug("recommend chip/volume context skipped: %r", exc)

    async def _filter_stock_recommends_by_zt_ytd(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
        trade_date: str,
    ) -> None:
        """Drop stock cards with zero calendar-year limit-ups (ETF kept)."""
        keys = (
            "recommend",
            "side_recommend",
            "link_recommend",
            "dragon_recommend",
            "independent_recommend",
            "watch_trial_recommend",
        )
        codes: list[str] = []
        names: dict[str, str] = {}
        for key in keys:
            box = verdict.get(key) or {}
            for item in box.get("items") or []:
                if item.get("kind") == "etf":
                    continue
                code = normalize_code(item.get("code"))
                if not code:
                    continue
                codes.append(code)
                names[code] = str(item.get("name") or "")
        if not codes:
            return
        zt_map = await self._zt_ytd_for_codes(
            client, codes, names, trade_date=trade_date, concurrency=4
        )
        for key in keys:
            box = verdict.get(key)
            if not isinstance(box, dict) or not box.get("items"):
                continue
            kept: list[dict[str, Any]] = []
            dropped = 0
            for item in box.get("items") or []:
                if item.get("kind") == "etf":
                    kept.append(item)
                    continue
                code = normalize_code(item.get("code"))
                hit = zt_map.get(code) or {}
                cnt = hit.get("count")
                item["zt_ytd"] = cnt
                item["zt_ytd_year"] = hit.get("year")
                if cnt is None:
                    # Fetch miss: keep but mark pending (do not hard-drop).
                    item["zt_ytd_pending"] = True
                    kept.append(item)
                    continue
                if int(cnt) < 1:
                    dropped += 1
                    continue
                kept.append(item)
            box["items"] = kept
            if dropped and key == "recommend":
                notes = list((verdict.get("algo_notes") or []))
                tip = f"年内0涨停已剔除{dropped}只"
                if tip not in notes:
                    notes.append(tip)
                verdict["algo_notes"] = notes
            if not kept and key != "recommend":
                verdict[key] = None if key != "recommend" else box

    async def _refine_independent_pullbacks(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
        trade_date: str,
    ) -> None:
        """Soft-check independent-pop cards against the near-N-day low.

        Prefer names holding the N-day low; when that fails but the pre-filter
        already tagged a day-low, optionally keep them (observe-only) so the
        panel is not wiped empty by stacked hard gates.
        """
        from market_desk.config import (
            INDEPENDENT_POP_KEEP_DAY_LOW_IF_5D_MISS,
            INDEPENDENT_POP_LOW_DAYS,
            INDEPENDENT_POP_NEAR_LOW_PCT,
        )
        from market_desk.leaders import within_n_day_low

        box = verdict.get("independent_recommend")
        if not isinstance(box, dict):
            return
        items = list(box.get("items") or [])
        if not items:
            verdict["independent_recommend"] = None
            return
        codes = [normalize_code(x.get("code")) for x in items if x.get("code")]
        codes = [c for c in codes if c]
        if not codes:
            return
        packed = await fetch_daily_klines_many(
            client, codes, limit=max(20, int(INDEPENDENT_POP_LOW_DAYS) + 5), concurrency=4
        )
        prefer: list[dict[str, Any]] = []
        soft: list[dict[str, Any]] = []
        keep_day = bool(INDEPENDENT_POP_KEEP_DAY_LOW_IF_5D_MISS)
        for item in items:
            code = normalize_code(item.get("code"))
            triple = packed.get(code) if packed else None
            lows: list[float | None] = []
            last = item.get("last") or item.get("price")
            if triple and len(triple) >= 3:
                ohlc = triple[2] or {}
                lows = list(ohlc.get("low") or [])
            last_f = float(last) if last is not None else None
            if within_n_day_low(
                lows,
                last_f,
                n=int(INDEPENDENT_POP_LOW_DAYS),
                max_pct=float(INDEPENDENT_POP_NEAR_LOW_PCT),
            ):
                item["near_5d_low"] = True
                prefer.append(item)
            elif not lows:
                # No bars yet — keep soft day-low candidates.
                prefer.append(item)
            elif keep_day:
                item["near_5d_low"] = False
                tip = "近低观察·未贴5日低"
                reason = str(item.get("reason") or "")
                if tip not in reason:
                    item["reason"] = f"{reason}；{tip}" if reason else tip
                soft.append(item)
        kept = prefer + soft
        box["items"] = kept
        if not kept:
            verdict["independent_recommend"] = None
        else:
            box["text"] = f"独立人气回踩 · {len(kept)}只"
            verdict["independent_recommend"] = box

    async def _apply_recommend_holders(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
    ) -> None:
        """Attach quarterly shareholder counts onto stock buy cards (skip ETF)."""
        keys = ("recommend", "side_recommend", "link_recommend")
        codes: list[str] = []
        for key in keys:
            for item in ((verdict.get(key) or {}).get("items") or []):
                if str(item.get("kind") or "stock") != "stock":
                    continue
                code = normalize_code(item.get("code"))
                if code:
                    codes.append(code)
        codes = list(dict.fromkeys(codes))
        if not codes:
            return
        try:
            holders = await fetch_holder_stats_many(client, codes)
        except Exception:
            log.exception("recommend holder stats failed")
            return
        for key in keys:
            rec = verdict.get(key)
            if not isinstance(rec, dict) or not rec.get("items"):
                continue
            verdict[key] = _attach_holders_to_items(rec, holders)

    async def _enrich_auction_strategy(self, client: httpx.AsyncClient) -> None:
        """Attach holders + YTD limit-up counts onto auction strategy rows."""
        snap = self.snapshot
        if not isinstance(snap, dict):
            return
        box = snap.get("auction_strategy")
        if not isinstance(box, dict):
            return
        tiers = list(box.get("tiers") or [])
        codes: list[str] = []
        for tier in tiers:
            for item in (tier.get("items") or []):
                code = normalize_code(item.get("code"))
                if code:
                    codes.append(code)
        codes = list(dict.fromkeys(codes))
        if not codes:
            box["enrich_pending"] = False
            snap["auction_strategy"] = box
            return
        holders: dict[str, dict[str, Any]] = {}
        zt_map: dict[str, Any] = {}
        names = {
            normalize_code(it.get("code")): str(it.get("name") or "")
            for tier in tiers
            for it in (tier.get("items") or [])
            if normalize_code(it.get("code"))
        }
        day = str(snap.get("trade_date") or "")[:10]
        try:
            holders = await fetch_holder_stats_many(client, codes)
        except Exception:
            log.exception("auction holder stats failed")
        try:
            zt_map = await self._zt_ytd_for_codes(
                client, codes, names, trade_date=day or datetime.now(CN_TZ).strftime("%Y-%m-%d")
            )
        except Exception:
            log.exception("auction zt_ytd failed")
        for tier in tiers:
            for item in (tier.get("items") or []):
                code = normalize_code(item.get("code"))
                if not code:
                    continue
                h = holders.get(code) or {}
                if h:
                    item["holder_num"] = h.get("holder_num")
                    item["holder_chg_pct"] = h.get("holder_chg_pct")
                    item["holder_avg_wan"] = h.get("holder_avg_wan")
                    item["holder_end"] = h.get("holder_end")
                z = zt_map.get(code)
                if isinstance(z, dict):
                    item["zt_ytd"] = z.get("count")
                    item["zt_ytd_year"] = z.get("year")
                elif z is not None:
                    item["zt_ytd"] = z
        box["enrich_pending"] = False
        box["tiers"] = tiers
        snap["auction_strategy"] = box
        self.snapshot = snap

    async def _apply_favorite_desk_holders(
        self,
        client: httpx.AsyncClient,
        fav_desk: dict[str, Any],
    ) -> None:
        """Attach shareholder counts onto favorite-desk stock buy cards."""
        boards = list((fav_desk or {}).get("boards") or [])
        if not boards:
            return
        codes: list[str] = []
        for board in boards:
            for item in ((board.get("buy") or {}).get("items") or []):
                if str(item.get("kind") or "stock") != "stock":
                    continue
                code = normalize_code(item.get("code"))
                if code:
                    codes.append(code)
        codes = list(dict.fromkeys(codes))
        if not codes:
            return
        try:
            holders = await fetch_holder_stats_many(client, codes)
        except Exception:
            log.exception("favorite-desk holder stats failed")
            return
        for board in boards:
            buy = board.get("buy")
            if isinstance(buy, dict) and buy.get("items"):
                board["buy"] = _attach_holders_to_items(buy, holders)

    async def _apply_favorite_desk_trends(
        self,
        client: httpx.AsyncClient,
        fav_desk: dict[str, Any],
        trade_date: str,
    ) -> None:
        """Apply daily-trend gates to favorite-desk stock buy cards."""
        boards = list((fav_desk or {}).get("boards") or [])
        if not boards:
            return
        codes: list[str] = []
        for board in boards:
            buy = board.get("buy") or {}
            for item in buy.get("items") or []:
                if item.get("kind") in ("stock", "etf") and item.get("code"):
                    codes.append(str(item.get("code") or ""))
        codes = list(dict.fromkeys(codes))
        if not codes:
            for board in boards:
                soft = bool(board.get("etf_soft"))
                board["buy"] = mark_pullback_entries(
                    board.get("buy"), observe_only=soft
                )
            return
        closes_by_code, fetch_ok_by_code = await self._resolve_daily_closes(
            client, codes, trade_date
        )
        for board in boards:
            soft = bool(board.get("etf_soft"))
            buy = apply_stock_daily_trends(
                board.get("buy") or {},
                closes_by_code,
                fetch_ok_by_code,
            )
            board["buy"] = mark_pullback_entries(buy, observe_only=soft)

    async def _apply_favorite_desk_minutes(
        self,
        client: httpx.AsyncClient,
        fav_desk: dict[str, Any],
    ) -> None:
        """Minute-gate ready cards inside favorite-desk buy boxes."""
        boards = list((fav_desk or {}).get("boards") or [])
        if not boards:
            return
        codes: list[str] = []
        for board in boards:
            if board.get("etf_soft"):
                continue
            buy = board.get("buy") or {}
            for item in buy.get("items") or []:
                if item.get("code") and (item.get("ready") or item.get("near_entry")):
                    codes.append(str(item.get("code") or "").zfill(6))
        codes = sorted(set(c for c in codes if c))
        if not codes:
            return
        now_ts = datetime.now(CN_TZ).timestamp()
        minutes_by_code: dict[str, list[dict[str, Any]]] = {}
        need: list[str] = []
        for code in codes:
            hit = self._minute_cache.get(code)
            if hit and now_ts - hit[0] < 45:
                minutes_by_code[code] = hit[1]
            else:
                need.append(code)
        if need:
            packed = await fetch_minute_trends_many(client, need)
            for code in need:
                series = list(packed.get(code) or [])
                if series:
                    self._minute_cache[code] = (now_ts, series)
                minutes_by_code[code] = series
        for board in boards:
            if board.get("etf_soft"):
                continue
            soft = bool(board.get("etf_soft"))
            buy = apply_minute_confirmations(board.get("buy") or {}, minutes_by_code)
            board["buy"] = mark_pullback_entries(buy, observe_only=soft)

    async def _apply_recommend_minutes(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
    ) -> None:
        """Fetch minute series for recommend cards and gate structure.

        Prefer ready / near-entry codes; also pull other item codes (cap 8) so
        buy-progress shows a real minute verdict instead of a soft placeholder.
        """
        rec = verdict.get("recommend") or {}
        dragon = verdict.get("dragon_recommend") or {}
        items = list(rec.get("items") or []) + list(dragon.get("items") or [])
        priority: list[str] = []
        rest: list[str] = []
        for x in items:
            code = str(x.get("code") or "").zfill(6)
            if not code or len(code) != 6:
                continue
            if x.get("ready") or x.get("near_entry"):
                priority.append(code)
            else:
                rest.append(code)
        # De-dupe while keeping priority first; cap to limit East Money fan-out.
        codes: list[str] = []
        seen: set[str] = set()
        for code in [*priority, *rest]:
            if code in seen:
                continue
            seen.add(code)
            codes.append(code)
            if len(codes) >= 8:
                break
        if not codes:
            return
        now_ts = datetime.now(CN_TZ).timestamp()
        minutes_by_code: dict[str, list[dict[str, Any]]] = {}
        need: list[str] = []
        for code in codes:
            hit = self._minute_cache.get(code)
            if hit and now_ts - hit[0] < 45:
                minutes_by_code[code] = hit[1]
            else:
                need.append(code)
        if need:
            packed = await fetch_minute_trends_many(client, need)
            for code in need:
                series = list(packed.get(code) or [])
                if series:
                    self._minute_cache[code] = (now_ts, series)
                minutes_by_code[code] = series
        verdict["recommend"] = apply_minute_confirmations(rec, minutes_by_code)
        if dragon.get("items"):
            verdict["dragon_recommend"] = apply_minute_confirmations(
                dragon, minutes_by_code
            )

    async def _prefetch_position_minutes(
        self,
        client: httpx.AsyncClient,
        codes: list[str] | None,
    ) -> None:
        """Warm minute cache for open positions (sell buffer + soft-take gates)."""
        uniq: list[str] = []
        seen: set[str] = set()
        for raw in codes or []:
            code = str(raw or "").zfill(6)
            if len(code) != 6 or code in seen:
                continue
            seen.add(code)
            uniq.append(code)
            if len(uniq) >= 10:
                break
        if not uniq:
            return
        now_ts = datetime.now(CN_TZ).timestamp()
        need: list[str] = []
        for code in uniq:
            hit = self._minute_cache.get(code)
            if hit and now_ts - hit[0] < 45:
                continue
            need.append(code)
        if not need:
            return
        packed = await fetch_minute_trends_many(client, need)
        for code in need:
            series = list(packed.get(code) or [])
            if series:
                self._minute_cache[code] = (now_ts, series)

    def apply_theme_manual_adj(
        self,
        theme_key: str,
        *,
        manual_adj: float | None = None,
        delta: float | None = None,
        note: str | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Persist a manual theme reputation nudge and patch live board affinity."""
        from market_desk.db import set_theme_manual_adj
        from market_desk.theme_memory import attach_board_affinity, reputation_map

        row = set_theme_manual_adj(
            theme_key,
            manual_adj=manual_adj,
            delta=delta,
            note=note,
            clear=clear,
        )
        if not row:
            return {"ok": False, "error": "empty theme"}
        rep = reputation_map()
        for pool in ("hot_boards", "pin_boards", "favorite_boards"):
            cards = list(self.snapshot.get(pool) or [])
            if cards:
                self.snapshot[pool] = attach_board_affinity(cards, rep)
        tm = dict(self.snapshot.get("theme_memory") or {})
        tm["reputation"] = list(rep.values())[:20]
        self.snapshot["theme_memory"] = tm
        # Soft-patch mainline why chips when the sticky theme matches.
        verdict = self.snapshot.get("verdict") or {}
        ml = verdict.get("mainline") or {}
        why = dict(ml.get("why") or {}) if isinstance(ml, dict) else {}
        from market_desk.mainline import theme_key as canon_theme

        ml_theme = canon_theme(str(ml.get("name") or "")) if ml else ""
        row_theme = str(row.get("theme_key") or "")
        if ml_theme and row_theme and ml_theme == row_theme:
            # Find refreshed adj from hot/pin cards.
            patched = None
            for pool in ("hot_boards", "pin_boards", "favorite_boards"):
                for b in self.snapshot.get(pool) or []:
                    if canon_theme(str(b.get("name") or "")) == ml_theme:
                        patched = b
                        break
                if patched:
                    break
            if patched is not None:
                why["rep_adj"] = patched.get("rep_adj")
                why["rep_label"] = patched.get("rep_label")
                ml = dict(ml)
                ml["why"] = why
                verdict = dict(verdict)
                verdict["mainline"] = ml
                self.snapshot["verdict"] = verdict
        return {
            "ok": True,
            "row": row,
            "theme_memory": self.snapshot.get("theme_memory"),
        }
