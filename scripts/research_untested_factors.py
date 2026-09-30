"""Offline test of pick-score candidates never tested before, on stored stock buy signals.

Groups (every feature uses only data known before the signal unless tagged 「含未来」):

* Leader / relative strength — limit-ups in the prior 10 days, streak into the
  signal day, 5/20-day return, 20-day max day, board-leader on the prior day
  (``fund_flow_daily.leader_code``), dragon signal type.
* Mainline strength — prior-day ``board_daily`` row of the stock's boards
  (limit-up count, pct, leader height), theme persistence before the day
  (``theme_day_outcome``), mainline switches earlier that day.
* Money flow — stock main-force net pct (prior day / prior 3 days) and the
  prior-day board flow from ``fund_flow_daily``.
* Dragon-tiger list — appearance within the prior 5 trade days, net buy ratio,
  famous hot-money / institution seats among the top buyers.
* Auction / open — the day's open gap and the move from open to the plan price.
* Size — prior-day turnover amount and float market cap.
* Minute structure — payload ``minute`` (tip / vol ratio / pullback / fails),
  judged mainly from the refresh price (``signals.last``).

Targets: ``d3`` (stored plan-price outcome), ``pc3`` (close D → close D+3,
excludes the signal day) and ``fl3`` (refresh price → close D+3). Stats are the
mean daily Spearman IC (t over days, share of positive days), the same inside
day × mainline groups, and flagged-vs-peers excess for binary features.

Usage: ``python scripts/research_untested_factors.py [--refresh]`` (reads data/desk.db read-only)
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import optimize_pick_score as ops  # noqa: E402
from optimize_pick_score import aps, af, fmt, mean, stdev  # noqa: E402
from research_regime_factors import limit_pct, series, stock_feats  # noqa: E402
from market_desk.config import HTTP_HEADERS  # noqa: E402
from market_desk.lhb_seats import classify_seat  # noqa: E402
from market_desk.pick_score import build_history_stats, score_pick  # noqa: E402

LHB_CACHE = Path(tempfile.gettempdir()) / "md-untested-lhb-cache.json"
DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
LHB_LOOKBACK = 5


# ---------------------------------------------------------------- data

def load_db() -> dict[str, Any]:
    """Read the aux tables used as point-in-time context."""
    conn = sqlite3.connect(f"file:{aps.DB}?mode=ro", uri=True)
    sig = {int(i): {"mainline": str(m or ""), "last": af._f(l)}
           for i, m, l in conn.execute("SELECT id, mainline, last FROM signals")}
    board: dict[str, dict[str, dict[str, Any]]] = {}
    for d, name, zt, pct, lb, st in conn.execute(
            "SELECT trade_date, name, zt_n, pct, leader_boards, status FROM board_daily"):
        board.setdefault(str(d)[:10], {})[str(name)] = {"zt": zt, "pct": pct, "lb": lb, "status": st}
    flow_b: dict[str, dict[str, float]] = {}
    leaders: dict[str, set[str]] = {}
    for d, name, mp, lc in conn.execute("SELECT trade_date, name, main_pct, leader_code FROM fund_flow_daily"):
        d = str(d)[:10]
        if mp is not None:
            flow_b.setdefault(d, {})[str(name)] = float(mp)
        if lc:
            leaders.setdefault(d, set()).add(str(lc).zfill(6))
    theme_out = [(str(t)[:10], str(n or "")[:10], str(k), str(o)) for t, n, k, o in conn.execute(
        "SELECT trade_date, next_date, theme_key, outcome FROM theme_day_outcome")]
    switches: dict[str, list[str]] = {}
    for d, at in conn.execute("SELECT trade_date, switched_at FROM mainline_switch"):
        switches.setdefault(str(d)[:10], []).append(str(at))
    conn.close()
    return {"sig": sig, "board": board, "flow_b": flow_b, "leaders": leaders,
            "theme_out": theme_out, "switches": switches}


async def _dc_page(client: httpx.AsyncClient, report: str, filt: str, page: int, size: int = 500) -> dict[str, Any]:
    params = {"reportName": report, "columns": "ALL", "filter": filt, "pageNumber": str(page),
              "pageSize": str(size), "source": "WEB", "client": "WEB",
              "sortColumns": "TRADE_DATE", "sortTypes": "-1"}
    for attempt in range(3):
        try:
            resp = await client.get(DC_URL, params=params, headers=HTTP_HEADERS, timeout=20.0)
            return (resp.json().get("result") or {}) if resp.status_code == 200 else {}
        except Exception:
            await asyncio.sleep(1.0 + attempt)
    return {}


async def fetch_lhb(start: str, end: str, refresh: bool) -> dict[str, Any]:
    """Billboard appearances in [start, end] plus top buy seats for each (code, day)."""
    cache: dict[str, Any] = {}
    if LHB_CACHE.exists() and not refresh:
        try:
            cache = json.loads(LHB_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if cache.get("range") == [start, end]:
        return cache
    filt = f"(TRADE_DATE>='{start}')(TRADE_DATE<='{end}')"
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        page, pages = 1, 1
        while page <= pages:
            res = await _dc_page(client, "RPT_DAILYBILLBOARD_DETAILS", filt, page)
            rows.extend(r for r in (res.get("data") or []) if isinstance(r, dict))
            pages = int(res.get("pages") or 0)
            page += 1
        apps: dict[str, dict[str, Any]] = {}
        for r in rows:
            key = f"{str(r.get('SECURITY_CODE') or '').zfill(6)}|{str(r.get('TRADE_DATE') or '')[:10]}"
            net = af._f(r.get("BILLBOARD_NET_AMT")) or 0.0
            if key not in apps or abs(net) > abs(apps[key]["net"]):
                apps[key] = {"net": net, "buy": af._f(r.get("BILLBOARD_BUY_AMT")) or 0.0,
                             "sell": af._f(r.get("BILLBOARD_SELL_AMT")) or 0.0,
                             "amt": af._f(r.get("ACCUM_AMOUNT")) or af._f(r.get("DEAL_AMOUNT")) or 0.0}
        cache = {"range": [start, end], "apps": apps, "seats": {}}
        print(f"lhb rows {len(rows)} · appearances {len(apps)}", flush=True)
    return cache


async def fetch_seats(cache: dict[str, Any], keys: list[str]) -> None:
    """Top buy seats per (code, day) key, stored as style lists."""
    todo = [k for k in keys if k not in cache["seats"]]
    if not todo:
        return
    print(f"fetching seats for {len(todo)} appearances ...", flush=True)
    sem = asyncio.Semaphore(3)

    async def one(client: httpx.AsyncClient, key: str) -> None:
        code, day = key.split("|")
        async with sem:
            res = await _dc_page(client, "RPT_BILLBOARD_DAILYDETAILSBUY",
                                 f"(TRADE_DATE='{day}')(SECURITY_CODE=\"{code}\")", 1, 10)
        out = []
        for r in res.get("data") or []:
            name = str(r.get("OPERATEDEPT_NAME") or "")
            style = "institution" if "机构专用" in name else classify_seat(name)["style"]
            out.append({"style": style, "net": af._f(r.get("NET")) or 0.0})
        cache["seats"][key] = out

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(one(client, k) for k in todo))


# ---------------------------------------------------------------- features

def _lot(code: str) -> float:
    """Shares per unit of Tencent daily volume: STAR reports shares, other boards lots of 100."""
    return 1.0 if code.startswith(("688", "689")) else 100.0


def _names(r: dict[str, Any], ml: str) -> list[str]:
    p = r["payload"]
    out = [str(x) for x in (p.get("board_names") or []) if x]
    for x in (p.get("source_board"), ml):
        if x and str(x) not in out:
            out.append(str(x))
    return out


def features(r: dict[str, Any], bars: list[dict[str, Any]], t: int, db: dict[str, Any],
             lhb: dict[str, Any], mkt: dict[str, float]) -> dict[str, float | None]:
    """All candidate features for one signal; ``t`` is the index of the signal day's bar."""
    code, day, p = r["code"], r["trade_date"], r["payload"]
    meta = db["sig"].get(int(r["id"])) or {}
    ml = meta.get("mainline") or ""
    prev_day = bars[t - 1]["date"]
    lim = limit_pct(code)
    zt_flags = [(b["pct"] or 0.0) >= lim - 0.3 for b in bars[t - 10:t]]
    streak = 0
    for z in reversed(zt_flags):
        if not z:
            break
        streak += 1
    f: dict[str, float | None] = dict(stock_feats(bars, t, mkt))
    f.update({
        "ret5": (bars[t - 1]["close"] / bars[t - 6]["close"] - 1.0) * 100.0,
        "zt10": float(sum(zt_flags)),
        "streak": float(streak),
        "leader_prev": 1.0 if code in db["leaders"].get(prev_day, set()) else 0.0,
        "leader_day": 1.0 if code in db["leaders"].get(day, set()) else 0.0,
        "dragon": 1.0 if r["signal_type"] == "buy_dragon" else 0.0,
    })
    names = _names(r, ml)
    bd_prev = [db["board"].get(prev_day, {}).get(n) for n in names]
    bd_prev = [x for x in bd_prev if x]
    bd_day = [x for x in (db["board"].get(day, {}).get(n) for n in names) if x]
    f["board_zt_prev"] = float(max(x["zt"] or 0 for x in bd_prev)) if bd_prev else None
    f["board_pct_prev"] = max(float(x["pct"] or 0.0) for x in bd_prev) if bd_prev else None
    f["board_lb_prev"] = float(max(x["lb"] or 0 for x in bd_prev)) if bd_prev else None
    f["board_zt_day"] = float(max(x["zt"] or 0 for x in bd_day)) if bd_day else None
    keys = set(names)
    known = [o for (td, nd, k, o) in db["theme_out"] if k in keys and nd and nd < day]
    f["theme_persist_n"] = float(sum(1 for o in known if o == "persist")) if known else None
    f["theme_fade_n"] = float(sum(1 for o in known if o == "fade")) if known else None
    f["theme_persist_rate"] = (sum(1 for o in known if o == "persist") / len(known)) if known else None
    sw = db["switches"].get(day) or []
    f["switches_before"] = float(sum(1 for at in sw if at < str(r["signaled_at"] or "")))
    flow = r.get("_flow") or {}
    fp = (flow.get(prev_day) or {}).get("main_pct")
    f3 = [(flow.get(b["date"]) or {}).get("main_pct") for b in bars[t - 3:t]]
    f3 = [x for x in f3 if x is not None]
    f["flow_prev"] = fp
    f["flow_3d"] = mean(f3) if len(f3) == 3 else None
    f["flow_day"] = (flow.get(day) or {}).get("main_pct")
    fb = [db["flow_b"].get(prev_day, {}).get(n) for n in names]
    fb = [x for x in fb if x is not None]
    f["board_flow_prev"] = max(fb) if fb else None
    lhb_days = [b["date"] for b in bars[t - LHB_LOOKBACK:t]]
    hits = [(d, lhb["apps"][f"{code}|{d}"]) for d in lhb_days if f"{code}|{d}" in lhb["apps"]]
    f["lhb5"] = 1.0 if hits else 0.0
    f["lhb_prev"] = 1.0 if f"{code}|{prev_day}" in lhb["apps"] else 0.0
    if hits:
        d, a = hits[-1]
        tot = a["buy"] + a["sell"]
        f["lhb_net_ratio"] = a["net"] / tot * 100.0 if tot else None
        seats = lhb["seats"].get(f"{code}|{d}") or []
        f["lhb_hot"] = float(sum(1 for s in seats if s["style"] == "hot_money"))
        f["lhb_inst"] = float(sum(1 for s in seats if s["style"] == "institution"))
        f["lhb_quant"] = float(sum(1 for s in seats if s["style"] == "quant"))
    b0, bp = bars[t], bars[t - 1]
    if b0.get("open") and bp.get("close"):
        f["gap_open"] = (b0["open"] / bp["close"] - 1.0) * 100.0
        f["since_open"] = (r["entry"] / b0["open"] - 1.0) * 100.0 if r["entry"] else None
    if bp.get("volume") and bp.get("close"):
        amt = bp["volume"] * _lot(code) * bp["close"]
        f["amount_prev"] = math.log10(amt / 1e8) if amt > 0 else None
        f["float_cap"] = math.log10(amt / (bp["turnover"] / 100.0) / 1e8) if bp.get("turnover") else None
    m = p.get("minute") if isinstance(p.get("minute"), dict) else None
    if m:
        f["m_ok"] = 1.0 if m.get("ok") else 0.0
        f["m_tip"] = 1.0 if m.get("at_tip") else 0.0
        f["m_volr"] = af._f(m.get("vol_ratio"))
        f["m_pullback"] = af._f(m.get("pullback_pct"))
        f["m_fails"] = float(len(m.get("fails") or []))
        f["m_vol_ok"] = 1.0 if m.get("vol_ok") else 0.0
    return f


