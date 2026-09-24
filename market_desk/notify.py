"""Windows toast notifications for high-priority desk alerts."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger("market_desk.notify")

# Per-event cooldown seconds (decision toasts use the runtime setting instead).
COOLDOWN_STOP_SEC = 60.0
COOLDOWN_ENTRY_SEC = 600.0
COOLDOWN_CHASE_SEC = 300.0


def notify_windows(title: str, body: str) -> bool:
    """Show a bottom-right Windows toast. Return True if the toast was queued."""
    try:
        from winotify import Notification, audio

        toast = Notification(
            app_id="牛来-作战台",
            title=(title or "作战台")[:60],
            msg=(body or "")[:220],
            duration="short",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return True
    except Exception:
        log.exception("windows toast failed")
        return False


def build_toast_alerts(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Diff two snapshots into (dedupe_key, title, body) toast alerts."""
    if not previous or not previous.get("ok") or not current.get("ok"):
        return []
    alerts: list[tuple[str, str, str]] = []
    prev_v = previous.get("verdict") or {}
    cur_v = current.get("verdict") or {}
    prev_action = prev_v.get("action") or ""
    cur_action = cur_v.get("action") or ""
    prev_ml = ((prev_v.get("mainline") or {}).get("name")) or ""
    cur_ml = ((cur_v.get("mainline") or {}).get("name")) or ""
    prev_phase = previous.get("phase") or ""
    cur_phase = current.get("phase") or ""

    if cur_action == "可买入" and prev_action != "可买入":
        rec = cur_v.get("recommend") or {}
        primary = rec.get("primary") or {}
        code = primary.get("code") or rec.get("code") or ""
        name = primary.get("name") or rec.get("name") or ""
        px = primary.get("buy_price") or primary.get("last") or rec.get("price") or ""
        life = (cur_v.get("mainline") or {}).get("lifecycle") or ""
        life_zh = {"starting": "萌芽", "ongoing": "主升", "ending": "衰退"}.get(
            str(life), str(life) or "—"
        )
        size = (cur_v.get("playbook") or {}).get("size_note") or rec.get("size_note") or ""
        half = bool(primary.get("ready_relaxed") or rec.get("ready_relaxed"))
        title = _push_title("buy_half" if half else "buy", name, code)
        body = (
            f"主线 {cur_ml or '—'} · 相位 {cur_phase or '—'} · 阶段 {life_zh}\n"
            f"{name} {code} 建议买 {px}"
        ).strip()
        if size:
            body = f"{body}\n{str(size)[:80]}"
        alerts.append(
            (
                f"buy:{code or cur_ml}",
                title,
                body,
            )
        )
    elif prev_action == "可买入" and cur_action in ("观望", "观察回踩"):
        alerts.append(
            (
                f"exit:{cur_action}:{cur_ml}",
                cur_action,
                f"主线 {cur_ml or '—'} · 刚从可买入切到{cur_action}，注意仓位",
            )
        )

    if cur_ml and cur_ml != prev_ml:
        alerts.append(
            (
                f"mainline:{cur_ml}",
                "主线切换",
                f"{prev_ml or '未明'} → {cur_ml} · {cur_action}",
            )
        )

    if cur_phase in ("恐慌", "高潮") and cur_phase != prev_phase:
        temp = current.get("temperature")
        alerts.append(
            (
                f"phase:{cur_phase}",
                f"相位 · {cur_phase}",
                f"温度 {temp if temp is not None else '—'} · 注意节奏与仓位",
            )
        )

    alerts.extend(build_fly_window_alerts(previous, current))

    prev_ready = {
        f"{x.get('urgency')}:{x.get('code')}"
        for x in ((previous.get("sell_advice") or {}).get("items") or [])
        if x.get("ready")
    }
    for item in ((current.get("sell_advice") or {}).get("items") or []):
        if not item.get("ready"):
            continue
        urgency = str(item.get("urgency") or "")
        if urgency not in ("stop", "take", "trim"):
            continue
        code = item.get("code") or ""
        key = f"{urgency}:{code}"
        if key in prev_ready:
            continue
        urg_map = {"stop": "止损", "take": "止盈", "trim": "减仓"}
        kind = urg_map.get(urgency, "卖出")
        name = item.get("name") or ""
        alerts.append(
            (
                f"sell:{key}",
                _push_title(kind, name, code),
                (
                    f"{name} {code} 建议卖 {item.get('sell_price')} "
                    f"浮盈 {item.get('pnl_pct')}%"
                ).strip(),
            )
        )
    return alerts


