"""Runtime self-learning of Sina → East Money board aliases from constituent overlap."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from market_desk.calendar import is_trading_day
from market_desk.config import (
    ALIAS_EM_COVERAGE_MIN,
    ALIAS_LEARN_EM_PER_TICK,
    ALIAS_LEARN_SINA_PER_TICK,
    ALIAS_SNAP_STALE_DAYS,
    CROWD_ABS_EXEMPT,
)

log = logging.getLogger("market_desk")

_TICK_SEC = 55.0
_EM_BACKOFF_SEC = 1800.0


def _alias_window(now: datetime) -> bool:
    """Crawl only off-hours so learning never competes with live board calls."""
    if not is_trading_day(now):
        return True
    minutes = now.hour * 60 + now.minute
    return minutes < 9 * 60 or minutes >= 15 * 60 + 30


def _stale(at: str, now: datetime) -> bool:
    try:
        stamp = datetime.strptime(str(at or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return now.replace(tzinfo=None) - stamp > timedelta(days=int(ALIAS_SNAP_STALE_DAYS))


class AliasLearnMixin:
    """Snapshot board constituents on both sources off-hours and learn aliases."""

    def _load_learned_aliases(self) -> int:
        """Apply learned aliases persisted in ``board_alias_learned``."""
        from market_desk.board_alias import set_learned_aliases
        from market_desk.db import load_board_alias_learned

        return set_learned_aliases(load_board_alias_learned())

    async def _maybe_learn_aliases(self, now: datetime) -> None:
        """One budgeted idle-loop step: snapshot a few boards, learn when coverage allows."""
        if not _alias_window(now):
            return
        now_ts = now.timestamp()
        if now_ts - self._alias_tick_at < _TICK_SEC:
            return
        self._alias_tick_at = now_ts
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                sina_todo = await self._alias_snap_sina(client, now)
                em_todo = await self._alias_snap_em(client, now)
            self._alias_progress.update({"sina_todo": sina_todo, "em_todo": em_todo})
            if sina_todo == 0 and self._alias_progress.get("em_cov", 0.0) >= float(ALIAS_EM_COVERAGE_MIN):
                self._alias_learn_now(now)
        except Exception:
            log.exception("alias learning step failed")

    async def _alias_snap_sina(self, client: httpx.AsyncClient, now: datetime) -> int:
        """Snapshot unmapped / learned Sina boards; return how many still need a snapshot."""
        from market_desk import board_fallback as bf
        from market_desk.db import load_board_members_snap, save_board_members_snap

        rows = await bf.fetch_hot_boards_sina(client)
        targets = {
            (r.get("sina_name") or r["name"]): r
            for r in rows
            if str(r.get("bk") or "").startswith(bf.SINA_KEY_PREFIX) or r.get("alias") == "learned"
        }
        snaps = load_board_members_snap("sina", with_codes=False)
        todo = [n for n in targets if n not in snaps or _stale(snaps[n]["at"], now)]
        for name in todo[: int(ALIAS_LEARN_SINA_PER_TICK)]:
            row = targets[name]
            codes = await bf.fetch_node_codes_sina(client, str(row.get("sina_node") or ""))
            save_board_members_snap(
                "sina", name, codes, name=name, kind=str(row.get("sina_kind") or row.get("kind") or "")
            )
        self._alias_progress["sina_targets"] = len(targets)
        return max(0, len(todo) - int(ALIAS_LEARN_SINA_PER_TICK))

    async def _alias_snap_em(self, client: httpx.AsyncClient, now: datetime) -> int:
        """Snapshot East Money boards within budget; back off when East Money is blocked."""
        from market_desk import board_fallback as bf
        from market_desk.db import load_board_members_snap, save_board_members_snap
        from market_desk.eastmoney import fetch_board_codes_em

        boards = {bk: (name, kind) for name, (bk, kind) in bf._em_boards().items() if name not in CROWD_ABS_EXEMPT}
        snaps = load_board_members_snap("em", with_codes=False)
        fresh = [bk for bk in boards if bk in snaps and not _stale(snaps[bk]["at"], now)]
        self._alias_progress["em_cov"] = round(len(fresh) / len(boards), 3) if boards else 0.0
        todo = [bk for bk in boards if bk not in fresh]
        if not todo or now.timestamp() < self._alias_em_backoff_until:
            return len(todo)
        empties: list[str] = []
        for bk in todo[: int(ALIAS_LEARN_EM_PER_TICK)]:
            codes = await fetch_board_codes_em(client, bk)
            if not codes:
                empties.append(bk)
                if len(empties) >= 2:
                    self._alias_em_backoff_until = now.timestamp() + _EM_BACKOFF_SEC
                    log.info("alias learning: East Money board members empty, backing off 30m")
                    break
                continue
            for empty_bk in empties:
                # A success right after one empty board means that board really is empty.
                save_board_members_snap("em", empty_bk, [], name=boards[empty_bk][0], kind=boards[empty_bk][1])
            empties = []
            save_board_members_snap("em", bk, codes, name=boards[bk][0], kind=boards[bk][1])
        return len(todo)

    def _alias_learn_now(self, now: datetime) -> None:
        """Run one overlap learning pass (at most once per calendar day)."""
        from market_desk import board_fallback as bf
        from market_desk.board_alias import learn_aliases, set_learned_aliases
        from market_desk.db import load_board_members_snap, replace_board_alias_learned

        day = now.strftime("%Y-%m-%d")
        if self._alias_learned_day == day:
            return
        self._alias_learned_day = day
        sina = load_board_members_snap("sina")
        em = load_board_members_snap("em")
        rows = learn_aliases(
            {n: {"kind": s["kind"], "codes": s["codes"]} for n, s in sina.items()},
            {bk: {"name": e["name"], "kind": e["kind"], "codes": e["codes"]} for bk, e in em.items()},
        )
        replace_board_alias_learned(rows)
        applied = set_learned_aliases(rows)
        bf._BOARDS_CACHE = None
        counts: dict[str, Any] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        self._alias_progress.update({"learned_at": now.strftime("%Y-%m-%d %H:%M"), **counts})
        log.info("alias learning: %s applied · %s", applied, counts)
