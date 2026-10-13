"""Classic A-share cross-sectional factors on cached daily bars; hypotheses are fixed in TODO.md.

Factors on day D (only bars up to D):

    R5 / R20  - 5- / 20-session return (short-term reversal; expected IC < 0)
    MOM       - return from D-250 to D-20 (momentum; expected weak, not reliably > 0)
    MAX20     - largest daily % in the last 20 sessions (lottery; expected < 0)
    IVOL20    - residual std of daily % vs CSI 1000 over 20 sessions (expected < 0)
    ABVOL     - 5-day mean volume / 60-day mean volume (abnormal turnover; expected < 0)
    ILLIQ     - Amihud: mean |daily %| / amount over 20 sessions (expected > 0)
    AMT20     - mean amount over 20 sessions, a size / liquidity proxy (expected < 0)

Outcome: buy at the next open (skipped when that open is within 0.5% of the
limit-up), hold to the close 5 / 10 sessions after D. Per day: Spearman IC and
the top-minus-bottom quintile spread; the daily series get ``stats_tools.safe_t``
with a Newey–West lag equal to the horizon.

Samples: X = today's top-800 by amount, Y = ranks 800–1600 (both survivor-biased);
periods P1 2023-09 → 2025-02 and P2 2025-03 → 2026-09.

Bars come from the ``scripts/oos_pot_bottom.py`` cache (run it first).

Usage: ``python scripts/research_factors.py``
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import oos_pot_bottom as pot  # noqa: E402
import validate_ma_fan as vm  # noqa: E402
from market_desk.stats_tools import safe_t, spearman  # noqa: E402
from market_desk.tencent import tencent_symbol  # noqa: E402

FACTORS = ("R5", "R20", "MOM", "MAX20", "IVOL20", "ABVOL", "ILLIQ", "AMT20")
EXPECT = {"R5": -1, "R20": -1, "MOM": 0, "MAX20": -1, "IVOL20": -1, "ABVOL": -1, "ILLIQ": 1, "AMT20": -1}
HORIZONS = (5, 10)
PERIODS = (("P1", "2023-09-01", "2025-02-28"), ("P2", "2025-03-01", "2026-09-30"))
MIN_NAMES = 50
LOOKBACK = 60


def _limit_pct(code: str) -> float:
    """Daily price limit in % for a plain A-share code."""
    return 20.0 if code.startswith(("30", "68")) else 10.0


def _rows_for(code: str, bars: list[dict[str, Any]], bench_pct: dict[str, float]) -> list[tuple]:
    """Return ``(day, factors..., fwd5, fwd10)`` rows for one name."""
    n = len(bars)
    c = [float(b["close"]) for b in bars]
    v = [float(b.get("volume") or 0) for b in bars]
    pct = [0.0] + [(c[t] / c[t - 1] - 1) * 100 if c[t - 1] > 0 else 0.0 for t in range(1, n)]
    amt = [c[t] * v[t] for t in range(n)]
    lim = _limit_pct(code) - 0.5
    out = []
    for i in range(LOOKBACK, n - 1):
        if min(v[i - 19 : i + 1]) <= 0 or c[i] <= 0:
            continue
        nxt_open = float(bars[i + 1].get("open") or bars[i + 1]["close"])
        if (nxt_open / c[i] - 1) * 100 >= lim:
            continue
        fwd = [vm._fwd(bars, i, h) for h in HORIZONS]
        if all(f is None for f in fwd):
            continue
        win = range(i - 19, i + 1)
        xs = [bench_pct.get(bars[t]["date"]) for t in win]
        ys = [pct[t] for t in win]
        ivol = None
        if all(x is not None for x in xs):
            mx, my = sum(xs) / 20, sum(ys) / 20
            vx = sum((x - mx) ** 2 for x in xs)
            beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx if vx else 0.0
            res = [y - my - beta * (x - mx) for x, y in zip(xs, ys)]
            ivol = (sum(r * r for r in res) / 18) ** 0.5
        a20 = sum(amt[t] for t in win) / 20
        out.append((
            bars[i]["date"],
            (c[i] / c[i - 5] - 1) * 100,
            (c[i] / c[i - 20] - 1) * 100,
            (c[i - 20] / c[i - 250] - 1) * 100 if i >= 250 and c[i - 250] > 0 else None,
            max(ys),
            ivol,
            (sum(v[i - 4 : i + 1]) / 5) / (sum(v[i - 59 : i + 1]) / 60),
            sum(abs(pct[t]) / amt[t] for t in win) / 20 * 1e9,
            a20,
            *fwd,
        ))
    return out


def _by_day(cache: dict[str, Any], codes: list[str], bench_pct: dict[str, float]) -> dict[str, list[tuple]]:
    """Group factor rows of ``codes`` by trading day."""
    days: dict[str, list[tuple]] = defaultdict(list)
    for code in codes:
        bars = cache["bars"].get(tencent_symbol(code)) or []
        for row in _rows_for(code, bars, bench_pct):
            days[row[0]].append(row[1:])
    return days


def _daily_stats(rows: list[tuple], fi: int, hi: int) -> tuple[float, float] | None:
    """Spearman IC and top-minus-bottom quintile spread (%) for one day."""
    pairs = [(r[fi], r[hi]) for r in rows if r[fi] is not None and r[hi] is not None]
    if len(pairs) < MIN_NAMES:
        return None
    ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
    if ic is None:
        return None
    pairs.sort(key=lambda p: p[0])
    q = len(pairs) // 5
    lo = sum(p[1] for p in pairs[:q]) / q
    hi_ = sum(p[1] for p in pairs[-q:]) / q
    return ic, hi_ - lo


def _cell(days: dict[str, list[tuple]], lo: str, hi: str, fi: int, hix: int, lag: int) -> dict[str, Any]:
    """IC / spread summary of one factor × horizon over ``lo..hi``."""
    ics, sps = [], []
    for d in sorted(days):
        if lo <= d <= hi:
            got = _daily_stats(days[d], fi, hix)
            if got:
                ics.append(got[0])
                sps.append(got[1])
    if len(ics) < 20:
        return {"n": len(ics)}
    return {
        "n": len(ics),
        "ic": sum(ics) / len(ics),
        "t": safe_t(ics, lag=lag),
        "sp": sum(sps) / len(sps),
        "spt": safe_t(sps, lag=lag),
    }


def _fmt(c: dict[str, Any]) -> str:
    if "ic" not in c:
        return f"{'-':>27}"
    t = f"{c['t']:+.1f}" if c["t"] is not None else "-"
    return f"{c['ic']:>+6.3f} t{t:>5} {c['sp']:>+6.2f}%{c['n']:>4}天"


def _verdict(cells: list[dict[str, Any]], sign: int) -> str:
    """Apply the TODO rule: all four cells on the expected side, ≥ 3 with |t| ≥ 2."""
    if sign == 0:
        return "（不预设方向）"
    if any("ic" not in c for c in cells):
        return "样本不足"
    same = all(c["ic"] * sign > 0 for c in cells)
    strong = sum(1 for c in cells if c["t"] is not None and c["t"] * sign >= 2)
    return f"{'站得住' if same and strong >= 3 else '不成立'}（同向 {sum(1 for c in cells if c['ic'] * sign > 0)}/4，|t|≥2 {strong}/4）"


def main() -> None:
    """Replay every factor on both samples and both periods, then apply the registered rule."""
    if not pot.CACHE.exists():
        raise SystemExit(f"缺缓存 {pot.CACHE}：先跑 scripts/oos_pot_bottom.py")
    cache = json.loads(pot.CACHE.read_text(encoding="utf-8"))
    bench = cache["bars"].get(vm.BENCH) or []
    bench_pct = {
        bench[t]["date"]: (bench[t]["close"] / bench[t - 1]["close"] - 1) * 100 for t in range(1, len(bench))
    }
    t0 = time.time()
    samples = {"X 前800": _by_day(cache, cache["a"], bench_pct), "Y 800–1600": _by_day(cache, cache["b"], bench_pct)}
    print(f"因子计算 {time.time() - t0:.0f}s；" + "；".join(f"{k} {sum(len(v) for v in d.values())} 行" for k, d in samples.items()))
    print("格式：IC均值  t  最高组−最低组  天数（Q5−Q1 > 0 表示因子值高的那组更好）")
    for h_i, h in enumerate(HORIZONS):
        print(f"\n===== {h} 日（次日开盘买） =====")
        head = "".join(f"{f'{s[:1]}·{p}':>28}" for s in samples for p, _a, _b in PERIODS)
        print(f"{'因子':<8}{head}   判定（只看 10 日）")
        for f_i, f in enumerate(FACTORS):
            cells = [
                _cell(days, lo, hi, f_i, len(FACTORS) + h_i, lag=h)
                for days in samples.values()
                for _p, lo, hi in PERIODS
            ]
            tail = _verdict(cells, EXPECT[f]) if h == 10 else ""
            print(f"{f:<8}" + "".join(f"{_fmt(c):>28}" for c in cells) + "   " + tail, flush=True)
    _print_corr(samples["X 前800"])
    print(f"\n总用时 {time.time() - t0:.0f}s")


def _print_corr(days: dict[str, list[tuple]], step: int = 5) -> None:
    """Mean daily Spearman between factor pairs (every ``step``-th day) to show overlap."""
    acc: dict[tuple[int, int], list[float]] = defaultdict(list)
    for d in sorted(days)[::step]:
        rows = days[d]
        for a in range(len(FACTORS)):
            for b in range(a + 1, len(FACTORS)):
                pairs = [(r[a], r[b]) for r in rows if r[a] is not None and r[b] is not None]
                if len(pairs) >= MIN_NAMES:
                    rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
                    if rho is not None:
                        acc[(a, b)].append(rho)
    print("\n因子间日度秩相关均值（X 前800，每 5 天取一天）")
    print(f"{'':<8}" + "".join(f"{f:>8}" for f in FACTORS[1:]))
    for a in range(len(FACTORS) - 1):
        cells = []
        for b in range(1, len(FACTORS)):
            v = acc.get((a, b))
            cells.append(f"{sum(v) / len(v):>+8.2f}" if b > a and v else f"{'':>8}")
        print(f"{FACTORS[a]:<8}" + "".join(cells))


if __name__ == "__main__":
    main()
