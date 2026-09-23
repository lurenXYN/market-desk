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
        "outcome_standard",
        "adapt_follow_outcome",
        "ready_style",
        "sell_open_watch_minutes",
        "open_mute_minutes",
        "tail_mute_minutes",
        "morning_push",
        "serverchan_sell_only",
    }
)

# Portable keys for export / import / presets (no secrets).
SETTINGS_PORTABLE_KEYS = frozenset(
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
        "open_mute_minutes",
        "tail_mute_minutes",
        "hit_rate_mode",
        "outcome_standard",
        "adapt_follow_outcome",
        "ready_style",
        "sell_open_watch_minutes",
        "morning_push",
        "serverchan_sell_only",
        "sticky_margin",
        "switch_min_seconds",
        "min_stock_mv_yi",
        "side_mainline_gap",
        "auto_backup",
        "backup_keep",
    }
)

# Risk/sizing profiles (user private knobs only).
SETTINGS_PRESETS: dict[str, dict[str, Any]] = {
    "defensive": {
        "risk_pct_per_trade": 0.6,
        "daily_loss_cap_pct": -2.0,
        "cool_after_losses": 2,
        "target_total_cost": 30000.0,
        "account_equity": 50000.0,
        "equal_weight_target": True,
        "batch_plan": True,
        "open_mute_minutes": 10,
        "tail_mute_minutes": 45,
        "alert_mode": "traded_watch",
        "decision_alerts": False,
        "ready_style": "strict",
    },
    "balanced": {
        "risk_pct_per_trade": 1.0,
        "daily_loss_cap_pct": -3.0,
        "cool_after_losses": 3,
        "target_total_cost": 50000.0,
        "account_equity": 50000.0,
        "equal_weight_target": True,
        "batch_plan": True,
        "open_mute_minutes": 5,
        "tail_mute_minutes": 30,
        "alert_mode": "traded_watch",
        "decision_alerts": True,
        "ready_style": "band",
    },
    "aggressive": {
        "risk_pct_per_trade": 1.8,
        "daily_loss_cap_pct": -5.0,
        "cool_after_losses": 4,
        "target_total_cost": 80000.0,
        "account_equity": 80000.0,
        "equal_weight_target": False,
        "batch_plan": True,
        "open_mute_minutes": 0,
        "tail_mute_minutes": 15,
        "alert_mode": "all",
        "decision_alerts": True,
        "ready_style": "band",
    },
}

