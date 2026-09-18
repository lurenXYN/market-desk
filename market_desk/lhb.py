"""Dragon-tiger list (龙虎榜) fetch for open positions."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from market_desk.config import HTTP_HEADERS
from market_desk.filters import normalize_code
from market_desk.lhb_seats import classify_seat, style_brief
from market_desk.numbers import num

log = logging.getLogger(__name__)

_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 600.0


def _yi(v: Any) -> float | None:
    """Convert yuan amount to 亿元 (2 decimals)."""
    x = num(v)
    if x is None:
        return None
    return round(float(x) / 1e8, 2)


def _wan(v: Any) -> float | None:
    """Convert yuan amount to 万元 (1 decimal)."""
    x = num(v)
    if x is None:
        return None
    return round(float(x) / 1e4, 1)


async def _dc_rows(
    client: httpx.AsyncClient,
    *,
    report: str,
    filt: str,
    sort_columns: str | None = None,
    sort_types: str | None = None,
    page_size: int = 50,
) -> list[dict[str, Any]]:
    """Fetch one East Money datacenter report page."""
    params: dict[str, str] = {
        "reportName": report,
        "columns": "ALL",
        "filter": filt,
        "pageNumber": "1",
        "pageSize": str(max(1, min(int(page_size), 200))),
        "source": "WEB",
        "client": "WEB",
    }
    if sort_columns:
        params["sortColumns"] = sort_columns
    if sort_types:
        params["sortTypes"] = sort_types
    try:
        resp = await client.get(_DC_URL, params=params, headers=HTTP_HEADERS, timeout=20.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        log.exception("lhb datacenter fetch failed report=%s", report)
        return []
    if not payload.get("success"):
        return []
    data = (payload.get("result") or {}).get("data") or []
    return [r for r in data if isinstance(r, dict)]


def _pack_seat(row: dict[str, Any], *, side: str) -> dict[str, Any]:
    """Normalize one buy/sell seat row and attach style tags."""
    name = str(row.get("OPERATEDEPT_NAME") or "").strip()
    meta = classify_seat(name)
    buy = num(row.get("BUY"))
    sell = num(row.get("SELL"))
    net = num(row.get("NET"))
    if net is None and buy is not None and sell is not None:
        net = buy - sell
    return {
        "side": side,
        "name": name,
        "buy": buy,
        "sell": sell,
        "net": net,
        "buy_yi": _yi(buy),
        "sell_yi": _yi(sell),
        "net_yi": _yi(net),
        "buy_wan": _wan(buy),
        "sell_wan": _wan(sell),
        "net_wan": _wan(net),
        "style": meta.get("style"),
        "style_label": meta.get("label"),
        "style_note": meta.get("note"),
        "alias": meta.get("alias"),
    }


def _summarize_seats(buys: list[dict[str, Any]], sells: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a short reading of seat mix for one listing day."""
    all_seats = list(buys) + list(sells)
    styles = [str(s.get("style") or "") for s in all_seats]

    def _net_for(style: str, side: str | None = None) -> float:
        total = 0.0
        src = all_seats
        if side == "buy":
            src = buys
        elif side == "sell":
            src = sells
        for s in src:
            if str(s.get("style") or "") != style:
                continue
            if side == "buy":
                total += float(s.get("buy") or 0)
            elif side == "sell":
                total += float(s.get("sell") or 0)
            else:
                total += float(s.get("net") or 0)
        return total

    inst_net = _net_for("institution")
    nb_net = _net_for("northbound")
    smash_sell = _net_for("smash", "sell")
    quant_buy = _net_for("quant", "buy")
    quant_sell = _net_for("quant", "sell")
    hints: list[str] = []
    if abs(inst_net) >= 1e6:
        hints.append(
            ("机构净买 " if inst_net > 0 else "机构净卖 ")
            + f"{_yi(inst_net)} 亿"
        )
    if abs(nb_net) >= 1e6:
        hints.append(
            ("北向净买 " if nb_net > 0 else "北向净卖 ")
            + f"{_yi(nb_net)} 亿"
        )
    if smash_sell >= 1e6:
        hints.append(f"砸盘王席卖出 {_yi(smash_sell)} 亿")
    if quant_buy + quant_sell >= 1e6:
        hints.append(
            f"量化倾向席 买{_yi(quant_buy)}/卖{_yi(quant_sell)} 亿"
        )
    risk_flags: list[str] = []
    if smash_sell >= 1e6:
        risk_flags.append("smash_sell")
    if inst_net <= -1e6:
        risk_flags.append("inst_net_sell")
    if nb_net <= -1e6:
        risk_flags.append("nb_net_sell")
    if quant_sell >= 1e6 and quant_sell > quant_buy * 1.2:
        risk_flags.append("quant_net_sell")
    seat_risk = "ok"
    if "smash_sell" in risk_flags or "inst_net_sell" in risk_flags:
        seat_risk = "bad"
    elif risk_flags:
        seat_risk = "warn"
    return {
        "style_line": style_brief(styles),
        "hints": hints[:4],
        "institution_net_yi": _yi(inst_net),
        "northbound_net_yi": _yi(nb_net),
        "smash_sell_yi": _yi(smash_sell),
        "risk_flags": risk_flags,
        "seat_risk": seat_risk,
    }


