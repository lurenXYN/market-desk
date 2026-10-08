"""Offline audit: where do buy-card 3-day returns come from?

Every scored buy card is replayed with three obtainable entries, all held to
the 3rd session close after the signal day D:

    signal  - the price when the card first appeared (``payload.first_last``)
    close   - close(D)
    plan    - the plan price, only for cards whose day-D low touched it

The signal-entry return is split into four additive parts (percent):

    signal = market + board_pick + stock_pick + entry
    market     = benchmark index, close(D) -> close(D+3)
    board_pick = card board index, close(D) -> close(D+3), minus market
    stock_pick = buy-at-close return minus board
    entry      = signal-entry return minus buy-at-close return

``signals.price`` is the plan price, so the stored ``outcome_day3_pct`` is a
paper return for cards that never traded down to plan; it is not used here.

Board = ``source_board`` / first ``board_names`` / ``mainline``; mapped to an
East Money BK code via ``board_daily``. Stock / index bars come from Sina
(unadjusted, so plan / first-seen prices compare like for like; Tencent as a
fallback); board index bars from East Money, paced one request at a
time because bursts get the IP cut off. Bars are cached under the temp dir and
the cache is extended on every run. Reads ``data/desk.db`` read-only.

Usage: ``python scripts/audit_attribution.py [--bench sh000852] [--refresh]``
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

from market_desk.calendar import is_trading_day  # noqa: E402

DB = ROOT / "data" / "desk.db"
CACHE = Path(tempfile.gettempdir()) / "desk_attr_bars_v2.json"
M5_CACHE = Path(tempfile.gettempdir()) / "desk_attr_m5.json"
BUY_TYPES = ("buy", "buy_side", "buy_link", "buy_dragon", "buy_indep")
TYPE_LABEL = {
    "buy": "主线",
    "buy_side": "支线",
    "buy_link": "联动",
    "buy_dragon": "龙头",
    "buy_indep": "独立人气",
}
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
BENCHES = ("sh000852", "sh000300", "sh000001")
EM_PACE_SEC = 1.5

Bars = dict[str, list[float | None]]  # date -> [close, low]


def _key(code: str) -> str:
    """Return the bar key: ``BKxxxx`` for boards, the Tencent symbol for stocks / ETFs."""
    from market_desk.tencent import tencent_symbol

    if code.upper().startswith("BK"):
        return code.upper()
    return tencent_symbol(code)


async def _board_bars(client: httpx.AsyncClient, bk: str) -> Bars:
    """Fetch daily ``[close, low]`` of one East Money board index."""
    params = {
        "secid": f"90.{bk}",
        "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55",
        "klt": 101,
        "fqt": 0,
        "beg": "20260801",
        "end": "20261231",
    }
    resp = await client.get(KLINE_URL, params=params)
    rows = ((resp.json() or {}).get("data") or {}).get("klines") or []
    out: Bars = {}
    for r in rows:
        f = r.split(",")
        out[f[0]] = [float(f[2]), float(f[4])]
    return out


async def _sina_bars(client: httpx.AsyncClient, sym: str) -> Bars:
    """Fetch unadjusted daily ``[close, low]`` from Sina for a full symbol such as ``sz002317``."""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=90"
    )
    try:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        rows = resp.json()
    except Exception:
        return {}
    if not isinstance(rows, list):
        return {}
    return {str(r["day"])[:10]: [float(r["close"]), float(r["low"])] for r in rows if r.get("close")}


def _save(cache: dict[str, Bars], path: Path = CACHE) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


async def _load_m5(syms: set[str], refresh: bool) -> dict[str, Bars]:
    """Load Sina 5-minute ``[close, low]`` bars (label = bar end time, ~21 sessions back)."""
    cache: dict[str, Bars] = {}
    if M5_CACHE.exists() and not refresh:
        cache = json.loads(M5_CACHE.read_text(encoding="utf-8"))
    todo = sorted(s for s in syms if not cache.get(s))
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:

        async def _one(sym: str) -> None:
            url = (
                "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"CN_MarketData.getKLineData?symbol={sym}&scale=5&ma=no&datalen=1023"
            )
            async with sem:
                try:
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
                    rows = resp.json()
                except Exception:
                    rows = []
                if isinstance(rows, list) and rows:
                    cache[sym] = {str(r["day"]): [float(r["close"]), float(r["low"])] for r in rows if r.get("close")}
                await asyncio.sleep(0.3)

        await asyncio.gather(*(_one(s) for s in todo))
    _save(cache, M5_CACHE)
    missing = [s for s in syms if not cache.get(s)]
    if missing:
        print(f"5 分钟线缺 {len(missing)} 只")
    return cache


def _intraday_entry(m5: Bars, at: str, plan: float) -> tuple[float | None, float | None]:
    """Return (signal price, plan fill) from 5-minute bars, without look-ahead.

    The signal price is the close of the bar containing ``at``. The plan fills at
    the signal price when already at / below plan, else at plan only if a *later*
    bar's low reaches it; otherwise the plan fill is ``None``.
    """
    day = at[:10]
    bars = sorted((k, v) for k, v in m5.items() if k[:10] == day)
    idx = next((i for i, (k, _) in enumerate(bars) if k >= at), None)
    if not bars or idx is None:
        return (bars[-1][1][0] if bars else None), None
    sig = bars[idx][1][0]
    if sig <= plan:
        return sig, sig
    later_low = min((v[1] for _, v in bars[idx + 1 :]), default=None)
    return sig, (plan if later_low is not None and later_low <= plan else None)


async def _load_bars(keys: set[str], refresh: bool) -> dict[str, Bars]:
    """Load bars for every key, extending the temp-dir cache (failures are retried next run)."""
    from market_desk.tencent import fetch_daily_bars_symbol

    cache: dict[str, Bars] = {}
    if CACHE.exists() and not refresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    def _needs(k: str) -> bool:
        bars = cache.get(k)
        return not bars or (not k.startswith(("BK", "sh000")) and all(v[1] is None for v in bars.values()))

    todo_tx = sorted(k for k in keys if not k.startswith("BK") and _needs(k))
    todo_bk = sorted(k for k in keys if k.startswith("BK") and _needs(k))
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:

        async def _tx(sym: str) -> None:
            async with sem:
                got = await _sina_bars(client, sym)
                if not got:
                    bars = await fetch_daily_bars_symbol(client, sym, limit=90)
                    got = {b["date"]: [float(b["close"]), b.get("low")] for b in bars or []}
                if got:
                    cache[sym] = got
                await asyncio.sleep(0.3)

        await asyncio.gather(*(_tx(s) for s in todo_tx))
        _save(cache)
        client.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        fails = 0
        for bk in todo_bk:
            if fails >= 3:
                break
            try:
                got = await _board_bars(client, bk)
            except Exception:
                got = {}
            if got:
                cache[bk] = got
                fails = 0
                _save(cache)
            else:
                fails += 1
            await asyncio.sleep(EM_PACE_SEC)
    missing = [k for k in todo_bk if not cache.get(k)]
    if missing:
        print(f"板块 K 线缺 {len(missing)} 个（东财限流，稍后重跑会续上）")
    return cache


def _load_cards() -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Load buy cards on trading days and the board name -> BK map."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    name_bk: dict[str, str] = {}
    for name, bk in con.execute("SELECT name, bk FROM board_daily ORDER BY trade_date"):
        if name and str(bk).upper().startswith("BK"):
            name_bk[str(name)] = str(bk).upper()
    cards = []
    q = (
        "SELECT trade_date, signaled_at, signal_type, mainline, code, price, ready, payload "
        "FROM signals WHERE signal_type IN (%s)" % ",".join("?" * len(BUY_TYPES))
    )
    for day, at, stype, ml, code, price, ready, raw in con.execute(q, BUY_TYPES):
        day = str(day)[:10]
        if not is_trading_day(day) or not price:
            continue
        try:
            p = json.loads(raw or "{}")
        except ValueError:
            p = {}
        names = p.get("board_names") or []
        board = p.get("source_board") or (names[0] if names else None) or ml
        first_last = p.get("first_last")
        cards.append(
            {
                "day": day,
                "at": str(at or ""),
                "type": stype,
                "code": str(code),
                "plan": float(price),
                "first_last": float(first_last) if first_last else None,
                "lit": bool(p.get("ever_ready") or ready),
                "board": board,
                "vs_mainline": p.get("vs_mainline") or "",
                "trend": "升" if p.get("trend_ok") else ("降" if p.get("trend_down") else "其他"),
            }
        )
    con.close()
    return cards, name_bk


def _nth_after(bars: Bars, day: str, n: int) -> str | None:
    """Return the ``n``-th session date after ``day`` present in ``bars``."""
    after = sorted(d for d in bars if d > day)
    return after[n - 1] if len(after) >= n else None


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def _decompose(
    card: dict[str, Any], bars: dict[str, Bars], m5: dict[str, Bars], bench: str, name_bk: dict[str, str]
) -> None:
    """Attach entry returns and attribution parts to ``card`` in place."""
    sym = _key(card["code"])
    st = bars.get(sym) or {}
    bm = bars.get(bench) or {}
    d0 = card["day"]
    d3 = _nth_after(st, d0, 3)
    if d0 not in st or not d3 or d0 not in bm or d3 not in bm:
        return
    c0 = st[d0][0]
    c3 = st[d3][0]
    market = _pct(bm[d3][0], bm[d0][0])
    card.update(market=market, r_close=_pct(c3, c0))
    sig, fill = _intraday_entry(m5.get(sym) or {}, card["at"], card["plan"]) if card["at"] else (None, None)
    card["sig_m5"] = sig
    sig = sig or card["first_last"]
    if sig:
        card["sig_px"] = sig
        card["r_signal"] = _pct(c3, sig)
    card["touched"] = fill is not None
    if fill is not None:
        card["r_plan"] = _pct(c3, fill)
    bk = name_bk.get(str(card["board"] or ""))
    bb = bars.get(bk) if bk else None
    if bb and d0 in bb and d3 in bb:
        card["board_ret"] = _pct(bb[d3][0], bb[d0][0])


def _dedupe(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one card per (day, code): lit first, then earliest (same rule as edge_shadow)."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for c in cards:
        key = (c["day"], c["code"])
        prev = best.get(key)
        if prev is None or (not c["lit"], c["at"]) < (not prev["lit"], prev["at"]):
            best[key] = c
    return list(best.values())


def _excess(rows: list[dict[str, Any]], key: str) -> tuple[int, float | None, str]:
    """Return (n, mean excess over market, 'positive days/days tD') for one entry return.

    ``tD`` is the t-stat of the daily mean excess across days; cards on the same day
    share one market move, so days rather than cards are the independent samples.
    """
    rows = [r for r in rows if key in r]
    if not rows:
        return 0, None, "-"
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r[key] - r["market"])
    means = [sum(v) / len(v) for v in by_day.values()]
    pos = sum(1 for m in means if m > 0)
    t = ""
    if len(means) >= 3:
        mu = sum(means) / len(means)
        sd = (sum((m - mu) ** 2 for m in means) / (len(means) - 1)) ** 0.5
        t = f" t{mu / (sd / len(means) ** 0.5):+.1f}" if sd else ""
    return len(rows), sum(r[key] - r["market"] for r in rows) / len(rows), f"{pos}/{len(by_day)}{t}"


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:+.2f}"


def _entry_table(title: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Print excess over market for the three entries (mean, positive days / days)."""
    print(f"\n== {title} ==")
    print(
        f"{'分组':<10}{'卡':>5}{'大盘':>8} | {'信号价买':>8}{'正/天 t':>13} | {'收盘买':>8}{'正/天 t':>13}"
        f" | {'触价卡':>6}{'计划价买':>9}{'正/天 t':>13}"
    )
    for label, rows in groups:
        rows = [r for r in rows if "market" in r]
        if not rows:
            continue
        mk = sum(r["market"] for r in rows) / len(rows)
        _, s_ex, s_pos = _excess(rows, "r_signal")
        _, c_ex, c_pos = _excess(rows, "r_close")
        p_n, p_ex, p_pos = _excess(rows, "r_plan")
        print(
            f"{label:<10}{len(rows):>5}{mk:>+8.2f} | {_fmt(s_ex):>8}{s_pos:>13} | {_fmt(c_ex):>8}{c_pos:>13}"
            f" | {p_n:>6}{_fmt(p_ex):>9}{p_pos:>13}"
        )


