"""Adapt effect scorecard: size/gate buckets, today cards, miss trends, theme tops.

Read-only helpers for ``agent_pack.adapt_effect``. Does not change live knobs.
"""

from __future__ import annotations

from typing import Any

from market_desk.numbers import num


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def _size_bucket(mult: float | None) -> str:
    """Map a frozen size_mult into shrink / flat / boost."""
    if mult is None:
        return "unknown"
    m = float(mult)
    if m <= 0.75:
        return "shrink"
    if m >= 1.15:
        return "boost"
    return "flat"


def _has_gate_fail(row: dict[str, Any]) -> bool:
    """Return True when confirm_fail history recorded any hard fail."""
    p = _payload(row)
    fails = (
        p.get("confirm_fail_hist")
        or p.get("final_fail")
        or p.get("confirm_fail")
        or row.get("confirm_fail")
        or []
    )
    return any(str(x or "").strip() for x in fails)


def _minute_fail(row: dict[str, Any]) -> bool:
    """Return True when any fail flag maps to the minute bucket."""
    from market_desk.review.hit_rates import _gate_bucket

    p = _payload(row)
    fails = (
        p.get("confirm_fail_hist")
        or p.get("final_fail")
        or p.get("confirm_fail")
        or row.get("confirm_fail")
        or []
    )
    return any(_gate_bucket(str(x or "")) == "分时" for x in fails)