FEATS = (
    ("龙头/相对强度", (("zt10", "近10日涨停数"), ("streak", "连板数(至昨日)"), ("ret5", "5日涨幅"),
                  ("ret20", "20日涨幅"), ("max20", "20日最大单日涨幅"), ("ivol20", "特质波动(对照)"),
                  ("leader_prev", "昨日是板块领涨"), ("leader_day", "当日是板块领涨·含未来"),
                  ("dragon", "龙头信号"))),
    ("主线强度", (("board_zt_prev", "所属板块昨日涨停数"), ("board_pct_prev", "所属板块昨日涨幅"),
              ("board_lb_prev", "所属板块昨日最高板"), ("board_zt_day", "所属板块当日涨停数·含未来"),
              ("theme_persist_n", "题材此前持续次数"), ("theme_fade_n", "题材此前退潮次数"),
              ("theme_persist_rate", "题材此前持续率"), ("switches_before", "当日此前主线切换次数"))),
    ("资金流", (("flow_prev", "个股昨日主力净占比"), ("flow_3d", "个股前3日主力净占比"),
             ("flow_day", "个股当日主力净占比·含未来"), ("board_flow_prev", "板块昨日主力净占比"))),
    ("龙虎榜", (("lhb5", "近5日上榜"), ("lhb_prev", "昨日上榜"), ("lhb_net_ratio", "上榜净买占比"),
             ("lhb_hot", "买方知名游资席位数"), ("lhb_inst", "买方机构席位数"), ("lhb_quant", "买方量化席位数"))),
    ("竞价/开盘", (("gap_open", "开盘缺口%"), ("since_open", "开盘到计划价%"))),
    ("规模", (("amount_prev", "昨日成交额(log亿)"), ("float_cap", "流通市值(log亿)"))),
    ("分时结构", (("m_ok", "分时通过"), ("m_tip", "贴近分时尖"), ("m_volr", "分时量比"),
              ("m_pullback", "分时回撤%"), ("m_fails", "分时失败条数"), ("m_vol_ok", "量已降温"))),
)
BINARY = {"leader_prev", "leader_day", "dragon", "lhb5", "lhb_prev", "m_ok", "m_tip", "m_vol_ok"}


