"""Audit: lit-but-unbought vs never-touched plan — next-day outcomes.

Answers whether 「挂计划价、不追」 is too tight by comparing two untraded buckets:

* **lit_miss** — card lit ready at least once (``ever_ready`` / ``first_ready_at`` /
  ``cf.ready_n``) but was not marked traded.
* **untouch** — never touched plan (no ``cf.touch_n``, and day low after the card
  did not reach plan when bars are available) and never lit / traded.

Metrics (percent):

* ``plan_d1`` / ``plan_d3`` — paper buy at plan (stored outcome, classic).
* ``ready_d1`` — buy at first_ready_px (or first_last), hold to next close; needs bars.
* ``chase_d1`` — buy at day-D close, hold to next close (what chasing the close does).
* ``next_fill`` — next session's low ≤ plan (overnight hang at plan would fill).

All means are day-clustered with ``safe_t`` vs CSI 1000 when bars exist.

Usage::

    python scripts/research_miss_next.py [--db PATH] [--until YYYY-MM-DD] [--refresh]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from market_desk.calendar import add_trading_days, is_trading_day  # noqa: E402
from market_desk.numbers import num  # noqa: E402
from market_desk.stats_tools import day_means, safe_t  # noqa: E402
from research_db import research_db_path  # noqa: E402

BUY_TYPES = ("buy", "buy_side", "buy_link", "buy_dragon", "buy_indep")
TYPE_RANK = {t: i for i, t in enumerate(BUY_TYPES)}
CACHE = Path(tempfile.gettempdir()) / "desk_miss_next_bars.json"
BENCH = "sh000852"


def next_trading_day(day: str) -> str | None:
    """Return the next A-share session after ``day`` as YYYY-MM-DD."""
    nxt = add_trading_days(day, 1)
    return nxt.isoformat() if nxt else None


def _is_lit(row: dict[str, Any]) -> bool:
    """Return True when the card lit ready at least once that day."""
    p = row["payload"]
    cf = p.get("cf") if isinstance(p.get("cf"), dict) else {}
    if p.get("ever_ready") or p.get("first_ready_at"):
        return True
    if int(cf.get("ready_n") or 0) > 0:
        return True
    return bool(int(row.get("ready") or 0))


def _cf_touched(row: dict[str, Any]) -> bool | None:
    """Return True/False from live cf touch count; None if cf never recorded."""
    p = row["payload"]
    cf = p.get("cf") if isinstance(p.get("cf"), dict) else {}
    if not cf:
        return None
    return int(cf.get("touch_n") or 0) > 0


def _plan_of(row: dict[str, Any]) -> float | None:
    """Return the locked plan / signal price."""
    p = row["payload"]
    return num(p.get("plan_price")) or num(row.get("price"))


def _ready_px(row: dict[str, Any]) -> float | None:
    """Return the best available ready / first-seen price for a lit miss."""
    p = row["payload"]
    return (
        num(p.get("first_ready_px"))
        or num(p.get("first_last"))
        or num(row.get("last"))
        or _plan_of(row)
    )


def _load_cards(db: Path, *, until: str | None) -> list[dict[str, Any]]:
    """Load buy cards through ``until`` (exclusive of incomplete next-day)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = (
        "SELECT id, trade_date, signaled_at, signal_type, code, name, price, last, "
        "ready, traded, skipped, outcome_day1_pct, outcome_day3_pct, outcome_label, payload "
        "FROM signals WHERE signal_type IN (%s)" % ",".join("?" * len(BUY_TYPES))
    )
    out: list[dict[str, Any]] = []
    for r in con.execute(q, BUY_TYPES):
        day = str(r[1] or "")[:10]
        if not day or not is_trading_day(day):
            continue
        if until and day > until:
            continue
        try:
            payload = json.loads(r[14] or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append(
            {
                "id": r[0],
                "day": day,
                "at": str(r[2] or ""),
                "type": r[3],
                "code": str(r[4] or "").zfill(6),
                "name": r[5],
                "price": num(r[6]),
                "last": num(r[7]),
                "ready": int(r[8] or 0),
                "traded": int(r[9] or 0),
                "skipped": int(r[10] or 0),
                "outcome_day1_pct": num(r[11]),
                "outcome_day3_pct": num(r[12]),
                "outcome_label": r[13],
                "payload": payload,
            }
        )
    con.close()
    return out


def _dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one card per day+code: lit first, then lower type rank, then earliest."""
    best: dict[str, dict[str, Any]] = {}
    for c in cards:
        key = f"{c['day']}|{c['code']}"
        cur = best.get(key)
        if cur is None:
            best[key] = c
            continue
        lit_new, lit_old = _is_lit(c), _is_lit(cur)
        if lit_new and not lit_old:
            best[key] = c
            continue
        if lit_old and not lit_new:
            continue
        if TYPE_RANK.get(c["type"], 99) < TYPE_RANK.get(cur["type"], 99):
            best[key] = c
            continue
        if c["at"] and cur["at"] and c["at"] < cur["at"]:
            best[key] = c
    return list(best.values())


def _bucket(row: dict[str, Any], *, close_above_plan: bool | None) -> str | None:
    """Assign lit_miss / untouch / touch_miss; traded cards are separate.

    ``untouch`` prefers live ``cf.touch_n==0``. When cf is missing, fall back to
    ``first_last`` and day close both still above plan (whole-day low is not used
    — it can predate the card and falsely count as a touch).
    """
    if int(row.get("traded") or 0):
        return "traded"
    lit = _is_lit(row)
    if lit:
        return "lit_miss"
    touched = _cf_touched(row)
    if touched is True:
        return "touch_miss"
    if touched is False:
        return "untouch"
    if close_above_plan is True:
        return "untouch"
    if close_above_plan is False:
        return "touch_miss"
    return "unknown"


Bars = dict[str, list[float | None]]  # date -> [open, close, low]


async def _sina_ohlc(client: httpx.AsyncClient, sym: str) -> Bars:
    """Fetch unadjusted daily open/close/low from Sina."""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=120"
    )
    try:
        resp = await client.get(
            url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}
        )
        rows = resp.json()
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    out: Bars = {}
    for r in rows:
        try:
            out[str(r["day"])[:10]] = [float(r["open"]), float(r["close"]), float(r["low"])]
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def _load_bars(syms: set[str], *, refresh: bool) -> dict[str, Bars]:
    """Load / extend OHLC cache for stock symbols and the benchmark."""
    from market_desk.tencent import tencent_symbol

    cache: dict[str, Bars] = {}
    if CACHE.exists() and not refresh:
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    keys = {tencent_symbol(s) if not s.startswith("sh") else s for s in syms}
    keys.add(BENCH)
    todo = sorted(k for k in keys if not cache.get(k))
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:

        async def _one(sym: str) -> None:
            async with sem:
                got = await _sina_ohlc(client, sym)
                if got:
                    cache[sym] = got
                await asyncio.sleep(0.25)

        if todo:
            print(f"拉取日线 {len(todo)} 只…")
            await asyncio.gather(*(_one(s) for s in todo))
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def _bar(cache: dict[str, Bars], code: str, day: str) -> list[float | None] | None:
    from market_desk.tencent import tencent_symbol

    key = BENCH if code == BENCH else tencent_symbol(code)
    return (cache.get(key) or {}).get(day)


def _ret(entry: float, exit_px: float | None) -> float | None:
    if entry is None or exit_px is None or entry <= 0 or exit_px <= 0:
        return None
    return (exit_px / entry - 1.0) * 100.0


def _summarize(label: str, pairs: list[tuple[str, float]]) -> None:
    """Print day-clustered mean / t for one series."""
    if not pairs:
        print(f"  {label}: 无样本")
        return
    means = day_means(pairs, day_of=lambda r: r[0], value_of=lambda r: r[1])
    vals = [v for _, v in means]
    pos = sum(1 for v in vals if v > 0)
    t = safe_t(vals, lag=1)
    flat = [v for _, v in pairs]
    win = sum(1 for v in flat if v > 0)
    t_s = f"{t:+.1f}" if t is not None else "—"
    print(
        f"  {label}: n={len(flat)} / {len(vals)}天  "
        f"均值 {sum(flat)/len(flat):+.2f}%  天均值 {sum(vals)/len(vals):+.2f}%  "
        f"正/天 {pos}/{len(vals)}  卡胜率 {100*win/len(flat):.0f}%  t={t_s}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--until", default=None, help="last signal day to include (YYYY-MM-DD)")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--db", default=None)
    args, _ = ap.parse_known_args()
    db = research_db_path(ROOT / "data" / "desk.db")

    # Need next session settled: default until = last day with a following trade day
    # that is already past, approximated by excluding today if outcome missing.
    cards = _dedupe(_load_cards(db, until=args.until))
    print(f"库 {db} · 去重买卡 {len(cards)}")

    need_bars = {c["code"] for c in cards}
    need_bars.add(BENCH)
    cache = asyncio.run(_load_bars(need_bars, refresh=args.refresh))

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cards:
        plan = _plan_of(c)
        close_above = None
        if plan and plan > 0:
            p = c["payload"]
            first = num(p.get("first_last")) or num(c.get("last"))
            bar0 = _bar(cache, c["code"], c["day"])
            close = float(bar0[1]) if bar0 and bar0[1] else None
            if first is not None and close is not None:
                close_above = first > plan * 1.001 and close > plan * 1.001
        b = _bucket(c, close_above_plan=close_above)
        c["bucket"] = b
        c["plan"] = plan
        buckets[b or "unknown"].append(c)

    for k in ("lit_miss", "untouch", "touch_miss", "traded", "unknown"):
        print(f"  {k}: {len(buckets.get(k, []))}")

    def _series(
        rows: list[dict[str, Any]], *, mode: str
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        """Return (raw, excess vs bench) pairs for one entry mode."""
        raw: list[tuple[str, float]] = []
        xs: list[tuple[str, float]] = []
        for c in rows:
            day = c["day"]
            nxt = next_trading_day(day)
            if not nxt:
                continue
            b0 = _bar(cache, c["code"], day)
            b1 = _bar(cache, c["code"], nxt)
            bb0 = _bar(cache, BENCH, day)
            bb1 = _bar(cache, BENCH, nxt)
            if not b1 or b1[1] is None:
                continue
            if mode == "plan_stored":
                r = c.get("outcome_day1_pct")
                if r is None:
                    plan = c.get("plan")
                    if plan and b1[1]:
                        r = _ret(plan, b1[1])
                if r is None:
                    continue
                raw.append((day, float(r)))
                if bb0 and bb1 and bb0[1] and bb1[1]:
                    mkt = _ret(float(bb0[1]), float(bb1[1]))
                    if mkt is not None:
                        xs.append((day, float(r) - mkt))
                continue
            if mode == "plan_d3_stored":
                r = c.get("outcome_day3_pct")
                if r is None:
                    continue
                raw.append((day, float(r)))
                continue
            if mode == "ready_d1":
                entry = _ready_px(c)
            elif mode == "chase_d1":
                entry = float(b0[1]) if b0 and b0[1] else None
            elif mode == "plan_next_open":
                entry = c.get("plan")
                if entry and b1[0]:
                    r = _ret(float(entry), float(b1[0]))
                    if r is not None:
                        raw.append((day, r))
                        if bb0 and bb1 and bb0[1] and bb1[0]:
                            mkt = _ret(float(bb0[1]), float(bb1[0]))
                            if mkt is not None:
                                xs.append((day, r - mkt))
                continue
            else:
                entry = None
            if entry is None or not b1[1]:
                continue
            r = _ret(float(entry), float(b1[1]))
            if r is None:
                continue
            raw.append((day, r))
            if bb0 and bb1 and bb0[1] and bb1[1]:
                mkt = _ret(float(bb0[1]), float(bb1[1]))
                if mkt is not None:
                    xs.append((day, r - mkt))
        return raw, xs

    def _next_fill_rate(rows: list[dict[str, Any]]) -> None:
        ok = tot = 0
        for c in rows:
            plan = c.get("plan")
            nxt = next_trading_day(c["day"])
            if not plan or not nxt:
                continue
            b1 = _bar(cache, c["code"], nxt)
            if not b1 or b1[2] is None:
                continue
            tot += 1
            if float(b1[2]) <= float(plan) * 1.001:
                ok += 1
        if tot:
            print(f"  次日曾触及计划价（隔夜挂单可成）：{ok}/{tot} = {100*ok/tot:.0f}%")
        else:
            print("  次日曾触及计划价：无样本")

    print("\n=== 亮灯未买 lit_miss（到了可买、没记成交）===")
    lit = buckets.get("lit_miss") or []
    _summarize("计划价纸面·次日(存库)", _series(lit, mode="plan_stored")[0])
    _summarize("计划价纸面·三日(存库)", _series(lit, mode="plan_d3_stored")[0])
    raw, xs = _series(lit, mode="ready_d1")
    _summarize("亮灯价买·次日收", raw)
    _summarize("亮灯价买·次日超额(中证1000)", xs)
    raw, xs = _series(lit, mode="chase_d1")
    _summarize("当日收盘追买·次日收", raw)
    _summarize("当日收盘追买·次日超额", xs)
    _next_fill_rate(lit)

    print("\n=== 未到计划价 untouch（没回踩到、也没亮灯）===")
    unt = buckets.get("untouch") or []
    _summarize("若挂计划价·次日收(纸面)", _series(unt, mode="plan_stored")[0])
    _summarize("若挂计划价·三日(纸面)", _series(unt, mode="plan_d3_stored")[0])
    raw, xs = _series(unt, mode="chase_d1")
    _summarize("若不挂、收盘追买·次日收", raw)
    _summarize("若不挂、收盘追买·次日超额", xs)
    raw, xs = _series(unt, mode="plan_next_open")
    _summarize("挂计划价·次日开盘相对计划", raw)
    _next_fill_rate(unt)

    print("\n=== 对照：已交易 traded ===")
    tr = buckets.get("traded") or []
    _summarize("计划价纸面·次日", _series(tr, mode="plan_stored")[0])
    _summarize("计划价纸面·三日", _series(tr, mode="plan_d3_stored")[0])

    # Recent days detail for lit_miss
    print("\n=== 近 8 个交易日·亮灯未买明细（计划纸面次日）===")
    by_day = defaultdict(list)
    for c in lit:
        by_day[c["day"]].append(c)
    for day in sorted(by_day)[-8:]:
        rows = by_day[day]
        d1s = [c["outcome_day1_pct"] for c in rows if c.get("outcome_day1_pct") is not None]
        avg = sum(d1s) / len(d1s) if d1s else None
        names = "、".join(f"{c['name'] or c['code']}" for c in rows[:6])
        more = f" 等{len(rows)}只" if len(rows) > 6 else f" {len(rows)}只"
        print(f"  {day}  纸面次日均 {avg:+.2f}%" if avg is not None else f"  {day}  纸面次日 —", end="")
        print(f"  {names}{more}")

    print(
        "\n判读提示：若 lit_miss 亮灯价次日超额明显为负，而 untouch 收盘追买更差、"
        "挂计划纸面也不好，则不宜因「高于计划 +5%」放宽不追；"
        "若 untouch 次日大量触及计划且挂单纸面为正、追买为负，则维持挂单纪律。"
    )


if __name__ == "__main__":
    main()
