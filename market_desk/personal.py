"""Per-user personal layer over the shared market snapshot."""

from __future__ import annotations

import copy
from typing import Any

from market_desk.db import load_favorite_boards, load_positions, load_watchlist, touch_position_peaks
from market_desk.filters import normalize_code
from market_desk.settings import use_user_settings
from market_desk.verdict import (
    apply_size_cap_gate,
    attach_position_daily_trends,
    attach_position_sell_hints,
    build_risk_overview,
    build_sell_advice,
    build_watch_trial_recommend,
    decorate_positions,
    finalize_recommend_buy_ux,
    position_summary,
    _attach_risk_sizing,
)


def _quote_map_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a code→quote map from previously decorated personal rows."""
    quotes: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        code = str(row.get("code") or "").zfill(6)
        if not code:
            continue
        quotes[code] = {
            "code": code,
            "name": row.get("name") or "",
            "price": row.get("last"),
            "pct": row.get("last_pct"),
            "high": row.get("high"),
            "low": row.get("low"),
        }
    return quotes


def attach_personal_layer(
    snapshot: dict[str, Any],
    user_id: int,
    *,
    book_quotes: dict[str, dict[str, Any]] | None = None,
    trends_for=None,
    decorate_watchlist=None,
    decorate_favorites=None,
    minutes_for=None,
) -> dict[str, Any]:
    """Return a deep-copied snapshot with one user's positions / watch / risk.

    ``book_quotes`` is the shared engine mark map (all users' book codes).
    ``trends_for``, ``decorate_watchlist``, ``decorate_favorites`` are callables
    provided by DeskEngine to reuse its caches / helpers.
    ``minutes_for(codes)`` optionally returns minute series for sell soft-take gates.
    """
    out = copy.deepcopy(snapshot or {})
    uid = int(user_id)
    trade_date = str(out.get("trade_date") or "")
    trade_dash = (
        f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        if len(trade_date) == 8
        else trade_date[:10]
    )
    quotes: dict[str, dict[str, Any]] = {}
    # Shared refresh marks first (authoritative live last/pct/prev).
    for code, q in (book_quotes or {}).items():
        c = normalize_code(code) or str(code or "").zfill(6)
        if not c or not isinstance(q, dict):
            continue
        quotes[c] = dict(q)
        quotes[c]["code"] = c
    # Fallback: any leftover decorated rows on the snapshot (usually empty).
    for code, q in _quote_map_from_rows(list(out.get("positions") or [])).items():
        if code not in quotes or quotes[code].get("price") is None:
            quotes[code] = q
    # Prefer live quotes already on market cards when available.
    for pool in ("etfs", "hot_boards", "pin_boards"):
        for row in out.get(pool) or []:
            code = normalize_code(row.get("code") or row.get("leader_code"))
            if not code:
                continue
            price = row.get("price") if row.get("price") is not None else row.get("last")
            if price is None:
                continue
            if code not in quotes or quotes[code].get("price") is None:
                quotes[code] = {
                    "code": code,
                    "name": row.get("name") or row.get("leader_name") or "",
                    "price": price,
                    "pct": row.get("pct"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                }

    with use_user_settings(uid):
        positions = decorate_positions(
            load_positions(user_id=uid),
            quotes,
            trade_date=trade_dash,
            boards=list(out.get("hot_boards") or [])
            + list(out.get("pin_boards") or [])
            + list(load_favorite_boards(user_id=uid) or []),
        )
        peaks = {
            int(r["id"]): float(r["peak_price"])
            for r in positions
            if r.get("peak_dirty") and r.get("id") and r.get("peak_price")
        }
        if peaks:
            try:
                touch_position_peaks(peaks)
            except Exception:
                pass
        if trends_for:
            trends = trends_for(
                [normalize_code(r.get("code")) for r in positions if r.get("code")]
            )
            positions = attach_position_daily_trends(positions, trends)
        else:
            trends = {}

        out["positions"] = positions
        out["position_summary"] = position_summary(positions)
        verdict = dict(out.get("verdict") or {})
        out["sell_advice"] = build_sell_advice(
            positions,
            verdict,
            out.get("phase") or "",
            trade_date=trade_dash,
            trends_by_code=trends,
            similar=out.get("similar_days"),
            metrics=out.get("metrics"),
        )
        # Soft take / half: minute fade gate when series available (cache miss = soft pass).
        try:
            from market_desk.minute_confirm import apply_sell_minute_gates

            advice = out["sell_advice"] or {}
            need_codes = [
                str(it.get("code") or "").zfill(6)
                for it in (advice.get("all_items") or advice.get("items") or [])
                if it.get("minute_gate") and it.get("code")
            ]
            minutes: dict[str, list] = {}
            if need_codes and callable(minutes_for):
                try:
                    minutes = dict(minutes_for(need_codes) or {})
                except Exception:
                    minutes = {}
            out["sell_advice"] = apply_sell_minute_gates(advice, minutes)
        except Exception:
            pass

        advice = out.get("sell_advice") or {}
        all_items = list(advice.get("all_items") or advice.get("items") or [])
        advice["items"] = all_items[:4]
        # Keep all_items for sell-signal logging, then strip from API payload.
        if all_items:
            advice["all_items"] = all_items
        out["sell_advice"] = advice
        try:
            from market_desk.review import record_sell_advice_signals

            record_sell_advice_signals(out)
        except Exception:
            pass
        advice.pop("all_items", None)
        out["sell_advice"] = advice
        positions = attach_position_sell_hints(positions, all_items)
        out["positions"] = positions
        out["position_summary"] = position_summary(positions)
        cap = float((verdict.get("playbook") or {}).get("size_cap_pct") or 100)
        out["risk_overview"] = build_risk_overview(positions, size_cap_pct=cap)

        # Re-size recommend cards with this user's equity / open book.
        for key in ("recommend", "side_recommend", "link_recommend"):
            rec = verdict.get(key)
            if not rec:
                continue
            sized = _attach_risk_sizing(
                rec,
                playbook=verdict.get("playbook"),
                adapt=verdict.get("adapt"),
            )
            verdict[key] = finalize_recommend_buy_ux(
                sized,
                allow_probe=(key == "recommend"),
            )
        verdict = apply_size_cap_gate(verdict, positions)
        out["verdict"] = verdict

        wl_raw = load_watchlist(user_id=uid)
        if decorate_watchlist:
            out["watchlist"] = decorate_watchlist(wl_raw, quotes, verdict)
        else:
            out["watchlist"] = wl_raw

        watch_trial = build_watch_trial_recommend(
            out["watchlist"],
            playbook=verdict.get("playbook"),
            adapt=verdict.get("adapt"),
        )
        if watch_trial:
            for item in watch_trial.get("items") or []:
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
            watch_trial["buy"] = False
            watch_trial = finalize_recommend_buy_ux(watch_trial, allow_probe=False)
        out["verdict"]["watch_trial_recommend"] = watch_trial

        fav_rows = load_favorite_boards(user_id=uid)
        if decorate_favorites:
            out["favorite_boards"] = decorate_favorites(fav_rows)
        else:
            out["favorite_boards"] = fav_rows

        out["auth_user_id"] = uid
    return out
