"""Offline 5-minute replay of buy cards: day effect and entry methods.

Part A - does the intraday float of earlier cards predict the rest of the day?
    At a cutoff T (10:30 / 11:00 / 13:30) on day D, the "lit book" is every card
    first lit before T, marked from its lit price to the price at T (the review
    page's 已亮卡 float, replayed without look-ahead). The target is the mean
    3-day excess over the benchmark of cards that *appear* at or after T, bought
    at their signal price. The benchmark's own move (prev close -> T) is shown
    alongside, so a book float that only restates the index adds nothing.

Part B - which entry would have done best? Per card, held to close(D+3):
    signal  - close of the 5-minute bar containing the first-seen time
    touch   - plan price, only if a later bar's low reaches it (no look-ahead)
    reclaim - after the touch, the first later bar closing >= plan +0.5% on D;
              never reclaimed = skipped (zero return), same rule as edge_shadow
    lit     - first lit price (``first_ready_px``), lit cards only
    close   - close(D)
    next    - close of the first 5-minute bar of D+1 (about 09:35)
    Excess = return minus benchmark close(D) -> close(D+3). Paired differences on
    the same cards cancel the market leg.

Statistics are per trading day (cards of one day share the market) with the
conservative ``safe_t``. This is discovery data (2026-09): anything found here
is a hypothesis to register in TODO.md, not a rule change.

Usage: ``python scripts/replay_intraday.py [--bench sh000852] [--refresh] [--db PATH]``
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import audit_attribution as aa  # noqa: E402
from market_desk.stats_tools import day_means, mean, safe_t, spearman  # noqa: E402

CUTOFFS = ("10:30", "11:00", "13:30")
RECLAIM_PCT = 0.5


def _day_bars(m5: aa.Bars, day: str) -> list[tuple[str, list[float | None]]]:
    """Return the 5-minute bars of ``day`` in time order."""
    return sorted((k, v) for k, v in m5.items() if k[:10] == day)


def _px_at(m5: aa.Bars, day: str, hhmm: str) -> float | None:
    """Close of the last 5-minute bar ending at or before ``hhmm`` on ``day``."""
    stamp = f"{day} {hhmm}:00"
    last = None
    for k, v in _day_bars(m5, day):
        if k > stamp:
            break
        last = v[0]
    return last


def _touch_reclaim(m5: aa.Bars, at: str, plan: float) -> tuple[float | None, float | None]:
    """Return (touch fill, reclaim fill) on the signal day, without look-ahead.

    Touch fills at the signal bar close when already at / below plan, else at plan
    on the first later bar whose low reaches it. Reclaim fills at the close of the
    first bar after the touch that closes at or above plan × (1 + RECLAIM_PCT%).
    """
    bars = _day_bars(m5, at[:10])
    idx = next((i for i, (k, _) in enumerate(bars) if k >= at), None)
    if idx is None:
        return None, None
    sig = bars[idx][1][0]
    if sig is not None and sig <= plan:
        touch_i, fill = idx, sig
    else:
        touch_i = next((i for i in range(idx + 1, len(bars)) if (bars[i][1][1] or 1e18) <= plan), None)
        if touch_i is None:
            return None, None
        fill = plan
    bar = plan * (1.0 + RECLAIM_PCT / 100.0)
    rec = next((bars[i][1][0] for i in range(touch_i + 1, len(bars)) if (bars[i][1][0] or 0.0) >= bar), None)
    return fill, rec


def _prev_close(bars: aa.Bars, day: str) -> float | None:
    """Daily close of the session before ``day``."""
    before = [d for d in bars if d < day]
    return bars[max(before)][0] if before else None


def _attach_entries(c: dict[str, Any], bars: dict[str, aa.Bars], m5: dict[str, aa.Bars]) -> None:
    """Add Part B entry returns (``e_*`` keys, percent to close(D+3)) to card ``c``."""
    sym = aa._key(c["code"])
    st, sm5 = bars.get(sym) or {}, m5.get(sym) or {}
    d3 = aa._nth_after(st, c["day"], 3)
    d1 = aa._nth_after(st, c["day"], 1)
    if not d3 or not sm5 or not c["at"]:
        return
    c3 = st[d3][0]
    if c.get("sig_m5"):
        c["e_signal"] = aa._pct(c3, c["sig_m5"])
    c["e_close"] = c["r_close"]
    touch, rec = _touch_reclaim(sm5, c["at"], c["plan"])
    if touch is not None:
        c["e_touch"] = aa._pct(c3, touch)
        c["e_reclaim"] = aa._pct(c3, rec) if rec is not None else None
    if c["lit"] and c.get("lit_px"):
        c["e_lit"] = aa._pct(c3, c["lit_px"])
    nb = _day_bars(sm5, d1) if d1 else []
    if nb and nb[0][1][0]:
        c["e_next"] = aa._pct(c3, nb[0][1][0])


def _line(label: str, rows: list[dict[str, Any]], value: Callable[[dict[str, Any]], float | None]) -> str:
    """Format n, card mean, positive days / days and day-clustered t for one series."""
    vals = [(r, value(r)) for r in rows]
    vals = [(r, v) for r, v in vals if v is not None]
    if not vals:
        return f"{label:<22}{'-':>6}"
    daily = [m for _, m in day_means(vals, lambda x: x[0]["day"], lambda x: x[1])]
    tv = safe_t(daily)
    pos = sum(1 for m in daily if m > 0)
    t = f"t{tv:+.1f}" if tv is not None else "-"
    avg = sum(v for _, v in vals) / len(vals)
    return f"{label:<22}{len(vals):>6}{avg:>+9.2f}{mean(daily):>+9.2f}{pos:>5}/{len(daily):<4}{t:>7}"


def _part_b(cards: list[dict[str, Any]]) -> None:
    """Print entry-method excess and paired differences."""
    print("\n== B. 进场方式：相对大盘的三日超额（同日同代码去重，持有到 D+3 收盘） ==")
    print(f"{'进场':<22}{'卡':>6}{'单卡均':>9}{'按天均':>9}{'正/天':>10}{'t':>7}")
    ex = lambda k: (lambda r: r[k] - r["market"] if r.get(k) is not None else None)  # noqa: E731
    for label, key in (
        ("信号价", "e_signal"),
        ("触价即买（触价卡）", "e_touch"),
        (f"站回 +{RECLAIM_PCT}%（站回的）", "e_reclaim"),
        ("亮灯价（亮灯卡）", "e_lit"),
        ("收盘 D", "e_close"),
        ("次日 09:35", "e_next"),
    ):
        print(_line(label, cards, ex(key)))

    print("\n-- 同一批卡的配对差（大盘腿抵消；正 = 前者更好） --")
    print(f"{'对比':<22}{'卡':>6}{'单卡均':>9}{'按天均':>9}{'正/天':>10}{'t':>7}")

    def diff(a: str, b: str, *, skip_zero: bool = False) -> Callable[[dict[str, Any]], float | None]:
        def f(r: dict[str, Any]) -> float | None:
            if r.get(b) is None:
                return None
            va = r.get(a)
            if va is None:
                return (0.0 - r[b]) if skip_zero else None
            return va - r[b]

        return f

    print(_line("收盘 D − 信号价", cards, diff("e_close", "e_signal")))
    print(_line("次日 09:35 − 信号价", cards, diff("e_next", "e_signal")))
    # Same-card touch-vs-signal is positive by construction (plan < signal), so
    # compare strategies over all cards: untouched cards stay in cash.
    print(_line("等触价 − 信号价（没触价记 0）", cards, diff("e_touch", "e_signal", skip_zero=True)))
    print(_line("站回 − 触价（没站回记 0）", cards, diff("e_reclaim", "e_touch", skip_zero=True)))
    print(_line("亮灯价 − 信号价", cards, diff("e_lit", "e_signal")))
    touched = [c for c in cards if "e_touch" in c]
    rec_n = sum(1 for c in touched if c.get("e_reclaim") is not None)
    if touched:
        print(f"触价卡 {len(touched)} 张，当日站回 +{RECLAIM_PCT}% 的 {rec_n} 张（{rec_n * 100 // len(touched)}%）")


def _part_a(cards: list[dict[str, Any]], bars: dict[str, aa.Bars], m5: dict[str, aa.Bars], bench: str) -> None:
    """Print whether the early lit-book / seen-book float predicts later cards' excess."""
    print("\n== A. 早盘已亮卡浮动 → 之后出现的卡三日超额（按天，一天一个点） ==")
    bm5, bday = m5.get(bench) or {}, bars.get(bench) or {}
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cards:
        by_day[c["day"]].append(c)
    for cut in CUTOFFS:
        pts: list[dict[str, float]] = []
        for day, rows in sorted(by_day.items()):
            later = [c["r_signal"] - c["market"] for c in rows if c["at"][11:16] >= cut and "r_signal" in c]
            if len(later) < 2:
                continue
            lit_f, seen_f = [], []
            for c in rows:
                px = _px_at(m5.get(aa._key(c["code"])) or {}, day, cut)
                if not px:
                    continue
                if c["lit_at"][:10] == day and c["lit_at"][11:16] < cut and c.get("lit_px"):
                    lit_f.append(aa._pct(px, c["lit_px"]))
                if c["at"][11:16] < cut and c.get("sig_m5"):
                    seen_f.append(aa._pct(px, c["sig_m5"]))
            bpx, bprev = _px_at(bm5, day, cut), _prev_close(bday, day)
            if len(seen_f) < 2 or not bpx or not bprev:
                continue
            pts.append(
                {
                    "later": sum(later) / len(later),
                    "seen": sum(seen_f) / len(seen_f),
                    "lit": sum(lit_f) / len(lit_f) if lit_f else None,
                    "bench": aa._pct(bpx, bprev),
                }
            )
        if len(pts) < 5:
            print(f"{cut}：可用天数 {len(pts)}，不足 5 天")
            continue
        later = [p["later"] for p in pts]
        lit_pts = [p for p in pts if p["lit"] is not None]

        def _split(key: str, rows: list[dict[str, float]]) -> str:
            neg = [p["later"] for p in rows if p[key] < 0]
            pos = [p["later"] for p in rows if p[key] >= 0]
            fm = lambda v: f"{mean(v):+.2f}" if v else "-"  # noqa: E731
            return f"浮亏日 {len(neg)} 天 后续 {fm(neg)} / 浮盈日 {len(pos)} 天 后续 {fm(pos)}"

        rs = spearman([p["seen"] for p in pts], later)
        rb = spearman([p["bench"] for p in pts], later)
        rx = spearman([p["seen"] - p["bench"] for p in pts], later)
        print(f"{cut}（{len(pts)} 天）后续卡按天均 {mean(later):+.2f}")
        print(f"  已出现卡浮动  秩相关 {rs:+.2f}；{_split('seen', pts)}" if rs is not None else "  已出现卡浮动  -")
        print(f"  减去大盘后    秩相关 {rx:+.2f}" if rx is not None else "  减去大盘后    -")
        print(f"  大盘自身      秩相关 {rb:+.2f}；{_split('bench', pts)}" if rb is not None else "  大盘自身      -")
        if len(lit_pts) >= 5:
            rl = spearman([p["lit"] for p in lit_pts], [p["later"] for p in lit_pts])
            print(f"  已亮卡浮动（{len(lit_pts)} 天）秩相关 {rl:+.2f}；{_split('lit', lit_pts)}")
        else:
            print(f"  已亮卡浮动：有亮灯的天数 {len(lit_pts)}，不足 5 天")
    print("  注：约 18 天时秩相关要 |r| > 0.47 才算 5% 显著，且相邻天三日结果重叠，实际门槛更高。")


