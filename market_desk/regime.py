"""Benchmark regime ruler: CSI 1000 close vs its MA60 (display / shadow only).

Does not gate ready. Live desk shows a soft hint; buy-card payloads freeze the
side at first insert so edge-shadow can split sample-out by regime.
"""

from __future__ import annotations

from typing import Any, Iterable

from market_desk.numbers import num


def _as_close_pairs(
    closes: Iterable[Any] | None,
) -> list[tuple[str, float]]:
    """Normalize closes to ``[(YYYY-MM-DD, close)]`` oldest-first."""
    out: list[tuple[str, float]] = []
    for row in closes or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            day = str(row[0] or "")[:10]
            px = num(row[1])
        elif isinstance(row, dict):
            day = str(row.get("date") or row.get("trade_date") or "")[:10]
            px = num(row.get("close") or row.get("price"))
        else:
            continue
        if day and px is not None and float(px) > 0:
            out.append((day, float(px)))
    out.sort(key=lambda x: x[0])
    return out


def classify_bench_regime(
    closes: Iterable[Any] | None,
    *,
    asof: str | None = None,
    ma_n: int | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Classify whether the benchmark close is above or below its MA.

    Args:
        closes: Daily closes (tuples, lists, or bar dicts), oldest first.
        asof: Use the last session on or before this date (default: last bar).
        ma_n: Moving-average length (default ``REGIME_MA_N``).
        label: Display name (default ``REVIEW_BENCH_LABEL``).

    Returns:
        Dict with ``ok``, ``side`` (``above`` / ``below`` / ``unknown``),
        ``close``, ``ma``, ``asof``, ``label``, ``hint``.
    """
    from market_desk.config import REGIME_MA_N, REVIEW_BENCH_LABEL

    n = int(ma_n or REGIME_MA_N)
    name = str(label or REVIEW_BENCH_LABEL)
    pairs = _as_close_pairs(closes)
    if asof:
        cut = str(asof)[:10]
        pairs = [p for p in pairs if p[0] <= cut]
    if len(pairs) < n:
        return {
            "ok": False,
            "side": "unknown",
            "close": None,
            "ma": None,
            "ma_n": n,
            "asof": str(asof or "")[:10] or None,
            "label": name,
            "short_hint": "日线不足",
            "hint": f"{name} 日线不足（需≥{n}根），大盘尺暂不可用 · 只提示不关亮灯",
            "bars": len(pairs),
        }
    window = pairs[-n:]
    last_day, last_px = pairs[-1]
    ma = sum(p[1] for p in window) / float(n)
    side = "above" if last_px >= ma else "below"
    if side == "above":
        short_hint = "主线回踩为主"
        hint = "尺上（趋势上）：主线回踩为主；尖峰仍只观察 · 软旁注，不关亮灯"
    else:
        short_hint = "少做主题回踩 · 偏日线降"
        hint = "尺下（趋势下）：少做主题回踩/龙头追强，偏日线降与位置型 · 软旁注，不关亮灯"
    return {
        "ok": True,
        "side": side,
        "close": round(last_px, 4),
        "ma": round(ma, 4),
        "ma_n": n,
        "asof": last_day,
        "label": name,
        "short_hint": short_hint,
        "hint": hint,
        "gap_pct": round((last_px / ma - 1.0) * 100.0, 2),
        "bars": len(pairs),
    }


def regime_side_for_row(
    row: dict[str, Any],
    closes: Iterable[Any] | None = None,
) -> str:
    """Return ``above`` / ``below`` / ``unknown`` for one signal row.

    Prefers frozen ``payload.market_regime.side``; otherwise classifies the
    trade date from ``closes``.
    """
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    frozen = payload.get("market_regime") if isinstance(payload.get("market_regime"), dict) else {}
    side = str(frozen.get("side") or "").strip()
    if side in ("above", "below"):
        return side
    day = str(row.get("trade_date") or "")[:10]
    if not day or closes is None:
        return "unknown"
    got = classify_bench_regime(closes, asof=day)
    return str(got.get("side") or "unknown")


def _row_lit(row: dict[str, Any]) -> bool:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return bool(payload.get("ever_ready") or row.get("ready"))


def _row_trend_down(row: dict[str, Any]) -> bool:
    return bool(row.get("trend_down") or (row.get("payload") or {}).get("trend_down"))


def _row_dragon(row: dict[str, Any]) -> bool:
    src = str(row.get("desk_source") or row.get("signal_type") or "")
    if src in ("dragon", "buy_dragon") or "dragon" in src:
        return True
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(payload.get("desk_source") or "") in ("dragon", "emotion_dragon", "mid_army_dragon")


def _bucket_stats(
    vals: list[float],
    days: set[str],
    *,
    key: str,
    side: str,
    note: str,
) -> dict[str, Any]:
    mean = round(sum(vals) / len(vals), 2) if vals else None
    win = round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 1) if vals else None
    return {
        "key": key,
        "side": side,
        "n": len(vals),
        "days": len(days),
        "d3": mean,
        "win": win,
        "note": note,
    }


def build_regime_shadow_rows(
    samples: list[dict[str, Any]] | None,
    closes: Iterable[Any] | None,
    *,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Split scored buys by benchmark side and report mean 3-day returns.

    Prefers lit cards for the above/below headline (主线回踩口径). Also lists
    weak-regime subsets (日线降 / 亮过灯 / 龙头) for the pre-registered test.
    Display / audit only. ``since`` defaults to ``REGIME_SHADOW_SINCE``.
    """
    from market_desk.config import REGIME_SHADOW_SINCE

    start = str(since or REGIME_SHADOW_SINCE)[:10]
    rows = [r for r in (samples or []) if str(r.get("trade_date") or "")[:10] >= start]
    lit_buckets: dict[str, list[float]] = {"above": [], "below": [], "unknown": []}
    lit_days: dict[str, set[str]] = {"above": set(), "below": set(), "unknown": set()}
    below_down: list[float] = []
    below_down_days: set[str] = set()
    below_lit: list[float] = []
    below_lit_days: set[str] = set()
    below_dragon: list[float] = []
    below_dragon_days: set[str] = set()

    for r in rows:
        d3 = num(r.get("outcome_day3_pct"))
        if d3 is None:
            continue
        day = str(r.get("trade_date") or "")[:10]
        side = regime_side_for_row(r, closes)
        if side not in lit_buckets:
            side = "unknown"
        if _row_lit(r):
            lit_buckets[side].append(float(d3))
            lit_days[side].add(day)
        if side == "below":
            if _row_trend_down(r):
                below_down.append(float(d3))
                below_down_days.add(day)
            if _row_lit(r):
                below_lit.append(float(d3))
                below_lit_days.add(day)
            if _row_dragon(r):
                below_dragon.append(float(d3))
                below_dragon_days.add(day)

    labels = {
        "above": "尺上·亮过灯",
        "below": "尺下·亮过灯",
        "unknown": "尺未知·亮过灯",
    }
    out: list[dict[str, Any]] = []
    for key in ("above", "below", "unknown"):
        vals = lit_buckets[key]
        if not vals and key == "unknown":
            continue
        out.append(
            _bucket_stats(
                vals,
                lit_days[key],
                key=labels[key],
                side=key,
                note="信号日大盘尺 · 亮过灯优先 · 仅记账",
            )
        )
    if below_down or below_lit or below_dragon:
        out.append(
            _bucket_stats(
                below_down,
                below_down_days,
                key="尺下·日线降",
                side="below",
                note="弱市位置型对照",
            )
        )
        out.append(
            _bucket_stats(
                below_lit,
                below_lit_days,
                key="尺下·亮过灯(再列)",
                side="below",
                note="弱市主题回踩对照",
            )
        )
        out.append(
            _bucket_stats(
                below_dragon,
                below_dragon_days,
                key="尺下·龙头",
                side="below",
                note="弱市龙头对照",
            )
        )

    by_key = {r["key"]: r for r in out}
    above_d3 = (by_key.get("尺上·亮过灯") or {}).get("d3")
    below_d3 = (by_key.get("尺下·亮过灯") or {}).get("d3")
    down_d3 = (by_key.get("尺下·日线降") or {}).get("d3")
    if above_d3 is not None and below_d3 is not None:
        out.append(
            {
                "key": "Δ 尺上−尺下（亮过灯）",
                "side": "delta",
                "n": None,
                "days": None,
                "d3": round(float(above_d3) - float(below_d3), 2),
                "win": None,
                "note": "预登记主指标 · 仅记账",
                "delta": True,
            }
        )
    if down_d3 is not None and below_d3 is not None:
        out.append(
            {
                "key": "Δ 尺下日线降−亮过灯",
                "side": "delta",
                "n": None,
                "days": None,
                "d3": round(float(down_d3) - float(below_d3), 2),
                "win": None,
                "note": "弱市位置型优势 · 仅记账",
                "delta": True,
            }
        )
    return out


def build_regime_survival(
    samples: list[dict[str, Any]] | None,
    closes: Iterable[Any] | None = None,
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Below-MA survival pressure: absolute win vs excess win (display only).

    Answers 「弱市是策略烂还是大盘在砸」 without changing ready / soft_rank.
    """
    from market_desk.config import REGIME_SHADOW_SINCE, REVIEW_BENCH_LABEL
    from market_desk.dual_score import dual_from_pairs, load_bench_closes
    from market_desk.filters import normalize_code
    from market_desk.review.bench import attach_bench_excess
    from market_desk.review.outcome import OUTCOME_HORIZON_SESSIONS

    start = str(since or REGIME_SHADOW_SINCE)[:10]
    pairs = list(closes) if closes is not None else load_bench_closes(allow_http=True)
    if len(pairs) < 4:
        return {
            "ok": False,
            "since": start,
            "bench": REVIEW_BENCH_LABEL,
            "note": f"无{REVIEW_BENCH_LABEL}日线，尺下存活压力暂不可算",
        }

    attached = attach_bench_excess(list(samples or []), pairs)
    seen: set[tuple[str, str]] = set()
    buckets: dict[str, tuple[list[float], list[float]]] = {
        "below_all": ([], []),
        "below_lit": ([], []),
        "below_down": ([], []),
        "above_lit": ([], []),
    }
    days_below: set[str] = set()

    for r in attached:
        d3 = num(r.get("outcome_day3_pct"))
        xs = num(r.get("excess_d3_pct"))
        if d3 is None or xs is None:
            continue
        if int(r.get("bench_sessions") or 0) < OUTCOME_HORIZON_SESSIONS:
            continue
        if int(r.get("skipped") or 0):
            continue
        day = str(r.get("trade_date") or "")[:10]
        if not day or day < start:
            continue
        key = (day, normalize_code(r.get("code")))
        if key in seen:
            continue
        seen.add(key)
        side = regime_side_for_row(r, pairs)
        lit = _row_lit(r)
        if side == "above" and lit:
            buckets["above_lit"][0].append(float(d3))
            buckets["above_lit"][1].append(float(xs))
        if side != "below":
            continue
        days_below.add(day)
        buckets["below_all"][0].append(float(d3))
        buckets["below_all"][1].append(float(xs))
        if lit:
            buckets["below_lit"][0].append(float(d3))
            buckets["below_lit"][1].append(float(xs))
        if _row_trend_down(r):
            buckets["below_down"][0].append(float(d3))
            buckets["below_down"][1].append(float(xs))

    labels = {
        "below_all": "尺下·全部买卡",
        "below_lit": "尺下·亮过灯",
        "below_down": "尺下·日线降",
        "above_lit": "尺上·亮过灯（对照）",
    }
    groups = {
        k: dual_from_pairs(a, x, label=labels[k])
        for k, (a, x) in buckets.items()
    }
    below_n = int(groups["below_all"].get("n") or 0)
    return {
        "ok": bool(below_n or groups["above_lit"].get("n")),
        "since": start,
        "bench": REVIEW_BENCH_LABEL,
        "below_days": len(days_below),
        "groups": groups,
        "note": (
            "存活压力：尺下 abs_win=三日%>0，xs_win=超额>0。"
            "xs 仍正而 abs 很差 = 市场拖累；两者都差才怀疑边。"
            "只旁注，不改 ready / 软排序。"
        ),
    }


def regime_card_tag(item: dict[str, Any] | None, market_regime: dict[str, Any] | None) -> dict[str, str] | None:
    """Return a soft card tag for weak-regime context (display only).

    Below MA60: prefer 日线降 as 「弱市位置型」; lit theme / dragon as 「弱市慎追」.
    Above / unknown: no tag. Never changes ready.
    """
    mr = market_regime if isinstance(market_regime, dict) else {}
    if not mr.get("ok") or str(mr.get("side") or "") != "below":
        return None
    it = item if isinstance(item, dict) else {}
    if it.get("trend_down"):
        return {
            "label": "弱市位置型",
            "tone": "good",
            "note": "尺下偏日线降/位置型 · 软旁注，不关亮灯",
        }
    src = str(it.get("desk_source") or "")
    is_dragon = src in ("dragon", "emotion_dragon", "mid_army_dragon") or "dragon" in src
    lit = bool(it.get("ready") or it.get("near_entry") or it.get("fly_warn"))
    if is_dragon or lit:
        return {
            "label": "弱市慎追",
            "tone": "warn",
            "note": "尺下少做主题回踩/龙头追强 · 软旁注，不关亮灯",
        }
    return {
        "label": "弱市观察",
        "tone": "soft",
        "note": "尺下少做主题回踩 · 软旁注，不关亮灯",
    }


def annotate_recommend_regime(
    recommend: dict[str, Any] | None,
    market_regime: dict[str, Any] | None,
) -> dict[str, Any]:
    """Stamp ``regime_tag`` on each recommend item (soft UX only)."""
    rec = dict(recommend or {})
    items: list[dict[str, Any]] = []
    for raw in rec.get("items") or []:
        item = dict(raw)
        item.pop("regime_tag", None)
        tag = regime_card_tag(item, market_regime)
        if tag:
            item["regime_tag"] = tag
        items.append(item)
    rec["items"] = items
    return rec


def annotate_verdict_regime(
    verdict: dict[str, Any] | None,
    market_regime: dict[str, Any] | None,
) -> dict[str, Any]:
    """Annotate all recommend pools on a verdict with soft regime tags."""
    v = dict(verdict or {})
    for key in (
        "recommend",
        "side_recommend",
        "link_recommend",
        "dragon_recommend",
        "independent_recommend",
        "watch_trial_recommend",
    ):
        if isinstance(v.get(key), dict):
            v[key] = annotate_recommend_regime(v.get(key), market_regime)
    return v


def freeze_market_regime(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact regime dict suitable for freezing into a signal payload."""
    mr = (snapshot or {}).get("market_regime") if isinstance(snapshot, dict) else None
    if not isinstance(mr, dict) or not mr.get("ok"):
        return None
    side = str(mr.get("side") or "")
    if side not in ("above", "below"):
        return None
    return {
        "side": side,
        "asof": mr.get("asof"),
        "close": mr.get("close"),
        "ma": mr.get("ma"),
        "ma_n": mr.get("ma_n"),
        "label": mr.get("label"),
        "gap_pct": mr.get("gap_pct"),
    }
