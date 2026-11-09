"""ntfy mobile push: sim-aligned buy/sell text + pre-open prep.

Server酱 stays on the free daily cap (mostly eod). Intraday actionable
alerts go here so the user can mirror the paper sim book by hand.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from typing import Any

from market_desk.filters import normalize_code
from market_desk.notify import _mark_daily_once, drop_daily_repeats, push_origin

log = logging.getLogger("market_desk.ntfy")

_SIZE_ZH = {
    "full": "满仓",
    "half": "半仓",
    "probe": "试探",
    "side": "支线",
    "scaled": "缩放",
}


def _muted() -> bool:
    return str(os.environ.get("MARKET_DESK_MUTE_NTFY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _click_base() -> str:
    return str(os.environ.get("MARKET_DESK_PUBLIC_URL") or "").strip().rstrip("/")


def resolve_ntfy_endpoint(topic_or_url: str) -> str | None:
    """Return a publish URL from a topic name or full ntfy URL."""
    raw = str(topic_or_url or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    # Topic only → public instance.
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")
    if not safe:
        return None
    return f"https://ntfy.sh/{safe}"


def notify_ntfy(
    topic_or_url: str,
    title: str,
    body: str,
    *,
    priority: str = "high",
    tags: str = "",
    token: str | None = None,
    click: str | None = None,
) -> bool:
    """POST one plain-text notification to ntfy. Return True on HTTP success."""
    if _muted():
        log.info("ntfy muted | %s", title)
        return False
    url = resolve_ntfy_endpoint(topic_or_url)
    if not url:
        return False
    # urllib header values are latin-1 only; keep Title ASCII and put
    # the full (possibly Chinese) title at the top of the UTF-8 body.
    title_s = str(title or "desk")[:120]
    try:
        title_hdr = title_s.encode("latin-1").decode("latin-1")
    except UnicodeEncodeError:
        title_hdr = "market-desk"
    headers = {
        "Title": title_hdr[:120],
        "Priority": str(priority or "default"),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if tags:
        tag_s = str(tags)[:80]
        try:
            tag_s.encode("latin-1")
            headers["Tags"] = tag_s
        except UnicodeEncodeError:
            pass
    click_url = (click or _click_base() or "").strip()
    if click_url:
        headers["Click"] = click_url[:500]
    tok = str(token or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    body_s = str(body or "")
    if title_s and not body_s.startswith(title_s):
        body_s = f"{title_s}\n\n{body_s}"
    data = body_s.encode("utf-8")[:3500]
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        log.info("ntfy ok (%s) | %s", push_origin().get("host"), title)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("ntfy failed: %s | %s", exc, title)
        return False


def is_ntfy_alert(
    key: str,
    *,
    fly: bool = True,
    near: bool = True,
    soft_sell: bool = False,
) -> bool:
    """Return True for intraday alerts that should hit ntfy."""
    k = str(key or "")
    if k.startswith("prep:"):
        return True
    if k.startswith("buy:"):
        return True
    if k.startswith("fly:"):
        return bool(fly)
    if k.startswith("near:") or k.startswith("band:entry:"):
        return bool(near)
    if k.startswith("sell:stop:"):
        return True
    if k.startswith(("sell:take:", "sell:trim:")):
        return bool(soft_sell)
    return False


def filter_ntfy_alerts(
    alerts: list[tuple[str, str, str]],
    *,
    fly: bool = True,
    near: bool = True,
    soft_sell: bool = False,
) -> list[tuple[str, str, str]]:
    """Keep ntfy-eligible alerts for the user's switches."""
    return [
        a
        for a in alerts
        if is_ntfy_alert(a[0], fly=fly, near=near, soft_sell=soft_sell)
    ]