# ---------------------------------------------------------------- stats

def ic_by(rows: list[dict[str, Any]], key: str, target: str, group: str, min_n: int) -> dict[str, Any]:
    """Mean Spearman IC over groups with a t-stat over groups."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["f"].get(key) is not None and r.get(target) is not None:
            by.setdefault(r[group], []).append(r)
    ics = []
    for g in by.values():
        if len(g) < min_n or len({r["f"][key] for r in g}) < 2:
            continue
        rho = aps.spearman([float(r["f"][key]) for r in g], [r[target] for r in g])
        if rho is not None:
            ics.append(rho)
    sd = stdev(ics)
    return {"ic": mean(ics), "n": len(ics), "t": (mean(ics) / sd * math.sqrt(len(ics))) if sd and len(ics) > 2 else None,
            "pos": sum(1 for x in ics if x > 0) / len(ics) if ics else None,
            "cov": sum(1 for r in rows if r["f"].get(key) is not None)}


def flag_excess(rows: list[dict[str, Any]], key: str, target: str) -> tuple[int, float | None, float | None]:
    """Flagged rows vs same-day peers: count, mean excess, win-rate diff (pp)."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get(target) is not None and r["f"].get(key) is not None:
            by.setdefault(r["day"], []).append(r)
    ex, wd = [], []
    for g in by.values():
        m = mean([r[target] for r in g])
        w = sum(1 for r in g if r[target] > 0) / len(g)
        for r in g:
            if r["f"][key]:
                ex.append(r[target] - m)
                wd.append((1.0 if r[target] > 0 else 0.0) - w)
    return len(ex), mean(ex), (100 * mean(wd) if wd else None)


