"""Out-of-sample test of the pot-bottom (锅底) detector; hypotheses are fixed in TODO.md.

Two samples the detector was not looked at on:

    A (time)          - the same top-800 names, 2023-09 → 2025-12-14 (before discovery)
    B (cross-section) - names ranked 800–1600 by amount, 2025-12-15 → 2026-10-08
    D (discovery)     - the top-800 over discovery dates, printed for reference only

Bars: Tencent ``newfqkline`` (800 forward-adjusted sessions per name, cached in
the temp dir). Each base counts once (first breakout). Entry next open; excess
versus CSI 1000; same-day paired differences against every name of that sample
on the same day, day-clustered with ``stats_tools.safe_t``.

Hypotheses (registered before running):
    H1  all hits − same-day all names, 10d > 0
    H2  S (drop 15–25% and lift ≥ 2×) − same-day all names, 10d ≥ +1% with t ≥ 2
    H3  drop 15–25% − drop ≥ 25%, and lift ≥ 2× − lift < 2×, 10d both > 0

Usage: ``python scripts/oos_pot_bottom.py [--refresh]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import validate_ma_fan as vm  # noqa: E402
from market_desk.filters import is_st, normalize_code  # noqa: E402
from market_desk.ma_fan.sources import load_universe  # noqa: E402
from market_desk.pot_bottom import score_pot_bottom  # noqa: E402
from market_desk.stats_tools import safe_t  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402

CACHE = Path(tempfile.gettempdir()) / "desk_pot_long.json"
BARS = 800
WINDOW = 130
HORIZONS = (5, 10)
DISCOVERY_START = "2025-12-15"
OOS_A_START = "2023-09-01"
URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,,,{n},qfq"


async def _fetch(client: httpx.AsyncClient, sym: str) -> list[dict[str, Any]]:
    """Fetch up to ``BARS`` forward-adjusted daily bars for one Tencent symbol."""
    try:
        resp = await client.get(URL.format(sym=sym, n=BARS), headers={"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"})
        node = ((resp.json().get("data") or {}).get(sym)) or {}
    except Exception:
        return []
    out = []
    for r in node.get("qfqday") or node.get("day") or []:
        if isinstance(r, list) and len(r) >= 6:
            out.append({"date": str(r[0])[:10], "open": float(r[1]), "close": float(r[2]),
                        "high": float(r[3]), "low": float(r[4]), "volume": float(r[5])})
    return out


async def _load(refresh: bool) -> dict[str, Any]:
    """Return ``{"a": [codes], "b": [codes], "bars": {sym: bars}}`` with a temp-dir cache."""
    cache: dict[str, Any] = {"a": [], "b": [], "bars": {}}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    if not cache["a"]:
        top = json.loads(vm.CACHE.read_text(encoding="utf-8"))["names"] if vm.CACHE.exists() else {}
        cache["a"] = list(top)
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        if not cache["b"]:
            rows = await load_universe(client, boards="all", need=1600, min_amount_yi=0.3)
            a = set(cache["a"])
            b = []
            for r in rows[800:1600]:
                code = normalize_code(r.get("code")) or ""
                if code and code not in a and not is_st(str(r.get("name") or "")):
                    b.append(code)
            cache["b"] = b
        syms = [tencent_symbol(c) for c in cache["a"] + cache["b"]] + [vm.BENCH]
        todo = [s for s in syms if not cache["bars"].get(s)]
        sem = asyncio.Semaphore(4)

        async def one(sym: str) -> None:
            async with sem:
                got = await _fetch(client, sym)
                if got:
                    cache["bars"][sym] = got
                await asyncio.sleep(0.2)

        for k in range(0, len(todo), 200):
            await asyncio.gather(*(one(s) for s in todo[k : k + 200]))
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
            print(f"  日线 {min(k + 200, len(todo))}/{len(todo)}", flush=True)
    miss = sum(1 for s in syms if not cache["bars"].get(s))
    print(f"A {len(cache['a'])} 只，B {len(cache['b'])} 只，日线缺 {miss}")
    return cache


def _replay(cache: dict[str, Any], codes: list[str], lo: str, hi: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(hits, base)`` for ``codes`` on scan days ``lo <= day <= hi``."""
    bench = cache["bars"].get(vm.BENCH) or []
    bidx = {b["date"]: j for j, b in enumerate(bench)}
    hits: list[dict[str, Any]] = []
    base: list[dict[str, Any]] = []
    for code in codes:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        last = ""
        for i in range(WINDOW - 1, len(bars) - 1):
            day = bars[i]["date"]
            if day < lo or day > hi or day not in bidx:
                continue
            j = bidx[day]
            fx = {}
            for h in HORIZONS:
                r, b = vm._fwd(bars, i, h), vm._fwd(bench, j, h)
                if r is not None and b is not None:
                    fx[f"x{h}"] = r - b
            base.append({"day": day, **fx})
            got = score_pot_bottom(bars[i - WINDOW + 1 : i + 1])
            if got and got["base_end"] != last:
                last = got["base_end"]
                hits.append({"day": day, "code": code, **got, **fx})
    return hits, base


