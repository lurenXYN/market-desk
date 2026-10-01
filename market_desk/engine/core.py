"""DeskEngine assembled from feature mixins, plus the process-wide singleton."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timedelta as _timedelta
from typing import Any
from market_desk.db import (
    init_db,
    load_favorite_boards,
    load_watchlist,
    purge_stale_closed_positions,
)
from market_desk.glossary import GLOSSARY
from market_desk.verdict import build_risk_overview, position_summary

from market_desk.engine.watch import _decorate_watchlist
from market_desk.engine.alerts import AlertsMixin
from market_desk.engine.cards import CardsMixin
from market_desk.engine.recommend import RecommendMixin
from market_desk.engine.review_build import ReviewMixin
from market_desk.engine.eod import EodMixin
from market_desk.engine.refresh import RefreshMixin

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))
log = logging.getLogger("market_desk")


class DeskEngine(AlertsMixin, CardsMixin, RecommendMixin, ReviewMixin, EodMixin, RefreshMixin):
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
        self._morning_push_date: str | None = None
        self._theme_settle_date: str | None = None
        self._ma_fan_date: str | None = None
        # Last successful quote-derived breadth for EOD refill when clist blanked.
        self._last_good_breadth: dict[str, Any] | None = None
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
        # Review daily-trend chips: fingerprint -> payload (calendar day + signal set).
        self._review_trend_cache: dict[str, dict[str, Any]] = {}
        # Review payloads: key -> (monotonic_ts, payload)
        self._review_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # Review daily klines for outcome compare: code -> (monotonic_ts, packed row)
        self._review_kline_cache: dict[str, tuple[float, Any]] = {}
        self._review_scored_at: float = 0.0
        self._fund_flow_full_at: float = 0.0
        self._fund_flow_lock = asyncio.Lock()
        # (trade_date, industry rows, concept rows) from the last refresh, before the sticky fallback.
        self._day_flow_fresh: tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None = None
        # Live marks for all users' position / watchlist codes (not in public snap).
        self._book_quotes: dict[str, dict[str, Any]] = {}
        # Rolling source ok/fail/timeout counters for the health strip.
        self._source_stats: dict[str, dict[str, int]] = {}
        # Ops edge alerts (backup fail / health degraded) — once per trade day.
        self._ops_latch_date: str | None = None
        self._ops_latched: set[str] = set()
        self._pending_ops_alerts: list[tuple[str, str, str]] = []

    def _note_source(self, label: str, *, ok: bool = False, timeout: bool = False) -> None:
        """Increment one source outcome counter for the data-health strip."""
        key = str(label or "src").strip() or "src"
        bucket = self._source_stats.setdefault(key, {"ok": 0, "fail": 0, "timeout": 0})
        if timeout:
            bucket["timeout"] = int(bucket.get("timeout") or 0) + 1
            bucket["fail"] = int(bucket.get("fail") or 0) + 1
        elif ok:
            bucket["ok"] = int(bucket.get("ok") or 0) + 1
        else:
            bucket["fail"] = int(bucket.get("fail") or 0) + 1

    def start(self) -> None:
        """Create tables and start the polling task."""
        init_db()
        try:
            from market_desk.backup_store import check_db_integrity

            integrity = check_db_integrity()
            self.snapshot["db_integrity"] = integrity
            if not integrity.get("ok") and not integrity.get("skipped"):
                log.error("desk.db integrity failed: %s", integrity.get("detail"))
                self._queue_ops_alert(
                    f"ops:db:{datetime.now().strftime('%Y-%m-%d')}",
                    "数据库损坏",
                    str(integrity.get("detail") or "integrity_check failed")[:180],
                )
        except Exception:
            log.exception("db integrity check failed")
        try:
            from market_desk.theme_memory import rebuild_all_theme_reputation

            mig = rebuild_all_theme_reputation()
            if not mig.get("skipped"):
                log.info(
                    "theme reputation formula v%s rebuilt %s themes",
                    mig.get("version"),
                    mig.get("rebuilt"),
                )
        except Exception:
            log.exception("theme reputation formula migration failed")
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the polling task."""
        if self._task:
            self._task.cancel()

    def sync_positions(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """Reload positions for one user (or clear personal fields when None)."""
        trade_date = str(self.snapshot.get("trade_date") or datetime.now().strftime("%Y%m%d"))
        trade_dash = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}" if len(trade_date) == 8 else trade_date[:10]
        purge_stale_closed_positions(trade_dash)
        if user_id is None:
            self.snapshot["positions"] = []
            self.snapshot["position_summary"] = position_summary([])
            self.snapshot["risk_overview"] = build_risk_overview([], size_cap_pct=100)
            self.snapshot["sell_advice"] = {"items": [], "text": ""}
            return []
        from market_desk.personal import attach_personal_layer

        layered = attach_personal_layer(
            self.snapshot,
            int(user_id),
            book_quotes=self._book_quotes,
            trends_for=self._cached_trends_for,
            decorate_watchlist=lambda rows, quotes, verdict: _decorate_watchlist(
                rows, quotes, verdict=verdict
            ),
            decorate_favorites=lambda rows: self._decorate_favorite_rows(rows),
            minutes_for=self._minutes_for_codes,
        )
        # Keep shared snapshot free of personal data; caller uses return / layered.
        return list(layered.get("positions") or [])

    def snapshot_for_user(self, user_id: int | None) -> dict[str, Any]:
        """Market snapshot plus optional personal layer for the logged-in user."""
        # Shallow copy is fine: we overwrite every personal key below.
        base = dict(self.snapshot or {})
        empty_sum = position_summary([])
        empty_risk = build_risk_overview([], size_cap_pct=100)
        # Always strip personal slots first (shared engine snap must not leak).
        base["positions"] = []
        base["watchlist"] = []
        base["favorite_boards"] = []
        base["position_summary"] = empty_sum
        base["risk_overview"] = empty_risk
        base["sell_advice"] = {"items": [], "text": "", "title": ""}
        verdict = dict(base.get("verdict") or {})
        verdict["watch_trial_recommend"] = None
        # size_cap / personal demotions should not stick from another user
        if "size_cap" in verdict:
            verdict = dict(verdict)
            verdict.pop("size_cap", None)
        base["verdict"] = verdict
        base["auth_required_personal"] = False
        base["personal_locked"] = False
        if user_id is None:
            base["auth_required_personal"] = True
            base["personal_locked"] = True
            base["auth_user"] = None
            return base
        from market_desk.auth import is_guest, public_user
        from market_desk.db import get_user_by_id

        row = get_user_by_id(int(user_id))
        pub = public_user(row)
        if is_guest(pub):
            # Guest shares an empty personal layer; no books / fills.
            base["personal_locked"] = True
            base["auth_user"] = pub
            return base
        from market_desk.personal import attach_personal_layer

        layered = attach_personal_layer(
            base,
            int(user_id),
            book_quotes=self._book_quotes,
            trends_for=self._cached_trends_for,
            decorate_watchlist=lambda rows, quotes, verdict: _decorate_watchlist(
                rows, quotes, verdict=verdict
            ),
            decorate_favorites=lambda rows: self._decorate_favorite_rows(rows),
            minutes_for=self._minutes_for_codes,
        )
        layered["personal_locked"] = False
        layered["auth_user"] = pub
        return layered

    def _minutes_for_codes(self, codes: list[str] | None) -> dict[str, list]:
        """Return cached minute series for sell soft-take gates (sync, no fetch)."""
        out: dict[str, list] = {}
        cache = getattr(self, "_minute_cache", None) or {}
        for raw in codes or []:
            code = str(raw or "").zfill(6)
            if not code:
                continue
            hit = cache.get(code)
            if hit and isinstance(hit, (tuple, list)) and len(hit) >= 2:
                out[code] = list(hit[1] or [])
            # Missing keys omitted → apply_sell_minute_gates soft-passes.
        return out

    def _decorate_favorite_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Attach live board cards onto favorite rows (user-scoped)."""
        cards = list(self.snapshot.get("favorite_boards") or [])
        by_bk = {str(c.get("bk") or "").upper(): c for c in cards if c.get("bk")}
        for pool in ("hot_boards", "pin_boards", "ice_boards"):
            for c in self.snapshot.get(pool) or []:
                bk = str(c.get("bk") or "").upper()
                if bk and bk not in by_bk:
                    by_bk[bk] = c
        out: list[dict[str, Any]] = []
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
                }
            card["fav"] = True
            card["fav_id"] = row.get("id")
            card["fav_note"] = row.get("note") or ""
            out.append(card)
        return out

    def sync_watchlist(
        self,
        quotes: dict[str, dict[str, Any]] | None = None,
        *,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Reload personal watchlist for one user."""
        if user_id is None:
            self.snapshot["watchlist"] = []
            return []
        qmap = dict(self._book_quotes or {})
        qmap.update(quotes or {})
        rows = _decorate_watchlist(
            load_watchlist(user_id=int(user_id)),
            qmap,
            verdict=self.snapshot.get("verdict"),
        )
        return rows

    def sync_favorite_boards(self, *, user_id: int | None = None) -> list[dict[str, Any]]:
        """Reload favored boards for one user."""
        if user_id is None:
            return []
        return self._decorate_favorite_rows(load_favorite_boards(user_id=int(user_id)))

    def slice_snapshot(self, view: str | None = None) -> dict[str, Any]:
        """Return a tab-scoped snapshot slice to cut bandwidth / DOM work."""
        snap = self.snapshot or {}
        view_key = str(view or "full").strip().lower() or "full"
        if view_key in ("", "full", "all"):
            return snap

        common = {
            "ok": snap.get("ok"),
            "error": snap.get("error"),
            "warnings": snap.get("warnings"),
            "updated_at": snap.get("updated_at"),
            "prev_updated_at": snap.get("prev_updated_at"),
            "trade_date": snap.get("trade_date"),
            "live": snap.get("live"),
            "polling": snap.get("polling"),
            "trading_day": snap.get("trading_day"),
            "refresh_seconds": snap.get("refresh_seconds"),
            "phase": snap.get("phase"),
            "temperature": snap.get("temperature"),
            "verdict": snap.get("verdict"),
            "desk_gate_summary": snap.get("desk_gate_summary"),
            "health": snap.get("health"),
            "enrich_pending": snap.get("enrich_pending"),
            "view": view_key,
            # Always attach glossary: review/pos/backtest also have 「?」 tips;
            # restoring last tab from localStorage must not leave GLOSSARY empty.
            "glossary": snap.get("glossary") or GLOSSARY,
        }
        by_view: dict[str, tuple[str, ...]] = {
            "boards": (
                "hot_boards",
                "pin_boards",
                "ice_boards",
                "favorite_boards",
                "contagion",
                "mainline_lifecycle",
                "theme_memory",
            ),
            "desk": (
                "morning_brief",
                "news_radar",
                "seasonality",
                "deltas",
                "favorite_desk",
                "session_segments",
                "mainline_switches",
                "positions",
                "position_summary",
                "risk_overview",
                "sell_advice",
                "recent_toasts",
                "filter",
                "theme_memory",
                "ready_cross_day",
                "health",
            ),
            "market": (
                "metrics",
                "kpis",
                "indices",
                "etfs",
                "auction",
                "emotion_wave",
                "seasonality",
                "elliott",
                "history",
                "cycle",
                "similar_days",
                "events",
                "theme_memory",
            ),
            "funds": ("fund_flow",),
            "watch": ("watch", "watchlist", "stock_blacklist"),
            "auction": ("auction", "auction_strategy"),
            "pos": (
                "positions",
                "position_summary",
                "risk_overview",
                "watchlist",
                "stock_blacklist",
                "sell_advice",
            ),
            "review": ("phase", "temperature"),
        }
        keys = by_view.get(view_key) or ()
        out = dict(common)
        for key in keys:
            if key in snap:
                out[key] = snap.get(key)
        return out


engine = DeskEngine()
