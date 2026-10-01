"""clist host rotation, backoff state, and shared JSON / pagination fetchers."""

from __future__ import annotations

from typing import Any
import asyncio
import logging
import httpx
from market_desk.config import EASTMONEY_UT, HTTP_HEADERS, ZT_UT

log = logging.getLogger("market_desk.eastmoney")


# Prefer push2 for clist; delay edge often returns 502 on quotes/boards.
_CLIST_HOSTS: tuple[str, ...] = (
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
)


_CLIST_HOST_PREF: str | None = None


_CLIST_SEM: asyncio.Semaphore | None = None


_CLIST_FAIL_STREAK = 0


_CLIST_BACKOFF_UNTIL = 0.0


_CLIST_BACKOFF_SEC = 45.0


_CLIST_BACKOFF_AFTER = 2


def _clist_sem() -> asyncio.Semaphore:
    """Limit concurrent clist calls to reduce edge 502 under pagination fan-out."""
    global _CLIST_SEM
    if _CLIST_SEM is None:
        _CLIST_SEM = asyncio.Semaphore(3)
    return _CLIST_SEM


def clist_in_backoff() -> bool:
    """True while East Money clist edges are cooling down after repeated 5xx."""
    import time

    return time.time() < _CLIST_BACKOFF_UNTIL


def clist_backoff_remaining() -> float:
    """Seconds left in the clist cooldown window (0 when idle)."""
    import time

    return max(0.0, _CLIST_BACKOFF_UNTIL - time.time())


def _note_clist_ok() -> None:
    global _CLIST_FAIL_STREAK
    _CLIST_FAIL_STREAK = 0


def _note_clist_fail() -> None:
    """Arm a short process-wide clist pause after consecutive total failures."""
    import time

    global _CLIST_FAIL_STREAK, _CLIST_BACKOFF_UNTIL
    _CLIST_FAIL_STREAK += 1
    if _CLIST_FAIL_STREAK >= _CLIST_BACKOFF_AFTER:
        _CLIST_BACKOFF_UNTIL = time.time() + _CLIST_BACKOFF_SEC
        _CLIST_FAIL_STREAK = 0


def _zt_url(path: str, trade_date: str, extra: str = "") -> str:
    return (
        f"https://push2ex.eastmoney.com/{path}"
        f"?ut={ZT_UT}&dpt=wz.ztzt&Pageindex=0&pagesize=100&date={trade_date}{extra}"
    )


def _clist_url(
    fs: str,
    pz: int = 100,
    pn: int = 1,
    extra_fields: str = "",
    po: int = 1,
    fid: str = "f3",
    *,
    host: str | None = None,
) -> str:
    fields = (
        "f12,f13,f14,f2,f3,f4,f5,f6,f8,f15,f16,f17,f18,f9,f20,"
        "f104,f105,f128,f140,f141,f136"
        + extra_fields
    )
    h = host or _CLIST_HOST_PREF or _CLIST_HOSTS[0]
    return (
        f"https://{h}/api/qt/clist/get"
        f"?pn={pn}&pz={pz}&po={po}&np=1&fltt=2&invt=2&fid={fid}"
        f"&ut={EASTMONEY_UT}&fs={fs}&fields={fields}"
    )