def _paired(a: list[dict[str, Any]], b: list[dict[str, Any]], key: str) -> tuple[float | None, float | None, int]:
    """Mean same-day difference (a − b), conservative t, and day count."""
    def by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if key in r:
                acc[r["day"]].append(r[key])
        return {d: sum(v) / len(v) for d, v in acc.items()}

    da, db = by_day(a), by_day(b)
    diffs = [da[d] - db[d] for d in sorted(da) if d in db]
    if not diffs:
        return None, None, 0
    return sum(diffs) / len(diffs), safe_t(diffs), len(diffs)


def _fmt(res: tuple[float | None, float | None, int]) -> str:
    m, t, n = res
    if m is None:
        return f"{'-':>20}"
    ts = f"t{t:+.1f}" if t is not None else "t-"
    return f"{m:>+7.2f} {ts:>6} {n:>4}天"


def _report(name: str, hits: list[dict[str, Any]], base: list[dict[str, Any]]) -> None:
    """Print H1–H3 for one sample."""
    s_pred: Callable[[dict[str, Any]], bool] = lambda h: 15 <= h["drop_pct"] < 25 and h["lift_vol"] >= 2  # noqa: E731
    days = {h["day"] for h in hits}
    print(f"\n== {name}：锅底 {len(hits)} 次，{len(days)} 天，子集 S {sum(1 for h in hits if s_pred(h))} 次 ==")
    print(f"{'检验':<34}" + "".join(f"{f'{h}日':>20}" for h in HORIZONS))
    lines = [
        ("H1 全部 − 同日全体", hits, base),
        ("H2 S(前跌15–25%且放量≥2×) − 全体", [h for h in hits if s_pred(h)], base),
        ("H3a 前跌15–25% − ≥25%", [h for h in hits if 15 <= h["drop_pct"] < 25], [h for h in hits if h["drop_pct"] >= 25]),
        ("H3b 放量≥2× − <2×", [h for h in hits if h["lift_vol"] >= 2], [h for h in hits if h["lift_vol"] < 2]),
        ("参考 前跌15–25% − 全体", [h for h in hits if 15 <= h["drop_pct"] < 25], base),
        ("参考 放量≥2× − 全体", [h for h in hits if h["lift_vol"] >= 2], base),
    ]
    for label, a, b in lines:
        print(f"{label:<34}" + "".join(_fmt(_paired(a, b, f"x{h}")) for h in HORIZONS))


def main() -> None:
    """Run the out-of-sample replay for samples A, B and the discovery reference D."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    cache = asyncio.run(_load(args.refresh))
    t0 = time.time()
    end = "2026-10-08"
    a_hits, a_base = _replay(cache, cache["a"], OOS_A_START, "2025-12-14")
    b_hits, b_base = _replay(cache, cache["b"], DISCOVERY_START, end)
    d_hits, d_base = _replay(cache, cache["a"], DISCOVERY_START, end)
    print(f"回放 {time.time() - t0:.0f}s")
    _report(f"A 时间外（前 800 只，{OOS_A_START} → 2025-12-14）", a_hits, a_base)
    _report(f"B 换股票（800–1600 名，{DISCOVERY_START} → {end}）", b_hits, b_base)
    _report("D 发现期参考（前 800 只，同期；不作判定）", d_hits, d_base)


if __name__ == "__main__":
    main()
