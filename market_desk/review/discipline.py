"""Daily discipline scorecard: did today's fills follow the desk (chase, stops, rhythm)?

Per user and per trade day, from data the desk already stores:

* buys — traded buy signals (``signal_user_meta`` overlay) merged with exec-diary
  buys; diary buys without any desk card count as unplanned;
* stops — ready ``urgency == "stop"`` sell signals owned by the user; executed when
  the same-day sold quantity (diary sells, else position day-sold) covers the
  advised exit (half / clear);
* rhythm — buys while the card was not lit, the desk said 观望, the phase was 恐慌
  or the board was under an extreme-crowding block.

Score = earned points / available points × 100 over buy (40), stop (40) and
rhythm (20); a section without events is left out. Display and push only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.numbers import num
from market_desk.review.alerts import _ever_ready, chase_pct, signal_plan_price
from market_desk.review.exec_score import merge_exec_score_rows
from market_desk.review.signals import is_buy_signal

SECTION_MAX = {"buy": 40.0, "stop": 40.0, "rhythm": 20.0}
_SELL_SIDES = ("sell", "trim", "half", "clear")
_HIST_KEEP = 60


def _code(row: dict[str, Any]) -> str:
    return str(row.get("code") or "").strip().zfill(6)


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("payload") if isinstance(row.get("payload"), dict) else {}


def _diary_advice(row: dict[str, Any] | None) -> dict[str, Any]:
    adv = (row or {}).get("advice")
    return adv if isinstance(adv, dict) else {}


def _grade(score: float | None) -> tuple[str, str]:
    """Return (grade label, tone) for a 0–100 score."""
    if score is None:
        return "不评分", "low"
    if score >= 90:
        return "纪律优", "good"
    if score >= 75:
        return "良", "good"
    if score >= 60:
        return "及格", "mid"
    return "失守", "bad"


def _score_buy(row: dict[str, Any], *, warn_pct: float, hard_pct: float) -> tuple[float, str, float | None]:
    """Return (0–1 credit, label, chase %) for one planned buy fill."""
    payload = _payload(row)
    fill = num(row.get("fill_price"))
    plan = signal_plan_price(row)
    chase_px = num(row.get("chase_price") if row.get("chase_price") is not None else payload.get("chase_price"))
    pct = chase_pct(plan, fill)
    if chase_px is not None and fill is not None and fill >= chase_px:
        return 0.0, "越过不追价", pct
    if pct is None:
        return 0.5, "缺计划价", None
    if pct <= warn_pct:
        return 1.0, "价带内", pct
    if pct <= hard_pct:
        return 0.5, f"追价 {warn_pct:g}~{hard_pct:g}%", pct
    return 0.0, f"追价 >{hard_pct:g}%", pct


def _rhythm_flags(row: dict[str, Any], diary_buy: dict[str, Any] | None) -> list[str]:
    """Return rhythm violations for one buy (each costs half the rhythm credit)."""
    from market_desk.config import EVER_READY_TRACKED_SINCE

    payload = _payload(row)
    adv = _diary_advice(diary_buy)
    flags: list[str] = []
    tracked = True
    if str(row.get("exec_source") or "") == "diary":
        lit = bool((adv.get("buy") or {}).get("ready"))
    else:
        lit = bool(_ever_ready(row) or payload.get("ever_probe") or payload.get("probe_ok"))
        tracked = str(row.get("trade_date") or "")[:10] >= EVER_READY_TRACKED_SINCE
    if not lit and tracked:
        flags.append("未亮灯就买")
    if str(adv.get("action") or "") == "观望":
        flags.append("作战结论观望时买")
    phase = str(adv.get("phase") or row.get("phase") or "")
    if phase == "恐慌":
        flags.append("恐慌相位开新仓")
    if payload.get("crowd_block"):
        flags.append("极端拥挤禁开时买")
    return flags


def _sold_qty(code: str, day: str, diary: list[dict[str, Any]], positions: list[dict[str, Any]]) -> int:
    """Return shares of ``code`` sold on ``day`` (diary first, position day-sold as fallback)."""
    by_diary = sum(
        int(num(r.get("qty")) or 0)
        for r in diary
        if _code(r) == code
        and str(r.get("side") or "").lower() in _SELL_SIDES
        and str(r.get("trade_date") or "")[:10] == day
    )
    by_pos = sum(
        int(p.get("day_sold_qty") or 0)
        for p in positions
        if _code(p) == code and str(p.get("last_sell_date") or "")[:10] == day
    )
    return max(by_diary, by_pos)


def build_discipline_card(
    day_rows: list[dict[str, Any]] | None,
    diary: list[dict[str, Any]] | None,
    *,
    day: str,
    user_id: int | None,
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score one user's trade day against the desk's own plan.

    Args:
        day_rows: That day's signals already overlaid with the user's fills.
        diary: That day's exec-diary rows for the user (``advice`` decoded).
        day: Trade date ``YYYY-MM-DD``.
        user_id: Viewer id; None returns an empty card.
        positions: The user's position rows (day-sold fallback for sells).

    Returns:
        ``{ok, score, grade, tone, line, sections, items}``; ``score`` is None when
        the day had no buys and no stop events.
    """
    from market_desk.config import CHASE_WARN_PCT, DISCIPLINE_CHASE_HARD_PCT

    if user_id is None:
        return {"ok": False, "score": None, "note": "登录后按本人成交评分"}
    day = str(day or "")[:10]
    rows = [r for r in (day_rows or []) if str(r.get("trade_date") or "")[:10] == day]
    diary = [r for r in (diary or []) if str(r.get("trade_date") or "")[:10] == day]
    positions = list(positions or [])
    warn, hard = float(CHASE_WARN_PCT), float(DISCIPLINE_CHASE_HARD_PCT)
    diary_buys = {_code(r): r for r in diary if str(r.get("side") or "").lower() == "buy"}

    items: list[dict[str, Any]] = []
    buy_credit: list[float] = []
    rhythm_credit: list[float] = []
    merged = merge_exec_score_rows(rows, diary)
    bought: set[str] = set()
    for row in merged:
        if not is_buy_signal(row.get("signal_type")) or not int(row.get("traded") or 0):
            continue
        code = _code(row)
        if code in bought:
            continue
        bought.add(code)
        credit, label, pct = _score_buy(row, warn_pct=warn, hard_pct=hard)
        flags = _rhythm_flags(row, diary_buys.get(code))
        buy_credit.append(credit)
        rhythm_credit.append(max(0.0, 1.0 - 0.5 * len(flags)))
        items.append({
            "kind": "buy", "code": code, "name": row.get("name") or code,
            "plan": signal_plan_price(row), "fill": num(row.get("fill_price")),
            "chase": pct, "label": label, "credit": credit, "flags": flags,
        })
    pos_names = {_code(p): p.get("name") for p in positions if p.get("name")}
    for code, drow in diary_buys.items():
        if code in bought:
            continue
        bought.add(code)
        buy_credit.append(0.5)
        rhythm_credit.append(0.5)
        items.append({
            "kind": "buy", "code": code, "name": drow.get("name") or pos_names.get(code) or code,
            "plan": None, "fill": num(drow.get("price")), "chase": None,
            "label": "计划外买入", "credit": 0.5, "flags": ["无作战台买卡"],
        })

    stop_credit: list[float] = []
    for row in rows:
        payload = _payload(row)
        if str(row.get("signal_type") or "") != "sell" or str(payload.get("urgency") or "") != "stop":
            continue
        owner = row.get("owner_user_id") or payload.get("owner_user_id")
        if owner is None or int(owner) != int(user_id):
            continue
        code = _code(row)
        if code in diary_buys:
            continue
        held = int(num(payload.get("qty")) or 0)
        need = held * (0.5 if str(payload.get("exit_mode") or "") == "half" else 1.0)
        sold = _sold_qty(code, day, diary, positions)
        if sold > 0 and (need <= 0 or sold >= need * 0.95):
            credit, label = 1.0, "已执行"
        elif sold > 0:
            credit, label = 0.5, "部分执行"
        else:
            credit, label = 0.0, "扛单"
        signal_px = num(row.get("price"))
        live = num(row.get("live_last"))
        stop_credit.append(credit)
        items.append({
            "kind": "stop", "code": code, "name": row.get("name") or code,
            "rule": str(row.get("action") or "止损"), "need": int(need), "sold": sold,
            "signal_px": signal_px, "live": live,
            "vs_signal": round((live / signal_px - 1.0) * 100.0, 2) if live and signal_px else None,
            "label": label, "credit": credit,
        })

    sections: dict[str, dict[str, Any]] = {}
    for key, credits in (("buy", buy_credit), ("stop", stop_credit), ("rhythm", rhythm_credit)):
        if credits:
            mx = SECTION_MAX[key]
            sections[key] = {"n": len(credits), "pts": round(mx * sum(credits) / len(credits), 1), "max": mx}
    avail = sum(s["max"] for s in sections.values())
    score = round(sum(s["pts"] for s in sections.values()) / avail * 100.0, 1) if avail else None
    grade, tone = _grade(score)
    buys = [i for i in items if i["kind"] == "buy"]
    stops = [i for i in items if i["kind"] == "stop"]
    if score is None:
        line = "纪律分：今日无买入、无止损事件，不评分"
    else:
        bits = []
        if buys:
            in_band = sum(1 for i in buys if i["credit"] >= 1.0)
            bits.append(f"买 {len(buys)} 笔 价带内 {in_band}")
        if stops:
            held = sum(1 for i in stops if i["label"] == "扛单")
            bits.append(f"止损 {len(stops)} 次" + (f" 扛单 {held}" if held else " 全执行"))
        flag_n = sum(len(i.get("flags") or []) for i in buys)
        if flag_n:
            bits.append(f"节奏扣 {flag_n} 项")
        line = f"纪律分 {score:g}（{grade}）：" + "，".join(bits)
    return {
        "ok": True,
        "day": day,
        "score": score,
        "grade": grade,
        "tone": tone,
        "line": line,
        "sections": sections,
        "items": items,
        "warn_pct": warn,
        "hard_pct": hard,
    }


