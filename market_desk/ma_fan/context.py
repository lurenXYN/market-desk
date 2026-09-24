"""Desk / review linkage: theme and amount-band soft tags, review intersections."""

from __future__ import annotations

from typing import Any

from market_desk.filters import normalize_code
from market_desk.ma_fan.pattern import amount_band_for_rank


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
        item["review_nudge"] = 0.0
        item["score"] = round(score_base + score_nudge, 1)
        item["tags"] = tags
        item["note"] = " · ".join(tags) if tags else (item.get("note") or "命中")
        item.pop("_theme_nudged", None)
        out.append(item)
    out.sort(key=lambda h: float(h.get("score") or 0), reverse=True)
    return out


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
