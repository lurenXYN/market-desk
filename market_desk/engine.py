"""Refresh loop that assembles the dashboard snapshot."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from datetime import timedelta as _timedelta
from typing import Any
try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))

import httpx

from market_desk.config import (
    HOT_BOARD_COUNT,
    ICE_BOARD_COUNT,
    PIN_INDUSTRY_ALIASES,
)
from market_desk.calendar import is_trading_day
from market_desk.backup_store import write_auto_backup
from market_desk.settings import setting
from market_desk.db import (
    init_db,
    load_auction,
    load_board_hist_map,
    load_daily,
    load_favorite_boards,
    load_fund_flow_for_dates,
    list_fund_flow_dates,
    load_mainline_switches,
    load_positions,
    load_session_segments,
    load_signals_for_date,
    load_trend_overrides,
    load_unscored_signals,
    load_watchlist,
    purge_stale_closed_positions,
    save_auction,
    save_board_daily,
    save_daily,
    save_fund_flow_daily,
    touch_position_peaks,
    try_add_mainline_switch,
    upsert_session_segment,
)
from market_desk.auction_scan import build_auction_strategy
from market_desk.emotion_wave import build_emotion_wave
from market_desk.elliott import build_elliott_scenarios
from market_desk.fund_flow import build_fund_flow_board
from market_desk.seasonality import build_seasonality
from market_desk.lifecycle import build_mainline_lifecycle
from market_desk.review import (
    apply_outcomes,
    build_price_touch_alerts,
    build_review_payload,
    note_quote_ticks,
    record_session_signals,
)
from market_desk.zt_stats import count_limit_ups_ytd_from_bars
from market_desk.similar import build_similar_days
from market_desk.report import build_morning_brief
from market_desk.session import SEGMENT_ORDER, segment_snapshot_row, session_segment

from market_desk.eastmoney import (
    fetch_board_fund_flow,
    fetch_board_members,
    fetch_daily_bars,
    fetch_daily_closes_many,
    fetch_daily_klines_many,
    fetch_holder_stats_many,
    fetch_hot_boards,
    fetch_main_quotes,
    fetch_minute_trends,
    fetch_yesterday_zt,
    fetch_zb_pool,
    fetch_zt_pool,
)
from market_desk.filters import is_limit_down, is_main_board, normalize_code
from market_desk.gap_fade import sync_gap_fade_blacklist
from market_desk.glossary import GLOSSARY
from market_desk.minute_confirm import apply_minute_confirmations
from market_desk.numbers import num
from market_desk.notify import (
    build_toast_alerts,
    filter_alerts_for_policy,
    is_buy_quiet_window,
    notify_windows,
    select_toasts_for_round,
)
from market_desk.sentiment import (
    auction_from_quotes,
    board_cycle_tags,
    board_headline,
    board_note,
    board_status,
    build_market_metrics,
    classify_phase,
    cluster_path,
    cycle_flags,
    enrich_market_context,
    ice_status,
    kpi_bars,
    score_temperature,
    spark_values,
)
from market_desk.mainline import etf_spec_for_name, etf_spec_soft_fallback
from market_desk.tencent import fetch_etfs, fetch_indices, fetch_quotes, fetch_daily_bars_symbol
from market_desk.trend import classify_many
from market_desk.verdict import (
    align_action_with_ready,
    apply_size_cap_gate,
    apply_stock_daily_trends,
    attach_board_etf_trends,
    build_deltas,
    build_favorite_desk_plans,
    build_risk_overview,
    build_sell_advice,
    build_verdict,
    decorate_positions,
    mark_pullback_entries,
    position_summary,
)

log = logging.getLogger("market_desk")


class DeskEngine:
    """Hold the latest snapshot and refresh it in the background."""

    def __init__(self) -> None:
        self.snapshot: dict[str, Any] = {
            "ok": False,
            "error": "starting",
            "updated_at": None,
        }
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._eod_date: str | None = None
        self._toast_armed = False
        self._toast_sent: dict[str, float] = {}
        # Level toasts (band/wl) stay latched until the condition clears.
        self._toast_latched: set[str] = set()
        self._toast_latch_date: str | None = None
        self._toast_feed: list[dict[str, Any]] = []
        # Daily closes by trade date: one fetch per code per session day.
        self._kline_day: str | None = None
        self._kline_cache: dict[str, list[float]] = {}
        self._kline_ok: dict[str, bool] = {}
        self._minute_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        # Index OHLCV for Elliott scenarios (once per trade day).
        self._index_bars_day: str | None = None
        self._index_bars: list[dict[str, Any]] = []
        # code -> {"day": trade_date, "year": int, "count": int}
        self._zt_ytd_cache: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        """Create tables and start the polling task."""
        init_db()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the polling task."""
        if self._task:
            self._task.cancel()

    def sync_positions(self) -> list[dict[str, Any]]:
        """Reload positions from SQLite and reuse last quotes in the snapshot."""
        trade_date = str(self.snapshot.get("trade_date") or datetime.now().strftime("%Y%m%d"))
        trade_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if len(trade_date) == 8 else trade_date[:10]
        purge_stale_closed_positions(trade_dash)
        quotes: dict[str, dict[str, Any]] = {}
        for row in self.snapshot.get("positions") or []:
            code = str(row.get("code") or "").zfill(6)
            quotes[code] = {
                "code": code,
                "name": row.get("name") or "",
                "price": row.get("last"),
                "pct": row.get("last_pct"),
                "high": row.get("high"),
                "low": row.get("low"),
            }
        positions = decorate_positions(load_positions(), quotes, trade_date=trade_dash)
        self.snapshot["positions"] = positions
        self.snapshot["position_summary"] = position_summary(positions)
        cap = float(
            ((self.snapshot.get("verdict") or {}).get("playbook") or {}).get("size_cap_pct")
            or 100
        )
        self.snapshot["risk_overview"] = build_risk_overview(
            positions, size_cap_pct=cap
        )
        peaks = {
            int(r["id"]): float(r["peak_price"])
            for r in positions
            if r.get("peak_dirty") and r.get("id") and r.get("peak_price")
        }
        if peaks:
            try:
                touch_position_peaks(peaks)
            except Exception:
                log.exception("touch position peaks failed")
        trends = self._cached_trends_for(
            [normalize_code(r.get("code")) for r in positions if r.get("code")]
        )
        self.snapshot["sell_advice"] = build_sell_advice(
            positions,
            self.snapshot.get("verdict") or {},
            self.snapshot.get("phase") or "",
            trade_date=trade_dash,
            trends_by_code=trends,
        )
        return positions

    def sync_watchlist(self, quotes: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Reload personal watchlist and attach last prices when available."""
        qmap = dict(quotes or {})
        if not qmap:
            for row in self.snapshot.get("watchlist") or []:
                code = str(row.get("code") or "").zfill(6)
                if row.get("last") is not None:
                    qmap[code] = {"price": row.get("last"), "pct": row.get("last_pct"), "name": row.get("name")}
        rows = _decorate_watchlist(load_watchlist(), qmap)
        self.snapshot["watchlist"] = rows
        return rows

    def sync_favorite_boards(self) -> list[dict[str, Any]]:
        """Reload favored boards into the live snapshot without a full refresh."""
        rows = load_favorite_boards()
        cards = list(self.snapshot.get("favorite_boards") or [])
        by_bk = {str(c.get("bk") or "").upper(): c for c in cards if c.get("bk")}
        for pool in ("hot_boards", "pin_boards", "ice_boards"):
            for c in self.snapshot.get(pool) or []:
                bk = str(c.get("bk") or "").upper()
                if bk and bk not in by_bk:
                    by_bk[bk] = c
        out: list[dict[str, Any]] = []
        fav_bks = {str(r.get("bk") or "").upper() for r in rows if r.get("bk")}
        for row in rows:
            bk = str(row.get("bk") or "").upper()
            hit = by_bk.get(bk)
            if hit:
                card = dict(hit)
            else:
                card = {
                    "bk": bk,
                    "name": row.get("name") or bk,
                    "kind": row.get("kind") or "",
                    "pct": None,
                    "status": "观察",
                    "headline": "已加入看好，下一轮刷新后补全行情",
                    "tone": "slate",
                    "members": [],
                    "tags": [],
                    "spark": [],
                    "note": row.get("note") or "",
                }
            card["favorite_id"] = row.get("id")
            card["favorite_note"] = row.get("note") or ""
            card["in_favorite"] = True
            out.append(card)
        self.snapshot["favorite_boards"] = out
        for pool in ("hot_boards", "pin_boards", "ice_boards", "favorite_boards"):
            for card in self.snapshot.get(pool) or []:
                bk = str(card.get("bk") or "").upper()
                card["in_favorite"] = bk in fav_bks
                if card["in_favorite"]:
                    match = next(
                        (r for r in rows if str(r.get("bk") or "").upper() == bk),
                        None,
                    )
                    if match:
                        card["favorite_id"] = match.get("id")
        return out

    async def _loop(self) -> None:
        first = True
        while True:
            now = datetime.now(CN_TZ)
            live = _is_session(now)
            today = now.strftime("%Y-%m-%d")
            after_close = (
                is_trading_day(now) and _minutes(now) >= 15 * 60 + 5 and self._eod_date != today
            )
            if first or live or after_close:
                try:
                    await self.refresh()
                    if after_close:
                        self._eod_date = today
                        if bool(setting("auto_backup", True)):
                            try:
                                path = write_auto_backup(trade_date=today)
                                log.info("auto backup written %s", path)
                            except Exception:
                                log.exception("auto backup failed")
                except Exception as exc:
                    log.exception("refresh failed")
                    self.snapshot["ok"] = False
                    msg = f"{type(exc).__name__}: {exc}"
                    self.snapshot["error"] = f"refresh failed · {msg}"
                    warns = list(self.snapshot.get("warnings") or [])
                    if msg not in warns:
                        warns.append(msg)
                    self.snapshot["warnings"] = warns[-12:]
                first = False
            elif self.snapshot.get("ok"):
                self.snapshot["live"] = False
                self.snapshot["polling"] = False
                self.snapshot["trading_day"] = is_trading_day(now)
            await asyncio.sleep(
                int(setting("refresh_seconds", 20)) if live else int(setting("idle_seconds", 60))
            )

    async def refresh(self) -> None:
        """Pull public snapshots and rebuild the dashboard payload."""
        async with self._lock:
            now = datetime.now(CN_TZ)
            trade_date = now.strftime("%Y%m%d")
            trade_date_dash = now.strftime("%Y-%m-%d")
            errors: list[str] = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                (
                    zt, zb, quotes, boards, etfs, indices,
                    flow_ind_d, flow_con_d,
                    flow_ind_w, flow_con_w,
                    flow_ind_m, flow_con_m,
                ) = await asyncio.gather(
                    _safe(fetch_zt_pool, client, trade_date, errors=errors, label="zt"),
                    _safe(fetch_zb_pool, client, trade_date, errors=errors, label="zb"),
                    _safe(fetch_main_quotes, client, errors=errors, label="quotes"),
                    _safe(fetch_hot_boards, client, errors=errors, label="boards"),
                    _safe(fetch_etfs, client, errors=errors, label="etf"),
                    _safe(fetch_indices, client, errors=errors, label="index"),
                    _safe(fetch_board_fund_flow, client, "industry", 80, "day", errors=errors, label="flow-hy-d"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "day", errors=errors, label="flow-gn-d"),
                    _safe(fetch_board_fund_flow, client, "industry", 80, "week", errors=errors, label="flow-hy-w"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "week", errors=errors, label="flow-gn-w"),
                    _safe(fetch_board_fund_flow, client, "industry", 80, "month", errors=errors, label="flow-hy-m"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "month", errors=errors, label="flow-gn-m"),
                )
                yesterday_zt = await self._yesterday(client, now, errors)
                zt = zt or []
                zb = zb or []
                quotes = quotes or []
                boards = boards or []
                etfs = etfs or []
                indices = indices or []
                yesterday_zt = yesterday_zt or []
                try:
                    save_fund_flow_daily(
                        trade_date_dash,
                        list(flow_ind_d or []) + list(flow_con_d or []),
                    )
                except Exception:
                    log.exception("save fund_flow_daily failed")
                stored_dates = list_fund_flow_dates(trade_date_dash, limit=40)
                # Ensure today's fetch is visible even if save raced / failed.
                if trade_date_dash not in stored_dates and (flow_ind_d or flow_con_d):
                    stored_dates = [trade_date_dash] + stored_dates
                stored_rows = load_fund_flow_for_dates(stored_dates)
                if trade_date_dash not in {str(r.get("trade_date")) for r in stored_rows}:
                    for r in list(flow_ind_d or []) + list(flow_con_d or []):
                        row = dict(r)
                        row["trade_date"] = trade_date_dash
                        stored_rows.append(row)
                fund_flow = build_fund_flow_board(
                    {
                        "day": {
                            "industry": flow_ind_d or [],
                            "concept": flow_con_d or [],
                        },
                        "week": {
                            "industry": flow_ind_w or [],
                            "concept": flow_con_w or [],
                        },
                        "month": {
                            "industry": flow_ind_m or [],
                            "concept": flow_con_m or [],
                        },
                    },
                    trade_date=trade_date_dash,
                    stored_dates=stored_dates,
                    stored_rows=stored_rows,
                )
                ctx = {
                    "zt": zt,
                    "zb": zb,
                    "yzt": yesterday_zt,
                    "hist": load_board_hist_map(trade_date_dash),
                }
                hot_cards = await self._hot_cards(client, boards, ctx)
                pin_cards = await self._pin_cards(client, boards, hot_cards, ctx)
                ice_cards = await self._ice_cards(client, boards, hot_cards, ctx)
                fav_rows = load_favorite_boards()
                fav_cards = await self._favorite_cards(client, boards, fav_rows, ctx)
                _mark_favorite_flags(hot_cards + pin_cards + ice_cards + fav_cards, fav_rows)
                purge_stale_closed_positions(trade_date_dash)
                pos_rows = load_positions()
                wl_rows = load_watchlist()
                need_codes = [str(r.get("code") or "") for r in pos_rows] + [
                    str(r.get("code") or "") for r in wl_rows
                ]
                pos_quote_map = await _safe(
                    fetch_quotes,
                    client,
                    need_codes,
                    errors=errors,
                    label="pos",
                )
                await self._ensure_index_bars(client, trade_date_dash, errors=errors)
            note_quote_ticks(quotes)
            note_quote_ticks(etfs)
            note_quote_ticks(pos_quote_map if isinstance(pos_quote_map, dict) else None)
            try:
                blacklist_state = sync_gap_fade_blacklist(
                    quotes, trade_date=trade_date_dash, now=now
                )
            except Exception:
                log.exception("gap-fade blacklist sync failed")
                from market_desk.db import load_stock_blacklist
                rows = load_stock_blacklist()
                blacklist_state = {
                    "ok": False,
                    "items": rows,
                    "codes": [str(r.get("code") or "").zfill(6) for r in rows],
                }
            contagion = _contagion(ice_cards)
            save_board_daily(trade_date_dash, hot_cards + pin_cards + ice_cards + fav_cards)
            if not isinstance(pos_quote_map, dict):
                pos_quote_map = {}
            positions = decorate_positions(pos_rows, pos_quote_map, trade_date=trade_date_dash)
            peaks = {
                int(r["id"]): float(r["peak_price"])
                for r in positions
                if r.get("peak_dirty") and r.get("id") and r.get("peak_price")
            }
            if peaks:
                try:
                    touch_position_peaks(peaks)
                except Exception:
                    log.exception("touch position peaks failed")
            watchlist = _decorate_watchlist(wl_rows, pos_quote_map)

            metrics = build_market_metrics(quotes, zt, zb, yesterday_zt)
            history_prev = load_daily(20)
            metrics = enrich_market_context(
                metrics, indices=indices, history=history_prev
            )
            temperature = score_temperature(metrics)
            phase = classify_phase(
                metrics,
                temperature,
                panic_temp=int(setting("phase_panic_temp", 28)),
                ferment_temp=int(setting("phase_ferment_temp", 45)),
                climax_temp=int(setting("phase_climax_temp", 72)),
            )
            auction = self._auction(trade_date, now, quotes)
            save_daily(
                trade_date_dash,
                {
                    "phase": phase,
                    "temperature": temperature,
                    "ups": metrics["ups"],
                    "downs": metrics["downs"],
                    "zt": metrics["zt"],
                    "dt": metrics["dt"],
                    "zb_rate": metrics["zb_rate"],
                    "height": metrics["height"],
                    "promotion": metrics["promotion"],
                    "promo_1_2": metrics.get("promo_1_2"),
                    "promo_2_3": metrics.get("promo_2_3"),
                    "premium": metrics["premium"],
                    "ladder_fill": metrics.get("ladder_fill"),
                    "ladder_gap": metrics.get("ladder_gap"),
                    "ge2": metrics.get("ge2"),
                    "amount_yi": metrics["amount_yi"],
                    "amount_pctile": metrics.get("amount_pctile"),
                    "big_drop": metrics.get("big_drop"),
                    "hs300_pct": metrics.get("hs300_pct"),
                    "cyb_pct": metrics.get("cyb_pct"),
                    "event": _event_line(phase, metrics, hot_cards),
                },
            )
            history = load_daily(14)
            cycle = _cycle_view(history, trade_date_dash)
            similar = build_similar_days(
                phase=phase,
                temperature=temperature,
                metrics=metrics,
                history=history_prev,
            )
            prev = self.snapshot if self.snapshot.get("ok") else None
            # Day-once daily closes: carrier ETFs + open positions before mainline pick.
            etf_codes = _board_etf_codes(hot_cards + pin_cards + fav_cards)
            pos_codes = [
                normalize_code(r.get("code"))
                for r in positions
                if normalize_code(r.get("code"))
            ]
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                await self._resolve_daily_closes(
                    client, list(dict.fromkeys(etf_codes + pos_codes)), trade_date_dash
                )
                board_trends = self._cached_trends_for(etf_codes)
                hot_cards = attach_board_etf_trends(hot_cards, board_trends)
                pin_cards = attach_board_etf_trends(pin_cards, board_trends)
                fav_cards = attach_board_etf_trends(fav_cards, board_trends)
                verdict = build_verdict(
                    now,
                    phase,
                    metrics,
                    etfs,
                    hot_cards,
                    prev,
                    zt,
                    auction=auction,
                    similar=similar,
                    zb=zb,
                )
                await self._apply_recommend_trends(client, verdict, trade_date_dash)
                # Arm near-entry first, then minute-gate ready cards (never re-arm after).
                soft_main = bool((verdict.get("mainline") or {}).get("etf_soft"))
                verdict["recommend"] = mark_pullback_entries(
                    verdict.get("recommend"), observe_only=soft_main
                )
                verdict["side_recommend"] = mark_pullback_entries(
                    verdict.get("side_recommend"), observe_only=True
                )
                await self._apply_recommend_minutes(client, verdict)
                verdict = apply_size_cap_gate(verdict, positions)
                aligned = align_action_with_ready(verdict)
                verdict.clear()
                verdict.update(aligned)
                updated_at = now.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    self._persist_session_context(
                        trade_date_dash,
                        updated_at,
                        verdict,
                        phase,
                        temperature,
                        prev,
                    )
                except Exception:
                    log.exception("session context persist failed")
                segments = _decorate_segments(
                    load_session_segments(trade_date_dash),
                    (verdict.get("segment") or {}).get("display_key")
                    or (verdict.get("segment") or {}).get("key"),
                )
                switches = load_mainline_switches(trade_date_dash)
                payload = {
                    "ok": True,
                    "error": None,
                    "warnings": errors,
                    "updated_at": updated_at,
                    "prev_updated_at": (prev or {}).get("updated_at"),
                    "trade_date": trade_date_dash,
                    "live": _is_session(now),
                    "polling": _is_session(now),
                    "trading_day": is_trading_day(now),
                    "refresh_seconds": int(setting("refresh_seconds", 20)),
                    "phase": phase,
                    "temperature": temperature,
                    "metrics": metrics,
                    "kpis": kpi_bars(metrics),
                    "auction": auction,
                    "auction_strategy": build_auction_strategy(
                        yesterday_zt=yesterday_zt,
                        quotes=quotes,
                        zt_today=zt,
                        zb_today=zb,
                        now=now,
                        trading_day=is_trading_day(now),
                    ),
                    "etfs": etfs,
                    "indices": indices,
                    "emotion_wave": build_emotion_wave(
                        phase=phase,
                        temperature=temperature,
                        metrics=metrics,
                        mainline=(verdict.get("mainline") if isinstance(verdict, dict) else None)
                        or {},
                    ),
                    "seasonality": build_seasonality(
                        trade_date_dash,
                        history=history,
                    ),
                    "elliott": self._build_elliott(indices),
                    "fund_flow": fund_flow,
                    "hot_boards": hot_cards,
                    "pin_boards": pin_cards,
                    "ice_boards": ice_cards,
                    "favorite_boards": fav_cards,
                    "contagion": contagion,
                    "history": history,
                    "cycle": cycle,
                    "similar_days": similar,
                    "events": _today_events(phase, metrics, hot_cards, zt, ice_cards, contagion),
                    "watch": _decorate_watch_pool(
                        _watch_pool(zt, zb, quotes),
                        list(hot_cards) + list(pin_cards) + list(fav_cards),
                        ((verdict.get("mainline") or {}).get("name")) or "",
                        {
                            normalize_code(w.get("code"))
                            for w in watchlist
                            if normalize_code(w.get("code"))
                        },
                        quotes=quotes,
                    ),
                    "filter": "个股只做主板 · 市值门槛 · 创业板/科创走 ETF",
                    "verdict": verdict,
                    "session_segments": segments,
                    "mainline_switches": switches,
                    "mainline_lifecycle": build_mainline_lifecycle(hot_cards, pin_cards),
                    "positions": positions,
                    "position_summary": position_summary(positions),
                    "risk_overview": build_risk_overview(
                        positions,
                        size_cap_pct=float(
                            ((verdict.get("playbook") or {}).get("size_cap_pct") or 100)
                        ),
                    ),
                    "watchlist": watchlist,
                    "stock_blacklist": blacklist_state,
                    "recent_toasts": list(self._toast_feed),
                    "sell_advice": build_sell_advice(
                        positions,
                        verdict,
                        phase,
                        trade_date=trade_date_dash,
                        trends_by_code=self._cached_trends_for(pos_codes),
                    ),
                    "glossary": GLOSSARY,
                }
                fav_desk = build_favorite_desk_plans(
                    favorite_boards=fav_cards,
                    etfs=etfs,
                    zt=zt,
                    zb=zb,
                    positions=positions,
                    phase=phase,
                    bans=list((verdict.get("bans") or [])),
                    stock_block=bool(verdict.get("stock_block")),
                    mainline_name=str(((verdict.get("mainline") or {}).get("name")) or ""),
                    trade_date=trade_date_dash,
                    metrics=metrics,
                    hot_boards=hot_cards,
                )
                await self._apply_favorite_desk_trends(client, fav_desk, trade_date_dash)
                await self._apply_favorite_desk_minutes(client, fav_desk)
            payload["favorite_desk"] = fav_desk
            payload["health"] = _build_health(now, errors, updated_at, payload)
            payload["deltas"] = build_deltas(payload, prev)
            payload["morning_brief"] = build_morning_brief(payload)
            self._emit_toasts(prev, payload)
            try:
                record_session_signals(payload)
            except Exception:
                log.exception("signal record failed")
            self.snapshot = payload

    def _persist_session_context(
        self,
        trade_date: str,
        updated_at: str,
        verdict: dict[str, Any],
        phase: str,
        temperature: int,
        previous: dict[str, Any] | None,
    ) -> None:
        """Save segment conclusions and append mainline switch events."""
        seg = verdict.get("segment") or session_segment(datetime.now(CN_TZ))
        if seg.get("key") in SEGMENT_ORDER:
            upsert_session_segment(
                segment_snapshot_row(
                    trade_date, seg, verdict, phase, temperature, updated_at
                )
            )
        # Also keep the last active trading segment frozen when we enter lunch/close.
        if seg.get("key") == "closed":
            # Prefer updating morning during lunch, afternoon after close if already saved.
            pass

        cur_name = ((verdict.get("mainline") or {}).get("name") or "").strip()
        prev_name = (
            (((previous or {}).get("verdict") or {}).get("mainline") or {}).get("name")
            or ""
        ).strip()
        if cur_name and prev_name and cur_name != prev_name:
            # Sibling boards in the same theme are sticky continuity, not a switch event.
            from market_desk.mainline import same_theme

            if same_theme(prev_name, cur_name):
                return
            try_add_mainline_switch(
                {
                    "trade_date": trade_date,
                    "switched_at": updated_at,
                    "from_name": prev_name,
                    "to_name": cur_name,
                    "action": verdict.get("action"),
                    "phase": phase,
                    "temperature": temperature,
                },
                min_seconds=int(setting("switch_min_seconds", 300)),
            )

    async def build_review(
        self,
        limit: int = 180,
        view_date: str | None = None,
        vs_mainline_mode: str | None = None,
    ) -> dict[str, Any]:
        """Score pending historical signals then return one trade-date review payload."""
        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        day = str(view_date or today).strip()[:10] or today
        pending = load_unscored_signals(today, limit=80)
        quotes: dict[str, dict[str, Any]] = {}
        holders: dict[str, dict[str, Any]] = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                day_rows = load_signals_for_date(day)
                live_codes = [str(r.get("code") or "") for r in day_rows]
                stock_codes = [
                    str(r.get("code") or "")
                    for r in day_rows
                    if str(r.get("kind") or "") != "etf"
                ]

                async def _score_pending() -> None:
                    if not pending:
                        return
                    codes = [str(r.get("code") or "") for r in pending]
                    packed = await fetch_daily_klines_many(client, codes, limit=40)
                    apply_outcomes(pending, packed)

                async def _quotes() -> dict[str, dict[str, Any]]:
                    return await fetch_quotes(client, live_codes)

                async def _holders() -> dict[str, dict[str, Any]]:
                    return await fetch_holder_stats_many(client, stock_codes)

                # zt_ytd is heavy (per-code daily bars); load via /api/review/zt-ytd.
                _, quotes, holders = await asyncio.gather(
                    _score_pending(),
                    _quotes(),
                    _holders(),
                )
                note_quote_ticks(quotes)
        except Exception:
            log.exception("signal scoring / live marks failed")
        phase = None
        if day == today and self.snapshot:
            phase = self.snapshot.get("phase")
        return build_review_payload(
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
        return {"trade_date": day, "by_code": by_code}

    async def _zt_ytd_for_codes(
        self,
        client: httpx.AsyncClient,
        codes: list[str],
        names: dict[str, str],
        *,
        trade_date: str,
        concurrency: int = 5,
    ) -> dict[str, dict[str, Any]]:
        """Resolve calendar-year limit-up counts (cached once per code per day)."""
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
            hit = self._zt_ytd_cache.get(c)
            if hit and hit.get("day") == day and hit.get("year") == year:
                out[c] = hit
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
                    bars, name=names.get(c) or "", year=year
                )
                row = {
                    "day": day,
                    "year": year,
                    "count": cnt,
                    "note": f"{year}年日线涨停次数（未复权收盘涨幅阈值，非官方字段）",
                }
                self._zt_ytd_cache[c] = row
                out[c] = row
        return out

    def _emit_toasts(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
    ) -> None:
        """Fire page feed + optional Windows toasts for important transitions."""
        if not self._toast_armed:
            self._toast_armed = True
            return
        now = datetime.now(CN_TZ)
        now_ts = now.timestamp()
        day = str(current.get("trade_date") or "")
        if day and day != self._toast_latch_date:
            self._toast_latched.clear()
            self._toast_latch_date = day
        cooldown = int(setting("toast_cooldown", 180))
        alerts = list(build_toast_alerts(previous, current))
        try:
            alerts.extend(build_price_touch_alerts(current))
        except Exception:
            log.exception("price-touch alerts failed")
        alerts = filter_alerts_for_policy(
            alerts,
            decision_alerts=bool(setting("decision_alerts", True)),
            quiet_buy=is_buy_quiet_window(
                now,
                trading_day=bool(current.get("trading_day", True)),
                open_mute_minutes=int(setting("open_mute_minutes", 5)),
            ),
        )
        chosen, latched = select_toasts_for_round(
            alerts,
            latched=self._toast_latched,
            sent_at=self._toast_sent,
            now_ts=now_ts,
            cooldown=float(cooldown),
            max_n=2,
        )
        self._toast_latched = latched
        win_on = bool(setting("toast_enabled", True))
        stamp = now.strftime("%H:%M:%S")
        for key, title, body in chosen:
            self._toast_sent[key] = now_ts
            self._toast_feed.insert(
                0,
                {
                    "ts": stamp,
                    "key": key,
                    "title": title,
                    "body": body,
                },
            )
            self._toast_feed = self._toast_feed[:12]
            if win_on:
                if notify_windows(title, body):
                    log.info("toast %s | %s", title, body)
            else:
                log.info("toast(page) %s | %s", title, body)
        if chosen:
            current["recent_toasts"] = list(self._toast_feed)

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
        codes = [
            str(x.get("code") or "")
            for x in list(rec.get("items") or []) + list(side_rec.get("items") or [])
            if x.get("kind") in ("stock", "etf") and x.get("code")
        ]
        codes = list(dict.fromkeys(codes))
        if not codes:
            return
        closes_by_code, fetch_ok_by_code = await self._resolve_daily_closes(
            client, codes, trade_date
        )
        overrides = load_trend_overrides(trade_date)
        verdict["recommend"] = apply_stock_daily_trends(
            rec, closes_by_code, fetch_ok_by_code, overrides
        )
        if side_rec.get("items"):
            verdict["side_recommend"] = apply_stock_daily_trends(
                side_rec, closes_by_code, fetch_ok_by_code, overrides
            )
            # Side branch stays observation-only even if trend looks up.
            for item in (verdict["side_recommend"].get("items") or []):
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
            verdict["side_recommend"]["buy"] = False

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
        overrides = load_trend_overrides(trade_date)
        for board in boards:
            soft = bool(board.get("etf_soft"))
            buy = apply_stock_daily_trends(
                board.get("buy") or {},
                closes_by_code,
                fetch_ok_by_code,
                overrides,
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
                if item.get("ready") and item.get("code"):
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
            fetched = await asyncio.gather(
                *[fetch_minute_trends(client, c) for c in need],
                return_exceptions=True,
            )
            for code, rows in zip(need, fetched):
                if isinstance(rows, Exception):
                    minutes_by_code[code] = []
                    continue
                series = list(rows or [])
                self._minute_cache[code] = (now_ts, series)
                minutes_by_code[code] = series
        for board in boards:
            if board.get("etf_soft"):
                continue
            buy = apply_minute_confirmations(board.get("buy") or {}, minutes_by_code)
            board["buy"] = buy

    async def _apply_recommend_minutes(
        self,
        client: httpx.AsyncClient,
        verdict: dict[str, Any],
    ) -> None:
        """Fetch minute series for ready cards and downgrade failed structures."""
        rec = verdict.get("recommend") or {}
        codes = sorted(
            {
                str(x.get("code") or "").zfill(6)
                for x in (rec.get("items") or [])
                if x.get("ready") and x.get("code")
            }
        )
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
            fetched = await asyncio.gather(
                *[fetch_minute_trends(client, c) for c in need],
                return_exceptions=True,
            )
            for code, rows in zip(need, fetched):
                if isinstance(rows, Exception):
                    log.debug("minute fetch %s failed: %s", code, rows)
                    minutes_by_code[code] = []
                    continue
                series = list(rows or [])
                self._minute_cache[code] = (now_ts, series)
                minutes_by_code[code] = series
        verdict["recommend"] = apply_minute_confirmations(rec, minutes_by_code)

    def apply_trend_override(self, code: str, verdict_flag: str) -> dict[str, Any]:
        """Persist a manual trend judgment and refresh recommend cards in-memory."""
        from market_desk.db import upsert_trend_override
        from market_desk.filters import normalize_code

        trade_date = self.snapshot.get("trade_date") or datetime.now(CN_TZ).strftime("%Y-%m-%d")
        c = normalize_code(code)
        row = upsert_trend_override(trade_date, c, verdict_flag)
        rec = ((self.snapshot.get("verdict") or {}).get("recommend")) or {}
        items = list(rec.get("items") or [])
        if items:
            overrides = load_trend_overrides(trade_date)
            # Re-apply using cached closes when available.
            closes_by_code: dict[str, list[float]] = {}
            fetch_ok_by_code: dict[str, bool] = {}
            for item in items:
                if item.get("kind") != "stock":
                    continue
                code_i = normalize_code(item.get("code"))
                if code_i in self._kline_cache:
                    closes_by_code[code_i] = list(self._kline_cache.get(code_i) or [])
                    fetch_ok_by_code[code_i] = bool(
                        self._kline_ok.get(code_i, bool(closes_by_code[code_i]))
                    )
                else:
                    closes_by_code[code_i] = []
                    fetch_ok_by_code[code_i] = False
            new_rec = apply_stock_daily_trends(
                rec, closes_by_code, fetch_ok_by_code, overrides
            )
            if self.snapshot.get("verdict"):
                self.snapshot["verdict"]["recommend"] = mark_pullback_entries(new_rec)
                self.snapshot["verdict"] = align_action_with_ready(self.snapshot["verdict"])
            try:
                record_session_signals(self.snapshot)
            except Exception:
                log.exception("signal record after trend override failed")
        return {"ok": True, "override": row, "recommend": ((self.snapshot.get("verdict") or {}).get("recommend"))}

    async def _yesterday(
        self,
        client: httpx.AsyncClient,
        now: datetime,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        cursor = now.date() - timedelta(days=1)
        for _ in range(10):
            if not is_trading_day(cursor):
                cursor -= timedelta(days=1)
                continue
            key = cursor.strftime("%Y%m%d")
            rows = await _safe(
                fetch_yesterday_zt, client, key, errors=errors, label=f"yzt-{key}"
            )
            if rows:
                return rows
            cursor -= timedelta(days=1)
        return []

    async def _ensure_index_bars(
        self,
        client: httpx.AsyncClient,
        trade_date: str,
        *,
        errors: list[str] | None = None,
    ) -> None:
        """Load 上证指数 daily OHLCV once per trade day for Elliott scenarios."""
        day = str(trade_date or "")[:10]
        want = 320  # Tencent fq kline soft cap; ~1.3y sessions
        if (
            day
            and day == self._index_bars_day
            and len(self._index_bars) >= max(80, want - 40)
        ):
            return
        bars = await _safe(
            fetch_daily_bars_symbol,
            client,
            "sh000001",
            want,
            errors=errors,
            label="index-k",
        )
        if bars and len(bars) >= 30:
            self._index_bars = list(bars)
            self._index_bars_day = day
        elif not self._index_bars:
            self._index_bars = list(bars or [])
            self._index_bars_day = day

    def _build_elliott(self, indices: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Attach multi-scenario Elliott readout for 上证指数."""
        last = None
        for row in indices or []:
            name = str(row.get("name") or "")
            code = str(row.get("code") or "")
            if code == "000001" or "上证" in name:
                last = row.get("price")
                break
        try:
            return build_elliott_scenarios(
                self._index_bars,
                index_name="上证指数",
                index_code="000001",
                last_price=float(last) if last not in (None, "") else None,
            )
        except Exception:
            log.exception("elliott scenarios failed")
            return {
                "ok": False,
                "standalone": True,
                "scenarios": [],
                "note": "波浪情景计算失败",
                "disclaimer": "波浪计数多解，仅观察；不改作战台买卖结论。",
            }

    def _auction(
        self, trade_date: str, now: datetime, quotes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        locked = load_auction(trade_date)
        hhmm = now.hour * 100 + now.minute
        if locked and hhmm >= 925:
            locked["locked"] = True
            return locked
        summary = auction_from_quotes(quotes)
        summary["locked"] = hhmm >= 925
        if hhmm >= 925 and quotes:
            save_auction(trade_date, summary)
        return summary

    async def _hot_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            boards,
            key=lambda b: (b.get("pct") or 0) * 2 + (b.get("up_count") or 0) * 0.02,
            reverse=True,
        )
        picked = ranked[:12]
        if not picked:
            return []
        enriched = await asyncio.gather(
            *[_enrich_board(client, board, ctx) for board in picked]
        )
        enriched = [x for x in enriched if x]
        enriched.sort(
            key=lambda b: (b.get("zt_n") or 0) * 4 + (b.get("pct") or 0),
            reverse=True,
        )
        return enriched[:HOT_BOARD_COUNT]

    async def _pin_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        hot: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        hot_names = {x["name"] for x in hot}
        industry = [b for b in boards if b.get("kind") == "industry"]
        out: list[dict[str, Any]] = []
        for label, aliases in PIN_INDUSTRY_ALIASES.items():
            match = _pick_pin_board(industry, aliases)
            if not match:
                continue
            match = dict(match)
            match["already_hot"] = match["name"] in hot_names
            card = await _enrich_board(client, match, ctx)
            card["pin_label"] = label
            out.append(card)
        return out

    async def _favorite_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Enrich personally favored boards from the live board universe."""
        if not rows:
            return []
        by_bk = {
            str(b.get("bk") or "").upper(): b
            for b in boards
            if b.get("bk")
        }
        # Cap enrich fan-out; keep newest favorites first (rows already DESC).
        picked = rows[:12]
        out: list[dict[str, Any]] = []
        for row in picked:
            bk = str(row.get("bk") or "").upper()
            if not bk:
                continue
            match = by_bk.get(bk)
            if not match:
                match = {
                    "bk": bk,
                    "name": row.get("name") or bk,
                    "kind": row.get("kind") or "industry",
                    "pct": None,
                    "amount": None,
                    "up_count": 0,
                    "down_count": 0,
                    "leader_name": "",
                    "leader_code": "",
                    "leader_pct": None,
                }
            card = await _enrich_board(client, dict(match), ctx)
            if not card:
                continue
            card["favorite_id"] = row.get("id")
            card["favorite_note"] = row.get("note") or ""
            card["in_favorite"] = True
            if row.get("note"):
                card["note"] = _join_board_note(card.get("note"), f"看好备注：{row.get('note')}")
            out.append(card)
        return out

    async def _ice_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        hot: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        picked = _rank_ice_boards(boards, {x["name"] for x in hot})
        if not picked:
            return []
        enriched = await asyncio.gather(
            *[_enrich_board(client, board, ctx, weakest=True) for board in picked]
        )
        cards = [x for x in enriched if x]
        cards.sort(
            key=lambda b: (
                0 if b.get("status") == "传染预警" else 1,
                b.get("pct") if b.get("pct") is not None else 0,
            )
        )
        return cards[:ICE_BOARD_COUNT]


def _pick_pin_board(
    industry: list[dict[str, Any]], aliases: tuple[str, ...]
) -> dict[str, Any] | None:
    """Prefer an exact industry name, then a clean prefix match."""
    exact = [b for b in industry if b["name"] in aliases]
    if exact:
        return max(exact, key=lambda b: b.get("pct") or 0)
    prefixed = [
        b
        for b in industry
        if any(b["name"].startswith(alias) for alias in aliases) and not b["name"].startswith("其他")
    ]
    if prefixed:
        return max(prefixed, key=lambda b: b.get("pct") or 0)
    contains = [
        b
        for b in industry
        if any(alias in b["name"] for alias in aliases) and not b["name"].startswith("其他")
    ]
    if contains:
        return max(contains, key=lambda b: b.get("pct") or 0)
    return None


def _mark_favorite_flags(
    cards: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    """Stamp in_favorite / favorite_id onto board cards."""
    by_bk = {
        str(r.get("bk") or "").upper(): r
        for r in rows
        if r.get("bk")
    }
    for card in cards:
        bk = str(card.get("bk") or "").upper()
        row = by_bk.get(bk)
        card["in_favorite"] = bool(row)
        if row:
            card["favorite_id"] = row.get("id")
            if row.get("note") and not card.get("favorite_note"):
                card["favorite_note"] = row.get("note")


def _join_board_note(base: Any, extra: str) -> str:
    """Append a short note fragment without duplicating text."""
    text = str(base or "").strip()
    bit = str(extra or "").strip()
    if not bit:
        return text
    if bit in text:
        return text
    return f"{text}；{bit}" if text else bit


def _rank_ice_boards(
    boards: list[dict[str, Any]], hot_names: set[str]
) -> list[dict[str, Any]]:
    """Pick the coldest industries; hot names are deprioritized but still allowed."""
    industry = [b for b in boards if b.get("kind") == "industry"]
    ranked = sorted(
        industry,
        key=lambda b: (
            1 if b.get("name") in hot_names else 0,
            b.get("pct") if b.get("pct") is not None else 0.0,
            -(b.get("down_count") or 0),
            b.get("up_count") or 0,
        ),
    )
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for board in ranked:
        name = board.get("name") or ""
        if not name or name in seen or name.startswith("其他"):
            continue
        seen.add(name)
        picked.append(board)
        if len(picked) >= ICE_BOARD_COUNT:
            break
    return picked


async def _enrich_board(
    client: httpx.AsyncClient,
    board: dict[str, Any],
    ctx: dict[str, Any],
    weakest: bool = False,
) -> dict[str, Any]:
    zt = ctx.get("zt") or []
    zb = ctx.get("zb") or []
    yzt = ctx.get("yzt") or []
    hist = (ctx.get("hist") or {}).get(board.get("bk") or "", [])
    members = await fetch_board_members(client, board["bk"], weakest=weakest)
    zt_by_code = {x["code"]: x for x in zt}
    zb_codes = {x["code"] for x in zb}
    yzt_codes = {x["code"] for x in yzt}
    zt_n = sum(1 for m in members if m["code"] in zt_by_code)
    dt_n = sum(1 for m in members if is_limit_down(m.get("name"), m.get("pct")))
    ranked_leaders = sorted(
        [zt_by_code[m["code"]] for m in members if m["code"] in zt_by_code],
        key=lambda x: int(x.get("boards") or 0),
        reverse=True,
    )
    leader = ranked_leaders[0] if ranked_leaders else None
    slot = ranked_leaders[1] if len(ranked_leaders) > 1 else None
    main_leader_ok = is_main_board(board.get("leader_code"))
    leader_name = (leader or {}).get("name") or (
        board.get("leader_name") if main_leader_ok else (members[0]["name"] if members else "")
    )
    leader_code = (leader or {}).get("code") or (
        board.get("leader_code") if main_leader_ok else (members[0]["code"] if members else "")
    )
    leader_boards = int((leader or {}).get("boards") or (1 if leader_code in zt_by_code else 0))
    tiandi = any(
        m["code"] in yzt_codes and (is_limit_down(m.get("name"), m.get("pct")) or m["code"] in zb_codes)
        for m in members
    )
    giveback = 0
    for m in members:
        high = m.get("high")
        price = m.get("price")
        if high and price and high > 0 and (high - price) / high >= 0.05 and (m.get("pct") or 0) < 1:
            giveback += 1
    zt_in_board = [zt_by_code[m["code"]] for m in members if m["code"] in zt_by_code]
    zb_n = sum(1 for m in members if m["code"] in zb_codes)
    explode_sum = sum(int(x.get("explode_count") or 0) for x in zt_in_board)
    late_seal_n = 0
    ladder = {
        "ge2": 0,
        "ladder_fill": 0.0,
        "ladder_gap": False,
        "rungs_filled": 0,
        "rungs_span": 0,
        "ladder_missing": 0,
    }
    try:
        from market_desk.sentiment import is_late_first_seal, ladder_stats

        late_seal_n = sum(1 for x in zt_in_board if is_late_first_seal(x.get("first_seal")))
        ladder = ladder_stats(zt_in_board)
    except Exception:
        late_seal_n = 0
    pct = board.get("pct") or 0
    card = dict(board)
    card["members"] = members[:5]
    card["pool"] = members
    card["zt_n"] = zt_n
    card["zb_n"] = zb_n
    card["explode_sum"] = explode_sum
    card["late_seal_n"] = late_seal_n
    card["ge2"] = ladder.get("ge2", 0)
    card["ladder_fill"] = ladder.get("ladder_fill", 0.0)
    card["ladder_gap"] = bool(ladder.get("ladder_gap"))
    card["rungs_filled"] = ladder.get("rungs_filled", 0)
    card["ladder_missing"] = ladder.get("ladder_missing", 0)
    card["dt_n"] = dt_n
    card["leader_name"] = leader_name
    card["leader_code"] = leader_code
    card["leader_boards"] = leader_boards
    card["slot_name"] = (slot or {}).get("name")
    card["slot_code"] = (slot or {}).get("code")
    card["slot_boards"] = int((slot or {}).get("boards") or 0)
    card["ice"] = weakest
    if weakest:
        card["status"] = ice_status(
            pct, dt_n, int(board.get("up_count") or 0), int(board.get("down_count") or 0)
        )
    else:
        card["status"] = board_status(pct, zt_n, leader_boards)
    ice = weakest or card["status"] in ("冰点", "冷冻", "相对最冷", "传染预警")
    contagion = card["status"] == "传染预警"
    flags = cycle_flags(
        pct,
        zt_n,
        leader_boards,
        dt_n=dt_n,
        ice=ice,
        contagion=contagion,
        tiandi=tiandi,
        hist=hist,
        giveback=giveback,
    )
    headline, tone = board_headline(card["status"], flags, ice)
    card["flags"] = flags
    card["tags"] = board_cycle_tags(flags, ice=ice)
    card["headline"] = headline
    card["tone"] = tone
    card["cluster"] = cluster_path(hist, zt_n)
    card["spark"] = spark_values(hist, zt_n)
    card["hist"] = hist
    card["note"] = board_note(flags, leader_name, leader_boards, card.get("slot_name"), ice)
    card["focus"] = round(zt_n / max(len(zt), 1) * 100.0, 1) if zt else 0.0
    return card


def _board_etf_codes(boards: list[dict[str, Any]] | None) -> list[str]:
    """Collect exact + soft-mapped carrier ETF codes from board cards."""
    out: list[str] = []
    for board in boards or []:
        name = str(board.get("name") or "")
        spec = etf_spec_for_name(name) or etf_spec_soft_fallback(name)
        if not spec:
            continue
        code = normalize_code(spec[1])
        if code:
            out.append(code)
    return list(dict.fromkeys(out))


async def _safe(fn, *args, errors: list[str], label: str):
    try:
        return await fn(*args)
    except Exception as exc:
        log.warning("%s: %s", label, exc)
        errors.append(f"{label}: {exc}")
        return []


def _minutes(now: datetime) -> int:
    """Return minutes since midnight for session-window checks."""
    return now.hour * 60 + now.minute


def _decorate_watchlist(
    rows: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach live quote fields onto personal watchlist rows."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = str(item.get("code") or "").zfill(6)
        q = quotes.get(code) or {}
        item["code"] = code
        item["name"] = item.get("name") or q.get("name") or code
        item["last"] = q.get("price")
        item["last_pct"] = q.get("pct")
        out.append(item)
    return out


def _decorate_segments(
    rows: list[dict[str, Any]],
    current_key: str | None,
) -> list[dict[str, Any]]:
    """Merge saved segment rows into a fixed morning→afternoon strip."""
    from market_desk.session import SEGMENT_LABELS, SEGMENT_ORDER

    by = {str(r.get("segment")): dict(r) for r in rows or []}
    out: list[dict[str, Any]] = []
    for key in SEGMENT_ORDER:
        row = by.get(key) or {
            "segment": key,
            "label": SEGMENT_LABELS.get(key, key),
            "action": None,
            "mainline": "",
            "phase": "",
            "reason": "",
            "size_hint": "",
            "updated_at": None,
        }
        row["label"] = row.get("label") or SEGMENT_LABELS.get(key, key)
        row["current"] = key == current_key
        row["filled"] = bool(row.get("action"))
        out.append(row)
    return out


def _is_weekday(now: datetime) -> bool:
    """Return True for Monday–Friday in the given local time."""
    return now.weekday() < 5


def _is_session(now: datetime) -> bool:
    """Return True during A-share continuous auction on a trading day."""
    if not is_trading_day(now):
        return False
    minutes = _minutes(now)
    morning = 9 * 60 + 15 <= minutes <= 11 * 60 + 30
    afternoon = 13 * 60 <= minutes <= 15 * 60 + 5
    return morning or afternoon


def _build_health(
    now: datetime,
    errors: list[str],
    updated_at: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a visible data-health strip for the dashboard banner."""
    failed = list(errors or [])
    live = bool(payload.get("live"))
    trading = bool(payload.get("trading_day"))
    tips: list[str] = []
    score = 100
    if not trading:
        tips.append("今日非交易日（周末或节假日），不刷盘中行情")
        score -= 5
    if failed:
        score -= min(60, 15 * len(failed))
        tips.append("行情源异常：" + "、".join(failed[:6]))
    if live and not (payload.get("etfs") or []):
        score -= 15
        tips.append("ETF 报价为空")
    if live and not (payload.get("hot_boards") or []):
        score -= 15
        tips.append("热点板块为空")
    if live and not (payload.get("indices") or []):
        score -= 10
        tips.append("指数为空")
    stale_sec = None
    if updated_at:
        try:
            ts = datetime.strptime(updated_at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
            stale_sec = max(0, int((now - ts).total_seconds()))
            if live and stale_sec > int(setting("refresh_seconds", 20)) * 3:
                score -= 20
                tips.append(f"快照偏旧约 {stale_sec}s")
        except Exception:
            stale_sec = None
    score = max(0, min(100, score))
    level = "ok" if score >= 80 else ("warn" if score >= 50 else "bad")
    return {
        "score": score,
        "level": level,
        "trading_day": trading,
        "failed_sources": failed,
        "stale_seconds": stale_sec,
        "tips": tips,
    }


def _event_line(phase: str, metrics: dict[str, Any], hot: list[dict[str, Any]]) -> str:
    top = hot[0]["name"] if hot else "—"
    leader = metrics.get("leader") or {}
    return f"{phase} · 最高{metrics['height']}板 · 热点{top} · {leader.get('name') or '—'}"


def _contagion(ice: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag clustered limit-downs inside the coldest industries."""
    hits = [b for b in ice if (b.get("dt_n") or 0) >= 2 or b.get("status") == "传染预警"]
    if not hits:
        cold = ice[0] if ice else None
        return {
            "on": False,
            "text": (
                f"冰点观察 {cold.get('name')} {(cold.get('pct') or 0):+.2f}% · 暂无板块级传染"
                if cold
                else "暂无板块级跌停传染"
            ),
        }
    board = hits[0]
    return {
        "on": True,
        "name": board.get("name"),
        "dt_n": board.get("dt_n") or 0,
        "text": f"{board.get('name')} {board.get('dt_n') or 0} 家靠近跌停 · 传染预警 · 非买点",
    }


def _today_events(
    phase: str,
    metrics: dict[str, Any],
    hot: list[dict[str, Any]],
    zt: list[dict[str, Any]],
    ice: list[dict[str, Any]] | None = None,
    contagion: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    leader = metrics.get("leader") or {}
    top = hot[0] if hot else {}
    cold = (ice or [None])[0] if ice else None
    items = [
        {"tone": "phase", "text": f"主板相位 {phase}，温度 {metrics.get('zt', 0)}涨停 / {metrics.get('dt', 0)}跌停"},
        {
            "tone": "lead",
            "text": f"高度 {metrics['height']} 板 · {leader.get('name') or '—'} {leader.get('code') or ''}",
        },
        {
            "tone": "hot",
            "text": f"实时热点 {top.get('name') or '—'} {top.get('pct', 0):+.2f}%"
            if top
            else "热点待刷新",
        },
        {
            "tone": "ice",
            "text": f"实时冰点 {cold.get('name')} {(cold.get('pct') or 0):+.2f}% · {cold.get('status')}"
            if cold
            else "冰点板块待刷新",
        },
        {
            "tone": "promo",
            "text": (
                f"昨停溢价 {metrics['premium']}% · 晋级 {metrics['promotion']}% "
                f"(1→2 {metrics.get('promo_1_2') if metrics.get('promo_1_2') is not None else '—'}% / "
                f"2→3 {metrics.get('promo_2_3') if metrics.get('promo_2_3') is not None else '—'}%) · "
                f"梯队 {metrics.get('ladder_fill') or 0}%"
                f"{'·断' if metrics.get('ladder_gap') else ''} · 炸板 {metrics['zb_rate']}%"
            ),
        },
    ]
    if contagion and contagion.get("on"):
        items.insert(0, {"tone": "warn", "text": contagion["text"]})
    if zt:
        banned = [x for x in zt if int(x.get("boards") or 0) >= 3]
        if banned:
            items.append(
                {
                    "tone": "warn",
                    "text": f"高位连板 {banned[0]['name']} {banned[0]['boards']}板 · 纪律禁追",
                }
            )
    return items


def _cycle_view(history: list[dict[str, Any]], today: str) -> dict[str, Any]:
    """Build a climax-relative timeline; attach daily snapshots so nodes are clickable."""
    ordered = list(reversed(history))
    by_date = {r.get("trade_date"): r for r in history if r.get("trade_date")}
    last_climax = None
    for row in ordered:
        if row.get("phase") == "高潮":
            last_climax = row.get("trade_date")
    dates = [r.get("trade_date") for r in ordered]
    offset = 0
    anchor_idx = -1
    if last_climax and last_climax in dates and today in dates:
        offset = dates.index(today) - dates.index(last_climax)
        anchor_idx = dates.index(last_climax)
    elif today in dates:
        offset = 0
        anchor_idx = dates.index(today)
    elif dates:
        offset = 0
        anchor_idx = len(dates) - 1

    nodes = []
    for i in range(-1, 10):
        trade_date = None
        row = None
        if anchor_idx >= 0:
            idx = anchor_idx + i
            if 0 <= idx < len(dates):
                trade_date = dates[idx]
                row = by_date.get(trade_date)
        is_now = i == offset
        detail = None
        if row:
            detail = {
                "trade_date": trade_date,
                "phase": row.get("phase"),
                "temperature": row.get("temperature"),
                "zt": row.get("zt"),
                "dt": row.get("dt"),
                "zb_rate": row.get("zb_rate"),
                "height": row.get("height"),
                "promotion": row.get("promotion"),
                "promo_1_2": row.get("promo_1_2"),
                "promo_2_3": row.get("promo_2_3"),
                "premium": row.get("premium"),
                "ladder_fill": row.get("ladder_fill"),
                "ladder_gap": row.get("ladder_gap"),
                "event": row.get("event"),
                "ups": row.get("ups"),
                "downs": row.get("downs"),
                "amount_yi": row.get("amount_yi"),
            }
        nodes.append(
            {
                "i": i,
                "current": is_now,
                "label": "今" if is_now else f"D{i:+d}",
                "trade_date": trade_date,
                "has_data": detail is not None,
                "detail": detail,
            }
        )
    return {
        "offset": offset,
        "last_climax": last_climax,
        "nodes": nodes,
        "note": (
            f"距上次高潮 {offset} 日 · 可点击日子查看当日摘要"
            if last_climax
            else "本地尚无高潮样本，先攒日级数据 · 可点有数据的日子"
        ),
    }


def _watch_pool(
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a compact intraday anomaly list (not a buy list)."""
    rows: list[dict[str, Any]] = []
    for item in sorted(zt, key=lambda x: int(x.get("boards") or 0), reverse=True)[:12]:
        rows.append(
            {
                **item,
                "group": "涨停",
                "tag": "禁追" if int(item.get("boards") or 0) >= 2 else "观察",
                "reason": f"{item.get('boards')}板涨停",
            }
        )
    for item in zb[:8]:
        rows.append({**item, "group": "炸板", "tag": "观察", "reason": "炸板"})
    dt = [q for q in quotes if is_limit_down(q.get("name"), q.get("pct"))]
    for item in dt[:6]:
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "pct": item["pct"],
                "group": "跌停",
                "tag": "观察",
                "reason": "跌停",
                "boards": 0,
            }
        )
    seen = {r["code"] for r in rows}
    hot_turn = sorted(
        [q for q in quotes if (q.get("turnover") or 0) >= 15 and q["code"] not in seen],
        key=lambda q: q.get("turnover") or 0,
        reverse=True,
    )
    for item in hot_turn[:6]:
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "pct": item["pct"],
                "group": "高换手",
                "tag": "禁追" if (item.get("pct") or 0) >= 7 else "观察",
                "reason": f"换手 {item.get('turnover'):.0f}%",
                "boards": 0,
            }
        )
    return rows[:24]


