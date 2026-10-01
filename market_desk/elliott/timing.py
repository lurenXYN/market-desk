"""Turn windows, Fibonacci day counts, wave clock, and scenario levels."""

from __future__ import annotations

from typing import Any


def _timing_summary(
    sid: str,
    ind: dict[str, Any],
    st: dict[str, Any],
) -> dict[str, Any]:
    """Attach MACD/RSI context that soft-confirms or challenges a scenario."""
    macd = ind.get("macd_snap") or {}
    rsi = ind.get("rsi_snap") or {}
    div = ind.get("divergence") or {}
    bias = "neutral"
    if sid.startswith("imp_up") or sid in ("corr_b",):
        bias = "bull"
    elif sid.startswith("imp_dn") or sid in ("corr_a", "corr_c"):
        bias = "bear"
    notes: list[str] = []
    weight = 50
    macd_div = (div.get("macd") or {}).get("kind")
    rsi_div = (div.get("rsi") or {}).get("kind")
    zone = rsi.get("zone")
    if bias == "bull":
        if macd_div == "底背离" or rsi_div == "底背离":
            notes.append("底背离支持偏多浪")
            weight += 18
        if zone == "超卖":
            notes.append("RSI 超卖，利于反抽/第2浪结束假设")
            weight += 10
        if macd.get("cross") == "金叉":
            notes.append("MACD 金叉")
            weight += 8
        if macd_div == "顶背离" or zone == "超买":
            notes.append("顶背离/超买对升浪末段示警")
            weight -= 12
    elif bias == "bear":
        if macd_div == "顶背离" or rsi_div == "顶背离":
            notes.append("顶背离支持偏空浪")
            weight += 18
        if zone == "超买":
            notes.append("RSI 超买，利于回落/B浪结束假设")
            weight += 10
        if macd.get("cross") == "死叉":
            notes.append("MACD 死叉")
            weight += 8
        if macd_div == "底背离" or zone == "超卖":
            notes.append("底背离/超卖对下跌末段示警")
            weight -= 12
    else:
        if zone in ("超买", "超卖"):
            notes.append(f"RSI {zone}，突破方向前先防假突破")
        if macd_div or rsi_div:
            notes.append(div.get("note") or "存在背离，宜等收盘确认")
    weight = max(0, min(100, weight))
    return {
        "macd": macd,
        "rsi": rsi,
        "divergence": div.get("note"),
        "weight": weight,
        "notes": notes or ["指标中性，以结构为主"],
    }


