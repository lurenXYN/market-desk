"""Ops alerts, toast emission, and personal sell pushes."""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta as _timedelta
from typing import Any
from market_desk.calendar import is_trading_day
from market_desk.settings import setting
from market_desk.review import build_price_touch_alerts
from market_desk.notify import (
    build_damped_toast_alerts,
    filter_alerts_for_policy,
    is_buy_quiet_window,
    is_pre_match_window,
    notify_windows,
    push_serverchan_alerts,
    select_toasts_for_round,
)

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))
log = logging.getLogger("market_desk")


class AlertsMixin:
    """Ops alerts, toast emission, and personal sell pushes."""

    def _push_personal_sells(self, now: datetime) -> int:
        """Build each ServerChan user's sell cards and push new ready sells to them.

        The shared snapshot never carries personal books, so sell pushes must be
        evaluated per user here rather than in ``_emit_toasts``.
        """
        from market_desk.config import SERVERCHAN_EVENT_PUSH
        from market_desk.notify import is_sell_push_window, push_user_sell_alerts

        if not SERVERCHAN_EVENT_PUSH:
            return 0
        if not is_sell_push_window(now, trading_day=is_trading_day(now)):
            return 0
        from market_desk.db import list_serverchan_recipients

        trade_date = str((self.snapshot or {}).get("trade_date") or now.strftime("%Y-%m-%d"))
        if len(trade_date) == 8 and trade_date.isdigit():
            trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        ok_n = 0
        for user in list_serverchan_recipients():
            try:
                uid = int(user.get("id") or 0)
                if uid <= 0:
                    continue
                snap = self.snapshot_for_user(uid)
                if snap.get("personal_locked"):
                    continue
                ok_n += push_user_sell_alerts(user, snap, trade_date=trade_date)
            except Exception:
                log.exception("personal sell push failed user=%s", user.get("id"))
        return ok_n

    def _queue_ops_alert(
        self,
        key: str,
        title: str,
        body: str,
        *,
        day: str | None = None,
    ) -> None:
        """Queue a once-per-day ops toast (backup / health)."""
        trade_day = str(day or datetime.now(CN_TZ).strftime("%Y-%m-%d"))
        if trade_day != self._ops_latch_date:
            self._ops_latch_date = trade_day
            self._ops_latched.clear()
        if key in self._ops_latched:
            return
        self._ops_latched.add(key)
        self._pending_ops_alerts.append((key, title, body))

    def _maybe_ops_health_alert(self, payload: dict[str, Any]) -> None:
        """Fire once when health flips to degraded on a trading day."""
        health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
        if not health.get("degraded"):
            return
        day = str(payload.get("trade_date") or datetime.now(CN_TZ).strftime("%Y-%m-%d"))
        tips = list(health.get("tips") or [])
        body = "；".join(str(t) for t in tips[:3]) if tips else "行情源失败率偏高或快照偏旧"
        self._queue_ops_alert(
            f"ops:health:{day}",
            "数据降级",
            body[:200],
            day=day,
        )

    def _emit_toasts(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        *,
        damping: bool = False,
        replay_base: dict[str, Any] | None = None,
    ) -> None:
        """Fire page feed + optional Windows toasts for important transitions.

        ``damping`` / ``replay_base`` come from the board-source guard; see
        ``build_damped_toast_alerts``.
        """
        if not self._toast_armed:
            self._toast_armed = True
            # Still drain ops alerts on first armed round so backup fail is not lost.
            if self._pending_ops_alerts:
                pass
            else:
                return
        now = datetime.now(CN_TZ)
        now_ts = now.timestamp()
        day = str(current.get("trade_date") or "")
        if day and day != self._toast_latch_date:
            self._toast_latched.clear()
            self._toast_latch_date = day
        cooldown = int(setting("toast_cooldown", 180))
        alerts = build_damped_toast_alerts(
            previous, current, damping=damping, replay_base=replay_base
        )
        try:
            alerts.extend(build_price_touch_alerts(current))
        except Exception:
            log.exception("price-touch alerts failed")
        if self._pending_ops_alerts:
            alerts.extend(self._pending_ops_alerts)
            self._pending_ops_alerts = []
        alerts = filter_alerts_for_policy(
            alerts,
            pre_match=is_pre_match_window(now),
            decision_alerts=bool(setting("decision_alerts", True)),
            quiet_buy=is_buy_quiet_window(
                now,
                trading_day=bool(current.get("trading_day", True)),
                open_mute_minutes=int(setting("open_mute_minutes", 5)),
                tail_mute_minutes=int(setting("tail_mute_minutes", 30)),
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
            try:
                push_serverchan_alerts(chosen, current)
            except Exception:
                log.exception("serverchan fan-out failed")
