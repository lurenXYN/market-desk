"""A-share seasonality / calendar windows (observe-only hints)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from market_desk.calendar import holiday_set, is_trading_day


def _holidays() -> set[str]:
    return holiday_set()


def build_seasonality(
    day: date | datetime | str | None = None,
    *,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """List active calendar windows and a short playbook for each.

    ``history`` (newest-first daily digests) is optional soft context only.
    """
    today = _as_date(day) or date.today()
    windows = _detect_windows(today)
    # Always include a baseline weekday note.
    weekday_note = _weekday_note(today)
    catalog = _full_catalog(today, windows)
    active = [w for w in catalog if w.get("active")]
    hist_note = _history_hint(history)
    return {
        "ok": True,
        "standalone": True,
        "title": "季节性 / 日历",
        "trade_date": today.isoformat(),
        "trading_day": is_trading_day(today),
        "weekday_note": weekday_note,
        "active": active,
        "windows": catalog,
        "history_note": hist_note,
        "note": (
            f"{today.isoformat()} · 激活 {len(active)} 个日历窗"
            + (f" · {weekday_note}" if weekday_note else "")
        ),
        "disclaimer": "日历效应是统计偏好不是定律；仅观察，不改作战台买卖结论。",
    }


def _as_date(day: date | datetime | str | None) -> date | None:
    if day is None:
        return None
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    try:
        return date.fromisoformat(str(day)[:10])
    except ValueError:
        return None


def _detect_windows(today: date) -> set[str]:
    active: set[str] = set()
    if _is_month_start(today):
        active.add("month_start")
    if _is_month_end(today):
        active.add("month_end")
    if _is_holiday_eve(today):
        active.add("holiday_eve")
    if _is_holiday_reopen(today):
        active.add("holiday_reopen")
    q = _report_season(today)
    if q:
        active.add(q)
    if today.month in (1, 2) and _near_spring_festival(today):
        active.add("spring_festival")
    if today.weekday() == 0 and is_trading_day(today):
        active.add("monday")
    if today.weekday() == 4 and is_trading_day(today):
        active.add("friday")
    return active


def _full_catalog(today: date, active_ids: set[str]) -> list[dict[str, Any]]:
    """Return all calendar windows with active flags and A-share playbooks."""
    defs: list[dict[str, Any]] = [
        {
            "id": "month_start",
            "title": "月初窗口",
            "when": "每月前 1–3 个交易日",
            "path": "机构调仓/新资金试错常见；情绪修复月更易出主线萌芽，仍忌盲目追高。",
            "watch": "若仍处恐慌相位，月初效应会被情绪压过。",
        },
        {
            "id": "month_end",
            "title": "月末窗口",
            "when": "每月最后 2–3 个交易日",
            "path": "部分资金择时调仓、题材兑现；高潮月注意尾盘波动，宜降隔夜预期。",
            "watch": "强主线月末也可能继续，但波动放大。",
        },
        {
            "id": "holiday_eve",
            "title": "长假前夕",
            "when": "法定长假前最后 1–2 个交易日",
            "path": "风险偏好常下降，仓位倾向收敛；偏防守或只留核心，少开隔夜博弈仓。",
            "watch": "政策预期强时例外，但流动性仍可能变薄。",
        },
        {
            "id": "holiday_reopen",
            "title": "长假归来",
            "when": "长假后首个交易日附近",
            "path": "缺口/情绪重定价；先看竞价与昨停溢价，再定主线，避免开盘前十分钟冲动。",
            "watch": "外盘/政策隔夜冲击可覆盖季节性。",
        },
        {
            "id": "spring_festival",
            "title": "春节前后",
            "when": "春节假期前后约两周",
            "path": "节前偏谨慎、节后常有「开门红」叙事；以情绪相位为准，叙事只作背景。",
            "watch": "与长假前夕/归来重叠时，合并看仓位纪律。",
        },
        {
            "id": "report_q1",
            "title": "年报/一季报季",
            "when": "约 1 月中下旬–4 月底",
            "path": "业绩披露扰动大；回避纯题材、关注真业绩主线，突发雷区多。",
            "watch": "高位题材更怕业绩证伪。",
        },
        {
            "id": "report_mid",
            "title": "中报窗口",
            "when": "约 7–8 月",
            "path": "中报验证期；扩散行情更挑基本面，纯情绪高标容错变差。",
            "watch": "同期若情绪高潮，波动与回撤都会放大。",
        },
        {
            "id": "report_q3",
            "title": "三季报窗口",
            "when": "约 10 月",
            "path": "三季报+国庆前后流动性变化；先稳仓位再谈进攻。",
            "watch": "与国庆长假窗口叠加时，优先假日纪律。",
        },
        {
            "id": "monday",
            "title": "周一",
            "when": "每周一（交易日）",
            "path": "隔周末信息定价；竞价与主线身份更重要，开盘半小时宜观察。",
            "watch": "强趋势市周一也可能高开接力。",
        },
        {
            "id": "friday",
            "title": "周五",
            "when": "每周五（交易日）",
            "path": "部分资金降隔夜；高潮拥挤时更应控制周五新开仓。",
            "watch": "强主线确认中也可能无视周五效应。",
        },
    ]
    out: list[dict[str, Any]] = []
    for row in defs:
        item = dict(row)
        item["active"] = row["id"] in active_ids
        item["today"] = today.isoformat()
        out.append(item)
    return out


def _weekday_note(today: date) -> str:
    names = "一二三四五六日"
    w = today.weekday()
    base = f"周{names[w]}"
    if not is_trading_day(today):
        return f"{base} · 休市"
    if w == 0:
        return f"{base} · 关注竞价定价"
    if w == 4:
        return f"{base} · 注意隔夜仓位"
    return base


def _is_month_start(today: date) -> bool:
    if not is_trading_day(today):
        return False
    # Count trading days from month start inclusive.
    n = 0
    d = date(today.year, today.month, 1)
    while d <= today:
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return 1 <= n <= 3


def _is_month_end(today: date) -> bool:
    if not is_trading_day(today):
        return False
    # Look ahead within this month for remaining trading days.
    left = 0
    d = today
    # Find last calendar day of month.
    if today.month == 12:
        end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(today.year, today.month + 1, 1) - timedelta(days=1)
    while d <= end:
        if is_trading_day(d):
            left += 1
        d += timedelta(days=1)
    return 1 <= left <= 3


def _is_holiday_eve(today: date) -> bool:
    if not is_trading_day(today):
        return False
    # Next 1–3 calendar days contain a holiday stretch start.
    for i in range(1, 4):
        nxt = today + timedelta(days=i)
        if nxt.isoformat() in _holidays():
            # today should be last trading day before that holiday day
            cursor = today + timedelta(days=1)
            while cursor < nxt:
                if is_trading_day(cursor):
                    return False
                cursor += timedelta(days=1)
            return True
    return False


def _is_holiday_reopen(today: date) -> bool:
    if not is_trading_day(today):
        return False
    # Yesterday (or recent) was holiday and today is first trading day back.
    for i in range(1, 8):
        prev = today - timedelta(days=i)
        if prev.isoformat() in _holidays():
            # Ensure no trading day between prev-holiday and today.
            cursor = prev + timedelta(days=1)
            while cursor < today:
                if is_trading_day(cursor):
                    return False
                cursor += timedelta(days=1)
            return True
        if is_trading_day(prev):
            return False
    return False


def _near_spring_festival(today: date) -> bool:
    """True when within ~10 calendar days of a Spring Festival holiday cluster."""
    # Heuristic: Feb holiday stretches in our table, or Jan 20–Feb 28 window near CNY holidays.
    hol = sorted(h for h in _holidays() if h.startswith(f"{today.year}-01") or h.startswith(f"{today.year}-02"))
    for h in hol:
        try:
            hd = date.fromisoformat(h)
        except ValueError:
            continue
        if abs((hd - today).days) <= 10:
            return True
    return False


def _report_season(today: date) -> str | None:
    m, d = today.month, today.day
    if m == 1 and d >= 15:
        return "report_q1"
    if m in (2, 3, 4):
        return "report_q1"
    if m in (7, 8):
        return "report_mid"
    if m == 10:
        return "report_q3"
    return None


def _history_hint(history: list[dict[str, Any]] | None) -> str:
    rows = list(history or [])[:8]
    if len(rows) < 3:
        return ""
    phases = [str(r.get("phase") or "") for r in rows if r.get("phase")]
    if not phases:
        return ""
    climax_n = sum(1 for p in phases if p == "高潮")
    panic_n = sum(1 for p in phases if p == "恐慌")
    if climax_n >= 3:
        return "近端日级偏高潮密集，日历窗上更应防拥挤兑现"
    if panic_n >= 3:
        return "近端日级偏恐慌，月初/假后修复窗才更有意义"
    return ""
