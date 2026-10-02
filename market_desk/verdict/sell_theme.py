"""Sell-side theme membership: which hot themes a held name belongs to."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import mainline_score, same_theme

from market_desk.verdict.common import (
    _lookup_hot_board,
    _pool_codes_from_board,
    _theme_entry,
)
from market_desk.verdict.branches import _recommend_codes


def build_sell_themes(
    *,
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any] | None,
    vehicle: dict[str, Any] | None,
    side_info: dict[str, Any] | None,
    recommend: dict[str, Any] | None = None,
    side_recommend: dict[str, Any] | None = None,
    link_info: dict[str, Any] | None = None,
    link_recommend: dict[str, Any] | None = None,
    life_stage: str | None = None,
) -> list[dict[str, Any]]:
    """Build multi-theme set for sells; buys still follow sticky mainline only.

    Includes sticky primary, observation side branch, soft link peer, and other
    hot boards within ``SELL_THEME_GAP`` of the sticky score (退潮 peers kept so
    residual positions can still soft-exit on their own theme fade).
    """
    from market_desk.config import SELL_THEME_GAP, SELL_THEME_MAX

    main = main or {}
    vehicle = vehicle or {}
    primary = str(main.get("name") or "").strip()
    themes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(entry: dict[str, Any]) -> None:
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            return
        if len(themes) >= int(SELL_THEME_MAX):
            return
        seen.add(name)
        themes.append(entry)

    if primary:
        _add(
            _theme_entry(
                name=primary,
                role="primary",
                status=str(main.get("status") or ""),
                lifecycle=life_stage or classify_lifecycle(main),
                pool_codes=_pool_codes_from_board(main),
                carrier_code=vehicle.get("code"),
                rec_codes=_recommend_codes(recommend),
                score=mainline_score(main) if main else None,
                main_yi=main.get("main_yi"),
                board=main
                if (main.get("pool") or main.get("members"))
                else _lookup_hot_board(hot, name=primary, bk=main.get("bk")),
            )
        )

    side = side_info or {}
    side_name = str(side.get("name") or "").strip()
    if side_name:
        _add(
            _theme_entry(
                name=side_name,
                role="side",
                status=str(side.get("status") or ""),
                lifecycle=side.get("lifecycle") or "",
                pool_codes=list(side.get("pool_codes") or []),
                carrier_code=side.get("carrier_code"),
                rec_codes=_recommend_codes(side_recommend),
                score=side.get("score"),
                main_yi=side.get("main_yi"),
                board=_lookup_hot_board(hot, name=side_name, bk=side.get("bk")),
            )
        )

    link = link_info or {}
    link_name = str(link.get("name") or "").strip()
    if link_name:
        _add(
            _theme_entry(
                name=link_name,
                role="link",
                status=str(link.get("status") or ""),
                lifecycle=link.get("lifecycle") or "",
                pool_codes=list(link.get("pool_codes") or []),
                carrier_code=link.get("carrier_code"),
                rec_codes=_recommend_codes(link_recommend),
                score=link.get("score"),
                main_yi=link.get("main_yi"),
                board=_lookup_hot_board(hot, name=link_name, bk=link.get("bk")),
            )
        )

    gap = float(SELL_THEME_GAP)
    boards = list(hot or [])
    industries = [b for b in boards if b.get("kind") == "industry"]
    pool = industries or boards
    main_score = mainline_score(main) if primary else 0.0
    # Pass 1: competitive peers within gap. Pass 2: fading leftovers (any gap).
    ranked = sorted(pool, key=mainline_score, reverse=True)

    def _maybe_add(board: dict[str, Any], *, allow_outside_fade: bool) -> None:
        name = str(board.get("name") or "").strip()
        if not name or name in seen:
            return
        sc = mainline_score(board)
        status = str(board.get("status") or "")
        life = classify_lifecycle(board)
        outside = bool(primary) and (main_score - sc) > gap
        fading = status == "退潮" or life == "ending"
        if outside and not (allow_outside_fade and fading):
            return
        _add(
            _theme_entry(
                name=name,
                role="hot",
                status=status,
                lifecycle=life,
                pool_codes=_pool_codes_from_board(board),
                carrier_code=None,
                score=round(sc, 1),
                main_yi=board.get("main_yi"),
                board=board,
            )
        )

    for board in ranked:
        _maybe_add(board, allow_outside_fade=False)
    for board in ranked:
        _maybe_add(board, allow_outside_fade=True)
    return themes


def _position_tied_to_mainline(row: dict[str, Any], verdict: dict[str, Any]) -> bool:
    """Return True when a held name is the live carrier or in the mainline pool.

    Soft mainline-fade sells should not fire on unrelated residual positions.
    Prefer ``_sell_theme_context`` for sells (multi-theme); this remains the
    sticky-only fallback when ``sell_themes`` is absent.
    """
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code((verdict.get("carrier") or {}).get("code"))
    if carrier and code == carrier:
        return True
    main = verdict.get("mainline") or {}
    pool = {normalize_code(c) for c in (main.get("pool_codes") or []) if c}
    if code in pool:
        return True
    for item in ((verdict.get("recommend") or {}).get("items") or []):
        if normalize_code(item.get("code")) == code:
            return True
    board = str(row.get("board") or row.get("sector") or row.get("industry") or "")
    main_name = str(main.get("name") or "")
    if board and main_name and (board in main_name or main_name in board):
        return True
    if board and main_name and same_theme(board, main_name):
        return True
    return False


def _match_sell_theme(row: dict[str, Any], theme: dict[str, Any]) -> bool:
    """Return True when a position belongs to one sell-theme descriptor."""
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code(theme.get("carrier_code"))
    if carrier and code == carrier:
        return True
    pool = {normalize_code(c) for c in (theme.get("pool_codes") or []) if c}
    if code in pool:
        return True
    rec = {normalize_code(c) for c in (theme.get("rec_codes") or []) if c}
    if code in rec:
        return True
    name = str(theme.get("name") or "")
    labels: list[str] = []
    for raw in (
        row.get("board"),
        row.get("sector"),
        row.get("industry"),
        row.get("entry_board"),
    ):
        text = str(raw or "").strip()
        if text:
            labels.append(text)
    for raw in row.get("board_names") or []:
        text = str(raw or "").strip()
        if text:
            labels.append(text)
    for held in labels:
        if name and (held in name or name in held or same_theme(held, name)):
            return True
    return False


def _sell_theme_context(row: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Resolve which sell-theme(s) a position belongs to and the active context.

    Buys ignore this. Sells use multi-theme membership so a side/hot peer fade
    can trim the right bags without requiring them to be today's sticky name.
    When both strong and fading themes match, fade wins (safer exit bias).
    """
    themes = list(verdict.get("sell_themes") or [])
    if not themes:
        tied = _position_tied_to_mainline(row, verdict)
        main = verdict.get("mainline") or {}
        status = str(main.get("status") or "")
        life = str(main.get("lifecycle") or "")
        fade = status == "退潮" or life == "ending"
        return {
            "tied": tied,
            "name": main.get("name") if tied else None,
            "role": "primary" if tied else None,
            "status": status if tied else "",
            "lifecycle": life if tied else "",
            "fade": bool(tied and fade),
            "carrier_falling": bool(tied and (verdict.get("carrier") or {}).get("falling")),
            "carrier_pct": (verdict.get("carrier") or {}).get("pct") if tied else None,
            "matched_names": [main.get("name")] if tied and main.get("name") else [],
        }

    matches = [t for t in themes if _match_sell_theme(row, t)]
    if not matches:
        return {
            "tied": False,
            "name": None,
            "role": None,
            "status": "",
            "lifecycle": "",
            "fade": False,
            "carrier_falling": False,
            "carrier_pct": None,
            "matched_names": [],
        }

    def _is_fade(t: dict[str, Any]) -> bool:
        return str(t.get("status") or "") == "退潮" or str(t.get("lifecycle") or "") == "ending"

    def _is_strong(t: dict[str, Any]) -> bool:
        return str(t.get("status") or "") == "确认中" and str(t.get("lifecycle") or "") != "ending"

    fade_hits = [t for t in matches if _is_fade(t)]
    strong_hits = [t for t in matches if _is_strong(t)]
    # Prefer primary among equals so sticky context stays visible in reasons.
    def _rank(t: dict[str, Any]) -> tuple[int, float]:
        role_rank = {
            "primary": 0,
            "side": 1,
            "switch_from": 2,
            "link": 3,
            "hot": 4,
        }.get(str(t.get("role") or ""), 9)
        try:
            sc = -float(t.get("score") or 0)
        except (TypeError, ValueError):
            sc = 0.0
        return (role_rank, sc)

    fade_hits.sort(key=_rank)
    strong_hits.sort(key=_rank)
    matches_sorted = sorted(matches, key=_rank)
    ctx = fade_hits[0] if fade_hits else (strong_hits[0] if strong_hits else matches_sorted[0])
    tied_primary = any(str(t.get("role") or "") == "primary" for t in matches)
    return {
        "tied": True,
        "name": ctx.get("name"),
        "role": ctx.get("role"),
        "status": str(ctx.get("status") or ""),
        "lifecycle": str(ctx.get("lifecycle") or ""),
        "fade": bool(fade_hits),
        "carrier_falling": bool(
            tied_primary and (verdict.get("carrier") or {}).get("falling")
        ),
        "carrier_pct": (verdict.get("carrier") or {}).get("pct") if tied_primary else None,
        "matched_names": [str(t.get("name")) for t in matches_sorted if t.get("name")],
    }
