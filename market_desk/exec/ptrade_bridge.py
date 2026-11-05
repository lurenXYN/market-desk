"""Guotai Haitong PTrade bridge helpers (stage-1 read + intent pull).

PTrade runs on the broker side and cannot host the desk. The strategy polls
desk HTTP APIs with a shared bridge token: push positions for reconcile, pull
buy/sell intents for display (Send stays off until stage 2).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from market_desk.db import load_setting, save_setting
from market_desk.exec.readonly import positions_from_payload, trades_from_payload
from market_desk.filters import normalize_code
from market_desk.sim_desk import portfolio_stats

_TOKEN_KEY = "ptrade_bridge_token"


def get_bridge_token() -> str:
    """Return the configured bridge token (empty when unset)."""
    raw = load_setting(_TOKEN_KEY)
    if isinstance(raw, dict):
        return str(raw.get("token") or "")
    return str(raw or "")


def set_bridge_token(token: str | None = None) -> dict[str, Any]:
    """Create or replace the bridge token; returns a one-time plaintext copy."""
    value = (token or "").strip() or secrets.token_urlsafe(24)
    save_setting(_TOKEN_KEY, {"token": value})
    return {"ok": True, "token": value, "hint": "PTrade 策略里配置 DESK_BRIDGE_TOKEN；只显示一次请立刻抄走"}


def token_configured() -> bool:
    """Return True when a non-empty bridge token is stored."""
    return bool(get_bridge_token())


def verify_bridge_token(provided: str | None) -> bool:
    """Constant-time compare of the request token to the stored secret."""
    expect = get_bridge_token()
    got = str(provided or "").strip()
    if not expect or not got:
        return False
    return hmac.compare_digest(expect, got)


def normalize_ptrade_positions(rows: list[Any] | None) -> list[dict[str, Any]]:
    """Map common PTrade ``get_positions`` fields into desk ingest rows."""
    out: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = (
            raw.get("code")
            or raw.get("stock_code")
            or raw.get("security_code")
            or raw.get("symbol")
            or ""
        )
        # PTrade often returns 600000.XSHG / 000001.XSHE
        code_s = str(code).split(".")[0]
        code_s = normalize_code(code_s) or code_s.zfill(6)
        try:
            volume = int(
                raw.get("volume")
                if raw.get("volume") is not None
                else (
                    raw.get("current_amount")
                    if raw.get("current_amount") is not None
                    else (
                        raw.get("amount")
                        if raw.get("amount") is not None
                        else raw.get("enable_amount") or raw.get("qty") or 0
                    )
                )
            )
        except (TypeError, ValueError):
            volume = 0
        if not code_s or volume <= 0:
            continue
        try:
            price = float(
                raw.get("price")
                if raw.get("price") is not None
                else (
                    raw.get("cost_basis")
                    if raw.get("cost_basis") is not None
                    else raw.get("cost_price") or raw.get("avg_cost") or 0
                )
            )
        except (TypeError, ValueError):
            price = 0.0
        try:
            last = raw.get("last_sale_price")
            if last is None:
                last = raw.get("last_price") or raw.get("price_now")
            last_f = None if last is None else float(last)
        except (TypeError, ValueError):
            last_f = None
        try:
            yd = int(
                raw.get("yd_volume")
                if raw.get("yd_volume") is not None
                else raw.get("enable_amount") or 0
            )
        except (TypeError, ValueError):
            yd = 0
        out.append(
            {
                "code": code_s,
                "name": str(raw.get("name") or raw.get("stock_name") or ""),
                "volume": volume,
                "price": price,
                "last": last_f,
                "yd_volume": max(0, yd),
                "gateway": "ptrade",
            }
        )
    return out


def normalize_ptrade_trades(rows: list[Any] | None) -> list[dict[str, Any]]:
    """Map common PTrade trade/fill rows into desk ingest trades."""
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(rows or []):
        if not isinstance(raw, dict):
            continue
        code = raw.get("code") or raw.get("stock_code") or raw.get("security_code") or ""
        code_s = normalize_code(str(code).split(".")[0]) or str(code).split(".")[0].zfill(6)
        try:
            volume = int(raw.get("volume") or raw.get("amount") or raw.get("qty") or 0)
        except (TypeError, ValueError):
            volume = 0
        if not code_s or volume <= 0:
            continue
        side = str(raw.get("side") or raw.get("direction") or raw.get("business_name") or "")
        direction = "SHORT" if ("卖" in side or side.upper() in ("SELL", "SHORT", "S")) else "LONG"
        try:
            price = float(raw.get("price") or raw.get("business_price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        out.append(
            {
                "code": code_s,
                "name": str(raw.get("name") or raw.get("stock_name") or ""),
                "direction": direction,
                "offset": "CLOSE" if direction == "SHORT" else "OPEN",
                "volume": volume,
                "price": price,
                "tradeid": str(raw.get("tradeid") or raw.get("business_id") or f"pt-{i+1}"),
                "datetime": str(raw.get("datetime") or raw.get("business_time") or "")[:19],
                "gateway": "ptrade",
            }
        )
    return out


def normalize_ptrade_account(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Map PTrade portfolio/cash dict into desk account fields."""
    if not isinstance(raw, dict):
        return None
    try:
        balance = float(
            raw.get("balance")
            if raw.get("balance") is not None
            else raw.get("total_value") or raw.get("equity") or 0
        )
        available = float(
            raw.get("available")
            if raw.get("available") is not None
            else raw.get("cash") or raw.get("enable_balance") or balance
        )
        frozen = float(raw.get("frozen") or raw.get("frozen_cash") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "balance": balance,
        "available": available,
        "frozen": frozen,
        "gateway": "ptrade",
    }