async def _latest_appearance(
    client: httpx.AsyncClient, code: str
) -> dict[str, Any] | None:
    """Return the newest billboard appearance row for one ticker."""
    c = normalize_code(code)
    rows = await _dc_rows(
        client,
        report="RPT_DAILYBILLBOARD_DETAILS",
        filt=f'(SECURITY_CODE="{c}")',
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=8,
    )
    if not rows:
        return None
    # Same day may list multiple EXPLANATION rows; keep the largest |net|.
    best = rows[0]
    day = str(best.get("TRADE_DATE") or "")[:10]
    same = [r for r in rows if str(r.get("TRADE_DATE") or "")[:10] == day]
    if len(same) > 1:
        best = max(
            same,
            key=lambda r: abs(float(num(r.get("BILLBOARD_NET_AMT")) or 0)),
        )
    reasons = []
    for r in same:
        ex = str(r.get("EXPLANATION") or r.get("EXPLAIN") or "").strip()
        if ex and ex not in reasons:
            reasons.append(ex)
    return {
        "code": c,
        "name": str(best.get("SECURITY_NAME_ABBR") or ""),
        "trade_date": day,
        "reason": "；".join(reasons) if reasons else "",
        "reasons": reasons,
        "close": num(best.get("CLOSE_PRICE")),
        "change_pct": num(best.get("CHANGE_RATE")),
        "buy_amt": num(best.get("BILLBOARD_BUY_AMT")),
        "sell_amt": num(best.get("BILLBOARD_SELL_AMT")),
        "net_amt": num(best.get("BILLBOARD_NET_AMT")),
        "buy_yi": _yi(best.get("BILLBOARD_BUY_AMT")),
        "sell_yi": _yi(best.get("BILLBOARD_SELL_AMT")),
        "net_yi": _yi(best.get("BILLBOARD_NET_AMT")),
    }


