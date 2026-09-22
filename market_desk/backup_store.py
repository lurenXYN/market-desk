"""Persist JSON / SQLite backups under data/backup on demand or after the close."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from market_desk.config import DATA_DIR
from market_desk.db import export_backup_payload

BACKUP_DIR = DATA_DIR / "backup"
DB_PATH = DATA_DIR / "desk.db"


def write_auto_backup(
    *,
    trade_date: str | None = None,
    keep: int = 30,
    copy_db: bool = True,
) -> Path:
    """Write one dated JSON backup (and optional SQLite copy); prune older files."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    day = (trade_date or datetime.now().strftime("%Y-%m-%d")).replace("/", "-")
    stamp = datetime.now().strftime("%H%M%S")
    path = BACKUP_DIR / f"auto-{day}-{stamp}.json"
    payload = export_backup_payload()
    payload["auto"] = True
    payload["trade_date"] = day
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if copy_db and DB_PATH.exists():
        db_dest = BACKUP_DIR / f"desk-{day}-{stamp}.db"
        try:
            shutil.copy2(DB_PATH, db_dest)
        except OSError:
            pass
    lim = max(5, min(90, int(keep or 30)))
    _prune_backups(limit=lim)
    return path


def _prune_backups(*, limit: int = 30) -> None:
    """Keep only the newest ``limit`` auto JSON and desk-*.db copies each."""
    for pattern in ("auto-*.json", "desk-*.db"):
        files = sorted(
            BACKUP_DIR.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[limit:]:
            try:
                old.unlink()
            except OSError:
                pass


def list_auto_backups(limit: int = 10) -> list[dict[str, Any]]:
    """Return recent auto backup file metadata for the UI."""
    if not BACKUP_DIR.exists():
        return []
    files = sorted(
        list(BACKUP_DIR.glob("auto-*.json")) + list(BACKUP_DIR.glob("desk-*.db")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for path in files[: max(1, min(40, int(limit or 10)))]:
        out.append(
            {
                "name": path.name,
                "kind": "db" if path.suffix == ".db" else "json",
                "path": str(path),
                "size": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
    return out


def resolve_backup_file(name: str) -> Path | None:
    """Resolve a safe backup filename under ``data/backup`` (no path traversal)."""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw or "/" in raw or ".." in raw:
        return None
    if not (raw.startswith("auto-") or raw.startswith("desk-")):
        return None
    if not (raw.endswith(".json") or raw.endswith(".db")):
        return None
    path = (BACKUP_DIR / raw).resolve()
    try:
        path.relative_to(BACKUP_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def check_db_integrity() -> dict[str, Any]:
    """Run SQLite ``PRAGMA integrity_check`` on the live desk database."""
    import sqlite3

    if not DB_PATH.exists():
        return {"ok": True, "skipped": True, "detail": "no desk.db yet"}
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}
    msg = str(row[0]) if row else "unknown"
    ok = msg.strip().lower() == "ok"
    return {
        "ok": ok,
        "detail": msg if ok else msg[:500],
        "path": str(DB_PATH),
    }
