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
MA_FAN_FORMULA_VERSION = 5

# Nightly staggered slices: (earliest minute-of-day, amount-rank offset, count, key).
# 18:00 → 0–400；20:00 → 400–800；22:00 → 800–1000（末段流动性更薄，只扫 200）.
MA_FAN_SCHEDULE: tuple[tuple[int, int, int, str], ...] = (
    (18 * 60, 0, 400, "0-400"),
    (20 * 60, 400, 400, "400-800"),
    (22 * 60, 800, 200, "800-1000"),
)


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
    """Score one name; return diagnostics or None when pattern fails hard gates.

    Observation layer: hard gates stay permissive; quality is expressed via
    ``score``, ``stage``, and structured ``tags`` rather than exclusion.
    """
    if len(bars) < 80:
        return None
    closes = [float(b["close"]) for b in bars]
    highs = [float(b.get("high") or b["close"]) for b in bars]
    lows = [float(b.get("low") or b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0) for b in bars]
    i = len(bars) - 1

    # Stickiness: MA spread + price amplitude (P1 — filter noisy squeezes).
    sticky_max = 6.2
    sticky_len = 10
    sticky_amp_max = 20.0
    best: tuple[float, float, int, int] | None = None  # avg, amp, s, e
    lo = max(60, i - 55)
    hi = i - 4
    for end in range(lo + (sticky_len - 1), hi + 1):
        start = end - (sticky_len - 1)
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
        hi_w = max(highs[start : end + 1])
        lo_w = min(lows[start : end + 1])
        mid_w = (hi_w + lo_w) / 2.0
        amp = ((hi_w - lo_w) / mid_w * 100.0) if mid_w > 0 else 999.0
        if amp > sticky_amp_max:
            continue
        avg = sum(spreads) / len(spreads)
        # Prefer tighter MA + quieter price; amp is a soft tie-break.
        key = avg + amp * 0.04
        if best is None or key < (best[0] + best[1] * 0.04):
            best = (avg, amp, start, end)
    if best is None:
        return None
    sticky_avg, sticky_amp, sticky_s, sticky_e = best

    fan_pick: dict[str, Any] | None = None
    for k in range(max(i - 6, sticky_e + 1), i + 1):
        mas = _ma_bundle(closes, k)
        if not mas:
            continue
        # Allow one adjacent inversion (near-bull stack) instead of strict order.
        inversions = sum(
            1 for t in range(len(mas) - 1) if mas[t] < mas[t + 1] * 0.998
        )
        if inversions > 1:
            continue
        ordered = inversions == 0
        slopes = sum(1 for n in (5, 10, 20, 30) if _slope_up(closes, k, n, 5))
        if slopes < 2:
            continue
        fan_sp = _spread_pct(mas)
        if fan_sp < sticky_avg * 1.18 and fan_sp < 3.6:
            continue
        if closes[k] < mas[0] * 0.985:
            continue
        fan_pick = {
            "i": k,
            "mas": mas,
            "spread": fan_sp,
            "slopes": slopes,
            "ordered": ordered,
            "inversions": inversions,
        }
    if not fan_pick:
        return None

    k = int(fan_pick["i"])
    fan_sp = float(fan_pick["spread"])
    mas = list(fan_pick["mas"])
    fan_ratio = fan_sp / max(sticky_avg, 0.15)
    slopes = int(fan_pick["slopes"])
    ordered = bool(fan_pick["ordered"])

    # P1: progressive fan — MA spread should rise over recent sessions.
    spread_hist: list[float] = []
    for t in range(max(sticky_e + 1, k - 4), k + 1):
        m = _ma_bundle(closes, t)
        if m:
            spread_hist.append(_spread_pct(m))
    rising_steps = 0
    if len(spread_hist) >= 2:
        rising_steps = sum(
            1 for a, b in zip(spread_hist, spread_hist[1:]) if b + 0.08 >= a
        )
    progressive = len(spread_hist) >= 3 and rising_steps >= max(1, len(spread_hist) - 2)
    one_day_pop = (
        len(spread_hist) >= 2
        and spread_hist[-1] >= spread_hist[0] * 1.55
        and rising_steps <= 1
    )

    # P1: MA60 not in clear downtrend (exclude down-leg squeezes).
    ma60_now = _sma(closes, k, 60)
    ma60_prev = _sma(closes, k - 10, 60) if k >= 70 else None
    ma60_ok = True
    ma60_strong = False
    if ma60_now is not None and ma60_prev is not None and ma60_prev > 0:
        if ma60_now < ma60_prev * 0.985:
            return None  # clear MA60 downtrend
        ma60_ok = ma60_now >= ma60_prev * 0.998
        ma60_strong = ma60_now >= ma60_prev * 1.002

    sticky_vols = [vols[j] for j in range(sticky_s, sticky_e + 1) if vols[j] > 0]
    if len(sticky_vols) < 4:
        return None
    sticky_vol = sorted(sticky_vols)[len(sticky_vols) // 2]
    recent = [vols[j] for j in range(k - 4, k + 1) if j >= 0 and vols[j] > 0]
    if len(recent) < 3 or sticky_vol <= 0:
        return None
    recent_vol = sum(recent) / len(recent)
    vol_ratio = recent_vol / sticky_vol
    if vol_ratio < 0.95 or vol_ratio > 5.5:
        return None
    day_spike = vols[k] / max(recent_vol, 1.0)

    sticky_mid = sum(closes[sticky_s : sticky_e + 1]) / (sticky_e - sticky_s + 1)
    ext = (closes[k] / sticky_mid - 1.0) * 100.0 if sticky_mid > 0 else 0.0
    if ext > 150:
        return None

    if ext <= 28:
        stage = "初期"
    elif ext <= 55:
        stage = "中期"
    else:
        stage = "后期"

    days_since_sticky = max(0, k - sticky_e)
    freshness = "刚发散" if days_since_sticky <= 2 else (
        "续发散" if days_since_sticky <= 5 else "远端发散"
    )

    score = 0.0
    score += max(0.0, (sticky_max - sticky_avg) * 7)
    score += max(0.0, (sticky_amp_max - sticky_amp) * 0.35)
    score += min(32.0, (fan_ratio - 1.3) * 11)
    if 1.25 <= vol_ratio <= 2.9:
        score += 12
    elif 0.95 <= vol_ratio < 1.25:
        score += 5
    elif 2.9 < vol_ratio <= 3.8:
        score += 3
    else:
        score -= 5
    if day_spike > 3.5:
        score -= 4
    score += slopes * 2.5
    if ordered:
        score += 4
    else:
        score -= 2
    if stage == "初期":
        score += 12
    elif stage == "中期":
        score += 5
    else:
        score -= (ext - 55) * 0.35
    if freshness == "刚发散":
        score += 4
    elif freshness == "远端发散":
        score -= 3
    if progressive:
        score += 7
    elif one_day_pop:
        score -= 8
    if ma60_strong:
        score += 4
    elif not ma60_ok:
        score -= 5

    tags: list[str] = [stage, freshness]
    if sticky_avg <= 2.8:
        tags.append("粘连很紧")
    elif sticky_avg <= 4.5:
        tags.append("粘连够")
    else:
        tags.append("粘连偏松")
    if sticky_amp <= 12:
        tags.append("振幅小")
    elif sticky_amp <= 16:
        tags.append("振幅可控")
    else:
        tags.append("振幅偏大")
    if progressive:
        tags.append("渐进发散")
    elif one_day_pop:
        tags.append("一日拉开")
    if ma60_strong:
        tags.append("MA60稳升")
    elif ma60_ok:
        tags.append("MA60稳")
    else:
        tags.append("MA60偏弱")
    if 1.25 <= vol_ratio <= 2.9:
        tags.append("量能温和")
    elif vol_ratio < 1.25:
        tags.append("量起步")
    elif vol_ratio <= 3.8:
        tags.append("量偏猛")
    else:
        tags.append("放量过猛")
    if day_spike > 3.2:
        tags.append("单日放量")
    if slopes >= 4:
        tags.append("坡度强")
    elif slopes >= 3:
        tags.append("坡度够")
    else:
        tags.append("坡度弱")
    if ordered:
        tags.append("多头排列")
    else:
        tags.append("近多头")
    if ext >= 70:
        tags.append(f"已拉{ext:.0f}%")
    elif stage == "中期":
        tags.append(f"离开{ext:.0f}%")

    return {
        "score": round(score, 1),
        "close": round(closes[k], 2),
        "pct": bars[k].get("pct"),
        "sticky_end": str(bars[sticky_e].get("date") or ""),
        "sticky_spread": round(sticky_avg, 2),
        "sticky_amp": round(sticky_amp, 1),
        "fan_spread": round(fan_sp, 2),
        "fan_ratio": round(fan_ratio, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ma_order": ">".join(f"{v:.2f}" for v in mas),
        "note": " · ".join(tags) or "命中",
        "ext_pct": round(ext, 1),
        "stage": stage,
        "tags": tags,
        "freshness": freshness,
        "slopes": slopes,
        "progressive": progressive,
        "ma60_ok": ma60_ok,
    }


def amount_band_for_rank(rank: int | None) -> str:
    """Map a 1-based amount rank into a short liquidity-band tag."""
    try:
        r = int(rank or 0)
    except (TypeError, ValueError):
        r = 0
    if r <= 0:
        return ""
    if r <= 100:
        return "额档·头百"
    if r <= 400:
        return "额档·前400"
    if r <= 800:
        return "额档·400-800"
    return "额档·800+"


def _code_board_index(snapshot: dict[str, Any] | None) -> dict[str, list[str]]:
    """Build code → board-name list from desk snapshot pools."""
    snap = snapshot or {}
    out: dict[str, list[str]] = {}
    for key in ("hot_boards", "pin_boards", "ice_boards", "favorite_boards"):
        for card in snap.get(key) or []:
            if not isinstance(card, dict):
                continue
            bname = str(card.get("name") or "").strip()
            if not bname:
                continue
            for member in card.get("pool") or card.get("members") or []:
                if not isinstance(member, dict):
                    continue
                code = normalize_code(member.get("code"))
                if not code:
                    continue
                bucket = out.setdefault(code, [])
                if bname not in bucket:
                    bucket.append(bname)
    return out


def _desk_theme_names(snapshot: dict[str, Any] | None, trade_date: str) -> dict[str, str]:
    """Resolve sticky main / side / link board names for soft theme tags."""
    snap = snapshot or {}
    verdict = snap.get("verdict") if isinstance(snap.get("verdict"), dict) else {}
    main = str(((verdict.get("mainline") or {}) if isinstance(verdict, dict) else {}).get("name") or "").strip()
    side = str(((verdict.get("side_mainline") or {}) if isinstance(verdict, dict) else {}).get("name") or "").strip()
    link = str(((verdict.get("link_mainline") or {}) if isinstance(verdict, dict) else {}).get("name") or "").strip()
    if not main:
        try:
            from market_desk.db import load_daily

            day = str(trade_date or "")[:10]
            for row in load_daily(limit=40):
                if str(row.get("trade_date") or "")[:10] == day:
                    main = str(row.get("mainline") or "").strip()
                    break
        except Exception:
            pass
    return {"main": main, "side": side, "link": link}


def annotate_hits_with_desk_context(
    items: list[dict[str, Any]],
    *,
    trade_date: str,
    snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach amount-band and mainline/side/link theme soft tags onto hits.

    Does not gate ready; only nudges score slightly when theme-aligned.
    Idempotent: recomputes from ``score_base`` so refresh does not stack nudges.
    """
    from market_desk.review import compare_boards_to_mainline

    themes = _desk_theme_names(snapshot, trade_date)
    board_ix = _code_board_index(snapshot)
    theme_labels = {
        "贴主线", "近主线", "主线同主题",
        "贴支线", "近支线", "支线同主题",
        "贴联动", "近联动", "联动同主题",
        "复盘加权",
    }
    out: list[dict[str, Any]] = []
    for raw in items or []:
        item = dict(raw or {})
        code = normalize_code(item.get("code")) or ""
        try:
            score_base = float(item.get("score_base") or item.get("score") or 0)
        except (TypeError, ValueError):
            score_base = 0.0
        item["score_base"] = score_base
        tags = [
            str(t) for t in (item.get("tags") or [])
            if t and str(t) not in theme_labels
        ]
        # Amount band from scan rank (preferred) or amount_yi fallback.
        band = str(item.get("amount_band") or "").strip()
        if not band:
            band = amount_band_for_rank(item.get("amount_rank"))
        if not band and item.get("amount_yi") is not None:
            try:
                yi = float(item.get("amount_yi") or 0)
            except (TypeError, ValueError):
                yi = 0.0
            if yi >= 20:
                band = "额档·头百"
            elif yi >= 5:
                band = "额档·前400"
            elif yi >= 2:
                band = "额档·400-800"
            elif yi > 0:
                band = "额档·800+"
        # Keep a single amount-band tag.
        tags = [t for t in tags if not str(t).startswith("额档·")]
        if band:
            tags.append(band)
        item["amount_band"] = band

        boards = list(item.get("boards") or [])
        if not boards and code and code in board_ix:
            boards = list(board_ix[code])
        item["boards"] = boards

        theme_hit = ""
        score_nudge = 0.0
        for role, label_prefix, bump in (
            ("main", "主线", 6.0),
            ("side", "支线", 3.0),
            ("link", "联动", 2.0),
        ):
            target = themes.get(role) or ""
            if not target or not boards:
                continue
            cmp = compare_boards_to_mainline(boards, target, role=role)
            align = str(cmp.get("align") or "")
            if align not in ("belong", "near", "theme"):
                continue
            if align == "belong":
                tag = f"贴{label_prefix}"
            elif align == "near":
                tag = f"近{label_prefix}"
            else:
                tag = f"{label_prefix}同主题"
            tags.append(tag)
            theme_hit = tag
            score_nudge = bump
            break  # main wins over side/link

        item["theme_tag"] = theme_hit
        item["theme_nudge"] = score_nudge
        # P2: same-day review buy-signal soft boost (idempotent via score_base).
        review_nudge = 0.0
        if item.get("in_review"):
            review_nudge = 8.0
            if "复盘加权" not in tags:
                tags.append("复盘加权")
        item["review_nudge"] = review_nudge
        item["score"] = round(score_base + score_nudge + review_nudge, 1)
        item["tags"] = tags
        item["note"] = " · ".join(tags) if tags else (item.get("note") or "命中")
        item.pop("_theme_nudged", None)
        out.append(item)
    out.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
    return out


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
    need: int = 400,
    min_amount_yi: float = 1.2,
) -> list[dict[str, Any]]:
    """Load amount-ranked names from Sina until ``need`` rows (or pages run out)."""
    if boards == "main":
        nodes = ["hs_a"]
    elif boards == "growth":
        nodes = ["cyb", "kcb"]
    else:
        nodes = ["hs_a", "cyb", "kcb"]
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    per_page = 80
    want = max(50, int(need or 400))
    pages = max(3, min(20, (want // max(1, len(nodes)) // per_page) + 3))
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
            if len(pool) >= want * 2:
                break
        if len(pool) >= want * 2:
            break
    pool.sort(key=lambda x: float(x.get("amount") or 0), reverse=True)
    return pool[:want]


async def _score_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    row: dict[str, Any],
    boards: str,
    *,
    min_price: float = 0.0,
    prefer_main: bool = True,
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
    close = float(got["close"])
    if min_price > 0 and close < float(min_price):
        return None
    amt = row.get("amount")
    amt_yi = round(float(amt) / 1e8, 2) if amt not in (None, "") else None
    try:
        amount_rank = int(row.get("_rank") or 0) or None
    except (TypeError, ValueError):
        amount_rank = None
    band = amount_band_for_rank(amount_rank)
    tags = list(got.get("tags") or [])
    if band and band not in tags:
        tags.append(band)
    score_base = float(got["score"])
    if prefer_main and is_main_board(code):
        score_base += 3.0
        if "主板" not in tags:
            tags.append("主板")
    elif prefer_main and is_chinext_or_star(code):
        if "成长板" not in tags:
            tags.append("成长板")
    return {
        "code": code,
        "name": name,
        "score": score_base,
        "score_base": score_base,
        "close": close,
        "pct": None if got.get("pct") is None else float(got["pct"]),
        "amount_yi": amt_yi,
        "amount_rank": amount_rank,
        "amount_band": band,
        "sticky_end": str(got["sticky_end"]),
        "sticky_spread": float(got["sticky_spread"]),
        "sticky_amp": got.get("sticky_amp"),
        "fan_spread": float(got["fan_spread"]),
        "fan_ratio": float(got["fan_ratio"]),
        "vol_ratio": float(got["vol_ratio"]),
        "ma_order": str(got["ma_order"]),
        "note": " · ".join(tags) if tags else str(got["note"]),
        "ext_pct": got.get("ext_pct"),
        "stage": got.get("stage") or "",
        "tags": tags,
        "freshness": got.get("freshness") or "",
        "slopes": got.get("slopes"),
        "progressive": bool(got.get("progressive")),
        "ma60_ok": bool(got.get("ma60_ok", True)),
    }


def _merge_hits(
    prev_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    *,
    keep: int,
) -> list[dict[str, Any]]:
    """Merge hits by code, keeping the higher pattern score, then trim to ``keep``."""
    by_code: dict[str, dict[str, Any]] = {}
    for raw in list(prev_items or []) + list(new_items or []):
        item = dict(raw or {})
        code = normalize_code(item.get("code")) or ""
        if not code:
            continue
        try:
            base = float(item.get("score_base") or item.get("score") or 0)
        except (TypeError, ValueError):
            base = 0.0
        item["score_base"] = base
        old = by_code.get(code)
        old_base = float((old or {}).get("score_base") or (old or {}).get("score") or 0) if old else -1e9
        if old is None or base >= old_base:
            by_code[code] = item
    out = list(by_code.values())
    out.sort(key=lambda h: float(h.get("score_base") or h.get("score") or 0), reverse=True)
    return out[: max(10, keep)]


def slices_done_for_day(trade_date: str) -> set[str]:
    """Return slice keys already persisted for ``trade_date``."""
    from market_desk.db import load_ma_fan_day

    body = load_ma_fan_day(str(trade_date or "")[:10]) or {}
    return {str(x) for x in (body.get("slices_done") or []) if x}


def next_due_ma_fan_slice(
    *,
    trade_date: str,
    minutes: int,
) -> tuple[int, int, str] | None:
    """Return ``(offset, count, key)`` for the next due unfinished slice, or None."""
    done = slices_done_for_day(trade_date)
    for start_min, offset, count, key in MA_FAN_SCHEDULE:
        if int(minutes) < int(start_min):
            continue
        if key in done:
            continue
        return int(offset), int(count), str(key)
    return None


async def run_ma_fan_scan(
    *,
    trade_date: str,
    offset: int = 0,
    limit: int = 400,
    slice_key: str = "0-400",
    top: int = 80,
    min_amount_yi: float = 1.2,
    boards: str = "all",
    persist: bool = True,
    replace: bool = False,
    snapshot: dict[str, Any] | None = None,
    min_price: float = 0.0,
    prefer_main: bool = True,
) -> dict[str, Any]:
    """Scan one amount-rank slice and merge into the day payload.

    ``offset``/``limit`` select the liquidity band (e.g. 400–800). Results merge
    into ``ma_fan_day`` unless ``replace=True`` (admin full rebuild of this slice
    still merges by code; pass replace to clear prior slices_done for a fresh day).
    """
    day = str(trade_date or "")[:10]
    if not day:
        day = datetime.now().strftime("%Y-%m-%d")
    off = max(0, int(offset))
    lim = max(20, min(int(limit or 400), 500))
    key = str(slice_key or f"{off}-{off + lim}")
    need = off + lim
    min_px = max(0.0, float(min_price or 0))
    prefer = bool(prefer_main)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False) as client:
        universe = await load_universe(
            client, boards=boards, need=need, min_amount_yi=min_amount_yi
        )
        pool = universe[off : off + lim]
        ranked: list[dict[str, Any]] = []
        for i, row in enumerate(pool):
            item = dict(row or {})
            item["_rank"] = off + i + 1
            ranked.append(item)
        sem = asyncio.Semaphore(5)
        raw = await asyncio.gather(
            *[
                _score_one(
                    client,
                    sem,
                    row,
                    boards,
                    min_price=min_px,
                    prefer_main=prefer,
                )
                for row in ranked
            ]
        )
    chunk = [h for h in raw if h]
    chunk.sort(key=lambda h: float(h.get("score_base") or h.get("score") or 0), reverse=True)

    from market_desk.db import load_ma_fan_day, save_ma_fan_day

    prev = {} if replace else (load_ma_fan_day(day) or {})
    prev_items = [] if replace else list(prev.get("items") or [])
    keep = max(20, min(int(top or 80), 150))
    merged = _merge_hits(prev_items, chunk, keep=keep)
    signal_codes = _buy_signal_codes_for_day(day)
    for h in merged:
        code = normalize_code(h.get("code")) or ""
        h["in_review"] = bool(code and code in signal_codes)
    merged = annotate_hits_with_desk_context(
        merged, trade_date=day, snapshot=snapshot
    )
    # Re-trim after theme / review score nudges.
    merged = merged[:keep]

    done = set() if replace else {str(x) for x in (prev.get("slices_done") or [])}
    done.add(key)
    scanned_total = int(prev.get("scanned") or 0) + len(pool)
    if replace:
        scanned_total = len(pool)

    payload = {
        "ok": True,
        "trade_date": day,
        "scanned": scanned_total,
        "hit_n": len(merged),
        "boards": boards,
        "min_amount_yi": min_amount_yi,
        "min_price": min_px,
        "prefer_main": prefer,
        "formula_version": MA_FAN_FORMULA_VERSION,
        "items": merged,
        "slices_done": sorted(done, key=lambda s: (len(s), s)),
        "last_slice": key,
        "last_slice_scanned": len(pool),
        "last_slice_hits": len(chunk),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": (
            "观察层：粘连→渐进发散；18/20/22 分档扫成交额榜；"
            "标签含额档/主线/复盘加权；不进 ready。"
        ),
    }
    if persist:
        save_ma_fan_day(day, payload)
        log.info(
            "ma_fan slice %s day=%s pool=%s chunk_hits=%s merged=%s done=%s",
            key,
            day,
            len(pool),
            len(chunk),
            len(merged),
            sorted(done),
        )
    return payload


async def run_ma_fan_all_due_slices(
    *,
    trade_date: str,
    minutes: int,
    top: int = 80,
    min_amount_yi: float = 1.2,
    boards: str = "all",
    force_all: bool = False,
    snapshot: dict[str, Any] | None = None,
    min_price: float = 0.0,
    prefer_main: bool = True,
) -> dict[str, Any]:
    """Run the next due slice (or all slices when ``force_all``)."""
    day = str(trade_date or "")[:10]
    if force_all:
        out: dict[str, Any] = {}
        first = True
        for _start, offset, count, key in MA_FAN_SCHEDULE:
            out = await run_ma_fan_scan(
                trade_date=day,
                offset=offset,
                limit=count,
                slice_key=key,
                top=top,
                min_amount_yi=min_amount_yi,
                boards=boards,
                persist=True,
                replace=first,
                snapshot=snapshot,
                min_price=min_price,
                prefer_main=prefer_main,
            )
            first = False
        return out or {"ok": False, "detail": "no slices"}
    due = next_due_ma_fan_slice(trade_date=day, minutes=minutes)
    if not due:
        from market_desk.db import load_ma_fan_day

        body = load_ma_fan_day(day) or {}
        return {"ok": True, "skipped": True, "scan": body, "view_date": day}
    offset, count, key = due
    return await run_ma_fan_scan(
        trade_date=day,
        offset=offset,
        limit=count,
        slice_key=key,
        top=top,
        min_amount_yi=min_amount_yi,
        boards=boards,
        persist=True,
        replace=False,
        snapshot=snapshot,
        min_price=min_price,
        prefer_main=prefer_main,
    )


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
            item["ma_fan_stage"] = hit.get("stage") or ""
            item["ma_fan_tags"] = list(hit.get("tags") or [])
            item["ma_fan_freshness"] = hit.get("freshness") or ""
            item["ma_fan_theme"] = hit.get("theme_tag") or ""
            item["ma_fan_amount_band"] = hit.get("amount_band") or ""
        out.append(item)
    return out


def attach_review_flags_to_ma_fan(
    payload: dict[str, Any] | None,
    trade_date: str | None = None,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh ``in_review`` and theme/amount soft tags on stored hits."""
    body = dict(payload or {})
    day = str(trade_date or body.get("trade_date") or "")[:10]
    signal_codes = _buy_signal_codes_for_day(day) if day else set()
    seeded: list[dict[str, Any]] = []
    for raw in body.get("items") or []:
        item = dict(raw or {})
        code = normalize_code(item.get("code")) or ""
        item["in_review"] = bool(code and code in signal_codes)
        seeded.append(item)
    items = annotate_hits_with_desk_context(
        seeded,
        trade_date=day,
        snapshot=snapshot,
    )
    body["items"] = items
    body["review_overlap_n"] = sum(1 for x in items if x.get("in_review"))
    body["theme_overlap_n"] = sum(1 for x in items if x.get("theme_tag"))
    return body
