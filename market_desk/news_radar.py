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


def _sector_overlap(sector: str, mainline: str) -> bool:
    """True when news sector and desk mainline share a meaningful token."""
    s = str(sector or "").strip()
    m = str(mainline or "").strip()
    if not s or not m:
        return False
    if s in m or m in s:
        return True
    for token in ("半导体", "人工智能", "AI", "新能源", "锂电", "军工", "白酒",
                  "银行", "证券", "煤炭", "有色", "石油", "医药", "创新药",
                  "通信", "电力", "农业", "房地产", "红利", "消费"):
        if token in s and token in m:
            return True
    return False


def enrich_news_radar(
    payload: dict[str, Any] | None,
    *,
    mainline_name: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Attach mainline contrast, checklist Top3, and health status for the desk."""
    if not enabled:
        return {
            "ok": False,
            "enabled": False,
            "status": "disabled",
            "status_label": "未接入",
            "sectors": [],
            "checklist": [],
            "mainline_note": "",
        }
    if not payload:
        return {
            "ok": False,
            "enabled": True,
            "status": "offline",
            "status_label": "离线/超时",
            "sectors": [],
            "checklist": [],
            "mainline_note": "新闻雷达未连通（检查 URL 与 :8770 是否在跑）",
        }

    rows = list(payload.get("sectors") or [])
    ml = str(mainline_name or "").strip()
    overlap_n = 0
    for r in rows:
        hit = _sector_overlap(str(r.get("sector") or ""), ml)
        r["mainline_match"] = hit
        if hit:
            overlap_n += 1
            r["mainline_note"] = f"与今日主线「{ml}」同向 → 增强观察"
        elif ml:
            r["mainline_note"] = f"新闻热但主线是「{ml}」→ 仅支线"
        else:
            r["mainline_note"] = "主线未明：按新闻催化单独观察"

    if not rows:
        status, label = "empty", "空数据"
        note = "雷达在线但暂无热板块"
    elif overlap_n > 0:
        status, label = "online", "在线·同主线"
        note = f"新闻热与主线「{ml}」有重叠（{overlap_n}）→ 增强观察"
    elif ml:
        status, label = "online", "在线·支线"
        note = f"新闻热但主线是「{ml}」→ 仅支线对照"
    else:
        status, label = "online", "在线"
        note = ""

    checklist: list[dict[str, Any]] = []
    for r in rows[:3]:
        checklist.append(
            {
                "sector": r.get("sector"),
                "confirm_label": r.get("confirm_label") or r.get("confirm"),
                "tone_label": r.get("tone_label") or "",
                "board_pct": r.get("board_pct"),
                "delta": r.get("delta"),
                "etfs": list(r.get("etfs") or [])[:3],
                "action_hint": r.get("action_hint")
                or "开盘看高开低走还是站稳；不追尖",
                "mainline_note": r.get("mainline_note") or "",
                "articles": list(r.get("articles") or [])[:3],
            }
        )

    out = dict(payload)
    out["enabled"] = True
    out["ok"] = bool(payload.get("ok")) and bool(rows)
    out["status"] = status if out["ok"] or status == "empty" else "offline"
    if not bool(payload.get("ok")) and rows:
        out["status"] = "stale"
        label = "数据陈旧"
    out["status_label"] = label
    out["mainline_name"] = ml
    out["mainline_note"] = note
    out["checklist"] = checklist
    out["sectors"] = rows
    return out


def news_radar_brief_lines(payload: dict[str, Any] | None, *, max_n: int = 3) -> list[str]:
    """Format compact morning-brief bullets from an export payload."""
    if not payload or not payload.get("enabled", True):
        return []
    if payload.get("status") == "offline":
        return ["新闻雷达：未连通（软提示跳过）"]
    if not payload.get("ok") and not (payload.get("sectors") or []):
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
        tone = str(r.get("tone_label") or "").strip()
        pct = r.get("board_pct")
        delta = r.get("delta")
        bits = [name]
        if label:
            bits.append(label)
        if tone and tone != "中性":
            bits.append(tone)
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
    lines = ["新闻催化（软）：" + " · ".join(parts)]
    note = str(payload.get("mainline_note") or "").strip()
    if note:
        lines.append("新闻×主线：" + note)
    return lines
