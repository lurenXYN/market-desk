"""Lightweight similar-session lookup from stored daily snapshots."""

from __future__ import annotations

from typing import Any


def build_similar_days(
    *,
    phase: str,
    temperature: int | float | None,
    metrics: dict[str, Any] | None,
    history: list[dict[str, Any]] | None,
    limit: int = 6,
    mainline: str | None = None,
) -> dict[str, Any]:
    """Find recent same-phase days and summarize what happened next session.

    ``history`` is newest-first (as returned by ``load_daily``). The day at
    index ``i-1`` is treated as the following session when dates differ.

    Peers are soft-ranked by temperature / zt proximity and theme match; cool/hot
    votes are weight-averaged so sparse theme samples still help.
    """
    hist = list(history or [])
    if not phase or not hist:
        return {
            "phase": phase or "",
            "n": 0,
            "peers": [],
            "note": "相似日需要更多日级快照后再对照",
            "bias": "",
            "gate": None,
            "sell_gate": None,
        }

    try:
        temp = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temp = None
    zt_now = _num((metrics or {}).get("zt"))
    peers: list[dict[str, Any]] = []
    ml_now = str(mainline or "").strip()

    for i, day in enumerate(hist):
        if str(day.get("phase") or "") != str(phase):
            continue
        day_temp = _num(day.get("temperature"))
        if temp is not None and day_temp is not None and abs(day_temp - temp) > 14:
            continue
        day_zt = _num(day.get("zt"))
        if zt_now is not None and day_zt is not None and abs(day_zt - zt_now) > 25:
            continue
        nxt = hist[i - 1] if i > 0 else None
        if nxt is not None and str(nxt.get("trade_date") or "") <= str(day.get("trade_date") or ""):
            nxt = None
        day_ml = str(day.get("mainline") or "").strip()
        theme_hit = bool(_theme_match(ml_now, day_ml)) if ml_now and day_ml else False
        # Soft proximity score (higher = closer peer).
        score = 10.0
        if temp is not None and day_temp is not None:
            score -= min(8.0, abs(day_temp - temp) * 0.35)
        if zt_now is not None and day_zt is not None:
            score -= min(6.0, abs(day_zt - zt_now) * 0.12)
        if theme_hit:
            score += 4.0
        elif ml_now and day_ml:
            score -= 1.0
        peer = {
            "date": day.get("trade_date"),
            "temperature": day_temp,
            "zt": day_zt,
            "zb_rate": day.get("zb_rate"),
            "height": day.get("height"),
            "mainline": day_ml or None,
            "theme_match": theme_hit if ml_now and day_ml else None,
            "peer_score": round(score, 2),
            "next_date": None if not nxt else nxt.get("trade_date"),
            "next_phase": None if not nxt else nxt.get("phase"),
            "next_temperature": None if not nxt else _num(nxt.get("temperature")),
            "next_zt": None if not nxt else _num(nxt.get("zt")),
            "next_dt": None if not nxt else _num(nxt.get("dt")),
        }
        peers.append(peer)

    peers.sort(key=lambda p: float(p.get("peer_score") or 0), reverse=True)
    themed_n = sum(1 for p in peers if p.get("theme_match"))
    peers = peers[: max(limit, 1)]
    theme_note = f"；主题加权（同主题候选 {themed_n}）" if ml_now else ""

    # Weight-average next-day stats by peer_score.
    def _wavg(key: str) -> float | None:
        num = 0.0
        den = 0.0
        for p in peers:
            v = p.get(key)
            if v is None:
                continue
            w = max(0.5, float(p.get("peer_score") or 1.0))
            num += float(v) * w
            den += w
        return None if den <= 0 else round(num / den, 1)

    next_temps = [p["next_temperature"] for p in peers if p.get("next_temperature") is not None]
    cooler_w = 0.0
    hotter_w = 0.0
    for p in peers:
        a, b = p.get("temperature"), p.get("next_temperature")
        if a is None or b is None:
            continue
        w = max(0.5, float(p.get("peer_score") or 1.0))
        if b < a - 3:
            cooler_w += w
        elif b > a + 3:
            hotter_w += w
    cooler = int(round(cooler_w))
    hotter = int(round(hotter_w))

    bias = ""
    note = f"近端同相位(温度±14、涨停接近)对照 {len(peers)} 日" + theme_note
    gate = None
    sell_gate = None
    avg_t = _wavg("next_temperature")
    avg_z = _wavg("next_zt")
    avg_dt = _wavg("next_dt")
    if peers and next_temps:
        note += f"；次日温度均值 {avg_t}"
        if avg_z is not None:
            note += f"、涨停均值 {avg_z}"
        if avg_dt is not None:
            note += f"、跌停均值 {avg_dt}"
        if cooler_w >= hotter_w + 2 and cooler_w >= 2:
            bias = "相似日后偏降温，新开仓宜更小"
            gate = "cool"
            sell_gate = "urgent" if (avg_dt is not None and avg_dt >= 8) or cooler_w >= hotter_w + 3 else "watch"
        elif hotter_w >= cooler_w + 2 and hotter_w >= 2:
            bias = "相似日后偏升温，仍防追高"
            gate = "hot"
            sell_gate = "hold"
        else:
            bias = "相似日次日冷热互现，按回踩执行"
            gate = "mixed"
            sell_gate = "watch" if cooler_w > hotter_w else "hold"
    elif peers:
        note += "；尚缺次日样本"
        bias = "相似日样本不足，不作倾向"
        gate = None
        sell_gate = None
    else:
        note = f"近端暂无接近的「{phase}」日"
        bias = ""
        gate = None
        sell_gate = None

    return {
        "phase": phase,
        "n": len(peers),
        "peers": peers,
        "note": note,
        "bias": bias,
        "gate": gate,
        "sell_gate": sell_gate,
        "mainline": ml_now or None,
        "next_temp_avg": avg_t,
        "next_zt_avg": avg_z,
        "cooler_n": cooler,
        "hotter_n": hotter,
    }


def _theme_match(a: str, b: str) -> bool:
    """Return True when two mainline labels share a theme family."""
    try:
        from market_desk.mainline import same_theme

        return bool(same_theme(a, b) or a == b or a in b or b in a)
    except Exception:
        return a == b or a in b or b in a


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
