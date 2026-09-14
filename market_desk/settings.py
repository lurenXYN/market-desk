"""Runtime settings persisted in SQLite for the desk UI panel."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from market_desk import config as cfg
from market_desk.db import load_setting, load_user_setting, save_setting, save_user_setting

_SETTINGS_KEY = "runtime"
_CACHE: dict[str, Any] | None = None
_USER_OVERLAY: ContextVar[dict[str, Any] | None] = ContextVar("user_settings", default=None)

# Per-user private knobs (risk / sizing / personal alerts).
USER_PRIVATE_KEYS = frozenset(
    {
        "account_equity",
        "risk_pct_per_trade",
        "daily_loss_cap_pct",
        "cool_after_losses",
        "target_total_cost",
        "equal_weight_target",
        "batch_plan",
        "alert_mode",
        "toast_enabled",
        "toast_cooldown",
        "decision_alerts",
        "hit_rate_mode",
    }
)

DEFAULTS: dict[str, Any] = {
    "refresh_seconds": int(cfg.SESSION_REFRESH_SECONDS),
    "idle_seconds": int(cfg.IDLE_CHECK_SECONDS),
    "sticky_margin": float(cfg.MAINLINE_STICKY_MARGIN),
    "switch_min_seconds": int(cfg.MAINLINE_SWITCH_MIN_SECONDS),
    "toast_enabled": bool(cfg.TOAST_ENABLED),
    "toast_cooldown": int(cfg.TOAST_COOLDOWN_SECONDS),
    # Decision / phase / mainline toasts (sell always follows toast pipeline).
    "decision_alerts": True,
    # all | traded_watch | watch_only | off  (price-band scope only)
    "alert_mode": "traded_watch",
    "daily_loss_cap_pct": -3.0,
    "cool_after_losses": 3,
    "target_total_cost": float(cfg.POSITION_MAX_TOTAL_COST),
    "equal_weight_target": True,
    "batch_plan": True,
    "auto_backup": True,
    "account_equity": 50000.0,
    "risk_pct_per_trade": 1.0,
    # Total market-cap floor (亿元) for stock recommend cards; 0 = off.
    "min_stock_mv_yi": float(cfg.MIN_STOCK_MV_YI),
    # Observation side branch: max main−side score gap to surface (0 = off).
    "side_mainline_gap": float(cfg.SIDE_MAINLINE_GAP),
    # Mute buy pricing / buy toasts for N minutes after 09:30; 0 = off.
    "open_mute_minutes": int(cfg.OPEN_MUTE_MINUTES),
    # Review hit-rate: traded (executed only) | all (paper signals too).
    "hit_rate_mode": "traded",
    # Phase temperature cutoffs (see classify_phase).
    "phase_panic_temp": int(cfg.PHASE_PANIC_TEMP),
    "phase_ferment_temp": int(cfg.PHASE_FERMENT_TEMP),
    "phase_climax_temp": int(cfg.PHASE_CLIMAX_TEMP),
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
        # One-shot bump when still on the previous factory defaults (8 / 180).
        # Custom values are left alone.
        migrated = False
        try:
            if float(raw.get("sticky_margin", -1)) == 8.0:
                out["sticky_margin"] = float(DEFAULTS["sticky_margin"])
                migrated = True
            if int(raw.get("switch_min_seconds", -1)) == 180:
                out["switch_min_seconds"] = int(DEFAULTS["switch_min_seconds"])
                migrated = True
        except (TypeError, ValueError):
            migrated = False
        if migrated:
            save_setting(_SETTINGS_KEY, _normalize(out))
    out = _normalize(out)
    _CACHE = out
    return dict(out)


def update_settings(patch: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    """Apply a partial settings patch.

    When ``user_id`` is set, only ``USER_PRIVATE_KEYS`` are written to that user;
    global keys still update the shared runtime row (caller should gate by admin).
    """
    global _CACHE
    cur = get_settings(refresh=True)
    personal_patch = {
        k: v for k, v in (patch or {}).items() if k in USER_PRIVATE_KEYS and v is not None
    }
    global_patch = {
        k: v
        for k, v in (patch or {}).items()
        if k in DEFAULTS and k not in USER_PRIVATE_KEYS and v is not None
    }
    if global_patch:
        for key, value in global_patch.items():
            cur[key] = value
        cur = _normalize(cur)
        # Persist global keys only (keep legacy personal values as server defaults).
        save_setting(_SETTINGS_KEY, cur)
        _CACHE = cur
    if user_id is not None and personal_patch:
        prev = load_user_setting(int(user_id), "runtime") or {}
        if not isinstance(prev, dict):
            prev = {}
        merged = dict(prev)
        merged.update(personal_patch)
        # Normalize via full merge then store private subset.
        probe = dict(cur)
        probe.update(merged)
        probe = _normalize(probe)
        store = {k: probe[k] for k in USER_PRIVATE_KEYS}
        save_user_setting(int(user_id), "runtime", store)
    return get_settings_for_user(user_id) if user_id is not None else get_settings(refresh=True)


def get_settings_for_user(user_id: int | None) -> dict[str, Any]:
    """Merge global runtime settings with one user's private overrides."""
    base = get_settings()
    if user_id is None:
        return base
    raw = load_user_setting(int(user_id), "runtime") or {}
    out = dict(base)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key in USER_PRIVATE_KEYS:
                out[key] = value
    return _normalize(out)


