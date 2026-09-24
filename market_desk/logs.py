"""Rotating file log under ``data/logs`` plus a tail reader for the admin UI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from market_desk.config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "market-desk.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUPS = 5
_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MARK = "_market_desk_file_log"


def setup_file_logging() -> Path:
    """Attach the rotating file handler once; safe to call repeatedly.

    Hooks both ``market_desk`` (app logs) and ``uvicorn`` (request errors and
    unhandled tracebacks). Under a bare ``uvicorn`` launch the root logger has
    no handler, so a console handler is added too to keep journald useful.

    Returns:
        Path of the active log file.
    """
    targets = [logging.getLogger("market_desk"), logging.getLogger("uvicorn")]
    if any(getattr(h, _MARK, False) for h in targets[0].handlers):
        return LOG_FILE
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_FMT))
    setattr(handler, _MARK, True)
    for lg in targets:
        lg.addHandler(handler)
    app_log = targets[0]
    if app_log.level == logging.NOTSET or app_log.level > logging.INFO:
        app_log.setLevel(logging.INFO)
    if not logging.getLogger().handlers:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FMT))
        app_log.addHandler(console)
    return LOG_FILE


def tail_log(lines: int = 200, query: str = "", *, level: str = "") -> list[str]:
    """Return the last ``lines`` log lines, optionally filtered.

    Args:
        lines: Maximum number of lines to return (clamped to 1–2000).
        query: Case-insensitive substring every returned line must contain.
        level: Minimum level to keep: ``""`` (all), ``"warning"`` or ``"error"``.
            Traceback continuation lines stay attached to their header line.

    Returns:
        Matching lines, oldest first.
    """
    n = max(1, min(int(lines or 200), 2000))
    if not LOG_FILE.exists():
        return []
    budget = 4_000_000 if (query or level) else 600_000
    with LOG_FILE.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - budget))
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace").splitlines()
    if size > budget and text:
        text = text[1:]
    keep_levels = {
        "warning": ("WARNING", "ERROR", "CRITICAL"),
        "error": ("ERROR", "CRITICAL"),
    }.get(str(level or "").lower())
    q = str(query or "").strip().lower()
    out: list[str] = []
    keep_block = False
    for line in text:
        is_header = len(line) > 24 and line[:4].isdigit() and line[4] == "-"
        if is_header:
            ok = True
            if keep_levels is not None:
                parts = line.split(" ", 3)
                ok = len(parts) > 2 and parts[2] in keep_levels
            if ok and q:
                ok = q in line.lower()
            keep_block = ok
            if ok:
                out.append(line)
        elif keep_block:
            out.append(line)
    return out[-n:]
