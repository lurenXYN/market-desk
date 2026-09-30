"""Market gates, auction bridge, switch guard, review and similar-day bias."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.verdict.common import (
    _join_hint,
    _lookup_hot_board,
    _pool_codes_from_board,
    _theme_entry,
)


def _mainline_narrative(
    *,
    board_name: str,
    status: str,
    action: str,
    phase: str,
    pct: Any,
    zt_n: Any,
    leader_name: str | None,
    prev_name: str,
    segment_label: str,
    size_hint: str | None,
) -> str:
    """Build a one-line explanation of why the live mainline looks this way."""
    bits: list[str] = []
    if prev_name and board_name and prev_name != board_name:
        bits.append(f"主线由「{prev_name}」切到「{board_name}」")
    elif board_name:
        bits.append(f"当前主线「{board_name}」")
    else:
        bits.append("主线尚未识别")
    if status:
        bits.append(f"状态{status}")
    if pct is not None:
        try:
            bits.append(f"涨幅{float(pct):+.2f}%")
        except (TypeError, ValueError):
            pass
    if zt_n is not None:
        try:
            bits.append(f"涨停{int(zt_n)}只")
        except (TypeError, ValueError):
            pass
    if leader_name:
        bits.append(f"龙头{leader_name}")
    if phase:
        bits.append(f"大盘{phase}")
    if segment_label:
        bits.append(segment_label)
    bits.append(f"结论{action}")
    if size_hint:
        bits.append(size_hint)
    return "；".join(bits) + "。"


def apply_market_gates(
    action: str,
    reason: str,
    size_hint: str,
    *,
    metrics: dict[str, Any] | None,
    auction: dict[str, Any] | None,
    phase: str,
    segment_key: str,
) -> tuple[str, str, str, list[str]]:
    """Tighten buy actions using volume, index and auction context."""
    notes: list[str] = []
    m = metrics or {}
    hint = size_hint or ""
    act = action
    why = reason

    if phase == "恐慌" and act == "可买入":
        act = "观望"
        why = f"相位恐慌，新开仓关闭：{why}"
        notes.append("恐慌禁开仓")

    if phase == "高潮" and act == "可买入":
        act = "观察回踩"
        why = f"相位高潮，防冲高回落，先等回踩：{why}"
        notes.append("高潮降级")
        hint = _join_hint(hint, "高潮只兑现/等回踩，不追尖")

    if m.get("weak_index") and act == "可买入":
        act = "观察回踩"
        why = f"指数偏弱，降级观察回踩：{why}"
        notes.append("指数闸门")
        hint = _join_hint(hint, "指数偏弱宜更小仓")

    if m.get("style_weak") and act == "可买入":
        act = "观察回踩"
        why = f"成长风格偏弱(创业−沪深300)，降级观察回踩：{why}"
        notes.append("风格闸门")
        hint = _join_hint(hint, "成长风格弱宜更小仓")
    elif m.get("style_hot") and act == "可买入":
        hint = _join_hint(hint, "成长风格偏热仍防追高")
        notes.append("风格偏热防追")

    if float(m.get("avg_explode") or 0) >= 2.0 and act == "可买入":
        hint = _join_hint(hint, "涨停反复炸板，控仓")
        notes.append("炸板质量偏弱")
    if float(m.get("late_seal_rate") or 0) >= 50 and act == "可买入":
        hint = _join_hint(hint, "晚封偏多，控仓")
        notes.append("晚封偏多")

    try:
        prem = float(m.get("premium") or 0)
    except (TypeError, ValueError):
        prem = 0.0
    if prem <= -1.0 and act == "可买入":
        act = "观察回踩"
        why = f"昨停溢价偏弱({prem:.2f}%)，降级观察回踩：{why}"
        notes.append("负溢价闸门")
        hint = _join_hint(hint, "负溢价控仓或不做")
    elif prem <= 0 and act == "可买入":
        hint = _join_hint(hint, "溢价偏弱控仓")
        notes.append("溢价偏弱")
    elif prem >= 3.0 and act == "可买入":
        hint = _join_hint(hint, "溢价偏热仍防追")
        notes.append("溢价偏热防追")

    if m.get("thin_volume") and act == "可买入":
        act = "观察回踩"
        why = f"成交额分位偏低，防缩量假强：{why}"
        notes.append("缩量闸门")
        hint = _join_hint(hint, "缩量行情小仓或不做")

    if int(m.get("big_drop") or 0) >= 100 and act == "可买入":
        act = "观察回踩"
        why = f"大面家数偏多，降级观察：{why}"
        notes.append("大面闸门")

    auc = auction or {}
    med = auc.get("median_open")
    try:
        med_f = float(med) if med is not None else None
    except (TypeError, ValueError):
        med_f = None
    if med_f is not None and segment_key in ("open30", "morning", "afternoon"):
        if med_f <= -1.2 and act == "可买入":
            act = "观察回踩"
            why = f"弱竞价(中位{med_f:.2f}%)，降级观察：{why}"
            notes.append("弱竞价闸门")
            hint = _join_hint(hint, "弱竞价只试错或观望")
        elif med_f <= -0.5:
            hint = _join_hint(hint, "竞价偏弱控仓")
            notes.append("竞价偏弱")
        elif med_f >= 2.0 and act == "可买入":
            hint = _join_hint(hint, "强竞价勿追高")
            notes.append("强竞价防追")

    for g in m.get("context_gates") or []:
        if g not in notes:
            notes.append(str(g))
    return act, why, hint, notes[:6]


def apply_auction_open_bridge(
    action: str,
    reason: str,
    size_hint: str,
    *,
    now: datetime,
    segment_key: str,
    auction: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    mainline_pct: Any = None,
    carrier_pct: Any = None,
) -> tuple[str, str, str, list[str], dict[str, Any]]:
    """Demote buys when a strong auction fades in the first open minutes.

    Window: 09:30–09:45 (continuous session start). Strong median open plus a
    weak index / soft mainline-carrier print → observe + revoke probes.
    """
    from market_desk.config import (
        AUCTION_OPEN_BRIDGE_MINUTES,
        AUCTION_OPEN_STRONG_MEDIAN,
        AUCTION_OPEN_WEAK_HS300,
    )

    notes: list[str] = []
    bridge: dict[str, Any] = {
        "active": False,
        "revoke_probe": False,
        "median_open": None,
        "window_minutes": int(AUCTION_OPEN_BRIDGE_MINUTES),
        "segment_key": str(segment_key or ""),
    }
    act = action
    why = reason
    hint = size_hint or ""
    minutes = now.hour * 60 + now.minute
    open_m = 9 * 60 + 30
    end_m = open_m + int(AUCTION_OPEN_BRIDGE_MINUTES)
    # First N minutes of continuous auction (covers open_mute overlay on open30).
    in_window = open_m <= minutes < end_m
    if not in_window:
        return act, why, hint, notes, bridge

    auc = auction or {}
    med = auc.get("median_open")
    try:
        med_f = float(med) if med is not None else None
    except (TypeError, ValueError):
        med_f = None
    bridge["median_open"] = med_f
    if med_f is None or med_f < float(AUCTION_OPEN_STRONG_MEDIAN):
        return act, why, hint, notes, bridge

    m = metrics or {}
    weak = bool(m.get("weak_index"))
    try:
        hs = m.get("hs300_pct")
        if hs is not None and float(hs) <= float(AUCTION_OPEN_WEAK_HS300):
            weak = True
    except (TypeError, ValueError):
        pass
    try:
        if mainline_pct is not None and float(mainline_pct) < 0:
            weak = True
    except (TypeError, ValueError):
        pass
    try:
        if carrier_pct is not None and float(carrier_pct) < 0:
            weak = True
    except (TypeError, ValueError):
        pass
    if not weak:
        return act, why, hint, notes, bridge

    bridge["active"] = True
    bridge["revoke_probe"] = True
    notes.append("竞价开盘桥")
    if act == "可买入":
        act = "观察回踩"
        why = f"竞价强但开盘偏弱，降级观察回踩：{why}"
    hint = _join_hint(hint, "竞价强开盘弱·降仓")
    return act, why, hint, notes, bridge


def _switch_age_from_since(sticky_since: str | None, now: datetime) -> float | None:
    """Return seconds since sticky_since wall clock, or None if unparseable."""
    text = str(sticky_since or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime as _dt

        since_dt = _dt.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        now_naive = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now
        return max(0.0, (now_naive - since_dt).total_seconds())
    except (TypeError, ValueError):
        return None


def build_switch_guard(
    *,
    now: datetime,
    sticky_since: str | None,
    current_name: str,
    prev_name: str | None,
    grace_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a grace-window guard when sticky mainline just switched."""
    from market_desk.config import SWITCH_SELL_GRACE_SECONDS

    grace = float(
        grace_seconds if grace_seconds is not None else SWITCH_SELL_GRACE_SECONDS
    )
    cur = str(current_name or "").strip()
    prev = str(prev_name or "").strip()
    age = _switch_age_from_since(sticky_since, now)
    from_name = ""
    to_name = cur
    active = False
    if cur and prev and cur != prev:
        # Flip on this tick: sticky_since was just reset.
        from_name = prev
        if age is None:
            age = 0.0
        active = age <= grace
    elif cur and age is not None and age <= grace:
        # Same sticky name still inside grace after an earlier flip today.
        active = True
        to_name = cur
    return {
        "active": bool(active and cur),
        "from_name": from_name,
        "to_name": to_name,
        "grace_seconds": grace,
        "age_seconds": None if age is None else round(float(age), 1),
    }


