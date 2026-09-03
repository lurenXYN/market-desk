"""Sentiment phase and temperature scoring."""

from __future__ import annotations

from typing import Any

from market_desk.filters import is_limit_down, is_limit_up
from market_desk.numbers import median


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
        "premium": round(premium, 2),
        "breadth": round(breadth, 1),
        "amount_yi": round(amount / 1e8, 1),
        "leader": max(zt_pool, key=lambda x: int(x.get("boards") or 0), default=None),
    }


def score_temperature(m: dict[str, Any]) -> int:
    """Map market metrics to a 0-100 sentiment temperature."""
    temp = 0.0
    temp += min((m["zt"] or 0) / 80.0, 1.0) * 25
    temp += min((m["promotion"] or 0) / 50.0, 1.0) * 18
    temp += min((m["height"] or 0) / 8.0, 1.0) * 18
    temp += min((m["breadth"] or 0) / 100.0, 1.0) * 14
    temp += min(max(m["premium"] or 0, 0) / 8.0, 1.0) * 10
    temp += (1.0 - min((m["zb_rate"] or 0) / 100.0, 1.0)) * 8
    temp += (1.0 - min((m["dt"] or 0) / 40.0, 1.0)) * 7
    return int(round(max(0.0, min(temp, 100.0))))


def classify_phase(m: dict[str, Any], temperature: int) -> str:
    """Classify the session into panic / divergence / ferment / climax."""
    if m["dt"] >= 40 or temperature < 28 or (m["zt"] <= 8 and m["dt"] >= 15):
        return "恐慌"
    if m["height"] >= 6 and m["promotion"] >= 28 and m["zt"] >= 35:
        return "高潮"
    if temperature >= 72 and m["height"] >= 5:
        return "高潮"
    if m["zb_rate"] >= 48 or (m["zt"] < 18 and m["promotion"] < 12):
        return "分歧"
    if temperature >= 45:
        return "发酵"
    return "分歧"


def kpi_bars(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the horizontal KPI bars shown on the sentiment card."""
    return [
        {"key": "昨停溢价", "value": m["premium"], "unit": "%", "fill": _clip(m["premium"], 0, 8), "hue": "blue"},
        {"key": "晋级率", "value": m["promotion"], "unit": "%", "fill": _clip(m["promotion"], 0, 60), "hue": "green"},
        {"key": "高度", "value": m["height"], "unit": "板", "fill": _clip(m["height"], 0, 8), "hue": "orange"},
        {"key": "广度", "value": m["breadth"], "unit": "%", "fill": _clip(m["breadth"], 0, 70), "hue": "cyan"},
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