def report(rows: list[dict[str, Any]]) -> None:
    targets = (("d3", "计划价d3"), ("pc3", "收盘起d3"), ("fl3", "刷新价起d3"))
    for title, feats in FEATS:
        print(f"\n######## {title}")
        print(f"  {'因子':<22}{'覆盖':>5}" + "".join(f"{lab:>22}" for _, lab in targets) + f"{'同题材IC(计划价)':>18}")
        print(f"  {'':<22}{'':>5}" + "".join(f"{'IC  t  >0%  天':>22}" for _ in targets))
        for key, lab in feats:
            cells = []
            for tk, _ in targets:
                s = ic_by(rows, key, tk, "day", 6)
                cells.append(f"{fmt(s['ic'], '+.3f'):>7}{fmt(s['t'], '+5.1f'):>6}"
                             f"{fmt(None if s['pos'] is None else 100 * s['pos'], '4.0f'):>5}{s['n']:>4}")
            th = ic_by(rows, key, "d3", "theme", 4)
            cov = sum(1 for r in rows if r["f"].get(key) is not None)
            print(f"  {lab:<22}{cov:>5}" + "".join(f"{c:>22}" for c in cells)
                  + f"{fmt(th['ic'], '+10.3f')}({th['n']})")
            if key in BINARY:
                parts = []
                for tk, tl in targets:
                    n, e, w = flag_excess(rows, key, tk)
                    parts.append(f"{tl} 超额{fmt(e, '+.2f')}% 胜率差{fmt(w, '+.1f')}pp")
                n = sum(1 for r in rows if r["f"].get(key))
                print(f"  {'':<22}  命中 {n} 条 · " + " · ".join(parts))


