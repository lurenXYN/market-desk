"""Dual score: absolute win rate vs excess-over-benchmark win rate.

Harsh markets crush absolute 三日红 while excess can stay informative.
Use both side-by-side; never collapse them into one 「胜率」.
"""

from __future__ import annotations

from typing import Any, Iterable

from market_desk.config import EDGE_SHADOW_SINCE, REVIEW_BENCH_LABEL, REVIEW_BENCH_SYMBOL
from market_desk.filters import normalize_code
from market_desk.numbers import num
from market_desk.review.bench import attach_bench_excess
from market_desk.review.outcome import OUTCOME_HORIZON_SESSIONS
from market_desk.review.signals import is_buy_signal


def win_rate(values: Iterable[float]) -> float | None:
    """Return % of values strictly greater than zero."""
    xs = [float(v) for v in values]
    if not xs:
        return None
    return round(100.0 * sum(1 for v in xs if v > 0) / len(xs), 1)


def dual_from_pairs(
    abs_vals: list[float],
    xs_vals: list[float],
    *,
    label: str = "",
) -> dict[str, Any]:
    """Build one dual-win block from aligned absolute and excess return lists."""
    n = min(len(abs_vals), len(xs_vals))
    a = abs_vals[:n]
    x = xs_vals[:n]
    return {
        "ok": bool(n),
        "label": label,
        "n": n,
        "abs_win": win_rate(a),
        "xs_win": win_rate(x),
        "mean_abs": round(sum(a) / n, 2) if n else None,
        "mean_xs": round(sum(x) / n, 2) if n else None,
        "note": "abs=三日%>0；xs=超额(三日%−基准同窗)>0",
    }


def _fetch_bench_closes_http(sym: str, limit: int) -> list[tuple[str, float]]:
    """Sync QQ day-line fallback when the bar cache has no index rows."""
    try:
        import httpx

        from market_desk.config import HTTP_HEADERS
    except Exception:
        return []
    url = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={sym},day,,,{int(limit)},qfq"
    )
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(url, headers={**HTTP_HEADERS, "Referer": "https://gu.qq.com/"})
            resp.raise_for_status()
            node = ((resp.json().get("data") or {}).get(sym)) or {}
    except Exception:
        return []
    out: list[tuple[str, float]] = []
    for row in node.get("qfqday") or node.get("day") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        close = num(row[2])
        if close is not None:
            out.append((str(row[0])[:10], float(close)))
    return out


def load_bench_closes(*, limit: int = 240, allow_http: bool = True) -> list[tuple[str, float]]:
    """Load CSI1000 closes from the daily bar cache, optionally via HTTP.

    Args:
        limit: Max bars to read / fetch.
        allow_http: When the cache is thin, hit QQ day-line (disable in unit tests
            that do not pass explicit closes).
    """
    from market_desk.db import load_daily_bars

    want = max(40, min(int(limit), 2000))
    sym = str(REVIEW_BENCH_SYMBOL or "sh000852").lower()
    code = normalize_code(sym.replace("sh", "").replace("sz", ""))
    bars = load_daily_bars(code, limit=want)
    out: list[tuple[str, float]] = []
    for b in bars:
        c = num(b.get("close"))
        d = str(b.get("trade_date") or b.get("date") or "")[:10]
        if c is not None and d:
            out.append((d, float(c)))
    if len(out) >= 4 or not allow_http:
        return out
    return _fetch_bench_closes_http(sym, want)


def oos_buy_dual_wins(
    rows: list[dict[str, Any]],
    closes: list[tuple[str, float]] | None = None,
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Dual wins for scored out-of-sample buy cards (all + lit subset).

    Dedupes per (day, code); only full 3-session horizons count.
    """
    start = str(since or EDGE_SHADOW_SINCE)[:10]
    closes = list(closes) if closes is not None else load_bench_closes()
    if len(closes) < 4:
        return {
            "ok": False,
            "bench": REVIEW_BENCH_LABEL,
            "note": f"无{REVIEW_BENCH_LABEL}日线缓存，双胜率暂不可算",
        }

    attached = attach_bench_excess(rows, closes)
    seen: set[tuple[str, str]] = set()
    all_abs: list[float] = []
    all_xs: list[float] = []
    lit_abs: list[float] = []
    lit_xs: list[float] = []
    days: set[str] = set()

    for r in attached:
        if not is_buy_signal(r.get("signal_type")):
            continue
        if int(r.get("skipped") or 0):
            continue
        d3 = num(r.get("outcome_day3_pct"))
        xs = num(r.get("excess_d3_pct"))
        if d3 is None or xs is None:
            continue
        if int(r.get("bench_sessions") or 0) < OUTCOME_HORIZON_SESSIONS:
            continue
        day = str(r.get("trade_date") or "")[:10]
        if not day or day < start:
            continue
        key = (day, normalize_code(r.get("code")))
        if key in seen:
            continue
        seen.add(key)
        days.add(day)
        all_abs.append(float(d3))
        all_xs.append(float(xs))
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        lit = bool(payload.get("ever_ready") or r.get("ready"))
        if lit:
            lit_abs.append(float(d3))
            lit_xs.append(float(xs))

    return {
        "ok": bool(all_abs),
        "bench": REVIEW_BENCH_LABEL,
        "since": start,
        "days": len(days),
        "all": dual_from_pairs(all_abs, all_xs, label="样本外买卡"),
        "lit": dual_from_pairs(lit_abs, lit_xs, label="亮过可买"),
        "note": (
            "恶劣市看 xs_win（跑赢大盘）；绝对盈亏看 abs_win。"
            "改规则用相对；开不开工用绝对（模拟闸）。"
        ),
    }


def soft_candidate_hint(item: dict[str, Any]) -> bool:
    """Return True only when item metrics clearly pass a known TODO shape.

    Conservative: missing t / halves → False (verdict stays hold).
    """
    iid = str(item.get("id") or "")
    m = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    if iid == "narrative":
        hit = num(m.get("hit_rate"))
        lead = num(m.get("median_lead_min"))
        days_c = int(m.get("days_with_cluster") or 0)
        return bool(
            hit is not None
            and hit >= 0.5
            and lead is not None
            and lead >= 10
            and days_c >= 5
        )
    if iid == "sell_fly":
        fly = num(m.get("fly_rate"))
        # Prefer per-rule with n>=15; fall back to headline fly only if >60 and no positive save.
        rules = m.get("by_rule") if isinstance(m.get("by_rule"), list) else []
        for row in rules:
            if not isinstance(row, dict):
                continue
            if int(row.get("n") or 0) < 15:
                continue
            fr = num(row.get("fly_rate"))
            saved = num(row.get("avg_saved_d3"))
            if fr is not None and fr > 60 and saved is not None and saved < 0:
                return True
        return bool(fly is not None and fly > 60)
    if iid == "pick_audit":
        ic = num(m.get("ic"))
        ic_t = num(m.get("ic_t"))
        return bool(ic is not None and ic > 0 and ic_t is not None and abs(ic_t) >= 2)
    if iid == "sim_desk_gate":
        return bool(m.get("gate_ok"))
    return False
