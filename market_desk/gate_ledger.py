"""Gate ledger: per-gate outcome attribution for buy signals (display only).

Each scored buy carries the gates / flags it met when it was recorded:

- row gates: ready / near-entry / confirm fails / soft confirms / trend flags
  stored on the signal payload;
- market gates: verdict ``algo_notes`` at first capture (``market_gates``).

Row gates are judged against same-day peers without the gate, which removes the
market-day effect. Market gates are day-level (nearly every signal that day
carries them), so they are judged by gated-vs-ungated raw 3-day return instead.
The ledger never feeds back into signals; it only tells which gates earn their
keep.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from market_desk.numbers import num

# (payload key, label, polarity): +1 = expected to pick better buys, -1 = caution.
_ROW_FLAGS: tuple[tuple[str, str, int], ...] = (
    ("near_entry", "近买点", 1),
    ("probe_ok", "可试探", 1),
    ("ready_relaxed", "放宽亮灯", 1),
    ("block_ready", "禁亮灯", -1),
    ("fly_warn", "飞刀提示", -1),
    ("size_cap_block", "仓位上限", -1),
    ("trend_down", "日线向下", -1),
)

_NUM_RE = re.compile(r"[×x]?[-+]?\d+(?:\.\d+)?%?")
_PAREN_RE = re.compile(r"[（(][^（()）]*[)）]")
_BUY_TYPES = frozenset({"buy", "buy_side", "buy_link", "buy_trial", "buy_indep", "buy_dragon"})


def normalize_gate_note(note: Any) -> str:
    """Reduce a verdict note to a stable gate key.

    Strips parenthesised detail, anything after ``=`` and embedded numbers so
    ``复盘命中32.1%·软降`` and ``复盘命中28%·软降`` count as the same gate.

    Args:
        note: Raw ``algo_notes`` entry.

    Returns:
        Normalized key, or an empty string when nothing meaningful remains.
    """
    text = str(note or "").strip()
    if not text:
        return ""
    text = text.split("=", 1)[0]
    text = _PAREN_RE.sub("", text)
    text = _NUM_RE.sub("", text)
    text = re.sub(r"·{2,}", "·", text).strip(" ·")
    return text[:24]


def normalize_gate_notes(notes: Iterable[Any] | None) -> list[str]:
    """Normalize and de-duplicate verdict notes, preserving first-seen order."""
    out: list[str] = []
    for note in notes or []:
        key = normalize_gate_note(note)
        if key and key not in out:
            out.append(key)
    return out[:16]


def signal_gate_keys(row: dict[str, Any]) -> list[tuple[str, str, int]]:
    """List ``(key, scope, polarity)`` gates carried by one buy signal.

    Args:
        row: Decoded signal row (``payload`` as dict).

    Returns:
        Gate tuples; scope is ``row`` or ``market``.
    """
    from market_desk.review import _ever_ready, _gate_bucket

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    out: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    def _add(key: str, scope: str, polarity: int) -> None:
        if key and key not in seen:
            seen.add(key)
            out.append((key, scope, polarity))

    if _ever_ready(row):
        _add("亮过可买", "row", 1)
    for field, label, pol in _ROW_FLAGS:
        if payload.get(field):
            _add(label, "row", pol)
    if str(row.get("kind") or "stock") == "stock" and (
        payload.get("trend_pending")
        or payload.get("trend_unknown")
        or payload.get("trend_quality") == "fetch_fail"
    ):
        _add("日线缺失", "row", -1)
    fails = payload.get("confirm_fail_hist") or payload.get("final_fail") or payload.get("confirm_fail") or []
    for flag in fails:
        if str(flag).strip():
            _add(f"卡·{_gate_bucket(str(flag))}", "row", -1)
    for flag in payload.get("confirm_soft") or []:
        text = str(flag).strip()
        if text:
            _add(f"软·{text[:12]}", "row", -1)
    for note in payload.get("market_gates") or []:
        key = normalize_gate_note(note)
        if key:
            _add(f"市·{key}", "market", -1)
    return out


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _judge(metric: float | None, polarity: int, n: int, days: int, cfg: dict[str, float]) -> tuple[str, str]:
    """Return ``(tone, verdict)`` for one ledger row."""
    if n < cfg["min_n"] or days < cfg["min_days"]:
        return "low", "样本不足"
    if metric is None:
        return "low", "无同日对照"
    edge = cfg["edge"]
    signed = metric * polarity
    if signed >= edge:
        return "good", "有效" if polarity > 0 else "挡对了"
    if signed <= -edge:
        return "bad", "反向" if polarity > 0 else "误伤"
    return "mid", "无差异"


def build_gate_ledger(
    rows: list[dict[str, Any]] | None,
    *,
    days: int | None = None,
) -> dict[str, Any]:
    """Attribute 3-day buy outcomes to every gate over recent scored trade days.

    Args:
        rows: Signal rows (any order). Non-buy, skipped, unscored and
            non-trading-day rows are ignored.
        days: Rolling window in scored trade dates (defaults to config).

    Returns:
        Dict with ``ok``, window stats, ``rows`` (one per gate, most frequent
        first) and a one-line ``note``. Each row has ``n``, ``days``, ``win3``,
        ``d3``, ``excess`` (vs same-day peers without the gate), ``raw_diff``
        (vs all ungated buys), ``tone`` and ``verdict``.
    """
    from market_desk.calendar import is_trading_day
    from market_desk.config import (
        GATE_LEDGER_DAYS,
        GATE_LEDGER_EDGE_PCT,
        GATE_LEDGER_MIN_DAYS,
        GATE_LEDGER_MIN_N,
    )

    cfg = {
        "min_n": float(GATE_LEDGER_MIN_N),
        "min_days": float(GATE_LEDGER_MIN_DAYS),
        "edge": float(GATE_LEDGER_EDGE_PCT),
    }
    window = int(days or GATE_LEDGER_DAYS)
    by_day: dict[str, list[tuple[float, dict[str, tuple[str, int]]]]] = {}
    for r in rows or []:
        if str(r.get("signal_type") or "") not in _BUY_TYPES:
            continue
        if int(r.get("skipped") or 0):
            continue
        d3 = num(r.get("outcome_day3_pct"))
        day = str(r.get("trade_date") or "")[:10]
        if d3 is None or len(day) < 10:
            continue
        try:
            if not is_trading_day(day):
                continue
        except ValueError:
            continue
        keys = {k: (scope, pol) for k, scope, pol in signal_gate_keys(r)}
        by_day.setdefault(day, []).append((float(d3), keys))
    picked = sorted(by_day)[-window:]
    samples = [(day, d3, keys) for day in picked for d3, keys in by_day[day]]
    total = len(samples)
    if not total:
        return {"ok": False, "days": 0, "n": 0, "rows": [], "note": "暂无已打分买点"}

    meta: dict[str, tuple[str, int]] = {}
    for _, _, keys in samples:
        for k, v in keys.items():
            meta.setdefault(k, v)

    out_rows: list[dict[str, Any]] = []
    for key, (scope, pol) in meta.items():
        gated = [(day, d3) for day, d3, keys in samples if key in keys]
        ungated = [d3 for _, d3, keys in samples if key not in keys]
        g_days = sorted({d for d, _ in gated})
        wsum = 0.0
        wn = 0
        peer_n = 0
        for day in g_days:
            g = [d3 for d3, keys in by_day[day] if key in keys]
            p = [d3 for d3, keys in by_day[day] if key not in keys]
            if not p:
                continue
            wsum += len(g) * ((_mean(g) or 0.0) - (_mean(p) or 0.0))
            wn += len(g)
            peer_n += len(p)
        excess = round(wsum / wn, 2) if wn else None
        g_d3 = [d3 for _, d3 in gated]
        raw_diff = (
            round((_mean(g_d3) or 0.0) - (_mean(ungated) or 0.0), 2) if ungated else None
        )
        metric = excess if scope == "row" else raw_diff
        tone, verdict = _judge(metric, pol, len(gated), len(g_days), cfg)
        out_rows.append({
            "key": key,
            "scope": scope,
            "polarity": pol,
            "n": len(gated),
            "days": len(g_days),
            "win3": round(100.0 * sum(1 for v in g_d3 if v > 0) / len(g_d3), 1),
            "d3": round(_mean(g_d3) or 0.0, 2),
            "excess": excess,
            "peer_n": peer_n,
            "raw_diff": raw_diff,
            "metric": metric,
            "tone": tone,
            "verdict": verdict,
        })
    out_rows.sort(key=lambda x: (-x["n"], x["key"]))
    good = sum(1 for x in out_rows if x["tone"] == "good")
    bad = sum(1 for x in out_rows if x["tone"] == "bad")
    judged = sum(1 for x in out_rows if x["tone"] != "low")
    note = (
        f"近 {len(picked)} 日 {total} 个已打分买点 · {len(out_rows)} 道闸门/标记"
        f" · 可判 {judged}：有效 {good} / 反向或误伤 {bad}"
    )
    return {
        "ok": True,
        "days": len(picked),
        "n": total,
        "rows": out_rows[:30],
        "good_n": good,
        "bad_n": bad,
        "note": note,
    }