def _dual_bucket(rows: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    """Build abs/xs dual wins for one bucket (needs excess already attached)."""
    from market_desk.dual_score import dual_from_pairs, win_rate

    abs_vals: list[float] = []
    pair_abs: list[float] = []
    pair_xs: list[float] = []
    for r in rows:
        d3 = num(r.get("outcome_day3_pct"))
        if d3 is None:
            continue
        abs_vals.append(float(d3))
        xs = num(r.get("excess_d3_pct"))
        if xs is not None:
            pair_abs.append(float(d3))
            pair_xs.append(float(xs))
    dual = dual_from_pairs(pair_abs, pair_xs, label=label)
    dual["n"] = len(abs_vals)
    dual["xs_n"] = len(pair_xs)
    dual["abs_win"] = win_rate(abs_vals)
    dual["mean_abs"] = round(sum(abs_vals) / len(abs_vals), 2) if abs_vals else None
    dual["ok"] = bool(abs_vals)
    return dual


def build_size_gate_scorecard(
    buy_rows: list[dict[str, Any]] | None,
    *,
    recent_n: int = 80,
) -> dict[str, Any]:
    """Score recent scored buys by frozen size_mult and gate-fail flags.

    Buckets are observational (not a full counterfactual). Thin n stays visible.
    """
    from market_desk.dual_score import load_bench_closes
    from market_desk.review.bench import attach_bench_excess
    from market_desk.review.signals import is_buy_signal

    rows = [
        r
        for r in (buy_rows or [])
        if is_buy_signal(r.get("signal_type"))
        and not int(r.get("skipped") or 0)
        and str(r.get("outcome_label") or "").strip()
    ]
    # Newest-first signals → take head then chronological for stability.
    rows = list(rows[: max(20, int(recent_n))])
    if not rows:
        return {
            "ok": False,
            "n": 0,
            "by_size": [],
            "by_gate": [],
            "note": "无已评分买样本",
        }

    try:
        closes = load_bench_closes(limit=240, allow_http=False)
        attached = attach_bench_excess(rows, closes) if len(closes) >= 4 else list(rows)
    except Exception:
        attached = list(rows)

    by_size: dict[str, list[dict[str, Any]]] = {
        "shrink": [],
        "flat": [],
        "boost": [],
        "unknown": [],
    }
    by_gate: dict[str, list[dict[str, Any]]] = {
        "gate_fail": [],
        "clean": [],
        "minute_fail": [],
    }
    for r in attached:
        p = _payload(r)
        mult = num(p.get("size_mult") if p.get("size_mult") is not None else r.get("size_mult"))
        by_size[_size_bucket(mult)].append(r)
        if _has_gate_fail(r):
            by_gate["gate_fail"].append(r)
            if _minute_fail(r):
                by_gate["minute_fail"].append(r)
        else:
            by_gate["clean"].append(r)

    size_rows = [
        {
            "key": key,
            "label": {
                "shrink": "缩仓≤0.75",
                "flat": "平仓≈1",
                "boost": "加仓≥1.15",
                "unknown": "无 size_mult",
            }.get(key, key),
            **_dual_bucket(items, label=key),
        }
        for key, items in by_size.items()
        if items or key in ("shrink", "flat", "boost")
    ]
    gate_rows = [
        {
            "key": key,
            "label": {
                "gate_fail": "有闸失败史",
                "clean": "无闸失败",
                "minute_fail": "分时失败史",
            }.get(key, key),
            **_dual_bucket(items, label=key),
        }
        for key, items in by_gate.items()
    ]
    return {
        "ok": True,
        "n": len(attached),
        "recent_n": int(recent_n),
        "by_size": size_rows,
        "by_gate": gate_rows,
        "note": (
            "观测分档：比缩仓/平仓/加仓与有无闸失败的 abs/xs；"
            "不是关掉热度的反事实。n 薄只参考。"
        ),
    }


def build_today_buy_cards(*, limit: int = 15) -> dict[str, Any]:
    """Slim today's recommend cards from the warm engine snapshot."""
    try:
        from market_desk.engine import engine

        snap = engine.snapshot or {}
    except Exception as exc:
        return {"ok": False, "n": 0, "items": [], "note": f"{type(exc).__name__}: {exc}"[:120]}

    verdict = snap.get("verdict") if isinstance(snap.get("verdict"), dict) else {}
    rec = verdict.get("recommend") if isinstance(verdict.get("recommend"), dict) else {}
    items_raw = list(rec.get("items") or [])
    main = verdict.get("mainline") if isinstance(verdict.get("mainline"), dict) else {}
    action = str(verdict.get("action") or "")
    out_items: list[dict[str, Any]] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        ready = bool(it.get("ready"))
        probe = bool(it.get("probe_ok") or it.get("ready_relaxed"))
        if not (ready or probe or it.get("near_entry")):
            continue
        fails = [str(x) for x in (it.get("confirm_fail") or []) if str(x).strip()]
        out_items.append(
            {
                "code": str(it.get("code") or "").zfill(6),
                "name": it.get("name"),
                "kind": it.get("kind") or "stock",
                "ready": ready,
                "probe_ok": probe,
                "size_mult": it.get("size_mult"),
                "qty": it.get("qty"),
                "desk_source": it.get("desk_source") or it.get("source"),
                "confirm_fail": fails[:4],
                "plan_price": it.get("plan_price") or it.get("price"),
            }
        )
        if len(out_items) >= int(limit):
            break
    return {
        "ok": True,
        "n": len(out_items),
        "action": action,
        "mainline": main.get("name") or verdict.get("mainline_name"),
        "items": out_items,
        "note": "仅 ready/半仓/近价卡，≤15；对照 adapt_live.size_mult",
    }


def build_miss_trend(
    buy_rows: list[dict[str, Any]] | None,
    *,
    days: int = 5,
) -> dict[str, Any]:
    """Count miss-attribution buckets for the last ``days`` signal dates."""
    from market_desk.review import build_miss_attribution
    from market_desk.review.signals import is_buy_signal

    dates: list[str] = []
    seen: set[str] = set()
    for r in buy_rows or []:
        if not is_buy_signal(r.get("signal_type")):
            continue
        d = str(r.get("trade_date") or "")[:10]
        if len(d) != 10 or d in seen:
            continue
        seen.add(d)
        dates.append(d)
        if len(dates) >= int(days):
            break

    series: list[dict[str, Any]] = []
    totals = {"never_touched": 0, "touched_not_bought": 0, "gate_blocked": 0}
    for d in dates:
        attr = build_miss_attribution(buy_rows or [], trade_date=d)
        counts = attr.get("counts") if isinstance(attr.get("counts"), dict) else {}
        row = {
            "day": d,
            "never_touched": int(counts.get("never_touched") or 0),
            "touched_not_bought": int(counts.get("touched_not_bought") or 0),
            "gate_blocked": int(counts.get("gate_blocked") or 0),
        }
        series.append(row)
        for k in totals:
            totals[k] += int(row[k])
    return {
        "ok": bool(series),
        "days": len(series),
        "totals": totals,
        "series": series,
        "note": "近窗未交易买卡三类；仅 never_touched 进 auto_tune",
    }


def build_theme_rep_snapshot(*, each: int = 5) -> dict[str, Any]:
    """Return worst/best theme reputation rows for Agent audit."""
    try:
        from market_desk.db import load_theme_reputation

        rows = load_theme_reputation()
    except Exception as exc:
        return {"ok": False, "worst": [], "best": [], "note": f"{type(exc).__name__}: {exc}"[:120]}

    cleaned: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cleaned.append(
            {
                "theme": r.get("theme_key") or r.get("theme"),
                "score_adj": r.get("score_adj"),
                "auto_adj": r.get("auto_adj"),
                "trade_adj": r.get("trade_adj"),
                "manual_adj": r.get("manual_adj"),
                "fade_n": r.get("fade_n"),
                "persist_n": r.get("persist_n"),
                "label": r.get("label"),
            }
        )
    worst = cleaned[: int(each)]
    best = list(reversed(cleaned[-int(each) :])) if cleaned else []
    return {
        "ok": bool(cleaned),
        "n": len(cleaned),
        "worst": worst,
        "best": best,
        "note": "按 score_adj 升序表头/表尾；进主线分，不硬禁",
    }


def build_adapt_effect_panel(
    buy_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the effect-audit block for agent-pack."""
    scorecard = build_size_gate_scorecard(buy_rows)
    today = build_today_buy_cards()
    miss = build_miss_trend(buy_rows)
    themes = build_theme_rep_snapshot()
    return {
        "ok": True,
        "scorecard": scorecard,
        "today_cards": today,
        "miss_trend": miss,
        "theme_rep": themes,
        "note": (
            "看效果用：size/闸分档 abs·xs + 今日卡对照 adapt_live + 漏买三类 + 题材信誉两端。"
            "仍非因子反事实。"
        ),
    }
