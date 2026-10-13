"""Post-announcement drift after A-share earnings forecasts (业绩预告); hypotheses are fixed in TODO.md.

Forecasts: East Money datacenter ``RPT_PUBLIC_OP_NEWPREDICT``, net profit to
shareholders only (indicator 004), earliest notice per name and report period,
cached in the temp dir.

Entry: the open of the first session after the notice date (the date carries no
time, so this never peeks whether the notice came pre-open or after the close);
skipped when that open is within 0.5% of the limit-up. Hold 10 / 20 sessions.
Excess = event return − mean return of every cached name over the same window;
day-clustered by entry day with ``stats_tools.safe_t`` (lag = horizon).

Groups:
    BIG  - type 预增 with growth lower bound ≥ 100%
    GOOD - 预增 / 略增 / 扭亏 / 续盈
    BAD  - 首亏 / 预减 / 略减 / 续亏

Hypotheses (registered before running):
    E1  BIG 20d excess > 0
    E2  BAD 20d excess < 0
    E3  BIG − BAD 20d > 0
Rule: same sign in all four cells (X / Y × P1 / P2) and pooled |t| ≥ 2.5.

Prices come from the ``scripts/oos_pot_bottom.py`` cache (run it first).

Usage: ``python scripts/research_forecast.py [--refresh]``
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import oos_pot_bottom as pot  # noqa: E402
from market_desk.stats_tools import safe_t  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402

CACHE = Path(tempfile.gettempdir()) / "desk_forecast.json"
URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
COLUMNS = "SECURITY_CODE,NOTICE_DATE,REPORT_DATE,PREDICT_TYPE,ADD_AMP_LOWER,ADD_AMP_UPPER"
REPORT_DATES = (
    "2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
)
HORIZONS = (10, 20)
PERIODS = (("P1", "2023-10-01", "2025-02-28"), ("P2", "2025-03-01", "2026-09-30"))
GOOD = {"预增", "略增", "扭亏", "续盈"}
BAD = {"首亏", "预减", "略减", "续亏"}
BIG_GROWTH = 100.0


def _fetch_period(client: httpx.Client, report: str) -> list[dict[str, Any]]:
    """Return every net-profit forecast row for one report period."""
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {
            "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
            "columns": COLUMNS,
            "pageSize": 500,
            "pageNumber": page,
            "sortColumns": "NOTICE_DATE,SECURITY_CODE",
            "sortTypes": "1,1",
            "filter": f"(REPORT_DATE='{report}')(PREDICT_FINANCE_CODE=\"004\")",
        }
        resp = client.get(URL, params=params)
        res = (resp.json() or {}).get("result") or {}
        rows += res.get("data") or []
        if page >= int(res.get("pages") or 0):
            return rows
        page += 1
        time.sleep(0.3)


def _load_forecasts(refresh: bool) -> dict[str, list[dict[str, Any]]]:
    """Return ``{report_date: rows}``, fetching missing periods into the temp cache."""
    cache: dict[str, list[dict[str, Any]]] = {}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
    with httpx.Client(timeout=30, headers=headers, trust_env=False) as client:
        for rep in REPORT_DATES:
            if cache.get(rep):
                continue
            cache[rep] = _fetch_period(client, rep)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  预告 {rep}: {len(cache[rep])} 条", flush=True)
    return cache


def _events(forecasts: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Earliest notice per (code, report period), tagged with its group."""
    first: dict[tuple[str, str], dict[str, Any]] = {}
    for rep, rows in forecasts.items():
        for r in rows:
            code = str(r.get("SECURITY_CODE") or "")
            day = str(r.get("NOTICE_DATE") or "")[:10]
            if not code or not day:
                continue
            key = (code, rep)
            if key not in first or day < first[key]["notice"]:
                first[key] = {
                    "code": code,
                    "report": rep,
                    "notice": day,
                    "type": str(r.get("PREDICT_TYPE") or ""),
                    "lo": r.get("ADD_AMP_LOWER"),
                }
    out = []
    for ev in first.values():
        t = ev["type"]
        ev["good"] = t in GOOD
        ev["bad"] = t in BAD
        ev["big"] = t == "预增" and ev["lo"] is not None and float(ev["lo"]) >= BIG_GROWTH
        out.append(ev)
    return out