def record_discipline_history(user_id: int, card: dict[str, Any], *, final: bool) -> list[dict[str, Any]]:
    """Persist a final day score under ``discipline_hist:{uid}`` and return recent history.

    Args:
        user_id: Owner of the card.
        card: Output of ``build_discipline_card``.
        final: Only settled days (past dates or after the close) are written.

    Returns:
        Up to 20 most recent ``{day, score, grade}`` rows, oldest first.
    """
    from market_desk.db import load_setting, save_setting

    key = f"discipline_hist:{int(user_id)}"
    hist = load_setting(key)
    hist = dict(hist) if isinstance(hist, dict) else {}
    day = str(card.get("day") or "")[:10]
    if final and day and card.get("score") is not None:
        entry = {"score": card["score"], "grade": card.get("grade")}
        if hist.get(day) != entry:
            hist[day] = entry
            for old in sorted(hist)[:-_HIST_KEEP]:
                hist.pop(old, None)
            save_setting(key, hist)
    return [{"day": d, **hist[d]} for d in sorted(hist)[-20:]]


def is_final_day(day: str, now: datetime | None = None) -> bool:
    """Return True for past trade days, or today after 15:00 local time."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    d = str(day or "")[:10]
    return bool(d) and (d < today or (d == today and now.hour >= 15))
