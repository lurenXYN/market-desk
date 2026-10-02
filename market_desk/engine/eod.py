"""End-of-day jobs, fund flow, morning brief, index bars, and Elliott view."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from datetime import timedelta as _timedelta
from typing import Any
import httpx
from market_desk.calendar import is_trading_day
from market_desk.db import (
    load_auction,
    load_daily,
    save_auction,
    save_daily,
    save_fund_flow_daily,
)
from market_desk.elliott import build_elliott_scenarios
from market_desk.fund_flow import build_fund_flow_board
from market_desk.eastmoney import fetch_board_fund_flow, fetch_yesterday_zt
from market_desk.sentiment import auction_from_quotes
from market_desk.tencent import fetch_index_daily_bars

from market_desk.engine.util import _minutes, _safe

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))
log = logging.getLogger("market_desk")


class EodMixin:
    """End-of-day jobs, fund flow, morning brief, index bars, and Elliott view."""

    def _ensure_eod_breadth(self, day: str) -> None:
        """Refill today's ups/downs/amount from last-good quotes when still empty.

        Runs once at the after-close tick so a full-day East Money 502 cannot leave
        a zeroed daily_snapshot row (the 2026-09-21 failure mode).
        """
        day_s = str(day or "").strip()[:10]
        if not day_s:
            return
        rows = {str(r.get("trade_date") or "")[:10]: r for r in load_daily(8)}
        cur = rows.get(day_s) or {}
        ups = int(cur.get("ups") or 0)
        downs = int(cur.get("downs") or 0)
        amt = float(cur.get("amount_yi") or 0)
        span = ups + downs
        degraded = bool(cur.get("breadth_degraded")) or (
            ups == 0 and downs == 0 and amt <= 0
        ) or (0 < span < 800)
        if not degraded:
            return

        patch: dict[str, Any] | None = None
        cached = self._last_good_breadth or {}
        cached_span = int(cached.get("ups") or 0) + int(cached.get("downs") or 0)
        if (
            str(cached.get("trade_date") or "")[:10] == day_s
            and cached_span >= 800
        ):
            patch = {
                "ups": cached.get("ups"),
                "downs": cached.get("downs"),
                "amount_yi": cached.get("amount_yi"),
                "amount_pctile": cached.get("amount_pctile"),
                "big_drop": cached.get("big_drop"),
                "breadth_degraded": False,
                "breadth_eod_refill": True,
            }
        else:
            try:
                from market_desk.eastmoney import MAIN_QUOTES_BREADTH_MIN, cached_main_quotes
            except Exception:
                cached_main_quotes = None  # type: ignore[assignment]
                MAIN_QUOTES_BREADTH_MIN = 800  # type: ignore[misc]
            quotes = cached_main_quotes() if cached_main_quotes else []
            valid = [q for q in quotes if q.get("pct") is not None]
            if len(valid) >= int(MAIN_QUOTES_BREADTH_MIN):
                u = sum(1 for q in valid if (q.get("pct") or 0) > 0)
                d = sum(1 for q in valid if (q.get("pct") or 0) < 0)
                amount = sum(float(q.get("amount") or 0) for q in quotes)
                big = sum(1 for q in valid if (q.get("pct") or 0) <= -5.0)
                patch = {
                    "ups": u,
                    "downs": d,
                    "amount_yi": round(amount / 1e8, 1),
                    "big_drop": big,
                    "breadth_degraded": False,
                    "breadth_eod_refill": True,
                }
        if not patch:
            log.warning("eod breadth guard: no refill source for %s", day_s)
            return
        save_daily(day_s, patch)
        log.info(
            "eod breadth guard refilled %s ups=%s downs=%s amt=%s",
            day_s,
            patch.get("ups"),
            patch.get("downs"),
            patch.get("amount_yi"),
        )
        try:
            if isinstance(self.snapshot, dict):
                self.snapshot["history"] = load_daily(14)
                m = self.snapshot.get("metrics")
                if isinstance(m, dict):
                    m["ups"] = patch.get("ups")
                    m["downs"] = patch.get("downs")
                    m["amount_yi"] = patch.get("amount_yi")
        except Exception:
            pass

    async def _run_ma_fan_scan(
        self,
        day: str,
        *,
        slice_spec: tuple[int, int, str] | None = None,
        force_all: bool = False,
        claimed: bool = False,
    ) -> dict[str, Any]:
        """Run one due MA-fan liquidity slice (or all slices when forced)."""
        from market_desk.ma_fan import run_ma_fan_all_due_slices
        from market_desk.settings import setting as _setting

        day_s = str(day or "").strip()[:10]
        top = int(_setting("ma_fan_top", 80) or 80)
        min_amt = float(_setting("ma_fan_min_amount_yi", 1.2) or 1.2)
        boards = str(_setting("ma_fan_boards", "all") or "all")
        boards = boards if boards in ("main", "growth", "all") else "all"
        top_n = max(20, min(top, 120))
        min_yi = max(0.5, min_amt)
        min_price = float(_setting("ma_fan_min_price", 0) or 0)
        prefer_main = bool(_setting("ma_fan_prefer_main", False))
        spec = None
        if slice_spec and not force_all:
            offset, count, key = slice_spec
            spec = (int(offset), int(count), str(key))
        out = await run_ma_fan_all_due_slices(
            trade_date=day_s,
            minutes=22 * 60,
            top=top_n,
            min_amount_yi=min_yi,
            boards=boards,
            force_all=bool(force_all),
            snapshot=self.snapshot,
            min_price=min_price,
            prefer_main=prefer_main,
            slice_spec=spec,
            claimed=claimed,
        )
        if out.get("ok") and not out.get("skipped"):
            try:
                from market_desk.ma_fan import refresh_ma_fan_extras

                await refresh_ma_fan_extras(force=True)
            except Exception:
                log.exception("ma_fan extras after scan failed day=%s", day_s)
        return out

    async def _write_eod_onepager(self, day: str) -> None:
        """Persist the end-of-day one-pager and push ServerChan once per day.

        Market-level brief is stored under ``eod:{date}``. Eligible users with
        Server酱 enabled get a personalized body (own books) at most once.
        Push body uses curated ``push_bullets`` (P&L first, ~8 lines).
        """
        from market_desk.db import (
            list_serverchan_recipients,
            load_exec_diary,
            load_setting,
            save_setting,
        )
        from market_desk.notify import format_serverchan_desp, notify_serverchan
        from market_desk.report import build_eod_onepager

        day_s = str(day or "").strip()[:10]
        if not day_s:
            return
        review = await self.build_review(view_date=day_s)
        brief = build_eod_onepager(snapshot=self.snapshot, review=review)
        store_key = f"eod:{day_s}"
        prev = load_setting(store_key)
        if not (isinstance(prev, dict) and prev.get("markdown")):
            save_setting(
                store_key,
                {
                    "title": brief.get("title"),
                    "focus": brief.get("focus"),
                    "bullets": brief.get("bullets"),
                    "push_bullets": brief.get("push_bullets"),
                    "markdown": brief.get("markdown"),
                    "date": day_s,
                    "as_of": brief.get("as_of"),
                    "saved_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            log.info("eod onepager saved %s", day_s)

        push_flag = f"eod_pushed:{day_s}"
        if load_setting(push_flag):
            return
        try:
            recipients = list_serverchan_recipients()
        except Exception:
            log.exception("list serverchan recipients for eod failed")
            recipients = []
        title = str(brief.get("title") or f"收盘一页纸 · {day_s}")
        ok_n = 0
        for user in recipients:
            sendkey = str(user.get("serverchan_sendkey") or "").strip()
            if not sendkey:
                continue
            uid = user.get("id")
            snap = self.snapshot
            ubrief = brief
            try:
                if uid is not None:
                    snap = self.snapshot_for_user(int(uid))
                    urev = await self.build_review(view_date=day_s, user_id=int(uid))
                    diary = load_exec_diary(user_id=int(uid), trade_date=day_s, limit=40)
                    ubrief = build_eod_onepager(
                        snapshot=snap, review=urev, diary=diary
                    )
            except Exception:
                log.exception("eod personal brief failed user=%s", uid)
            push_list = ubrief.get("push_bullets")
            if not isinstance(push_list, list) or not push_list:
                raw = [str(b) for b in (ubrief.get("bullets") or []) if b]
                pnl = [b for b in raw if b.startswith(("今日盈亏", "纪律分"))]
                rest = [b for b in raw if not b.startswith(("今日盈亏", "纪律分"))]
                push_list = (pnl + rest)[:8]
            body_lines = [str(ubrief.get("focus") or "").strip()]
            for b in push_list[:8]:
                body_lines.append(f"· {b}")
            body = "\n".join(x for x in body_lines if x)
            alert_key = f"eod:{day_s}"
            desp = format_serverchan_desp(alert_key, title, body, snap)
            if notify_serverchan(sendkey, title, desp):
                ok_n += 1
        save_setting(
            push_flag,
            {
                "ok_n": ok_n,
                "at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        log.info("eod onepager push day=%s ok=%s", day_s, ok_n)

    async def _maybe_push_morning_brief(self, now: datetime) -> None:
        """Push morning decision brief once per day (09:25–09:50) when enabled."""
        from market_desk.config import SERVERCHAN_MORNING_PUSH

        if not SERVERCHAN_MORNING_PUSH or not is_trading_day(now):
            return
        mins = _minutes(now)
        # After auction lock window through early open.
        if mins < 9 * 60 + 25 or mins > 9 * 60 + 50:
            return
        day_s = now.strftime("%Y-%m-%d")
        if self._morning_push_date == day_s:
            return
        from market_desk.db import list_serverchan_recipients, load_setting, save_setting
        from market_desk.notify import format_serverchan_desp, notify_serverchan
        from market_desk.report import build_morning_brief
        from market_desk.settings import get_settings_for_user

        push_flag = f"morning_pushed:{day_s}"
        if load_setting(push_flag):
            self._morning_push_date = day_s
            return
        try:
            recipients = list_serverchan_recipients()
        except Exception:
            log.exception("list serverchan recipients for morning failed")
            recipients = []
        brief = (self.snapshot or {}).get("morning_brief") or build_morning_brief(
            self.snapshot
        )
        title = str(brief.get("title") or f"早决策 · {day_s}")
        focus = str(brief.get("focus") or "").strip()
        bullets = [str(b) for b in (brief.get("bullets") or []) if b][:8]
        body_lines = [focus] if focus else []
        for b in bullets:
            body_lines.append(f"· {b}")
        body = "\n".join(x for x in body_lines if x)
        if not body:
            return
        ok_n = 0
        for user in recipients:
            uid = user.get("id")
            sendkey = str(user.get("serverchan_sendkey") or "").strip()
            if not sendkey or uid is None:
                continue
            try:
                us = get_settings_for_user(int(uid))
                if not bool(us.get("morning_push")):
                    continue
            except Exception:
                continue
            alert_key = f"morning:{day_s}"
            desp = format_serverchan_desp(alert_key, title, body, self.snapshot)
            if notify_serverchan(sendkey, title, desp):
                ok_n += 1
        save_setting(
            push_flag,
            {
                "ok_n": ok_n,
                "at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        self._morning_push_date = day_s
        if ok_n:
            log.info("morning brief push day=%s ok=%s", day_s, ok_n)

    async def _refetch_eod_fund_flow(self, day: str) -> int:
        """Replace the hot-path day flow (top-80 inflow) with a fuller post-close pull.

        Keeps the hot-path rows when the full pull returns fewer rows (e.g. clist
        paused and only the Sina fallback answers). Returns rows now held.
        """
        from market_desk.config import EOD_FUND_FLOW_LIMIT

        errors: list[str] = []
        lim = int(EOD_FUND_FLOW_LIMIT)
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            ind, con = await asyncio.gather(
                _safe(fetch_board_fund_flow, client, "industry", lim, "day", errors=errors, label="eod-flow-hy"),
                _safe(fetch_board_fund_flow, client, "concept", lim, "day", errors=errors, label="eod-flow-gn"),
            )
        fresh = self._day_flow_fresh
        held = len(fresh[1]) + len(fresh[2]) if fresh and fresh[0] == day else 0
        full = len(ind or []) + len(con or [])
        if full > held:
            self._day_flow_fresh = (day, list(ind or []), list(con or []))
            return full
        return held

    def _save_eod_fund_flow(self, day: str) -> int:
        """Persist the post-close day board flow once per trade date; return rows written.

        Research data only (e.g. prior-day board leader / board inflow for pick-score
        tests); the funds tab keeps reading East Money directly and never loads it.
        """
        fresh = self._day_flow_fresh
        if not fresh or fresh[0] != day or not (fresh[1] or fresh[2]):
            log.warning("eod fund flow snapshot skipped: no fresh day flow for %s", day)
            return 0
        n = save_fund_flow_daily(day, list(fresh[1]) + list(fresh[2]))
        log.info("eod fund flow snapshot %s rows=%s", day, n)
        return n

    async def refresh_fund_flow(self, *, force: bool = False) -> dict[str, Any]:
        """Fetch East Money day/week/month fund-flow boards (funds tab on demand)."""
        import time

        now_m = time.monotonic()
        if (
            not force
            and self._fund_flow_full_at
            and now_m - self._fund_flow_full_at < 300
            and ((self.snapshot.get("fund_flow") or {}).get("full_ready"))
        ):
            return self.snapshot.get("fund_flow") or {}
        async with self._fund_flow_lock:
            if (
                not force
                and self._fund_flow_full_at
                and time.monotonic() - self._fund_flow_full_at < 300
                and ((self.snapshot.get("fund_flow") or {}).get("full_ready"))
            ):
                return self.snapshot.get("fund_flow") or {}
            trade_date_dash = str(
                self.snapshot.get("trade_date")
                or datetime.now(CN_TZ).strftime("%Y-%m-%d")
            )[:10]
            errors: list[str] = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                (
                    flow_ind_d, flow_con_d,
                    flow_ind_w, flow_con_w,
                    flow_ind_m, flow_con_m,
                ) = await asyncio.gather(
                    _safe(fetch_board_fund_flow, client, "industry", 80, "day", errors=errors, label="flow-hy-d"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "day", errors=errors, label="flow-gn-d"),
                    _safe(fetch_board_fund_flow, client, "industry", 80, "week", errors=errors, label="flow-hy-w"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "week", errors=errors, label="flow-gn-w"),
                    _safe(fetch_board_fund_flow, client, "industry", 80, "month", errors=errors, label="flow-hy-m"),
                    _safe(fetch_board_fund_flow, client, "concept", 80, "month", errors=errors, label="flow-gn-m"),
                )
            api_raw = {
                "day": {
                    "industry": list(flow_ind_d or []),
                    "concept": list(flow_con_d or []),
                },
                "week": {
                    "industry": list(flow_ind_w or []),
                    "concept": list(flow_con_w or []),
                },
                "month": {
                    "industry": list(flow_ind_m or []),
                    "concept": list(flow_con_m or []),
                },
            }
            from market_desk.db import list_fund_flow_dates, load_fund_flow_for_dates

            stored_dates = list_fund_flow_dates(trade_date_dash, limit=5)
            stored_rows = load_fund_flow_for_dates(stored_dates) if stored_dates else []
            fund_flow = build_fund_flow_board(
                api_raw,
                trade_date=trade_date_dash,
                stored_dates=stored_dates,
                stored_rows=stored_rows,
            )
            fund_flow["api_raw"] = api_raw
            fund_flow["full_ready"] = True
            fund_flow["refreshed_at"] = datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
            fund_flow["refreshed_kind"] = "full"
            if errors:
                fund_flow["warnings"] = errors
            self.snapshot["fund_flow"] = fund_flow
            self._fund_flow_full_at = time.monotonic()
            return fund_flow

    async def _yesterday(
        self,
        client: httpx.AsyncClient,
        now: datetime,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        """Fetch prior-session limit-ups with today's follow-through.

        East Money ``getYesterdayZTPool`` takes an *as-of* date (usually today):
        it returns the previous session's seal list scored as of that date.
        Passing yesterday's calendar day would load T-2 seals — wrong for the
        auction strategy tab.
        """
        cursor = now.date()
        for _ in range(10):
            key = cursor.strftime("%Y%m%d")
            rows = await _safe(
                fetch_yesterday_zt, client, key, errors=errors, label=f"yzt-{key}"
            )
            if rows:
                return rows
            cursor -= timedelta(days=1)
            while cursor.weekday() >= 5:
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
            fetch_index_daily_bars,
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
        if len(self._index_bars) < 30 and errors is not None:
            msg = "index-k: 上证日线不足，大盘波浪暂不可用"
            if msg not in errors:
                errors.append(msg)

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
