"""Sentiment phase and temperature scoring."""

from __future__ import annotations

from collections import Counter
from typing import Any

from market_desk.filters import is_limit_down, is_limit_up
from market_desk.numbers import median


def _yesterday_boards(row: dict[str, Any]) -> int:
    """Return yesterday's consecutive board count for a yesterday-ZT row."""
    n = int(row.get("boards_yesterday") or row.get("boards") or 0)
    return n if n > 0 else 1


def rung_promotion_rates(
    yesterday_zt: list[dict[str, Any]],
    zt_pool: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute 1→2 and 2→3 promotion rates from yesterday board rungs."""
    today_boards = {x["code"]: int(x.get("boards") or 0) for x in zt_pool}
    out: dict[str, Any] = {}
    for from_n, to_n, key in ((1, 2, "promo_1_2"), (2, 3, "promo_2_3")):
        base = [x for x in yesterday_zt if _yesterday_boards(x) == from_n]
        if not base:
            out[key] = None
            out[f"{key}_base"] = 0
            out[f"{key}_ok"] = 0
            continue
        ok = sum(1 for x in base if today_boards.get(x["code"], 0) >= to_n)
        out[key] = round(ok / len(base) * 100.0, 1)
        out[f"{key}_base"] = len(base)
        out[f"{key}_ok"] = ok
    return out


def ladder_stats(zt_pool: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize limit-up ladder completeness (fill / gap / ge2)."""
    counts = Counter(max(1, int(x.get("boards") or 1)) for x in zt_pool)
    height = max(counts) if counts else 0
    ge2 = sum(n for b, n in counts.items() if b >= 2)
    span = min(height, 8) if height else 0
    filled = sum(1 for k in range(1, span + 1) if counts.get(k, 0) > 0) if span else 0
    missing = sum(1 for k in range(1, span) if counts.get(k, 0) == 0) if span else 0
    # High board with a missing mid rung = classic ladder break.
    ladder_gap = bool(height >= 4 and missing >= 1)
    ladder_fill = round(filled / span * 100.0, 1) if span else 0.0
    return {
        "ge2": ge2,
        "rungs_filled": filled,
        "rungs_span": span,
        "ladder_missing": missing,
        "ladder_gap": ladder_gap,
        "ladder_fill": ladder_fill,
    }


def build_market_metrics(
    quotes: list[dict[str, Any]],
    zt_pool: list[dict[str, Any]],
    zb_pool: list[dict[str, Any]],
    yesterday_zt: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute breadth, limit stats, promotion and premium from snapshots."""
    valid = [q for q in quotes if q.get("pct") is not None]
    ups = sum(1 for q in valid if (q["pct"] or 0) > 0)
    downs = sum(1 for q in valid if (q["pct"] or 0) < 0)
    flats = max(len(valid) - ups - downs, 0)
    dt_list = [q for q in valid if is_limit_down(q.get("name"), q.get("pct"))]
    zt_from_quotes = [q for q in valid if is_limit_up(q.get("name"), q.get("pct"))]
    zt_count = len(zt_pool) if zt_pool else len(zt_from_quotes)
    zb_count = len(zb_pool)
    dt_count = len(dt_list)
    denom = zt_count + zb_count
    zb_rate = (zb_count / denom) if denom else 0.0
    height = max((int(x.get("boards") or 1) for x in zt_pool), default=0)
    y_codes = {x["code"] for x in yesterday_zt}
    promoted = [x for x in zt_pool if x["code"] in y_codes]
    promotion = (len(promoted) / len(yesterday_zt) * 100.0) if yesterday_zt else 0.0
    y_pcts = [x["pct"] for x in yesterday_zt if x.get("pct") is not None]
    premium = median(y_pcts) or 0.0
    amount = sum(q.get("amount") or 0.0 for q in quotes)
    breadth = (ups / len(valid) * 100.0) if valid else 0.0
    big_drop = sum(1 for q in valid if (q["pct"] or 0) <= -5.0)
    explode_vals = [int(x.get("explode_count") or 0) for x in zt_pool]
    avg_explode = (sum(explode_vals) / len(explode_vals)) if explode_vals else 0.0
    late_n = sum(1 for x in zt_pool if _is_late_first_seal(x.get("first_seal")))
    late_seal_rate = (late_n / len(zt_pool) * 100.0) if zt_pool else 0.0
    rungs = rung_promotion_rates(yesterday_zt, zt_pool)
    ladder = ladder_stats(zt_pool)
    return {
        "sample": len(valid),
        "ups": ups,
        "downs": downs,
        "flats": flats,
        "zt": zt_count,
        "zb": zb_count,
        "dt": dt_count,
        "zb_rate": round(zb_rate * 100.0, 1),
        "height": height,
        "promotion": round(promotion, 1),
        "promo_1_2": rungs.get("promo_1_2"),
        "promo_2_3": rungs.get("promo_2_3"),
        "promo_1_2_base": rungs.get("promo_1_2_base", 0),
        "promo_2_3_base": rungs.get("promo_2_3_base", 0),
        "premium": round(premium, 2),
        "breadth": round(breadth, 1),
        "amount_yi": round(amount / 1e8, 1),
        "big_drop": big_drop,
        "avg_explode": round(avg_explode, 2),
        "late_seal_rate": round(late_seal_rate, 1),
        "ge2": ladder["ge2"],
        "rungs_filled": ladder["rungs_filled"],
        "rungs_span": ladder["rungs_span"],
        "ladder_missing": ladder["ladder_missing"],
        "ladder_gap": ladder["ladder_gap"],
        "ladder_fill": ladder["ladder_fill"],
        "leader": max(zt_pool, key=lambda x: int(x.get("boards") or 0), default=None),
    }


def is_late_first_seal(fbt: Any) -> bool:
    """Return True when first seal is after ~10:30 (weak open-seal quality)."""
    minutes = _first_seal_minutes(fbt)
    if minutes is None:
        return False
    # 10:30 = 630 minutes from midnight.
    return minutes >= 10 * 60 + 30


def _is_late_first_seal(fbt: Any) -> bool:
    """Backward-compatible alias for is_late_first_seal."""
    return is_late_first_seal(fbt)


def _first_seal_minutes(fbt: Any) -> int | None:
    """Parse East Money first-seal time into minutes from midnight."""
    if fbt is None:
        return None
    raw = str(fbt).strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        if len(digits) >= 6:
            hh = int(digits[:2])
            mm = int(digits[2:4])
        elif len(digits) == 5:
            hh = int(digits[0])
            mm = int(digits[1:3])
        elif len(digits) <= 4:
            # Seconds-from-midnight style.
            total = int(digits)
            hh = total // 3600
            mm = (total % 3600) // 60
        else:
            hh = int(digits[:2])
            mm = int(digits[2:4])
    except ValueError:
        return None
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def enrich_market_context(
    metrics: dict[str, Any],
    *,
    indices: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach amount percentile, index bias and volume/index gate flags."""
    m = dict(metrics or {})
    amounts = [
        float(h.get("amount_yi") or 0)
        for h in (history or [])
        if h.get("amount_yi") is not None and float(h.get("amount_yi") or 0) > 0
    ]
    # Prefer prior sessions so today's unfinished amount does not inflate the rank.
    today_amt = float(m.get("amount_yi") or 0)
    prior = amounts[1:] if amounts and abs(amounts[0] - today_amt) < 1e-6 else amounts
    amt_pctile = None
    if prior and today_amt > 0:
        below = sum(1 for a in prior if a <= today_amt)
        amt_pctile = round(100.0 * below / len(prior), 1)
    elif prior:
        amt_pctile = 0.0
    m["amount_pctile"] = amt_pctile
    m["thin_volume"] = bool(amt_pctile is not None and amt_pctile < 30)
    m["hot_volume"] = bool(amt_pctile is not None and amt_pctile >= 70)

    by_name = {(x.get("name") or ""): x for x in (indices or [])}
    by_code = {str(x.get("code") or ""): x for x in (indices or [])}
    hs = by_name.get("沪深300") or by_code.get("000300") or {}
    cyb = by_name.get("创业板指") or by_code.get("399006") or {}
    sh = by_name.get("上证指数") or by_code.get("000001") or {}
    hs_pct = hs.get("pct")
    cyb_pct = cyb.get("pct")
    sh_pct = sh.get("pct")
    m["hs300_pct"] = None if hs_pct is None else round(float(hs_pct), 2)
    m["cyb_pct"] = None if cyb_pct is None else round(float(cyb_pct), 2)
    m["sh_pct"] = None if sh_pct is None else round(float(sh_pct), 2)
    if hs_pct is not None and cyb_pct is not None:
        m["index_spread"] = round(float(cyb_pct) - float(hs_pct), 2)
    else:
        m["index_spread"] = None

    weak = False
    if hs_pct is not None and float(hs_pct) <= -1.2:
        weak = True
    if sh_pct is not None and float(sh_pct) <= -1.5:
        weak = True
    if hs_pct is not None and cyb_pct is not None and float(hs_pct) < 0 and float(cyb_pct) <= -2.0:
        weak = True
    strong = bool(
        hs_pct is not None
        and float(hs_pct) >= 0.5
        and (cyb_pct is None or float(cyb_pct) >= -0.3)
    )
    m["weak_index"] = weak
    m["strong_index"] = strong
    m["style_weak"] = False
    m["style_hot"] = False
    gates: list[str] = []
    if m["thin_volume"]:
        gates.append(f"成交额分位偏低({amt_pctile})")
    if m["hot_volume"]:
        gates.append(f"成交额分位偏高({amt_pctile})")
    if weak:
        gates.append("指数偏弱")
    if strong:
        gates.append("指数偏强")
    spread = m.get("index_spread")
    if spread is not None:
        if float(spread) <= -1.5:
            m["style_weak"] = True
            gates.append(f"成长风格偏弱({spread})")
        elif float(spread) >= 2.0:
            m["style_hot"] = True
            gates.append(f"成长风格偏热({spread})")
    if float(m.get("avg_explode") or 0) >= 1.5:
        gates.append(f"涨停炸板偏多({m.get('avg_explode')})")
    if float(m.get("late_seal_rate") or 0) >= 45:
        gates.append(f"晚封占比高({m.get('late_seal_rate')}%)")
    if int(m.get("big_drop") or 0) >= 80:
        gates.append(f"大面{m.get('big_drop')}家")
    m["context_gates"] = gates
    return m


def score_temperature(m: dict[str, Any]) -> int:
    """Map market metrics to a 0-100 sentiment temperature."""
    temp = 0.0
    temp += min((m["zt"] or 0) / 80.0, 1.0) * 25
    # Overall promotion plus rung splits (1→2 / 2→3) when samples exist.
    temp += min((m["promotion"] or 0) / 50.0, 1.0) * 14
    p12 = m.get("promo_1_2")
    p23 = m.get("promo_2_3")
    if p12 is not None:
        temp += min(float(p12) / 55.0, 1.0) * 4
    if p23 is not None:
        temp += min(float(p23) / 45.0, 1.0) * 4
    temp += min((m["height"] or 0) / 8.0, 1.0) * 16
    temp += min(float(m.get("ladder_fill") or 0) / 100.0, 1.0) * 3
    temp += min((m["breadth"] or 0) / 100.0, 1.0) * 14
    prem = float(m.get("premium") or 0)
    temp += min(max(prem, 0) / 8.0, 1.0) * 10
    if prem < 0:
        temp -= min(abs(prem) / 4.0, 1.0) * 4
    temp += (1.0 - min((m["zb_rate"] or 0) / 100.0, 1.0)) * 8
    temp += (1.0 - min((m["dt"] or 0) / 40.0, 1.0)) * 7
    # Soft adjustments from volume / index / cascade sells.
    if m.get("thin_volume"):
        temp -= 6
    elif m.get("hot_volume"):
        temp += 3
    if m.get("weak_index"):
        temp -= 5
    elif m.get("strong_index"):
        temp += 2
    if m.get("ladder_gap"):
        temp -= 4
    # Seal quality: repeated opens and late first seals cool the tape.
    avg_explode = float(m.get("avg_explode") or 0)
    if avg_explode >= 2.0:
        temp -= 5
    elif avg_explode >= 1.2:
        temp -= 3
    late_rate = float(m.get("late_seal_rate") or 0)
    if late_rate >= 55:
        temp -= 4
    elif late_rate >= 40:
        temp -= 2
    if m.get("style_weak"):
        temp -= 2
    big_drop = int(m.get("big_drop") or 0)
    if big_drop >= 120:
        temp -= 10
    elif big_drop >= 80:
        temp -= 6
    return int(round(max(0.0, min(temp, 100.0))))


def classify_phase(
    m: dict[str, Any],
    temperature: int,
    *,
    panic_temp: int | None = None,
    ferment_temp: int | None = None,
    climax_temp: int | None = None,
) -> str:
    """Classify the session into panic / divergence / ferment / climax.

    Temperature cutoffs default to config and may be overridden by runtime settings.
    """
    from market_desk import config as cfg

    panic_t = int(panic_temp if panic_temp is not None else cfg.PHASE_PANIC_TEMP)
    ferment_t = int(ferment_temp if ferment_temp is not None else cfg.PHASE_FERMENT_TEMP)
    climax_t = int(climax_temp if climax_temp is not None else cfg.PHASE_CLIMAX_TEMP)
    # Keep ordering panic < ferment <= climax.
    ferment_t = max(ferment_t, panic_t + 1)
    climax_t = max(climax_t, ferment_t)

    big_drop = int(m.get("big_drop") or 0)
    if (
        m["dt"] >= 40
        or temperature < panic_t
        or (m["zt"] <= 8 and m["dt"] >= 15)
        or (big_drop >= 120 and temperature < max(panic_t + 12, 40))
        or (m.get("weak_index") and m["dt"] >= 25)
    ):
        return "恐慌"

    promo = float(m.get("promotion") or 0)
    p23 = m.get("promo_2_3")
    # Strong overall promotion or solid 2→3 can support climax height path.
    strong_promo = promo >= 28 or (p23 is not None and float(p23) >= 35)
    climax = False
    if m["height"] >= 6 and strong_promo and m["zt"] >= 35:
        climax = True
    if temperature >= climax_t and m["height"] >= 5:
        climax = True
    # Block fake climax on thin volume, weak index, or broken ladder.
    if climax and (m.get("thin_volume") or m.get("weak_index") or m.get("ladder_gap")):
        return "发酵" if temperature >= ferment_t else "分歧"
    if climax:
        return "高潮"

    if m["zb_rate"] >= 48 or (m["zt"] < 18 and promo < 12):
        return "分歧"
    # High board with ladder gap and soft promotion → treat as divergence.
    if m.get("ladder_gap") and m["height"] >= 5 and promo < 22:
        return "分歧"
    if temperature >= ferment_t:
        return "发酵"
    return "分歧"


def kpi_bars(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the horizontal KPI bars shown on the sentiment card."""
    amt_fill = m.get("amount_pctile")
    if amt_fill is None:
        amt_fill = _clip(float(m.get("amount_yi") or 0), 0, 12000)
    p12 = m.get("promo_1_2")
    p23 = m.get("promo_2_3")
    return [
        {"key": "昨停溢价", "value": m["premium"], "unit": "%", "fill": _clip(m["premium"], 0, 8), "hue": "blue"},
        {"key": "晋级率", "value": m["promotion"], "unit": "%", "fill": _clip(m["promotion"], 0, 60), "hue": "green"},
        {
            "key": "晋级1→2",
            "value": p12 if p12 is not None else "—",
            "unit": "%" if p12 is not None else "",
            "fill": _clip(float(p12), 0, 60) if p12 is not None else 0.0,
            "hue": "green",
        },
        {
            "key": "晋级2→3",
            "value": p23 if p23 is not None else "—",
            "unit": "%" if p23 is not None else "",
            "fill": _clip(float(p23), 0, 55) if p23 is not None else 0.0,
            "hue": "green",
        },
        {"key": "高度", "value": m["height"], "unit": "板", "fill": _clip(m["height"], 0, 8), "hue": "orange"},
        {
            "key": "梯队",
            "value": m.get("ladder_fill") or 0,
            "unit": "%" + ("·断" if m.get("ladder_gap") else ""),
            "fill": _clip(float(m.get("ladder_fill") or 0), 0, 100),
            "hue": "orange",
        },
        {"key": "广度", "value": m["breadth"], "unit": "%", "fill": _clip(m["breadth"], 0, 70), "hue": "cyan"},
        {
            "key": "成交分位",
            "value": m.get("amount_pctile") if m.get("amount_pctile") is not None else m.get("amount_yi"),
            "unit": "%" if m.get("amount_pctile") is not None else "亿",
            "fill": float(amt_fill),
            "hue": "blue",
        },
        {
            "key": "炸板",
            "value": m["zb_rate"],
            "unit": "%",
            "fill": _clip(100 - m["zb_rate"], 0, 100),
            "inverse": True,
            "hue": "gold",
        },
        {
            "key": "跌停",
            "value": m["dt"],
            "unit": "家",
            "fill": _clip(100 - min(m["dt"] * 3, 100), 0, 100),
            "inverse": True,
            "hue": "violet",
        },
    ]


def _clip(value: float, lo: float, hi: float) -> float:
    if hi == lo:
        return 0.0
    return round(max(0.0, min((value - lo) / (hi - lo) * 100.0, 100.0)), 1)


def auction_from_quotes(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the 09:25 open using the first print of each name."""
    opens = [q["open_pct"] for q in quotes if q.get("open_pct") is not None]
    med = median(opens) or 0.0
    high_share = (
        sum(1 for x in opens if x >= 3.0) / len(opens) * 100.0 if opens else 0.0
    )
    if med >= 2.0:
        tone = "强势高开"
    elif med >= 0.6:
        tone = "正常 1~3%"
    elif med >= -0.4:
        tone = "平开"
    else:
        tone = "弱势低开"
    return {
        "median_open": round(med, 2),
        "high_open_share": round(high_share, 1),
        "sample": len(opens),
        "tone": tone,
    }


def board_status(pct: float, zt_n: int, leader_boards: int) -> str:
    """Label a sector card without turning it into a buy signal."""
    if leader_boards >= 3 or pct >= 4.5:
        return "尖峰禁追"
    if pct >= 1.5 and zt_n >= 2:
        return "确认中"
    if pct < 0:
        return "退潮"
    return "观察"


def ice_status(pct: float, dt_n: int, up_count: int, down_count: int) -> str:
    """Label a cold industry card. Ice is observation, never a buy."""
    if dt_n >= 2:
        return "传染预警"
    total = max(up_count + down_count, 1)
    down_share = down_count / total
    if pct <= -2.0 and down_share >= 0.65:
        return "冰点"
    if pct < 0:
        return "冷冻"
    return "相对最冷"


def board_cycle_tags(flags: dict[str, bool], ice: bool = False) -> list[dict[str, Any]]:
    """Return the screenshot cycle chips; only the highest-priority hit is on."""
    keys = (
        ["退潮", "冰点", "传染", "A杀", "修复"]
        if ice
        else ["点火", "一波", "天地", "反包", "二波", "加速", "滞涨", "A杀", "修复"]
    )
    priority = (
        ["传染", "冰点", "A杀", "修复", "退潮"]
        if ice
        else ["A杀", "加速", "滞涨", "二波", "反包", "天地", "一波", "点火", "修复"]
    )
    active = next((k for k in priority if flags.get(k)), None)
    return [{"k": k, "on": k == active} for k in keys]


def cycle_flags(
    pct: float,
    zt_n: int,
    leader_boards: int,
    dt_n: int = 0,
    ice: bool = False,
    contagion: bool = False,
    tiandi: bool = False,
    hist: list[dict[str, Any]] | None = None,
    giveback: int = 0,
) -> dict[str, bool]:
    """Approximate screenshot cycle states from today plus stored sector days."""
    prev = hist[-1] if hist else None
    peak = max((int(h.get("zt_n") or 0) for h in (hist or [])), default=0)
    prev_pct = (prev or {}).get("pct")
    prev_zt = int((prev or {}).get("zt_n") or 0)
    a_kill = False
    if prev and (prev_zt >= 2 or (prev_pct or 0) >= 2) and pct <= -1.5:
        a_kill = True
    if giveback >= 2 and (zt_n >= 1 or leader_boards >= 2):
        a_kill = True
    rebound = (
        prev is not None
        and (prev_pct or 0) <= -1.5
        and pct > (prev_pct or 0) + 0.8
        and pct > -1.2
    )
    return {
        "点火": (not ice) and zt_n >= 1 and leader_boards <= 1 and pct > 0.5,
        "一波": (not ice) and zt_n >= 2 and leader_boards <= 2 and pct > 0,
        "天地": (not ice) and tiandi,
        "反包": (not ice) and prev is not None and (prev_pct or 0) < 0 and pct >= 1.0 and zt_n >= 1,
        "二波": (
            (not ice)
            and peak >= 2
            and prev_zt < peak
            and zt_n >= 2
            and leader_boards <= 2
            and pct > 0
        ),
        "加速": (not ice) and leader_boards >= 3,
        "滞涨": (not ice) and leader_boards >= 2 and pct < 1.0,
        "A杀": a_kill,
        "修复": rebound or (ice and pct > -1.0 and not contagion),
        "退潮": ice or pct < 0,
        "冰点": ice and not contagion,
        "传染": contagion,
    }


def board_headline(status: str, flags: dict[str, bool], ice: bool) -> tuple[str, str]:
    """Return a screenshot-style title and color tone for a sector card."""
    if flags.get("传染"):
        return "传染预警 · 非买点", "orange"
    if ice:
        return "冰点观察 · 非买点", "blue"
    if flags.get("A杀"):
        return "A杀观察 · 修复≠反转", "blue"
    if flags.get("加速") or status == "尖峰禁追":
        return "启动确认 · 禁追", "orange"
    if flags.get("修复") or status == "退潮":
        return "退潮修复中", "blue"
    if status == "确认中" or flags.get("一波") or flags.get("二波"):
        return "主线确认中", "green"
    if flags.get("点火"):
        return "点火观察", "green"
    return "观察中", "slate"


def cluster_path(hist: list[dict[str, Any]] | None, zt_n: int) -> str:
    """Render concentration as a short zt_n path such as 2 → 6 → 7."""
    vals = [int(h.get("zt_n") or 0) for h in (hist or [])][-4:]
    vals.append(int(zt_n or 0))
    compact: list[int] = []
    for v in vals:
        if not compact or compact[-1] != v:
            compact.append(v)
    return " → ".join(str(v) for v in compact[-4:])


def spark_values(hist: list[dict[str, Any]] | None, zt_n: int) -> list[int]:
    """Build 0-100 bar heights from recent limit-up counts."""
    vals = [int(h.get("zt_n") or 0) for h in (hist or [])][-6:]
    vals.append(int(zt_n or 0))
    cap = max(vals + [4])
    return [int(round(v / cap * 100)) for v in vals]


def board_note(
    flags: dict[str, bool],
    leader_name: str,
    leader_boards: int,
    slot_name: str | None,
    ice: bool,
) -> str:
    """One-line event hint; never a buy instruction."""
    if flags.get("传染"):
        return "板块内多只靠近跌停 · 传染预警 · 非买点"
    if flags.get("A杀"):
        return "高位回吐 / 昨强今弱 · 修复≠反转 · 非买点"
    if ice:
        return "最冷行业观察，不是抄底"
    if flags.get("加速"):
        return f"总龙头 {leader_name or '—'} {leader_boards}板 · 尖峰禁追"
    if flags.get("滞涨"):
        return f"龙头 {leader_name or '—'} 有高度，板块涨幅没跟上"
    if slot_name:
        return f"总龙头 {leader_name or '—'} {leader_boards}板 · 卡位 {slot_name}"
    if flags.get("点火"):
        return "刚有首批涨停，未确认主线"
    return "只观察结构，不自动变成买点"
