"""What-If gate net-value audit: did each gate save money or cost money?

Population = buy cards whose price actually came to the plan on day0 (daily
low ≤ plan). Cards that never got there are not a gate decision and are only
counted. Of the touched cards:

* released — lit ready / probe at some refresh (the gates let it through);
* blocked — never lit; each blocker in its first gated touch shares the
  counterfactual result with weight 1/|blockers| so the ledger adds up.

Per gate: avoided % = −counterfactual net return. Positive = the gate saved
money (功臣); negative = it killed winners (损耗). The gate is also compared
with same-day released fills (excess), checked by bootstrap CI and a
first-half / second-half split before a verdict is issued.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from market_desk.config import (
    CF_BOOTSTRAP_N,
    CF_EDGE_PCT,
    CF_ENTRY_OFFSETS,
    CF_HOLD_DAYS,
    CF_MIN_DAYS,
    CF_MIN_N,
    CF_ROUNDTRIP_COST_PCT,
    CF_WINDOW_DAYS,
)
from market_desk.counterfactual.sim import simulate_cf_trade
from market_desk.counterfactual.trace import UNKNOWN_KEY, gate_kind, item_blockers
from market_desk.stats_tools import day_block_bootstrap_ci


def _wmean(pairs: list[tuple[float, float]]) -> float | None:
    """Weighted mean of (value, weight) pairs."""
    tw = sum(w for _, w in pairs)
    if tw <= 0:
        return None
    return sum(v * w for v, w in pairs) / tw


def bootstrap_ci(
    samples: list[tuple[str, float, float]],
    *,
    n: int = CF_BOOTSTRAP_N,
    seed: int = 7,
) -> tuple[float | None, float | None]:
    """90% CI of a gate's weighted mean avoided %, resampling trading days in blocks.

    Cards blocked on the same day share one market move and 3-day outcomes of
    neighbouring days overlap, so resampling single cards made the interval far
    too narrow; see ``stats_tools.day_block_bootstrap_ci``.

    Args:
        samples: ``(day, value, weight)`` rows.
        n: Resample count.
        seed: RNG seed so the panel does not flicker between refreshes.

    Returns:
        ``(low, high)``, or ``(None, None)`` below 3 samples or 2 trading days.
    """
    return day_block_bootstrap_ci(samples, n=int(n), seed=seed)


def _verdict(
    key: str,
    eff_n: float,
    days: int,
    mean_avoid: float | None,
    ci: tuple[float | None, float | None],
    stable: str,
    excess: float | None,
    excess_n: int,
) -> tuple[str, str]:
    """Return (verdict label, tone) for one gate row.

    Money first (bootstrap CI of avoided %), then selection: a gate that cost
    money while its blocked cards did no better than same-day released fills
    is labelled 随大盘 — the loss came from a rising tape, not bad picking.
    """
    if key == UNKNOWN_KEY:
        return "待查", "low"
    if eff_n < float(CF_MIN_N) or days < int(CF_MIN_DAYS) or mean_avoid is None:
        return "样本不足", "low"
    lo, hi = ci
    if lo is not None and lo > 0 and stable != "翻转":
        return "功臣", "good"
    if hi is not None and hi < 0 and stable != "翻转":
        if excess is not None and excess_n >= 3 and excess <= CF_EDGE_PCT * 0.5:
            return "随大盘", "mid"
        return "损耗", "bad"
    if mean_avoid >= CF_EDGE_PCT * 0.5:
        return "偏功臣", "mid"
    if mean_avoid <= -CF_EDGE_PCT * 0.5:
        return "偏损耗", "mid"
    return "无差异", "mid"


def _split_stability(samples: list[dict[str, Any]]) -> str:
    """Compare avoided-% sign in the earlier vs later half of trading days."""
    days = sorted({s["day"] for s in samples})
    if len(days) < 4:
        return "—"
    cut = days[len(days) // 2]
    a = _wmean([(s["avoid"], s["w"]) for s in samples if s["day"] < cut])
    b = _wmean([(s["avoid"], s["w"]) for s in samples if s["day"] >= cut])
    if a is None or b is None:
        return "—"
    if abs(a) < 0.3 or abs(b) < 0.3:
        return "弱"
    return "稳定" if (a > 0) == (b > 0) else "翻转"


def _row_blockers(row: dict[str, Any]) -> tuple[list[str], bool]:
    """Return (blockers, exact) for a touched-but-blocked card.

    ``exact`` = taken from the live trace (``payload.cf.first_blk``); otherwise
    rebuilt from stored payload flags (approximate: open-mute / auction blocks
    were not recorded before the trace existed).
    """
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    cf = payload.get("cf") if isinstance(payload.get("cf"), dict) else {}
    first = [str(x) for x in (cf.get("first_blk") or []) if str(x).strip()]
    if first:
        return first, True
    return item_blockers(payload, desk_source=payload.get("desk_source")), False


def _released(row: dict[str, Any]) -> bool:
    """Return True when the card lit ready or half-size probe at any refresh."""
    from market_desk.review.alerts import _ever_ready

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    cf = payload.get("cf") if isinstance(payload.get("cf"), dict) else {}
    return bool(
        _ever_ready(row)
        or payload.get("ever_probe")
        or payload.get("probe_ok")
        or int(cf.get("ready_n") or 0) > 0
    )


_CHANNEL_RANK = {"buy": 0, "buy_dragon": 1, "buy_side": 2, "buy_link": 3, "buy_indep": 4, "buy_trial": 5}


def _one_card_per_stock_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the same stock on the same day across channels into one decision.

    A lit card on any channel counts as released; otherwise the highest-priority
    channel's card (main > dragon > side > link > independent > trial) carries
    the blockers, so one stock-day is never double counted.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key = (str(r.get("trade_date") or "")[:10], str(r.get("code") or "").zfill(6))
        groups[key].append(r)
    out: list[dict[str, Any]] = []
    for items in groups.values():
        items.sort(key=lambda r: _CHANNEL_RANK.get(str(r.get("signal_type") or "").lower(), 9))
        lit = [r for r in items if _released(r)]
        out.append(lit[0] if lit else items[0])
    return out


def build_counterfactual_audit(
    rows: list[dict[str, Any]],
    klines: dict[str, Any],
    *,
    days: int | None = None,
    strict: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the gate net-value ledger over the last ``days`` signal days.

    Args:
        rows: Stored signal rows (any order; non-buy and skipped rows ignored).
        klines: ``{code: (dates, closes, ohlc)}`` daily bars.
        days: Window in distinct signal trade days (default ``CF_WINDOW_DAYS``).
        strict: Drop the weakest touch tier (早盘: early card + daily low only).
            On by default — that tier is look-ahead biased (the low often came
            before the card and the price then recovered).
        now: Clock override for tests.

    Returns:
        ``{"summary", "gates", "entry_sens", "headline"}`` ready for the review
        panel; ``gates`` sorted by |net avoided| descending.
    """
    from market_desk.review.signals import is_buy_signal

    span = max(1, int(days or CF_WINDOW_DAYS))
    buys = [
        r for r in rows or []
        if is_buy_signal(r.get("signal_type")) and not int(r.get("skipped") or 0)
    ]
    all_days = sorted({str(r.get("trade_date") or "")[:10] for r in buys if r.get("trade_date")})
    window_days = set(all_days[-span:])
    counts = {"cards": 0, "untouched": 0, "touch_unknown": 0, "weak_dropped": 0, "pending": 0, "no_bars": 0}
    tiers: dict[str, int] = defaultdict(int)
    released: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    touched_rows: list[tuple[dict[str, Any], Any, bool]] = []
    near_rows: list[tuple[dict[str, Any], Any]] = []
    for row in _one_card_per_stock_day([r for r in buys if str(r.get("trade_date") or "")[:10] in window_days]):
        day = str(row.get("trade_date") or "")[:10]
        counts["cards"] += 1
        code = str(row.get("code") or "").zfill(6)
        sim = simulate_cf_trade(row, klines.get(code), now=now)
        if sim is None:
            counts["no_bars"] += 1
            continue
        if not sim.get("filled"):
            counts["touch_unknown" if sim.get("touch") == "触达未知" else "untouched"] += 1
            if sim.get("touch") == "实测未到":
                near_rows.append((row, klines.get(code)))
            continue
        if strict and sim.get("touch") == "早盘":
            counts["weak_dropped"] += 1
            continue
        if sim.get("pending") or sim.get("r") is None:
            counts["pending"] += 1
            continue
        tiers[str(sim.get("touch") or "—")] += 1
        base = {
            "day": day, "code": code, "name": row.get("name") or code,
            "r": float(sim["r"]), "r1": sim.get("r1"), "stop_hit": bool(sim.get("stop_hit")),
            "tier": sim.get("touch"),
        }
        touched_rows.append((row, klines.get(code), _released(row)))
        if _released(row):
            released.append(base)
            continue
        blockers, exact = _row_blockers(row)
        base.update({"blockers": blockers, "exact": exact})
        blocked.append(base)

    rel_by_day: dict[str, list[float]] = defaultdict(list)
    for s in released:
        rel_by_day[s["day"]].append(s["r"])
    rel_day_mean = {d: sum(v) / len(v) for d, v in rel_by_day.items() if v}

    per_gate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in blocked:
        keys = s["blockers"] or [UNKNOWN_KEY]
        w = 1.0 / len(keys)
        for key in keys:
            per_gate[key].append({
                "day": s["day"], "r": s["r"], "avoid": -s["r"], "w": w,
                "sole": len(keys) == 1, "exact": s["exact"], "tier": s["tier"],
                "name": s["name"], "code": s["code"],
            })

    gates: list[dict[str, Any]] = []
    for key, samples in per_gate.items():
        pairs = [(x["avoid"], x["w"]) for x in samples]
        eff_n = sum(x["w"] for x in samples)
        mean_avoid = _wmean(pairs)
        n_days = len({x["day"] for x in samples})
        ci = bootstrap_ci([(x["day"], x["avoid"], x["w"]) for x in samples])
        stable = _split_stability(samples)
        sole = [x for x in samples if x["sole"]]
        excess_pairs = [
            (x["r"] - rel_day_mean[x["day"]], x["w"]) for x in samples if x["day"] in rel_day_mean
        ]
        excess = _wmean(excess_pairs)
        verdict, tone = _verdict(key, eff_n, n_days, mean_avoid, ci, stable, excess, len(excess_pairs))
        winners = sorted(samples, key=lambda x: x["r"], reverse=True)
        gates.append({
            "key": key,
            "kind": gate_kind(key),
            "n": len(samples),
            "eff_n": round(eff_n, 2),
            "days": n_days,
            "saved_n": sum(1 for x in samples if x["r"] <= -CF_EDGE_PCT),
            "miss_n": sum(1 for x in samples if x["r"] >= CF_EDGE_PCT),
            "flat_n": sum(1 for x in samples if abs(x["r"]) < CF_EDGE_PCT),
            "cf_mean": round(-mean_avoid, 2) if mean_avoid is not None else None,
            "avoid_mean": round(mean_avoid, 2) if mean_avoid is not None else None,
            "net": round(sum(a * w for a, w in pairs), 2),
            "sole_n": len(sole),
            "sole_net": round(sum(x["avoid"] for x in sole), 2),
            "vs_released": round(excess, 2) if excess is not None else None,
            "vs_released_n": len(excess_pairs),
            "ci_lo": ci[0],
            "ci_hi": ci[1],
            "stable": stable,
            "exact_share": round(sum(1 for x in samples if x["exact"]) / len(samples), 2),
            "strong_share": round(
                sum(1 for x in samples if x["tier"] in ("实测", "盘中", "收盘")) / len(samples), 2
            ),
            "verdict": verdict,
            "tone": tone,
            "top_miss": [
                {"name": x["name"], "day": x["day"], "r": round(x["r"], 2)}
                for x in winners[:3] if x["r"] >= CF_EDGE_PCT
            ],
        })
    gates.sort(key=lambda g: (-abs(g["net"]), g["key"]))

    def _side(samples: list[dict[str, Any]]) -> dict[str, Any]:
        rs = [s["r"] for s in samples]
        if not rs:
            return {"n": 0, "mean": None, "win": None, "sum": 0.0}
        return {
            "n": len(rs),
            "mean": round(sum(rs) / len(rs), 2),
            "win": round(sum(1 for v in rs if v > 0) / len(rs) * 100.0, 1),
            "sum": round(sum(rs), 2),
        }

    rel = _side(released)
    blk = _side(blocked)
    everything = _side(released + blocked)
    unknown_n = sum(1 for s in blocked if s["blockers"] == [UNKNOWN_KEY])
    exact_n = sum(1 for s in blocked if s["exact"])
    channel_net = round(sum(g["net"] for g in gates if g["kind"] == "通道"), 2)
    stock_net = round(sum(g["net"] for g in gates if g["kind"] != "通道"), 2)
    summary = {
        "window_days": len(window_days),
        "first_day": min(window_days) if window_days else None,
        "last_day": max(window_days) if window_days else None,
        **counts,
        "touched": len(released) + len(blocked),
        "released": rel,
        "blocked": blk,
        "all_open": everything,
        "gates_net": round(-blk["sum"], 2),
        "gates_avoid_mean": round(-blk["mean"], 2) if blk["mean"] is not None else None,
        "tiers": dict(tiers),
        "tier_means": {
            t: round(sum(s["r"] for s in grp) / len(grp), 2)
            for t in tiers
            for grp in [[s for s in released + blocked if s["tier"] == t]]
            if grp
        },
        "strict": bool(strict),
        "channel_net": channel_net,
        "stock_net": stock_net,
        "unknown_n": unknown_n,
        "exact_n": exact_n,
        "approx_n": len(blocked) - exact_n,
        "hold_days": int(CF_HOLD_DAYS),
        "cost_pct": float(CF_ROUNDTRIP_COST_PCT),
    }
    return {
        "summary": summary,
        "gates": gates,
        "entry_sens": build_entry_sensitivity(touched_rows, now=now),
        "near_miss": build_near_miss(near_rows, now=now),
        "stop_cmp": build_stop_compare(touched_rows, now=now),
        "headline": _headline(summary, gates),
    }


