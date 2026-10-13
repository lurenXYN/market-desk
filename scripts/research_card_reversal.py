"""Does short-term reversal (R20) rank the desk's own buy cards? Hypotheses are fixed in TODO.md.

R20 = close on the session before signal day D over the close 20 sessions
earlier, ranked against the same-day R20 of the ~1450 liquid names cached by
``scripts/oos_pot_bottom.py`` (forward-adjusted). Outcome = buy-at-close 3-day
% minus CSI 1000 (``audit_attribution`` bars and card loading, deduped one per
day and code). Stocks only; ETFs have no comparable cross-section.

    C1  top third of R20 percentile − bottom third, 3-day excess < 0
    C2  the same after dropping 日线降 cards (is R20 just 日线降 renamed?)
Support: C1 ≤ −1% with t ≤ −2. These cards overlap the 日线降 discovery, so a
pass is corroborating only.

Usage: ``python scripts/research_card_reversal.py [--db PATH] [--until 2026-09-30]``
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_attribution as aa  # noqa: E402
import oos_pot_bottom as pot  # noqa: E402
from market_desk.filters import is_chinext_or_star, is_main_board  # noqa: E402
from market_desk.stats_tools import safe_t  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402

BENCH = "sh000852"
LOOK = 20


def _r20_by_day(cache: dict[str, Any]) -> tuple[dict[str, list[float]], dict[str, dict[str, float]]]:
    """Return sorted same-day R20 lists and per-symbol ``{date: R20}`` from the factor cache."""
    cross: dict[str, list[float]] = defaultdict(list)
    per: dict[str, dict[str, float]] = {}
    for sym, bars in cache["bars"].items():
        if sym == BENCH:
            continue
        m: dict[str, float] = {}
        for i in range(LOOK, len(bars)):
            c0 = float(bars[i - LOOK]["close"])
            if c0 > 0:
                v = (float(bars[i]["close"]) / c0 - 1) * 100
                m[bars[i]["date"]] = v
                cross[bars[i]["date"]].append(v)
        per[sym] = m
    for v in cross.values():
        v.sort()
    return cross, per


def _prev_day(dates: list[str], day: str) -> str | None:
    """Return the last date strictly before ``day`` in the sorted ``dates``."""
    k = bisect.bisect_left(dates, day)
    return dates[k - 1] if k > 0 else None


def _card_rows(until: str) -> list[dict[str, Any]]:
    """Deduped stock buy cards with 3-day close-buy excess over CSI 1000."""
    cards, _ = aa._load_cards()
    cards = [
        c for c in aa._dedupe(cards)
        if c["day"] <= until and (is_main_board(c["code"]) or is_chinext_or_star(c["code"]))
    ]
    keys = {aa._key(c["code"]) for c in cards} | {BENCH}
    bars = asyncio.run(aa._load_bars(keys, refresh=False))
    bm = bars.get(BENCH) or {}
    out = []
    for c in cards:
        st = bars.get(aa._key(c["code"])) or {}
        d3 = aa._nth_after(st, c["day"], 3)
        if c["day"] not in st or not d3 or c["day"] not in bm or d3 not in bm:
            continue
        r = aa._pct(st[d3][0], st[c["day"]][0])
        mkt = aa._pct(bm[d3][0], bm[c["day"]][0])
        out.append({**c, "x3": r - mkt})
    return out


def _attr_r20(bars: dict[str, list[Any]], prev: str) -> float | None:
    """R20 on ``prev`` from ``audit_attribution`` bars (unadjusted; fallback only)."""
    ds = sorted(d for d in bars if d <= prev)
    if len(ds) <= LOOK or ds[-1] != prev:
        return None
    c0 = float(bars[ds[-1 - LOOK]][0] or 0)
    return (float(bars[prev][0]) / c0 - 1) * 100 if c0 > 0 else None


def _attach_pct(rows: list[dict[str, Any]], cross: dict[str, list[float]], per: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    """Attach the card's R20 and its same-day percentile (both as of the prior session).

    The card's own R20 comes from the forward-adjusted factor cache when the name
    is there, else from the unadjusted attribution bars; the percentile is always
    against the cached cross-section of that day.
    """
    attr = json.loads(aa.CACHE.read_text(encoding="utf-8")) if aa.CACHE.exists() else {}
    dates = sorted(cross)
    out = []
    for r in rows:
        prev = _prev_day(dates, r["day"])
        xs = cross.get(prev or "") or []
        if prev is None or len(xs) < 200:
            continue
        v = (per.get(tencent_symbol(r["code"])) or {}).get(prev)
        src = "qfq"
        if v is None:
            v, src = _attr_r20(attr.get(aa._key(r["code"])) or {}, prev), "raw"
        if v is None:
            continue
        out.append({**r, "r20": v, "src": src, "pct": bisect.bisect_left(xs, v) / len(xs)})
    return out


def _group(rows: list[dict[str, Any]]) -> tuple[int, float | None, float | None, int]:
    """Count, day-clustered mean excess, conservative t and day count."""
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        acc[r["day"]].append(r["x3"])
    series = [sum(v) / len(v) for _d, v in sorted(acc.items())]
    if len(series) < 3:
        return len(rows), None, None, len(series)
    return len(rows), sum(series) / len(series), safe_t(series), len(series)


def _diff(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> tuple[float | None, float | None, int]:
    """Mean of same-day (a − b) on days with both groups, conservative t, day count."""
    def by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            acc[r["day"]].append(r["x3"])
        return {d: sum(v) / len(v) for d, v in acc.items()}

    da, db = by_day(a), by_day(b)
    s = [da[d] - db[d] for d in sorted(da) if d in db]
    if len(s) < 3:
        return None, None, len(s)
    return sum(s) / len(s), safe_t(s), len(s)


def _fmt_g(g: tuple[int, float | None, float | None, int]) -> str:
    n, m, t, d = g
    if m is None:
        return f"n={n:<4} -"
    return f"n={n:<4} {m:+6.2f}%  t{(t or 0):+5.1f}  {d}天"


def _fmt_d(g: tuple[float | None, float | None, int]) -> str:
    m, t, d = g
    if m is None:
        return f"- ({d}天)"
    return f"{m:+6.2f}%  t{(t or 0):+5.1f}  {d}天"


def _terciles(title: str, rows: list[dict[str, Any]]) -> tuple[float | None, float | None, int]:
    """Print bottom / middle / top thirds of the R20 percentile and their spread."""
    lo = [r for r in rows if r["pct"] < 1 / 3]
    mid = [r for r in rows if 1 / 3 <= r["pct"] < 2 / 3]
    hi = [r for r in rows if r["pct"] >= 2 / 3]
    print(f"\n== {title}（{len(rows)} 张）==")
    for label, g in (("低 1/3（近 20 日涨得少）", lo), ("中 1/3", mid), ("高 1/3（涨得多）", hi)):
        print(f"  {label:<20}{_fmt_g(_group(g))}")
    d = _diff(hi, lo)
    print(f"  {'高 − 低（同日配对）':<18}{_fmt_d(d)}")
    return d


def main() -> None:
    """Load cards and the factor cache, then print C1 / C2 and the 日线 cross-tab."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=None, help="read by research_db at import time")
    ap.add_argument("--until", default="2026-09-30")
    args = ap.parse_args()
    if not pot.CACHE.exists():
        raise SystemExit(f"缺缓存 {pot.CACHE}：先跑 scripts/oos_pot_bottom.py")
    cross, per = _r20_by_day(json.loads(pot.CACHE.read_text(encoding="utf-8")))
    rows = _attach_pct(_card_rows(args.until), cross, per)
    print(
        f"买卡（同日同代码一次，个股）有三日结果且有 R20 分位：{len(rows)} 张，{len({r['day'] for r in rows})} 天"
        f"（前复权 {sum(r['src'] == 'qfq' for r in rows)}，未复权补算 {sum(r['src'] == 'raw' for r in rows)}）"
    )
    pcts = sorted(r["pct"] for r in rows)
    if pcts:
        print(f"R20 分位中位数 {pcts[len(pcts) // 2]:.0%}，高于 2/3 的占 {sum(p >= 2 / 3 for p in pcts) / len(pcts):.0%}")
    c1 = _terciles("C1 全部买卡", rows)
    c2 = _terciles("C2 去掉「日线降」", [r for r in rows if r["trend"] != "降"])
    print("\n日线状态 × R20 三分（张数 / 三日超额均值，不聚类）")
    for tr in ("升", "其他", "降"):
        cells = []
        for lo, hi in ((0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.01)):
            g = [r["x3"] for r in rows if r["trend"] == tr and lo <= r["pct"] < hi]
            cells.append(f"{len(g):>3} / {sum(g) / len(g):+5.2f}%" if g else f"{0:>3} /    -  ")
        print(f"  日线{tr:<3}" + "   ".join(cells))
    ok = c1[0] is not None and c1[0] <= -1 and c1[1] is not None and c1[1] <= -2
    print(f"\n判定 C1：{'支持' if ok else '不支持'}；C2 高 − 低 {_fmt_d(c2)}")


if __name__ == "__main__":
    main()
