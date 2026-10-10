"""Side-by-side pick checklist for a handful of candidate tickers.

Each factor is tagged hard (execution blocker), risk, plus or info; the
checklist orders the comparison. The legacy 0–100 score is still computed with
frozen points and stored as ``pick0`` so ``pick_self_audit`` can judge it out of
sample. Display only — never gates or rewrites a signal.
"""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    CV_SMALL_CAP_YI,
    PICK_BASE_SCORE,
    PICK_CV_CHIP_HIGH_PTS,
    PICK_CV_IVOL_HIGH_PTS,
    PICK_CV_IVOL_LOW_PTS,
    PICK_CV_SMALL_CAP_PTS,
    PICK_CV_VOL_FADE_PTS,
    PICK_CV_VOL_SHRINK_PTS,
    PICK_CV_VOL_SPIKE_PTS,
    PICK_GAP_WATCH_PCT,
    PICK_HIST_MAX_ADJ,
    PICK_HIST_MIN_DAYS,
    PICK_HIST_MIN_N,
    PICK_HIST_PP_TO_PTS,
    PICK_LHB_LOOKBACK_DAYS,
    PICK_ZT_NONE_PTS,
)
from market_desk.numbers import num
from market_desk.stats_tools import safe_t, spearman

SOURCE_LABELS: dict[str, str] = {
    "buy": "主线回踩",
    "buy_side": "支线回踩",
    "buy_link": "联动回踩",
    "buy_trial": "自选试探",
    "buy_indep": "独立人气",
    "buy_dragon": "龙头",
}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return the signal payload dict (empty for manual rows)."""
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def _is_buy(row: dict[str, Any]) -> bool:
    """Return True for stored buy-family signals (manual rows excluded)."""
    from market_desk.review import is_buy_signal

    return is_buy_signal(row.get("signal_type"))


def history_buckets(row: dict[str, Any], *, live: bool) -> dict[str, str]:
    """Map a row onto history-comparable buckets.

    ``live`` reads the freshly enriched fields (candidate rows); otherwise the
    signal-time payload is used (stored history rows).
    """
    payload = _payload(row)
    out: dict[str, str] = {}
    if live and (row.get("trend_ok") or row.get("trend_down") or row.get("daily_trend")):
        up, down = bool(row.get("trend_ok")), bool(row.get("trend_down"))
        out["trend"] = "up" if up else ("down" if down else "flat")
    elif not live and ("trend_ok" in payload or "trend_down" in payload):
        up, down = bool(payload.get("trend_ok")), bool(payload.get("trend_down"))
        out["trend"] = "up" if up else ("down" if down else "flat")
    match = row.get("board_match") if live else payload.get("board_match")
    if match is True:
        out["board"] = "match"
    elif match is False:
        out["board"] = "off"
    if _is_buy(row):
        out["source"] = str(row.get("signal_type") or "")
        fails = payload.get("confirm_fail")
        if fails is not None:
            out["gate"] = "fail" if fails else "clean"
        # ``ready`` is overwritten on every refresh; ever_ready / first_ready_at mean "lit at least once".
        lit = payload.get("ever_ready") or payload.get("first_ready_at") or int(row.get("ready") or 0)
        out["ready"] = "1" if lit else "0"
    cv = row.get("cv") if live else payload.get("cv")
    if isinstance(cv, dict):
        for key in ("chip_pos", "vol1", "vol3", "ivol", "cap"):
            if cv.get(key):
                out[key] = str(cv[key])
    return out


def _day_balanced(by_day: dict[str, list[int]]) -> float | None:
    """Mean of per-day win rates so one hot/cold session cannot dominate a bucket."""
    rates = [w / n for n, w in by_day.values() if n]
    return round(100.0 * sum(rates) / len(rates), 1) if rates else None


def build_history_stats(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Aggregate day-balanced 3-day win rate per (factor, bucket) over scored buys."""
    base_n = 0
    base_days: dict[str, list[int]] = {}
    acc: dict[tuple[str, str], dict[str, list[int]]] = {}
    for row in rows or []:
        if not _is_buy(row):
            continue
        d3 = num(row.get("outcome_day3_pct"))
        if d3 is None:
            continue
        win = 1 if d3 > 0 else 0
        day = str(row.get("trade_date") or row.get("signaled_at") or "")[:10]
        base_n += 1
        slot = base_days.setdefault(day, [0, 0])
        slot[0] += 1
        slot[1] += win
        for key, bucket in history_buckets(row, live=False).items():
            per_day = acc.setdefault((key, bucket), {})
            s = per_day.setdefault(day, [0, 0])
            s[0] += 1
            s[1] += win
    buckets = {
        f"{k}:{b}": {
            "n": sum(n for n, _ in per_day.values()),
            "days": len(per_day),
            "win3": _day_balanced(per_day),
        }
        for (k, b), per_day in acc.items()
        if per_day
    }
    return {"n": base_n, "base_win3": _day_balanced(base_days), "buckets": buckets}


