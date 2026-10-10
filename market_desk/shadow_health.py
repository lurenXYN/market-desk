"""Shadow-ledger health: per-day row counts and how close each trigger came.

A shadow ledger that records nothing is ambiguous: the trigger may simply not have
fired, or the wiring may be broken (the narrative throttle once cached pre-open
empties for weeks). Each row pairs the ledger count with the reason it could be
empty, so the two cases can be told apart at a glance.
"""

from __future__ import annotations

import json
from typing import Any

from market_desk.db.core import _connect

# Signal payload fields every buy card should carry from its first upsert.
CARD_FIELDS = ("book_open", "cf", "stop_atr", "market_gates", "first_last")
# Fields written only once a card has lit (``ever_ready``).
LIT_FIELDS = ("first_ready_px", "book_ready", "slip_bps")
# Below this share of buy cards a card field counts as missing.
FIELD_MIN_COVER = 0.8


def _count_by_day(conn: Any, table: str, days: list[str], where: str = "") -> dict[str, int]:
    """Return ``{YYYY-MM-DD: rows}`` for ``table`` over ``days`` (missing table → {}).

    Some ledgers key days as ``YYYYMMDD``; both forms are matched. ``where`` is an
    optional extra SQL condition (trusted constant, not user input).
    """
    keys = list(days) + [d.replace("-", "") for d in days]
    marks = ",".join("?" * len(keys))
    extra = f" AND ({where})" if where else ""
    try:
        rows = conn.execute(
            f"SELECT trade_date, COUNT(*) FROM {table} WHERE trade_date IN ({marks}){extra} GROUP BY trade_date",
            keys,
        ).fetchall()
    except Exception:
        return {}
    out: dict[str, int] = {}
    for d, n in rows:
        s = str(d)
        day = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s[:10]
        out[day] = out.get(day, 0) + int(n)
    return out


def shadow_health(days: int = 5) -> list[dict[str, Any]]:
    """Summarize the shadow ledgers for the most recent ``days`` signal days, newest first.

    Returns:
        One dict per day with ``cards`` / ``lit``, ``card_fields`` and ``lit_fields``
        coverage ratios, ``missing`` (card fields under ``FIELD_MIN_COVER``),
        ``narrative`` / ``crowd`` / ``auction`` / ``pulse`` row counts,
        ``narrative_snap`` (narrative rows first seen on the snapshot pool), and
        ``crowd_max`` (highest industry / concept turnover share that day) so an
        empty crowd ledger can be compared with the ``CROWD_ABS_*`` thresholds.
    """
    from market_desk.config import CROWD_ABS_EXEMPT

    with _connect() as conn:
        day_rows = conn.execute(
            "SELECT DISTINCT trade_date FROM signals WHERE signal_type LIKE 'buy%' "
            "ORDER BY trade_date DESC LIMIT ?",
            (max(1, int(days)),),
        ).fetchall()
        dates = [str(r[0])[:10] for r in day_rows]
        if not dates:
            return []
        narrative = _count_by_day(conn, "narrative_shadow", dates)
        narrative_snap = _count_by_day(conn, "narrative_shadow", dates, "pool = 'snap'")
        crowd = _count_by_day(conn, "crowd_shadow", dates)
        auction = _count_by_day(conn, "auction_lock", dates)
        pulse = _count_by_day(conn, "etf_pulse_log", dates)
        marks = ",".join("?" * len(dates))
        crowd_max: dict[str, dict[str, Any]] = {}
        try:
            for day, name, kind, share in conn.execute(
                f"SELECT trade_date, name, kind, share FROM board_crowding WHERE trade_date IN ({marks})",
                dates,
            ):
                if name in CROWD_ABS_EXEMPT or share is None:
                    continue
                slot = crowd_max.setdefault(str(day), {})
                best = slot.get(kind)
                if best is None or float(share) > best["share"]:
                    slot[kind] = {"name": name, "share": round(float(share), 2)}
        except Exception:
            pass
        cards: dict[str, list[dict[str, Any]]] = {d: [] for d in dates}
        for day, raw in conn.execute(
            f"SELECT trade_date, payload FROM signals WHERE signal_type LIKE 'buy%' AND trade_date IN ({marks})",
            dates,
        ):
            try:
                cards[str(day)[:10]].append(json.loads(raw or "{}"))
            except (ValueError, KeyError):
                continue

    out: list[dict[str, Any]] = []
    for day in dates:
        rows = cards.get(day) or []
        lit = [p for p in rows if p.get("ever_ready")]

        def cover(items: list[dict[str, Any]], key: str) -> float | None:
            return round(sum(1 for p in items if p.get(key) is not None) / len(items), 2) if items else None

        card_fields = {k: cover(rows, k) for k in CARD_FIELDS}
        out.append(
            {
                "date": day,
                "cards": len(rows),
                "lit": len(lit),
                "card_fields": card_fields,
                "lit_fields": {k: cover(lit, k) for k in LIT_FIELDS},
                "missing": [k for k, v in card_fields.items() if v is not None and v < FIELD_MIN_COVER],
                "narrative": narrative.get(day, 0),
                "narrative_snap": narrative_snap.get(day, 0),
                "crowd": crowd.get(day, 0),
                "crowd_max": crowd_max.get(day, {}),
                "auction": auction.get(day, 0),
                "pulse": pulse.get(day, 0),
            }
        )
    return out


def shadow_check(rows: list[dict[str, Any]], narrative_live: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fold the latest ``shadow_health`` row into one ops-check entry.

    ``bad`` when card fields are missing (wiring broken), ``warn`` when the
    narrative ledger is empty on the snapshot-less pool, else ``ok``. The detail
    line always states why an empty ledger is empty.
    """
    from market_desk.config import CROWD_ABS_EXTREME_PCT

    if not rows:
        return {"id": "shadows", "title": "影子账本", "level": "ok", "detail": "尚无买卡"}
    r = rows[0]
    parts = [f"{r['date']} 买卡{r['cards']}·亮灯{r['lit']}"]
    level = "ok"
    if r["missing"]:
        level = "bad"
        parts.append("缺字段：" + "、".join(r["missing"]))
    ind = (r.get("crowd_max") or {}).get("industry")
    crowd_why = f"（行业最高 {ind['name']} {ind['share']}% < {CROWD_ABS_EXTREME_PCT}%）" if ind and not r["crowd"] else ""
    parts.append(f"拥挤{r['crowd']}{crowd_why}")
    pool = str((narrative_live or {}).get("pool") or "")
    concepts = (narrative_live or {}).get("concepts")
    pool_txt = f"（概念池 {'快照' if pool == 'snap' else '实时'} {concepts} 个）" if concepts is not None else ""
    snap_n = int(r.get("narrative_snap") or 0)
    split = f"（快照池 {snap_n}）" if snap_n and snap_n != r["narrative"] else ""
    parts.append(f"叙事{r['narrative']}{split}{pool_txt}")
    if not r["narrative"] and pool != "snap" and level == "ok":
        level = "warn"
    parts.append(f"竞价锁{r['auction']}·脉冲{r['pulse']}")
    return {"id": "shadows", "title": "影子账本", "level": level, "detail": " · ".join(parts)}
