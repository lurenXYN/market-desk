"""DeskEngine mixin: What-If gate net-value audit for the review board."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class CounterfactualMixin:
    """Lazy, cached counterfactual audit (daily klines + stored buy signals)."""

    async def build_counterfactual(self, days: int | None = None, strict: bool = True) -> dict[str, Any]:
        """Return the gate net-value ledger over the last ``days`` signal days.

        Cached for ``CF_CACHE_SEC`` per window; klines reuse the review cache.

        Args:
            days: Window in distinct signal trade days (default ``CF_WINDOW_DAYS``).
            strict: Drop the weakest touch-evidence tier (早盘).

        Returns:
            ``build_counterfactual_audit`` output plus ``ok`` / ``built_at``.
        """
        from datetime import datetime, timedelta, timezone

        from market_desk.config import CF_CACHE_SEC, CF_WINDOW_DAYS
        from market_desk.counterfactual import build_counterfactual_audit
        from market_desk.db import load_signals
        from market_desk.review.signals import _dragon_hide_from_review, is_buy_signal

        span = max(5, min(int(days or CF_WINDOW_DAYS), 60))
        cache: dict[Any, tuple[float, dict[str, Any]]] = getattr(self, "_cf_cache", None) or {}
        self._cf_cache = cache
        ckey = (span, bool(strict))
        hit = cache.get(ckey)
        now_m = time.monotonic()
        if hit and now_m - hit[0] < float(CF_CACHE_SEC):
            return {**hit[1], "cache_hit": True}
        try:
            rows = [
                r for r in load_signals(limit=4000)
                if is_buy_signal(r.get("signal_type")) and not _dragon_hide_from_review(r)
            ]
        except Exception:
            log.exception("counterfactual: load signals failed")
            return {"ok": False, "note": "信号读取失败", "gates": [], "summary": {}}
        days_sorted = sorted({str(r.get("trade_date") or "")[:10] for r in rows if r.get("trade_date")})
        keep = set(days_sorted[-span:])
        rows = [r for r in rows if str(r.get("trade_date") or "")[:10] in keep]
        codes = list(dict.fromkeys(str(r.get("code") or "").zfill(6) for r in rows if r.get("code")))
        klines: dict[str, Any] = {}
        if codes:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                    klines = await self._review_klines(client, codes, limit=max(40, span + 20))
            except Exception:
                log.exception("counterfactual: klines failed")
        out = build_counterfactual_audit(rows, klines, days=span, strict=bool(strict))
        out["ok"] = True
        out["built_at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
        out["kline_codes"] = f"{len(klines)}/{len(codes)}"
        cache[ckey] = (now_m, out)
        return out