def _hist_adj(key: str, bucket: str | None, stats: dict[str, Any]) -> tuple[float, str]:
    """Return (points, note) nudging a factor by its bucket's win-rate edge."""
    if not bucket:
        return 0.0, ""
    base = stats.get("base_win3")
    hit = (stats.get("buckets") or {}).get(f"{key}:{bucket}")
    if base is None or not hit or hit.get("win3") is None:
        return 0.0, ""
    note = f"历史三日胜率 {hit['win3']}%（{hit['n']} 条 / {hit['days']} 天，整体 {base}%）"
    if float(PICK_HIST_PP_TO_PTS) == 0.0:
        return 0.0, note + "·仅参考不计分"
    if int(hit["n"]) < int(PICK_HIST_MIN_N) or int(hit["days"]) < int(PICK_HIST_MIN_DAYS):
        return 0.0, note + "·样本少不计"
    raw = (float(hit["win3"]) - float(base)) * float(PICK_HIST_PP_TO_PTS)
    cap = float(PICK_HIST_MAX_ADJ)
    return max(-cap, min(cap, raw)), note


HARD_KEYS = frozenset({"gate", "pct"})
RISK_WATCH_KEYS = frozenset({"gap", "lhb"})


def factor_kind(f: dict[str, Any]) -> str:
    """Classify a factor row as ``hard`` / ``risk`` / ``plus`` / ``info``.

    Works on stored ``pick0`` factors too (older rows carry no ``kind``): hard
    keys are execution blockers, watch-only tags count as risk, everything else
    follows the sign of its points.
    """
    kind = f.get("kind")
    if kind in ("hard", "risk", "plus", "info"):
        return str(kind)
    key = str(f.get("key") or "")
    pts = num(f.get("points")) or 0.0
    if key in HARD_KEYS and pts < 0:
        return "hard"
    if key in RISK_WATCH_KEYS or pts < 0:
        return "risk"
    return "plus" if pts > 0 else "info"


def _factor(key: str, label: str, points: float, detail: str, hist: str = "") -> dict[str, Any]:
    """Pack one scored factor row."""
    row = {
        "key": key,
        "label": label,
        "points": round(points, 1),
        "detail": detail,
        "hist": hist,
    }
    row["kind"] = factor_kind(row)
    return row