def use_user_settings(user_id: int | None):
    """Context manager that overlays one user's private settings onto ``setting()``."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        token = _USER_OVERLAY.set(get_settings_for_user(user_id) if user_id is not None else None)
        try:
            yield
        finally:
            _USER_OVERLAY.reset(token)

    return _ctx()


def setting(key: str, default: Any = None) -> Any:
    """Return one runtime setting value (honors active user overlay)."""
    overlay = _USER_OVERLAY.get()
    if isinstance(overlay, dict) and key in overlay:
        return overlay[key]
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
    out["decision_alerts"] = bool(out["decision_alerts"])
    mode = str(out.get("alert_mode") or "traded_watch").strip().lower()
    if mode not in ("all", "traded_watch", "watch_only", "off"):
        mode = "traded_watch"
    out["alert_mode"] = mode
    out["daily_loss_cap_pct"] = max(-20.0, min(0.0, float(out["daily_loss_cap_pct"])))
    out["cool_after_losses"] = max(1, min(10, int(out["cool_after_losses"])))
    out["target_total_cost"] = max(1000.0, min(5_000_000.0, float(out["target_total_cost"])))
    out["equal_weight_target"] = bool(out["equal_weight_target"])
    out["batch_plan"] = bool(out["batch_plan"])
    out["auto_backup"] = bool(out["auto_backup"])
    out["account_equity"] = max(1000.0, min(5_000_000.0, float(out["account_equity"])))
    out["risk_pct_per_trade"] = max(0.2, min(5.0, float(out["risk_pct_per_trade"])))
    out["min_stock_mv_yi"] = max(0.0, min(500.0, float(out["min_stock_mv_yi"])))
    out["side_mainline_gap"] = max(0.0, min(40.0, float(out.get("side_mainline_gap") or 0.0)))
    out["open_mute_minutes"] = max(0, min(30, int(out["open_mute_minutes"])))
    hit_mode = str(out.get("hit_rate_mode") or "traded").strip().lower()
    if hit_mode not in ("traded", "all"):
        hit_mode = "traded"
    out["hit_rate_mode"] = hit_mode
    panic_t = max(5, min(50, int(out.get("phase_panic_temp") or cfg.PHASE_PANIC_TEMP)))
    ferment_t = max(panic_t + 1, min(80, int(out.get("phase_ferment_temp") or cfg.PHASE_FERMENT_TEMP)))
    climax_t = max(ferment_t, min(95, int(out.get("phase_climax_temp") or cfg.PHASE_CLIMAX_TEMP)))
    out["phase_panic_temp"] = panic_t
    out["phase_ferment_temp"] = ferment_t
    out["phase_climax_temp"] = climax_t
    return out