def _window_ret(bars: list[dict[str, Any]], e: int, h: int) -> float | None:
    """% from the open of session ``e`` to the close ``h`` sessions later (entry day counts)."""
    if e + h - 1 >= len(bars):
        return None
    entry = float(bars[e].get("open") or 0)
    return (float(bars[e + h - 1]["close"]) / entry - 1) * 100 if entry > 0 else None


def _baseline(cache: dict[str, Any], codes: list[str]) -> dict[str, dict[int, float]]:
    """Mean same-window return of every cached name per entry date and horizon."""
    acc: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for code in codes:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        for e in range(1, len(bars)):
            for h in HORIZONS:
                r = _window_ret(bars, e, h)
                if r is not None:
                    acc[bars[e]["date"]][h].append(r)
    return {d: {h: sum(v) / len(v) for h, v in hs.items() if v} for d, hs in acc.items()}


def _price_events(
    events: list[dict[str, Any]], cache: dict[str, Any], codes: set[str], base: dict[str, dict[int, float]]
) -> list[dict[str, Any]]:
    """Attach entry date, opening gap and same-day-paired excess returns."""
    out = []
    for ev in events:
        if ev["code"] not in codes:
            continue
        bars = cache["bars"].get(tencent_symbol(ev["code"])) or []
        e = next((i for i, b in enumerate(bars) if b["date"] > ev["notice"]), None)
        if e is None or e < 1:
            continue
        prev = float(bars[e - 1]["close"])
        gap = (float(bars[e]["open"]) / prev - 1) * 100 if prev > 0 else 0.0
        lim = (20.0 if ev["code"].startswith(("30", "68")) else 10.0) - 0.5
        if gap >= lim:
            continue
        row = {**ev, "entry": bars[e]["date"], "gap": gap}
        for h in HORIZONS:
            r = _window_ret(bars, e, h)
            b = (base.get(bars[e]["date"]) or {}).get(h)
            if r is not None and b is not None:
                row[f"x{h}"] = r - b
        out.append(row)
    return out


def _stat(rows: list[dict[str, Any]], key: str, lag: int) -> tuple[float | None, float | None, int, int]:
    """Day-clustered mean excess, conservative t, event count and entry-day count."""
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if key in r:
            acc[r["entry"]].append(r[key])
    series = [sum(v) / len(v) for _d, v in sorted(acc.items())]
    if len(series) < 3:
        return None, None, sum(len(v) for v in acc.values()), len(series)
    return sum(series) / len(series), safe_t(series, lag=lag), sum(len(v) for v in acc.values()), len(series)


def _diff(a: list[dict[str, Any]], b: list[dict[str, Any]], key: str, lag: int) -> tuple[float | None, float | None, int, int]:
    """BIG − BAD on entry days that have both; same output shape as ``_stat``."""
    def by_day(rows: list[dict[str, Any]]) -> dict[str, float]:
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            if key in r:
                acc[r["entry"]].append(r[key])
        return {d: sum(v) / len(v) for d, v in acc.items()}

    da, db = by_day(a), by_day(b)
    series = [da[d] - db[d] for d in sorted(da) if d in db]
    if len(series) < 3:
        return None, None, 0, len(series)
    return sum(series) / len(series), safe_t(series, lag=lag), 0, len(series)


def _fmt(s: tuple[float | None, float | None, int, int]) -> str:
    m, t, n, d = s
    if m is None:
        return f"{'-':>26}"
    ts = f"{t:+.1f}" if t is not None else "-"
    return f"{m:>+6.2f}% t{ts:>5} {n:>5}次{d:>4}天"


