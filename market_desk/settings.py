"""Runtime settings persisted in SQLite for the desk UI panel."""

from __future__ import annotations

from typing import Any

from market_desk import config as cfg
from market_desk.db import load_setting, save_setting

_SETTINGS_KEY = "runtime"
_CACHE: dict[str, Any] | None = None

DEFAULTS: dict[str, Any] = {
    "refresh_seconds": int(cfg.SESSION_REFRESH_SECONDS),
    "idle_seconds": int(cfg.IDLE_CHECK_SECONDS),
    "sticky_margin": float(cfg.MAINLINE_STICKY_MARGIN),
    "switch_min_seconds": int(cfg.MAINLINE_SWITCH_MIN_SECONDS),
    "toast_enabled": bool(cfg.TOAST_ENABLED),
    "toast_cooldown": int(cfg.TOAST_COOLDOWN_SECONDS),
    # all | traded_watch | watch_only | off
    "alert_mode": "all",
    "daily_loss_cap_pct": -3.0,
    "cool_after_losses": 3,
    "target_total_cost": float(cfg.POSITION_MAX_TOTAL_COST),
    "equal_weight_target": True,
    "batch_plan": True,
    "auto_backup": True,
}


def get_settings(*, refresh: bool = False) -> dict[str, Any]:
    """Return merged runtime settings (defaults + saved overrides)."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return dict(_CACHE)
    raw = load_setting(_SETTINGS_KEY) or {}
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in DEFAULTS:
                out[key] = value
    out = _normalize(out)
    _CACHE = out
    return dict(out)


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial settings patch and persist it."""
    global _CACHE
    cur = get_settings(refresh=True)
    for key, value in (patch or {}).items():
        if key in DEFAULTS and value is not None:
            cur[key] = value
    cur = _normalize(cur)
    save_setting(_SETTINGS_KEY, cur)
    _CACHE = cur
    return dict(cur)


def setting(key: str, default: Any = None) -> Any:
    """Return one runtime setting value."""
    vals = get_settings()
    if key in vals:
        return vals[key]
    return default


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Clamp and coerce settings into safe ranges."""
    out = dict(DEFAULTS)
    out.update(raw or {})
    out["refresh_seconds"] = max(10, min(120, int(out["refresh_seconds"])))
    out["idle_seconds"] = max(30, min(600, int(out["idle_seconds"])))
    out["sticky_margin"] = max(0.0, min(40.0, float(out["sticky_margin"])))
    out["switch_min_seconds"] = max(30, min(900, int(out["switch_min_seconds"])))
    out["toast_enabled"] = bool(out["toast_enabled"])
    out["toast_cooldown"] = max(30, min(900, int(out["toast_cooldown"])))
    mode = str(out.get("alert_mode") or "all").strip().lower()
    if mode not in ("all", "traded_watch", "watch_only", "off"):
        mode = "all"
    out["alert_mode"] = mode
    out["daily_loss_cap_pct"] = max(-20.0, min(0.0, float(out["daily_loss_cap_pct"])))
    out["cool_after_losses"] = max(1, min(10, int(out["cool_after_losses"])))
    out["target_total_cost"] = max(1000.0, min(5_000_000.0, float(out["target_total_cost"])))
    out["equal_weight_target"] = bool(out["equal_weight_target"])
    out["batch_plan"] = bool(out["batch_plan"])
    out["auto_backup"] = bool(out["auto_backup"])
    return out
