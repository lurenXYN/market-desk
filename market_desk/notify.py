"""Windows toast notifications for high-priority desk alerts."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("market_desk.notify")


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
