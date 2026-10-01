"""Serve the dashboard's split JS / CSS sources as one script and one stylesheet.

``static/js/desk/*.js`` are split by feature for editing, but they must run as
a single classic script: function declarations are hoisted across the whole
program and async boot code (auth check → first tick) assumes every function
already exists. Loading them as separate ``<script>`` tags would break both.

``static/css/desk/*.css`` are split the same way; the numeric filename prefix
preserves the original cascade order, so later rules still win ties.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from market_desk.config import STATIC_DIR

DESK_JS_DIR = Path(STATIC_DIR) / "js" / "desk"
DESK_CSS_DIR = Path(STATIC_DIR) / "css" / "desk"

_caches: dict[str, dict[str, object]] = {}


def _sources(folder: Path, suffix: str) -> list[Path]:
    """Return sources in load order (numeric filename prefix)."""
    return sorted(folder.glob(f"*{suffix}"), key=lambda p: p.name)


def _bundle(folder: Path, suffix: str, banner: str) -> tuple[bytes, str]:
    """Concatenate ``folder/*suffix`` in name order and return ``(body, etag)``.

    Rebuilt only when a source file is added, removed or modified, so edits
    show up on the next page load without restarting the server.
    """
    cache = _caches.setdefault(suffix, {"key": None, "body": b"", "etag": ""})
    files = _sources(folder, suffix)
    key = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    if cache["key"] != key:
        parts: list[str] = []
        for p in files:
            parts.append(banner.format(name=p.name))
            parts.append(p.read_text(encoding="utf-8").rstrip() + "\n\n")
        body = "".join(parts).encode("utf-8")
        cache.update(
            {"key": key, "body": body, "etag": '"' + hashlib.sha1(body).hexdigest()[:16] + '"'}
        )
    return cache["body"], str(cache["etag"])  # type: ignore[return-value]


def desk_js_files() -> list[Path]:
    """Return desk JS sources in load order (numeric filename prefix)."""
    return _sources(DESK_JS_DIR, ".js")


def desk_bundle() -> tuple[bytes, str]:
    """Return ``(body, etag)`` for the concatenated desk script."""
    return _bundle(DESK_JS_DIR, ".js", "// ===== {name} =====\n")


def desk_css_files() -> list[Path]:
    """Return desk CSS sources in cascade order (numeric filename prefix)."""
    return _sources(DESK_CSS_DIR, ".css")


def desk_css_bundle() -> tuple[bytes, str]:
    """Return ``(body, etag)`` for the concatenated desk stylesheet."""
    return _bundle(DESK_CSS_DIR, ".css", "/* ===== {name} ===== */\n")
