"""Persist JSON backups under data/backup on demand or after the close."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from market_desk.config import DATA_DIR
from market_desk.db import export_backup_payload

BACKUP_DIR = DATA_DIR / "backup"


def write_auto_backup(*, trade_date: str | None = None) -> Path:
    """Write one dated JSON backup file and prune older copies (keep 30)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    day = (trade_date or datetime.now().strftime("%Y-%m-%d")).replace("/", "-")
    stamp = datetime.now().strftime("%H%M%S")
    path = BACKUP_DIR / f"auto-{day}-{stamp}.json"
    payload = export_backup_payload()
    payload["auto"] = True
    payload["trade_date"] = day
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_backups(limit=30)
    return path


def _prune_backups(*, limit: int = 30) -> None:
    """Keep only the newest ``limit`` auto-*.json files."""
    files = sorted(BACKUP_DIR.glob("auto-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[limit:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_auto_backups(limit: int = 10) -> list[dict[str, Any]]:
    """Return recent auto backup file metadata for the UI."""
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("auto-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        out.append(
            {
                "name": path.name,
                "path": str(path),
                "size": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return out
