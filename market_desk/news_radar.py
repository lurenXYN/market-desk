"""Fetch soft sector hints from the standalone news-radar service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("market_desk.news_radar")


async def fetch_news_radar_export(
    *,
    base_url: str,
    limit: int = 8,
    timeout: float = 4.0,
) -> dict[str, Any] | None:
    """GET news-radar ``/api/export/sectors``. Return None on any failure."""
    root = str(base_url or "").rstrip("/")
    if not root:
        return None
    url = f"{root}/api/export/sectors?limit={int(limit)}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict):
            return None
        data["source_url"] = root
        return data
    except Exception as exc:  # noqa: BLE001 — soft fail
        log.info("news-radar fetch skipped: %s", exc)
        return None


def news_radar_brief_lines(payload: dict[str, Any] | None, *, max_n: int = 3) -> list[str]:
    """Format compact morning-brief bullets from an export payload."""
    if not payload or not payload.get("ok"):
        return []
    rows = list(payload.get("sectors") or [])[:max_n]
    if not rows:
        return []
    parts: list[str] = []
    for r in rows:
        name = str(r.get("sector") or "").strip()
        if not name:
            continue
        label = str(r.get("confirm_label") or r.get("confirm") or "").strip()
        pct = r.get("board_pct")
        delta = r.get("delta")
        bits = [name]
        if label:
            bits.append(label)
        if pct is not None:
            try:
                bits.append(f"{float(pct):+.1f}%")
            except (TypeError, ValueError):
                pass
        if delta is not None:
            try:
                d = float(delta)
                if abs(d) >= 0.5:
                    bits.append(f"热Δ{d:+.0f}")
            except (TypeError, ValueError):
                pass
        parts.append("/".join(bits))
    if not parts:
        return []
    return ["新闻催化（软）：" + " · ".join(parts)]
