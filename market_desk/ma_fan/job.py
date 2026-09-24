"""Scan job control: outbound pacing, single-slot claim, progress and score cache."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any


# Outbound pacing. Sina rank pages and Tencent/East Money daily bars are free
# public endpoints that ban bursty clients; this layer is nightly-grade, so
# slow and steady beats fast: ~1000 codes take about 3–4 minutes.
MA_FAN_CONCURRENCY = 2
MA_FAN_MIN_INTERVAL_S = 0.2
MA_FAN_PAGE_DELAY_S = 0.5
MA_FAN_MAX_CONSECUTIVE_FAIL = 30
MA_FAN_FORCE_COOLDOWN_S = 600

# Fetch enough bars to cover the calendar year for 年内涨停; scoring keeps the
# original 120-bar window so pattern results do not shift.
MA_FAN_BARS_LIMIT = 260
MA_FAN_SCORE_BARS = 120

# Daily bars are final after the close, so evening rescans reuse per-code
# results instead of re-downloading ~1000 series.
MA_FAN_CACHE_FROM = (15, 5)
_PROGRESS: dict[str, Any] = {"running": False}
_LAST_FORCE_END = 0.0
_SCORE_CACHE: dict[str, tuple[str, dict[str, Any] | None, int | None]] = {}


def _score_cache_day(now: datetime | None = None) -> str | None:
    """Return today's cache key when bars are final (after the close), else None."""
    t = now or datetime.now()
    if (t.hour, t.minute) < MA_FAN_CACHE_FROM:
        return None
    return t.strftime("%Y-%m-%d")


def _score_cache_get(code: str) -> tuple[dict[str, Any] | None, int | None] | None:
    """Return a cached ``(pattern, zt_ytd)`` for today's post-close bars."""
    day = _score_cache_day()
    hit = _SCORE_CACHE.get(code) if day else None
    if not hit or hit[0] != day:
        return None
    return hit[1], hit[2]


def _score_cache_put(code: str, got: dict[str, Any] | None, zt_ytd: int | None) -> None:
    """Cache one code's pattern result; drops entries from earlier days."""
    day = _score_cache_day()
    if not day:
        return
    if _SCORE_CACHE and next(iter(_SCORE_CACHE.values()))[0] != day:
        _SCORE_CACHE.clear()
    _SCORE_CACHE[code] = (day, got, zt_ytd)


class _Pacer:
    """Space request starts at least ``interval`` seconds apart across tasks."""

    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, float(interval))
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        """Block until this caller may start its request."""
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next = now + self.interval


class _ScanStats:
    """Per-run counters; ``live`` runs mirror them into the shared progress."""

    def __init__(self, *, live: bool) -> None:
        self.live = live
        self.consecutive_fail = 0
        self.aborted = False
        self.total_fixed = False

    def phase(self, name: str) -> None:
        """Record the current phase (universe / bars)."""
        if self.live:
            _PROGRESS["phase"] = name
            if name == "bars" and "_tb" not in _PROGRESS:
                _PROGRESS["_tb"] = time.monotonic()

    def begin_slice(self, key: str, idx: int, n: int) -> None:
        """Record which liquidity slice is being scanned."""
        if self.live:
            _PROGRESS.update({"slice": key, "slice_idx": idx, "slice_n": n})

    def set_total(self, total: int) -> None:
        """Fix the overall code count so the bar spans every slice."""
        self.total_fixed = True
        if self.live:
            _PROGRESS["total"] = int(total)

    def add_total(self, n: int) -> None:
        """Grow the code count when slices are scanned one at a time."""
        if self.live and not self.total_fixed:
            _PROGRESS["total"] = int(_PROGRESS.get("total") or 0) + int(n)

    def record_fetch(self, ok: bool) -> None:
        """Count one bar fetch; trip ``aborted`` after a long failure streak."""
        if ok:
            self.consecutive_fail = 0
        else:
            self.consecutive_fail += 1
            if self.consecutive_fail >= MA_FAN_MAX_CONSECUTIVE_FAIL:
                self.aborted = True
        if self.live:
            _PROGRESS["done"] = int(_PROGRESS.get("done") or 0) + 1
            if not ok:
                _PROGRESS["fails"] = int(_PROGRESS.get("fails") or 0) + 1

    def record_hit(self) -> None:
        """Count one pattern hit."""
        if self.live:
            _PROGRESS["hits"] = int(_PROGRESS.get("hits") or 0) + 1


def try_claim_scan(kind: str, trade_date: str) -> bool:
    """Mark a scan as running; return False when another scan holds the slot.

    Call from the event loop before scheduling the task so a double click
    cannot start two scans.
    """
    if _PROGRESS.get("running"):
        return False
    _PROGRESS.clear()
    _PROGRESS.update(
        {
            "running": True,
            "kind": str(kind),
            "trade_date": str(trade_date or "")[:10],
            "phase": "queued",
            "slice": "",
            "slice_idx": 0,
            "slice_n": 0,
            "done": 0,
            "total": 0,
            "hits": 0,
            "fails": 0,
            "error": None,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "_t0": time.monotonic(),
        }
    )
    return True


def release_scan(*, error: str | None = None) -> None:
    """Mark the running scan finished (or failed) and start the force cooldown."""
    global _LAST_FORCE_END
    t0 = float(_PROGRESS.get("_t0") or time.monotonic())
    _PROGRESS.update(
        {
            "running": False,
            "phase": "error" if error else "done",
            "error": error,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_s": round(time.monotonic() - t0, 1),
        }
    )
    if _PROGRESS.get("kind") == "force":
        _LAST_FORCE_END = time.monotonic()


def force_cooldown_left() -> int:
    """Seconds until another manual full rescan is allowed (0 when ready)."""
    if not _LAST_FORCE_END:
        return 0
    left = MA_FAN_FORCE_COOLDOWN_S - (time.monotonic() - _LAST_FORCE_END)
    return max(0, int(left + 0.999))


def scan_progress() -> dict[str, Any]:
    """Public snapshot of the current / last scan with percent and ETA."""
    out = {k: v for k, v in _PROGRESS.items() if not str(k).startswith("_")}
    done = int(out.get("done") or 0)
    total = int(out.get("total") or 0)
    out["pct"] = round(min(100.0, done * 100.0 / total), 1) if total else 0.0
    if out.get("running"):
        now = time.monotonic()
        out["elapsed_s"] = round(now - float(_PROGRESS.get("_t0") or now), 1)
        bars_s = now - float(_PROGRESS.get("_tb") or now)
        out["eta_s"] = (
            int(bars_s / done * (total - done)) if done and total > done and bars_s > 0 else None
        )
    out["cooldown_s"] = force_cooldown_left()
    return out
