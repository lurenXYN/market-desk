# -*- coding: utf-8 -*-
"""Guotai Haitong PTrade ↔ market-desk bridge (stage 1: read-only).

Paste into the PTrade strategy editor after editing the three constants below.
Do NOT call order_* while SEND_ALLOWED is False.

PTrade API names vary by broker build; adjust get_positions / get_portfolio
wrappers if your environment uses different helpers.
"""

# ========= edit these three =========
DESK_BASE_URL = "https://YOUR_DESK_HOST"  # no trailing slash
DESK_BRIDGE_TOKEN = "PASTE_TOKEN_FROM_OPS"
SEND_ALLOWED = False  # stage 1 MUST stay False
# ====================================

import json

try:
    from six.moves.urllib.request import Request, urlopen  # type: ignore
except Exception:  # pragma: no cover
    from urllib.request import Request, urlopen  # noqa: F401


def _desk_request(method, path, body=None):
    """Call desk bridge HTTP with the shared token header."""
    url = DESK_BASE_URL.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=data, method=method.upper())
    req.add_header("X-Desk-Bridge-Token", DESK_BRIDGE_TOKEN)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _positions_payload():
    """Build ingest JSON from common PTrade helpers (best-effort)."""
    positions = []
    account = None
    trades = []
    try:
        # Prefer context helpers when present.
        if "get_positions" in globals() and callable(get_positions):  # noqa: F821
            positions = list(get_positions() or [])  # noqa: F821
        elif "context" in globals() and hasattr(context, "portfolio"):  # noqa: F821
            pos_map = getattr(context.portfolio, "positions", {}) or {}  # noqa: F821
            for code, p in pos_map.items():
                positions.append(
                    {
                        "stock_code": str(code),
                        "volume": int(getattr(p, "amount", 0) or getattr(p, "total_amount", 0) or 0),
                        "enable_amount": int(getattr(p, "enable_amount", 0) or 0),
                        "cost_basis": float(getattr(p, "avg_cost", 0) or getattr(p, "cost_basis", 0) or 0),
                        "last_sale_price": float(getattr(p, "price", 0) or 0),
                        "name": str(getattr(p, "name", "") or ""),
                    }
                )
    except Exception as exc:
        log.info("desk bridge positions err: %s" % exc)  # noqa: F821

    try:
        if "get_portfolio" in globals() and callable(get_portfolio):  # noqa: F821
            pf = get_portfolio()  # noqa: F821
            if isinstance(pf, dict):
                account = pf
            else:
                account = {
                    "total_value": float(getattr(pf, "portfolio_value", 0) or getattr(pf, "total_value", 0) or 0),
                    "cash": float(getattr(pf, "cash", 0) or getattr(pf, "available_cash", 0) or 0),
                    "frozen_cash": float(getattr(pf, "frozen_cash", 0) or 0),
                }
        elif "context" in globals() and hasattr(context, "portfolio"):  # noqa: F821
            pf = context.portfolio  # noqa: F821
            account = {
                "total_value": float(getattr(pf, "portfolio_value", 0) or 0),
                "cash": float(getattr(pf, "cash", 0) or 0),
                "frozen_cash": float(getattr(pf, "frozen_cash", 0) or 0),
            }
    except Exception as exc:
        log.info("desk bridge account err: %s" % exc)  # noqa: F821

    return {
        "gateway": "ptrade",
        "account": account,
        "positions": positions,
        "trades": trades,
    }


def initialize(context):  # noqa: ARG001
    """PTrade entry: schedule a slow poll; never place orders in stage 1."""
    log.info("desk bridge stage1 start; SEND_ALLOWED=%s" % SEND_ALLOWED)  # noqa: F821
    # Run every ~2 minutes during continuous auction; adjust to your build.
    try:
        run_interval(context, _poll_once, seconds=120)  # noqa: F821
    except Exception:
        # Some builds only support run_daily / handle_data timers.
        log.info("run_interval unavailable; call _poll_once from handle_data")  # noqa: F821


def _poll_once(context):  # noqa: ARG001
    """Push positions then pull intents (watch-only)."""
    if not DESK_BRIDGE_TOKEN or "PASTE_" in DESK_BRIDGE_TOKEN or "YOUR_" in DESK_BASE_URL:
        log.info("desk bridge: edit DESK_BASE_URL / DESK_BRIDGE_TOKEN first")  # noqa: F821
        return
    try:
        health = _desk_request("GET", "/api/bridge/ptrade/health")
        log.info("desk health gate_ok=%s readonly=%s" % (  # noqa: F821
            health.get("gate_ok"), health.get("gate_readonly_ok")
        ))
    except Exception as exc:
        log.info("desk health fail: %s" % exc)  # noqa: F821
        return

    try:
        body = _positions_payload()
        pushed = _desk_request("POST", "/api/bridge/ptrade/positions", body)
        log.info("desk positions push ok=%s n=%s" % (  # noqa: F821
            pushed.get("ok"), len(body.get("positions") or [])
        ))
    except Exception as exc:
        log.info("desk positions fail: %s" % exc)  # noqa: F821

    try:
        q = "/api/bridge/ptrade/intents?send_allowed=%s" % (
            "true" if SEND_ALLOWED else "false"
        )
        intents = _desk_request("GET", q)
        buys = intents.get("buys") or []
        sells = intents.get("sells") or []
        log.info(  # noqa: F821
            "desk intents stage=%s buys=%s sells=%s etag=%s"
            % (intents.get("stage"), len(buys), len(sells), intents.get("etag"))
        )
        for b in buys[:5]:
            log.info("  buy watch %s %s ready=%s armed=%s" % (  # noqa: F821
                b.get("code"), b.get("name"), b.get("ready"), b.get("armed")
            ))
        for s in sells[:5]:
            log.info("  sell watch %s %s mode=%s" % (  # noqa: F821
                s.get("code"), s.get("exit_mode"), s.get("armed")
            ))
        # Stage 1 hard stop: never order even if armed somehow flipped.
        if SEND_ALLOWED:
            log.info("SEND_ALLOWED true but stage1 script still refuses order_*")  # noqa: F821
    except Exception as exc:
        log.info("desk intents fail: %s" % exc)  # noqa: F821


def handle_data(context, data):  # noqa: ARG001
    """Fallback tick hook when run_interval is missing."""
    # Throttle yourself if the build calls this every bar.
    pass
