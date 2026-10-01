"""Order-flow microstructure glue: auction tape, absorption and slippage gates."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from market_desk.db import save_auction
from market_desk.filters import normalize_code
from market_desk.microstructure import (
    apply_absorption_gates,
    apply_auction_alpha,
    apply_slippage_filter,
    compute_alpha,
    in_tape_window,
    record_tape,
    tape_snapshot,
)
from market_desk.tencent import fetch_quotes

log = logging.getLogger("market_desk")

_RECOMMEND_KEYS = (
    "recommend",
    "dragon_recommend",
    "side_recommend",
    "link_recommend",
    "independent_recommend",
)


class MicroMixin:
    """Order-flow microstructure glue: auction tape, absorption and slippage gates."""

    _alpha_live: tuple[str, dict[str, Any]] | None = None

    def _auction_candidates(
        self, yesterday_zt: list[dict[str, Any]] | None
    ) -> dict[str, dict[str, Any]]:
        """Collect auction-tape candidates with peer group and yesterday-ZT flags.

        Priority: live recommend cards (grouped by their box mainline / source
        board), then auction-strategy rows and yesterday's limit-ups (grouped by
        industry). Capped by ``AUCTION_ALPHA_CANDIDATE_CAP``.
        """
        from market_desk.config import AUCTION_ALPHA_CANDIDATE_CAP

        cap = int(AUCTION_ALPHA_CANDIDATE_CAP)
        yzt_codes = {
            normalize_code(r.get("code")): str(r.get("industry") or "")
            for r in (yesterday_zt or [])
            if normalize_code(r.get("code"))
        }
        out: dict[str, dict[str, Any]] = {}

        def _add(code: Any, group: str) -> None:
            c = normalize_code(code)
            if not c or c in out or len(out) >= cap:
                return
            out[c] = {"group": group or yzt_codes.get(c) or "", "yzt": c in yzt_codes}

        snap = self.snapshot or {}
        verdict = snap.get("verdict") or {}
        ml_name = str(((verdict.get("mainline") or {}).get("name")) or "")
        for key in _RECOMMEND_KEYS:
            box = verdict.get(key) or {}
            for item in box.get("items") or []:
                if (item.get("kind") or "stock") != "stock":
                    continue
                group = str(item.get("source_board") or item.get("board") or ml_name)
                _add(item.get("code"), group)
        strat = snap.get("auction_strategy") or {}
        for tier in strat.get("tiers") or []:
            for row in tier.get("items") or []:
                _add(row.get("code"), str(row.get("industry") or ""))
        for code, industry in yzt_codes.items():
            _add(code, industry)
        return out

    async def _sample_auction_tape(
        self,
        client: httpx.AsyncClient,
        now: datetime,
        trade_date: str,
        yesterday_zt: list[dict[str, Any]] | None,
    ) -> int:
        """Sample Tencent auction quotes for candidates during 09:20–09:25."""
        if not in_tape_window(now):
            return 0
        meta = self._auction_candidates(yesterday_zt)
        if not meta:
            return 0
        try:
            quotes = await fetch_quotes(client, list(meta))
        except Exception:
            log.exception("auction tape quotes failed")
            return 0
        return record_tape(quotes, now, trade_date=trade_date, meta=meta)

    def _auction_alpha_map(
        self,
        trade_date: str,
        now: datetime,
        auction: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        """Return per-code auction alpha; lock it into the auction payload at 09:26.

        Before the lock a live preview is computed from the in-memory tape. Once
        locked the result is persisted inside the existing ``auction_lock``
        payload (``alpha`` key), so a restart keeps the day's verdicts.
        """
        auc = auction if isinstance(auction, dict) else {}
        stored = auc.get("alpha")
        if isinstance(stored, dict) and stored.get("locked"):
            self._alpha_live = (trade_date, stored)
            return dict(stored.get("codes") or {})
        cached = self._alpha_live
        if cached and cached[0] == trade_date and cached[1].get("locked"):
            auc["alpha"] = cached[1]
            return dict(cached[1].get("codes") or {})
        tape = tape_snapshot()
        if not tape:
            return {}
        hhmm = now.hour * 100 + now.minute
        locked = hhmm >= 926
        codes = compute_alpha(market_median=auc.get("median_open"))
        payload = {
            "locked": locked,
            "at": now.strftime("%H:%M:%S"),
            "codes": codes,
            "trap_n": sum(1 for r in codes.values() if r.get("tag") == "trap"),
            "strong_n": sum(1 for r in codes.values() if r.get("tag") == "strong"),
        }
        auc["alpha"] = payload
        self._alpha_live = (trade_date, payload)
        if locked and int(auc.get("sample") or 0) > 0:
            try:
                save_auction(trade_date, auc)
            except Exception:
                log.exception("auction alpha lock save failed")
        return dict(codes)

    def _alpha_codes_for(self, trade_date: str) -> dict[str, dict[str, Any]]:
        """Return the cached alpha map for ``trade_date`` (empty when absent)."""
        cached = self._alpha_live
        if not cached or cached[0] != trade_date:
            return {}
        return dict(cached[1].get("codes") or {})

    async def _micro_quotes(
        self, client: httpx.AsyncClient, codes: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch Tencent quotes with book levels (``{}`` on failure)."""
        uniq = [c for c in dict.fromkeys(normalize_code(x) for x in codes) if c]
        if not uniq:
            return {}
        try:
            return await fetch_quotes(client, uniq)
        except Exception:
            log.exception("micro quotes failed")
            return {}

    def _apply_micro_gates(
        self,
        rec: dict[str, Any] | None,
        *,
        minutes_by_code: dict[str, list[dict[str, Any]]] | None,
        quotes_by_code: dict[str, dict[str, Any]] | None,
        now: datetime,
        trade_date: str,
    ) -> dict[str, Any]:
        """Run auction alpha, absorption and slippage on one recommend box."""
        out = dict(rec or {})
        if not out.get("items"):
            return out
        out = apply_auction_alpha(out, self._alpha_codes_for(trade_date), now=now)
        out = apply_absorption_gates(out, minutes_by_code, quotes_by_code)
        out = apply_slippage_filter(out, quotes_by_code, now=now)
        primary = out.get("primary")
        if isinstance(primary, dict) and primary.get("code"):
            pcode = normalize_code(primary.get("code"))
            for item in out.get("items") or []:
                if normalize_code(item.get("code")) == pcode:
                    out["primary"] = item
                    break
        return out