def _scenario_turns(
    sid: str,
    st: dict[str, Any],
    pivots: list[dict[str, Any]],
    series: list[dict[str, Any]],
    last: float,
    ind: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build scenario-specific time windows (dates only; no glued price targets).

    Time counts differ by wave family. Price Fib levels are attached separately
    via ``_scenario_levels`` and must NOT be read as 「该日必达价」.
    """
    from datetime import date as _date

    from market_desk.calendar import add_trading_days

    lp = st.get("last_pivot") or {}
    pp = st.get("prev_pivot") or {}
    last_bar_date = str((series[-1].get("date") if series else "") or "")[:10]
    pivot_date = str(lp.get("date") or "")[:10]
    try:
        asof = _date.fromisoformat(last_bar_date) if last_bar_date else _date.today()
    except ValueError:
        asof = _date.today()

    swing_bars = 8
    if lp.get("index") is not None and pp.get("index") is not None:
        swing_bars = max(3, abs(int(lp["index"]) - int(pp["index"])))

    # Wave-specific Fib session sets — different shapes → different clocks.
    fib_days = _fib_days_for_scenario(sid, swing_bars)
    timing = _timing_summary(sid, ind, st)
    div_note = (ind.get("divergence") or {}).get("note") or ""
    rsi_zone = ((ind.get("rsi_snap") or {}).get("zone")) or ""

    def _pack(anchor: str, days: int, source: str) -> dict[str, Any] | None:
        when = add_trading_days(anchor, days)
        if when is None or when <= asof:
            return None
        conf = 40
        conf_notes: list[str] = [f"本浪时钟 · {_wave_clock_note(sid)}"]
        if days in (8, 13, 21, 34, 55):
            conf += 10
        if source == "自今日":
            conf += 4
            conf_notes.append("近端日历窗")
        elif source == "自枢轴":
            conf_notes.append(f"锚点枢轴 {anchor}")
        if timing.get("weight", 50) >= 60:
            conf += 8
            conf_notes.append("MACD/RSI 同向")
        if timing.get("weight", 50) <= 40:
            conf -= 8
            conf_notes.append("指标逆势示警")
        if "背离" in div_note and days <= 13:
            conf += 6
            conf_notes.append(div_note)
        if rsi_zone in ("超买", "超卖") and days <= 8:
            conf += 4
            conf_notes.append(f"RSI {rsi_zone}")
        conf = max(0, min(100, conf))
        return {
            "date": when.isoformat(),
            "bars_ahead": days,
            "fib": f"{days} 个交易日",
            "anchor": anchor,
            "source": source,
            # Intentionally no price here — levels are separate (avoids「某日必达价」).
            "macd_hint": ((ind.get("macd_snap") or {}).get("cross")) or div_note or "—",
            "rsi_hint": (
                f"RSI {((ind.get('rsi_snap') or {}).get('value'))} · {rsi_zone}"
                if (ind.get("rsi_snap") or {}).get("value") is not None
                else rsi_zone or "—"
            ),
            "confidence": conf,
            "note": "；".join(conf_notes),
        }

    turns: list[dict[str, Any]] = []
    seen_dates: set[str] = set()

    # Prefer wave-relative pivot clock when still producing future dates.
    if pivot_date:
        for days in fib_days:
            row = _pack(pivot_date, days, "自枢轴")
            if not row or row["date"] in seen_dates:
                continue
            seen_dates.add(row["date"])
            turns.append(row)

    # Top up with near-term windows from last bar, still using THIS scenario's fib set.
    if len(turns) < 3 and last_bar_date:
        for days in fib_days:
            row = _pack(last_bar_date, days, "自今日")
            if not row or row["date"] in seen_dates:
                continue
            seen_dates.add(row["date"])
            turns.append(row)

    turns.sort(key=lambda x: (int(x.get("bars_ahead") or 0), str(x.get("date") or "")))
    return turns[:4]


def _fib_days_for_scenario(sid: str, swing_bars: int) -> list[int]:
    """Return Fibonacci trading-day offsets tuned to the wave hypothesis."""
    base: list[int]
    if sid in ("corr_b", "imp_dn_w2", "imp_up_w2"):
        # Bounce / pullback legs often resolve faster.
        base = [3, 5, 8, 13, 21]
    elif sid in ("corr_a", "imp_dn_w1", "imp_up_w1"):
        base = [5, 8, 13, 21]
    elif sid in ("corr_c", "imp_dn_w3", "imp_up_w3"):
        # Impulse / C legs: medium-long clocks.
        base = [8, 13, 21, 34]
    elif sid in ("imp_up_w4", "imp_dn_w4", "triangle", "complex"):
        # Consolidation burns time.
        base = [13, 21, 34, 55]
    elif sid in ("imp_up_w5", "imp_dn_w5"):
        base = [5, 8, 13, 21, 34]
    else:
        base = [5, 8, 13, 21, 34]
    # Blend one swing-length multiple unique to this structure.
    for r, bump in ((0.618, 0), (1.0, 1), (1.618, 2)):
        d = max(3, int(round(swing_bars * r)) + bump)
        if d not in base:
            base.append(d)
    # Tiny per-scenario offset so near-term clocks diverge across families.
    salt = {
        "corr_b": 0,
        "corr_a": 1,
        "corr_c": 2,
        "triangle": 2,
        "complex": 3,
        "imp_dn_w5": 1,
        "imp_dn_w2": 0,
        "imp_dn_w1": 1,
        "imp_dn_w3": 2,
        "imp_dn_w4": 3,
        "imp_up_w1": 0,
        "imp_up_w2": 0,
        "imp_up_w3": 1,
        "imp_up_w4": 2,
        "imp_up_w5": 1,
    }.get(sid, 0)
    return sorted({max(2, d + (salt if d >= 8 else 0)) for d in base})


def _wave_clock_note(sid: str) -> str:
    if sid in ("corr_b", "imp_dn_w2", "imp_up_w2"):
        return "反抽/回调浪偏短周期"
    if sid in ("triangle", "complex", "imp_up_w4", "imp_dn_w4"):
        return "盘整浪偏长周期"
    if sid in ("corr_c", "imp_dn_w3", "imp_up_w3"):
        return "主跌/主升浪中长周期"
    if sid in ("imp_up_w5", "imp_dn_w5"):
        return "末段浪关注背离窗口"
    return "通用斐波那契交易日"


def _scenario_levels(
    sid: str,
    st: dict[str, Any],
    last: float,
) -> list[dict[str, Any]]:
    """Structural Fib watch levels for one scenario (both above and below last).

    Always returns a mix of upside resistances and downside supports relative to
    the live price. Swing pivots alone are not enough when price has already
    slipped outside the last confirmed ZigZag range.
    """
    hh = st.get("last_high")
    hl = st.get("last_low")
    ph = st.get("prev_high")
    pl = st.get("prev_low")
    try:
        hi = float(hh) if hh is not None else float(last)
        lo = float(hl) if hl is not None else float(last)
    except (TypeError, ValueError):
        hi, lo = float(last), float(last)
    try:
        if ph is not None:
            hi = max(hi, float(ph))
        if pl is not None:
            lo = min(lo, float(pl))
    except (TypeError, ValueError):
        pass

    # Expand the working range so live price is inside; otherwise every Fib
    # print sits on one side of the market.
    hi = max(hi, float(last))
    lo = min(lo, float(last))
    span = abs(hi - lo)
    if span <= 0:
        span = abs(last) * 0.02 or 1.0

    raw: list[dict[str, Any]] = []

    def add(px: float, tag: str, role: str) -> None:
        if px is None or px <= 0:
            return
        raw.append({"price": round(float(px), 2), "tag": tag, "role": role})

    # Universal grid around live price (guarantees both directions).
    for r, lab in ((0.382, "38.2%"), (0.5, "50%"), (0.618, "61.8%")):
        add(last + span * r, f"上方 {lab}", "阻力")
        add(last - span * r, f"下方 {lab}", "支撑")
    add(last + span, "上方等长", "强阻力")
    add(last - span, "下方等长", "强支撑")

    # Scenario-flavored labels on the same geometry.
    if sid in ("corr_b", "imp_dn_w2"):
        add(lo + span * 0.382, "反抽 38.2%", "阻力/目标")
        add(lo + span * 0.618, "反抽 61.8%", "阻力/目标")
        add(hi, "摆动高点", "强阻力")
        add(lo, "摆动低点", "支撑/否决偏多")
        add(lo - span * 0.382, "跌破后再下 38.2%", "支撑")
    elif sid in ("imp_up_w2",):
        add(hi - span * 0.382, "回撤 38.2%", "支撑/回踩")
        add(hi - span * 0.618, "回撤 61.8%", "支撑/回踩")
        add(lo, "摆动低点", "升浪否决")
        add(hi, "摆动高点", "阻力")
        add(hi + span * 0.382, "过后再上 38.2%", "阻力/目标")
    elif sid in ("corr_a", "corr_c", "imp_dn_w1", "imp_dn_w3", "imp_dn_w5"):
        add(hi - span * 0.382, "下探参照 38.2%", "支撑/目标")
        add(hi - span * 0.618, "下探参照 61.8%", "支撑/目标")
        add(lo, "摆动低点", "支撑")
        add(lo - span * 0.382, "延伸 1.382", "支撑/目标")
        add(lo - span * 0.618, "延伸 1.618", "强支撑")
        add(hi, "近端高点", "反弹否决偏空")
    elif sid in ("imp_up_w1", "imp_up_w3", "imp_up_w5"):
        add(lo + span * 0.382, "推进 38.2%", "阻力/目标")
        add(lo + span * 0.618, "推进 61.8%", "阻力/目标")
        add(hi, "摆动高点", "阻力")
        add(hi + span * 0.382, "延伸 1.382", "阻力/目标")
        add(lo, "近端低点", "升浪否决")
        add(lo - span * 0.382, "失守后再下", "支撑")
    else:
        # Triangle / complex / w4: box around expanded range.
        mid = (hi + lo) / 2.0
        add(lo, "箱体下沿", "支撑")
        add(mid, "箱体中轴", "衡轴")
        add(hi, "箱体上沿", "阻力")
        add(lo - span * 0.382, "箱破下沿", "支撑")
        add(hi + span * 0.382, "箱破上沿", "阻力")

    # Deduplicate by rounded price.
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in raw:
        px = float(row["price"])
        key = int(round(px * 100))
        if key in seen or px <= 0:
            continue
        seen.add(key)
        vs = round((px - last) / last * 100.0, 2) if last else None
        out.append(
            {
                **row,
                "vs_last_pct": vs,
                "side": "up" if (vs or 0) > 0 else ("down" if (vs or 0) < 0 else "flat"),
                "note": "结构观察位，不是某变盘日的必达价",
            }
        )

    ups = [x for x in out if float(x.get("vs_last_pct") or 0) > 0.15]
    downs = [x for x in out if float(x.get("vs_last_pct") or 0) < -0.15]
    flats = [x for x in out if abs(float(x.get("vs_last_pct") or 0)) <= 0.15]
    ups.sort(key=lambda x: float(x.get("vs_last_pct") or 0))
    downs.sort(key=lambda x: float(x.get("vs_last_pct") or 0), reverse=True)
    # Keep nearest 3 above + nearest 3 below (+ any flat).
    picked = downs[:3] + flats[:1] + ups[:3]
    picked.sort(key=lambda x: float(x.get("price") or 0), reverse=True)
    return picked[:6]
