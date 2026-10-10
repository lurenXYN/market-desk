"""Re-weight the MA-fan pattern score on replayed hits, fitting on early sessions only.

Replays v8 list hits (failed fans excluded) with every score part and raw feature,
then reports each one's daily rank IC against forward excess return. Days before
``--split`` are the training half; later days are only used to check the result.

Usage::

    .venv/Scripts/python.exe scripts/tune_ma_fan_score.py [--n 800] [--split 2026-05-01] [--replay]
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from market_desk.ma_fan.job import MA_FAN_SCORE_BARS  # noqa: E402
from market_desk.ma_fan.pattern import MA_FAN_FORMULA_VERSION, score_pattern  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402
from validate_ma_fan import BENCH, HORIZONS, _fwd, _load  # noqa: E402

ROWS = Path(tempfile.gettempdir()) / "desk_mafan_rows.json"
MIN_DAY_HITS = 8


def _replay(cache: dict[str, Any]) -> list[dict[str, Any]]:
    """Return list hits with ``score`` / ``parts`` / ``feat`` and forward excess ``x{h}``."""
    bench = cache["bars"].get(BENCH) or []
    bench_idx = {str(b["date"]): j for j, b in enumerate(bench)}
    rows: list[dict[str, Any]] = []
    t0 = time.time()
    for code in cache["names"]:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        for i in range(MA_FAN_SCORE_BARS - 1, len(bars) - 1):
            got = score_pattern(bars[i - MA_FAN_SCORE_BARS + 1 : i + 1])
            if not got:
                continue
            day = str(bars[i]["date"])
            j = bench_idx.get(day)
            row = {"code": code, "day": day, "score": got["score"], "parts": got["parts"], "feat": got["feat"]}
            for h in HORIZONS:
                r = _fwd(bars, i, h)
                b = _fwd(bench, j, h) if j is not None else None
                if r is not None and b is not None:
                    row[f"x{h}"] = r - b
            rows.append(row)
    print(f"回放 {len(rows)} 个列表命中日（{time.time() - t0:.0f}s）")
    return rows


def load_rows(n: int, replay: bool) -> list[dict[str, Any]]:
    """Load cached replay rows for this formula version, or replay and cache them."""
    if ROWS.exists() and not replay:
        body = json.loads(ROWS.read_text(encoding="utf-8"))
        if body.get("version") == MA_FAN_FORMULA_VERSION and body.get("n") == n:
            return body["rows"]
    rows = _replay(asyncio.run(_load(n, False)))
    ROWS.write_text(json.dumps({"version": MA_FAN_FORMULA_VERSION, "n": n, "rows": rows}), encoding="utf-8")
    return rows


def _rank(xs: list[float]) -> list[float]:
    """Average ranks (ties share the mean rank)."""
    order = sorted(range(len(xs)), key=lambda t: xs[t])
    out = [0.0] * len(xs)
    a = 0
    while a < len(order):
        b = a
        while b + 1 < len(order) and xs[order[b + 1]] == xs[order[a]]:
            b += 1
        for t in range(a, b + 1):
            out[order[t]] = (a + b) / 2.0
        a = b + 1
    return out


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


def daily_ic(rows: list[dict[str, Any]], value, key: str) -> tuple[float | None, float | None, int]:
    """Mean daily Spearman IC of ``value(row)`` vs ``row[key]``, its t over days, and day count."""
    by_day: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        if key in r:
            by_day[r["day"]].append((float(value(r)), float(r[key])))
    ics = []
    for pts in by_day.values():
        if len(pts) < MIN_DAY_HITS:
            continue
        ic = _corr(_rank([p[0] for p in pts]), _rank([p[1] for p in pts]))
        if ic is not None:
            ics.append(ic)
    if len(ics) < 3:
        return None, None, len(ics)
    mu = sum(ics) / len(ics)
    sd = (sum((x - mu) ** 2 for x in ics) / (len(ics) - 1)) ** 0.5
    return mu, (mu / (sd / len(ics) ** 0.5) if sd else None), len(ics)


def _fmt_ic(ic: tuple[float | None, float | None, int]) -> str:
    mu, t, _n = ic
    if mu is None:
        return f"{'-':>14}"
    return f"{mu:+.3f}(t{t:+.1f})" if t is not None else f"{mu:+.3f}"


def ic_table(title: str, rows: list[dict[str, Any]], split: str, getters: list[tuple[str, Any]]) -> None:
    """Print train / test daily IC for each getter at 5 and 10 sessions."""
    train = [r for r in rows if r["day"] < split]
    test = [r for r in rows if r["day"] >= split]
    print(f"\n== {title} ==")
    print(f"{'项':<18}{'训练5日':>16}{'训练10日':>16}{'检验5日':>16}{'检验10日':>16}")
    for name, fn in getters:
        cells = [_fmt_ic(daily_ic(part, fn, k)) for part in (train, test) for k in ("x5", "x10")]
        print(f"{name:<18}" + "".join(f"{c:>16}" for c in cells))


def top_table(title: str, rows: list[dict[str, Any]], split: str, scorers: list[tuple[str, Any]], tops=(10, 20)) -> None:
    """Mean forward excess of each day's top-N by each scorer vs all list hits (test half)."""
    test = [r for r in rows if r["day"] >= split]
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in test:
        by_day[r["day"]].append(r)
    print(f"\n== {title}（检验段 {len(by_day)} 天，每天前 N 名的平均超额）==")
    print(f"{'打分':<18}" + "".join(f"{f'前{n} 5日':>12}{f'前{n} 10日':>12}" for n in tops) + f"{'全部 5日':>12}{'全部 10日':>12}")
    for name, fn in scorers:
        cells = []
        for n in tops:
            for k in ("x5", "x10"):
                vals = []
                for day_rows in by_day.values():
                    ranked = sorted((r for r in day_rows if k in r), key=fn, reverse=True)[:n]
                    vals += [r[k] for r in ranked]
                cells.append(sum(vals) / len(vals) if vals else 0.0)
        for k in ("x5", "x10"):
            vals = [r[k] for r in test if k in r]
            cells.append(sum(vals) / len(vals) if vals else 0.0)
        print(f"{name:<18}" + "".join(f"{c:>+12.2f}" for c in cells))


