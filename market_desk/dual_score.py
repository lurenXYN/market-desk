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


def bench_cache_code(sym: str | None = None) -> str:
    """Return the ``market_bar_daily`` key for the review benchmark index.

    Uses ``IDX######`` so CSI1000 never collides with the A-share ticker 000852
    (石化机械) which shares the same six digits.
    """
    raw = str(sym or REVIEW_BENCH_SYMBOL or "sh000852").lower()
    digits = raw.replace("sh", "").replace("sz", "").replace("idx", "")
    six = normalize_code(digits) or str(digits).zfill(6)
    return f"IDX{six}"


def load_bench_closes(*, limit: int = 240, allow_http: bool = True) -> list[tuple[str, float]]:
    """Load CSI1000 closes from the daily bar cache, optionally via HTTP.

    Args:
        limit: Max bars to read / fetch.
        allow_http: When the cache is thin, hit QQ day-line (disable in unit tests
            that do not pass explicit closes). Successful HTTP pulls also upsert
            into ``market_bar_daily`` under :func:`bench_cache_code`.
    """
    from market_desk.db import load_daily_bars, upsert_daily_bars

    want = max(40, min(int(limit), 2000))
    sym = str(REVIEW_BENCH_SYMBOL or "sh000852").lower()
    code = bench_cache_code(sym)
    bars = load_daily_bars(code, limit=want)
    out: list[tuple[str, float]] = []
    for b in bars:
        c = num(b.get("close"))
        d = str(b.get("trade_date") or b.get("date") or "")[:10]
        if c is not None and d:
            out.append((d, float(c)))
    if len(out) >= 4 or not allow_http:
        return out
    fetched = _fetch_bench_closes_http(sym, want)
    if fetched:
        try:
            upsert_daily_bars(
                code,
                [{"date": d, "close": c, "open": c, "high": c, "low": c} for d, c in fetched],
                source="bench_http",
            )
        except Exception:
            pass
    return fetched


def bench_cache_fresh(*, limit: int = 240, max_age_days: int = 7) -> dict[str, Any]:
    """Return freshness of the IDX bench cache (no HTTP)."""
    closes = load_bench_closes(limit=limit, allow_http=False)
    out: dict[str, Any] = {
        "ok": len(closes) >= 4,
        "n": len(closes),
        "code": bench_cache_code(),
        "from": closes[0][0] if closes else None,
        "to": closes[-1][0] if closes else None,
        "stale": False,
    }
    if not closes:
        out["note"] = "指数 IDX 缓存为空；相对闸拒闸直至灌库"
        return out
    try:
        from datetime import date, timedelta

        from market_desk.calendar import today_cn

        end_d = date.fromisoformat(str(closes[-1][0])[:10])
        if end_d < today_cn() - timedelta(days=int(max_age_days)):
            out["ok"] = False
            out["stale"] = True
            out["note"] = f"指数缓存过旧（至 {closes[-1][0]}）"
    except Exception:
        pass
    return out


def ensure_bench_cache(*, limit: int = 240, force: bool = False) -> dict[str, Any]:
    """Warm IDX bench bars via HTTP when empty/stale (startup / ops; not gate path).

    Gate path keeps ``allow_http=False`` so ``portfolio_stats`` stays offline.
    """
    status = bench_cache_fresh(limit=limit)
    if status.get("ok") and not force:
        status["refreshed"] = False
        return status
    closes = load_bench_closes(limit=limit, allow_http=True)
    after = bench_cache_fresh(limit=limit)
    after["refreshed"] = True
    after["fetched_n"] = len(closes)
    if not after.get("ok"):
        after["note"] = after.get("note") or "HTTP 灌库后仍不足；检查网络或 REVIEW_BENCH_SYMBOL"
    return after


def _dual_win_block(
    rows: list[dict[str, Any]],
    closes: list[tuple[str, float]],
    *,
    since: str,
    label_all: str,
    label_lit: str,
) -> dict[str, Any]:
    """Internal: one dual-win block (classic or executable remap)."""
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
        lab = str(r.get("outcome_label") or "").strip()
        if lab in {"当日未触达", "未触达", "无成交", "无日线", "样本不足", "待隔日"}:
            continue
        d3 = num(r.get("outcome_day3_pct"))
        xs = num(r.get("excess_d3_pct"))
        if d3 is None or xs is None:
            continue
        if int(r.get("bench_sessions") or 0) < OUTCOME_HORIZON_SESSIONS:
            continue
        day = str(r.get("trade_date") or "")[:10]
        if not day or day < since:
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
        "days": len(days),
        "n": len(all_abs),
        "all": dual_from_pairs(all_abs, all_xs, label=label_all),
        "lit": dual_from_pairs(lit_abs, lit_xs, label=label_lit),
    }


def oos_buy_dual_wins(
    rows: list[dict[str, Any]],
    closes: list[tuple[str, float]] | None = None,
    *,
    since: str | None = None,
) -> dict[str, Any]:
    """Dual wins for scored out-of-sample buy cards (all + lit subset).

    Dedupes per (day, code); only full 3-session horizons count.
    Also reports ``touch`` = same_day_plan executable remap (day low touched
    plan) side-by-side with persisted classic, when bar cache allows.
    """
    start = str(since or EDGE_SHADOW_SINCE)[:10]
    closes = list(closes) if closes is not None else load_bench_closes()
    if len(closes) < 4:
        return {
            "ok": False,
            "bench": REVIEW_BENCH_LABEL,
            "note": f"无{REVIEW_BENCH_LABEL}日线缓存，双胜率暂不可算",
        }

    classic = _dual_win_block(
        rows,
        closes,
        since=start,
        label_all="样本外买卡·classic",
        label_lit="亮过可买·classic",
    )

    touch: dict[str, Any] = {"ok": False, "note": "无日线缓存，可成交口径暂不可算"}
    try:
        from market_desk.adapt.bundle import (
            _remap_rows_for_adapt_standard,
            closes_map_from_bar_cache,
        )

        codes = [str(r.get("code") or "") for r in rows]
        packed = closes_map_from_bar_cache(codes)
        if packed:
            remapped, n_hit = _remap_rows_for_adapt_standard(
                rows, "same_day_plan", closes_map=packed
            )
            if n_hit > 0:
                touch = _dual_win_block(
                    remapped,
                    closes,
                    since=start,
                    label_all="样本外买卡·可触达",
                    label_lit="亮过可买·可触达",
                )
                touch["remapped_n"] = n_hit
                touch["note"] = "same_day_plan：日低触到计划价才计；复盘判据以本列为保守对照"
            else:
                touch = {"ok": False, "note": "日线在但无可重算样本"}
    except Exception as exc:
        touch = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    return {
        "ok": bool(classic.get("ok")),
        "bench": REVIEW_BENCH_LABEL,
        "since": start,
        "days": classic.get("days"),
        "all": classic.get("all"),
        "lit": classic.get("lit"),
        "classic": classic,
        "touch": touch,
        "note": (
            "恶劣市看 xs_win（跑赢大盘）；绝对盈亏看 abs_win。"
            "classic=落库计划价进场；touch=可触达计划价（same_day_plan）。"
            "改规则看相对，优先对照 touch；开不开工用绝对（模拟闸）。"
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
        # Absolute green (真钱) or relative excess (券商只读) both count as candidates.
        return bool(m.get("gate_ok") or m.get("gate_readonly_ok"))
    return False
