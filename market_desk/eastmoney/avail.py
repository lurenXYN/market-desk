"""East Money request accounting: hourly ok/fail counters, block transitions, push2 pacing."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from market_desk.config import EM_BLOCK_AFTER_FAILS, EM_PUSH2_MIN_GAP_SEC

try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    _CN_TZ = timezone(timedelta(hours=8))

log = logging.getLogger("market_desk.eastmoney")

_PUSH2_HOSTS = ("push2", "push2delay", "push2his")
_PATH_KINDS = (("/clist/", "clist"), ("/ulist", "ulist"), ("/kline/", "kline"), ("/trends2/", "trends"))

# Pending deltas keyed by (day, hour, family): [ok, fail, empty, last_err].
_HOURLY: dict[tuple[str, int, str], list[Any]] = {}
# Pending block / recover transitions not yet written to SQLite.
_EVENTS: list[dict[str, Any]] = []
# Live per-family state: up, consecutive fails, since, last error, today's totals.
_STATE: dict[str, dict[str, Any]] = {}
_STATE_DAY = ""
# Monotonic time of the next free push2 request slot.
_PACE_NEXT = 0.0


def _cn_now() -> datetime:
    return datetime.now(_CN_TZ)


def classify(url: str) -> str:
    """Map an East Money URL to an endpoint family such as ``push2/clist``.

    Families split by host (WAF rules differ per edge) and by API path kind.
    Non-push2 hosts collapse to their first label (``push2ex``, ``datacenter-web``).
    """
    parts = urlsplit(str(url))
    head = (parts.hostname or "").lower().split(".")[0]
    if head in _PUSH2_HOSTS:
        for key, kind in _PATH_KINDS:
            if key in parts.path:
                return f"{head}/{kind}"
        return f"{head}/other"
    return head or "unknown"


def _family_state(family: str) -> dict[str, Any]:
    return _STATE.setdefault(
        family, {"up": True, "fails": 0, "since": "", "err": "", "ok": 0, "fail": 0, "empty": 0}
    )


def _roll_day(day: str) -> None:
    """Reset today's per-family totals when the calendar day changes."""
    global _STATE_DAY
    if day == _STATE_DAY:
        return
    _STATE_DAY = day
    for st in _STATE.values():
        st.update({"ok": 0, "fail": 0, "empty": 0})


def note(family: str, ok: bool, err: str = "", *, now: datetime | None = None) -> None:
    """Record one request outcome and detect blocked / recovered transitions.

    Args:
        family: Endpoint family from :func:`classify`.
        ok: True when the edge answered with a non-error HTTP status.
        err: Short error text for failures.
        now: Override clock (tests).
    """
    cur = now or _cn_now()
    day = cur.strftime("%Y-%m-%d")
    _roll_day(day)
    cell = _HOURLY.setdefault((day, cur.hour, family), [0, 0, 0, ""])
    st = _family_state(family)
    stamp = cur.strftime("%Y-%m-%d %H:%M:%S")
    if ok:
        cell[0] += 1
        st["ok"] += 1
        st["fails"] = 0
        if not st["up"]:
            st.update({"up": True, "since": stamp})
            _EVENTS.append({"at": stamp, "family": family, "state": "ok", "err": ""})
            log.warning("east money %s recovered", family)
        return
    text = str(err or "")[:120]
    cell[1] += 1
    cell[3] = text
    st["fail"] += 1
    st["fails"] += 1
    st["err"] = text
    st["fail_ts"] = cur.timestamp()
    if st["up"] and st["fails"] >= int(EM_BLOCK_AFTER_FAILS):
        st.update({"up": False, "since": stamp})
        _EVENTS.append({"at": stamp, "family": family, "state": "blocked", "err": text})
        log.warning("east money %s blocked after %s failures: %s", family, st["fails"], text)


def note_empty(url: str, *, now: datetime | None = None) -> None:
    """Count an HTTP 200 answer whose ``data`` was null (soft block on list APIs)."""
    cur = now or _cn_now()
    day = cur.strftime("%Y-%m-%d")
    _roll_day(day)
    family = classify(url)
    _HOURLY.setdefault((day, cur.hour, family), [0, 0, 0, ""])[2] += 1
    _family_state(family)["empty"] += 1


async def _pace(url: str) -> None:
    """Space push2* request starts process-wide by ``EM_PUSH2_MIN_GAP_SEC``.

    Slots are reserved synchronously (no await between read and write), so
    concurrent coroutines queue up without a lock.
    """
    global _PACE_NEXT
    gap = float(EM_PUSH2_MIN_GAP_SEC)
    if gap <= 0 or classify(url).split("/")[0] not in _PUSH2_HOSTS:
        return
    now = time.monotonic()
    slot = max(now, _PACE_NEXT)
    _PACE_NEXT = slot + gap
    if slot > now:
        await asyncio.sleep(slot - now)


def _err_text(exc: BaseException) -> str:
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}"[:120] if msg else type(exc).__name__


async def em_get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET an East Money URL with push2 pacing and availability accounting.

    Transport errors are recorded and re-raised; HTTP status errors are recorded
    but the response is returned so callers keep their ``raise_for_status`` flow.
    """
    family = classify(url)
    await _pace(url)
    try:
        resp = await client.get(url, **kwargs)
    except Exception as exc:
        note(family, False, _err_text(exc))
        raise
    if resp.status_code >= 400:
        note(family, False, f"HTTP {resp.status_code}")
    else:
        note(family, True)
    return resp


def drain() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pop pending hourly deltas and transitions for persistence."""
    rows = [
        {"day": d, "hour": h, "family": f, "ok": c[0], "fail": c[1], "empty": c[2], "last_err": c[3]}
        for (d, h, f), c in _HOURLY.items()
    ]
    events = list(_EVENTS)
    _HOURLY.clear()
    _EVENTS.clear()
    return rows, events


def avail_state() -> dict[str, dict[str, Any]]:
    """Return a copy of the live per-family state for the health strip."""
    return {fam: dict(st) for fam, st in sorted(_STATE.items())}


def recently_blocked(url: str) -> bool:
    """Return True when ``url``'s family is blocked and last failed within ``EM_BLOCK_SKIP_SEC``.

    Only for callers that have another source: they skip the request instead of
    waiting on a known-dead edge. Once the last failure is older than the window
    the next call goes through again, which re-probes the family.
    """
    from market_desk.config import EM_BLOCK_SKIP_SEC

    st = _STATE.get(classify(url))
    if not st or st.get("up", True):
        return False
    return _cn_now().timestamp() - float(st.get("fail_ts") or 0.0) < float(EM_BLOCK_SKIP_SEC)


def blocked_families(within_sec: float | None = None) -> list[str]:
    """Families currently considered blocked (consecutive failures, no success since).

    Args:
        within_sec: When set, only report blocks whose last failure is this recent;
            a block with no traffic since is stale evidence and should be re-probed.
    """
    cutoff = None if within_sec is None else _cn_now().timestamp() - float(within_sec)
    return [
        fam
        for fam, st in sorted(_STATE.items())
        if not st.get("up") and (cutoff is None or float(st.get("fail_ts") or 0.0) >= cutoff)
    ]