def build_entry_sensitivity(
    touched: list[tuple[dict[str, Any], Any, bool]],
    *,
    offsets: tuple[float, ...] = CF_ENTRY_OFFSETS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-run the same touched cards with the entry paid at plan + offset%.

    The stop stays anchored on plan, so each row isolates the cost of paying
    more than the plan price (chasing inside or beyond the entry band).

    Args:
        touched: ``(row, klines, released)`` for every audited touched card.
        offsets: Entry premiums over plan in percent (0 = exactly at plan).
        now: Clock override for tests.

    Returns:
        ``{"rows": [...], "note"}``; each row has ``off``, ``n``, ``mean``,
        ``win``, ``stop_rate``, ``delta`` (vs plan) and ``rel_mean`` (lit cards only).
    """
    rows: list[dict[str, Any]] = []
    base_mean: float | None = None
    for off in offsets:
        rs: list[float] = []
        rel: list[float] = []
        stops = 0
        for row, packed, lit in touched:
            sim = simulate_cf_trade(row, packed, entry_offset_pct=float(off), now=now)
            if not sim or not sim.get("filled") or sim.get("r") is None:
                continue
            rs.append(float(sim["r"]))
            stops += 1 if sim.get("stop_hit") else 0
            if lit:
                rel.append(float(sim["r"]))
        if not rs:
            continue
        mean = sum(rs) / len(rs)
        if base_mean is None:
            base_mean = mean
        rows.append({
            "off": float(off),
            "n": len(rs),
            "mean": round(mean, 2),
            "win": round(sum(1 for v in rs if v > 0) / len(rs) * 100.0, 1),
            "stop_rate": round(stops / len(rs) * 100.0, 1),
            "delta": round(mean - base_mean, 2),
            "rel_mean": round(sum(rel) / len(rel), 2) if rel else None,
            "rel_n": len(rel),
        })
    return {
        "rows": rows,
        "note": "同一批到价卡，只改入场价（计划价 + x%，限在当日最低~最高之间；低开到计划价下方仍按开盘），止损价按计划价固定",
    }


def build_near_miss(
    untouched: list[tuple[dict[str, Any], Any]],
    *,
    offsets: tuple[float, ...] = CF_ENTRY_OFFSETS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Cards that never reached plan but came within plan + x%: what a limit at plan + x% would have earned.

    Complements ``build_entry_sensitivity``: that table prices the extra paid on
    cards that did fill; this one prices the extra fills a higher limit buys.
    Only traced cards qualify — ``cf.lo_px`` is the lowest live price seen after
    the card existed, so a pre-card daily low never counts. Cumulative per
    offset: a card within +0.5% also fills at +1%, paying plan × 1.01.

    Returns:
        ``{"rows": [...], "traced", "note"}``; each row has ``off``, ``n``,
        ``mean``, ``win``, ``stop_rate`` (pending fills are skipped).
    """
    with_lo: list[tuple[dict[str, Any], Any, float, float]] = []
    for row, packed in untouched:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        cf = payload.get("cf") if isinstance(payload.get("cf"), dict) else {}
        plan = payload.get("plan_price") or payload.get("buy_price") or row.get("price")
        try:
            lo, plan_f = float(cf.get("lo_px")), float(plan)
        except (TypeError, ValueError):
            continue
        if lo > 0 and plan_f > 0:
            with_lo.append((row, packed, lo, plan_f))
    rows: list[dict[str, Any]] = []
    for off in offsets:
        if off <= 0:
            continue
        rs: list[float] = []
        stops = 0
        for row, packed, lo, plan in with_lo:
            limit = plan * (1.0 + float(off) / 100.0)
            if lo > limit:
                continue
            sim = simulate_cf_trade(row, packed, force_entry=limit, now=now)
            if not sim or not sim.get("filled") or sim.get("r") is None:
                continue
            rs.append(float(sim["r"]))
            stops += 1 if sim.get("stop_hit") else 0
        if not rs:
            continue
        rows.append({
            "off": float(off),
            "n": len(rs),
            "mean": round(sum(rs) / len(rs), 2),
            "win": round(sum(1 for v in rs if v > 0) / len(rs) * 100.0, 1),
            "stop_rate": round(stops / len(rs) * 100.0, 1),
        })
    return {
        "rows": rows,
        "traced": len(with_lo),
        "note": "没回踩到计划价、但出卡后实测最低价进过计划价 +x% 以内的卡，按 +x% 挂单成交；止损按计划价固定；累计口径",
    }


def _shadow_stop_for(row: dict[str, Any], packed: Any) -> tuple[float | None, bool]:
    """Return (shadow ATR stop, recorded) for one card.

    Prefers the live-recorded ``payload.stop_atr``; otherwise rebuilds it from
    daily bars completed before day0 (the same inputs the live refresh uses).
    """
    from market_desk.trend import daily_atr_pct
    from market_desk.verdict.common import _is_etf_code, atr_shadow_stop

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    try:
        rec = float(payload.get("stop_atr")) if payload.get("stop_atr") is not None else None
    except (TypeError, ValueError):
        rec = None
    if rec is not None and rec > 0:
        return rec, True
    plan = payload.get("plan_price") or payload.get("buy_price") or row.get("price")
    atr = None
    if packed:
        dates, closes, ohlc = packed
        day0 = str(row.get("trade_date") or "")[:10]
        if day0 in list(dates):
            i0 = list(dates).index(day0)
            atr = daily_atr_pct(list(ohlc.get("high") or [])[:i0], list(ohlc.get("low") or [])[:i0], list(closes)[:i0])
    etf = _is_etf_code(str(row.get("code") or "").zfill(6))
    return atr_shadow_stop(plan, payload.get("stop_price"), atr, etf), False


def build_stop_compare(
    touched: list[tuple[dict[str, Any], Any, bool]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare the live session-low stop with the shadow ATR stop on the same touched cards.

    Returns:
        ``{"rows": [...], "recorded_n", "note"}``; each row has ``label``, ``n``,
        ``mean``, ``win``, ``stop_rate``, ``gap_med`` (stop distance %) and
        ``r_mult_med`` (median net return ÷ stop distance; −1 = lost exactly the
        planned risk; median because sub-1% stops make the mean explode).
    """
    import copy

    variants: dict[str, list[dict[str, Any]]] = {"现状·日低止损": [], "影子·ATR 止损": []}
    recorded = 0
    for row, packed, _lit in touched:
        shadow, was_rec = _shadow_stop_for(row, packed)
        recorded += 1 if was_rec else 0
        alt = copy.deepcopy(row)
        if shadow is not None:
            alt.setdefault("payload", {})["stop_price"] = shadow
        for label, r in (("现状·日低止损", row), ("影子·ATR 止损", alt)):
            sim = simulate_cf_trade(r, packed, now=now)
            if not sim or not sim.get("filled") or sim.get("r") is None or not sim.get("stop"):
                continue
            gap = (float(sim["entry"]) - float(sim["stop"])) / float(sim["entry"]) * 100.0
            if gap <= 0:
                continue
            variants[label].append({"r": float(sim["r"]), "gap": gap, "stop": bool(sim.get("stop_hit"))})
    rows: list[dict[str, Any]] = []
    for label, xs in variants.items():
        if not xs:
            continue
        gaps = sorted(x["gap"] for x in xs)
        mults = sorted(x["r"] / x["gap"] for x in xs)
        rows.append({
            "label": label,
            "n": len(xs),
            "mean": round(sum(x["r"] for x in xs) / len(xs), 2),
            "win": round(sum(1 for x in xs if x["r"] > 0) / len(xs) * 100.0, 1),
            "stop_rate": round(sum(1 for x in xs if x["stop"]) / len(xs) * 100.0, 1),
            "gap_med": round(gaps[len(gaps) // 2], 2),
            "r_mult_med": round(mults[len(mults) // 2], 2),
        })
    from market_desk.config import (
        BUY_STOP_ATR_MULT,
        ETF_BUY_STOP_MAX_PCT,
        ETF_BUY_STOP_MIN_PCT,
        STOCK_BUY_STOP_MAX_PCT,
        STOCK_BUY_STOP_MIN_PCT,
    )

    return {
        "rows": rows,
        "recorded_n": recorded,
        "note": f"影子止损 = min(日低止损, 计划价 × (1 − clamp({BUY_STOP_ATR_MULT:g}×日线ATR, "
        f"个股 {STOCK_BUY_STOP_MIN_PCT:g}~{STOCK_BUY_STOP_MAX_PCT:g}% / ETF {ETF_BUY_STOP_MIN_PCT:g}~{ETF_BUY_STOP_MAX_PCT:g}%)))；"
        "只记录与对照，卡片、仓位、提醒仍用日低止损；无实测记录的卡按出卡前日线现算",
    }


def _headline(summary: dict[str, Any], gates: list[dict[str, Any]]) -> str:
    """One-line Chinese conclusion for the panel header."""
    touched = int(summary.get("touched") or 0)
    if touched == 0:
        return "窗口内没有到过计划价的买卡，暂无可审计样本"
    rel = summary.get("released") or {}
    blk = summary.get("blocked") or {}
    allo = summary.get("all_open") or {}
    avoid = summary.get("gates_avoid_mean")
    head = (
        f"到价 {touched} 张：放行 {rel.get('n', 0)} 张均 {_pct(rel.get('mean'))}，"
        f"被拦 {blk.get('n', 0)} 张若放行均 {_pct(blk.get('mean'))}，全放行均 {_pct(allo.get('mean'))}"
    )
    if avoid is not None:
        head += f"；闸门每拦一张{'少亏' if avoid >= 0 else '少赚'} {abs(float(avoid)):.2f}%"
    best = [g for g in gates if g["verdict"] == "功臣"]
    worst = [g for g in gates if g["verdict"] == "损耗"]
    tape = [g for g in gates if g["verdict"] == "随大盘"]
    if best:
        head += f"；功臣：{'、'.join(g['key'] for g in best[:2])}"
    if worst:
        head += f"；损耗：{'、'.join(g['key'] for g in worst[:2])}"
    if tape:
        head += f"；随大盘：{'、'.join(g['key'] for g in tape[:2])}"
    return head


def _pct(value: Any) -> str:
    """Format a signed percent or an em dash."""
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "—"