def main() -> None:
    """Fetch forecasts, price the events and apply the registered rule."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if not pot.CACHE.exists():
        raise SystemExit(f"缺缓存 {pot.CACHE}：先跑 scripts/oos_pot_bottom.py")
    events = _events(_load_forecasts(args.refresh))
    cache = json.loads(pot.CACHE.read_text(encoding="utf-8"))
    t0 = time.time()
    all_codes = list(cache["a"]) + list(cache["b"])
    base = _baseline(cache, all_codes)
    samples = {
        "X": _price_events(events, cache, set(cache["a"]), base),
        "Y": _price_events(events, cache, set(cache["b"]), base),
    }
    print(f"事件 {len(events)} 个（全市场），进场可算 X {len(samples['X'])} / Y {len(samples['Y'])}；用时 {time.time() - t0:.0f}s")
    groups = {
        "BIG 大幅预增": lambda r: r["big"],
        "GOOD 利好": lambda r: r["good"],
        "BAD 利空": lambda r: r["bad"],
    }
    cells = [(s, p, lo, hi) for s in samples for p, lo, hi in PERIODS]
    for h in HORIZONS:
        key = f"x{h}"
        print(f"\n===== {h} 日（公告后首个交易日开盘买，减同日全体） =====")
        print(f"{'分组':<14}" + "".join(f"{f'{s}·{p}':>27}" for s, p, _lo, _hi in cells) + f"{'合并':>27}")
        per_group: dict[str, list[tuple]] = {}
        for name, pred in groups.items():
            stats = [
                _stat([r for r in samples[s] if pred(r) and lo <= r["notice"] <= hi], key, h)
                for s, _p, lo, hi in cells
            ]
            pooled = _stat([r for s in samples for r in samples[s] if pred(r) and PERIODS[0][1] <= r["notice"] <= PERIODS[-1][2]], key, h)
            per_group[name] = stats + [pooled]
            print(f"{name:<14}" + "".join(f"{_fmt(x):>27}" for x in stats + [pooled]))
        diffs = [
            _diff(
                [r for r in samples[s] if r["big"] and lo <= r["notice"] <= hi],
                [r for r in samples[s] if r["bad"] and lo <= r["notice"] <= hi],
                key,
                h,
            )
            for s, _p, lo, hi in cells
        ]
        allr = [r for s in samples for r in samples[s]]
        pooled_diff = _diff([r for r in allr if r["big"]], [r for r in allr if r["bad"]], key, h)
        print(f"{'BIG − BAD':<14}" + "".join(f"{_fmt(x):>27}" for x in diffs + [pooled_diff]))
        if h == 20:
            print("\n判定（20 日）：")
            for label, stats, sign in (
                ("E1 大幅预增 > 0", per_group["BIG 大幅预增"], 1),
                ("E2 利空 < 0", per_group["BAD 利空"], -1),
                ("E3 大幅预增 − 利空 > 0", diffs + [pooled_diff], 1),
            ):
                four, pooled = stats[:4], stats[4]
                same = sum(1 for m, *_ in four if m is not None and m * sign > 0)
                ok = same == 4 and pooled[1] is not None and pooled[1] * sign >= 2.5
                print(f"  {label:<22} {'成立' if ok else '不成立'}（同号 {same}/4，合并 t {pooled[1] if pooled[1] is None else round(pooled[1], 1)}）")
    big = [r for s in samples for r in samples[s] if r["big"]]
    if big:
        print("\n参考（不作判定）：大幅预增按进场日开盘跳空分组，20 日超额")
        for label, pred in (("跳空 < 2%", lambda g: g < 2), ("2–5%", lambda g: 2 <= g < 5), ("≥ 5%", lambda g: g >= 5)):
            print(f"  {label:<10}{_fmt(_stat([r for r in big if pred(r['gap'])], 'x20', 20))}")


if __name__ == "__main__":
    main()