Rule = tuple[str, float, float | None, str]


def composite(rows: list[dict[str, Any]], rules: list[Rule]) -> str:
    """Score plus rule points: same-day IC, days improved, and top-third minus bottom-third spread."""
    def adj(r: dict[str, Any]) -> float:
        s = r["score"]
        for key, pts, thr, sign in rules:
            x = r["f"].get(key)
            if x is None:
                continue
            hit = bool(x) if thr is None else (x >= thr if sign == ">=" else x <= thr)
            s += pts if hit else 0.0
        return s

    out = []
    for tk in ("d3", "pc3", "fl3"):
        sub = [dict(r, adj=adj(r)) for r in rows if r.get(tk) is not None]
        by: dict[str, list[dict[str, Any]]] = {}
        for r in sub:
            by.setdefault(r["day"], []).append(r)
        ib, ia, sb, sa, better = [], [], [], [], 0
        for g in by.values():
            if len(g) < 6:
                continue
            ys = [r[tk] for r in g]
            b = aps.spearman([r["score"] for r in g], ys)
            a = aps.spearman([r["adj"] for r in g], ys)
            if b is None or a is None:
                continue
            ib.append(b)
            ia.append(a)
            better += a > b + 1e-9
            k = max(2, len(g) // 3)
            for key, acc in (("score", sb), ("adj", sa)):
                s = sorted(g, key=lambda r: -r[key])
                acc.append(mean([r[tk] for r in s[:k]]) - mean([r[tk] for r in s[-k:]]))
        out.append(f"{tk} IC {fmt(mean(ib))}→{fmt(mean(ia))} 前1/3−后1/3 {fmt(mean(sb), '+.2f')}→{fmt(mean(sa), '+.2f')}pp"
                   f" ({better}/{len(ib)}天变好)")
    return " · ".join(out)


DEEP_KEYS = (("lhb5", "近5日上榜"), ("lhb_prev", "昨日上榜"), ("gap_open", "开盘缺口%"),
             ("float_cap", "流通市值(log亿)"), ("dragon", "龙头信号"), ("leader_prev", "昨日是板块领涨"),
             ("flow_3d", "个股前3日主力净占比"), ("max20", "20日最大单日涨幅"), ("pct", "信号时涨幅%"))


def _resid_ic(rows: list[dict[str, Any]], key: str, target: str) -> tuple[float | None, int]:
    """Daily IC of a feature with the target after removing the score's rank effect."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["f"].get(key) is not None and r.get(target) is not None:
            by.setdefault(r["day"], []).append(r)
    ics = []
    for g in by.values():
        if len(g) < 6 or len({r["f"][key] for r in g}) < 2:
            continue
        ys, xs = ops.ranks01([r[target] for r in g]), ops.ranks01([r["score"] for r in g])
        sxx = sum(x * x for x in xs)
        b = sum(x * y for x, y in zip(xs, ys)) / sxx if sxx else 0.0
        res = [y - b * x for x, y in zip(xs, ys)]
        rho = aps.spearman([float(r["f"][key]) for r in g], res)
        if rho is not None:
            ics.append(rho)
    return mean(ics), len(ics)


def deep(rows: list[dict[str, Any]]) -> None:
    for x in rows:
        x["f"]["pct"] = af._f(x["r"]["payload"].get("pct"))
    print("\n######## 深挖 A：去掉现行打分的影响后还剩多少（同日 IC：原始 → 残差）")
    for key, lab in DEEP_KEYS:
        cells = []
        for tk in ("d3", "pc3", "fl3"):
            raw = ic_by(rows, key, tk, "day", 6)["ic"]
            res, n = _resid_ic(rows, key, tk)
            cells.append(f"{tk} {fmt(raw)}→{fmt(res)}")
        print(f"  {lab:<20}" + " · ".join(cells))
    print("\n######## 深挖 B：候选因子之间的同日秩相关（均值）")
    keys = [k for k, _ in DEEP_KEYS]
    print(f"  {'':<14}" + "".join(f"{k[:9]:>10}" for k in keys))
    for a in keys:
        cells = []
        for b in keys:
            by: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                if r["f"].get(a) is not None and r["f"].get(b) is not None:
                    by.setdefault(r["day"], []).append(r)
            rh = [aps.spearman([float(r["f"][a]) for r in g], [float(r["f"][b]) for r in g])
                  for g in by.values() if len(g) >= 6]
            cells.append(fmt(mean([x for x in rh if x is not None]), "+10.2f"))
        print(f"  {a[:14]:<14}" + "".join(cells))
    print("\n######## 深挖 C：同日三分组超额%（低 / 中 / 高），括号为胜率差 pp")
    for key, lab in (("gap_open", "开盘缺口%"), ("float_cap", "流通市值(log亿)"), ("flow_3d", "个股前3日主力净占比"),
                     ("max20", "20日最大单日涨幅"), ("pct", "信号时涨幅%")):
        for tk in ("d3", "pc3", "fl3"):
            by: dict[str, list[dict[str, Any]]] = {}
            for r in rows:
                if r["f"].get(key) is not None and r.get(tk) is not None:
                    by.setdefault(r["day"], []).append(r)
            q: list[list[float]] = [[], [], []]
            w: list[list[float]] = [[], [], []]
            edges: list[list[float]] = [[], [], []]
            for g in by.values():
                if len(g) < 6:
                    continue
                m = mean([r[tk] for r in g])
                wr = sum(1 for r in g if r[tk] > 0) / len(g)
                for r, x in zip(g, ops.ranks01([float(r["f"][key]) for r in g])):
                    i = min(2, int((x + 0.5) * 3))
                    q[i].append(r[tk] - m)
                    w[i].append((1.0 if r[tk] > 0 else 0.0) - wr)
                    edges[i].append(float(r["f"][key]))
            cells = " / ".join(f"{fmt(mean(a), '+.2f')}({fmt(100 * mean(b) if b else None, '+.0f')})" for a, b in zip(q, w))
            rng = " | ".join(f"{fmt(mean(e), '.2f')}" for e in edges)
            print(f"  {lab:<20}{tk:<5}{cells}   组均值 {rng}")


# ---------------------------------------------------------------- big sample

async def fetch_lhb_all(start: str, end: str) -> set[str]:
    """All billboard (code|day) keys in [start, end], cached by range."""
    path = Path(tempfile.gettempdir()) / "md-untested-lhb-big.json"
    if path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if blob.get("range") == [start, end]:
                return set(blob["keys"])
        except Exception:
            pass
    keys: set[str] = set()
    async with httpx.AsyncClient() as client:
        page, pages = 1, 1
        while page <= pages:
            res = await _dc_page(client, "RPT_DAILYBILLBOARD_DETAILS",
                                 f"(TRADE_DATE>='{start}')(TRADE_DATE<='{end}')", page)
            for r in res.get("data") or []:
                keys.add(f"{str(r.get('SECURITY_CODE') or '').zfill(6)}|{str(r.get('TRADE_DATE') or '')[:10]}")
            pages = int(res.get("pages") or 0)
            page += 1
    path.write_text(json.dumps({"range": [start, end], "keys": sorted(keys)}), encoding="utf-8")
    return keys


def big_sample(codes: list[str]) -> None:
    """Uptrend-pullback pseudo events (~2 years): open gap, float cap, amount, billboard."""
    universe = asyncio.run(ops.fetch_universe(codes, False))
    stocks = {c: series(b) for c, b in universe.items() if b and not c.startswith(("1", "5"))}
    dates = sorted({b["date"] for bars in stocks.values() for b in bars})
    lhb = asyncio.run(fetch_lhb_all(dates[0], dates[-1]))
    evs: list[dict[str, Any]] = []
    for code, bars in stocks.items():
        closes = [b["close"] for b in bars]
        for t in range(ops.WARMUP, len(bars) - ops.HOLD):
            c0, c = closes[t - 1], closes[t]
            pct = (c / c0 - 1.0) * 100.0
            if not (c > sum(closes[t - 20:t]) / 20.0 and (c0 / closes[t - 21] - 1.0) * 100.0 > 5.0 and -4.0 <= pct <= 1.0):
                continue
            b0, bp = bars[t], bars[t - 1]
            amt = (bp.get("volume") or 0.0) * _lot(code) * bp["close"]
            f = {
                "gap_open": (b0["open"] / bp["close"] - 1.0) * 100.0 if b0.get("open") else None,
                "amount_prev": math.log10(amt / 1e8) if amt > 0 else None,
                "float_cap": math.log10(amt / (bp["turnover"] / 100.0) / 1e8) if amt > 0 and bp.get("turnover") else None,
                "lhb5": 1.0 if any(f"{code}|{x['date']}" in lhb for x in bars[t - 5:t]) else 0.0,
                "lhb_prev": 1.0 if f"{code}|{bp['date']}" in lhb else 0.0,
            }
            evs.append({"day": b0["date"], "fwd3": (closes[t + ops.HOLD] / c - 1.0) * 100.0, "f": f})
    n_days = len({e["day"] for e in evs})
    print(f"\n######## 大样本复核：{len(stocks)} 只票 · {len(evs)} 个上升回踩日 · {n_days} 天 · 龙虎榜记录 {len(lhb)}")
    for key, lab in (("gap_open", "开盘缺口%"), ("float_cap", "流通市值(log亿)"), ("amount_prev", "昨日成交额(log亿)"),
                     ("lhb5", "近5日上榜"), ("lhb_prev", "昨日上榜")):
        daily = ops.daily_ic([dict(e, **{key: e["f"][key]}) for e in evs], lambda e, k=key: e.get(k), "fwd3", min_n=10)
        s = ops.ic_summary(daily)
        line = (f"  {lab:<18}IC {fmt(s['ic'])} t {fmt(s['t'], '+.1f')} >0 {fmt(None if s['pos'] is None else 100 * s['pos'], '.0f')}%"
                f" 前半 {fmt(s['ic_h1'])} 后半 {fmt(s['ic_h2'])} ({s['n_days']}天)")
        if key.startswith("lhb"):
            n, e, w = flag_excess([dict(x, d=x["fwd3"]) for x in evs], key, "d")
            line += f" · 命中 {n} 超额 {fmt(e, '+.2f')}% 胜率差 {fmt(w, '+.1f')}pp"
        print(line)
    for key, lab in (("gap_open", "开盘缺口%"), ("float_cap", "流通市值(log亿)")):
        by: dict[str, list[dict[str, Any]]] = {}
        for e in evs:
            if e["f"].get(key) is not None:
                by.setdefault(e["day"], []).append(e)
        q: list[list[float]] = [[] for _ in range(5)]
        w: list[list[float]] = [[] for _ in range(5)]
        edge: list[list[float]] = [[] for _ in range(5)]
        for g in by.values():
            if len(g) < 10:
                continue
            m = mean([e["fwd3"] for e in g])
            wr = sum(1 for e in g if e["fwd3"] > 0) / len(g)
            for e, x in zip(g, ops.ranks01([float(e["f"][key]) for e in g])):
                i = min(4, int((x + 0.5) * 5))
                q[i].append(e["fwd3"] - m)
                w[i].append((1.0 if e["fwd3"] > 0 else 0.0) - wr)
                edge[i].append(float(e["f"][key]))
        print(f"  {lab} 五分组 超额%(胜率差pp)[组均值]: "
              + "  ".join(f"{fmt(mean(a), '+.2f')}({fmt(100 * mean(b), '+.1f')})[{fmt(mean(c), '.2f')}]"
                          for a, b, c in zip(q, w, edge)))


# ---------------------------------------------------------------- main

def main() -> None:
    refresh = "--refresh" in sys.argv
    rows = [r for r in aps.load_rows() if r["entry"] and (r.get("kind") or "stock") != "etf"]
    codes = sorted({r["code"] for r in rows})
    cache = asyncio.run(af.fetch_all(codes, refresh=refresh, need_flow=True))
    mkt = aps.index_pct()
    db = load_db()
    days = sorted({r["trade_date"] for r in rows})
    all_dates = sorted({b["date"] for c in codes for b in (cache.get(c) or {}).get("bars") or []})
    first = max(0, all_dates.index(days[0]) - LHB_LOOKBACK - 1) if days[0] in all_dates else 0
    lhb = asyncio.run(fetch_lhb(all_dates[first], days[-1], refresh))
    empty = build_history_stats([])
    out = []
    for r in rows:
        bars = series((cache.get(r["code"]) or {}).get("bars") or [])
        t = next((i for i, b in enumerate(bars) if b["date"] == r["trade_date"]), None)
        if t is None or t < 25:
            continue
        r["_flow"] = (cache.get(r["code"]) or {}).get("flow") or {}
        r["cand"] = aps.candidate(r, bars)
        meta = db["sig"].get(int(r["id"])) or {}
        last = meta.get("last")
        c3 = bars[t + 3]["close"] if t + 3 < len(bars) else None
        out.append({
            "r": r, "t": t, "bars": bars, "day": r["trade_date"],
            "theme": r["trade_date"] + "|" + (meta.get("mainline") or "-"),
            "d3": float(r["outcome_day3_pct"]),
            "pc3": (c3 / bars[t]["close"] - 1.0) * 100.0 if c3 else None,
            "fl3": (c3 / last - 1.0) * 100.0 if c3 and last else None,
            "score": float(score_pick(r["cand"], empty)["score"]),
        })
    keys = sorted({f"{x['r']['code']}|{b['date']}" for x in out
                   for b in x["bars"][x["t"] - LHB_LOOKBACK:x["t"]]
                   if f"{x['r']['code']}|{b['date']}" in lhb["apps"]})
    asyncio.run(fetch_seats(lhb, keys))
    LHB_CACHE.write_text(json.dumps(lhb, ensure_ascii=False), encoding="utf-8")
    for x in out:
        x["f"] = features(x["r"], x["bars"], x["t"], db, lhb, mkt)
        x["f"]["score"] = x["score"]
    n_days = len({x["day"] for x in out})
    print(f"股票买点 {len(out)} 条 · {n_days} 天 · {len({x['theme'] for x in out})} 个 日×主线 组 · "
          f"收盘起可算 {sum(1 for x in out if x['pc3'] is not None)} · 刷新价起可算 {sum(1 for x in out if x['fl3'] is not None)}")
    print("说明：IC=同日 Spearman 秩相关均值；t 按天计（三日持有有重叠，t 偏乐观）；>0% = IC 为正的天数占比")
    report(out)
    print("\n######## 现行打分（对照）")
    for tk in ("d3", "pc3", "fl3"):
        s = ic_by(out, "score", tk, "day", 6)
        print(f"  {tk}: IC {fmt(s['ic'])} t {fmt(s['t'], '+.1f')} >0 {fmt(None if s['pos'] is None else 100 * s['pos'], '.0f')}% ({s['n']}天)")
    th = ic_by(out, "score", "d3", "theme", 4)
    print(f"  同题材 IC {fmt(th['ic'])} ({th['n']}组)")
    if "--deep" in sys.argv:
        deep(out)
    if "--big" in sys.argv:
        big_sample(codes)
    if "--combo" in sys.argv:
        print("\n######## 叠加到现行打分（现行→叠加后）")
        combos: tuple[tuple[str, list[Rule]], ...] = (
            ("20日涨幅≥30 −2", [("ret20", -2.0, 30.0, ">=")]),
            ("20日最大单日≥9.5 −2", [("max20", -2.0, 9.5, ">=")]),
            ("贴近分时尖 −3", [("m_tip", -3.0, None, ">=")]),
            ("近5日上榜 −3", [("lhb5", -3.0, None, ">=")]),
            ("近5日上榜 −5", [("lhb5", -5.0, None, ">=")]),
            ("昨日上榜 −5", [("lhb_prev", -5.0, None, ">=")]),
            ("近5日上榜 −3 + 昨日上榜再 −2", [("lhb5", -3.0, None, ">="), ("lhb_prev", -2.0, None, ">=")]),
            ("开盘缺口≥2% −3", [("gap_open", -3.0, 2.0, ">=")]),
            ("流通市值≥300亿 −3", [("float_cap", -3.0, math.log10(300.0), ">=")]),
            ("流通市值≤80亿 +3", [("float_cap", 3.0, math.log10(80.0), "<=")]),
            ("龙头信号 −3", [("dragon", -3.0, None, ">=")]),
            ("前3日主力净占比≥3 +2", [("flow_3d", 2.0, 3.0, ">=")]),
            ("上榜−3 + 缺口≥2 −3 + 市值≥300亿 −3", [("lhb5", -3.0, None, ">="), ("gap_open", -3.0, 2.0, ">="),
                                              ("float_cap", -3.0, math.log10(300.0), ">=")]),
        )
        for label, rules in combos:
            print(f"  {label}\n      {composite(out, rules)}")


if __name__ == "__main__":
    main()