def _recommend_by_code(snap: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    verdict = (snap or {}).get("verdict") if isinstance((snap or {}).get("verdict"), dict) else {}
    rec = verdict.get("recommend") if isinstance(verdict.get("recommend"), dict) else {}
    items = list(rec.get("items") or [])
    primary = rec.get("primary")
    if isinstance(primary, dict):
        items = [primary] + items
    # Bridge / other shapes: flat list under recommend.
    if isinstance(verdict.get("recommend"), list):
        items = list(verdict.get("recommend") or [])
    for it in items:
        if not isinstance(it, dict):
            continue
        code = normalize_code(it.get("code")) or str(it.get("code") or "").zfill(6)
        if len(code) == 6:
            out[code] = it
    return out


def sim_buy_plan_text(
    *,
    code: str,
    name: str,
    price: float | None,
    payload: dict[str, Any] | None = None,
    ready: bool = True,
    signal_type: str = "buy",
) -> str:
    """Build one sim-aligned buy instruction line (qty from paper book rules)."""
    from market_desk.config import TRADE_FEE_CNY
    from market_desk.sim_desk import account_snapshot, plan_buy_qty, size_from_payload

    px = float(price or 0)
    if px <= 0:
        return f"{name} {code} · 缺价格，暂无法对齐模拟股数"
    acct = account_snapshot()
    cash = float(acct.get("cash") or 0)
    equity = float(acct.get("equity") or 0)
    weight, tag = size_from_payload(
        payload if isinstance(payload, dict) else {},
        ready=ready,
        signal_type=signal_type,
    )
    qty = plan_buy_qty(
        px=px,
        open_weight=weight,
        cash=cash,
        equity=equity,
        fee=float(TRADE_FEE_CNY),
    )
    tag_zh = _SIZE_ZH.get(tag, tag or "仓")
    if qty < 100:
        return (
            f"对齐模拟：{tag_zh}不足一手（现金 {cash:.0f} / 权益 {equity:.0f}）"
            f" · 计划价 {px:.2f} — 账户买不起则跳过"
        )
    return (
        f"对齐模拟：{tag_zh} {qty}股 · 限价约 {px:.2f}"
        f" · 现金 {cash:.0f} / 权益 {equity:.0f}"
    )


def sim_sell_plan_text(
    *,
    code: str,
    name: str,
    exit_mode: str,
    sell_price: float | None,
    fallback_qty: int | None = None,
) -> str:
    """Build one sim-aligned sell line from open paper lots when present."""
    from market_desk.db.sim_desk import load_open_sim_lots

    code_s = normalize_code(code) or str(code or "").zfill(6)
    lots = [x for x in load_open_sim_lots() if normalize_code(x.get("code")) == code_s]
    hold = sum(int(x.get("qty") or 0) for x in lots)
    mode = str(exit_mode or "clear")
    if hold > 0:
        if mode == "half" and hold > 100:
            qty = (hold // 200) * 100 or 100
            tag = "减半"
        else:
            qty = hold
            tag = "清仓"
        src = "模拟仓"
    else:
        qty = int(fallback_qty or 0)
        tag = "减半" if mode == "half" else "清仓"
        src = "记账仓" if qty > 0 else "无仓"
    px = float(sell_price or 0)
    px_s = f"{px:.2f}" if px > 0 else "—"
    if qty <= 0:
        return f"{name} {code_s} · {tag} · 无持仓股数（请核对） · 建议价 {px_s}"
    return f"对齐{src}：{tag} {qty}股 · 建议价 {px_s}"


def enrich_ntfy_alert(
    alert_key: str,
    title: str,
    body: str,
    snap: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Append sim-aligned sizing to buy/sell alert bodies."""
    k = str(alert_key or "")
    snap = snap if isinstance(snap, dict) else {}
    if k.startswith(("buy:", "fly:", "near:", "band:entry:")):
        code = k.split(":")[-1]
        code = normalize_code(code) or code
        card = _recommend_by_code(snap).get(code) or {}
        px = (
            card.get("buy_price")
            or card.get("plan_price")
            or card.get("wait_price")
            or card.get("price")
            or card.get("last")
        )
        try:
            px_f = float(px) if px is not None else None
        except (TypeError, ValueError):
            px_f = None
        payload = card.get("payload") if isinstance(card.get("payload"), dict) else card
        if not isinstance(payload, dict):
            payload = {}
        # Approach / fly → force half-size sim plan (prep, not full ready).
        prep = k.startswith(("fly:", "near:", "band:entry:"))
        if prep:
            payload = dict(payload)
            payload["early_half"] = payload.get("early_half") or {"prep": True}
        line = sim_buy_plan_text(
            code=code,
            name=str(card.get("name") or ""),
            price=px_f,
            payload=payload,
            ready=bool(card.get("ready") or k.startswith("buy:")) and not prep,
            signal_type="buy_side" if card.get("desk_source") == "side" else "buy",
        )
        if prep and "接近准备" not in body and "将飞" not in title:
            line = f"{line}\n⚠ 接近准备 · 非系统可买 · 最多半仓预挂或盯盘"
        return alert_key, title, f"{body}\n{line}".strip()
    if k.startswith("sell:"):
        parts = k.split(":")
        code = parts[-1] if parts else ""
        code = normalize_code(code) or code
        sa = snap.get("sell_advice") if isinstance(snap.get("sell_advice"), dict) else {}
        items = list(sa.get("alert_items") or sa.get("items") or [])
        item = next(
            (
                x
                for x in items
                if isinstance(x, dict)
                and (normalize_code(x.get("code")) or str(x.get("code") or "")) == code
            ),
            {},
        )
        mode = str(item.get("exit_mode") or ("clear" if "stop" in k else "half"))
        try:
            fb = int(item.get("sell_qty") or item.get("qty") or 0)
        except (TypeError, ValueError):
            fb = 0
        try:
            sp = float(item.get("sell_price")) if item.get("sell_price") is not None else None
        except (TypeError, ValueError):
            sp = None
        line = sim_sell_plan_text(
            code=code,
            name=str(item.get("name") or ""),
            exit_mode=mode,
            sell_price=sp,
            fallback_qty=fb,
        )
        return alert_key, title, f"{body}\n{line}".strip()
    return alert_key, title, body


def ntfy_priority_for(key: str) -> tuple[str, str]:
    """Return (priority, tags) for an alert key."""
    k = str(key or "")
    if k.startswith("sim:sell:") and ":half" in k:
        return "high", "chart_with_downwards_trend"
    if k.startswith("sim:sell:"):
        return "urgent", "warning,rotating_light"
    if k.startswith("sim:buy:"):
        return "high", "chart_with_upwards_trend"
    if k.startswith("sell:stop:"):
        return "urgent", "warning,rotating_light"
    if k.startswith("sell:"):
        return "high", "chart_with_downwards_trend"
    if k.startswith("buy:"):
        return "high", "chart_with_upwards_trend"
    if k.startswith("fly:"):
        return "default", "bird"
    if k.startswith("near:") or k.startswith("band:entry:"):
        return "default", "eyes"
    if k.startswith("prep:"):
        return "default", "clipboard"
    return "default", "bell"


def list_ntfy_recipients() -> list[dict[str, Any]]:
    """Active members with ntfy_on and a topic configured."""
    from market_desk.db import list_users
    from market_desk.settings import get_settings_for_user

    out: list[dict[str, Any]] = []
    for u in list_users():
        if str(u.get("status") or "") != "active":
            continue
        if str(u.get("role") or "") == "guest":
            continue
        if str(u.get("username") or "").strip().lower() == "guest":
            continue
        try:
            uid = int(u.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if uid <= 0:
            continue
        try:
            s = get_settings_for_user(uid)
        except Exception:
            continue
        if not bool(s.get("ntfy_on")):
            continue
        topic = str(s.get("ntfy_topic") or "").strip()
        if not topic:
            continue
        out.append(
            {
                "id": uid,
                "username": u.get("username"),
                "ntfy_topic": topic,
                "ntfy_token": str(s.get("ntfy_token") or "").strip(),
                "ntfy_fly": bool(s.get("ntfy_fly", True)),
                "ntfy_near": bool(s.get("ntfy_near", True)),
                "ntfy_soft_sell": bool(s.get("ntfy_soft_sell", False)),
                "ntfy_prep": bool(s.get("ntfy_prep", True)),
            }
        )
    return out


def push_ntfy_alerts(
    alerts: list[tuple[str, str, str]],
    current: dict[str, Any] | None = None,
) -> int:
    """Fan-out sim-enriched alerts to every configured ntfy user."""
    trade_date = str((current or {}).get("trade_date") or "")[:10]
    alerts, once_keys = drop_daily_repeats(alerts, trade_date=trade_date)
    if not alerts:
        return 0
    recipients = list_ntfy_recipients()
    if not recipients:
        return 0
    _mark_daily_once(trade_date, once_keys)
    ok_n = 0
    for user in recipients:
        filtered = filter_ntfy_alerts(
            alerts,
            fly=bool(user.get("ntfy_fly", True)),
            near=bool(user.get("ntfy_near", True)),
            soft_sell=bool(user.get("ntfy_soft_sell", False)),
        )
        for key, title, body in filtered:
            _k, _t, enriched = enrich_ntfy_alert(key, title, body, current)
            pri, tags = ntfy_priority_for(key)
            if notify_ntfy(
                str(user.get("ntfy_topic") or ""),
                _t,
                enriched,
                priority=pri,
                tags=tags,
                token=str(user.get("ntfy_token") or "") or None,
            ):
                ok_n += 1
                log.info(
                    "ntfy -> user %s | %s",
                    user.get("username") or user.get("id"),
                    title,
                )
    return ok_n


def _sim_fill_alert(
    event: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Build one ntfy tuple from a live sim fill event, or None if invalid."""
    side = str(event.get("side") or "").lower()
    code = normalize_code(event.get("code")) or str(event.get("code") or "").zfill(6)
    name = str(event.get("name") or code)
    day = str(event.get("trade_date") or "")[:10]
    try:
        lot_id = int(event.get("lot_id") or 0)
    except (TypeError, ValueError):
        lot_id = 0
    try:
        qty = int(event.get("qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    try:
        px = float(event.get("px")) if event.get("px") is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    if not day or not code or qty <= 0:
        return None
    note = str(event.get("note") or "").strip()
    px_s = f"{px:.3f}" if px > 0 else "—"
    if side == "buy":
        tag = str(event.get("size_tag") or "")
        tag_zh = _SIZE_ZH.get(tag, tag or "买入")
        key = f"sim:buy:{day}:{code}:{lot_id or qty}"
        title = f"模拟买入·请跟单 {name}"
        body = (
            f"买入 {code} {name} {qty}股 · 约 {px_s} · {tag_zh}\n"
            f"{note}\n"
            f"⚠ 模拟仓已成交 — 请在券商按同股数手动跟"
        ).strip()
        return key, title, body
    if side == "sell":
        mode = str(event.get("exit_mode") or "clear")
        mode_zh = "减半" if mode == "half" else "清仓"
        key = f"sim:sell:{day}:{lot_id or code}:{mode}"
        title = f"模拟{mode_zh}·请跟单 {name}"
        pnl = event.get("pnl_pct")
        try:
            pnl_s = f"{float(pnl):+.2f}%" if pnl is not None else "—"
        except (TypeError, ValueError):
            pnl_s = "—"
        body = (
            f"卖出 {code} {name} {qty}股 · 约 {px_s} · {mode_zh} · 盈亏 {pnl_s}\n"
            f"{note}\n"
            f"⚠ 模拟仓已成交 — 请在券商按同股数手动跟"
        ).strip()
        return key, title, body
    return None


def is_ntfy_sim_fill(
    event: dict[str, Any],
    *,
    soft_sell: bool = False,
) -> bool:
    """Return True when a sim fill should hit ntfy for this user."""
    side = str(event.get("side") or "").lower()
    if side == "buy":
        return True
    if side == "sell":
        mode = str(event.get("exit_mode") or "clear")
        if mode == "half":
            return bool(soft_sell)
        return True  # clear = hard follow (stop / full exit)
    return False


def push_ntfy_sim_fills(events: list[dict[str, Any]] | None) -> int:
    """Fan-out live sim fills so the user can mirror the paper book by hand.

    Dedupes per day with ``sim:buy:…`` / ``sim:sell:…`` keys. Half-size sells
    honor ``ntfy_soft_sell``; buys and clear exits always push when ntfy is on.
    Backfill / replay paths must pass ``notify=False`` upstream and skip this.
    """
    raw = [e for e in (events or []) if isinstance(e, dict)]
    if not raw:
        return 0
    recipients = list_ntfy_recipients()
    if not recipients:
        return 0
    built: list[tuple[str, str, str, dict[str, Any]]] = []
    day = ""
    for ev in raw:
        day = day or str(ev.get("trade_date") or "")[:10]
        alert = _sim_fill_alert(ev)
        if alert:
            built.append((*alert, ev))
    if not built:
        return 0
    alerts_only = [(k, t, b) for k, t, b, _ev in built]
    alerts_only, once_keys = drop_daily_repeats(alerts_only, trade_date=day)
    if not alerts_only:
        return 0
    keep = {a[0] for a in alerts_only}
    built = [row for row in built if row[0] in keep]
    _mark_daily_once(day, once_keys)
    ok_n = 0
    for user in recipients:
        soft = bool(user.get("ntfy_soft_sell", False))
        for key, title, body, ev in built:
            if not is_ntfy_sim_fill(ev, soft_sell=soft):
                continue
            pri, tags = ntfy_priority_for(key)
            if notify_ntfy(
                str(user.get("ntfy_topic") or ""),
                title,
                body,
                priority=pri,
                tags=tags,
                token=str(user.get("ntfy_token") or "") or None,
            ):
                ok_n += 1
                log.info(
                    "ntfy sim-fill -> user %s | %s",
                    user.get("username") or user.get("id"),
                    title,
                )
    return ok_n


def push_user_ntfy_sell_alerts(
    user_settings: dict[str, Any],
    user_meta: dict[str, Any],
    snap: dict[str, Any],
    *,
    trade_date: str,
) -> int:
    """Push this user's newly ready hard sells to their ntfy topic."""
    from market_desk.notify import build_sell_ready_alerts

    topic = str(user_settings.get("ntfy_topic") or "").strip()
    if not bool(user_settings.get("ntfy_on")) or not topic:
        return 0
    try:
        uid = int(user_meta.get("id") or 0)
    except (TypeError, ValueError):
        uid = 0
    day = str(trade_date or "")[:10]
    if uid <= 0 or not day:
        return 0
    sa = snap.get("sell_advice") or {}
    alerts = build_sell_ready_alerts(sa.get("alert_items") or sa.get("items"))
    soft = bool(user_settings.get("ntfy_soft_sell", False))
    alerts = filter_ntfy_alerts(alerts, fly=False, soft_sell=soft)
    if not alerts:
        return 0
    store = f"ntfy_sell_once:{day}:{uid}"
    try:
        from market_desk.db import load_setting, save_setting

        sent = list(load_setting(store) or [])
    except Exception:
        sent = []
    fresh = [a for a in alerts if a[0] not in sent]
    if not fresh:
        return 0
    ok_n = 0
    token = str(user_settings.get("ntfy_token") or "").strip() or None
    for key, title, body in fresh:
        _k, _t, enriched = enrich_ntfy_alert(key, title, body, snap)
        pri, tags = ntfy_priority_for(key)
        if notify_ntfy(topic, _t, enriched, priority=pri, tags=tags, token=token):
            ok_n += 1
            sent.append(key)
    if ok_n:
        try:
            from market_desk.db import save_setting

            save_setting(store, sent)
        except Exception:
            log.exception("save ntfy sell once failed uid=%s", uid)
    return ok_n


def build_ntfy_prep_brief(snap: dict[str, Any] | None) -> tuple[str, str] | None:
    """Build pre-open prep title/body aligned to the sim book + watchlist."""
    from market_desk.db.sim_desk import load_open_sim_lots
    from market_desk.sim_desk import account_snapshot

    snap = snap if isinstance(snap, dict) else {}
    day = str(snap.get("trade_date") or "")[:10] or "今日"
    acct = account_snapshot()
    cash = float(acct.get("cash") or 0)
    equity = float(acct.get("equity") or 0)
    lots = load_open_sim_lots()
    lines = [
        f"现金 {cash:.0f} · 权益 {equity:.0f} · 模拟持仓 {len(lots)}",
        "",
        "【关注·若亮灯按模拟股数】",
    ]
    cards = list(_recommend_by_code(snap).values())[:8]
    if not cards:
        morning = snap.get("morning_brief") if isinstance(snap.get("morning_brief"), dict) else {}
        focus = str(morning.get("focus") or "").strip()
        if focus:
            lines.append(f"· {focus[:120]}")
        for b in (morning.get("bullets") or [])[:5]:
            lines.append(f"· {b}")
    else:
        for card in cards:
            code = normalize_code(card.get("code")) or str(card.get("code") or "")
            name = str(card.get("name") or code)
            px = card.get("buy_price") or card.get("plan_price") or card.get("price") or card.get("last")
            try:
                px_f = float(px) if px is not None else None
            except (TypeError, ValueError):
                px_f = None
            payload = card.get("payload") if isinstance(card.get("payload"), dict) else card
            plan = sim_buy_plan_text(
                code=code,
                name=name,
                price=px_f,
                payload=payload if isinstance(payload, dict) else {},
                ready=bool(card.get("ready")),
                signal_type="buy_side" if card.get("desk_source") == "side" else "buy",
            )
            state = "已亮" if card.get("ready") else ("将飞" if card.get("fly_warn") else "等待")
            lines.append(f"· [{state}] {name} {code}")
            lines.append(f"  {plan}")
    lines.append("")
    lines.append("【模拟持仓·卖点预告】")
    if not lots:
        lines.append("· 无开仓")
    else:
        for lot in lots[:8]:
            code = lot.get("code") or ""
            name = lot.get("name") or code
            qty = lot.get("qty") or 0
            opx = lot.get("open_px")
            stop = None
            try:
                if opx is not None:
                    stop = float(opx) * 0.95  # soft preview only; real stop from sell engine
            except (TypeError, ValueError):
                stop = None
            stop_s = f" · 成本附近止损参考 {stop:.2f}" if stop else ""
            lines.append(f"· {name} {code} · {qty}股 · 成本 {opx}{stop_s}")
    lines.append("")
    lines.append("亮灯/硬止损会再推 ntfy；请打开券商 App 待命。非自动下单。")
    body = "\n".join(lines)
    if len(body) < 40:
        return None
    return f"盘前准备 · {day}", body


def push_ntfy_prep(snap: dict[str, Any] | None, *, day: str) -> int:
    """Send once-per-day prep brief to users with ntfy_prep enabled."""
    from market_desk.db import load_setting, save_setting

    flag = f"ntfy_prep_pushed:{day}"
    if load_setting(flag):
        return 0
    brief = build_ntfy_prep_brief(snap)
    if not brief:
        save_setting(flag, {"ok_n": 0, "skip": "empty"})
        return 0
    title, body = brief
    ok_n = 0
    for user in list_ntfy_recipients():
        if not bool(user.get("ntfy_prep", True)):
            continue
        if notify_ntfy(
            str(user.get("ntfy_topic") or ""),
            title,
            body,
            priority="default",
            tags="clipboard",
            token=str(user.get("ntfy_token") or "") or None,
        ):
            ok_n += 1
    save_setting(flag, {"ok_n": ok_n})
    return ok_n


def _fmt_px(v: Any) -> str:
    """Format a price for ntfy lines; return em-dash when missing."""
    try:
        f = float(v) if v is not None else None
    except (TypeError, ValueError):
        return "—"
    if f is None:
        return "—"
    return f"{f:.3f}".rstrip("0").rstrip(".") if abs(f) < 100 else f"{f:.2f}"


def _plan_px_from_trade(t: dict[str, Any]) -> float | None:
    """Prefer payload plan_price; fall back to ``计划X`` inside the note."""
    import re

    payload = t.get("lot_payload") if isinstance(t.get("lot_payload"), dict) else {}
    for key in ("plan_price", "plan_px"):
        try:
            v = float(payload.get(key)) if payload.get(key) is not None else None
        except (TypeError, ValueError):
            v = None
        if v and v > 0:
            return v
    note = str(t.get("note") or "")
    m = re.search(r"计划\s*([0-9]+(?:\.[0-9]+)?)", note)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def format_simday_trade_block(t: dict[str, Any], *, index: int, total: int) -> str:
    """Format one paper fill as a labeled multi-line block for ntfy."""
    from market_desk.pick_score import SOURCE_LABELS
    from market_desk.sim_desk import BUY_FILL_REASON_ZH

    side = str(t.get("side") or "")
    code = str(t.get("code") or "")
    name = str(t.get("name") or code)
    qty = t.get("qty")
    try:
        qty_i = int(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty_i = None
    qty_s = f"{qty_i}股" if qty_i is not None else "—"
    px_s = _fmt_px(t.get("px"))
    at = str(t.get("filled_at") or "")
    time_s = at[11:19] if len(at) >= 19 else (at[:19] or "—")
    head = f"—— {index}/{total} ——"
    lines: list[str] = [head]

    if side == "buy":
        tag = str(t.get("size_tag") or "").strip()
        tag_zh = _SIZE_ZH.get(tag) or (tag or "买入")
        plan = _plan_px_from_trade(t)
        px_line = f"成交  {px_s}"
        if plan and plan > 0:
            px_line += f"（计划 {_fmt_px(plan)}）"
        payload = t.get("lot_payload") if isinstance(t.get("lot_payload"), dict) else {}
        reason_code = str(payload.get("fill_reason") or t.get("note_code") or "").strip()
        reason_zh = BUY_FILL_REASON_ZH.get(reason_code, "")
        channel = SOURCE_LABELS.get(str(t.get("signal_type") or ""), "")
        # Prefer structured bits; fall back to a short cleaned note.
        note = str(t.get("note") or "")
        explain_parts: list[str] = []
        if channel:
            explain_parts.append(channel)
        if reason_zh:
            explain_parts.append(reason_zh)
        elif note:
            # Drop plan/fill/size/mainline duplicates already shown as fields.
            keep: list[str] = []
            for part in note.replace(" · ", "·").split("·"):
                p = part.strip()
                if not p:
                    continue
                if p.startswith("计划") or p.startswith("成交"):
                    continue
                if p in ("半仓", "满仓", "满仓档", "试探", "试探仓", "支线", "支线仓", "手工仓", tag_zh):
                    continue
                if p.startswith("主线"):
                    continue
                keep.append(p)
            explain_parts.extend(keep[:2])
        mainline = str(
            payload.get("mainline") or t.get("mainline") or ""
        ).strip()
        lines.append(f"买入  {name}（{code}）")
        lines.append(f"仓位  {tag_zh} · {qty_s}")
        lines.append(px_line)
        lines.append(f"时间  {time_s}")
        if explain_parts:
            lines.append(f"说明  {' · '.join(explain_parts)[:80]}")
        if mainline:
            lines.append(f"主线  {mainline}")
        try:
            notional = float(t.get("notional")) if t.get("notional") is not None else None
        except (TypeError, ValueError):
            notional = None
        if notional and notional > 0:
            lines.append(f"金额  约 {notional:.0f}")
    elif side == "sell":
        mode = str(t.get("exit_mode") or "clear")
        mode_zh = "减半" if mode == "half" else "清仓"
        pnl = t.get("pnl_pct")
        try:
            pnl_s = f"{float(pnl):+.2f}%" if pnl is not None else "—"
        except (TypeError, ValueError):
            pnl_s = "—"
        role = str(t.get("role_label") or "").strip()
        note = str(t.get("note") or "").strip()
        lines.append(f"卖出·{mode_zh}  {name}（{code}）")
        lines.append(f"数量  {qty_s}")
        lines.append(f"成交  {px_s} · 盈亏 {pnl_s}")
        lines.append(f"时间  {time_s}")
        detail = role or note
        if detail:
            lines.append(f"说明  {detail[:80]}")
    else:
        lines.append(f"{side or '成交'}  {name}（{code}） · {qty_s} · {px_s} · {time_s}")
    return "\n".join(lines)


def build_today_ntfy_messages(
    snap: dict[str, Any] | None = None,
    *,
    day: str | None = None,
) -> list[tuple[str, str, str, str]]:
    """Build today's ntfy payloads for manual replay / catch-up.

    Returns list of ``(key, title, body, priority)``. Includes prep, today's
    paper fills (补发), and live near/buy cards from ``snap`` when present.
    """
    from market_desk.calendar import today_cn
    from market_desk.sim_desk import recent_trades

    day_s = str(day or today_cn())[:10]
    snap = snap if isinstance(snap, dict) else {}
    out: list[tuple[str, str, str, str]] = []

    brief = build_ntfy_prep_brief(snap or {"trade_date": day_s})
    if brief:
        title, body = brief
        out.append((f"prep:{day_s}", f"【补发】{title}", body, "default"))

    day_trades = [
        t
        for t in recent_trades(200)
        if str(t.get("trade_date") or "")[:10] == day_s
        or str(t.get("filled_at") or "")[:10] == day_s
    ]
    if day_trades:
        n = len(day_trades)
        lines = [
            f"共 {n} 笔 · 对照券商手搓 · 非自动下单",
            "",
        ]
        for i, t in enumerate(day_trades, start=1):
            lines.append(format_simday_trade_block(t, index=i, total=n))
            lines.append("")
        out.append(
            (
                f"simday:{day_s}",
                f"【补发】今日模拟 · {n}笔 · {day_s}",
                "\n".join(lines).rstrip(),
                "high",
            )
        )

    # Live cards: near / ready (same filters as intraday).
    for code, card in _recommend_by_code(snap).items():
        name = str(card.get("name") or code)
        px = card.get("buy_price") or card.get("plan_price") or card.get("last")
        try:
            px_f = float(px) if px is not None else None
        except (TypeError, ValueError):
            px_f = None
        payload = card.get("payload") if isinstance(card.get("payload"), dict) else card
        if not isinstance(payload, dict):
            payload = {}
        if card.get("ready") and not card.get("ready_relaxed"):
            line = sim_buy_plan_text(
                code=code,
                name=name,
                price=px_f,
                payload=payload,
                ready=True,
                signal_type="buy_side" if card.get("desk_source") == "side" else "buy",
            )
            out.append(
                (
                    f"buy:{code}",
                    f"【补发·可买】{name} {code}",
                    f"{name} {code} 建议买 {px_f if px_f else '—'}\n{line}",
                    "high",
                )
            )
        elif card.get("near_entry") or card.get("fly_warn"):
            payload = dict(payload)
            payload["early_half"] = payload.get("early_half") or {"prep": True}
            line = sim_buy_plan_text(
                code=code,
                name=name,
                price=px_f,
                payload=payload,
                ready=False,
                signal_type="buy_side" if card.get("desk_source") == "side" else "buy",
            )
            kind = "将飞" if card.get("fly_warn") else "接近"
            out.append(
                (
                    f"near:{code}",
                    f"【补发·{kind}准备】{name} {code}",
                    f"{name} {code} · 现价/计划 {px_f if px_f else '—'}\n"
                    f"{line}\n⚠ 接近准备 · 非系统可买 · 最多半仓预挂或盯盘",
                    "default",
                )
            )
    return out


def push_ntfy_replay_today(
    *,
    topic: str,
    token: str | None = None,
    snap: dict[str, Any] | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    """Send catch-up ntfy messages for today to one topic. Returns send stats."""
    msgs = build_today_ntfy_messages(snap, day=day)
    sent = 0
    titles: list[str] = []
    for _key, title, body, pri in msgs:
        tags = "clipboard" if _key.startswith("prep:") else (
            "chart_with_upwards_trend" if _key.startswith("buy:") else "bell"
        )
        if notify_ntfy(topic, title, body, priority=pri, tags=tags, token=token):
            sent += 1
            titles.append(title)
    return {
        "ok": sent > 0 or not msgs,
        "built": len(msgs),
        "sent": sent,
        "titles": titles,
        "empty": not msgs,
    }
