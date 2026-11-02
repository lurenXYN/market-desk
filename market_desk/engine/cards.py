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
        Sina full list. Member fetch keeps the matched board's live identity
        (Sina node / BK); the sealed ``bk``/name are restored afterward so the
        frozen column can remount. Quote-only cards (empty constituents) are
        kept when the board list already has a live ``pct``.
        """
        from market_desk import board_fallback
        from market_desk.board_alias import normalize_board_name
        from market_desk.config import LIFECYCLE_SIDE_MAX

        try:
            from market_desk.db.market import board_alias_name
        except Exception:  # noqa: BLE001
            board_alias_name = lambda _n: None  # type: ignore[assignment]

        off = [
            r
            for col in ("starting", "ongoing", "peak", "ending")
            for r in (lifecycle.get(col) or [])
            if r.get("off_hot") and r.get("bk")
        ][: int(LIFECYCLE_SIDE_MAX)]
        if not off:
            return {}

        def _name_keys(raw: Any) -> list[str]:
            text = str(raw or "").strip()
            if not text:
                return []
            keys = [text]
            norm = normalize_board_name(text)
            if norm and norm not in keys:
                keys.append(norm)
            try:
                alias = board_alias_name(text) or board_alias_name(norm)
            except Exception:
                alias = None
            if alias and alias not in keys:
                keys.append(alias)
            return keys

        def _index(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
            by_bk = {str(b.get("bk") or "").upper(): b for b in rows if b.get("bk")}
            by_name: dict[str, Any] = {}
            for b in rows:
                for key in _name_keys(b.get("name")) + _name_keys(b.get("sina_name")):
                    by_name.setdefault(key, b)
            return by_bk, by_name

        def _lookup(
            by_bk: dict[str, Any], by_name: dict[str, Any], bk: str, name: str
        ) -> dict[str, Any] | None:
            hit = by_bk.get(bk.upper())
            if hit is not None:
                return hit
            for key in _name_keys(name):
                hit = by_name.get(key)
                if hit is not None:
                    return hit
            return None

        by_bk, by_name = _index(boards)
        picks: list[tuple[str, dict[str, Any] | None, str]] = []
        for r in off:
            bk, name = str(r["bk"]), str(r.get("name") or "")
            picks.append((bk, _lookup(by_bk, by_name, bk, name), name))
        if any(match is None for _, match, _ in picks):
            try:
                sina_bk, sina_name = _index(await board_fallback.fetch_hot_boards_sina(client))
                picks = [
                    (bk, match or _lookup(sina_bk, sina_name, bk, name), name)
                    for bk, match, name in picks
                ]
            except Exception as exc:
                log.warning("lifecycle side boards: sina list failed: %r", exc)

        async def _one(pick: tuple[str, dict[str, Any] | None, str]) -> dict[str, Any] | None:
            seal_bk, match, seal_name = pick
            if match is None:
                return None
            # Keep the matched board's bk/sina_node for constituent fetch — do not
            # overwrite with the sealed BK first (that breaks Sina node lookup when
            # East Money clist is paused).
            board = dict(match)
            try:
                card = await _enrich_board(client, board, ctx)
            except Exception as exc:
                log.warning("lifecycle side board %s enrich failed: %r", seal_bk, exc)
                return None
            if not card:
                return None
            if not card.get("pool"):
                # Board-list pct is still live; better than freezing on last close.
                if match.get("pct") is None and card.get("pct") is None:
                    return None
                card["pool"] = []
                card["members"] = card.get("members") or []
            # Remount onto the sealed identity so frozen columns find this card.
            if str(seal_bk or "").upper().startswith("BK"):
                card["bk"] = seal_bk
            if seal_name:
                card["name"] = seal_name
            return card

        cards = await _map_capped(_one, picks, limit=3)
        out = {str(c["bk"]): c for c in cards if c and c.get("bk")}
        if picks and not out:
            missing = [f"{bk}/{name}" for bk, match, name in picks if match is None]
            log.warning(
                "lifecycle side boards: 0/%s live (unmatched=%s)",
                len(picks),
                ",".join(missing[:6]) or "enrich/pool",
            )
        return out

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