def _push_title(kind: str, name: Any = "", code: Any = "") -> str:
    """Build a unified WeChat / toast title for buy · fly · sell alerts."""
    nm = str(name or "").strip()
    cd = str(code or "").strip()
    who = f"{nm} {cd}".strip() or "—"
    labels = {
        "buy": "可买·满",
        "buy_half": "可买·半",
        "fly": "将飞·半仓",
        "止损": "止损",
        "止盈": "止盈",
        "减仓": "减仓",
        "卖出": "卖出",
    }
    tag = labels.get(str(kind), str(kind) or "提醒")
    return f"【{tag}】{who}"


def _recommend_fly_codes(snap: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Map code → recommend item currently tagged with fly_warn."""
    out: dict[str, dict[str, Any]] = {}
    rec = ((snap or {}).get("verdict") or {}).get("recommend") or {}
    for item in rec.get("items") or []:
        if not item.get("fly_warn"):
            continue
        code = str(item.get("code") or "").strip()
        if code:
            out[code] = item
    return out


def build_fly_window_alerts(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """Edge-fire when a half-size fly window newly appears on a recommend card."""
    if not previous or not previous.get("ok") or not current.get("ok"):
        return []
    prev = _recommend_fly_codes(previous)
    cur = _recommend_fly_codes(current)
    alerts: list[tuple[str, str, str]] = []
    for code, item in cur.items():
        if code in prev:
            continue
        name = item.get("name") or ""
        px = item.get("buy_price") or item.get("last") or ""
        kind = "价带半仓" if item.get("ready_relaxed") else "可试探半仓"
        note = item.get("fly_note") or "半仓试探窗口，再等可能飞"
        body = f"{name} {code} · {kind} · 建议 {px}\n{note}".strip()
        alerts.append((f"fly:{code}", _push_title("fly", name, code), body))
    return alerts


def toast_priority(key: str) -> int:
    """Return sort rank for one toast key (lower = more urgent)."""
    k = str(key or "")
    if k.startswith("ops:"):
        return 0
    if k.startswith("sell:") or k.startswith("band:stop:") or k.startswith("wl:stop:"):
        return 0
    if k.startswith("buy:") or k.startswith("fly:"):
        return 1
    if k.startswith("band:entry:") or k.startswith("wl:suggest:"):
        return 2
    if k.startswith("band:chase:") or k.startswith("wl:chase:"):
        return 3
    if k.startswith("exit:"):
        return 4
    if k.startswith("mainline:"):
        return 5
    if k.startswith("phase:"):
        return 6
    return 9


def is_level_toast(key: str) -> bool:
    """Return True for price-zone toasts that should fire on edge only."""
    k = str(key or "")
    return k.startswith("band:") or k.startswith("wl:")


def is_decision_toast(key: str) -> bool:
    """Return True for verdict / phase / mainline decision toasts (not sells)."""
    k = str(key or "")
    return k.startswith(("buy:", "fly:", "exit:", "mainline:", "phase:"))


def is_risk_toast(key: str) -> bool:
    """Return True for sell / stop / ops toasts that stay active in quiet windows."""
    k = str(key or "")
    return (
        k.startswith("sell:")
        or k.startswith("band:stop:")
        or k.startswith("wl:stop:")
        or k.startswith("ops:")
    )


def cooldown_for_key(key: str, decision_cooldown: float) -> float:
    """Return cooldown seconds for one toast key."""
    k = str(key or "")
    if k.startswith(("sell:", "band:stop:", "wl:stop:")):
        return COOLDOWN_STOP_SEC
    if k.startswith(("band:entry:", "wl:suggest:", "fly:")):
        return COOLDOWN_ENTRY_SEC
    if k.startswith(("band:chase:", "wl:chase:")):
        return COOLDOWN_CHASE_SEC
    return float(decision_cooldown)


def is_buy_quiet_window(
    now: datetime,
    *,
    trading_day: bool | None = None,
    open_mute_minutes: int = 5,
    tail_mute_minutes: int = 30,
) -> bool:
    """
    Return True when buy/entry/mainline noise should be muted.

    Quiet during auction (09:15–09:30), configured open mute after 09:30,
    lunch, late-session tail mute before close, after close, and non-trading days.
    Risk toasts still pass via ``filter_alerts_for_policy``.
    """
    if trading_day is False:
        return True
    minutes = now.hour * 60 + now.minute
    if 9 * 60 + 15 <= minutes < 9 * 60 + 30:
        return True
    mute = max(0, int(open_mute_minutes or 0))
    if mute and 9 * 60 + 30 <= minutes < 9 * 60 + 30 + mute:
        return True
    if 9 * 60 + 30 <= minutes <= 11 * 60 + 30:
        return False
    # Afternoon session: quiet only in the configured tail before 15:00.
    if 13 * 60 <= minutes <= 15 * 60:
        tail = max(0, min(90, int(tail_mute_minutes or 0)))
        if tail and minutes >= 15 * 60 - tail:
            return True
        return False
    return True


def is_pre_match_window(now: datetime) -> bool:
    """True before the 09:25 call-auction match (nothing can be filled yet)."""
    return now.hour * 60 + now.minute < 9 * 60 + 25


def is_trade_toast(key: str) -> bool:
    """Return True for buy / sell / price-zone toasts that imply an order now."""
    k = str(key or "")
    return k.startswith(("buy:", "fly:", "sell:", "band:", "wl:", "exit:"))


def filter_alerts_for_policy(
    alerts: list[tuple[str, str, str]],
    *,
    decision_alerts: bool = True,
    quiet_buy: bool = False,
    pre_match: bool = False,
) -> list[tuple[str, str, str]]:
    """Apply decision-toggle, pre-09:25 and auction/off-session mute rules."""
    out: list[tuple[str, str, str]] = []
    for key, title, body in alerts:
        if pre_match and is_trade_toast(key):
            continue
        if not decision_alerts and is_decision_toast(key):
            continue
        if quiet_buy and not is_risk_toast(key):
            continue
        out.append((key, title, body))
    return out


def select_toasts_for_round(
    alerts: list[tuple[str, str, str]],
    *,
    latched: set[str],
    sent_at: dict[str, float],
    now_ts: float,
    cooldown: float,
    max_n: int = 2,
) -> tuple[list[tuple[str, str, str]], set[str]]:
    """
    Apply edge latch + per-key cooldown + priority cap for one refresh round.

    Level toasts (band/wl) fire once while the condition stays true; they may
    fire again only after the condition clears. Decision toasts use cooldown.
    """
    active_level = {k for k, _, _ in alerts if is_level_toast(k)}
    next_latched = {k for k in latched if k in active_level}
    ranked = sorted(alerts, key=lambda row: (toast_priority(row[0]), row[0]))
    chosen: list[tuple[str, str, str]] = []
    for key, title, body in ranked:
        if len(chosen) >= max_n:
            break
        cd = cooldown_for_key(key, cooldown)
        if is_level_toast(key):
            if key in next_latched:
                continue
            last = sent_at.get(key)
            if last is not None and now_ts - last < cd:
                continue
            chosen.append((key, title, body))
            next_latched.add(key)
            continue
        last = sent_at.get(key)
        if last is not None and now_ts - last < cd:
            continue
        chosen.append((key, title, body))
    return chosen, next_latched


def is_serverchan_alert(key: str) -> bool:
    """Return True for buy/sell/lhb/eod/morning/ops alerts that may go to ServerChan.

    Health/degraded banners stay on-page only — ``ops:health:`` is never WeChat-pushed.
    """
    k = str(key or "")
    if k.startswith("ops:health:"):
        return False
    return k.startswith(("buy:", "fly:", "sell:", "lhb:", "eod:", "morning:", "ops:"))


def is_serverchan_must_sell(key: str) -> bool:
    """Return True for stop/must-sell style WeChat alerts (quiet sell-only mode)."""
    k = str(key or "")
    if k.startswith(("lhb:", "eod:", "ops:")):
        return True
    if k.startswith(("band:stop:", "wl:stop:")):
        return True
    # sell:stop:CODE — keep; trim/take muted in sell-only mode.
    if k.startswith("sell:stop:"):
        return True
    return False


def filter_serverchan_alerts(
    alerts: list[tuple[str, str, str]],
    *,
    sell_only: bool = False,
) -> list[tuple[str, str, str]]:
    """Keep buy / sell / lhb / eod advice for WeChat push.

    When ``sell_only`` is True, drop buy/fly/trim/take and keep stop + eod/lhb/ops.
    """
    out = [(k, t, b) for k, t, b in alerts if is_serverchan_alert(k)]
    if sell_only:
        out = [(k, t, b) for k, t, b in out if is_serverchan_must_sell(k)]
    return out


def format_serverchan_desp(
    key: str,
    title: str,
    body: str,
    current: dict[str, Any] | None = None,
) -> str:
    """Build markdown body for ServerChan (buy/fly/sell share one footer template).

    EOD / morning pushes already embed phase / mainline / action in the body, so
    the footer stays a short trade-date line to avoid repeating the same facts.
    """
    cur = current or {}
    key_s = str(key or "")
    body_md = str(body or "").replace("\n", "\n\n")
    if key_s.startswith("eod:") or key_s.startswith("morning:"):
        return "\n".join(
            [
                f"**{title}**",
                "",
                body_md,
                "",
                "---",
                f"_交易日 {cur.get('trade_date') or '—'} · market-desk_",
            ]
        )
    v = cur.get("verdict") or {}
    ml = (v.get("mainline") or {}).get("name") or cur.get("mainline") or "—"
    phase = cur.get("phase") or "—"
    life = (v.get("mainline") or {}).get("lifecycle") or ""
    life_zh = {"starting": "萌芽", "ongoing": "主升", "ending": "衰退"}.get(
        str(life), str(life) or "—"
    )
    action = v.get("action") or "—"
    # Unified action tag for buy / fly / sell so WeChat cards scan the same way.
    if key_s.startswith("fly:"):
        kind_zh = "将飞·半仓"
    elif key_s.startswith("buy:"):
        kind_zh = "可买"
    elif key_s.startswith("sell:"):
        kind_zh = "建议卖出"
    else:
        kind_zh = "提醒"
    lines = [
        f"**{title}**",
        "",
        f"> {kind_zh}",
        "",
        body_md,
        "",
        "---",
        f"- 作战结论：{action}",
        f"- 主线：{ml}",
        f"- 相位：{phase}",
        f"- 阶段：{life_zh}",
        f"- 交易日：{cur.get('trade_date') or '—'}",
    ]
    if key_s.startswith("buy:") or key_s.startswith("fly:"):
        rec = v.get("recommend") or {}
        primary = rec.get("primary") or {}
        why = primary.get("reason") or rec.get("text") or ""
        if why:
            lines.extend(["", f"> {str(why)[:200]}"])
    return "\n".join(lines)


def notify_serverchan(sendkey: str, title: str, desp: str) -> bool:
    """POST one message to Server酱³. Return True on HTTP success."""
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    key = str(sendkey or "").strip()
    if not key:
        return False
    url = f"https://sctapi.ftqq.com/{urllib.parse.quote(key)}.send"
    payload = urllib.parse.urlencode(
        {
            "title": str(title or "作战台")[:100],
            "desp": str(desp or "")[:4000],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
            # SCT returns {code:0,...} on success
            if isinstance(data, dict) and data.get("code") not in (0, "0", None):
                log.warning("serverchan reject: %s", data.get("message") or raw[:120])
                return False
        except json.JSONDecodeError:
            pass
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("serverchan failed: %s", exc)
        return False


def _daily_once_key(alert_key: str) -> str:
    """Return the per-day dedupe id for buy/fly WeChat alerts, or ``""``."""
    k = str(alert_key or "")
    if k.startswith(("buy:", "fly:")):
        return k
    return ""


def drop_daily_repeats(
    alerts: list[tuple[str, str, str]],
    *,
    trade_date: str,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Drop buy/fly alerts already pushed to WeChat on ``trade_date``.

    Card flicker re-fires the page toast edge; WeChat only needs one ping per
    code per day. Returns ``(kept, newly_marked_keys)``.
    """
    day = str(trade_date or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    try:
        from market_desk.db import load_setting

        sent = set(load_setting(f"sc_daily_once:{day}") or [])
    except Exception:
        sent = set()
    kept: list[tuple[str, str, str]] = []
    marked: list[str] = []
    for key, title, body in alerts:
        once = _daily_once_key(key)
        if once:
            if once in sent or once in marked:
                continue
            marked.append(once)
        kept.append((key, title, body))
    return kept, marked


def _mark_daily_once(trade_date: str, keys: list[str]) -> None:
    """Persist buy/fly keys pushed today so restarts do not re-ping."""
    if not keys:
        return
    day = str(trade_date or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    try:
        from market_desk.db import load_setting, save_setting

        store = f"sc_daily_once:{day}"
        cur = list(load_setting(store) or [])
        for k in keys:
            if k not in cur:
                cur.append(k)
        save_setting(store, cur)
    except Exception:
        log.exception("mark serverchan daily-once failed")


def push_serverchan_alerts(
    alerts: list[tuple[str, str, str]],
    current: dict[str, Any] | None = None,
) -> int:
    """Fan-out buy/sell alerts to every user with ServerChan enabled.

    No SendKey / not allowed / off → skip. Returns number of successful POSTs.
    Per-user ``serverchan_sell_only`` mutes buy/fly/soft-sell pushes.
    Buy / fly alerts go out at most once per code per trade day.
    """
    trade_date = str((current or {}).get("trade_date") or "")[:10]
    alerts, once_keys = drop_daily_repeats(alerts, trade_date=trade_date)
    if not alerts:
        return 0
    try:
        from market_desk.db import list_serverchan_recipients, load_user_setting

        recipients = list_serverchan_recipients()
    except Exception:
        log.exception("list serverchan recipients failed")
        return 0
    if not recipients:
        return 0
    _mark_daily_once(trade_date, once_keys)
    ok_n = 0
    for user in recipients:
        key = str(user.get("serverchan_sendkey") or "").strip()
        if not key:
            continue
        sell_only = False
        try:
            uid = int(user.get("id") or 0)
            raw = load_user_setting(uid, "runtime") if uid else None
            if isinstance(raw, dict):
                sell_only = bool(raw.get("serverchan_sell_only"))
        except Exception:
            sell_only = False
        sc = filter_serverchan_alerts(alerts, sell_only=sell_only)
        if not sc:
            continue
        for alert_key, title, body in sc:
            desp = format_serverchan_desp(alert_key, title, body, current)
            if notify_serverchan(key, title, desp):
                ok_n += 1
                log.info(
                    "serverchan -> user %s | %s",
                    user.get("username") or user.get("id"),
                    title,
                )
    return ok_n


def push_named_serverchan(
    alerts: list[tuple[str, str, str]],
    current: dict[str, Any] | None = None,
) -> int:
    """Push arbitrary ServerChan-eligible alerts (same recipient fan-out)."""
    return push_serverchan_alerts(alerts, current)


def lhb_seat_edge_alerts(
    items: list[dict[str, Any]] | None,
    *,
    prev_fp: dict[str, str] | None = None,
) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
    """Diff LHB seat_risk fingerprints; return (alerts, next_fp).

    First seed (empty prev) only stores fingerprints — no push.
    Alerts cover worsen and improve (and same-rank flag structure changes),
    each with an explicit Chinese reason.
    """
    from market_desk.lhb import explain_seat_risk_change

    prev = dict(prev_fp or {})
    next_fp: dict[str, str] = {}
    alerts: list[tuple[str, str, str]] = []
    seeded = not prev
    for it in items or []:
        if not it or not it.get("on_list"):
            continue
        code = str(it.get("code") or "").zfill(6)
        if len(code) != 6:
            continue
        summary = it.get("summary") if isinstance(it.get("summary"), dict) else {}
        risk = str(summary.get("seat_risk") or "ok")
        flags = sorted(str(x) for x in (summary.get("risk_flags") or []) if x)
        sig = f"{it.get('trade_date') or ''}|{risk}|{','.join(flags)}"
        next_fp[code] = sig
        if seeded:
            continue
        if prev.get(code) == sig:
            continue
        prev_raw = str(prev.get(code) or "")
        parts = prev_raw.split("|")
        prev_risk = parts[1] if len(parts) > 1 else "ok"
        prev_flags = [x for x in (parts[2].split(",") if len(parts) > 2 else []) if x]
        change = explain_seat_risk_change(
            prev_risk=prev_risk,
            prev_flags=prev_flags,
            risk=risk,
            risk_flags=flags,
            hints=list(summary.get("hints") or []),
            list_reason=str(it.get("reason") or ""),
        )
        if change.get("direction") in (None, "none"):
            continue
        name = it.get("name") or code
        reason = str(change.get("reason") or "").strip()
        body = f"{name} {code}"
        if reason:
            body = f"{body} · {reason}"
        direction = str(change.get("direction") or "worsen")
        alerts.append(
            (
                f"lhb:{direction}:{code}",
                str(change.get("title") or "龙虎席位变化"),
                body,
            )
        )
    return alerts[:4], next_fp
