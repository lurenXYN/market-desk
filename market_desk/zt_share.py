"""Limit-up board-share lift table (display only; never recommends codes).

Daily: count today's limit-ups by industry (from the zt pool) and by concept
(board member overlap). Mark lift when share ≥ 20-day mean + 1σ and n ≥ 3.
Forward glance: whether a lifted board later appears as mainline / narrative —
shown for audit, not as a stock tip.
"""

from __future__ import annotations

import math
from typing import Any

from market_desk.filters import normalize_code
from market_desk.numbers import num


def _member_codes(board: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for m in board.get("pool") or board.get("members") or []:
        if isinstance(m, dict):
            c = normalize_code(m.get("code"))
        else:
            c = normalize_code(m)
        if c:
            out.add(c)
    return out


def count_zt_by_board(
    zt_rows: list[dict[str, Any]] | None,
    *,
    concept_boards: list[dict[str, Any]] | None = None,
    industry_boards: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return per-board counts ``{board, kind, n, total, share}`` for one day."""
    zt_codes = {
        normalize_code(r.get("code")): r
        for r in (zt_rows or [])
        if normalize_code(r.get("code"))
    }
    total = len(zt_codes)
    if total <= 0:
        return []
    tallies: dict[tuple[str, str], int] = {}
    for row in zt_codes.values():
        ind = str(row.get("industry") or "").strip()
        if ind:
            tallies[("industry", ind)] = tallies.get(("industry", ind), 0) + 1
    for board in industry_boards or []:
        name = str(board.get("name") or "").strip()
        if not name:
            continue
        hit = len(_member_codes(board) & set(zt_codes))
        if hit:
            key = ("industry", name)
            tallies[key] = max(tallies.get(key, 0), hit)
    for board in concept_boards or []:
        name = str(board.get("name") or "").strip()
        if not name:
            continue
        hit = len(_member_codes(board) & set(zt_codes))
        if hit:
            tallies[("concept", name)] = hit
    rows: list[dict[str, Any]] = []
    for (kind, name), n in tallies.items():
        rows.append(
            {
                "board": name,
                "kind": kind,
                "n": int(n),
                "total": total,
                "share": round(100.0 * n / total, 2),
            }
        )
    rows.sort(key=lambda r: (-float(r["share"]), -int(r["n"]), r["board"]))
    return rows


def annotate_lifts(
    today: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    lookback: int | None = None,
    z_thr: float | None = None,
    min_n: int | None = None,
) -> list[dict[str, Any]]:
    """Stamp ``lift`` / ``z`` / ``mean`` / ``std`` using prior days only."""
    from market_desk.config import ZT_SHARE_LOOKBACK, ZT_SHARE_MIN_N, ZT_SHARE_Z

    lb = int(ZT_SHARE_LOOKBACK if lookback is None else lookback)
    z0 = float(ZT_SHARE_Z if z_thr is None else z_thr)
    min_n = int(ZT_SHARE_MIN_N if min_n is None else min_n)
    by_key: dict[tuple[str, str], list[float]] = {}
    for row in history or []:
        key = (str(row.get("kind") or ""), str(row.get("board") or ""))
        share = num(row.get("share"))
        if not key[0] or not key[1] or share is None:
            continue
        by_key.setdefault(key, []).append(float(share))
    out: list[dict[str, Any]] = []
    for row in today or []:
        item = dict(row)
        key = (str(item.get("kind") or ""), str(item.get("board") or ""))
        series = (by_key.get(key) or [])[-lb:]
        mean = std = z = None
        lift = 0
        if len(series) >= max(5, lb // 4):
            mean = sum(series) / len(series)
            var = sum((x - mean) ** 2 for x in series) / max(1, len(series) - 1)
            std = math.sqrt(var)
            share = float(num(item.get("share")) or 0.0)
            z = (share - mean) / std if std > 1e-9 else 0.0
            if (
                int(item.get("n") or 0) >= min_n
                and share >= mean + z0 * std
            ):
                lift = 1
        item["mean"] = None if mean is None else round(mean, 2)
        item["std"] = None if std is None else round(std, 2)
        item["z"] = None if z is None else round(z, 2)
        item["lift"] = lift
        out.append(item)
    return out


def build_zt_share_review(
    recent: list[dict[str, Any]] | None,
    *,
    mainline_by_day: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compact review/ops payload: lift days and a soft forward hit glance."""
    from market_desk.config import ZT_SHARE_SHADOW_SINCE

    since = str(ZT_SHARE_SHADOW_SINCE)[:10]
    rows = [
        r
        for r in (recent or [])
        if str(r.get("trade_date") or "")[:10] >= since and int(r.get("lift") or 0)
    ]
    days = sorted({str(r.get("trade_date") or "")[:10] for r in rows})
    # Forward: lifted board equals that day's+1..+5 mainline name (string match).
    hits = 0
    checked = 0
    ml = mainline_by_day or {}
    for r in rows:
        day = str(r.get("trade_date") or "")[:10]
        board = str(r.get("board") or "")
        if not day or not board:
            continue
        checked += 1
        for d, name in ml.items():
            if d <= day:
                continue
            # Rough calendar gap ≤ 10 calendar days ≈ 5 sessions.
            if d > day and (d[:10] <= _plus_days(day, 10)) and board in str(name or ""):
                hits += 1
                break
    hit_rate = round(100.0 * hits / checked, 1) if checked else None
    top = sorted(rows, key=lambda r: (-float(r.get("z") or 0), -int(r.get("n") or 0)))[:16]
    return {
        "ok": True,
        "since": since,
        "lift_days": len(days),
        "lift_n": len(rows),
        "forward_checked": checked,
        "forward_hits": hits,
        "forward_hit_rate": hit_rate,
        "top": [
            {
                "day": r.get("trade_date"),
                "board": r.get("board"),
                "kind": r.get("kind"),
                "n": r.get("n"),
                "share": r.get("share"),
                "z": r.get("z"),
            }
            for r in top
        ],
        "note": (
            f"样本外自 {since}；抬升=占比≥近20日均值+1σ 且只数≥3；"
            "只展示，永不荐股；与叙事软提示同一复盘日最多上一条。"
        ),
        "verdict": (
            f"抬升日 {len(days)} · 前瞻命中 {hit_rate}%"
            if hit_rate is not None
            else f"抬升日 {len(days)} · 前瞻未齐"
        ),
    }


def _plus_days(day: str, n: int) -> str:
    from datetime import date, timedelta

    try:
        d = date.fromisoformat(day[:10])
    except ValueError:
        return day
    return (d + timedelta(days=int(n))).isoformat()
