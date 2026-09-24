"""Serve the dashboard's split JS sources as one script.

``static/js/desk/*.js`` are split by feature for editing, but they must run as
a single classic script: function declarations are hoisted across the whole
program and async boot code (auth check → first tick) assumes every function
already exists. Loading them as separate ``<script>`` tags would break both.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from market_desk.config import STATIC_DIR

DESK_JS_DIR = Path(STATIC_DIR) / "js" / "desk"

_cache: dict[str, object] = {"key": None, "body": b"", "etag": ""}


def desk_js_files() -> list[Path]:
    """Return desk JS sources in load order (numeric filename prefix)."""
    return sorted(DESK_JS_DIR.glob("*.js"), key=lambda p: p.name)


def desk_bundle() -> tuple[bytes, str]:
    """Return ``(body, etag)`` for the concatenated desk script.

    Rebuilt only when a source file is added, removed or modified, so edits
    show up on the next page load without restarting the server.
    """
    files = desk_js_files()
    key = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    if _cache["key"] != key:
        parts: list[str] = []
        for p in files:
            parts.append(f"// ===== {p.name} =====\n")
            parts.append(p.read_text(encoding="utf-8").rstrip() + "\n\n")
        body = "".join(parts).encode("utf-8")
        _cache.update(
            {"key": key, "body": body, "etag": '"' + hashlib.sha1(body).hexdigest()[:16] + '"'}
        )
    return _cache["body"], str(_cache["etag"])  # type: ignore[return-value]
