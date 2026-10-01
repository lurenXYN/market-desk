"""Board-source flip guard: damping window after a flip and the lunch-break probe."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from market_desk.calendar import is_trading_day
from market_desk.config import BOARDS_LUNCH_PROBE_SEC, SOURCE_SHIFT_DAMP_SEC

log = logging.getLogger("market_desk")


class SourceGuardMixin:
    """Track the hot-board source across refresh rounds and damp its flips."""

    def _track_boards_source(
        self,
        now: datetime,
        previous: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Note the current board source and return (state, toast replay base).

        A flip opens a ``SOURCE_SHIFT_DAMP_SEC`` window. The snapshot published
        right before the first flip is kept as the toast replay base and handed
        back exactly once, on the first round after the window closes.
        """
        from market_desk.eastmoney import boards_switch_stats

        stats = boards_switch_stats()
        source = str(stats.get("source") or "")
        now_ts = now.timestamp()
        last = self._src_last
        if last and source and source != last:
            if now_ts >= self._src_damp_until or self._src_damp_base is None:
                self._src_damp_base = previous if (previous or {}).get("ok") else None
                self._src_shift_from = last
            self._src_damp_until = now_ts + float(SOURCE_SHIFT_DAMP_SEC)
            log.warning("boards source flip %s -> %s: damping %.0fs", last, source, SOURCE_SHIFT_DAMP_SEC)
        if source:
            self._src_last = source
        damping = now_ts < self._src_damp_until
        replay_base: dict[str, Any] | None = None
        if not damping and self._src_damp_base is not None:
            replay_base = self._src_damp_base
            self._src_damp_base = None
        state = {
            "source": source,
            "damping": damping,
            "damp_left": int(max(0.0, self._src_damp_until - now_ts)) if damping else 0,
            "shift_from": self._src_shift_from if damping else "",
            "switches": int(stats.get("switches") or 0),
            "log": list(stats.get("log") or []),
            "locked": bool(stats.get("locked")),
            "alias_learn": dict(getattr(self, "_alias_progress", None) or {}),
        }
        return state, replay_base

    async def _maybe_probe_boards(self, now: datetime) -> None:
        """Re-probe East Money boards at lunch while the source is Sina.

        The refresh loop idles from 11:30 to 13:00, so without this probe a
        morning degrade would keep the afternoon locked on Sina.
        """
        if not is_trading_day(now):
            return
        minutes = now.hour * 60 + now.minute
        if not (11 * 60 + 30 < minutes < 13 * 60):
            return
        from market_desk.eastmoney import boards_switch_stats, probe_boards_source

        if boards_switch_stats().get("source") != "sina":
            return
        now_ts = now.timestamp()
        if now_ts - self._src_probe_at < float(BOARDS_LUNCH_PROBE_SEC):
            return
        self._src_probe_at = now_ts
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                source = await probe_boards_source(client)
            log.info("lunch boards probe -> %s", source)
        except Exception:
            log.exception("lunch boards probe failed")
