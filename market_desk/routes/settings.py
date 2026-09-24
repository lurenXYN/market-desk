"""Runtime settings read / write / presets / import."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from market_desk.auth import is_guest
from market_desk.deps import current_member_required, current_user_required
from market_desk.settings import get_settings_for_user, update_settings

router = APIRouter()


class SettingsIn(BaseModel):
    """Partial runtime settings patch from the UI panel."""

    refresh_seconds: int | None = None
    idle_seconds: int | None = None
    auction_refresh_seconds: int | None = None
    sticky_margin: float | None = None
    switch_min_seconds: int | None = None
    toast_enabled: bool | None = None
    toast_cooldown: int | None = None
    decision_alerts: bool | None = None
    alert_mode: str | None = None
    open_mute_minutes: int | None = None
    tail_mute_minutes: int | None = None
    daily_loss_cap_pct: float | None = None
    cool_after_losses: int | None = None
    target_total_cost: float | None = None
    equal_weight_target: bool | None = None
    batch_plan: bool | None = None
    auto_backup: bool | None = None
    backup_keep: int | None = None
    account_equity: float | None = None
    risk_pct_per_trade: float | None = None
    min_stock_mv_yi: float | None = None
    # Review hit-rate: traded (executed only) | all (paper signals too).
    hit_rate_mode: str | None = None
    # Review outcome standard: classic | same_day_plan.
    outcome_standard: str | None = None
    adapt_follow_outcome: bool | None = None
    # Ready style: strict | band.
    ready_style: str | None = None
    # Soft-sell open buffer minutes after 09:30.
    sell_open_watch_minutes: int | None = None
    morning_push: bool | None = None
    news_radar_enabled: bool | None = None
    news_radar_url: str | None = None
    phase_panic_temp: int | None = None
    phase_ferment_temp: int | None = None
    phase_climax_temp: int | None = None


@router.get("/api/settings")
def read_settings(user: dict = Depends(current_user_required)) -> dict:
    """Return runtime settings (merged with user private knobs when allowed)."""
    uid = None if is_guest(user) else int(user["id"])
    from market_desk.settings import SETTINGS_PRESETS, export_portable_settings

    settings = get_settings_for_user(uid)
    return {
        "ok": True,
        "settings": settings,
        "portable": export_portable_settings(settings),
        "presets": {k: dict(v) for k, v in SETTINGS_PRESETS.items()},
        "auth_user": user,
    }


@router.post("/api/settings/preset")
def apply_settings_preset(
    name: str = Query(..., min_length=1),
    user: dict = Depends(current_member_required),
) -> dict:
    """Apply defensive / balanced / aggressive personal risk preset."""
    from market_desk.settings import preset_patch

    patch = preset_patch(name)
    if not patch:
        raise HTTPException(400, "unknown preset (defensive|balanced|aggressive)")
    return {
        "ok": True,
        "preset": name,
        "settings": update_settings(patch, user_id=int(user["id"])),
        "auth_user": user,
    }


@router.post("/api/settings/import")
def import_settings_payload(
    body: dict[str, Any],
    user: dict = Depends(current_member_required),
) -> dict:
    """Import a portable settings JSON (only known portable keys)."""
    from market_desk.settings import USER_PRIVATE_KEYS, filter_portable_patch

    raw = body.get("settings") if isinstance(body.get("settings"), dict) else body
    patch = filter_portable_patch(raw if isinstance(raw, dict) else {})
    if not patch:
        raise HTTPException(400, "no portable settings in payload")
    if any(k not in USER_PRIVATE_KEYS for k in patch) and user.get("role") != "admin":
        patch = {k: v for k, v in patch.items() if k in USER_PRIVATE_KEYS}
        if not patch:
            raise HTTPException(403, "全局参数仅管理员可导入")
    return {
        "ok": True,
        "settings": update_settings(patch, user_id=int(user["id"])),
        "imported": sorted(patch.keys()),
        "auth_user": user,
    }


@router.post("/api/settings")
def write_settings(
    body: SettingsIn,
    user: dict = Depends(current_member_required),
) -> dict:
    """Patch settings: private keys per user; global keys require admin."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    from market_desk.settings import USER_PRIVATE_KEYS

    global_keys = [k for k in patch if k not in USER_PRIVATE_KEYS]
    if global_keys and user.get("role") != "admin":
        raise HTTPException(403, "全局参数仅管理员可改：" + "、".join(global_keys[:6]))
    return {
        "ok": True,
        "settings": update_settings(patch, user_id=int(user["id"])),
        "auth_user": user,
    }