def bench_state(cache: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per session: CSI 1000 20-day return and distance from its MA60 (known at the close)."""
    bench = cache["bars"].get(BENCH) or []
    closes = [float(b["close"]) for b in bench]
    out: dict[str, dict[str, float]] = {}
    for j, b in enumerate(bench):
        if j < 60:
            continue
        ma60 = sum(closes[j - 59 : j + 1]) / 60
        out[str(b["date"])] = {
            "ret20": (closes[j] / closes[j - 20] - 1) * 100,
            "ma60": (closes[j] / ma60 - 1) * 100,
        }
    return out


def regime_table(rows: list[dict[str, Any]], state: dict[str, dict[str, float]], feats: list[str]) -> None:
    """Daily IC by month and by market state, to see whether sign flips are predictable."""
    months = sorted({r["day"][:7] for r in rows})
    print("\n== 按月 10 日 IC（每月日均）==")
    print(f"{'特征':<14}" + "".join(f"{m[2:]:>7}" for m in months))
    for f in feats:
        cells = []
        for m in months:
            mu, _t, _n = daily_ic([r for r in rows if r["day"][:7] == m], lambda r, f=f: r["feat"][f], "x10")
            cells.append(f"{mu:+.2f}" if mu is not None else "-")
        print(f"{f:<14}" + "".join(f"{c:>7}" for c in cells))
    groups = [
        ("中证1000 20日涨", lambda d: state.get(d, {}).get("ret20", 0) > 0),
        ("中证1000 20日跌", lambda d: state.get(d, {}).get("ret20", 0) <= 0),
        ("在MA60上方", lambda d: state.get(d, {}).get("ma60", 0) > 0),
        ("在MA60下方", lambda d: state.get(d, {}).get("ma60", 0) <= 0),
    ]
    print("\n== 按大盘状态分组的日度 IC（5日 / 10日）==")
    print(f"{'特征':<14}" + "".join(f"{g[0]:>26}" for g in groups))
    for f in feats:
        cells = []
        for _name, pred in groups:
            sub = [r for r in rows if pred(r["day"])]
            a = _fmt_ic(daily_ic(sub, lambda r, f=f: r["feat"][f], "x5")).strip()
            b = _fmt_ic(daily_ic(sub, lambda r, f=f: r["feat"][f], "x10")).strip()
            cells.append(f"{a} {b}")
        print(f"{f:<14}" + "".join(f"{c:>26}" for c in cells))
    for name, pred in groups:
        print(f"  {name}: {len({r['day'] for r in rows if pred(r['day'])})} 天")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--split", default="2026-05-01")
    ap.add_argument("--replay", action="store_true", help="ignore the cached replay rows")
    ap.add_argument("--regime", action="store_true", help="only print the month / market-state IC tables")
    args = ap.parse_args()
    rows = load_rows(args.n, args.replay)
    if args.regime:
        state = bench_state(asyncio.run(_load(args.n, False)))
        regime_table(rows, state, ["c_ma20", "ret5", "slopes", "udr", "progressive", "macd_dead", "lag",
                                   "days_since_sticky", "sticky_amp", "ext", "vol_ratio", "ma60_slope"])
        return
    days = sorted({r["day"] for r in rows})
    n_train = sum(1 for d in days if d < args.split)
    print(f"区间 {days[0]} ~ {days[-1]}，训练 {n_train} 天 / 检验 {len(days) - n_train} 天（分界 {args.split}）")
    ic_table("现有总分", rows, args.split, [("v8 总分", lambda r: r["score"])])
    parts = sorted(rows[0]["parts"])
    ic_table("各分项（分数）", rows, args.split, [(p, (lambda r, p=p: r["parts"][p])) for p in parts])
    feats = sorted(rows[0]["feat"])
    ic_table("原始特征", rows, args.split, [(f, (lambda r, f=f: r["feat"][f])) for f in feats])


if __name__ == "__main__":
    main()
