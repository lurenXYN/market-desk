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
            app_id="A股情绪作战台",
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
        alerts.append(
            (
                f"buy:{code or cur_ml}",
                "可买入",
                f"主线 {cur_ml or '—'} · {name} {code} 建议买 {px}".strip(),
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
        label = item.get("role_label") or "建议卖出"
        alerts.append(
            (
                f"sell:{key}",
                label,
                (
                    f"{item.get('name') or ''} {code} 建议卖 {item.get('sell_price')} "
                    f"浮盈 {item.get('pnl_pct')}%"
                ).strip(),
            )
        )
    return alerts


def toast_priority(key: str) -> int:
    """Return sort rank for one toast key (lower = more urgent)."""
    k = str(key or "")
    if k.startswith("sell:") or k.startswith("band:stop:") or k.startswith("wl:stop:"):
        return 0
    if k.startswith("buy:"):
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
    return k.startswith(("buy:", "exit:", "mainline:", "phase:"))


def is_risk_toast(key: str) -> bool:
    """Return True for sell / stop toasts that stay active in quiet windows."""
    k = str(key or "")
    return k.startswith("sell:") or k.startswith("band:stop:") or k.startswith("wl:stop:")


def cooldown_for_key(key: str, decision_cooldown: float) -> float:
    """Return cooldown seconds for one toast key."""
    k = str(key or "")
    if k.startswith(("sell:", "band:stop:", "wl:stop:")):
        return COOLDOWN_STOP_SEC
    if k.startswith(("band:entry:", "wl:suggest:")):
        return COOLDOWN_ENTRY_SEC
    if k.startswith(("band:chase:", "wl:chase:")):
        return COOLDOWN_CHASE_SEC
    return float(decision_cooldown)


def is_buy_quiet_window(
    now: datetime,
    *,
    trading_day: bool | None = None,
    open_mute_minutes: int = 5,
) -> bool:
    """
    Return True when buy/entry/mainline noise should be muted.

    Quiet during auction (09:15–09:30), configured open mute after 09:30,
    lunch, after close, and non-trading days.
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
    if 13 * 60 <= minutes <= 15 * 60:
        return False
    return True


def filter_alerts_for_policy(
    alerts: list[tuple[str, str, str]],
    *,
    decision_alerts: bool = True,
    quiet_buy: bool = False,
) -> list[tuple[str, str, str]]:
    """Apply decision-toggle and auction/off-session mute rules."""
    out: list[tuple[str, str, str]] = []
    for key, title, body in alerts:
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
