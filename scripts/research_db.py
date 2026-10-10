"""Pick the desk.db a research script reads: ``--db PATH`` or the local data dir.

Research scripts read the database read-only. Pass ``--db %TEMP%\\desk-vps.db``
(see ``scripts/pull-vps-db-research.ps1``) to analyse the VPS copy without
touching the local working database.
"""

from __future__ import annotations

import sys
from pathlib import Path


def research_db_path(default: Path) -> Path:
    """Return the database path from ``--db PATH`` / ``--db=PATH`` in argv, else ``default``.

    Args:
        default: Path used when the flag is absent.

    Returns:
        The selected path; raises ``SystemExit`` when the file does not exist.
    """
    argv = sys.argv[1:]
    chosen = default
    for i, arg in enumerate(argv):
        if arg == "--db" and i + 1 < len(argv):
            chosen = Path(argv[i + 1])
        elif arg.startswith("--db="):
            chosen = Path(arg[5:])
    if not chosen.exists():
        raise SystemExit(f"database not found: {chosen}")
    return chosen