def main() -> None:
    """Run the replay and print both parts."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bench", default="sh000852", choices=aa.BENCHES)
    ap.add_argument("--refresh", action="store_true", help="Refetch bars instead of using the cache")
    ap.add_argument("--db", help="desk.db to read (default data/desk.db), e.g. a VPS copy in the temp dir")
    args = ap.parse_args()

    cards, name_bk = aa._load_cards()
    keys = {aa._key(c["code"]) for c in cards} | set(aa.BENCHES)
    bars = asyncio.run(aa._load_bars(keys, args.refresh))
    m5 = asyncio.run(aa._load_m5({aa._key(c["code"]) for c in cards} | {args.bench}, args.refresh))
    for c in cards:
        aa._decompose(c, bars, m5, args.bench, name_bk)
    done = aa._dedupe([c for c in cards if "market" in c and c.get("sig_m5")])
    for c in done:
        _attach_entries(c, bars, m5)
    days = sorted({c["day"] for c in done})
    print(f"有 5 分钟线且满三日的买卡（去重）{len(done)} 张，{len(days)} 天（{days[0] if days else '-'} → {days[-1] if days else '-'}）；基准 {args.bench}")
    _part_a(done, bars, m5, args.bench)
    _part_b(done)


if __name__ == "__main__":
    main()