def pick_check(factors: list[dict[str, Any]] | None, position: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize factors as a hard / risk / plus checklist (the primary pick display).

    Args:
        factors: Factor rows from ``score_pick`` or a stored ``pick0``.
        position: Optional live position status; stop / chase count as hard.

    Returns:
        ``{hard, risk, plus, label, tone, rank}`` where ``rank`` sorts best first.
    """
    hard: list[str] = []
    risk: list[str] = []
    plus: list[str] = []
    for f in factors or []:
        kind = factor_kind(f)
        text = str(f.get("label") or f.get("key") or "")
        if kind == "hard":
            hard.append(text)
        elif kind == "risk":
            risk.append(text)
        elif kind == "plus":
            plus.append(text)
    if position and position.get("kind") in ("stop", "chase"):
        hard.insert(0, str(position.get("label") or "位置"))
    if hard:
        label, tone = "硬伤", "bad"
    elif risk:
        label, tone = f"风险{len(risk)}", "warn"
    else:
        label, tone = "无风险", ("good" if plus else "mid")
    return {
        "hard": hard,
        "risk": risk,
        "plus": plus,
        "label": label,
        "tone": tone,
        "rank": [len(hard), len(risk), -len(plus)],
    }


def _position_status(row: dict[str, Any]) -> dict[str, Any] | None:
    """Describe where the live price sits against the plan band (execution status, not scored).

    Kept out of the score: it says whether the buy is actionable right now, not
    whether the stock is a good pick, and its points used to swamp every other factor.
    Returns ``{kind, label, tone, detail}`` or None for manual rows / missing prices.
    """
    flags = list(row.get("price_flags") or [])
    last = num(row.get("live_last"))
    plan = num(row.get("price"))
    if row.get("manual") or plan is None or last is None:
        return None
    above = num(row.get("above_plan_pct"))
    if "stop_hit" in flags:
        return {"kind": "stop", "label": "到止损", "tone": "bad", "detail": "现价已到止损带，今天不买"}
    if "chase_hit" in flags:
        return {"kind": "chase", "label": "过不追", "tone": "bad", "detail": "已过不追价，放弃"}
    if above is not None:
        return {"kind": "above", "label": f"等回踩 +{above:.1f}%", "tone": "warn",
                "detail": f"高于计划价 +{above:.1f}%，挂计划价等回踩"}
    if "in_band" in flags or "near_wait" in flags:
        return {"kind": "band", "label": "回踩带", "tone": "good", "detail": "在回踩带 / 建议价附近，可挂单"}
    if last <= plan:
        return {"kind": "below", "label": "低于计划价", "tone": "good",
                "detail": f"低于计划价（现 {last} / 计划 {plan}），先看站稳"}
    return {"kind": "near", "label": "贴近计划价", "tone": "mid", "detail": "略高于计划价（<1%）"}


def _cv_factors(
    row: dict[str, Any], buckets: dict[str, str], stats: dict[str, Any]
) -> list[dict[str, Any]]:
    """Score chip position and prior-day volume from the row's ``cv`` context."""
    cv = row.get("cv") if isinstance(row.get("cv"), dict) else {}
    out: list[dict[str, Any]] = []
    chip = buckets.get("chip_pos")
    if chip:
        profit, vc = num(cv.get("profit")), num(cv.get("vs_cost"))
        text = f"获利盘 {profit:.0f}%，距平均成本 {vc:+.1f}%" if profit is not None and vc is not None else "筹码位置"
        adj, note = _hist_adj("chip_pos", chip, stats)
        if chip == "high":
            out.append(_factor("chip", "筹码位置", float(PICK_CV_CHIP_HIGH_PTS) + adj, text + "，位置偏高", note))
        else:
            out.append(_factor("chip", "筹码位置", adj, text, note))
    vol1 = buckets.get("vol1")
    vr = num(cv.get("vr_prev"))
    if vol1 == "shrink":
        adj, note = _hist_adj("vol1", vol1, stats)
        out.append(_factor("vol1", "昨日量能", float(PICK_CV_VOL_SHRINK_PTS) + adj, f"昨缩量（量比 {vr}）", note))
    elif vol1 == "spike":
        adj, note = _hist_adj("vol1", vol1, stats)
        out.append(_factor("vol1", "昨日量能", float(PICK_CV_VOL_SPIKE_PTS) + adj, f"昨巨量（量比 {vr}）", note))
    if buckets.get("vol3") == "fade":
        adj, note = _hist_adj("vol3", "fade", stats)
        out.append(_factor(
            "vol3", "量能趋势", float(PICK_CV_VOL_FADE_PTS) + adj,
            f"近 3 日连续缩量（{cv.get('vol_trend3')} 倍）", note,
        ))
    ivol = buckets.get("ivol")
    if ivol in ("high", "low"):
        adj, note = _hist_adj("ivol", ivol, stats)
        pts, text = (
            (PICK_CV_IVOL_HIGH_PTS, "股性躁") if ivol == "high" else (PICK_CV_IVOL_LOW_PTS, "走势稳")
        )
        out.append(_factor(
            "ivol", "特质波动", float(pts) + adj,
            f"{text}（近 20 日扣掉创业板指后日波动 {cv.get('ivol20')}%）", note,
        ))
    if buckets.get("cap") == "small":
        adj, note = _hist_adj("cap", "small", stats)
        out.append(_factor(
            "cap", "流通市值", float(PICK_CV_SMALL_CAP_PTS) + adj,
            f"小盘（昨日流通市值约 {cv.get('float_cap_yi')} 亿，≤{CV_SMALL_CAP_YI:.0f} 亿）", note,
        ))
    return out


def _watch_factors(row: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Watch-only tags (0 points): today's open gap and a recent billboard appearance."""
    out: list[dict[str, Any]] = []
    if kind == "etf":
        return out
    gap = num(row.get("open_gap_pct"))
    if gap is not None and gap >= float(PICK_GAP_WATCH_PCT):
        out.append(_factor("gap", "开盘", 0.0, f"高开 +{gap:.1f}%（观察项，不计分：高开后回踩的票三日偏弱，样本待确认）"))
    lhb = row.get("lhb_recent") if isinstance(row.get("lhb_recent"), dict) else None
    if lhb:
        day = str(lhb.get("date") or "")[5:10]
        net = num(lhb.get("net_yi"))
        net_txt = f"，净买 {net:+.2f} 亿" if net is not None else ""
        out.append(_factor(
            "lhb", "龙虎榜", 0.0,
            f"近 {int(PICK_LHB_LOOKBACK_DAYS)} 日上榜（{day}{net_txt}）· 观察项，不计分：上榜后回踩三日偏弱",
        ))
    return out


def score_pick(row: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Score one candidate row; return total, grade and per-factor breakdown."""
    factors: list[dict[str, Any]] = []
    buckets = history_buckets(row, live=True)
    kind = str(row.get("kind") or "stock")

    pos = _position_status(row)

    # Trend / board / ready carry no rule points: the 2026-09 audit found their
    # classic direction reversed on stored pullback buys; only the (optional) history nudge applies.
    trend = buckets.get("trend")
    adj, note = _hist_adj("trend", trend, stats)
    if trend:
        label = {"up": "日线上升", "down": "日线下降", "flat": "日线震荡"}[trend]
        factors.append(_factor("trend", "日线趋势", adj, label, note))

    board = buckets.get("board")
    if board:
        adj, note = _hist_adj("board", board, stats)
        of = str(row.get("vs_mainline_of") or "主线")
        text = f"贴合{of}" if board == "match" else f"偏离{of}"
        factors.append(_factor("board", "板块", adj, text, note))

    src = buckets.get("source")
    if src:
        adj, note = _hist_adj("source", src, stats)
        factors.append(_factor("source", "信号来源", adj, SOURCE_LABELS.get(src, src), note))

    gate = buckets.get("gate")
    if gate == "fail":
        adj, note = _hist_adj("gate", "fail", stats)
        fails = _payload(row).get("confirm_fail") or []
        text = "闸门未过：" + "、".join(str(f) for f in fails[:2]) if fails else "闸门未过"
        factors.append(_factor("gate", "确认闸门", -10.0 + adj, text, note))

    if buckets.get("ready") == "1":
        adj, note = _hist_adj("ready", "1", stats)
        factors.append(_factor("ready", "可买入", adj, "作战板亮过可买", note))

    pct = num(row.get("live_pct"))
    if pct is not None and pct >= 9.5:
        factors.append(_factor("pct", "当日涨幅", -12, f"+{pct:.1f}% 接近涨停，难按计划买"))

    zt = row.get("zt_ytd")
    if kind != "etf" and zt is not None and int(zt) == 0:
        factors.append(_factor("zt", "股性", float(PICK_ZT_NONE_PTS), "年内无涨停"))

    chg = num(row.get("holder_chg_pct"))
    if chg is not None:
        if chg <= -5:
            factors.append(_factor("holder", "股东户数", 4, f"环比 {chg:.1f}%，筹码集中"))
        elif chg >= 10:
            factors.append(_factor("holder", "股东户数", -4, f"环比 +{chg:.1f}%，筹码分散"))

    if row.get("ma_fan"):
        factors.append(_factor("mafan", "均线", 5, "均线粘连后向上发散"))

    factors.extend(_cv_factors(row, buckets, stats))
    factors.extend(_watch_factors(row, kind))

    total = float(PICK_BASE_SCORE) + sum(float(f["points"]) for f in factors)
    score = int(round(max(0.0, min(100.0, total))))
    if score >= 75:
        grade = "优先"
    elif score >= 60:
        grade = "可以考虑"
    elif score >= 45:
        grade = "谨慎"
    else:
        grade = "放弃"
    factors.sort(key=lambda f: -abs(float(f["points"])))
    return {
        "id": row.get("id"),
        "code": row.get("code"),
        "name": row.get("name") or row.get("code"),
        "manual": bool(row.get("manual")),
        "source_label": SOURCE_LABELS.get(str(row.get("signal_type") or ""), "手输"),
        "price": row.get("price"),
        "live_last": row.get("live_last"),
        "live_pct": row.get("live_pct"),
        "score": score,
        "grade": grade,
        "factors": factors,
        "check": pick_check(factors, pos),
        "position": pos,
        "waiting": bool(pos and pos["kind"] == "above"),
        "blocked": bool(pos and pos["kind"] in ("stop", "chase")),
    }


def _check_of(it: dict[str, Any]) -> dict[str, Any]:
    """Return an item's checklist, rebuilding it for stored rows that predate it."""
    chk = it.get("check")
    if isinstance(chk, dict) and "rank" in chk:
        return chk
    return pick_check(it.get("factors"), it.get("position"))


def pick_top(items: dict[str, dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Return codes with no hard or risk item (one row per code), most plus items first."""
    best: dict[str, dict[str, Any]] = {}
    for sid, it in (items or {}).items():
        code = str(it.get("code") or "")
        chk = _check_of(it)
        if not code or chk["hard"] or chk["risk"] or it.get("blocked"):
            continue
        if code not in best or len(chk["plus"]) > best[code]["plus_n"]:
            best[code] = {
                "id": sid,
                "code": code,
                "name": it.get("name") or code,
                "plus_n": len(chk["plus"]),
                "plus": chk["plus"],
            }
    return sorted(best.values(), key=lambda x: (-x["plus_n"], x["code"]))[:limit]


def rank_picks(rows: list[dict[str, Any]], history: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Rank candidates by their checklist (hard, then risk, then plus count); ties stay ties.

    The 0–100 score rides along for the self-audit only: on every sample tested
    it did not reliably rank buys, so it no longer orders the comparison.
    """
    stats = build_history_stats(history)
    items = [score_pick(r, stats) for r in rows]
    items.sort(key=lambda it: (it["check"]["rank"], str(it["code"] or "")))
    verdict = ""
    if items:
        names = lambda group: "、".join(str(it["name"]) for it in group)  # noqa: E731
        hard = [it for it in items if it["check"]["hard"]]
        ok = [it for it in items if not it["check"]["hard"]]
        bits: list[str] = []
        if ok:
            best = ok[0]["check"]["rank"]
            lead = [it for it in ok if it["check"]["rank"] == best]
            what = "无风险项" if not best[1] else f"风险项最少（{best[1]} 项）"
            if len(lead) > 1:
                bits.append(f"{names(lead)} 并列{what}，清单分不出高下：按仓位分开，或只选更贴主线的")
            else:
                bits.append(f"先看 {lead[0]['name']}（{what}）")
            waiting = [it for it in lead if it["waiting"]]
            if waiting:
                bits.append(f"{names(waiting)} 现价高于计划价，挂计划价等回踩")
        if hard:
            bits.append("有硬伤，今天别买：" + "；".join(
                f"{it['name']}（{'、'.join(it['check']['hard'])}）" for it in hard
            ))
        verdict = "；".join(bits)
    return {
        "ok": True,
        "items": items,
        "verdict": verdict,
        "history_n": stats["n"],
        "base_win3": stats["base_win3"],
    }


def pick_self_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Out-of-sample check of the stored first score (``pick0``) against 3-day outcomes.

    Each (day, code) counts once (earliest signal). Per day with enough rows:
    Spearman IC of the score, top-third minus bottom-third 3-day return, and the
    mean 3-day return of checklist-clean rows minus flagged rows.

    Args:
        rows: Buy signals with ``trade_date``, ``code``, ``outcome_day3_pct`` and
            a decoded ``payload``; rows before ``PICK_AUDIT_SINCE`` are ignored.

    Returns:
        Summary dict with ``status`` in ``building`` / ``works`` / ``weak`` / ``fails``
        and a one-line Chinese ``label`` for the UI.
    """
    from market_desk.config import PICK_AUDIT_MIN_DAYS, PICK_AUDIT_MIN_PER_DAY, PICK_AUDIT_SINCE

    seen: set[tuple[str, str]] = set()
    by_day: dict[str, list[tuple[float, float, bool]]] = {}
    for row in sorted(rows or [], key=lambda r: (str(r.get("trade_date") or ""), int(r.get("id") or 0))):
        day = str(row.get("trade_date") or "")[:10]
        code = str(row.get("code") or "")
        d3 = num(row.get("outcome_day3_pct"))
        p0 = _payload(row).get("pick0")
        if day < str(PICK_AUDIT_SINCE) or not code or d3 is None or not isinstance(p0, dict):
            continue
        score = num(p0.get("score"))
        if score is None or (day, code) in seen:
            continue
        seen.add((day, code))
        chk = _check_of(p0)
        by_day.setdefault(day, []).append((score, d3, not chk["hard"] and not chk["risk"]))
    n = sum(len(v) for v in by_day.values())
    ics: list[float] = []
    spreads: list[float] = []
    clean_gap: list[float] = []
    for vals in by_day.values():
        if len(vals) < int(PICK_AUDIT_MIN_PER_DAY):
            continue
        ic = spearman([v[0] for v in vals], [v[1] for v in vals])
        if ic is not None:
            ics.append(ic)
        ordered = sorted(vals, key=lambda v: -v[0])
        k = max(1, len(ordered) // 3)
        spreads.append(sum(v[1] for v in ordered[:k]) / k - sum(v[1] for v in ordered[-k:]) / k)
        clean = [v[1] for v in vals if v[2]]
        flagged = [v[1] for v in vals if not v[2]]
        if clean and flagged:
            clean_gap.append(sum(clean) / len(clean) - sum(flagged) / len(flagged))
    mean = lambda v: round(sum(v) / len(v), 3) if v else None  # noqa: E731
    ic_mean, ic_t = mean(ics), safe_t(ics)
    days = len(ics)
    if days < int(PICK_AUDIT_MIN_DAYS):
        status = "building"
        label = f"样本外积累中：{days}/{int(PICK_AUDIT_MIN_DAYS)} 天（{n} 条，{PICK_AUDIT_SINCE} 起）"
    elif (ic_mean or 0.0) <= 0:
        status = "fails"
        label = f"样本外无效：同日秩相关 {ic_mean or 0.0:+.2f}（{days} 天），分数只存档、不作参考"
    elif ic_t is not None and ic_t >= 2.0:
        status = "works"
        label = f"样本外有效：同日秩相关 {ic_mean:+.2f}（t {ic_t:+.1f}，{days} 天）"
    else:
        status = "weak"
        label = f"样本外不显著：同日秩相关 {ic_mean:+.2f}（{days} 天）"
    return {
        "since": str(PICK_AUDIT_SINCE),
        "n": n,
        "days": days,
        "ic": ic_mean,
        "ic_t": round(ic_t, 2) if ic_t is not None else None,
        "top_minus_bottom": mean(spreads),
        "clean_minus_flagged": mean(clean_gap),
        "clean_days": len(clean_gap),
        "status": status,
        "label": label,
    }
