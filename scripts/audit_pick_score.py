"""Offline audit: does the pick score rank stored buy signals by their 3-day outcome?

Rebuilds each scored buy signal as of its signal day (payload trend / board /
gate / ready / pct, zt count and chip-volume context from prior-day bars) and
scores it twice: rule points only, and rule + walk-forward history nudges that
only use signals whose 3-day outcome was known before that day.

Not reproducible offline (skipped): live position vs plan, holder change, ma-fan.

Usage: ``python scripts/audit_pick_score.py [--refresh]``  (reads data/desk.db read-only)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import market_desk.config as cfg  # noqa: E402

_SCRATCH = Path(tempfile.mkdtemp()) / "unused.db"
cfg.DB_PATH = _SCRATCH
import market_desk.db as desk_db  # noqa: E402

desk_db.DB_PATH = _SCRATCH
desk_db.DATA_DIR = _SCRATCH.parent

import audit_factors as af  # noqa: E402
from market_desk.chip_volume import build_cv  # noqa: E402
from market_desk.pick_score import build_history_stats, score_pick  # noqa: E402

DB = ROOT / "data" / "desk.db"
LAG_DAYS = 4
DROP_RULE = {"日线趋势", "板块", "可买入", "当日涨幅"}


def PROP_RULE(label: str, pts: float) -> float:  # noqa: N802
    """Proposed rule points given the current rule points of one factor."""
    if label in ("日线趋势", "板块", "可买入", "昨日量能"):
        return 0.0
    if label == "当日涨幅":
        return pts if pts <= -12 else 0.0
    if label == "出信号时段":
        return -6.0
    if label == "量能趋势":
        return -5.0
    if label == "股性":
        return -4.0 if pts < 0 else (3.0 if pts > 0 else 0.0)
    return pts


def load_rows() -> list[dict[str, Any]]:
    """Load scored buy signals with decoded payload and flattened plan price."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, trade_date, signaled_at, code, name, kind, signal_type, ready,
               price, fill_price, payload, outcome_day1_pct, outcome_day3_pct
        FROM signals
        WHERE signal_type LIKE 'buy%' AND outcome_day3_pct IS NOT NULL
        """
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"] or "{}")
        except Exception:
            d["payload"] = {}
        d["code"] = str(d["code"]).zfill(6)
        d["trade_date"] = str(d["trade_date"])[:10]
        p = d["payload"]
        d["entry"] = af._f(d["fill_price"]) or af._f(p.get("plan_price")) or af._f(d["price"])
        out.append(d)
    return out


def zt_ytd(bars: list[dict[str, Any]], day: str) -> int | None:
    """Count calendar-year limit-up closes (≥9.8%) before ``day``."""
    year = day[:4]
    prior = [b for b in bars if b["date"][:4] == year and b["date"] < day]
    if not prior:
        return None
    return sum(1 for b in prior if (b.get("pct") or 0) >= 9.8)


INDEX_CACHE = Path(tempfile.gettempdir()) / "md-opt-index-cache.json"
_MKT: dict[str, float] = {}


def index_pct(sym: str = cfg.CV_IVOL_INDEX) -> dict[str, float]:
    """Daily pct of the ivol index over ~2 years (temp-dir cache shared with research scripts)."""
    if _MKT:
        return _MKT
    cache: dict[str, Any] = {}
    if INDEX_CACHE.exists():
        try:
            cache = json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if not cache.get(sym):
        import httpx

        url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,,,500,qfq"
        try:
            node = (httpx.get(url, timeout=10.0).json().get("data") or {}).get(sym) or {}
            rows = node.get("qfqday") or node.get("day") or []
            cache[sym] = [{"date": str(p[0])[:10], "close": af._f(p[2])} for p in rows if len(p) >= 3]
            INDEX_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        except Exception:
            return {}
    prev = None
    for b in cache.get(sym) or []:
        if b.get("close") and prev:
            _MKT[b["date"]] = (b["close"] / prev - 1.0) * 100.0
        prev = b.get("close") or prev
    return _MKT


def candidate(row: dict[str, Any], bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild the live-shaped candidate row score_pick expects, as of the signal day."""
    p = row["payload"]
    before = [b for b in bars if b["date"] < row["trade_date"]]
    cand = {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "kind": row.get("kind") or "stock",
        "signal_type": row["signal_type"],
        "signaled_at": row["signaled_at"],
        "ready": row["ready"],
        "payload": p,
        "trend_ok": bool(p.get("trend_ok")),
        "trend_down": bool(p.get("trend_down")),
        "daily_trend": p.get("trend"),
        "board_match": p.get("board_match"),
        "live_pct": af._f(p.get("pct")),
    }
    if cand["kind"] != "etf":
        cand["zt_ytd"] = zt_ytd(bars, row["trade_date"])
        cv = build_cv(before[-130:], row["entry"], index_pct()) if before and row["entry"] else None
        if cv:
            cand["cv"] = cv
            p["cv"] = cv
    return cand


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation (average ranks for ties)."""
    n = len(xs)
    if n < 5:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def _mean(v: list[float]) -> float | None:
    return sum(v) / len(v) if v else None


def band_table(scored: list[dict[str, Any]], key: str) -> None:
    """Print outcome by score band for one score variant."""
    bands = [("≥75 优先", 75, 101), ("60-74 可以考虑", 60, 75), ("45-59 谨慎", 45, 60), ("<45 放弃", -1, 45)]
    base = af.day_balanced(scored, lambda r: r["d3"] > 0)
    print(f"{'档位':<14}{'n':>5}{'天':>4}{'胜率3':>8}{'差pp':>7}{'均d3%':>8}{'均d1%':>8}")
    for lab, lo, hi in bands:
        sub = [r for r in scored if lo <= r[key] < hi]
        if not sub:
            print(f"{lab:<14}{0:>5}")
            continue
        w3 = af.day_balanced(sub, lambda r: r["d3"] > 0)
        d1 = _mean([r["d1"] for r in sub if r["d1"] is not None])
        print(
            f"{lab:<14}{len(sub):>5}{len({r['day'] for r in sub}):>4}{w3:>8.1f}{w3 - base:>+7.1f}"
            f"{_mean([r['d3'] for r in sub]):>8.2f}{(d1 or 0):>8.2f}"
        )


def within_day(scored: list[dict[str, Any]], key: str) -> None:
    """Average per-day spread between the top and bottom third by score."""
    spreads, wins, rhos, top1 = [], [], [], []
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in scored:
        by_day.setdefault(r["day"], []).append(r)
    for rows in by_day.values():
        if len(rows) < 6:
            continue
        rows = sorted(rows, key=lambda r: -r[key])
        k = max(2, len(rows) // 3)
        top, bot = rows[:k], rows[-k:]
        spreads.append(_mean([r["d3"] for r in top]) - _mean([r["d3"] for r in bot]))
        wins.append(
            100.0 * (sum(r["d3"] > 0 for r in top) / k - sum(r["d3"] > 0 for r in bot) / k)
        )
        day_mean = _mean([r["d3"] for r in rows])
        top1.append(rows[0]["d3"] - day_mean)
        rho = spearman([r[key] for r in rows], [r["d3"] for r in rows])
        if rho is not None:
            rhos.append(rho)
    pos = sum(1 for s in spreads if s > 0)
    print(
        f"  当日前1/3 vs 后1/3：均d3差 {_mean(spreads):+.2f}pp，胜率差 {_mean(wins):+.1f}pp，"
        f"前1/3更好的天数 {pos}/{len(spreads)}"
    )
    print(f"  当日最高分那只 vs 当日均值：均d3 {_mean(top1):+.2f}pp；当日秩相关均值 {_mean(rhos):+.3f}")


def factor_table(scored: list[dict[str, Any]]) -> None:
    """Win rate when each factor fires, split by the sign it was scored with."""
    base = af.day_balanced(scored, lambda r: r["d3"] > 0)
    acc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in scored:
        for f in r["factors"]:
            pts = float(f["points"])
            sign = "+" if pts > 0 else ("-" if pts < 0 else "0")
            acc.setdefault((f["label"], sign), []).append(r)
    print(f"{'因子':<16}{'方向':>4}{'n':>5}{'天':>4}{'胜率3':>8}{'差pp':>7}{'均d3%':>8}  判断")
    for (lab, sign), sub in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        days = len({r["day"] for r in sub})
        w3 = af.day_balanced(sub, lambda r: r["d3"] > 0)
        edge = w3 - base
        verdict = ""
        if len(sub) >= 15 and days >= 4 and sign in "+-":
            agree = (edge > 0) == (sign == "+")
            verdict = "一致" if agree and abs(edge) >= 3 else ("相反" if not agree and abs(edge) >= 3 else "无区分")
        print(
            f"{lab:<16}{sign:>4}{len(sub):>5}{days:>4}{w3:>8.1f}{edge:>+7.1f}"
            f"{_mean([r['d3'] for r in sub]):>8.2f}  {verdict}"
        )


def main() -> None:
    rows = [r for r in load_rows() if r["entry"]]
    codes = sorted({r["code"] for r in rows})
    cache = asyncio.run(af.fetch_all(codes, refresh="--refresh" in sys.argv, need_flow=False))
    days = sorted({r["trade_date"] for r in rows})
    idx = {d: i for i, d in enumerate(days)}
    for r in rows:
        r["cand"] = candidate(r, (cache.get(r["code"]) or {}).get("bars") or [])
    import market_desk.pick_score as ps

    empty = build_history_stats([])
    scored = []
    for r in rows:
        known = [h for h in rows if idx[h["trade_date"]] <= idx[r["trade_date"]] - LAG_DAYS]
        stats = build_history_stats(known)
        rule = score_pick(r["cand"], empty)
        hyb = score_pick(r["cand"], stats)
        ps.PICK_HIST_PP_TO_PTS, ps.PICK_HIST_MAX_ADJ = 0.4, 10.0
        wide = score_pick(r["cand"], stats)
        ps.PICK_HIST_PP_TO_PTS, ps.PICK_HIST_MAX_ADJ = 0.4, 8.0
        mid = score_pick(r["cand"], stats)
        ps.PICK_HIST_PP_TO_PTS, ps.PICK_HIST_MAX_ADJ = cfg.PICK_HIST_PP_TO_PTS, cfg.PICK_HIST_MAX_ADJ
        mid_pts = {f["label"]: float(f["points"]) for f in mid["factors"]}
        rule_pts = {f["label"]: float(f["points"]) for f in rule["factors"]}
        wide_pts = {f["label"]: float(f["points"]) for f in wide["factors"]}
        # History-led: drop rule points that ran against the data; keep their history nudge.
        led = 60.0 + sum(
            (wide_pts[k] - rule_pts.get(k, 0.0)) if k in DROP_RULE else wide_pts[k]
            for k in wide_pts
        )
        prop = 60.0 + sum(
            (mid_pts[k] - rule_pts.get(k, 0.0)) + PROP_RULE(k, rule_pts.get(k, 0.0))
            for k in mid_pts
        )
        scored.append({
            "day": r["trade_date"],
            "d3": float(r["outcome_day3_pct"]),
            "d1": af._f(r["outcome_day1_pct"]),
            "rule": rule["score"],
            "hyb": hyb["score"],
            "led": int(round(max(0.0, min(100.0, led)))),
            "prop": int(round(max(0.0, min(100.0, prop)))),
            "factors": rule["factors"],
            "known_n": len(known),
        })
    base = af.day_balanced(scored, lambda r: r["d3"] > 0)
    print(f"样本 {len(scored)} 条 · {len(days)} 天 · 基准三日胜率(按天均衡) {base:.1f}%")
    print(f"历史修正可用（已知样本≥30）的行：{sum(1 for s in scored if s['known_n'] >= 30)}")
    variants = (
        ("rule", "现行规则分（不含历史修正）"),
        ("hyb", "现行规则 + 历史修正（只用当时已知结果）"),
        ("led", "方案：去掉趋势/板块/可买/涨幅规则分，历史修正放大(0.4分/pp，±10)"),
        ("prop", "拟调整（样本内）：见 PROP_RULE，历史修正 0.4分/pp ±8"),
    )
    for key, lab in variants:
        rho = spearman([s[key] for s in scored], [s["d3"] for s in scored])
        print(f"\n== {lab} · 分数与三日收益秩相关 {rho:+.3f} ==")
        band_table(scored, key)
        within_day(scored, key)
    half = days[len(days) // 2]
    print(f"\n== 分段稳定性（前段 < {half} ≤ 后段）：当日前1/3 vs 后1/3 ==")
    for key, lab in variants:
        for seg, sub in (("前段", [s for s in scored if s["day"] < half]), ("后段", [s for s in scored if s["day"] >= half])):
            print(f"  [{lab[:6]}·{seg}]", end="")
            within_day(sub, key)
    print("\n== 各因子（规则分方向）命中时的表现 ==")
    factor_table(scored)
    for seg, sub in (("前段", [s for s in scored if s["day"] < half]), ("后段", [s for s in scored if s["day"] >= half])):
        print(f"\n== 各因子 · {seg} ==")
        factor_table(sub)


if __name__ == "__main__":
    main()