def _board_table(title: str, groups: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """Print the four-part split of the signal-entry return on board-mapped cards."""
    print(f"\n== {title} ==")
    print(
        f"{'分组':<10}{'卡':>5}{'天':>4}{'信号价三日':>10}{'大盘':>8}{'选板块':>8}{'选个股':>8}{'进场':>8}"
        f"{'板块正/天':>10}{'个股正/天':>10}{'跑赢板块':>9}"
    )
    for label, rows in groups:
        rows = [r for r in rows if "board_ret" in r and "r_signal" in r]
        if not rows:
            continue
        n = len(rows)
        days: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            days[r["day"]].append(r)

        def pos_days(fn) -> str:
            return f"{sum(1 for v in days.values() if sum(fn(x) for x in v) / len(v) > 0)}/{len(days)}"

        mean = lambda fn: sum(fn(r) for r in rows) / n  # noqa: E731
        board_pick = lambda r: r["board_ret"] - r["market"]  # noqa: E731
        stock_pick = lambda r: r["r_close"] - r["board_ret"]  # noqa: E731
        print(
            f"{label:<10}{n:>5}{len(days):>4}{mean(lambda r: r['r_signal']):>+10.2f}{mean(lambda r: r['market']):>+8.2f}"
            f"{mean(board_pick):>+8.2f}{mean(stock_pick):>+8.2f}{mean(lambda r: r['r_signal'] - r['r_close']):>+8.2f}"
            f"{pos_days(board_pick):>10}{pos_days(stock_pick):>10}"
            f"{100.0 * sum(1 for r in rows if r['r_close'] > r['board_ret']) / n:>8.0f}%"
        )


def main() -> None:
    """Run the attribution audit and print the tables."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bench", default="sh000852", choices=BENCHES)
    ap.add_argument("--refresh", action="store_true", help="Refetch bars instead of using the cache")
    args = ap.parse_args()

    cards, name_bk = _load_cards()
    keys = {_key(c["code"]) for c in cards} | set(BENCHES)
    keys |= {name_bk[c["board"]] for c in cards if c["board"] in name_bk}
    bars = asyncio.run(_load_bars(keys, args.refresh))
    m5 = asyncio.run(_load_m5({_key(c["code"]) for c in cards}, args.refresh))
    for c in cards:
        _decompose(c, bars, m5, args.bench, name_bk)

    chk = []
    for c in cards:
        if c["first_last"] and c["at"]:
            sig, _ = _intraday_entry(m5.get(_key(c["code"])) or {}, c["at"], c["plan"])
            if sig:
                chk.append(abs(sig / c["first_last"] - 1) * 100)
    if chk:
        chk.sort()
        print(f"信号价校验：5 分钟线 vs 首见价 {len(chk)} 张，偏差中位 {chk[len(chk) // 2]:.2f}%，最大 {chk[-1]:.2f}%")

    done = [c for c in cards if "market" in c]
    uniq = _dedupe(done)
    print(
        f"买卡 {len(cards)}，满三个交易日 {len(done)}（去重后 {len(uniq)}），"
        f"有首见价 {sum(1 for c in done if 'r_signal' in c)}，当日触及计划价 {sum(1 for c in done if c.get('touched'))}，"
        f"能映射板块 {sum(1 for c in done if 'board_ret' in c)}；基准 {args.bench}"
    )
    unmapped: dict[str, int] = defaultdict(int)
    for c in done:
        if "board_ret" not in c:
            unmapped[str(c["board"])] += 1
    if unmapped:
        top = sorted(unmapped.items(), key=lambda kv: -kv[1])[:8]
        print("未映射板块（卡数）：" + "、".join(f"{k}({v})" for k, v in top))

    lit_groups = [("全部", uniq), ("亮过灯", [c for c in uniq if c["lit"]]), ("没亮灯", [c for c in uniq if not c["lit"]])]
    type_groups = [(TYPE_LABEL[t], [c for c in done if c["type"] == t]) for t in BUY_TYPES]
    vs = sorted({c["vs_mainline"] for c in uniq if c["vs_mainline"]})
    vs_groups = [(v, [c for c in uniq if c["vs_mainline"] == v]) for v in vs]
    trend_groups = [(f"日线{t}", [c for c in uniq if c["trend"] == t]) for t in ("升", "降", "其他")]

    _entry_table("三种进场相对大盘的三日超额（同日同代码去重）", lit_groups + trend_groups)
    _entry_table("按卡片类型（不去重）", type_groups)
    _board_table("信号价买的四段拆分（能映射板块的卡，去重）", lit_groups + trend_groups + vs_groups)
    _board_table("四段拆分按卡片类型（不去重）", type_groups)


if __name__ == "__main__":
    main()
