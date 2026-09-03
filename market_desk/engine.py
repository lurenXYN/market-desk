"""Refresh loop that assembles the dashboard snapshot."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from datetime import timedelta as _timedelta
from typing import Any
try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))

import httpx

from market_desk.config import (
    HOT_BOARD_COUNT,
    ICE_BOARD_COUNT,
    IDLE_CHECK_SECONDS,
    PIN_INDUSTRY_ALIASES,
    SESSION_REFRESH_SECONDS,
)
from market_desk.db import (
    init_db,
    load_auction,
    load_board_hist_map,
    load_daily,
    load_positions,
    save_auction,
    save_board_daily,
    save_daily,
)
from market_desk.eastmoney import (
    fetch_board_members,
    fetch_hot_boards,
    fetch_main_quotes,
    fetch_yesterday_zt,
    fetch_zb_pool,
    fetch_zt_pool,
)
from market_desk.filters import is_limit_down, is_main_board
from market_desk.glossary import GLOSSARY
from market_desk.sentiment import (
    auction_from_quotes,
    board_cycle_tags,
    board_headline,
    board_note,
    board_status,
    build_market_metrics,
    classify_phase,
    cluster_path,
    cycle_flags,
    ice_status,
    kpi_bars,
    score_temperature,
    spark_values,
)
from market_desk.tencent import fetch_etfs, fetch_quotes
from market_desk.verdict import build_deltas, build_verdict, decorate_positions, position_summary

log = logging.getLogger("market_desk")


class DeskEngine:
    """Hold the latest snapshot and refresh it in the background."""

    def __init__(self) -> None:
        self.snapshot: dict[str, Any] = {
            "ok": False,
            "error": "starting",
            "updated_at": None,
        }
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._eod_date: str | None = None

    def start(self) -> None:
        """Create tables and start the polling task."""
        init_db()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the polling task."""
        if self._task:
            self._task.cancel()

    def sync_positions(self) -> list[dict[str, Any]]:
        """Reload positions from SQLite and reuse last quotes in the snapshot."""
        quotes: dict[str, dict[str, Any]] = {}
        for row in self.snapshot.get("positions") or []:
            code = str(row.get("code") or "").zfill(6)
            quotes[code] = {
                "code": code,
                "name": row.get("name") or "",
                "price": row.get("last"),
                "pct": row.get("last_pct"),
                "high": row.get("high"),
                "low": row.get("low"),
            }
        positions = decorate_positions(load_positions(), quotes)
        self.snapshot["positions"] = positions
        self.snapshot["position_summary"] = position_summary(positions)
        return positions

    async def _loop(self) -> None:
        first = True
        while True:
            now = datetime.now(CN_TZ)
            live = _is_session(now)
            today = now.strftime("%Y-%m-%d")
            after_close = (
                _is_weekday(now) and _minutes(now) >= 15 * 60 + 5 and self._eod_date != today
            )
            if first or live or after_close:
                try:
                    await self.refresh()
                    if after_close:
                        self._eod_date = today
                except Exception:
                    log.exception("refresh failed")
                    self.snapshot["ok"] = False
                    self.snapshot["error"] = "refresh failed"
                first = False
            elif self.snapshot.get("ok"):
                self.snapshot["live"] = False
                self.snapshot["polling"] = False
            await asyncio.sleep(SESSION_REFRESH_SECONDS if live else IDLE_CHECK_SECONDS)

    async def refresh(self) -> None:
        """Pull public snapshots and rebuild the dashboard payload."""
        async with self._lock:
            now = datetime.now(CN_TZ)
            trade_date = now.strftime("%Y%m%d")
            trade_date_dash = now.strftime("%Y-%m-%d")
            errors: list[str] = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                zt, zb, quotes, boards, etfs = await asyncio.gather(
                    _safe(fetch_zt_pool, client, trade_date, errors=errors, label="zt"),
                    _safe(fetch_zb_pool, client, trade_date, errors=errors, label="zb"),
                    _safe(fetch_main_quotes, client, errors=errors, label="quotes"),
                    _safe(fetch_hot_boards, client, errors=errors, label="boards"),
                    _safe(fetch_etfs, client, errors=errors, label="etf"),
                )
                yesterday_zt = await self._yesterday(client, now, errors)
                zt = zt or []
                zb = zb or []
                quotes = quotes or []
                boards = boards or []
                etfs = etfs or []
                yesterday_zt = yesterday_zt or []
                ctx = {
                    "zt": zt,
                    "zb": zb,
                    "yzt": yesterday_zt,
                    "hist": load_board_hist_map(trade_date_dash),
                }
                hot_cards = await self._hot_cards(client, boards, ctx)
                pin_cards = await self._pin_cards(client, boards, hot_cards, ctx)
                ice_cards = await self._ice_cards(client, boards, hot_cards, ctx)
                pos_rows = load_positions()
                pos_quote_map = await _safe(
                    fetch_quotes,
                    client,
                    [str(r.get("code") or "") for r in pos_rows],
                    errors=errors,
                    label="pos",
                )
            contagion = _contagion(ice_cards)
            save_board_daily(trade_date_dash, hot_cards + pin_cards + ice_cards)
            if not isinstance(pos_quote_map, dict):
                pos_quote_map = {}
            positions = decorate_positions(pos_rows, pos_quote_map)

            metrics = build_market_metrics(quotes, zt, zb, yesterday_zt)
            temperature = score_temperature(metrics)
            phase = classify_phase(metrics, temperature)
            auction = self._auction(trade_date, now, quotes)
            save_daily(
                trade_date_dash,
                {
                    "phase": phase,
                    "temperature": temperature,
                    "ups": metrics["ups"],
                    "downs": metrics["downs"],
                    "zt": metrics["zt"],
                    "dt": metrics["dt"],
                    "zb_rate": metrics["zb_rate"],
                    "height": metrics["height"],
                    "promotion": metrics["promotion"],
                    "premium": metrics["premium"],
                    "amount_yi": metrics["amount_yi"],
                    "event": _event_line(phase, metrics, hot_cards),
                },
            )
            history = load_daily(14)
            cycle = _cycle_view(history, trade_date_dash)
            prev = self.snapshot if self.snapshot.get("ok") else None
            verdict = build_verdict(
                now, phase, metrics, etfs, hot_cards, prev, zt
            )
            payload = {
                "ok": True,
                "error": None,
                "warnings": errors,
                "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "prev_updated_at": (prev or {}).get("updated_at"),
                "trade_date": trade_date_dash,
                "live": _is_session(now),
                "polling": _is_session(now),
                "refresh_seconds": SESSION_REFRESH_SECONDS,
                "phase": phase,
                "temperature": temperature,
                "metrics": metrics,
                "kpis": kpi_bars(metrics),
                "auction": auction,
                "etfs": etfs,
                "hot_boards": hot_cards,
                "pin_boards": pin_cards,
                "ice_boards": ice_cards,
                "contagion": contagion,
                "history": history,
                "cycle": cycle,
                "events": _today_events(phase, metrics, hot_cards, zt, ice_cards, contagion),
                "watch": _watch_pool(zt, zb, quotes),
                "filter": "个股只做主板 · 创业板/科创走 ETF",
                "verdict": verdict,
                "positions": positions,
                "position_summary": position_summary(positions),
                "glossary": GLOSSARY,
            }
            payload["deltas"] = build_deltas(payload, prev)
            self.snapshot = payload

    async def _yesterday(
        self,
        client: httpx.AsyncClient,
        now: datetime,
        errors: list[str],
    ) -> list[dict[str, Any]]:
        cursor = now.date() - timedelta(days=1)
        for _ in range(10):
            if cursor.weekday() >= 5:
                cursor -= timedelta(days=1)
                continue
            key = cursor.strftime("%Y%m%d")
            rows = await _safe(
                fetch_yesterday_zt, client, key, errors=errors, label=f"yzt-{key}"
            )
            if rows:
                return rows
            cursor -= timedelta(days=1)
        return []

    def _auction(
        self, trade_date: str, now: datetime, quotes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        locked = load_auction(trade_date)
        hhmm = now.hour * 100 + now.minute
        if locked and hhmm >= 925:
            locked["locked"] = True
            return locked
        summary = auction_from_quotes(quotes)
        summary["locked"] = hhmm >= 925
        if hhmm >= 925 and quotes:
            save_auction(trade_date, summary)
        return summary

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
        enriched = await asyncio.gather(
            *[_enrich_board(client, board, ctx) for board in picked]
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
        out: list[dict[str, Any]] = []
        for label, aliases in PIN_INDUSTRY_ALIASES.items():
            match = _pick_pin_board(industry, aliases)
            if not match:
                continue
            match = dict(match)
            match["already_hot"] = match["name"] in hot_names
            card = await _enrich_board(client, match, ctx)
            card["pin_label"] = label
            out.append(card)
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
        enriched = await asyncio.gather(
            *[_enrich_board(client, board, ctx, weakest=True) for board in picked]
        )
        cards = [x for x in enriched if x]
        cards.sort(
            key=lambda b: (
                0 if b.get("status") == "传染预警" else 1,
                b.get("pct") if b.get("pct") is not None else 0,
            )
        )
        return cards[:ICE_BOARD_COUNT]


def _pick_pin_board(
    industry: list[dict[str, Any]], aliases: tuple[str, ...]
) -> dict[str, Any] | None:
    """Prefer an exact industry name, then a clean prefix match."""
    exact = [b for b in industry if b["name"] in aliases]
    if exact:
        return max(exact, key=lambda b: b.get("pct") or 0)
    prefixed = [
        b
        for b in industry
        if any(b["name"].startswith(alias) for alias in aliases) and not b["name"].startswith("其他")
    ]
    if prefixed:
        return max(prefixed, key=lambda b: b.get("pct") or 0)
    contains = [
        b
        for b in industry
        if any(alias in b["name"] for alias in aliases) and not b["name"].startswith("其他")
    ]
    if contains:
        return max(contains, key=lambda b: b.get("pct") or 0)
    return None


def _rank_ice_boards(
    boards: list[dict[str, Any]], hot_names: set[str]
) -> list[dict[str, Any]]:
    """Pick the coldest industries; hot names are deprioritized but still allowed."""
    industry = [b for b in boards if b.get("kind") == "industry"]
    ranked = sorted(
        industry,
        key=lambda b: (
            1 if b.get("name") in hot_names else 0,
            b.get("pct") if b.get("pct") is not None else 0.0,
            -(b.get("down_count") or 0),
            b.get("up_count") or 0,
        ),
    )
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for board in ranked:
        name = board.get("name") or ""
        if not name or name in seen or name.startswith("其他"):
            continue
        seen.add(name)
        picked.append(board)
        if len(picked) >= ICE_BOARD_COUNT:
            break
    return picked


async def _enrich_board(
    client: httpx.AsyncClient,
    board: dict[str, Any],
    ctx: dict[str, Any],
    weakest: bool = False,
) -> dict[str, Any]:
    zt = ctx.get("zt") or []
    zb = ctx.get("zb") or []
    yzt = ctx.get("yzt") or []
    hist = (ctx.get("hist") or {}).get(board.get("bk") or "", [])
    members = await fetch_board_members(client, board["bk"], weakest=weakest)
    zt_by_code = {x["code"]: x for x in zt}
    zb_codes = {x["code"] for x in zb}
    yzt_codes = {x["code"] for x in yzt}
    zt_n = sum(1 for m in members if m["code"] in zt_by_code)
    dt_n = sum(1 for m in members if is_limit_down(m.get("name"), m.get("pct")))
    ranked_leaders = sorted(
        [zt_by_code[m["code"]] for m in members if m["code"] in zt_by_code],
        key=lambda x: int(x.get("boards") or 0),
        reverse=True,
    )
    leader = ranked_leaders[0] if ranked_leaders else None
    slot = ranked_leaders[1] if len(ranked_leaders) > 1 else None
    main_leader_ok = is_main_board(board.get("leader_code"))
    leader_name = (leader or {}).get("name") or (
        board.get("leader_name") if main_leader_ok else (members[0]["name"] if members else "")
    )
    leader_code = (leader or {}).get("code") or (
        board.get("leader_code") if main_leader_ok else (members[0]["code"] if members else "")
    )
    leader_boards = int((leader or {}).get("boards") or (1 if leader_code in zt_by_code else 0))
    tiandi = any(
        m["code"] in yzt_codes and (is_limit_down(m.get("name"), m.get("pct")) or m["code"] in zb_codes)
        for m in members
    )
    giveback = 0
    for m in members:
        high = m.get("high")
        price = m.get("price")
        if high and price and high > 0 and (high - price) / high >= 0.05 and (m.get("pct") or 0) < 1:
            giveback += 1
    pct = board.get("pct") or 0
    card = dict(board)
    card["members"] = members[:5]
    card["pool"] = members
    card["zt_n"] = zt_n
    card["dt_n"] = dt_n
    card["leader_name"] = leader_name
    card["leader_code"] = leader_code
    card["leader_boards"] = leader_boards
    card["slot_name"] = (slot or {}).get("name")
    card["slot_code"] = (slot or {}).get("code")
    card["slot_boards"] = int((slot or {}).get("boards") or 0)
    card["ice"] = weakest
    if weakest:
        card["status"] = ice_status(
            pct, dt_n, int(board.get("up_count") or 0), int(board.get("down_count") or 0)
        )
    else:
        card["status"] = board_status(pct, zt_n, leader_boards)
    ice = weakest or card["status"] in ("冰点", "冷冻", "相对最冷", "传染预警")
    contagion = card["status"] == "传染预警"
    flags = cycle_flags(
        pct,
        zt_n,
        leader_boards,
        dt_n=dt_n,
        ice=ice,
        contagion=contagion,
        tiandi=tiandi,
        hist=hist,
        giveback=giveback,
    )
    headline, tone = board_headline(card["status"], flags, ice)
    card["flags"] = flags
    card["tags"] = board_cycle_tags(flags, ice=ice)
    card["headline"] = headline
    card["tone"] = tone
    card["cluster"] = cluster_path(hist, zt_n)
    card["spark"] = spark_values(hist, zt_n)
    card["note"] = board_note(flags, leader_name, leader_boards, card.get("slot_name"), ice)
    card["focus"] = round(zt_n / max(len(zt), 1) * 100.0, 1) if zt else 0.0
    return card


async def _safe(fn, *args, errors: list[str], label: str):
    try:
        return await fn(*args)
    except Exception as exc:
        log.warning("%s: %s", label, exc)
        errors.append(f"{label}: {exc}")
        return []


def _minutes(now: datetime) -> int:
    """Return minutes since midnight for session-window checks."""
    return now.hour * 60 + now.minute


def _is_weekday(now: datetime) -> bool:
    """Return True for Monday–Friday in the given local time."""
    return now.weekday() < 5


def _is_session(now: datetime) -> bool:
    minutes = _minutes(now)
    morning = 9 * 60 + 15 <= minutes <= 11 * 60 + 30
    afternoon = 13 * 60 <= minutes <= 15 * 60 + 5
    return _is_weekday(now) and (morning or afternoon)


def _event_line(phase: str, metrics: dict[str, Any], hot: list[dict[str, Any]]) -> str:
    top = hot[0]["name"] if hot else "—"
    leader = metrics.get("leader") or {}
    return f"{phase} · 最高{metrics['height']}板 · 热点{top} · {leader.get('name') or '—'}"


def _contagion(ice: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag clustered limit-downs inside the coldest industries."""
    hits = [b for b in ice if (b.get("dt_n") or 0) >= 2 or b.get("status") == "传染预警"]
    if not hits:
        cold = ice[0] if ice else None
        return {
            "on": False,
            "text": (
                f"冰点观察 {cold.get('name')} {(cold.get('pct') or 0):+.2f}% · 暂无板块级传染"
                if cold
                else "暂无板块级跌停传染"
            ),
        }
    board = hits[0]
    return {
        "on": True,
        "name": board.get("name"),
        "dt_n": board.get("dt_n") or 0,
        "text": f"{board.get('name')} {board.get('dt_n') or 0} 家靠近跌停 · 传染预警 · 非买点",
    }


def _today_events(
    phase: str,
    metrics: dict[str, Any],
    hot: list[dict[str, Any]],
    zt: list[dict[str, Any]],
    ice: list[dict[str, Any]] | None = None,
    contagion: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    leader = metrics.get("leader") or {}
    top = hot[0] if hot else {}
    cold = (ice or [None])[0] if ice else None
    items = [
        {"tone": "phase", "text": f"主板相位 {phase}，温度 {metrics.get('zt', 0)}涨停 / {metrics.get('dt', 0)}跌停"},
        {
            "tone": "lead",
            "text": f"高度 {metrics['height']} 板 · {leader.get('name') or '—'} {leader.get('code') or ''}",
        },
        {
            "tone": "hot",
            "text": f"实时热点 {top.get('name') or '—'} {top.get('pct', 0):+.2f}%"
            if top
            else "热点待刷新",
        },
        {
            "tone": "ice",
            "text": f"实时冰点 {cold.get('name')} {(cold.get('pct') or 0):+.2f}% · {cold.get('status')}"
            if cold
            else "冰点板块待刷新",
        },
        {
            "tone": "promo",
            "text": f"昨停溢价 {metrics['premium']}% · 晋级率 {metrics['promotion']}% · 炸板 {metrics['zb_rate']}%",
        },
    ]
    if contagion and contagion.get("on"):
        items.insert(0, {"tone": "warn", "text": contagion["text"]})
    if zt:
        banned = [x for x in zt if int(x.get("boards") or 0) >= 3]
        if banned:
            items.append(
                {
                    "tone": "warn",
                    "text": f"高位连板 {banned[0]['name']} {banned[0]['boards']}板 · 纪律禁追",
                }
            )
    return items


def _cycle_view(history: list[dict[str, Any]], today: str) -> dict[str, Any]:
    ordered = list(reversed(history))
    last_climax = None
    for row in ordered:
        if row.get("phase") == "高潮":
            last_climax = row.get("trade_date")
    offset = 0
    if last_climax:
        dates = [r.get("trade_date") for r in ordered]
        try:
            offset = dates.index(today) - dates.index(last_climax)
        except ValueError:
            offset = 0
    nodes = []
    for i in range(-1, 10):
        label = "今" if i == offset else f"D{i:+d}".replace("+", "+")
        nodes.append({"i": i, "current": i == offset, "label": label})
    return {
        "offset": offset,
        "last_climax": last_climax,
        "nodes": nodes,
        "note": f"距上次高潮 {offset} 日" if last_climax else "本地尚无高潮样本，先攒日级数据",
    }


def _watch_pool(
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(zt, key=lambda x: int(x.get("boards") or 0), reverse=True)[:12]:
        rows.append(
            {
                **item,
                "tag": "禁追" if int(item.get("boards") or 0) >= 2 else "观察",
                "reason": f"{item.get('boards')}板涨停",
            }
        )
    for item in zb[:8]:
        rows.append({**item, "tag": "观察", "reason": "炸板"})
    dt = [q for q in quotes if is_limit_down(q.get("name"), q.get("pct"))]
    for item in dt[:6]:
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "pct": item["pct"],
                "tag": "观察",
                "reason": "跌停",
                "boards": 0,
            }
        )
    seen = {r["code"] for r in rows}
    hot_turn = sorted(
        [q for q in quotes if (q.get("turnover") or 0) >= 15 and q["code"] not in seen],
        key=lambda q: q.get("turnover") or 0,
        reverse=True,
    )
    for item in hot_turn[:6]:
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "pct": item["pct"],
                "tag": "禁追" if (item.get("pct") or 0) >= 7 else "观察",
                "reason": f"换手 {item.get('turnover'):.0f}%",
                "boards": 0,
            }
        )
    return rows[:24]


engine = DeskEngine()