def _rough_watch_band(
    item: dict[str, Any],
    quote: dict[str, Any] | None = None,
) -> tuple[float | None, float | None, float | None]:
    """Derive a coarse suggest / stop / chase band for anomaly → watchlist."""
    q = quote or {}
    last = num(item.get("price") or item.get("last") or q.get("price"))
    if last is None or last <= 0:
        return None, None, None
    high = num(item.get("high") or q.get("high"))
    low = num(item.get("low") or q.get("low"))
    tag = str(item.get("tag") or "")
    group = str(item.get("group") or "")
    # Limit-up / chase tags: only watch a pullback; chase = last (do not chase).
    if tag == "禁追" or group == "涨停":
        suggest = round(last * 0.97, 2)
        stop = round((low if low and low < last else last * 0.94), 2)
        chase = round(last, 2)
        return suggest, stop, chase
    if group == "跌停":
        # Do not treat limit-down as a buy band; stop near last, chase slightly above.
        suggest = round(last * 1.01, 2)
        stop = round(last * 0.97, 2)
        chase = round(last * 1.03, 2)
        return suggest, stop, chase
    mid = (float(low) + float(last)) / 2.0 if low and low < last else last * 0.985
    suggest = round(min(float(last) * 0.985, mid), 2)
    stop = round(float(low) if low and low < last else last * 0.97, 2)
    chase = round(float(high) if high and high > last else last * 1.02, 2)
    if stop >= suggest:
        stop = round(suggest * 0.98, 2)
    if chase <= suggest:
        chase = round(suggest * 1.02, 2)
    return suggest, stop, chase


def _decorate_watch_pool(
    rows: list[dict[str, Any]],
    boards: list[dict[str, Any]] | None,
    mainline: str | None,
    watchlist_codes: set[str] | None = None,
    quotes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach board / mainline hints, rough price bands, and watchlist membership."""
    from market_desk.review import compare_boards_to_mainline, lookup_code_boards

    wl = watchlist_codes or set()
    qmap = {
        normalize_code(q.get("code")): q
        for q in (quotes or [])
        if normalize_code(q.get("code"))
    }
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = dict(row)
        code = normalize_code(item.get("code"))
        names = lookup_code_boards(code, boards)
        cmp = compare_boards_to_mainline(names, mainline)
        item["board_names"] = cmp["boards"]
        item["board_text"] = cmp["board_text"] if names else ""
        item["vs_mainline"] = cmp["vs_mainline"] if names else None
        item["board_match"] = cmp["match"]
        item["in_watchlist"] = code in wl
        suggest, stop, chase = _rough_watch_band(item, qmap.get(code))
        item["suggest_price"] = suggest
        item["stop_price"] = stop
        item["chase_price"] = chase
        out.append(item)
    return out


engine = DeskEngine()