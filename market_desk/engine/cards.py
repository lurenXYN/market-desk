"""Hot, pinned, favorite, lifecycle-side, and ice board cards."""

from __future__ import annotations

import logging
from typing import Any
import httpx
from market_desk.config import HOT_BOARD_COUNT, ICE_BOARD_COUNT, PIN_INDUSTRY_ALIASES

from market_desk.engine.util import _map_capped
from market_desk.engine.boards import (
    _enrich_board,
    _join_board_note,
    _pick_pin_board,
    _rank_ice_boards,
)

log = logging.getLogger("market_desk")


class CardsMixin:
    """Hot, pinned, favorite, lifecycle-side, and ice board cards."""

    async def _hot_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            boards,
            key=lambda b: (b.get("pct") or 0) * 2 + (b.get("up_count") or 0) * 0.02,
            reverse=True,
        )
        picked = ranked[:12]
        if not picked:
            return []
        enriched = await _map_capped(
            lambda board: _enrich_board(client, board, ctx),
            picked,
            limit=3,
        )
        enriched = [x for x in enriched if x]
        enriched.sort(
            key=lambda b: (b.get("zt_n") or 0) * 4 + (b.get("pct") or 0),
            reverse=True,
        )
        return enriched[:HOT_BOARD_COUNT]

    async def _pin_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        hot: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        hot_names = {x["name"] for x in hot}
        industry = [b for b in boards if b.get("kind") == "industry"]
        tasks: list[tuple[str, dict[str, Any]]] = []
        for label, aliases in PIN_INDUSTRY_ALIASES.items():
            match = _pick_pin_board(industry, aliases)
            if not match:
                continue
            match = dict(match)
            match["already_hot"] = match["name"] in hot_names
            tasks.append((label, match))
        if not tasks:
            return []

        async def _one(pair: tuple[str, dict[str, Any]]) -> dict[str, Any]:
            label, board = pair
            card = await _enrich_board(client, board, ctx)
            card["pin_label"] = label
            return card

        return list(await _map_capped(_one, tasks, limit=3))

    async def _favorite_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        rows: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Enrich personally favored boards from the live board universe."""
        if not rows:
            return []
        by_bk = {
            str(b.get("bk") or "").upper(): b
            for b in boards
            if b.get("bk")
        }
        # Cap enrich fan-out; keep newest favorites first (rows already DESC).
        picked = rows[:12]

        async def _one(row: dict[str, Any]) -> dict[str, Any] | None:
            bk = str(row.get("bk") or "").upper()
            if not bk:
                return None
            match = by_bk.get(bk)
            if not match:
                match = {
                    "bk": bk,
                    "name": row.get("name") or bk,
                    "kind": row.get("kind") or "industry",
                    "pct": None,
                    "amount": None,
                    "up_count": 0,
                    "down_count": 0,
                    "leader_name": "",
                    "leader_code": "",
                    "leader_pct": None,
                }
            card = await _enrich_board(client, dict(match), ctx)
            if not card:
                return None
            card["favorite_id"] = row.get("id")
            card["favorite_note"] = row.get("note") or ""
            card["in_favorite"] = True
            if row.get("note"):
                card["note"] = _join_board_note(
                    card.get("note"), f"看好备注：{row.get('note')}"
                )
            return card

        enriched = await _map_capped(_one, picked, limit=3)
        return [card for card in enriched if card]

    async def _lifecycle_side_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        lifecycle: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Enrich frozen lifecycle boards absent from today's hot list, keyed by ``bk``.

        Looks each board up in this tick's universe first, then in the cached
        Sina full list; boards found nowhere keep their last-close row.
        """
        from market_desk import board_fallback
        from market_desk.config import LIFECYCLE_SIDE_MAX

        off = [
            r
            for col in ("starting", "ongoing", "ending")
            for r in (lifecycle.get(col) or [])
            if r.get("off_hot") and r.get("bk")
        ][: int(LIFECYCLE_SIDE_MAX)]
        if not off:
            return {}

        def _index(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
            by_bk = {str(b.get("bk") or "").upper(): b for b in rows if b.get("bk")}
            by_name = {str(b.get("name") or ""): b for b in rows if b.get("name")}
            return by_bk, by_name

        by_bk, by_name = _index(boards)
        picks: list[tuple[str, dict[str, Any] | None, str]] = []
        for r in off:
            bk, name = str(r["bk"]), str(r.get("name") or "")
            picks.append((bk, by_bk.get(bk.upper()) or by_name.get(name), name))
        if any(match is None for _, match, _ in picks):
            try:
                sina_bk, sina_name = _index(await board_fallback.fetch_hot_boards_sina(client))
                picks = [
                    (bk, match or sina_bk.get(bk.upper()) or sina_name.get(name), name)
                    for bk, match, name in picks
                ]
            except Exception as exc:
                log.debug("lifecycle side boards: sina list failed: %r", exc)

        async def _one(pick: tuple[str, dict[str, Any] | None, str]) -> dict[str, Any] | None:
            bk, match, _name = pick
            if match is None:
                return None
            try:
                card = await _enrich_board(client, dict(match, bk=bk), ctx)
            except Exception as exc:
                log.debug("lifecycle side board %s enrich failed: %r", bk, exc)
                return None
            # No members means zt counts would read 0; keep the last-close row instead.
            return card if card and card.get("pool") else None

        cards = await _map_capped(_one, picks, limit=3)
        return {str(c["bk"]): c for c in cards if c and c.get("bk")}

    async def _ice_cards(
        self,
        client: httpx.AsyncClient,
        boards: list[dict[str, Any]],
        hot: list[dict[str, Any]],
        ctx: dict[str, Any],
    ) -> list[dict[str, Any]]:
        picked = _rank_ice_boards(boards, {x["name"] for x in hot})
        if not picked:
            return []
        enriched = await _map_capped(
            lambda board: _enrich_board(client, board, ctx, weakest=True),
            picked,
            limit=3,
        )
        cards = [x for x in enriched if x]
        cards.sort(
            key=lambda b: (
                0 if b.get("status") == "传染预警" else 1,
                b.get("pct") if b.get("pct") is not None else 0,
            )
        )
        return cards[:ICE_BOARD_COUNT]