def inject_switch_from_theme(
    sell_themes: list[dict[str, Any]] | None,
    switch_guard: dict[str, Any] | None,
    *,
    hot: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Inject the previous sticky theme as an observe/fade sell peer."""
    themes = list(sell_themes or [])
    sg = switch_guard or {}
    from_name = str(sg.get("from_name") or "").strip()
    if not sg.get("active") or not from_name:
        return themes
    if any(str(t.get("name") or "").strip() == from_name for t in themes):
        # Already present: force fade observation so old bags enter soft sell set.
        out: list[dict[str, Any]] = []
        for t in themes:
            row = dict(t)
            if str(row.get("name") or "").strip() == from_name:
                if str(row.get("role") or "") == "primary":
                    out.append(row)
                    continue
                row["role"] = row.get("role") or "switch_from"
                if str(row.get("status") or "") not in ("退潮",):
                    row["status"] = "退潮"
                if str(row.get("lifecycle") or "") != "ending":
                    row["lifecycle"] = "ending"
                row["switch_from"] = True
            out.append(row)
        return out

    board = _lookup_hot_board(hot or [], name=from_name)
    pool = _pool_codes_from_board(board) if board else []
    entry = _theme_entry(
        name=from_name,
        role="switch_from",
        status="退潮",
        lifecycle="ending",
        pool_codes=pool,
        carrier_code=None,
        score=None,
        main_yi=(board or {}).get("main_yi") if board else None,
    )
    entry["switch_from"] = True
    themes.append(entry)
    return themes


def attach_switch_guard(
    verdict: dict[str, Any] | None,
    switches: list[dict[str, Any]] | None = None,
    *,
    now: datetime | None = None,
    hot: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge same-day switch events into switch_guard and inject old sell theme."""
    from market_desk.config import SWITCH_SELL_GRACE_SECONDS

    out = dict(verdict or {})
    grace = float(SWITCH_SELL_GRACE_SECONDS)
    sg = dict(out.get("switch_guard") or {})
    clock = now
    if clock is None:
        clock = datetime.now()
    rows = list(switches or [])
    latest = rows[0] if rows else None
    if latest:
        age = _switch_age_from_since(str(latest.get("switched_at") or ""), clock)
        from_name = str(latest.get("from_name") or "").strip()
        to_name = str(latest.get("to_name") or "").strip()
        if age is not None and age <= grace and to_name:
            sg["active"] = True
            sg["from_name"] = from_name or str(sg.get("from_name") or "")
            sg["to_name"] = to_name
            sg["age_seconds"] = round(float(age), 1)
            sg["grace_seconds"] = grace
        elif from_name and not sg.get("from_name"):
            sg["from_name"] = from_name
    if not sg.get("grace_seconds"):
        sg["grace_seconds"] = grace
    if sg.get("active") is None:
        sg["active"] = False
    out["switch_guard"] = sg
    if sg.get("active") and sg.get("from_name"):
        out["sell_themes"] = inject_switch_from_theme(
            out.get("sell_themes"), sg, hot=hot
        )
        notes = list(out.get("algo_notes") or [])
        tip = f"换防护栏={sg.get('from_name')}→{sg.get('to_name')}"
        if tip not in notes:
            notes.append(tip)
            out["algo_notes"] = notes
    return out


def apply_review_bias(
    action: str,
    reason: str,
    size_hint: str,
    *,
    phase: str,
) -> tuple[str, str, str, bool, list[str]]:
    """Use historical phase hit-rate and adaptive size heat to shrink size.

    Soft DSS: low phase hit-rate demotes action and size hints but never hard-bans
    the stock pool (``stock_block`` stays False). Hard bans remain blacklist /
    panic / size-cap only.
    """
    notes: list[str] = []
    stock_block = False  # kept for API compat; no longer hard-blocks stocks
    hint = size_hint or ""
    act = action
    why = reason
    try:
        from market_desk.db import load_signals
        from market_desk.review import build_phase_hit_rates

        hits = build_phase_hit_rates(load_signals(limit=240))
    except Exception:
        hits = []
    row = next((h for h in hits if str(h.get("phase") or "") == str(phase or "")), None)
    if row and int(row.get("scored_n") or 0) >= 5 and row.get("hit_rate") is not None:
        rate = float(row["hit_rate"])
        scored = int(row["scored_n"])
        if rate < 35:
            hint = _join_hint(
                hint,
                f"相位{phase}命中{rate}%（n={scored}）偏低·缩仓加严（个股仍可观察）",
            )
            notes.append(f"复盘命中{rate}%·软降")
            if act == "可买入":
                act = "观察回踩"
                why = f"相位{phase}历史命中偏低({rate}%)，降级观察回踩：{why}"
        elif rate < 45:
            hint = _join_hint(hint, f"相位{phase}命中{rate}%一般，偏小仓")
            notes.append(f"复盘命中{rate}%偏弱")
    try:
        from market_desk.adapt import build_size_heat

        heat = build_size_heat()
        if heat.get("ok") and abs(float(heat.get("size_mult") or 1.0) - 1.0) > 0.02:
            hint = _join_hint(hint, str(heat.get("note") or f"仓位热度×{heat.get('size_mult')}"))
            notes.append(f"仓位热度×{heat.get('size_mult')}")
    except Exception:
        pass
    return act, why, hint, stock_block, notes


def apply_similar_gate(
    action: str,
    reason: str,
    size_hint: str,
    *,
    similar: dict[str, Any] | None,
) -> tuple[str, str, str, list[str], float]:
    """Apply similar-day cool/hot as soft size bias (does not demote action).

    Returns ``(action, reason, size_hint, notes, size_mult)``. Cool days shrink
    suggested risk size; hot days only add chase-caution copy. Sell-side
    ``sell_gate`` remains unchanged elsewhere.
    """
    notes: list[str] = []
    sim = similar or {}
    hint = size_hint or ""
    act = action
    why = reason
    size_mult = 1.0
    bias = str(sim.get("bias") or "").strip()
    gate = sim.get("gate")
    sell_gate = str(sim.get("sell_gate") or "")
    n = int(sim.get("n") or 0)
    if bias:
        hint = _join_hint(hint, bias)
    if n:
        notes.append(f"相似日n={n}")
    if gate == "cool" and n >= 2:
        # Prefer size over action demote; urgent cool presses a bit harder.
        size_mult = 0.65 if sell_gate == "urgent" else 0.75
        notes.append(f"相似日降温·缩仓×{size_mult}")
        hint = _join_hint(hint, f"相似日降温·建议仓位×{size_mult}")
    elif gate == "hot" and act == "可买入":
        hint = _join_hint(hint, "相似日偏升温仍防追高")
        notes.append("相似日升温防追")
    return act, why, hint, notes, size_mult
