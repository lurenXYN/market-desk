"""Unify sell-advice semantics into execution trigger intents (vnpy Stop-like).

Decision layer still owns *whether* to sell; this module only maps a sell item
into when/how the execution layer may act — so paper, future auto, and UI share
one vocabulary (immediate / price_stop / deferred / blocked).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class TriggerMode(str, Enum):
    """When the execution layer may send a sell."""

    IMMEDIATE = "immediate"  # hard stop / must-clear — fire now
    PRICE_STOP = "price_stop"  # sell when last <= trigger (protective)
    PRICE_LIMIT = "price_limit"  # sell when last <= trigger (soft take/trim)
    DEFERRED = "deferred"  # open-buffer watching / armed but not released
    BLOCKED = "blocked"  # T+1 / pre-match / not ready / soft demoted


@dataclass
class ExecTrigger:
    """Normalized sell intent for the execution layer."""

    mode: TriggerMode
    code: str = ""
    name: str = ""
    exit_mode: str = "hold"  # clear / half / hold
    urgency: str = "hold"
    trigger_price: float | None = None
    last_price: float | None = None
    executable: bool = False
    reason: str = ""
    open_buffer_track: str = ""
    open_buffer_phase: str = ""
    reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for ops / API."""
        d = asdict(self)
        d["mode"] = self.mode.value
        return d


def _f(v: Any) -> float | None:
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def sell_item_to_trigger(item: dict[str, Any] | None) -> ExecTrigger:
    """Map one sell-advice / position-hint row into an ``ExecTrigger``.

    Precedence (high → low):
      1. T+1 / pre-match → blocked
      2. open_buffer must / urgency=stop / ready clear → immediate
      3. open_buffer watch (watching/armed) → deferred
      4. ready half/trim with trigger_price → price_limit
      5. hold with stop_price → price_stop (protective watch, not ready)
      6. else blocked / not ready
    """
    it = dict(item or {})
    code = str(it.get("code") or "").zfill(6) if it.get("code") else ""
    name = str(it.get("name") or "")
    exit_mode = str(it.get("exit_mode") or "hold").lower()
    urgency = str(it.get("urgency") or "hold").lower()
    track = str(it.get("open_buffer_track") or "").lower()
    phase = str(it.get("open_buffer_phase") or "").lower()
    trigger_px = _f(it.get("trigger_price") if it.get("trigger_price") is not None else it.get("sell_price"))
    stop_px = _f(it.get("stop_price"))
    last_px = _f(it.get("last") if it.get("last") is not None else it.get("price"))
    ref = str(it.get("id") or it.get("reference") or "")
    base = dict(
        code=code,
        name=name,
        exit_mode=exit_mode,
        urgency=urgency,
        last_price=last_px,
        open_buffer_track=track,
        open_buffer_phase=phase,
        reference=ref,
    )

    if it.get("t1_locked"):
        return ExecTrigger(
            mode=TriggerMode.BLOCKED,
            trigger_price=trigger_px or stop_px,
            executable=False,
            reason="t1_locked",
            **base,
        )
    if it.get("pre_match_hold"):
        return ExecTrigger(
            mode=TriggerMode.BLOCKED,
            trigger_price=trigger_px or stop_px,
            executable=False,
            reason="pre_match",
            **base,
        )
    if it.get("buy_conflict_hold"):
        return ExecTrigger(
            mode=TriggerMode.BLOCKED,
            trigger_price=stop_px,
            executable=False,
            reason="soft_demoted_buy_conflict",
            **base,
        )

    # Hard path: must-sell / stop urgency / ready clear → immediate when ready
    ready = bool(it.get("ready"))
    if track == "must" or urgency == "stop" or (ready and exit_mode == "clear"):
        px = trigger_px or stop_px
        reason = (
            "hard_stop"
            if urgency == "stop"
            else ("must_clear" if exit_mode == "clear" else "must")
        )
        return ExecTrigger(
            mode=TriggerMode.IMMEDIATE,
            trigger_price=px,
            # Fire only when advice is ready, or open-buffer stamped must.
            executable=ready or track == "must",
            reason=reason,
            **base,
        )

    # Soft open-buffer still watching
    if track == "watch" and phase in ("watching", "armed", ""):
        if phase == "released" and bool(it.get("ready")):
            return ExecTrigger(
                mode=TriggerMode.PRICE_LIMIT,
                trigger_price=trigger_px,
                executable=True,
                reason="open_buffer_released",
                **base,
            )
        return ExecTrigger(
            mode=TriggerMode.DEFERRED,
            trigger_price=trigger_px,
            executable=False,
            reason=f"open_buffer_{phase or 'watching'}",
            **base,
        )
    if it.get("open_buffer_pending") and not it.get("ready"):
        return ExecTrigger(
            mode=TriggerMode.DEFERRED,
            trigger_price=trigger_px,
            executable=False,
            reason="open_buffer_pending",
            **base,
        )

    if bool(it.get("ready")) and exit_mode in ("half", "clear"):
        return ExecTrigger(
            mode=TriggerMode.PRICE_LIMIT if exit_mode == "half" else TriggerMode.IMMEDIATE,
            trigger_price=trigger_px,
            executable=True,
            reason="ready_" + exit_mode,
            **base,
        )

    if stop_px is not None and exit_mode == "hold":
        breached = last_px is not None and last_px <= stop_px + 1e-9
        return ExecTrigger(
            mode=TriggerMode.PRICE_STOP,
            trigger_price=stop_px,
            executable=False,  # protective watch only until sell advice arms ready
            reason="stop_watch_breached" if breached else "stop_watch",
            **base,
        )

    return ExecTrigger(
        mode=TriggerMode.BLOCKED,
        trigger_price=trigger_px or stop_px,
        executable=False,
        reason="not_ready",
        **base,
    )


def sell_advice_to_triggers(advice: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Map ``all_items`` / ``items`` of a sell-advice payload to trigger dicts."""
    adv = advice or {}
    rows = adv.get("all_items") or adv.get("items") or []
    return [sell_item_to_trigger(r).to_dict() for r in rows if isinstance(r, dict)]
