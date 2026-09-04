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
) -> dict[str, Any]:
    """Find recent same-phase days and summarize what happened next session.

    ``history`` is newest-first (as returned by ``load_daily``). The day at
    index ``i-1`` is treated as the following session when dates differ.
    """
    hist = list(history or [])
    if not phase or not hist:
        return {
            "phase": phase or "",
            "n": 0,
            "peers": [],
            "note": "相似日需要更多日级快照后再对照",
            "bias": "",
        }

    try:
        temp = float(temperature) if temperature is not None else None
    except (TypeError, ValueError):
        temp = None
    zt_now = _num((metrics or {}).get("zt"))
    peers: list[dict[str, Any]] = []

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
        # Skip if "next" is not actually a later calendar day.
        if nxt is not None and str(nxt.get("trade_date") or "") <= str(day.get("trade_date") or ""):
            nxt = None
        peer = {
            "date": day.get("trade_date"),
            "temperature": day_temp,
            "zt": day_zt,
            "zb_rate": day.get("zb_rate"),
            "height": day.get("height"),
            "next_date": None if not nxt else nxt.get("trade_date"),
            "next_phase": None if not nxt else nxt.get("phase"),
            "next_temperature": None if not nxt else _num(nxt.get("temperature")),
            "next_zt": None if not nxt else _num(nxt.get("zt")),
            "next_dt": None if not nxt else _num(nxt.get("dt")),
        }
        peers.append(peer)
        if len(peers) >= limit:
            break

    next_temps = [p["next_temperature"] for p in peers if p.get("next_temperature") is not None]
    next_zts = [p["next_zt"] for p in peers if p.get("next_zt") is not None]
    cooler = 0
    hotter = 0
    for p in peers:
        a, b = p.get("temperature"), p.get("next_temperature")
        if a is None or b is None:
            continue
        if b < a - 3:
            cooler += 1
        elif b > a + 3:
            hotter += 1

    bias = ""
    note = f"近端同相位(温度±14、涨停接近)对照 {len(peers)} 日"
    if peers and next_temps:
        avg_t = round(sum(next_temps) / len(next_temps), 1)
        avg_z = None if not next_zts else round(sum(next_zts) / len(next_zts), 1)
        note += f"；次日温度均值 {avg_t}"
        if avg_z is not None:
            note += f"、涨停均值 {avg_z}"
        if cooler >= hotter + 2 and cooler >= 2:
            bias = "相似日后偏降温，新开仓宜更小"
        elif hotter >= cooler + 2 and hotter >= 2:
            bias = "相似日后偏升温，仍防追高"
        else:
            bias = "相似日次日冷热互现，按回踩执行"
    elif peers:
        note += "；尚缺次日样本"
        bias = "相似日样本不足，不作倾向"
    else:
        note = f"近端暂无接近的「{phase}」日"
        bias = ""

    return {
        "phase": phase,
        "n": len(peers),
        "peers": peers,
        "note": note,
        "bias": bias,
        "next_temp_avg": None if not next_temps else round(sum(next_temps) / len(next_temps), 1),
        "next_zt_avg": None if not next_zts else round(sum(next_zts) / len(next_zts), 1),
        "cooler_n": cooler,
        "hotter_n": hotter,
    }


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
