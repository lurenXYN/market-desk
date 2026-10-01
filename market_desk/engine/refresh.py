"""Background loop and the full refresh round that builds the snapshot."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timedelta as _timedelta
from typing import Any
import httpx
from market_desk.calendar import is_trading_day
from market_desk.backup_store import write_auto_backup
from market_desk.settings import setting
from market_desk.db import (
    load_all_book_codes,
    load_board_hist_map,
    load_daily,
    load_favorite_boards,
    load_mainline_switches,
    load_session_segments,
    purge_stale_closed_positions,
    save_board_daily,
    save_daily,
    try_add_mainline_switch,
    upsert_session_segment,
)
from market_desk.auction_scan import build_auction_strategy
from market_desk.emotion_wave import build_emotion_wave
from market_desk.fund_flow import attach_boards_fund_flow, build_fund_flow_board
from market_desk.theme_memory import (
    attach_board_affinity,
    reputation_map,
    settle_theme_reputation,
)
from market_desk.seasonality import build_seasonality
from market_desk.review import note_quote_ticks, record_session_signals
from market_desk.similar import build_similar_days
from market_desk.report import build_morning_brief
from market_desk.session import SEGMENT_ORDER, segment_snapshot_row, session_segment
from market_desk.eastmoney import (
    fetch_board_fund_flow,
    fetch_daily_klines_many,
    fetch_hot_boards,
    fetch_main_quotes,
    fetch_zb_pool,
    fetch_zt_pool,
)
from market_desk.filters import normalize_code
from market_desk.gap_fade import sync_gap_fade_blacklist
from market_desk.glossary import GLOSSARY
from market_desk.sentiment import (
    build_market_metrics,
    classify_phase,
    enrich_market_context,
    kpi_bars,
    score_temperature,
)
from market_desk.tencent import fetch_etfs, fetch_indices, fetch_quotes
from market_desk.verdict import (
    align_action_with_ready,
    apply_size_cap_gate,
    attach_board_etf_trends,
    attach_position_daily_trends,
    attach_switch_guard,
    build_deltas,
    build_desk_gate_summary,
    build_favorite_desk_plans,
    build_risk_overview,
    build_verdict,
    build_watch_trial_recommend,
    finalize_recommend_buy_ux,
    mark_pullback_entries,
    position_summary,
    reconfirm_recommend_ready,
)

from market_desk.engine.util import (
    _effective_refresh_seconds,
    _is_session,
    _minutes,
    _safe,
)
from market_desk.engine.boards import (
    _board_etf_codes,
    _mark_favorite_flags,
    _stable_mainline_lifecycle,
)
from market_desk.engine.watch import (
    _annotate_watchlist_observe,
    _decorate_segments,
    _decorate_watch_pool,
    _watch_pool,
)
from market_desk.engine.health import (
    _build_health,
    _contagion,
    _cycle_view,
    _event_line,
    _today_events,
)

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))
log = logging.getLogger("market_desk")


class RefreshMixin:
    """Background loop and the full refresh round that builds the snapshot."""

    async def _loop(self) -> None:
        first = True
        while True:
            now = datetime.now(CN_TZ)
            live = _is_session(now)
            today = now.strftime("%Y-%m-%d")
            after_close = (
                is_trading_day(now) and _minutes(now) >= 15 * 60 + 5 and self._eod_date != today
            )
            ma_fan_due = is_trading_day(now) and _minutes(now) >= 18 * 60
            if first or live or after_close:
                try:
                    await self.refresh()
                    if after_close:
                        self._eod_date = today
                        try:
                            self._ensure_eod_breadth(today)
                        except Exception:
                            log.exception("eod breadth guard failed")
                        try:
                            await self._refetch_eod_fund_flow(today)
                            self._save_eod_fund_flow(today)
                        except Exception:
                            log.exception("eod fund flow snapshot failed")
                        if bool(setting("auto_backup", True)):
                            try:
                                keep = int(setting("backup_keep", 30) or 30)
                                path = write_auto_backup(trade_date=today, keep=keep)
                                log.info("auto backup written %s", path)
                            except Exception as backup_exc:
                                log.exception("auto backup failed")
                                self._queue_ops_alert(
                                    f"ops:backup:{today}",
                                    "自动备份失败",
                                    f"{type(backup_exc).__name__}: {backup_exc}"[:180],
                                    day=today,
                                )
                        try:
                            await self._write_eod_onepager(today)
                        except Exception:
                            log.exception("eod onepager failed")
                    try:
                        await self._maybe_push_morning_brief(now)
                    except Exception:
                        log.exception("morning push failed")
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
            if ma_fan_due:
                try:
                    from market_desk.ma_fan import next_due_ma_fan_slice

                    due = next_due_ma_fan_slice(
                        trade_date=today, minutes=_minutes(now)
                    )
                    if due:
                        await self._run_ma_fan_scan(today, slice_spec=due)
                except Exception:
                    log.exception("ma_fan nightly scan failed")
            await asyncio.sleep(_effective_refresh_seconds(now) if live else int(setting("idle_seconds", 60)))

    async def refresh(self) -> None:
        """Pull public snapshots and rebuild the dashboard payload."""
        async with self._lock:
            now = datetime.now(CN_TZ)
            trade_date = now.strftime("%Y%m%d")
            trade_date_dash = now.strftime("%Y-%m-%d")
            errors: list[str] = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                # Hot path: day fund-flow only (week/month via /api/fund-flow).
                (
                    zt, zb, quotes, boards, etfs, indices,
                    flow_ind_d, flow_con_d,
                ) = await asyncio.gather(
                    _safe(fetch_zt_pool, client, trade_date, errors=errors, label="zt"),
                    _safe(fetch_zb_pool, client, trade_date, errors=errors, label="zb"),
                    _safe(fetch_main_quotes, client, errors=errors, label="quotes"),
                    _safe(fetch_hot_boards, client, errors=errors, label="boards"),
                    _safe(fetch_etfs, client, errors=errors, label="etf"),
                    _safe(fetch_indices, client, errors=errors, label="index"),
                    _safe(fetch_board_fund_flow, client, "industry", 80, "day", errors=errors, label="flow-hy-d"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "day", errors=errors, label="flow-gn-d"),
                )
                yesterday_zt = await self._yesterday(client, now, errors)
                zt = zt or []
                zb = zb or []
                quotes = quotes or []
                boards = boards or []
                etfs = etfs or []
                indices = indices or []
                yesterday_zt = yesterday_zt or []
                self._day_flow_fresh = (trade_date_dash, list(flow_ind_d or []), list(flow_con_d or []))
                prev_snap = self.snapshot or {}
                prev_flow = prev_snap.get("fund_flow") or {}
                prev_periods = (prev_flow.get("api_raw") or {}) if isinstance(prev_flow, dict) else {}
                # Sticky day flow: do not blank funds tab when clist cooldown returns [].
                if not (flow_ind_d or flow_con_d):
                    prev_day = (prev_periods.get("day") or {}) if isinstance(prev_periods, dict) else {}
                    flow_ind_d = list(prev_day.get("industry") or []) or flow_ind_d
                    flow_con_d = list(prev_day.get("concept") or []) or flow_con_d
                fund_flow = build_fund_flow_board(
                    {
                        "day": {
                            "industry": flow_ind_d or [],
                            "concept": flow_con_d or [],
                        },
                        "week": (prev_periods.get("week") or {"industry": [], "concept": []}),
                        "month": (prev_periods.get("month") or {"industry": [], "concept": []}),
                    },
                    trade_date=trade_date_dash,
                )
                fund_flow["api_raw"] = {
                    "day": {
                        "industry": list(flow_ind_d or []),
                        "concept": list(flow_con_d or []),
                    },
                    "week": (prev_periods.get("week") or {"industry": [], "concept": []}),
                    "month": (prev_periods.get("month") or {"industry": [], "concept": []}),
                }
                fund_flow["full_ready"] = bool(
                    (fund_flow.get("api_raw") or {}).get("week")
                    and (
                        ((fund_flow["api_raw"]["week"].get("industry")) or [])
                        or ((fund_flow["api_raw"]["week"].get("concept")) or [])
                    )
                )
                fund_flow["note"] = (
                    (fund_flow.get("note") or "")
                    + (" · 近5/10日东财榜可点「资金」页补齐" if not fund_flow.get("full_ready") else "")
                ).strip(" ·")
                fund_flow["refreshed_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                fund_flow["refreshed_kind"] = (
                    "full" if fund_flow.get("full_ready") else "day"
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
                # Sticky board cards: empty fetch (cooldown / edge blip) must not wipe tabs.
                if not boards and not hot_cards:
                    hot_cards = list(prev_snap.get("hot_boards") or [])
                    pin_cards = list(prev_snap.get("pin_boards") or []) or pin_cards
                    ice_cards = list(prev_snap.get("ice_boards") or []) or ice_cards
                    if hot_cards and "boards:sticky" not in errors:
                        errors.append("boards:sticky")
                fav_rows = load_favorite_boards()
                fav_cards = await self._favorite_cards(client, boards, fav_rows, ctx)
                if not fav_cards and fav_rows and not boards:
                    fav_cards = list(prev_snap.get("favorite_boards") or [])
                _mark_favorite_flags(hot_cards + pin_cards + ice_cards + fav_cards, fav_rows)
                purge_stale_closed_positions(trade_date_dash)
                # Multi-user: fetch marks for every book's codes; never load rows here.
                need_codes = load_all_book_codes()
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
            # Normalize keys; keep marks on the engine for snapshot_for_user.
            book_quotes: dict[str, dict[str, Any]] = {}
            for raw_code, q in pos_quote_map.items():
                code = normalize_code(raw_code) or str(raw_code or "").zfill(6)
                if code and isinstance(q, dict):
                    book_quotes[code] = dict(q)
                    book_quotes[code]["code"] = code
            self._book_quotes = book_quotes
            # Shared snap stays empty; personal layer decorates per user.
            positions: list[dict[str, Any]] = []
            watchlist: list[dict[str, Any]] = []

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
            # Full main-board breadth needs ~800+ names; 200-row emergency samples
            # must not write ups/downs (looks like 200/0 on the history table).
            from market_desk.eastmoney import MAIN_QUOTES_BREADTH_MIN

            sample_n = int(metrics.get("sample") or len(quotes) or 0)
            quotes_ok = sample_n >= int(MAIN_QUOTES_BREADTH_MIN)
            daily_payload: dict[str, Any] = {
                "phase": phase,
                "temperature": temperature,
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
                "hs300_pct": metrics.get("hs300_pct"),
                "cyb_pct": metrics.get("cyb_pct"),
                "event": _event_line(phase, metrics, hot_cards),
            }
            # Quote-derived breadth: skip thin/failed samples so merge keeps prior values.
            if quotes_ok:
                daily_payload.update(
                    {
                        "ups": metrics["ups"],
                        "downs": metrics["downs"],
                        "amount_yi": metrics["amount_yi"],
                        "amount_pctile": metrics.get("amount_pctile"),
                        "big_drop": metrics.get("big_drop"),
                        "breadth_degraded": False,
                        "breadth_sample": sample_n,
                    }
                )
                self._last_good_breadth = {
                    "trade_date": trade_date_dash,
                    "ups": metrics["ups"],
                    "downs": metrics["downs"],
                    "amount_yi": metrics["amount_yi"],
                    "amount_pctile": metrics.get("amount_pctile"),
                    "big_drop": metrics.get("big_drop"),
                    "sample": sample_n,
                }
            else:
                daily_payload["breadth_degraded"] = True
                daily_payload["breadth_sample"] = sample_n
                # Soft UI: reuse same-day last-good breadth so the strip is not 200/0.
                cached_b = self._last_good_breadth or {}
                if (
                    str(cached_b.get("trade_date") or "")[:10] == trade_date_dash
                    and int(cached_b.get("ups") or 0) + int(cached_b.get("downs") or 0)
                    >= int(MAIN_QUOTES_BREADTH_MIN)
                ):
                    metrics = dict(metrics)
                    metrics["ups"] = cached_b.get("ups")
                    metrics["downs"] = cached_b.get("downs")
                    if cached_b.get("amount_yi") is not None:
                        metrics["amount_yi"] = cached_b.get("amount_yi")
                    if cached_b.get("big_drop") is not None:
                        metrics["big_drop"] = cached_b.get("big_drop")
                    metrics["breadth_soft"] = True
            save_daily(trade_date_dash, daily_payload)
            history = load_daily(14)
            cycle = _cycle_view(history, trade_date_dash)
            similar = build_similar_days(
                phase=phase,
                temperature=temperature,
                metrics=metrics,
                history=history_prev,
                mainline=str(
                    ((((self.snapshot or {}).get("verdict") or {}).get("mainline") or {}).get("name"))
                    or ""
                ),
            )
            prev = self.snapshot if self.snapshot.get("ok") else None
            # Day-once daily closes: carrier ETFs + all book codes before mainline pick.
            etf_codes = _board_etf_codes(hot_cards + pin_cards + fav_cards)
            pos_codes = [c for c in (self._book_quotes or {}) if c]
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                await self._resolve_daily_closes(
                    client, list(dict.fromkeys(etf_codes + pos_codes)), trade_date_dash
                )
                board_trends = self._cached_trends_for(etf_codes)
                hot_cards = attach_board_etf_trends(hot_cards, board_trends)
                pin_cards = attach_board_etf_trends(pin_cards, board_trends)
                fav_cards = attach_board_etf_trends(fav_cards, board_trends)
                hot_cards = attach_boards_fund_flow(hot_cards, fund_flow)
                pin_cards = attach_boards_fund_flow(pin_cards, fund_flow)
                fav_cards = attach_boards_fund_flow(fav_cards, fund_flow)
                theme_settle: dict[str, Any] = {}
                try:
                    if self._theme_settle_date != trade_date_dash:
                        theme_settle = settle_theme_reputation(trade_date_dash) or {}
                        self._theme_settle_date = trade_date_dash
                except Exception:
                    log.exception("theme reputation settle failed")
                    theme_settle = {"ok": False}
                try:
                    rep = reputation_map()
                    hot_cards = attach_board_affinity(hot_cards, rep)
                    pin_cards = attach_board_affinity(pin_cards, rep)
                    fav_cards = attach_board_affinity(fav_cards, rep)
                except Exception:
                    log.exception("board affinity attach failed")
                    rep = {}
                # Prefetch OHLC for adapt same_day_plan remapping (soft follow).
                try:
                    from market_desk.adapt import set_adapt_bars
                    from market_desk.db import load_signals
                    from market_desk.settings import setting as _set

                    if bool(_set("adapt_follow_outcome", False)) and str(
                        _set("outcome_standard", "classic") or ""
                    ).strip().lower() == "same_day_plan":
                        sig_codes = [
                            normalize_code(r.get("code"))
                            for r in load_signals(limit=240)
                            if r.get("code")
                        ]
                        sig_codes = [c for c in dict.fromkeys(sig_codes) if c][:80]
                        if sig_codes:
                            packed = await fetch_daily_klines_many(
                                client, sig_codes, limit=40, concurrency=4
                            )
                            set_adapt_bars(packed or {})
                        else:
                            set_adapt_bars({})
                    else:
                        set_adapt_bars({})
                except Exception:
                    log.exception("adapt bars prefetch failed")
                    try:
                        from market_desk.adapt import set_adapt_bars

                        set_adapt_bars({})
                    except Exception:
                        pass
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
                # Refresh similar with live sticky mainline when available.
                ml_name = str(((verdict.get("mainline") or {}).get("name")) or "")
                if ml_name:
                    similar = build_similar_days(
                        phase=phase,
                        temperature=temperature,
                        metrics=metrics,
                        history=history_prev,
                        mainline=ml_name,
                    )
                    try:
                        # Patch mainline onto today's metrics row (merge, don't wipe).
                        save_daily(trade_date_dash, {"mainline": ml_name})
                    except Exception:
                        log.exception("save daily mainline failed")
                await self._apply_recommend_trends(client, verdict, trade_date_dash)
                await self._refine_independent_pullbacks(
                    client, verdict, trade_date_dash
                )
                # Soft ETF maps block_ready on the vehicle only; stocks may arm.
                # Holders enrich runs after first snapshot publish (Phase A).
                seg_v = verdict.get("segment") or {}
                bridge = verdict.get("auction_open_bridge") or {}
                block_arm = (
                    bool(seg_v.get("open_mute"))
                    or bool(verdict.get("auction_only"))
                    or bool(bridge.get("revoke_probe"))
                )
                verdict["recommend"] = mark_pullback_entries(
                    verdict.get("recommend"),
                    observe_only=False,
                    block_arm=block_arm,
                )
                verdict["side_recommend"] = mark_pullback_entries(
                    verdict.get("side_recommend"),
                    observe_only=True,
                    block_arm=block_arm,
                )
                verdict["link_recommend"] = mark_pullback_entries(
                    verdict.get("link_recommend"),
                    observe_only=True,
                    block_arm=block_arm,
                )
                if verdict.get("dragon_recommend"):
                    verdict["dragon_recommend"] = mark_pullback_entries(
                        verdict.get("dragon_recommend"),
                        observe_only=False,
                        block_arm=block_arm,
                    )
                if verdict.get("independent_recommend"):
                    verdict["independent_recommend"] = mark_pullback_entries(
                        verdict.get("independent_recommend"),
                        observe_only=True,
                        block_arm=block_arm,
                    )
                await self._apply_recommend_minutes(client, verdict)
                await self._prefetch_position_minutes(client, pos_codes)
                # Rematch near-entry after minutes so only minute.ok=True arms.
                verdict["recommend"] = mark_pullback_entries(
                    verdict.get("recommend"),
                    observe_only=False,
                    block_arm=block_arm,
                )
                verdict["side_recommend"] = mark_pullback_entries(
                    verdict.get("side_recommend"),
                    observe_only=True,
                    block_arm=block_arm,
                )
                verdict["link_recommend"] = mark_pullback_entries(
                    verdict.get("link_recommend"),
                    observe_only=True,
                    block_arm=block_arm,
                )
                if verdict.get("dragon_recommend"):
                    verdict["dragon_recommend"] = mark_pullback_entries(
                        verdict.get("dragon_recommend"),
                        observe_only=False,
                        block_arm=block_arm,
                    )
                if verdict.get("independent_recommend"):
                    verdict["independent_recommend"] = mark_pullback_entries(
                        verdict.get("independent_recommend"),
                        observe_only=True,
                        block_arm=block_arm,
                    )
                verdict = reconfirm_recommend_ready(verdict, metrics)
                verdict = apply_size_cap_gate(verdict, positions)
                aligned = align_action_with_ready(verdict)
                verdict.clear()
                verdict.update(aligned)
                bridge = verdict.get("auction_open_bridge") or {}
                seg_lock = (
                    bool(seg_v.get("open_mute"))
                    or bool(verdict.get("auction_only"))
                    or bool(bridge.get("revoke_probe"))
                )
                verdict["recommend"] = finalize_recommend_buy_ux(
                    verdict.get("recommend"),
                    block_arm=seg_lock,
                    allow_probe=True,
                )
                verdict["side_recommend"] = finalize_recommend_buy_ux(
                    verdict.get("side_recommend"),
                    block_arm=seg_lock,
                    allow_probe=False,
                )
                verdict["link_recommend"] = finalize_recommend_buy_ux(
                    verdict.get("link_recommend"),
                    block_arm=seg_lock,
                    allow_probe=False,
                )
                if verdict.get("dragon_recommend"):
                    verdict["dragon_recommend"] = finalize_recommend_buy_ux(
                        verdict.get("dragon_recommend"),
                        block_arm=seg_lock,
                        allow_probe=True,
                    )
                if verdict.get("independent_recommend"):
                    verdict["independent_recommend"] = finalize_recommend_buy_ux(
                        verdict.get("independent_recommend"),
                        block_arm=seg_lock,
                        allow_probe=False,
                    )
                desk_gate_summary = build_desk_gate_summary(verdict, phase=phase)
                watchlist = _annotate_watchlist_observe(watchlist, verdict)
                watch_trial = build_watch_trial_recommend(
                    watchlist,
                    playbook=verdict.get("playbook"),
                    adapt=verdict.get("adapt"),
                )
                if watch_trial:
                    for item in watch_trial.get("items") or []:
                        item["ready"] = False
                        if item.get("wait_price") is not None:
                            item["buy_price"] = item.get("wait_price")
                    watch_trial["buy"] = False
                verdict["watch_trial_recommend"] = watch_trial
                await self._filter_stock_recommends_by_zt_ytd(
                    client, verdict, trade_date_dash
                )
                pos_trends = self._cached_trends_for(pos_codes)
                positions = attach_position_daily_trends(positions, pos_trends)
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
                try:
                    verdict = attach_switch_guard(
                        verdict,
                        switches,
                        now=now,
                        hot=list(hot_cards) + list(pin_cards) + list(fav_cards),
                    )
                except Exception:
                    log.exception("attach_switch_guard failed")
                lc_hist = ctx.get("hist") if isinstance(ctx, dict) else None
                lifecycle = _stable_mainline_lifecycle(hot_cards, pin_cards, lc_hist, now=now)
                side_window = is_trading_day(now) and 9 * 60 + 25 <= _minutes(now) < 15 * 60
                if lifecycle.get("mode") == "frozen" and side_window:
                    try:
                        side = await self._lifecycle_side_cards(client, boards, lifecycle, ctx)
                        if side:
                            lifecycle = _stable_mainline_lifecycle(
                                hot_cards, pin_cards, lc_hist, now=now, side_live=side
                            )
                    except Exception:
                        log.exception("lifecycle side boards failed")
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
                    "refresh_seconds": _effective_refresh_seconds(now),
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
                        boards=list(hot_cards) + list(pin_cards) + list(fav_cards),
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
                    "theme_memory": {
                        "reputation": list((rep or {}).values())[:20],
                        "settle": theme_settle or {},
                    },
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
                    "desk_gate_summary": desk_gate_summary,
                    "session_segments": segments,
                    "mainline_switches": switches,
                    "mainline_lifecycle": lifecycle,
                    # Multi-user: shared snap never carries personal books.
                    "positions": [],
                    "position_summary": position_summary([]),
                    "risk_overview": build_risk_overview(
                        [],
                        size_cap_pct=float(
                            ((verdict.get("playbook") or {}).get("size_cap_pct") or 100)
                        ),
                    ),
                    "watchlist": [],
                    "stock_blacklist": blacklist_state,
                    "recent_toasts": list(self._toast_feed),
                    "sell_advice": {"items": [], "text": "", "title": ""},
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
                # Phase A: publish desk/verdict before slow favorite enrich.
                payload["favorite_desk"] = fav_desk
                payload["enrich_pending"] = True
                # Soft news-radar attach (never blocks / never changes buy gates).
                try:
                    from market_desk.news_radar import (
                        enrich_news_radar,
                        fetch_news_radar_export,
                    )
                    from market_desk.settings import setting as _setting

                    nr_on = bool(_setting("news_radar_enabled", False))
                    ml_name = str(((verdict.get("mainline") or {}).get("name")) or "")
                    if nr_on:
                        raw = await fetch_news_radar_export(
                            base_url=str(_setting("news_radar_url", "") or ""),
                            limit=8,
                        )
                        payload["news_radar"] = enrich_news_radar(
                            raw, mainline_name=ml_name, enabled=True
                        )
                    else:
                        payload["news_radar"] = enrich_news_radar(
                            None, mainline_name=ml_name, enabled=False
                        )
                except Exception:
                    log.exception("news_radar attach failed")
                payload["health"] = _build_health(now, errors, updated_at, payload)
                payload["deltas"] = build_deltas(payload, prev)
                payload["morning_brief"] = build_morning_brief(payload)
                try:
                    from market_desk.report import ready_cross_day_tip

                    payload["ready_cross_day"] = ready_cross_day_tip(payload)
                except Exception:
                    log.exception("ready cross-day tip failed")
                    payload["ready_cross_day"] = None
                try:
                    self._maybe_ops_health_alert(payload)
                except Exception:
                    log.exception("ops health alert failed")
                self._emit_toasts(prev, payload)
                try:
                    record_session_signals(payload)
                except Exception:
                    log.exception("signal record failed")
                self.snapshot = payload
                self._maybe_capture_pick_scores(now)
                try:
                    self._push_personal_sells(now)
                except Exception:
                    log.exception("personal sell push failed")

                # Phase B: slow enrich (holders / trial trends / favorite) after UI can paint.
                try:
                    await self._apply_recommend_holders(client, verdict)
                except Exception:
                    log.exception("recommend holders enrich failed")
                try:
                    await self._enrich_auction_strategy(client)
                except Exception:
                    log.exception("auction strategy enrich failed")
                wt = verdict.get("watch_trial_recommend")
                if wt and (wt.get("items") or []):
                    try:
                        await self._apply_watch_trial_trends(
                            client, wt, trade_date_dash
                        )
                        for item in wt.get("items") or []:
                            item["ready"] = False
                            if item.get("wait_price") is not None:
                                item["buy_price"] = item.get("wait_price")
                        wt["buy"] = False
                        verdict["watch_trial_recommend"] = wt
                        payload["verdict"] = verdict
                    except Exception:
                        log.exception("watch trial trends enrich failed")
                await self._apply_favorite_desk_trends(client, fav_desk, trade_date_dash)
                await self._apply_favorite_desk_holders(client, fav_desk)
                await self._apply_favorite_desk_minutes(client, fav_desk)
            payload["favorite_desk"] = fav_desk
            payload["verdict"] = verdict if isinstance(verdict, dict) else payload.get("verdict")
            payload["enrich_pending"] = False
            payload["health"] = _build_health(now, errors, updated_at, payload)
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