async def _seats_for_day(
    client: httpx.AsyncClient, code: str, trade_date: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch buy/sell top seats for one code on one trade date."""
    c = normalize_code(code)
    day = str(trade_date or "")[:10]
    filt = f"(TRADE_DATE='{day}')(SECURITY_CODE=\"{c}\")"
    buy_rows, sell_rows = await asyncio.gather(
        _dc_rows(
            client,
            report="RPT_BILLBOARD_DAILYDETAILSBUY",
            filt=filt,
            sort_columns="BUY",
            sort_types="-1",
            page_size=10,
        ),
        _dc_rows(
            client,
            report="RPT_BILLBOARD_DAILYDETAILSSELL",
            filt=filt,
            sort_columns="SELL",
            sort_types="-1",
            page_size=10,
        ),
    )
    buys = [_pack_seat(r, side="buy") for r in buy_rows]
    sells = [_pack_seat(r, side="sell") for r in sell_rows]
    return buys, sells


async def build_positions_lhb(
    positions: list[dict[str, Any]] | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Build dragon-tiger cards for open (qty>0) positions only.

    Each card uses the stock's latest appearance (may be older than today).
    Seat style tags are heuristic; see ``lhb_seats``.
    """
    open_rows = [
        p
        for p in (positions or [])
        if isinstance(p, dict) and int(p.get("qty") or 0) > 0
    ]
    codes: list[str] = []
    names: dict[str, str] = {}
    for p in open_rows:
        c = normalize_code(p.get("code"))
        if len(c) != 6 or not c.isdigit():
            continue
        if c not in codes:
            codes.append(c)
        names[c] = str(p.get("name") or names.get(c) or "")
    cache_key = ",".join(codes)
    now = time.monotonic()
    if not force and cache_key in _CACHE:
        ts, hit = _CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            out = dict(hit)
            out["cache_hit"] = True
            return out
    if not codes:
        empty = {
            "ok": True,
            "items": [],
            "note": "暂无持仓，龙虎榜仅对照账上股票",
            "disclaimer": "席位风格为网络常见口碑标签，非官方分类；仅供对照。",
            "cache_hit": False,
        }
        _CACHE[cache_key] = (now, empty)
        return empty

    items: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
        appearances = await asyncio.gather(
            *[_latest_appearance(client, c) for c in codes]
        )
        seat_jobs = []
        meta_by_code: dict[str, dict[str, Any] | None] = {}
        for c, app in zip(codes, appearances):
            meta_by_code[c] = app
            if app and app.get("trade_date"):
                seat_jobs.append((c, str(app["trade_date"])))
            else:
                seat_jobs.append((c, ""))

        async def _one(code: str, day: str) -> tuple[str, list, list]:
            if not day:
                return code, [], []
            buys, sells = await _seats_for_day(client, code, day)
            return code, buys, sells

        seat_packs = await asyncio.gather(
            *[_one(c, d) for c, d in seat_jobs]
        )

    for code, buys, sells in seat_packs:
        app = meta_by_code.get(code)
        name = names.get(code) or (app or {}).get("name") or code
        if not app:
            items.append(
                {
                    "code": code,
                    "name": name,
                    "on_list": False,
                    "note": "近端无龙虎榜记录（或尚未披露）",
                    "buys": [],
                    "sells": [],
                    "summary": {"style_line": "—", "hints": []},
                }
            )
            continue
        summary = _summarize_seats(buys, sells)
        items.append(
            {
                "code": code,
                "name": name,
                "on_list": True,
                "trade_date": app.get("trade_date"),
                "reason": app.get("reason") or "",
                "change_pct": app.get("change_pct"),
                "close": app.get("close"),
                "buy_yi": app.get("buy_yi"),
                "sell_yi": app.get("sell_yi"),
                "net_yi": app.get("net_yi"),
                "buys": buys,
                "sells": sells,
                "summary": summary,
                "note": "",
            }
        )

    listed_n = sum(1 for it in items if it.get("on_list"))
    payload = {
        "ok": True,
        "items": items,
        "listed_n": listed_n,
        "total_n": len(items),
        "note": (
            f"持仓 {len(items)} 只 · 近端上榜 {listed_n} 只"
            "（取该票最近一次披露日）"
        ),
        "disclaimer": (
            "席位风格（砸盘王 / 量化倾向 / 游资绰号等）来自网络常见归类，"
            "仅供对照，不构成投资建议；以交易所披露的买卖金额为准。"
        ),
        "cache_hit": False,
    }
    _CACHE[cache_key] = (now, payload)
    return payload