DEFAULTS: dict[str, Any] = {
    "refresh_seconds": int(cfg.SESSION_REFRESH_SECONDS),
    "idle_seconds": int(cfg.IDLE_CHECK_SECONDS),
    # Call-auction window (09:15–09:30) poll cadence; 0 = use refresh_seconds.
    "auction_refresh_seconds": 5,
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
    # How many auto-*.json / desk-*.db copies to keep under data/backup.
    "backup_keep": 30,
    # Nightly MA stickiness→fan scan (observation only).
    "ma_fan_limit": 400,
    "ma_fan_top": 40,
    "ma_fan_min_amount_yi": 1.2,
    "ma_fan_boards": "all",
    "account_equity": 50000.0,
    "risk_pct_per_trade": 1.0,
    # Total market-cap floor (亿元) for stock recommend cards; 0 = off.
    "min_stock_mv_yi": float(cfg.MIN_STOCK_MV_YI),
    # Observation side branch: max main−side score gap to surface (0 = off).
    "side_mainline_gap": float(cfg.SIDE_MAINLINE_GAP),
    # Mute buy pricing / buy toasts for N minutes after 09:30; 0 = off.
    "open_mute_minutes": int(cfg.OPEN_MUTE_MINUTES),
    # Mute buy/decision toasts for N minutes before 15:00; 0 = off. Risk kept.
    "tail_mute_minutes": 30,
    # Review hit-rate: traded (executed only) | all (paper signals too).
    "hit_rate_mode": "traded",
    # Review outcome standard: classic | same_day_plan | filled (display overlay).
    "outcome_standard": "classic",
    # Soft adapt always uses persisted classic labels unless True (reserved; v1 still classic).
    "adapt_follow_outcome": False,
    # Ready style: strict | band (near_entry below chase → soft ready).
    "ready_style": str(cfg.READY_STYLE_DEFAULT),
    # Soft-sell open buffer minutes after 09:30 (must-sell ignores this).
    "sell_open_watch_minutes": int(cfg.SELL_OPEN_WATCH_MINUTES),
    # Per-user: push morning brief via Server酱 once near open (needs Key).
    "morning_push": False,
    # Per-user: Server酱 only stop/must-sell (+eod/lhb/ops); mute buy/fly/trim.
    "serverchan_sell_only": False,
    # Soft link to standalone news-radar (http://host:8770); empty = off.
    "news_radar_enabled": False,
    "news_radar_url": "http://127.0.0.1:8770",
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
    out["auction_refresh_seconds"] = max(0, min(30, int(out.get("auction_refresh_seconds") or 0)))
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
    out["backup_keep"] = max(5, min(90, int(out.get("backup_keep") or 30)))
    out["account_equity"] = max(1000.0, min(5_000_000.0, float(out["account_equity"])))
    out["risk_pct_per_trade"] = max(0.2, min(5.0, float(out["risk_pct_per_trade"])))
    out["min_stock_mv_yi"] = max(0.0, min(500.0, float(out["min_stock_mv_yi"])))
    out["side_mainline_gap"] = max(0.0, min(40.0, float(out.get("side_mainline_gap") or 0.0)))
    out["open_mute_minutes"] = max(0, min(30, int(out["open_mute_minutes"])))
    out["tail_mute_minutes"] = max(0, min(90, int(out.get("tail_mute_minutes") or 0)))
    hit_mode = str(out.get("hit_rate_mode") or "traded").strip().lower()
    if hit_mode not in ("traded", "all"):
        hit_mode = "traded"
    out["hit_rate_mode"] = hit_mode
    oc = str(out.get("outcome_standard") or "classic").strip().lower()
    if oc not in ("classic", "same_day_plan", "filled"):
        oc = "classic"
    out["outcome_standard"] = oc
    out["adapt_follow_outcome"] = bool(out.get("adapt_follow_outcome"))
    rs = str(out.get("ready_style") or cfg.READY_STYLE_DEFAULT).strip().lower()
    if rs not in ("strict", "band"):
        rs = str(cfg.READY_STYLE_DEFAULT)
    out["ready_style"] = rs
    sow = max(0, min(30, int(out.get("sell_open_watch_minutes") or cfg.SELL_OPEN_WATCH_MINUTES)))
    out["sell_open_watch_minutes"] = sow
    out["morning_push"] = bool(out.get("morning_push"))
    out["serverchan_sell_only"] = bool(out.get("serverchan_sell_only"))
    out["news_radar_enabled"] = bool(out.get("news_radar_enabled"))
    out["news_radar_url"] = str(out.get("news_radar_url") or "").strip().rstrip("/")
    panic_t = max(5, min(50, int(out.get("phase_panic_temp") or cfg.PHASE_PANIC_TEMP)))
    ferment_t = max(panic_t + 1, min(80, int(out.get("phase_ferment_temp") or cfg.PHASE_FERMENT_TEMP)))
    climax_t = max(ferment_t, min(95, int(out.get("phase_climax_temp") or cfg.PHASE_CLIMAX_TEMP)))
    out["phase_panic_temp"] = panic_t
    out["phase_ferment_temp"] = ferment_t
    out["phase_climax_temp"] = climax_t
    return out


def export_portable_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Return a JSON-safe subset of settings suitable for download / import."""
    src = settings or {}
    return {k: src[k] for k in sorted(SETTINGS_PORTABLE_KEYS) if k in src}


def preset_patch(name: str) -> dict[str, Any] | None:
    """Return a settings patch for defensive / balanced / aggressive."""
    key = str(name or "").strip().lower()
    aliases = {
        "防守": "defensive",
        "defense": "defensive",
        "平衡": "balanced",
        "进攻": "aggressive",
        "攻击": "aggressive",
    }
    key = aliases.get(key, key)
    patch = SETTINGS_PRESETS.get(key)
    return dict(patch) if patch else None


def filter_portable_patch(patch: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only portable keys from an import payload."""
    out: dict[str, Any] = {}
    for k, v in (patch or {}).items():
        if k in SETTINGS_PORTABLE_KEYS and v is not None:
            out[k] = v
    return out
