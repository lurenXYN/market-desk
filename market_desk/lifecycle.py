"""Multi-day board lifecycle: starting / ongoing / ending mainlines."""

from __future__ import annotations

from typing import Any


def build_mainline_lifecycle(
    hot: list[dict[str, Any]] | None,
    pins: list[dict[str, Any]] | None = None,
    limit_each: int = 4,
) -> dict[str, Any]:
    """Classify watched boards into starting / ongoing / ending buckets.

    Uses today's status plus stored prior-day hist on each card (already attached
    by the engine enrichment). Boards that never showed strength are skipped.
    """
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for src in (hot or []) + (pins or []):
        bk = str(src.get("bk") or src.get("name") or "")
        if not bk or bk in seen:
            continue
        seen.add(bk)
        stage = classify_lifecycle(src)
        if not stage:
            continue
        pool.append(_compact(src, stage))

    starting = [x for x in pool if x["stage"] == "starting"]
    ongoing = [x for x in pool if x["stage"] == "ongoing"]
    ending = [x for x in pool if x["stage"] == "ending"]

    starting.sort(key=_rank_start, reverse=True)
    ongoing.sort(key=_rank_ongoing, reverse=True)
    ending.sort(key=_rank_ending, reverse=True)

    return {
        "starting": starting[:limit_each],
        "ongoing": ongoing[:limit_each],
        "ending": ending[:limit_each],
        "note": "按近几日热点持续度划分：启动候选 / 进行中 / 退潮中（非买卖指令）",
    }


def classify_lifecycle(board: dict[str, Any]) -> str | None:
    """Return starting | ongoing | ending | None for one board card."""
    status = board.get("status") or ""
    if status in ("冰点", "冷冻", "相对最冷", "传染预警"):
        return None
    pct = float(board.get("pct") or 0)
    zt_n = int(board.get("zt_n") or 0)
    leader_boards = int(board.get("leader_boards") or 0)
    hist = list(board.get("hist") or [])
    flags = _flag_map(board)

    hot_days = _hot_day_count(hist, zt_n, pct, status)
    peak_zt = max([int(h.get("zt_n") or 0) for h in hist] + [zt_n], default=0)
    prev_zt = int(hist[-1].get("zt_n") or 0) if hist else 0
    jumped = zt_n >= max(3, prev_zt + 2) and prev_zt <= 2

    # Ending / repairing first.
    if (
        status == "退潮"
        or flags.get("修复")
        or flags.get("A杀")
        or (pct < -0.5 and peak_zt >= 2 and hot_days >= 1)
    ):
        if peak_zt >= 2 or hot_days >= 2 or status == "退潮":
            return "ending"
        return None

    # Starting: ignition, jump, or early confirm.
    if flags.get("点火") or jumped:
        return "starting"
    if status == "确认中" and hot_days <= 2 and (zt_n >= 2 or pct >= 1.5):
        return "starting"
    if status == "观察" and zt_n >= 2 and pct >= 1.2 and hot_days <= 2:
        return "starting"

    # Ongoing: multi-day confirmation / peak but not fade.
    if status in ("确认中", "尖峰禁追") and hot_days >= 2:
        return "ongoing"
    if status == "确认中" and (flags.get("一波") or flags.get("二波") or flags.get("加速")):
        return "ongoing"
    if hot_days >= 3 and zt_n >= 1 and pct >= 0:
        return "ongoing"
    if status == "尖峰禁追" and hot_days >= 1:
        return "ongoing"

    return None


def _flag_map(board: dict[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for tag in board.get("tags") or []:
        if isinstance(tag, dict) and tag.get("on"):
            out[str(tag.get("k") or "")] = True
    return out


def _hot_day_count(
    hist: list[dict[str, Any]],
    zt_n: int,
    pct: float,
    status: str,
) -> int:
    """Count recent days that look like a live theme, including today."""
    n = 0
    for h in hist[-6:]:
        if _day_hot(int(h.get("zt_n") or 0), float(h.get("pct") or 0), str(h.get("status") or "")):
            n += 1
    if _day_hot(zt_n, pct, status):
        n += 1
    return n


def _day_hot(zt_n: int, pct: float, status: str) -> bool:
    if status in ("确认中", "尖峰禁追"):
        return True
    if zt_n >= 2:
        return True
    if zt_n >= 1 and pct >= 1.0:
        return True
    if pct >= 2.0:
        return True
    return False


def _compact(board: dict[str, Any], stage: str) -> dict[str, Any]:
    active = next((t.get("k") for t in (board.get("tags") or []) if t.get("on")), None)
    return {
        "stage": stage,
        "stage_label": {"starting": "马上开始", "ongoing": "进行中", "ending": "快结束"}.get(
            stage, stage
        ),
        "bk": board.get("bk"),
        "name": board.get("name"),
        "kind": board.get("kind"),
        "status": board.get("status"),
        "headline": board.get("headline") or board.get("status"),
        "pct": board.get("pct"),
        "zt_n": board.get("zt_n"),
        "cluster": board.get("cluster"),
        "spark": board.get("spark") or [],
        "leader_name": board.get("leader_name"),
        "leader_boards": board.get("leader_boards"),
        "tone": board.get("tone"),
        "active_tag": active,
        "note": board.get("note") or "",
        "hot_days": _hot_day_count(
            list(board.get("hist") or []),
            int(board.get("zt_n") or 0),
            float(board.get("pct") or 0),
            str(board.get("status") or ""),
        ),
    }


def _rank_start(row: dict[str, Any]) -> tuple:
    return (int(row.get("zt_n") or 0), float(row.get("pct") or 0), int(row.get("hot_days") or 0))


def _rank_ongoing(row: dict[str, Any]) -> tuple:
    return (int(row.get("hot_days") or 0), int(row.get("zt_n") or 0), float(row.get("pct") or 0))


def _rank_ending(row: dict[str, Any]) -> tuple:
    # Prefer recently strong names that are fading.
    return (int(row.get("hot_days") or 0), -float(row.get("pct") or 0), int(row.get("zt_n") or 0))
