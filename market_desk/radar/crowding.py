"""Crowding Index: board turnover share of the whole market and the no-new-open gate."""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from market_desk.microstructure.slippage import session_minutes_elapsed


def market_turnover(indices: list[dict[str, Any]] | None) -> float | None:
    """Return whole-market turnover in 元 (上证指数 + 深证成指), or ``None``."""
    by_code = {str(r.get("code") or ""): r for r in indices or []}
    total = 0.0
    for code in ("000001", "399001"):
        try:
            amt = float((by_code.get(code) or {}).get("amount") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt <= 0:
            return None
        total += amt
    return total


def evaluate_board(
    share: float,
    *,
    kind: str,
    history: list[float] | None,
    absolute: bool | None = None,
) -> dict[str, Any]:
    """Grade one board's turnover share against absolute and own-history bands.

    Industry boards use absolute bands (``CROWD_ABS_*``) plus relative bands;
    concept boards overlap heavily, so only relative bands with a higher floor
    apply. Relative bands need ``CROWD_HIST_MIN_DAYS`` of prior shares.

    Args:
        share: Today's turnover share in percent.
        kind: ``"industry"`` or ``"concept"``.
        history: Prior daily shares, oldest first.
        absolute: Override whether absolute bands apply (defaults to industry).

    Returns:
        ``{"level": "extreme"|"warn"|"ok", "reasons", "median20", "peak60", "mult"}``.
    """
    from market_desk.config import (
        CROWD_ABS_EXTREME_PCT,
        CROWD_ABS_WARN_PCT,
        CROWD_HIST_MIN_DAYS,
        CROWD_REL_EXTREME_MULT,
        CROWD_REL_FLOOR_CONCEPT,
        CROWD_REL_FLOOR_INDUSTRY,
        CROWD_REL_WARN_MULT,
    )

    hist = [float(x) for x in (history or []) if x is not None]
    med20 = peak60 = mult = None
    if len(hist) >= int(CROWD_HIST_MIN_DAYS):
        med20 = float(median(hist[-20:]))
        peak60 = max(hist[-60:])
        mult = share / med20 if med20 > 0 else None
    industry = kind == "industry"
    use_abs = industry if absolute is None else bool(absolute)
    floor = float(CROWD_REL_FLOOR_INDUSTRY if industry else CROWD_REL_FLOOR_CONCEPT)
    reasons: list[str] = []
    level = "ok"
    if use_abs and share >= float(CROWD_ABS_EXTREME_PCT):
        level = "extreme"
        reasons.append(f"成交占比{share:.1f}%≥{float(CROWD_ABS_EXTREME_PCT):.0f}%")
    if (
        mult is not None
        and share >= floor
        and mult >= float(CROWD_REL_EXTREME_MULT)
        and peak60 is not None
        and share >= peak60
    ):
        level = "extreme"
        reasons.append(f"占比为20日中位{mult:.1f}倍且创60日新高")
    if level == "ok":
        if use_abs and share >= float(CROWD_ABS_WARN_PCT):
            level = "warn"
            reasons.append(f"成交占比{share:.1f}%≥{float(CROWD_ABS_WARN_PCT):.0f}%")
        elif mult is not None and share >= floor and mult >= float(CROWD_REL_WARN_MULT):
            level = "warn"
            reasons.append(f"占比为20日中位{mult:.1f}倍")
    return {
        "level": level,
        "reasons": reasons,
        "median20": None if med20 is None else round(med20, 2),
        "peak60": None if peak60 is None else round(peak60, 2),
        "mult": None if mult is None else round(mult, 2),
    }


def compute_crowding(
    boards: list[dict[str, Any]] | None,
    market: float | None,
    history: dict[str, list[float]] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Compute turnover shares for every board and grade them.

    Args:
        boards: Raw board universe with ``name`` / ``kind`` / ``amount`` (元).
        market: Whole-market turnover in 元.
        history: ``{name: [prior shares]}`` from ``load_board_crowding_history``.
        now: Wall clock (skips the first ``CROWD_MIN_SESSION_MIN`` minutes).

    Returns:
        ``{"ok", "note", "market_yi", "boards": {name: row}, "top", "extreme", "warn"}``.
    """
    from market_desk.config import CROWD_ABS_EXEMPT, CROWD_MIN_MARKET_YI, CROWD_MIN_SESSION_MIN

    out: dict[str, Any] = {
        "ok": False,
        "note": "",
        "market_yi": None,
        "boards": {},
        "top": [],
        "extreme": [],
        "warn": [],
    }
    elapsed = session_minutes_elapsed(now)
    if elapsed is None or elapsed < int(CROWD_MIN_SESSION_MIN):
        out["note"] = "开盘样本不足，暂不评拥挤"
        return out
    if not market or market / 1e8 < float(CROWD_MIN_MARKET_YI):
        out["note"] = "全市场成交额缺失"
        return out
    out["market_yi"] = round(market / 1e8, 0)
    rows: dict[str, dict[str, Any]] = {}
    for b in boards or []:
        name = str(b.get("name") or "").strip()
        kind = str(b.get("kind") or "concept")
        try:
            turnover = float(b.get("amount") or 0.0)
        except (TypeError, ValueError):
            turnover = 0.0
        if not name or turnover <= 0:
            continue
        if name in rows and rows[name]["kind"] == "industry":
            continue
        share = turnover / market * 100.0
        parent = kind == "industry" and name in CROWD_ABS_EXEMPT
        grade = evaluate_board(
            share,
            kind=kind,
            history=(history or {}).get(name),
            absolute=kind == "industry" and not parent,
        )
        rows[name] = {
            "name": name,
            "kind": kind,
            "share": round(share, 2),
            "turnover": turnover,
            "parent": parent,
            **grade,
        }
    if not rows:
        out["note"] = "板块成交额缺失"
        return out
    ranked = sorted(rows.values(), key=lambda r: -float(r["share"]))
    out.update(
        ok=True,
        boards=rows,
        top=[r for r in ranked if r["kind"] == "industry" and not r["parent"]][:5],
        extreme=[r for r in ranked if r["level"] == "extreme"],
        warn=[r for r in ranked if r["level"] == "warn"],
    )
    return out


def _box_boards(verdict: dict[str, Any]) -> list[tuple[str, str]]:
    """Map recommend box keys to their board name (item-level override handled later)."""
    ml = str(((verdict.get("mainline") or {}).get("name")) or "")
    side = str(((verdict.get("side_mainline") or {}).get("name")) or "")
    link = str(((verdict.get("link_mainline") or {}).get("name")) or "")
    return [
        ("recommend", ml),
        ("dragon_recommend", ml),
        ("side_recommend", side),
        ("link_recommend", link),
        ("independent_recommend", ml),
    ]


def climax_confirm(
    card: dict[str, Any] | None,
    *,
    phase: str = "",
) -> list[str]:
    """Return climax / ebb confirmations for an extreme-crowded board.

    Any hit upgrades the crowding verdict from strong-soft to a hard ban:
    market phase is ``高潮``; the board card carries an ebb flag in
    ``CROWD_CONFIRM_FLAGS`` (stall / A-kill / sky-to-floor / red-to-green); or
    broken limit-ups reach ``CROWD_CONFIRM_ZB_RATIO`` of touched limit-ups.

    Args:
        card: Enriched board card (``flags`` / ``zt_n`` / ``zb_n``), may be ``None``.
        phase: Market phase label.

    Returns:
        Human-readable confirmation reasons (empty when unconfirmed).
    """
    from market_desk.config import CROWD_CONFIRM_FLAGS, CROWD_CONFIRM_ZB_MIN, CROWD_CONFIRM_ZB_RATIO

    out: list[str] = []
    if phase == "高潮":
        out.append("相位高潮")
    c = card or {}
    flags = c.get("flags") or {}
    for name in CROWD_CONFIRM_FLAGS:
        if flags.get(name):
            out.append(f"板块{name}")
    zt_n = int(c.get("zt_n") or 0)
    zb_n = int(c.get("zb_n") or 0)
    if zb_n >= int(CROWD_CONFIRM_ZB_MIN) and zb_n / max(zt_n + zb_n, 1) >= float(CROWD_CONFIRM_ZB_RATIO):
        out.append(f"炸板{zb_n}/{zt_n + zb_n}")
    return out


def _add_label(item: dict[str, Any], key: str, label: str) -> None:
    """Append ``label`` to the list at ``item[key]`` once."""
    vals = list(item.get(key) or [])
    if label not in vals:
        vals.append(label)
    item[key] = vals


def apply_crowding_gate(
    verdict: dict[str, Any] | None,
    crowding: dict[str, Any] | None,
    *,
    cards: list[dict[str, Any]] | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    """Conditional no-new-open on extreme-crowded boards; soft size cut on warned ones.

    Extreme + climax confirmation (``climax_confirm``) → hard ban: every card
    of that board loses ``ready`` / probe, gets ``block_ready`` plus the hard
    ``CROWD_BAN_FLAG`` confirm-fail, and the box stops buying. A hard-banned
    mainline adds an ``algo_notes`` token in ``BUY_DEMOTE_LOCK_NOTES`` so the
    hero action cannot upgrade.

    Extreme without confirmation → strong soft: fully confirmed ready cards
    stay but shrink once by ``CROWD_EXTREME_SOFT_MULT``; every other card gets
    ``block_ready`` so band-relax / probe cannot arm it later this round.

    Warn: ready cards shrink once by ``CROWD_WARN_SIZE_MULT``.

    Every card touched by an extreme board is listed in ``crowding.hits`` for
    the shadow ledger (what a hard ban would have blocked).

    Args:
        verdict: Desk verdict with recommend boxes.
        crowding: ``compute_crowding`` output.
        cards: Enriched board cards used for climax confirmation.
        phase: Market phase (defaults to ``verdict["phase"]``).

    Returns:
        A copy of ``verdict`` with ``crowding`` summary attached.
    """
    from market_desk.config import (
        CROWD_BAN_FLAG,
        CROWD_EXTREME_SOFT_MULT,
        CROWD_SOFT_FLAG,
        CROWD_WARN_SIZE_MULT,
    )
    from market_desk.verdict.common import _demote_buy_to_wait, _join_hint, _scale_item_qty

    v = dict(verdict or {})
    crowd = crowding or {}
    rows = crowd.get("boards") or {}
    phase_s = str(phase if phase is not None else v.get("phase") or "")
    card_by_name: dict[str, dict[str, Any]] = {}
    for c in cards or []:
        name = str(c.get("name") or "")
        if name and name not in card_by_name:
            card_by_name[name] = c
    modes: dict[str, str] = {}
    confirms: dict[str, list[str]] = {}
    for r in crowd.get("extreme") or []:
        why = climax_confirm(card_by_name.get(r["name"]), phase=phase_s)
        confirms[r["name"]] = why
        modes[r["name"]] = "hard" if why else "soft"
    v["crowding"] = {
        "ok": bool(crowd.get("ok")),
        "note": crowd.get("note") or "",
        "market_yi": crowd.get("market_yi"),
        "top": crowd.get("top") or [],
        "extreme": [r["name"] for r in crowd.get("extreme") or []],
        "warn": [r["name"] for r in crowd.get("warn") or []],
        "shares": {
            r["name"]: r["share"] for r in (crowd.get("extreme") or []) + (crowd.get("warn") or [])
        },
        "modes": modes,
        "confirm": confirms,
        "banned": [],
        "soft": [],
        "hits": [],
    }
    if not crowd.get("ok") or not rows:
        return v

    def _tip(row: dict[str, Any]) -> str:
        why = "；".join(row.get("reasons") or [])
        return f"{row['name']}{why and '：' + why}"

    banned: list[str] = []
    softened: list[str] = []
    hits: list[dict[str, Any]] = []
    for key, box_board in _box_boards(v):
        rec = v.get(key)
        if not isinstance(rec, dict) or not rec.get("items"):
            continue
        rec = dict(rec)
        items: list[dict[str, Any]] = []
        box_banned = box_soft = False
        for raw in rec.get("items") or []:
            item = dict(raw)
            board = str(item.get("source_board") or box_board or "")
            row = rows.get(board)
            if row and row["level"] == "extreme":
                mode = modes.get(board, "soft")
                was_ready = bool(item.get("ready"))
                item["crowding"] = row
                item["crowd_mode"] = mode
                hits.append(
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "board": board,
                        "box": key,
                        "mode": mode,
                        "was_ready": was_ready,
                        "price": item.get("last") or item.get("price_now") or item.get("buy_price"),
                        "share": row.get("share"),
                    }
                )
                if mode == "hard":
                    box_banned = True
                    item["ready"] = False
                    item["probe_ok"] = False
                    item["block_ready"] = True
                    item["crowd_block"] = True
                    if was_ready:
                        _demote_buy_to_wait(item)
                    _add_label(item, "confirm_fail", CROWD_BAN_FLAG)
                    why = "、".join(confirms.get(board) or [])
                    item["reason"] = _join_hint(
                        f"⚠ 极端拥挤·高潮禁开：{_tip(row)}（确认：{why}）", str(item.get("reason") or "")
                    )
                    if board not in banned:
                        banned.append(board)
                else:
                    box_soft = True
                    item["probe_ok"] = False
                    if was_ready and not item.get("ready_relaxed"):
                        if not item.get("crowd_scaled"):
                            _scale_item_qty(item, float(CROWD_EXTREME_SOFT_MULT), CROWD_SOFT_FLAG)
                            item["crowd_scaled"] = True
                    else:
                        item["block_ready"] = True
                        if was_ready:
                            item["ready"] = False
                            _demote_buy_to_wait(item)
                    _add_label(item, "confirm_soft", CROWD_SOFT_FLAG)
                    item["crowd_warn"] = f"极端拥挤（未见高潮确认）：{_tip(row)}，仓位减半，只做完全确认的 ready"
                    item["reason"] = _join_hint(f"⚠ {item['crowd_warn']}", str(item.get("reason") or ""))
                    if board not in softened:
                        softened.append(board)
            elif row and row["level"] == "warn":
                item["crowding"] = row
                if item.get("ready") and not item.get("crowd_scaled"):
                    _scale_item_qty(item, float(CROWD_WARN_SIZE_MULT), "板块拥挤·降仓")
                    item["crowd_scaled"] = True
                    soft = list(item.get("confirm_soft") or [])
                    if "板块拥挤·降仓" not in soft:
                        soft.append("板块拥挤·降仓")
                    item["confirm_soft"] = soft
            items.append(item)
        rec["items"] = items
        any_ready = any(x.get("ready") for x in items)
        if box_banned and not any_ready:
            rec["buy"] = False
            rec["probe"] = False
            rec["title"] = "极端拥挤 · 禁开新仓"
            rec["size_note"] = _join_hint("⚠ 板块成交极端拥挤且见高潮，警惕主升鱼尾，只减不开", str(rec.get("size_note") or ""))
        elif box_soft:
            rec["probe"] = False
            if not any_ready:
                rec["buy"] = False
            rec["size_note"] = _join_hint(
                "⚠ 板块成交极端拥挤：只做完全确认的 ready，仓位减半", str(rec.get("size_note") or "")
            )
        v[key] = rec

    ml = str(((v.get("mainline") or {}).get("name")) or "")
    ml_row = rows.get(ml)
    if ml_row and ml_row["level"] == "extreme":
        notes = list(v.get("algo_notes") or [])
        if modes.get(ml) == "hard":
            note = "极端拥挤禁开"
            bans = list(v.get("bans") or [])
            ban = f"{ml}极端拥挤"
            if ban not in bans:
                bans.append(ban)
            v["bans"] = bans
            why = "、".join(confirms.get(ml) or [])
            v["reason"] = _join_hint(
                str(v.get("reason") or ""), f"主线成交极端拥挤：{_tip(ml_row)}，且{why}，高潮禁开新仓"
            )
        else:
            note = "极端拥挤·半仓"
            v["reason"] = _join_hint(
                str(v.get("reason") or ""), f"主线成交极端拥挤：{_tip(ml_row)}，未见高潮确认，仓位减半"
            )
        if note not in notes:
            notes.append(note)
        v["algo_notes"] = notes
    v["crowding"].update(banned=banned, soft=softened, hits=hits)
    return v