def build_bridge_ingest_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize a PTrade bridge POST body into ReadonlyBrokerGateway ingest shape."""
    account = body.get("account")
    if isinstance(account, dict):
        account = normalize_ptrade_account(account)
    positions = body.get("positions")
    if isinstance(positions, list):
        # Accept already-normalized desk rows or raw PTrade rows.
        if positions and isinstance(positions[0], dict) and (
            "stock_code" in positions[0] or "enable_amount" in positions[0]
        ):
            positions = normalize_ptrade_positions(positions)
        else:
            positions = [p for p in positions if isinstance(p, dict)]
    else:
        positions = []
    trades = body.get("trades")
    if isinstance(trades, list):
        if trades and isinstance(trades[0], dict) and (
            "business_price" in trades[0] or "stock_code" in trades[0]
        ):
            trades = normalize_ptrade_trades(trades)
        else:
            trades = [t for t in trades if isinstance(t, dict)]
    else:
        trades = []
    return {
        "gateway": str(body.get("gateway") or "ptrade"),
        "account": account,
        "positions": positions,
        "trades": trades,
        # Keep typed objects available for unit tests / callers.
        "_positions_data": positions_from_payload(positions),
        "_trades_data": trades_from_payload(trades),
    }


def build_exec_intents(
    snap: dict[str, Any] | None,
    *,
    send_allowed: bool = False,
) -> dict[str, Any]:
    """Build buy/sell intent list for a PTrade poller.

    ``send_allowed`` stays False in stage 1; PTrade must treat intents as
    watch-only until stage 2 arms Send after human confirm.
    """
    snap = snap if isinstance(snap, dict) else {}
    stats = portfolio_stats()
    gate_ok = bool(stats.get("gate_ok"))
    readonly_ok = bool(stats.get("gate_readonly_ok") or gate_ok)
    buys: list[dict[str, Any]] = []
    verdict = snap.get("verdict") if isinstance(snap.get("verdict"), dict) else {}
    recommend = list(verdict.get("recommend") or snap.get("recommend") or [])
    for card in recommend:
        if not isinstance(card, dict):
            continue
        ready = card.get("ready")
        if ready is None and isinstance(card.get("buy"), dict):
            ready = card["buy"].get("ready")
        ever = bool(card.get("ever_ready") or (card.get("payload") or {}).get("ever_ready"))
        if not ready and not ever:
            continue
        code = normalize_code(card.get("code")) or str(card.get("code") or "").zfill(6)
        if len(code) != 6:
            continue
        buys.append(
            {
                "side": "buy",
                "code": code,
                "name": str(card.get("name") or ""),
                "kind": str(card.get("kind") or "stock"),
                "plan_price": card.get("plan_price") or card.get("buy_price") or card.get("price"),
                "size_tag": card.get("size_tag") or card.get("open_weight") or "full",
                "ready": bool(ready),
                "armed": bool(send_allowed and gate_ok and ready),
                "note": "stage1_watch_only" if not send_allowed else "stage2_confirm_required",
            }
        )
    sells: list[dict[str, Any]] = []
    advice = snap.get("sell_advice") if isinstance(snap.get("sell_advice"), dict) else {}
    for item in advice.get("alert_items") or advice.get("items") or []:
        if not isinstance(item, dict):
            continue
        code = normalize_code(item.get("code")) or str(item.get("code") or "").zfill(6)
        if len(code) != 6:
            continue
        mode = str(item.get("exit_mode") or item.get("mode") or "")
        sells.append(
            {
                "side": "sell",
                "code": code,
                "name": str(item.get("name") or ""),
                "exit_mode": mode,
                "qty": item.get("qty") or item.get("sell_qty"),
                "armed": bool(send_allowed and gate_ok),
                "note": str(item.get("role_label") or item.get("text") or "")[:80],
            }
        )
    return {
        "ok": True,
        "broker": "ptrade",
        "broker_label": "国泰海通 PTrade",
        "send_allowed": bool(send_allowed and gate_ok),
        "gate_ok": gate_ok,
        "gate_readonly_ok": readonly_ok,
        "stage": "2_click" if send_allowed and gate_ok else "1_readonly",
        "buys": buys[:20],
        "sells": sells[:20],
        "rule": (
            "阶段1：只上报持仓/拉意图展示，禁止 order_*；"
            "阶段2：send_allowed 且 armed 且作战台确认后才可下单"
        ),
    }


def intents_etag(payload: dict[str, Any]) -> str:
    """Stable short hash so PTrade can skip unchanged polls."""
    raw = repr(
        (
            payload.get("stage"),
            [(b.get("code"), b.get("armed")) for b in payload.get("buys") or []],
            [(s.get("code"), s.get("exit_mode")) for s in payload.get("sells") or []],
        )
    ).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]
