"""Sector money-flow monitor (observe-only).

Two data sources are shown side by side:

1. **East Money API** — live rankings
   - em_day  → 今日 (f62)
   - em_5d   → 近5日累计 (f164)
   - em_10d  → 近10日累计 (f174)

2. **Local SQLite** — daily snapshots we persist each session, then sum
   - loc_5d / loc_10d → last N stored trading days (fair compare vs EM)
   - loc_week / loc_month → calendar week (Mon→today) / calendar month

Diffs compare EM rolling windows vs local same-length trading-day sums.
Does not feed desk buy/sell gates.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# id, label, source(api|local), hint
PERIODS: tuple[tuple[str, str, str, str], ...] = (
    ("em_day", "今日·东财", "api", "接口即时：东财今日主力净流入（每次刷新重拉）"),
    ("loc_day", "当日·本地", "local", "本地库：今日最后一次落库快照（点存，非滚动）"),
    ("em_5d", "近5日·东财", "api", "接口：东财近5日累计"),
    ("em_10d", "近10日·东财", "api", "接口：东财近10日累计"),
    ("loc_5d", "近5日·本地", "local", "本地库：最近5个已存交易日累加"),
    ("loc_10d", "近10日·本地", "local", "本地库：最近10个已存交易日累加"),
    ("loc_week", "本周·本地", "local", "本地库：自然周周一至今日累加"),
    ("loc_month", "本月·本地", "local", "本地库：自然月1日至今日累加"),
)


def build_fund_flow_board(
    api_periods: dict[str, dict[str, list[dict[str, Any]]]] | None,
    *,
    trade_date: str,
    stored_dates: list[str] | None = None,
    stored_rows: list[dict[str, Any]] | None = None,
    top_n: int = 15,
) -> dict[str, Any]:
    """Assemble API + local period boards, stats, and API-vs-local diffs."""
    day = str(trade_date or "")[:10]
    dates_desc = [str(d)[:10] for d in (stored_dates or []) if str(d or "").strip()]
    raw = list(stored_rows or [])

    loc_windows = _local_windows(day, dates_desc)
    packed: dict[str, Any] = {}

    # API periods (keys: day / week / month from engine → remap).
    api_map = {
        "em_day": (api_periods or {}).get("day") or (api_periods or {}).get("em_day") or {},
        "em_5d": (api_periods or {}).get("week") or (api_periods or {}).get("em_5d") or {},
        "em_10d": (api_periods or {}).get("month") or (api_periods or {}).get("em_10d") or {},
    }
    for pid in ("em_day", "em_5d", "em_10d"):
        blob = api_map[pid]
        packed[pid] = _pack_period(
            pid,
            industry=blob.get("industry") or [],
            concept=blob.get("concept") or [],
            top_n=top_n,
            extra={"dates": [day] if pid == "em_day" else [], "day_n": 1 if pid == "em_day" else None},
        )

    for pid, win_dates in loc_windows.items():
        agg = _aggregate_rows(raw, win_dates)
        packed[pid] = _pack_period(
            pid,
            industry=agg.get("industry") or [],
            concept=agg.get("concept") or [],
            top_n=top_n,
            extra={
                "dates": win_dates,
                "day_n": len(win_dates),
                "range": (
                    f"{win_dates[-1]} → {win_dates[0]}" if win_dates else ""
                ),
            },
        )

    diffs = [
        _diff_pair(
            packed,
            api_id="em_day",
            local_id="loc_day",
            title="当日：东财今日(即时) vs 本地当日(落库快照)",
            top_n=top_n,
        ),
        _diff_pair(
            packed,
            api_id="em_5d",
            local_id="loc_5d",
            title="近5日：东财接口 vs 本地累加",
            top_n=top_n,
        ),
        _diff_pair(
            packed,
            api_id="em_10d",
            local_id="loc_10d",
            title="近10日：东财接口 vs 本地累加",
            top_n=top_n,
        ),
    ]

    day_skew = (
        "「今日·东财」每次刷新重拉即时榜；「当日·本地」是写入 SQLite 的点存快照"
        "（本轮保存后应接近，若你对比的是上一刷新落库或盘中资金仍在跳变，就会有差异）。"
        "另：两边都只取约 Top80，板块集合与排序瞬时变化也会造成差额。"
    )

    return {
        "ok": any(
            bool((packed.get(p) or {}).get("totals", {}).get("industry_n"))
            or bool((packed.get(p) or {}).get("totals", {}).get("concept_n"))
            for p, *_ in PERIODS
        ),
        "standalone": True,
        "disclaimer": (
            "「东财」= 接口即时榜；「本地」= 本机按日落库后再读/累加。"
            + day_skew
            + "自然周/月仅本地有。非官方国家队持仓；不改作战台买卖。"
        ),
        "note": (
            f"本地已存 {len(dates_desc)} 个交易日"
            + (f"（最新 {dates_desc[0]}）" if dates_desc else "（尚无历史，今日起开始积累）")
        ),
        "day_skew_note": day_skew,
        "default_period": "em_day",
        "period_meta": [
            {"id": p, "label": lab, "source": src, "hint": hint}
            for p, lab, src, hint in PERIODS
        ],
        "periods": packed,
        "storage": {
            "stored_n": len(dates_desc),
            "stored_from": dates_desc[-1] if dates_desc else "",
            "stored_to": dates_desc[0] if dates_desc else "",
            "today_saved": day in dates_desc,
            "windows": {
                k: {"dates": v, "day_n": len(v), "range": f"{v[-1]} → {v[0]}" if v else ""}
                for k, v in loc_windows.items()
            },
        },
        "diffs": diffs,
        "compare": _cross_period_compare(packed),
        **_flat_alias(packed.get("em_day") or {}),
    }


def _local_windows(trade_date: str, dates_desc: list[str]) -> dict[str, list[str]]:
    """Build local aggregation date lists (newest-first, matching dates_desc order)."""
    try:
        today = date.fromisoformat(trade_date[:10])
    except ValueError:
        today = date.today()
    monday = today - timedelta(days=today.weekday())
    month_start = date(today.year, today.month, 1)

    def take_n(n: int) -> list[str]:
        return dates_desc[:n]

    def in_range(start: date, end: date) -> list[str]:
        out: list[str] = []
        for d in dates_desc:
            try:
                dd = date.fromisoformat(d)
            except ValueError:
                continue
            if start <= dd <= end:
                out.append(d)
        return out

    return {
        "loc_day": [day] if day in dates_desc or dates_desc[:1] == [day] else (
            [day] if day else []
        ),
        "loc_5d": take_n(5),
        "loc_10d": take_n(10),
        "loc_week": in_range(monday, today),
        "loc_month": in_range(month_start, today),
    }


def _aggregate_rows(
    raw: list[dict[str, Any]],
    dates: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Sum main/super/large nets by board across the selected trade dates."""
    want = set(dates)
    if not want:
        return {"industry": [], "concept": []}
    bucket: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw:
        d = str(row.get("trade_date") or "")[:10]
        if d not in want:
            continue
        bk = str(row.get("bk") or "")
        kind = str(row.get("kind") or "industry")
        if not bk:
            continue
        key = (kind, bk)
        cur = bucket.get(key)
        if cur is None:
            cur = {
                "bk": bk,
                "kind": kind,
                "name": row.get("name") or bk,
                "main_net": 0.0,
                "super_net": 0.0,
                "large_net": 0.0,
                "pct": None,
                "main_pct": None,
                "leader_name": row.get("leader_name") or "",
                "leader_code": row.get("leader_code") or "",
                "days": 0,
            }
            bucket[key] = cur
        cur["main_net"] = float(cur["main_net"] or 0) + float(_f(row.get("main_net")) or 0)
        cur["super_net"] = float(cur["super_net"] or 0) + float(_f(row.get("super_net")) or 0)
        cur["large_net"] = float(cur["large_net"] or 0) + float(_f(row.get("large_net")) or 0)
        cur["days"] = int(cur["days"] or 0) + 1
        # Keep latest name / leader / pct from newest date (dates_desc order in raw not guaranteed).
        if d == dates[0]:
            cur["name"] = row.get("name") or cur["name"]
            cur["pct"] = row.get("pct")
            cur["main_pct"] = row.get("main_pct")
            cur["leader_name"] = row.get("leader_name") or cur["leader_name"]
            cur["leader_code"] = row.get("leader_code") or cur["leader_code"]
    industry = [v for (kind, _), v in bucket.items() if kind == "industry"]
    concept = [v for (kind, _), v in bucket.items() if kind == "concept"]
    return {"industry": industry, "concept": concept}


