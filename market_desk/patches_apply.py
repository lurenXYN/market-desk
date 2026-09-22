"""Apply one-shot daily_snapshot patches shipped under ``market_desk/patches/``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from market_desk.db import init_db, load_setting, save_daily, save_setting

log = logging.getLogger("market_desk.patches")

_PATCH_DIR = Path(__file__).resolve().parent / "patches"
_APPLIED_KEY = "applied_daily_patches"


def apply_pending_daily_patches(*, force: bool = False) -> list[str]:
    """Apply JSON daily patches. Return applied ids.

    Patches replace the day's row (merge=False) so zeroed online breadth cannot
    block a known-good local restore. ``force=True`` re-applies even if marked.
    """
    init_db()
    if not _PATCH_DIR.is_dir():
        log.warning("patch dir missing: %s", _PATCH_DIR)
        return []
    applied_raw = load_setting(_APPLIED_KEY) or []
    applied: set[str] = set()
    if isinstance(applied_raw, list):
        applied = {str(x) for x in applied_raw}
    elif isinstance(applied_raw, dict):
        applied = {str(k) for k, v in applied_raw.items() if v}

    done: list[str] = []
    paths = sorted(_PATCH_DIR.glob("daily-*.json"))
    log.info("daily patches found=%s dir=%s force=%s", len(paths), _PATCH_DIR, force)
    for path in paths:
        patch_id = path.stem
        if not force and patch_id in applied:
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
        # Full replace: do not merge with the corrupted online stub.
        save_daily(day, payload, merge=False)
        applied.add(patch_id)
        done.append(patch_id)
        log.info(
            "applied daily patch %s -> %s ups=%s downs=%s amt=%s",
            patch_id,
            day,
            payload.get("ups"),
            payload.get("downs"),
            payload.get("amount_yi"),
        )

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