def _host_order() -> list[str]:
    """Prefer last-good host, then the rest."""
    pref = _CLIST_HOST_PREF
    if pref and pref in _CLIST_HOSTS:
        return [pref, *[h for h in _CLIST_HOSTS if h != pref]]
    return list(_CLIST_HOSTS)


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            resp = await client.get(url, headers=HTTP_HEADERS, timeout=25.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


async def _get_clist_json(
    client: httpx.AsyncClient,
    fs: str,
    *,
    pz: int = 100,
    pn: int = 1,
    extra_fields: str = "",
    po: int = 1,
    fid: str = "f3",
    host: str | None = None,
    bypass_backoff: bool = False,
) -> tuple[dict[str, Any], str]:
    """GET clist with multi-host failover. Return ``(payload, host_used)``.

    Heavy quote pagination arms a process-wide backoff. Light board / fund-flow
    calls pass ``bypass_backoff=True`` so a quotes edge blip does not blank the
    boards and funds tabs for the whole cooldown window.
    """
    global _CLIST_HOST_PREF
    if clist_in_backoff() and host is None and not bypass_backoff:
        raise RuntimeError(
            f"clist backoff {clist_backoff_remaining():.0f}s"
        )
    hosts = [host] if host else _host_order()
    last_error: Exception | None = None
    for h in hosts:
        url = _clist_url(
            fs, pz=pz, pn=pn, extra_fields=extra_fields, po=po, fid=fid, host=h
        )
        for attempt in range(2):
            try:
                async with _clist_sem():
                    resp = await client.get(url, headers=HTTP_HEADERS, timeout=20.0)
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise RuntimeError("clist non-dict payload")
                _CLIST_HOST_PREF = h
                _note_clist_ok()
                return data, h
            except httpx.HTTPStatusError as exc:
                last_error = exc
                # Edge 5xx: skip second attempt on the same host, try next edge.
                if exc.response is not None and exc.response.status_code >= 500:
                    break
                await asyncio.sleep(0.25 * (attempt + 1))
            except (
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadError,
            ) as exc:
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.25 * (attempt + 1))
    # Pref host may be a dead edge; forget it so the next tick reshuffles.
    if _CLIST_HOST_PREF and (
        host is None or host == _CLIST_HOST_PREF
    ):
        _CLIST_HOST_PREF = None
    if host is None:
        _note_clist_fail()
    assert last_error is not None
    raise last_error


def _diff_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    diff = ((payload.get("data") or {}).get("diff")) or []
    if isinstance(diff, dict):
        return list(diff.values())
    return list(diff)


async def _fetch_clist_pages(
    client: httpx.AsyncClient,
    fs: str,
    pz: int = 100,
    max_pages: int = 25,
    *,
    hosts: list[str] | None = None,
    page_sleep: float = 0.0,
    allow_partial: bool = False,
    bypass_backoff: bool = False,
) -> list[dict[str, Any]]:
    """Page through an East Money list endpoint until exhausted.

    ``hosts`` overrides the default clist host order (used by heavy quote pulls).
    When ``allow_partial`` is True, a mid-list disconnect returns rows already
    collected instead of raising (breadth prefers partial over empty).
    ``bypass_backoff`` lets light board pulls continue during quote cooldowns.
    """
    rows: list[dict[str, Any]] = []
    total = None
    order = list(hosts) if hosts else None
    host: str | None = order[0] if order else None
    for pn in range(1, max_pages + 1):
        payload: dict[str, Any] | None = None
        tried: list[str] = []
        # Prefer sticky host, then the rest of the order / default failover.
        candidates: list[str | None]
        if order:
            sticky = host if host in order else order[0]
            candidates = [sticky, *[h for h in order if h != sticky]]
        else:
            candidates = [host, None] if host else [None]
        last_exc: Exception | None = None
        for cand in candidates:
            if cand in tried:
                continue
            tried.append(cand)  # type: ignore[arg-type]
            try:
                payload, host = await _get_clist_json(
                    client,
                    fs,
                    pz=pz,
                    pn=pn,
                    host=cand,
                    bypass_backoff=bypass_backoff,
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                host = None
                continue
        if payload is None:
            if allow_partial and rows:
                break
            if last_exc is not None:
                raise last_exc
            raise RuntimeError(f"clist page {pn} empty failure")
        chunk = _diff_rows(payload)
        if not chunk:
            break
        rows.extend(chunk)
        total = ((payload.get("data") or {}).get("total")) or total
        if total is not None and len(rows) >= int(total):
            break
        if len(chunk) < pz:
            break
        if page_sleep and page_sleep > 0:
            await asyncio.sleep(float(page_sleep))
    return rows
