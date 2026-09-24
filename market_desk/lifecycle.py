"""Multi-day board lifecycle: starting / ongoing / ending mainlines."""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Display labels (UI / glossary). Internal stage keys stay English.
STAGE_LABELS = {
    "starting": "萌芽",
    "ongoing": "主升",
    "ending": "衰退",
}


def stage_label(stage: str | None) -> str:
    """Return the Chinese display label for a lifecycle stage key."""
    if not stage:
        return ""
    return STAGE_LABELS.get(str(stage), str(stage))


def build_mainline_lifecycle(
    hot: list[dict[str, Any]] | None,
    pins: list[dict[str, Any]] | None = None,
    limit_each: int = 4,
    *,
    hist_map: dict[str, list[dict[str, Any]]] | None = None,
    frozen: dict[str, str] | None = None,
    final: bool = True,
) -> dict[str, Any]:
    """Classify watched boards into starting / ongoing / ending buckets.

    Lifecycle is a multi-day view, so columns only move once per day:

    * ``final=True`` (after the close): classify today's cards with the closed
      session included; ``stage_map`` should be persisted as tomorrow's
      ``frozen`` baseline.
    * ``final=False`` (pre-open / intraday): columns come from ``frozen`` (the
      last close) or, when missing, from closed-day ``hist_map`` rows only.
      Live cards only decorate the rows (``live_hint``) and feed ``fresh``
      (intraday ignitions not yet counted).

    Thresholds soften when recent review hit-rate is weak.
    """
    bias = _review_lifecycle_bias()
    live: dict[str, dict[str, Any]] = {}
    for src in (hot or []) + (pins or []):
        bk = str(src.get("bk") or src.get("name") or "")
        if bk and bk not in live:
            live[bk] = src

    stage_map: dict[str, str] = {}
    pool: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    if final or hist_map is None:
        for bk, src in live.items():
            stage = classify_lifecycle(src, bias=bias)
            stage_map[bk] = stage or ""
            if stage:
                pool.append(_compact(src, stage, bias=bias))
        mode = "close" if final else "live"
    else:
        closed = _closed_series_map(hist_map)
        base = dict(frozen or {})
        for bk, series in closed.items():
            if bk not in base:
                base[bk] = closed_stage_from_hist(series, bias=bias) or ""
        for bk, stage in base.items():
            stage_map[bk] = stage
            if not stage:
                continue
            card = live.get(bk)
            if card is not None:
                row = _compact(card, stage, bias=bias)
                live_stage = classify_lifecycle(card, bias=bias)
                if live_stage != stage:
                    row["live_stage"] = live_stage or ""
                    row["live_hint"] = _live_hint(live_stage)
            else:
                series = closed.get(bk) or []
                if not series:
                    continue
                row = _compact(_card_from_closed(series), stage, bias=bias)
                row["live_hint"] = "今日未进热门 · 数据为上一收盘"
            pool.append(row)
        for bk, card in live.items():
            if stage_map.get(bk):
                continue
            if classify_lifecycle(card, bias=bias) == "starting":
                fresh.append(
                    {
                        "bk": card.get("bk"),
                        "name": card.get("name"),
                        "pct": card.get("pct"),
                        "zt_n": card.get("zt_n"),
                    }
                )
        fresh.sort(key=lambda r: (int(r.get("zt_n") or 0), float(r.get("pct") or 0)), reverse=True)
        mode = "frozen"

    starting = [x for x in pool if x["stage"] == "starting"]
    ongoing = [x for x in pool if x["stage"] == "ongoing"]
    ending = [x for x in pool if x["stage"] == "ending"]

    starting.sort(key=_rank_start, reverse=True)
    ongoing.sort(key=_rank_ongoing, reverse=True)
    ending.sort(key=_rank_ending, reverse=True)

    note = "按近几日热点持续度划分：萌芽 / 主升 / 衰退（非买卖指令）"
    if mode == "frozen":
        note += "；按上一收盘分列，盘中不换列，收盘后更新"
    elif mode == "close":
        note += "；已按今日收盘更新"
    if bias.get("strict"):
        note += f"；复盘命中偏弱({bias.get('hit_rate')}%)，衰退判定更敏感"
    elif bias.get("hit_rate") is not None:
        note += f"；复盘命中约 {bias.get('hit_rate')}%，沿用标准阈值"

    return {
        "starting": starting[:limit_each],
        "ongoing": ongoing[:limit_each],
        "ending": ending[:limit_each],
        "fresh": fresh[:limit_each],
        "note": note,
        "mode": mode,
        "stage_map": stage_map,
        "bias": bias,
    }