def _pack_period(
    pid: str,
    *,
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
    top_n: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = next((x for x in PERIODS if x[0] == pid), None)
    label = meta[1] if meta else pid
    source = meta[2] if meta else ""
    hint = meta[3] if meta else ""
    ind = _rank_rows(industry)
    con = _rank_rows(concept)
    out = {
        "id": pid,
        "label": label,
        "source": source,
        "hint": hint,
        "industry_in": ind[:top_n],
        "industry_out": list(reversed(ind[-top_n:])) if ind else [],
        "concept_in": con[:top_n],
        "concept_out": list(reversed(con[-top_n:])) if con else [],
        "spotlight": _spotlight(ind, con),
        "stats": _period_stats(ind, con, top_n=top_n),
        "industry_rank": ind[:40],
        "concept_rank": con[:40],
        "totals": {
            "industry_n": len(ind),
            "concept_n": len(con),
            "industry_in_yi": _sum_main(ind[:top_n]),
            "industry_out_yi": _sum_main(ind[-top_n:]) if ind else 0.0,
        },
    }
    if extra:
        out.update(extra)
    return out


def _diff_pair(
    packed: dict[str, Any],
    *,
    api_id: str,
    local_id: str,
    title: str,
    top_n: int,
) -> dict[str, Any]:
    """Compare industry rankings between an API period and a local period."""
    api = packed.get(api_id) or {}
    loc = packed.get(local_id) or {}
    api_all = (api.get("industry_rank") or []) or _merge_unique(
        api.get("industry_in") or [], api.get("industry_out") or []
    )
    loc_all = (loc.get("industry_rank") or []) or _merge_unique(
        loc.get("industry_in") or [], loc.get("industry_out") or []
    )
    by_bk: dict[str, dict[str, Any]] = {}
    for r in api_all:
        bk = str(r.get("bk") or "")
        if not bk:
            continue
        by_bk[bk] = {
            "bk": bk,
            "name": r.get("name") or bk,
            "api_yi": r.get("main_yi"),
            "local_yi": None,
            "delta_yi": None,
        }
    for r in loc_all:
        bk = str(r.get("bk") or "")
        if not bk:
            continue
        cur = by_bk.get(bk)
        if cur is None:
            cur = {
                "bk": bk,
                "name": r.get("name") or bk,
                "api_yi": None,
                "local_yi": r.get("main_yi"),
                "delta_yi": None,
            }
            by_bk[bk] = cur
        else:
            cur["local_yi"] = r.get("main_yi")
            if not cur.get("name"):
                cur["name"] = r.get("name") or bk
    rows = []
    for cur in by_bk.values():
        a = _f(cur.get("api_yi"))
        b = _f(cur.get("local_yi"))
        if a is None and b is None:
            continue
        delta = None
        if a is not None and b is not None:
            delta = round(b - a, 2)
        cur["delta_yi"] = delta
        cur["only"] = (
            "仅东财" if b is None and a is not None
            else ("仅本地" if a is None and b is not None else "")
        )
        rows.append(cur)
    rows.sort(key=lambda x: abs(float(x.get("delta_yi") or x.get("api_yi") or x.get("local_yi") or 0)), reverse=True)
    api_top = [str(r.get("name") or "") for r in (api.get("industry_in") or [])[:5]]
    loc_top = [str(r.get("name") or "") for r in (loc.get("industry_in") or [])[:5]]
    overlap = sorted(set(api_top) & set(loc_top))
    return {
        "id": f"{api_id}_vs_{local_id}",
        "title": title,
        "api_id": api_id,
        "local_id": local_id,
        "api_label": api.get("label") or api_id,
        "local_label": loc.get("label") or local_id,
        "local_range": loc.get("range") or "",
        "local_day_n": loc.get("day_n"),
        "api_top5": api_top,
        "local_top5": loc_top,
        "overlap_top5": overlap,
        "overlap_n": len(overlap),
        "note": (
            f"Top5 重合 {len(overlap)} 只"
            + (f"；本地样本 {loc.get('day_n') or 0} 日" if loc.get("day_n") is not None else "")
            + (f"（{loc.get('range')}）" if loc.get("range") else "")
        ),
        "rows": rows[: max(top_n, 20)],
    }


def _merge_unique(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for lst in lists:
        for r in lst:
            bk = str(r.get("bk") or "")
            if not bk or bk in seen:
                continue
            seen.add(bk)
            out.append(r)
    return out


def _flat_alias(day: dict[str, Any]) -> dict[str, Any]:
    if not day:
        return {
            "industry_in": [],
            "industry_out": [],
            "concept_in": [],
            "concept_out": [],
            "spotlight": [],
            "totals": {},
            "stats": {},
        }
    return {
        "industry_in": day.get("industry_in") or [],
        "industry_out": day.get("industry_out") or [],
        "concept_in": day.get("concept_in") or [],
        "concept_out": day.get("concept_out") or [],
        "spotlight": day.get("spotlight") or [],
        "totals": day.get("totals") or {},
        "stats": day.get("stats") or {},
    }


def _period_stats(
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
    *,
    top_n: int,
) -> dict[str, Any]:
    """Summarize breadth / concentration / big-money share for one period."""
    ind_in = industry[:top_n]
    ind_out = list(reversed(industry[-top_n:])) if industry else []
    in_yi = _sum_main(ind_in)
    out_yi = _sum_main(ind_out)
    pos_n = sum(1 for r in industry if float(r.get("main_yi") or 0) > 0)
    neg_n = sum(1 for r in industry if float(r.get("main_yi") or 0) < 0)
    top5 = industry[:5]
    top5_yi = _sum_main(top5)
    pos_total = sum(float(r.get("main_yi") or 0) for r in industry if float(r.get("main_yi") or 0) > 0)
    conc = round(top5_yi / pos_total * 100.0, 1) if pos_total > 0 else None
    big_n = sum(1 for r in industry[:30] if r.get("big_money"))
    big_con = sum(1 for r in concept[:30] if r.get("big_money"))
    return {
        "industry_in_yi": in_yi,
        "industry_out_yi": out_yi,
        "industry_net_yi": round(in_yi + out_yi, 2),
        "pos_n": pos_n,
        "neg_n": neg_n,
        "breadth": round(pos_n / max(pos_n + neg_n, 1) * 100.0, 1),
        "top5_in_yi": top5_yi,
        "top5_share_pct": conc,
        "big_money_industry_n": big_n,
        "big_money_concept_n": big_con,
        "top_in_names": [str(r.get("name") or "") for r in ind_in[:5]],
        "top_out_names": [str(r.get("name") or "") for r in ind_out[:5]],
    }


def _cross_period_compare(packed: dict[str, Any]) -> dict[str, Any]:
    """Sticky leaders across East Money day / 5d / 10d."""
    def names(pid: str) -> list[str]:
        rows = ((packed.get(pid) or {}).get("industry_in") or [])[:5]
        return [str(r.get("name") or "") for r in rows if r.get("name")]

    day_in, d5, d10 = names("em_day"), names("em_5d"), names("em_10d")
    sticky_5 = sorted(set(day_in) & set(d5))
    sticky_10 = sorted(set(day_in) & set(d10))
    sticky_all = sorted(set(day_in) & set(d5) & set(d10))
    return {
        "day_top5": day_in,
        "week_top5": d5,
        "month_top5": d10,
        "sticky_day_week": sticky_5,
        "sticky_day_month": sticky_10,
        "sticky_all": sticky_all,
        "note": (
            f"东财 日&5日同榜 {len(sticky_5)} · 日&10日同榜 {len(sticky_10)} · "
            f"三榜同在 {len(sticky_all)}"
        ),
    }


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        main = row.get("main_net")
        if main is None:
            continue
        try:
            main_f = float(main)
        except (TypeError, ValueError):
            continue
        super_net = _f(row.get("super_net"))
        large_net = _f(row.get("large_net"))
        main_pct = _f(row.get("main_pct"))
        item = {
            "bk": row.get("bk") or "",
            "name": row.get("name") or "",
            "kind": row.get("kind") or "",
            "period": row.get("period") or "",
            "pct": _round(row.get("pct"), 2),
            "main_net": round(main_f, 0),
            "main_yi": round(main_f / 1e8, 2),
            "main_pct": _round(main_pct, 2),
            "super_net": round(super_net, 0) if super_net is not None else None,
            "super_yi": round(super_net / 1e8, 2) if super_net is not None else None,
            "large_net": round(large_net, 0) if large_net is not None else None,
            "large_yi": round(large_net / 1e8, 2) if large_net is not None else None,
            "leader_name": row.get("leader_name") or "",
            "leader_code": row.get("leader_code") or "",
            "big_money": _is_big_money(main_f, super_net),
            "days": row.get("days"),
        }
        cleaned.append(item)
    cleaned.sort(key=lambda x: float(x.get("main_net") or 0.0), reverse=True)
    return cleaned


def _is_big_money(main: float, super_net: float | None) -> bool:
    """Flag boards where super-large orders dominate the main-force print."""
    if super_net is None:
        return abs(main) >= 3e8
    if abs(main) < 1e8:
        return False
    if abs(super_net) >= 2e8:
        return True
    if abs(main) > 0 and abs(super_net) / abs(main) >= 0.55:
        return True
    return False


def _spotlight(
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pick a short list of big-money led boards across industry + concept."""
    pool = [r for r in (industry[:20] + concept[:20]) if r.get("big_money")]
    pool.sort(key=lambda x: float(x.get("super_yi") or x.get("main_yi") or 0), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in pool:
        key = str(row.get("bk") or row.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= 8:
            break
    return out


def _sum_main(rows: list[dict[str, Any]]) -> float:
    return round(sum(float(r.get("main_yi") or 0) for r in rows), 2)


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _round(v: Any, n: int) -> float | None:
    f = _f(v)
    return round(f, n) if f is not None else None
