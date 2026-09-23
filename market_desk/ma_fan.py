"""Daily MA stickiness → upward fan screener (nightly observation layer).

Scans liquid names once per trade day after the close (historical daily bars).
Results are persisted for the「均线发散」tab and soft-tagged onto review signals
when codes intersect. Does not gate ready / buy / sell.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from market_desk.eastmoney import fetch_daily_bars
from market_desk.filters import (
    is_chinext_or_star,
    is_main_board,
    is_st,
    normalize_code,
)

log = logging.getLogger("market_desk.ma_fan")

MA_PERIODS = (5, 10, 20, 30, 60)
MA_FAN_FORMULA_VERSION = 1


def _sma(closes: list[float], end: int, n: int) -> float | None:
    """Simple moving average ending at ``end`` (inclusive), length ``n``."""
    if end < n - 1 or end >= len(closes):
        return None
    window = closes[end - n + 1 : end + 1]
    if len(window) < n:
        return None
    return sum(window) / n


def _ma_bundle(closes: list[float], i: int) -> list[float] | None:
    """Return [MA5, MA10, MA20, MA30, MA60] at bar ``i``, or None."""
    out: list[float] = []
    for n in MA_PERIODS:
        v = _sma(closes, i, n)
        if v is None or v <= 0:
            return None
        out.append(v)
    return out


def _spread_pct(mas: list[float]) -> float:
    """(max-min)/mean of the MA bundle, in percent."""
    mean = sum(mas) / len(mas)
    if mean <= 0:
        return 999.0
    return (max(mas) - min(mas)) / mean * 100.0


def _slope_up(closes: list[float], i: int, n: int, look: int = 5) -> bool:
    """True when MA(n) at ``i`` is above MA(n) at ``i-look``."""
    a = _sma(closes, i, n)
    b = _sma(closes, i - look, n)
    if a is None or b is None:
        return False
    return a > b * 1.001


def score_pattern(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Score one name; return diagnostics or None when pattern fails hard gates."""
    if len(bars) < 80:
        return None
    closes = [float(b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    i = len(bars) - 1

    sticky_max = 5.2
    best: tuple[float, int, int] | None = None
    lo = max(60, i - 50)
    hi = i - 5
    for end in range(lo + 11, hi + 1):
        start = end - 11
        spreads: list[float] = []
        ok = True
        for j in range(start, end + 1):
            mas = _ma_bundle(closes, j)
            if not mas:
                ok = False
                break
            sp = _spread_pct(mas)
            if sp > sticky_max:
                ok = False
                break
            spreads.append(sp)
        if not ok or not spreads:
            continue
        avg = sum(spreads) / len(spreads)
        if best is None or avg < best[0]:
            best = (avg, start, end)
    if best is None:
        return None
    sticky_avg, sticky_s, sticky_e = best

    fan_pick: dict[str, Any] | None = None
    for k in range(max(i - 4, sticky_e + 2), i + 1):
        mas = _ma_bundle(closes, k)
        if not mas:
            continue
        ordered = all(mas[t] > mas[t + 1] for t in range(len(mas) - 1))
        if not ordered:
            continue
        slopes = sum(1 for n in (5, 10, 20, 30) if _slope_up(closes, k, n, 5))
        if slopes < 3:
            continue
        fan_sp = _spread_pct(mas)
        if fan_sp < sticky_avg * 1.35 and fan_sp < 4.8:
            continue
        if closes[k] < mas[0] * 0.995:
            continue
        fan_pick = {"i": k, "mas": mas, "spread": fan_sp, "slopes": slopes}
    if not fan_pick:
        return None

    k = int(fan_pick["i"])
    fan_sp = float(fan_pick["spread"])
    mas = list(fan_pick["mas"])
    fan_ratio = fan_sp / max(sticky_avg, 0.15)

    sticky_vols = [vols[j] for j in range(sticky_s, sticky_e + 1) if vols[j] > 0]
    if len(sticky_vols) < 5:
        return None
    sticky_vol = sorted(sticky_vols)[len(sticky_vols) // 2]
    recent = [vols[j] for j in range(k - 4, k + 1) if j >= 0 and vols[j] > 0]
    if len(recent) < 3 or sticky_vol <= 0:
        return None
    recent_vol = sum(recent) / len(recent)
    vol_ratio = recent_vol / sticky_vol
    if vol_ratio < 1.08 or vol_ratio > 4.5:
        return None
    if vols[k] / max(recent_vol, 1.0) > 3.2:
        return None

    sticky_mid = sum(closes[sticky_s : sticky_e + 1]) / (sticky_e - sticky_s + 1)
    ext = (closes[k] / sticky_mid - 1.0) * 100.0 if sticky_mid > 0 else 0.0
    if ext > 120:
        return None

    score = 0.0
    score += max(0.0, (sticky_max - sticky_avg) * 8)
    score += min(35.0, (fan_ratio - 1.5) * 12)
    score += min(20.0, (vol_ratio - 1.15) * 10)
    if 1.4 <= vol_ratio <= 2.6:
        score += 8
    score += float(fan_pick["slopes"]) * 2
    if ext <= 35:
        score += 10
    elif ext <= 55:
        score += 4
    else:
        score -= (ext - 55) * 0.35

    note_bits: list[str] = []
    if sticky_avg <= 2.5:
        note_bits.append("粘连很紧")
    if 1.4 <= vol_ratio <= 2.6:
        note_bits.append("量能温和")
    elif vol_ratio > 2.6:
        note_bits.append("量略猛")
    if ext > 55:
        note_bits.append(f"已拉{ext:.0f}%")
    elif ext > 35:
        note_bits.append(f"离开粘连区{ext:.0f}%")
    else:
        note_bits.append("发散初期")

    return {
        "score": round(score, 1),
        "close": round(closes[k], 2),
        "pct": bars[k].get("pct"),
        "sticky_end": str(bars[sticky_e].get("date") or ""),
        "sticky_spread": round(sticky_avg, 2),
        "fan_spread": round(fan_sp, 2),
        "fan_ratio": round(fan_ratio, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ma_order": ">".join(f"{v:.2f}" for v in mas),
        "note": " · ".join(note_bits) or "命中",
        "ext_pct": round(ext, 1),
    }


def _board_ok(code: str, boards: str) -> bool:
    """Return True when ``code`` is in the requested board set."""
    if boards == "main":
        return is_main_board(code)
    if boards == "growth":
        return is_chinext_or_star(code)
    return is_main_board(code) or is_chinext_or_star(code)


async def load_universe(
    client: httpx.AsyncClient,
    *,
    boards: str = "all",
    limit: int = 400,
    min_amount_yi: float = 1.2,
) -> list[dict[str, Any]]:
    """Load a liquid name pool from Sina amount-ranked HQ nodes."""
    if boards == "main":
        nodes = ["hs_a"]
    elif boards == "growth":
        nodes = ["cyb", "kcb"]
    else:
        nodes = ["hs_a", "cyb", "kcb"]
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_page = 80
    pages = max(2, min(12, (limit // max(1, len(nodes)) // per_page) + 2))
    for node in nodes:
        for page in range(1, pages + 1):
            url = (
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
                "json_v2.php/Market_Center.getHQNodeData"
                f"?page={page}&num={per_page}&sort=amount&asc=0&node={node}"
                "&symbol=&_s_r_a=init"
            )
            try:
                resp = await client.get(url, timeout=20.0)
                resp.raise_for_status()
                rows = resp.json()
            except Exception:
                break
            if not isinstance(rows, list) or not rows:
                break
            for item in rows:
                if not isinstance(item, dict):
                    continue
                code = normalize_code(item.get("code"))
                name = str(item.get("name") or "")
                if not code or code in seen:
                    continue
                if not _board_ok(code, boards) or is_st(name):
                    continue
                try:
                    amt = float(item.get("amount") or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt < min_amount_yi * 1e8:
                    continue
                try:
                    pct = float(
                        item.get("changepercent") or item.get("changePercent") or 0
                    )
                except (TypeError, ValueError):
                    pct = None
                seen.add(code)
                pool.append(
                    {
                        "code": code,
                        "name": name,
                        "amount": amt,
                        "pct": pct,
                        "last": item.get("trade"),
                    }
                )
            await asyncio.sleep(0.12)
            if len(pool) >= limit * 2:
                break
        if len(pool) >= limit * 2:
            break
    pool.sort(key=lambda x: float(x.get("amount") or 0), reverse=True)
    return pool[: max(50, limit)]


async def _score_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    row: dict[str, Any],
    boards: str,
) -> dict[str, Any] | None:
    """Fetch bars and score one quote row into a hit dict."""
    code = normalize_code(row.get("code")) or ""
    name = str(row.get("name") or "")
    if not code or not _board_ok(code, boards) or is_st(name):
        return None
    async with sem:
        try:
            bars = await fetch_daily_bars(client, code, limit=120)
        except Exception:
            return None
        await asyncio.sleep(0.04)
    got = score_pattern(bars or [])
    if not got:
        return None
    amt = row.get("amount")
    amt_yi = round(float(amt) / 1e8, 2) if amt not in (None, "") else None
    return {
        "code": code,
        "name": name,
        "score": float(got["score"]),
        "close": float(got["close"]),
        "pct": None if got.get("pct") is None else float(got["pct"]),
        "amount_yi": amt_yi,
        "sticky_end": str(got["sticky_end"]),
        "sticky_spread": float(got["sticky_spread"]),
        "fan_spread": float(got["fan_spread"]),
        "fan_ratio": float(got["fan_ratio"]),
        "vol_ratio": float(got["vol_ratio"]),
        "ma_order": str(got["ma_order"]),
        "note": str(got["note"]),
        "ext_pct": got.get("ext_pct"),
    }


async def run_ma_fan_scan(
    *,
    trade_date: str,
    limit: int = 400,
    top: int = 40,
    min_amount_yi: float = 1.2,
    boards: str = "all",
    persist: bool = True,
) -> dict[str, Any]:
    """Scan liquid names and optionally persist the day payload.

    Intended for the nightly engine tick (≈18:00) or an admin force-run.
    """
    day = str(trade_date or "")[:10]
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False) as client:
        pool = await load_universe(
            client, boards=boards, limit=limit, min_amount_yi=min_amount_yi
        )
        sem = asyncio.Semaphore(5)
        raw = await asyncio.gather(
            *[_score_one(client, sem, row, boards) for row in pool]
        )
    hits = [h for h in raw if h]
    hits.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
    hits = hits[: max(5, top)]
    # Soft-tag intersection with same-day paper buy signals (if any).
    signal_codes = _buy_signal_codes_for_day(day)
    for h in hits:
        code = normalize_code(h.get("code")) or ""
        h["in_review"] = bool(code and code in signal_codes)
    payload = {
        "ok": True,
        "trade_date": day,
        "scanned": len(pool),
        "hit_n": len(hits),
        "boards": boards,
        "limit": limit,
        "min_amount_yi": min_amount_yi,
        "formula_version": MA_FAN_FORMULA_VERSION,
        "items": hits,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "观察层：粘连→向上发散→量能温和；不进 ready / 不改买卖闸门。",
    }
    if persist:
        from market_desk.db import save_ma_fan_day

        save_ma_fan_day(day, payload)
        log.info(
            "ma_fan scan saved %s scanned=%s hits=%s",
            day,
            payload["scanned"],
            payload["hit_n"],
        )
    return payload


def _buy_signal_codes_for_day(trade_date: str) -> set[str]:
    """Codes with a same-day buy signal (for soft intersection tags)."""
    try:
        from market_desk.db import load_signals_for_date
        from market_desk.review import is_buy_signal
    except Exception:
        return set()
    out: set[str] = set()
    for row in load_signals_for_date(trade_date):
        if not is_buy_signal(row.get("signal_type")):
            continue
        code = normalize_code(row.get("code"))
        if code:
            out.add(code)
    return out


def ma_fan_code_set(trade_date: str | None = None) -> set[str]:
    """Return codes from the persisted scan for ``trade_date`` (or latest)."""
    from market_desk.db import load_ma_fan_day, list_ma_fan_dates

    day = str(trade_date or "")[:10]
    if not day:
        dates = list_ma_fan_dates(limit=1)
        day = dates[0] if dates else ""
    if not day:
        return set()
    payload = load_ma_fan_day(day) or {}
    out: set[str] = set()
    for item in payload.get("items") or []:
        code = normalize_code((item or {}).get("code"))
        if code:
            out.add(code)
    return out


def enrich_signals_with_ma_fan(
    rows: list[dict[str, Any]],
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """Attach ``ma_fan`` / ``ma_fan_note`` soft tags onto review signal rows."""
    codes = ma_fan_code_set(trade_date)
    if not codes:
        return rows
    payload_by_code: dict[str, dict[str, Any]] = {}
    try:
        from market_desk.db import load_ma_fan_day, list_ma_fan_dates

        day = str(trade_date or "")[:10]
        if not day:
            dates = list_ma_fan_dates(limit=1)
            day = dates[0] if dates else ""
        body = load_ma_fan_day(day) or {}
        for item in body.get("items") or []:
            c = normalize_code((item or {}).get("code"))
            if c:
                payload_by_code[c] = item
    except Exception:
        payload_by_code = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = normalize_code(item.get("code"))
        if code and code in codes:
            item["ma_fan"] = True
            hit = payload_by_code.get(code) or {}
            item["ma_fan_note"] = hit.get("note") or "均线粘连后向上发散"
            item["ma_fan_score"] = hit.get("score")
        out.append(item)
    return out


def attach_review_flags_to_ma_fan(
    payload: dict[str, Any] | None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Refresh ``in_review`` on stored hits (cheap; no rescan)."""
    body = dict(payload or {})
    day = str(trade_date or body.get("trade_date") or "")[:10]
    signal_codes = _buy_signal_codes_for_day(day) if day else set()
    items: list[dict[str, Any]] = []
    for raw in body.get("items") or []:
        item = dict(raw or {})
        code = normalize_code(item.get("code")) or ""
        item["in_review"] = bool(code and code in signal_codes)
        items.append(item)
    body["items"] = items
    body["review_overlap_n"] = sum(1 for x in items if x.get("in_review"))
    return body
