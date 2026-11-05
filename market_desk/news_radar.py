"""Fetch soft sector hints from the standalone news-radar service.

Also freezes a per-signal shadow blob at record time so later review can
compare lit cards with vs without news resonance (never changes buy gates).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

log = logging.getLogger("market_desk.news_radar")

_CONFIRM_RANK = {
    "resonance": 40,
    "board_lead": 30,
    "diverge": 20,
    "news_only": 10,
    "neutral": 5,
    "unknown": 0,
}

try:
    from zoneinfo import ZoneInfo

    _CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    _CN_TZ = timezone(timedelta(hours=8))


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


def freeze_news_radar(
    snapshot: dict[str, Any] | None,
    *,
    board_names: list[str] | None = None,
    mainline: str = "",
    source_board: str = "",
) -> dict[str, Any] | None:
    """Freeze a compact news-radar shadow blob for one buy signal payload.

    Matches radar sectors against the card's boards / mainline. ``hit`` is True
    only for resonance / board_lead overlap. Never affects ready gates.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    nr = snap.get("news_radar") if isinstance(snap.get("news_radar"), dict) else {}
    if not nr:
        return None
    if not nr.get("enabled", True):
        return {
            "ok": False,
            "status": "disabled",
            "hit": False,
            "at": datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
    names = [str(x).strip() for x in (board_names or []) if str(x).strip()]
    for extra in (source_board, mainline, str(nr.get("mainline_name") or "")):
        e = str(extra or "").strip()
        if e and e not in names:
            names.append(e)
    rows = list(nr.get("sectors") or [])
    matches: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sector = str(r.get("sector") or "").strip()
        if not sector:
            continue
        if names and not any(_sector_overlap(sector, n) for n in names):
            continue
        confirm = str(r.get("confirm") or "")
        matches.append(
            {
                "sector": sector,
                "confirm": confirm,
                "confirm_label": r.get("confirm_label") or confirm,
                "board_pct": r.get("board_pct"),
                "delta": r.get("delta"),
                "score": r.get("score"),
            }
        )
    matches.sort(
        key=lambda m: (
            float(_CONFIRM_RANK.get(str(m.get("confirm") or ""), 0)),
            float(m.get("score") or 0),
        ),
        reverse=True,
    )
    best = matches[0] if matches else None
    hit = bool(
        best and str(best.get("confirm") or "") in ("resonance", "board_lead")
    )
    return {
        "ok": bool(nr.get("ok")),
        "status": str(nr.get("status") or ""),
        "status_label": str(nr.get("status_label") or ""),
        "hit": hit,
        "confirm": (best or {}).get("confirm") if best else None,
        "confirm_label": (best or {}).get("confirm_label") if best else None,
        "sector": (best or {}).get("sector") if best else None,
        "board_pct": (best or {}).get("board_pct") if best else None,
        "matches_n": len(matches),
        "at": datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }


def news_radar_shadow_summary(*, days: int = 30) -> dict[str, Any]:
    """Compare lit buy outcomes with vs without frozen news resonance.

    Uses ``payload.news_radar.hit`` frozen at record time and classic
    ``outcome_label`` / day1·day3 floats. Soft observation only.
    """
    from market_desk.config import NEWS_RADAR_SHADOW_SINCE
    from market_desk.db import _connect
    from market_desk.review.outcome import BUY_HIT_LABELS
    import json

    since = str(NEWS_RADAR_SHADOW_SINCE)[:10]
    cutoff_days = max(5, min(int(days or 30), 90))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, code, ready, outcome_label,
                   outcome_day1_pct, outcome_day3_pct, payload
            FROM signals
            WHERE trade_date >= ?
              AND COALESCE(owner_user_id, 0) = 0
              AND signal_type LIKE 'buy%'
            ORDER BY trade_date DESC, id DESC
            LIMIT 800
            """,
            (since,),
        ).fetchall()

    def _bucket() -> dict[str, Any]:
        return {"n": 0, "scored": 0, "hit": 0, "day1": [], "day3": []}

    with_nr = _bucket()
    without = _bucket()
    missing = 0
    days_seen: set[str] = set()

    for row in rows:
        day = str(row["trade_date"] or "")[:10]
        days_seen.add(day)
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        lit = bool(
            payload.get("ever_ready")
            or payload.get("first_ready_at")
            or int(row["ready"] or 0)
        )
        if not lit:
            continue
        nr = payload.get("news_radar") if isinstance(payload.get("news_radar"), dict) else None
        if nr is None:
            missing += 1
            continue
        bucket = with_nr if bool(nr.get("hit")) else without
        bucket["n"] += 1
        lab = str(row["outcome_label"] or "")
        if lab:
            bucket["scored"] += 1
            if lab in BUY_HIT_LABELS:
                bucket["hit"] += 1
        for key, dst in (
            ("outcome_day1_pct", "day1"),
            ("outcome_day3_pct", "day3"),
        ):
            try:
                if row[key] is not None:
                    bucket[dst].append(float(row[key]))
            except (TypeError, ValueError):
                pass

    def _fin(b: dict[str, Any]) -> dict[str, Any]:
        scored = int(b["scored"])
        d1 = b["day1"]
        d3 = b["day3"]
        return {
            "n": int(b["n"]),
            "scored": scored,
            "hit_rate": round(100.0 * b["hit"] / scored, 1) if scored else None,
            "avg_day1": round(sum(d1) / len(d1), 2) if d1 else None,
            "avg_day3": round(sum(d3) / len(d3), 2) if d3 else None,
        }

    w = _fin(with_nr)
    wo = _fin(without)
    delta_d3 = None
    if w.get("avg_day3") is not None and wo.get("avg_day3") is not None:
        delta_d3 = round(float(w["avg_day3"]) - float(wo["avg_day3"]), 2)
    hint = "样本不足，继续观察"
    if w["scored"] >= 5 and wo["scored"] >= 5 and delta_d3 is not None:
        if delta_d3 >= 0.5:
            hint = "有新闻共振的亮灯卡三日均更好 → 软提示保留"
        elif delta_d3 <= -0.5:
            hint = "有新闻共振反而更差 → 催化多半是噪音"
        else:
            hint = "有/无新闻共振差别不大 → 可降级推送"
    return {
        "ok": True,
        "since": since,
        "days": len(days_seen),
        "window_days": cutoff_days,
        "missing_freeze": missing,
        "with_news_hit": w,
        "without_news_hit": wo,
        "delta_day3": delta_d3,
        "hint": hint,
        "note": "只统计亮过灯的买卡；hit=当时板块有共振/盘面已动。不改闸门。",
    }
