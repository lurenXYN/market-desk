"""Apply one-shot daily_snapshot patches shipped under ``market_desk/patches/``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from market_desk.db import load_setting, save_daily, save_setting

log = logging.getLogger("market_desk.patches")

_PATCH_DIR = Path(__file__).resolve().parent / "patches"
_APPLIED_KEY = "applied_daily_patches"


def apply_pending_daily_patches() -> list[str]:
    """Apply JSON daily patches that have not been recorded yet. Return applied ids."""
    if not _PATCH_DIR.is_dir():
        return []
    applied_raw = load_setting(_APPLIED_KEY, []) or []
    applied: set[str] = set()
    if isinstance(applied_raw, list):
        applied = {str(x) for x in applied_raw}
    elif isinstance(applied_raw, dict):
        applied = {str(k) for k, v in applied_raw.items() if v}

    done: list[str] = []
    for path in sorted(_PATCH_DIR.glob("daily-*.json")):
        patch_id = path.stem
        if patch_id in applied:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.warning("skip patch %s: %s", path.name, exc)
            continue
        day, payload = _normalize_patch(raw)
        if not day or not payload:
            log.warning("skip patch %s: missing trade_date/payload", path.name)
            continue
        # Force-write local good breadth over online zero stubs.
        save_daily(day, payload, merge=True)
        applied.add(patch_id)
        done.append(patch_id)
        log.info("applied daily patch %s -> %s", patch_id, day)

    if done:
        save_setting(_APPLIED_KEY, sorted(applied))
    return done


def _normalize_patch(raw: Any) -> tuple[str, dict[str, Any]]:
    """Extract ``(trade_date, payload)`` from a patch file object."""
    if not isinstance(raw, dict):
        return "", {}
    day = str(raw.get("trade_date") or "").strip()[:10]
    payload = raw.get("payload")
    if isinstance(payload, dict) and day:
        return day, dict(payload)
    # Allow flat files that are themselves the daily payload.
    if day and raw.get("ups") is not None:
        body = {k: v for k, v in raw.items() if k not in ("trade_date", "note")}
        return day, body
    return "", {}
