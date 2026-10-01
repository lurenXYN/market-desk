"""Health strip, today's events, contagion, and cycle view builders."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta as _timedelta
from typing import Any
from market_desk.settings import setting

try:
    from zoneinfo import ZoneInfo

    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - Windows without tzdata
    from datetime import timezone as _tz

    CN_TZ = _tz(_timedelta(hours=8))


def _build_health(
    now: datetime,
    errors: list[str],
    updated_at: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a visible data-health strip for the dashboard banner."""
    from market_desk.engine.core import engine

    failed = list(errors or [])
    live = bool(payload.get("live"))
    trading = bool(payload.get("trading_day"))
    tips: list[str] = []
    score = 100
    if not trading:
        tips.append("今日非交易日（周末或节假日），不刷盘中行情")
        score -= 5
    if failed:
        score -= min(60, 15 * len(failed))
        tips.append("行情源异常：" + "、".join(failed[:6]))
    if live and not (payload.get("etfs") or []):
        score -= 15
        tips.append("ETF 报价为空")
    quotes_n = 0
    try:
        from market_desk.eastmoney import cached_main_quotes

        quotes_n = len(cached_main_quotes() or [])
    except Exception:
        quotes_n = 0
    # Thin cache is informational only when soft breadth already recovered;
    # do not alone mark the banner as 「数据降级」.
    thin_quotes = bool(live and quotes_n and quotes_n < 800)
    if thin_quotes:
        tips.append(f"主板行情缓存偏薄（{quotes_n}只），未用作涨跌家数")
        score -= 3
    if live and not (payload.get("hot_boards") or []):
        score -= 15
        tips.append("热点板块为空")
    if live and not (payload.get("indices") or []):
        score -= 10
        tips.append("指数为空")
    hot_n = len(payload.get("hot_boards") or [])
    if live and trading and 0 < hot_n < 3:
        score -= 8
        tips.append(f"热点板偏少（{hot_n}），主线样本偏薄")
    ew = payload.get("elliott") if isinstance(payload.get("elliott"), dict) else {}
    if ew.get("ok") is False and "日线" in str(ew.get("note") or ""):
        score -= 8
        tips.append("上证日线不足，大盘波浪暂不可用")
    fail_rates: dict[str, float] = {}
    sources: list[dict[str, Any]] = []
    degraded = False
    try:
        stats = getattr(engine, "_source_stats", {}) or {}
        rate_bits: list[str] = []
        for label, bucket in sorted(stats.items(), key=lambda kv: str(kv[0])):
            ok_n = int(bucket.get("ok") or 0)
            fail_n = int(bucket.get("fail") or 0)
            to_n = int(bucket.get("timeout") or 0)
            total = ok_n + fail_n
            rate = round(100.0 * fail_n / total, 1) if total else None
            sources.append(
                {
                    "name": str(label),
                    "ok": ok_n,
                    "fail": fail_n,
                    "timeout": to_n,
                    "total": total,
                    "fail_rate": rate,
                }
            )
            if total < 4:
                continue
            fail_rates[str(label)] = float(rate or 0)
            if float(rate or 0) >= 30:
                degraded = True
                bit = f"{label}失败率{rate}%"
                if to_n:
                    bit += f"（超时{to_n}）"
                rate_bits.append(bit)
        if rate_bits:
            score -= min(20, 5 * len(rate_bits))
            tips.append("源失败率：" + "、".join(rate_bits[:4]))
    except Exception:
        sources = []
    stale_sec = None
    if updated_at:
        try:
            ts = datetime.strptime(updated_at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
            stale_sec = max(0, int((now - ts).total_seconds()))
            if live and stale_sec > int(setting("refresh_seconds", 20)) * 3:
                score -= 20
                tips.append(f"快照偏旧约 {stale_sec}s")
                degraded = True
        except Exception:
            stale_sec = None
    if live and failed:
        degraded = True
    # Breadth: warn when zt alive but ups/downs still empty (failed clist day).
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    hist0 = (payload.get("history") or [None])[0] if isinstance(payload.get("history"), list) else None
    day_row = hist0 if isinstance(hist0, dict) and str(hist0.get("trade_date") or "")[:10] == str(
        payload.get("trade_date") or ""
    )[:10] else {}
    ups = int(metrics.get("ups") or day_row.get("ups") or 0)
    downs = int(metrics.get("downs") or day_row.get("downs") or 0)
    zt_n = int(metrics.get("zt") or day_row.get("zt") or 0)
    span = ups + downs
    if live and trading and zt_n >= 5 and span == 0:
        tips.append("今日广度未采到（涨停池有数但涨跌家数为 0）")
        score -= 15
        degraded = True
    elif live and trading and 0 < span < 800:
        tips.append(f"今日广度样本过薄（{span}），已忽略勿当真")
        score -= 10
        degraded = True
    elif bool(day_row.get("breadth_degraded")) and span < 800:
        tips.append("今日广度曾降级（clist 失败，已尽量保留前值）")
        score -= 8
        degraded = True
    clist: dict[str, Any] = {}
    try:
        from market_desk.eastmoney import clist_runtime_status

        clist = clist_runtime_status()
        host = str(clist.get("host") or "")
        short = host.split(".", 1)[0] if host else "—"
        if clist.get("backoff"):
            # Brief host cooldown is normal after disconnects; tip only.
            tips.append(f"clist 冷却中 · {short} · 剩 {clist.get('backoff_sec')}s")
            score -= 2
        else:
            tips.append(f"clist · {short}")
        if clist.get("quotes_source") == "tencent":
            tips.append(
                f"主板行情走腾讯备用源（东财列表被拦，{int(clist.get('quotes_pause_sec') or 0)}s 后重试东财）"
            )
        sw = clist.get("boards_switches") if isinstance(clist.get("boards_switches"), dict) else {}
        flips = int(sw.get("switches") or 0)
        if clist.get("boards_source") == "sina":
            if sw.get("locked"):
                tips.append("板块走新浪备用源（盘中锁定，午休/收盘后再探测东财）")
            else:
                tips.append(
                    f"板块走新浪备用源（东财板块被拦，{int(clist.get('boards_pause_sec') or 0)}s 后重试东财）"
                )
        al = clist.get("boards_alias") if isinstance(clist.get("boards_alias"), dict) else {}
        if clist.get("boards_source") == "sina" and al.get("total"):
            mapped = int(al.get("exact") or 0) + int(al.get("alias") or 0) + int(al.get("approx") or 0)
            tail = f"（近似 {al.get('approx')}）" if al.get("approx") else ""
            tips.append(f"新浪板块映射东财 {mapped}/{al.get('total')}{tail}")
        if flips >= 3:
            tips.append(f"板块数据源今日切换 {flips} 次（主线/提醒已减震）")
            score -= 3
        elif flips:
            tips.append(f"板块数据源今日切换 {flips} 次")
        blocked = [b for b in (clist.get("em_blocked") or []) if isinstance(b, dict)]
        if blocked:
            bits = [f"{b.get('family')}（{b.get('since') or '—'}起）" for b in blocked[:3]]
            tips.append("东财接口被拦：" + "、".join(bits) + "（多为按 IP 限流，已自动走备用源）")
        if clist.get("minute_source") == "tencent":
            tips.append(
                f"分时走腾讯备用源（东财分时拉空，{int(clist.get('minute_pause_sec') or 0)}s 后重试东财）"
            )
    except Exception:
        clist = {}
    nr = payload.get("news_radar") if isinstance(payload.get("news_radar"), dict) else {}
    if nr.get("enabled"):
        st = str(nr.get("status") or "")
        if st == "offline":
            tips.append("新闻雷达离线/超时")
            score -= 3
        elif st == "empty":
            tips.append("新闻雷达空数据")
        elif st in ("online", "stale") and nr.get("status_label"):
            tips.append("新闻雷达·" + str(nr.get("status_label")))
    integrity = payload.get("db_integrity")
    if not isinstance(integrity, dict):
        integrity = (getattr(engine, "snapshot", {}) or {}).get("db_integrity")
    if isinstance(integrity, dict) and not integrity.get("ok") and not integrity.get("skipped"):
        tips.append("SQLite 库损坏·请停服重拷 desk.db（勿留旧 wal）")
        score -= 40
        degraded = True
    score = max(0, min(100, score))
    level = "ok" if score >= 80 else ("warn" if score >= 50 else "bad")
    if degraded and level == "ok":
        level = "warn"
    return {
        "score": score,
        "level": level,
        "trading_day": trading,
        "failed_sources": failed,
        "fail_rates": fail_rates,
        "sources": sources,
        "degraded": degraded,
        "stale_seconds": stale_sec,
        "tips": tips[:10],
        "clist": clist or None,
        "news_radar": {
            "enabled": bool(nr.get("enabled")),
            "status": nr.get("status"),
            "status_label": nr.get("status_label"),
        }
        if nr
        else None,
    }


def _event_line(phase: str, metrics: dict[str, Any], hot: list[dict[str, Any]]) -> str:
    top = hot[0]["name"] if hot else ""
    if not top:
        top = str(metrics.get("mainline_hint") or "").strip() or "—"
    leader = metrics.get("leader") or {}
    return f"{phase} · 最高{metrics['height']}板 · 热点{top} · {leader.get('name') or '—'}"


def _contagion(ice: list[dict[str, Any]]) -> dict[str, Any]:
    """Flag clustered limit-downs inside the coldest industries."""
    hits = [b for b in ice if (b.get("dt_n") or 0) >= 2 or b.get("status") == "传染预警"]
    if not hits:
        cold = ice[0] if ice else None
        return {
            "on": False,
            "text": (
                f"冰点观察 {cold.get('name')} {(cold.get('pct') or 0):+.2f}% · 暂无板块级传染"
                if cold
                else "暂无板块级跌停传染"
            ),
        }
    board = hits[0]
    return {
        "on": True,
        "name": board.get("name"),
        "dt_n": board.get("dt_n") or 0,
        "text": f"{board.get('name')} {board.get('dt_n') or 0} 家靠近跌停 · 传染预警 · 非买点",
    }


def _today_events(
    phase: str,
    metrics: dict[str, Any],
    hot: list[dict[str, Any]],
    zt: list[dict[str, Any]],
    ice: list[dict[str, Any]] | None = None,
    contagion: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    leader = metrics.get("leader") or {}
    top = hot[0] if hot else {}
    cold = (ice or [None])[0] if ice else None
    items = [
        {"tone": "phase", "text": f"主板相位 {phase}，温度 {metrics.get('zt', 0)}涨停 / {metrics.get('dt', 0)}跌停"},
        {
            "tone": "lead",
            "text": f"高度 {metrics['height']} 板 · {leader.get('name') or '—'} {leader.get('code') or ''}",
        },
        {
            "tone": "hot",
            "text": f"实时热点 {top.get('name') or '—'} {top.get('pct', 0):+.2f}%"
            if top
            else "热点待刷新",
        },
        {
            "tone": "ice",
            "text": f"实时冰点 {cold.get('name')} {(cold.get('pct') or 0):+.2f}% · {cold.get('status')}"
            if cold
            else "冰点板块待刷新",
        },
        {
            "tone": "promo",
            "text": (
                f"昨停溢价 {metrics['premium']}% · 晋级 {metrics['promotion']}% "
                f"(1→2 {metrics.get('promo_1_2') if metrics.get('promo_1_2') is not None else '—'}% / "
                f"2→3 {metrics.get('promo_2_3') if metrics.get('promo_2_3') is not None else '—'}%) · "
                f"梯队 {metrics.get('ladder_fill') or 0}%"
                f"{'·断' if metrics.get('ladder_gap') else ''} · 炸板 {metrics['zb_rate']}%"
            ),
        },
    ]
    if contagion and contagion.get("on"):
        items.insert(0, {"tone": "warn", "text": contagion["text"]})
    if zt:
        banned = [x for x in zt if int(x.get("boards") or 0) >= 3]
        if banned:
            items.append(
                {
                    "tone": "warn",
                    "text": f"高位连板 {banned[0]['name']} {banned[0]['boards']}板 · 纪律禁追",
                }
            )
    return items


def _cycle_view(history: list[dict[str, Any]], today: str) -> dict[str, Any]:
    """Build a climax-relative timeline; attach daily snapshots so nodes are clickable."""
    ordered = list(reversed(history))
    by_date = {r.get("trade_date"): r for r in history if r.get("trade_date")}
    last_climax = None
    for row in ordered:
        if row.get("phase") == "高潮":
            last_climax = row.get("trade_date")
    dates = [r.get("trade_date") for r in ordered]
    offset = 0
    anchor_idx = -1
    if last_climax and last_climax in dates and today in dates:
        offset = dates.index(today) - dates.index(last_climax)
        anchor_idx = dates.index(last_climax)
    elif today in dates:
        offset = 0
        anchor_idx = dates.index(today)
    elif dates:
        offset = 0
        anchor_idx = len(dates) - 1

    nodes = []
    for i in range(-1, 10):
        trade_date = None
        row = None
        if anchor_idx >= 0:
            idx = anchor_idx + i
            if 0 <= idx < len(dates):
                trade_date = dates[idx]
                row = by_date.get(trade_date)
        is_now = i == offset
        detail = None
        if row:
            detail = {
                "trade_date": trade_date,
                "phase": row.get("phase"),
                "temperature": row.get("temperature"),
                "zt": row.get("zt"),
                "dt": row.get("dt"),
                "zb_rate": row.get("zb_rate"),
                "height": row.get("height"),
                "promotion": row.get("promotion"),
                "promo_1_2": row.get("promo_1_2"),
                "promo_2_3": row.get("promo_2_3"),
                "premium": row.get("premium"),
                "ladder_fill": row.get("ladder_fill"),
                "ladder_gap": row.get("ladder_gap"),
                "event": row.get("event"),
                "ups": row.get("ups"),
                "downs": row.get("downs"),
                "amount_yi": row.get("amount_yi"),
            }
        nodes.append(
            {
                "i": i,
                "current": is_now,
                "label": "今" if is_now else f"D{i:+d}",
                "trade_date": trade_date,
                "has_data": detail is not None,
                "detail": detail,
            }
        )
    return {
        "offset": offset,
        "last_climax": last_climax,
        "nodes": nodes,
        "note": (
            f"距上次高潮 {offset} 日 · 可点击日子查看当日摘要"
            if last_climax
            else "本地尚无高潮样本，先攒日级数据 · 可点有数据的日子"
        ),
    }