def closed_stage_from_hist(
    series: list[dict[str, Any]],
    *,
    bias: dict[str, Any] | None = None,
) -> str | None:
    """Classify a board using closed sessions only (last row acts as "today")."""
    if not series:
        return None
    return classify_lifecycle(_card_from_closed(series), bias=bias)


def _card_from_closed(series: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a pseudo card whose "today" is the last closed session."""
    last = series[-1]
    return {
        "bk": last.get("bk"),
        "name": last.get("name"),
        "zt_n": int(last.get("zt_n") or 0),
        "pct": float(last.get("pct") or 0),
        "status": str(last.get("status") or ""),
        "leader_boards": last.get("leader_boards"),
        "headline": last.get("status"),
        "hist": list(series[:-1]),
        "tags": [],
    }


def _closed_series_map(
    hist_map: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Keep trading-day rows and boards seen in the last two closed sessions.

    Boards seen two sessions ago but missing on the last one get a cooled
    placeholder day so they can still register as fading.
    """
    try:
        from market_desk.calendar import is_trading_day
    except Exception:  # noqa: BLE001
        is_trading_day = None  # type: ignore[assignment]

    def _ok(day: str) -> bool:
        if is_trading_day is None:
            return True
        try:
            return bool(is_trading_day(datetime.strptime(day[:10], "%Y-%m-%d")))
        except (TypeError, ValueError):
            return True

    cleaned: dict[str, list[dict[str, Any]]] = {}
    dates: set[str] = set()
    for bk, rows in (hist_map or {}).items():
        keep = [dict(r, bk=bk) for r in rows if _ok(str(r.get("trade_date") or ""))]
        if keep:
            cleaned[bk] = keep
            dates.update(str(r.get("trade_date") or "") for r in keep)
    recent = sorted(d for d in dates if d)[-2:]
    if not recent:
        return {}
    last_day = recent[-1]
    out: dict[str, list[dict[str, Any]]] = {}
    for bk, rows in cleaned.items():
        tail = str(rows[-1].get("trade_date") or "")
        if tail == last_day:
            out[bk] = rows
        elif tail in recent:
            out[bk] = rows + [
                {
                    "trade_date": last_day,
                    "bk": bk,
                    "name": rows[-1].get("name"),
                    "zt_n": 0,
                    "pct": 0.0,
                    "status": "",
                    "leader_boards": 0,
                }
            ]
    return out


def _live_hint(live_stage: str | None) -> str:
    """Short intraday note when live data disagrees with the frozen column."""
    if not live_stage:
        return "盘中转淡 · 收盘复核"
    return f"盘中像{stage_label(live_stage)} · 收盘复核"


def classify_lifecycle(
    board: dict[str, Any],
    *,
    bias: dict[str, Any] | None = None,
) -> str | None:
    """Return starting | ongoing | ending | None for one board card."""
    status = board.get("status") or ""
    if status in ("冰点", "冷冻", "相对最冷", "传染预警"):
        return None
    pct = float(board.get("pct") or 0)
    zt_n = int(board.get("zt_n") or 0)
    hist = list(board.get("hist") or [])
    flags = _flag_map(board)
    bias = bias or {}

    hot_days = _hot_day_count(hist, zt_n, pct, status)
    peak_zt = max([int(h.get("zt_n") or 0) for h in hist] + [zt_n], default=0)
    prev_zt = int(hist[-1].get("zt_n") or 0) if hist else 0
    jumped = zt_n >= max(3, prev_zt + 2) and prev_zt <= 2
    decay = _zt_decay_days(hist, zt_n)
    slope = _zt_slope(hist, zt_n)
    # Strict mode: fade one day earlier when review hit-rate is poor.
    decay_need = 1 if bias.get("strict") else 2
    fade_pct = -0.2 if bias.get("strict") else -0.5

    # Ending / repairing first (including soft decay while status still "确认中").
    if (
        status == "退潮"
        or flags.get("修复")
        or flags.get("A杀")
        or (pct < fade_pct and peak_zt >= 2 and hot_days >= 1)
        or (decay >= decay_need and peak_zt >= 3 and pct < 1.0)
        or (slope <= -1.5 and hot_days >= 2 and status != "尖峰禁追")
    ):
        if peak_zt >= 2 or hot_days >= 2 or status == "退潮" or decay >= decay_need:
            return "ending"
        return None

    # Starting: ignition, jump, recovery from a quiet base, or early confirm.
    recovering = prev_zt <= 1 and zt_n >= 2 and pct >= 1.0 and hot_days <= 2
    if flags.get("点火") or jumped or recovering:
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


def _review_lifecycle_bias() -> dict[str, Any]:
    """Soften/tighten lifecycle thresholds using recent buy hit-rate.

    Uses the same cool bar as ``apply_review_bias`` (n≥5, hit&lt;35%) on overall
    traded buys. Phase-specific demote stays in verdict; lifecycle only needs a
    shared threshold so both layers do not invent different cutoffs.
    """
    try:
        from market_desk.db import load_signals
        from market_desk.review import summarize_signals

        rows = load_signals(limit=240)
        summary = summarize_signals(rows)
        rate = summary.get("buy_hit_rate")
        scored_n = int(summary.get("buy_scored") or 0)
        if rate is None or scored_n < 5:
            return {"strict": False, "hit_rate": rate, "n": scored_n}
        return {
            "strict": bool(float(rate) < 35),
            "hit_rate": float(rate),
            "n": scored_n,
        }
    except Exception:
        return {"strict": False, "hit_rate": None, "n": 0}


def _zt_decay_days(hist: list[dict[str, Any]], today_zt: int) -> int:
    """Count consecutive sessions where limit-up count is falling into today."""
    series = [int(h.get("zt_n") or 0) for h in hist[-4:]] + [int(today_zt)]
    if len(series) < 2:
        return 0
    decay = 0
    for i in range(len(series) - 1, 0, -1):
        if series[i] < series[i - 1]:
            decay += 1
        else:
            break
    return decay


def _zt_slope(hist: list[dict[str, Any]], today_zt: int) -> float:
    """Rough zt slope over the last few sessions (today included)."""
    series = [int(h.get("zt_n") or 0) for h in hist[-3:]] + [int(today_zt)]
    if len(series) < 2:
        return 0.0
    return float(series[-1] - series[0]) / float(len(series) - 1)


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


def _compact(
    board: dict[str, Any],
    stage: str,
    *,
    bias: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = next((t.get("k") for t in (board.get("tags") or []) if t.get("on")), None)
    hist = list(board.get("hist") or [])
    zt_n = int(board.get("zt_n") or 0)
    pct = float(board.get("pct") or 0)
    status = str(board.get("status") or "")
    return {
        "stage": stage,
        "stage_label": stage_label(stage),
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
        "hot_days": _hot_day_count(hist, zt_n, pct, status),
        "zt_decay": _zt_decay_days(hist, zt_n),
        "zt_slope": round(_zt_slope(hist, zt_n), 2),
        "strict_bias": bool((bias or {}).get("strict")),
    }


def _rank_start(row: dict[str, Any]) -> tuple:
    return (int(row.get("zt_n") or 0), float(row.get("pct") or 0), int(row.get("hot_days") or 0))


def _rank_ongoing(row: dict[str, Any]) -> tuple:
    return (int(row.get("hot_days") or 0), int(row.get("zt_n") or 0), float(row.get("pct") or 0))


def _rank_ending(row: dict[str, Any]) -> tuple:
    # Prefer recently strong names that are fading.
    return (
        int(row.get("zt_decay") or 0),
        int(row.get("hot_days") or 0),
        -float(row.get("pct") or 0),
        int(row.get("zt_n") or 0),
    )
