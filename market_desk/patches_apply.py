"""Apply one-shot daily_snapshot patches shipped under ``market_desk/patches/``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from market_desk.db import init_db, load_daily, load_setting, save_daily, save_setting

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
    applied_raw = load_setting(_APPLIED_KEY)
    if applied_raw is None:
        applied_raw = []
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
            log.info("skip already-applied %s", patch_id)
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
        save_daily(day, payload, merge=False)
        # Verify round-trip so silent failures cannot mark applied.
        rows = {str(r.get("trade_date")): r for r in load_daily(30)}
        got = rows.get(day) or {}
        if _has_nonzero_breadth(payload) and not _has_nonzero_breadth(got):
            raise RuntimeError(
                f"patch {patch_id} write verify failed for {day}: "
                f"ups={got.get('ups')} downs={got.get('downs')} amt={got.get('amount_yi')}"
            )
        applied.add(patch_id)
        done.append(patch_id)
        log.info(
            "applied daily patch %s -> %s ups=%s downs=%s amt=%s",
            patch_id,
            day,
            got.get("ups"),
            got.get("downs"),
            got.get("amount_yi"),
        )

    if done:
        save_setting(_APPLIED_KEY, sorted(applied))
    return done


def verify_day_breadth(trade_date: str) -> dict[str, Any]:
    """Return the stored daily row fields used to confirm a patch landed."""
    day = str(trade_date or "")[:10]
    for row in load_daily(40):
        if str(row.get("trade_date") or "")[:10] == day:
            return {
                "trade_date": day,
                "ups": row.get("ups"),
                "downs": row.get("downs"),
                "amount_yi": row.get("amount_yi"),
                "phase": row.get("phase"),
                "event": row.get("event"),
                "breadth_degraded": row.get("breadth_degraded"),
            }
    return {"trade_date": day, "missing": True}


def _has_nonzero_breadth(payload: dict[str, Any] | None) -> bool:
    """True when ups/downs/amount look populated."""
    data = payload or {}
    try:
        if int(data.get("ups") or 0) > 0 or int(data.get("downs") or 0) > 0:
            return True
        if float(data.get("amount_yi") or 0) > 0:
            return True
    except (TypeError, ValueError):
        return False
    return False


def _normalize_patch(raw: Any) -> tuple[str, dict[str, Any]]:
    """Extract ``(trade_date, payload)`` from a patch file object."""
    if not isinstance(raw, dict):
        return "", {}
    day = str(raw.get("trade_date") or "").strip()[:10]
    payload = raw.get("payload")
    if isinstance(payload, dict) and day:
        return day, dict(payload)
    if day and raw.get("ups") is not None:
        body = {k: v for k, v in raw.items() if k not in ("trade_date", "note")}
        return day, body
    return "", {}
