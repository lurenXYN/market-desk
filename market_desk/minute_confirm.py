"""Intraday minute-structure checks for recommendation ready flags."""

from __future__ import annotations

from typing import Any


def _minute_gate_mult() -> float:
    """Return buy-gate multiplier for minute thresholds (loosen <1, tighten >1)."""
    try:
        from market_desk.adapt import resolve_gate_mult

        return float(resolve_gate_mult("minute", 1.0))
    except Exception:
        try:
            from market_desk.review import cached_buy_gate_bias

            bias = cached_buy_gate_bias() or {}
            gates = bias.get("gates") or {}
            minute = gates.get("minute") or {}
            if minute.get("mult") is not None:
                return max(0.75, min(1.25, float(minute["mult"])))
            return max(0.75, min(1.25, float(bias.get("mult") or 1.0)))
        except Exception:
            return 1.0


def evaluate_minute_structure(minutes: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Judge whether the minute chart supports a ready buy.

    Prefer fewer false chase buys:
    - Enough samples.
    - Not sitting on the recent minute tip / grinding highs.
    - Price back on/above the short minute MA after some pullback.
    - When volume is available: recent bars should cool vs the prior window.

    Review false-kills loosen tip/shallow thresholds; true-kills tighten them.
    """
    rows = list(minutes or [])
    prices: list[float] = []
    volumes: list[float] = []
    for row in rows:
        try:
            px = float(row.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if px <= 0:
            continue
        prices.append(px)
        try:
            vol = float(row.get("volume"))
        except (TypeError, ValueError, AttributeError):
            vol = 0.0
        volumes.append(max(0.0, vol))

    from market_desk.config import MINUTE_SAMPLE_MIN

    sample_min = int(MINUTE_SAMPLE_MIN)
    if len(prices) < sample_min:
        return {
            "ok": None,
            "label": "分时样本不足",
            "ma": None,
            "ma_source": None,
            "pullback_pct": None,
            "at_tip": False,
            "vol_ratio": None,
            "vol_ok": None,
        }

    last = prices[-1]
    # Prefer East Money official avg (VWAP-like) when present; else short MA.
    avgs: list[float] = []
    for row in rows:
        try:
            avg_px = float(row.get("avg"))
        except (TypeError, ValueError, AttributeError):
            continue
        if avg_px > 0:
            avgs.append(avg_px)
    if len(avgs) >= 15:
        ma = avgs[-1]
        ma_source = "avg"
    else:
        window = prices[-20:]
        ma = sum(window) / len(window)
        ma_source = "ma20"
    look_n = 40 if len(prices) >= 40 else len(prices)
    look = prices[-look_n:]
    hi = max(look)
    lo = min(look)
    from market_desk.config import (
        MINUTE_GRIND_PULLBACK,
        MINUTE_SHALLOW,
        MINUTE_TIP_THR_NARROW,
        MINUTE_TIP_THR_WIDE,
        MINUTE_VOL_PULLBACK,
    )

    gate_mult = _minute_gate_mult()
    pullback = (hi - last) / hi * 100.0 if hi > 0 else 0.0
    span = (hi - lo) / hi * 100.0 if hi > 0 else 0.0
    tip_thr = (MINUTE_TIP_THR_NARROW if span < 1.0 else MINUTE_TIP_THR_WIDE) * gate_mult
    shallow_thr = float(MINUTE_SHALLOW) * gate_mult
    grind_thr = float(MINUTE_GRIND_PULLBACK) * gate_mult
    vol_pb = float(MINUTE_VOL_PULLBACK) * gate_mult
    # Loosen → higher vol cool ratio allowed; tighten → stricter cool-down.
    vol_cool = 0.95 / gate_mult if gate_mult > 0 else 0.95

    at_tip = pullback < tip_thr
    prior = look[:-5] if len(look) > 8 else look[:-2]
    prior_hi = max(prior) if prior else hi
    grinding_high = bool(last >= prior_hi * 0.999 and pullback < grind_thr)
    above_ma = last >= ma * 0.999
    shallow = pullback < shallow_thr

    vol_ratio: float | None = None
    vol_ok: bool | None = None
    vol_vals = [v for v in volumes[-look_n:] if v > 0]
    if len(vol_vals) >= 15:
        recent = volumes[-5:]
        base = volumes[-20:-5]
        recent_pos = [v for v in recent if v > 0]
        base_pos = [v for v in base if v > 0]
        if recent_pos and base_pos:
            r_avg = sum(recent_pos) / len(recent_pos)
            b_avg = sum(base_pos) / len(base_pos)
            if b_avg > 0:
                vol_ratio = round(r_avg / b_avg, 2)
                vol_ok = vol_ratio <= vol_cool

    fails: list[str] = []
    if at_tip:
        fails.append("分时贴近近期高点")
    if grinding_high:
        fails.append("分时仍在抬高点")
    if not above_ma:
        fails.append("分时仍在均价下方" if ma_source == "avg" else "分时仍在均线下方")
    if shallow and not at_tip:
        fails.append("分时回撤过浅")
    if vol_ok is False and (at_tip or grinding_high or shallow or pullback < vol_pb):
        fails.append("分时回踩量能未缩")
    elif vol_ok is False and above_ma and not at_tip and not grinding_high:
        fails.append("分时放量未冷却")

    ok = len(fails) == 0
    if ok:
        label = "分时回踩缩量站稳" if vol_ok is True else "分时回踩站稳"
    else:
        label = fails[0]

    return {
        "ok": ok,
        "label": label,
        "ma": round(ma, 3),
        "ma_source": ma_source,
        "pullback_pct": round(pullback, 2),
        "at_tip": at_tip,
        "vol_ratio": vol_ratio,
        "vol_ok": vol_ok,
        "fails": fails,
        "gate_mult": round(gate_mult, 3),
    }


def _soft_scale_qty(item: dict[str, Any], mult: float, tip: str) -> None:
    """Soft-shrink suggested lot size without clearing ready."""
    try:
        qty = int(item.get("qty") or 0)
    except (TypeError, ValueError):
        return
    if qty <= 0 or abs(mult - 1.0) < 0.01:
        return
    new_qty = max(100, int(round(qty * mult / 100.0) * 100))
    item["qty"] = new_qty
    item["minute_size_mult"] = round(mult, 3)
    plan = item.get("risk_plan")
    if isinstance(plan, dict):
        plan = dict(plan)
        plan["qty"] = new_qty
        note = str(plan.get("note") or "").strip()
        tip = (tip or "").strip()
        if tip and tip not in note:
            plan["note"] = f"{note}；{tip}" if note else tip
        item["risk_plan"] = plan


def _is_tip_structure_fail(flag: str, verdict: dict[str, Any] | None = None) -> bool:
    """Return True when the minute fail is tip / grind / shallow (probe-allowed)."""
    from market_desk.config import TIP_PROBE_ALLOW_FAILS

    text = str(flag or "").strip()
    if text in TIP_PROBE_ALLOW_FAILS:
        return True
    v = verdict or {}
    if v.get("at_tip"):
        return True
    for raw in v.get("fails") or []:
        if str(raw or "").strip() in TIP_PROBE_ALLOW_FAILS:
            return True
    return False


def apply_minute_confirmations(
    recommend: dict[str, Any] | None,
    minutes_by_code: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Apply minute-structure checks: soft on thin samples, hard on structure fail."""
    rec = dict(recommend or {})
    items = [dict(x) for x in (rec.get("items") or [])]
    if not items:
        return rec
    minutes_by_code = minutes_by_code or {}
    changed = False
    soft_pending = False
    for item in items:
        code = str(item.get("code") or "").zfill(6)
        if not code or code not in minutes_by_code:
            continue
        verdict = evaluate_minute_structure(minutes_by_code.get(code))
        item["minute"] = verdict
        # Always surface pending / fail labels for progress UX.
        if verdict.get("ok") is None:
            item["minute_pending"] = True
            soft = list(item.get("confirm_soft") or [])
            flag = str(verdict.get("label") or "分时样本不足")
            if flag not in soft:
                soft.append(flag)
            item["confirm_soft"] = soft
            soft_pending = True
            if item.get("ready"):
                from market_desk.config import MINUTE_PENDING_SIZE_MULT

                _soft_scale_qty(
                    item,
                    float(MINUTE_PENDING_SIZE_MULT),
                    "分时样本不足·软缩仓",
                )
                item["reason"] = (
                    str(item.get("reason") or "") + f"；软提示：{flag}"
                ).strip("；")
            continue
        if not item.get("ready"):
            item["minute_pending"] = False
            continue
        if verdict.get("ok") is False:
            changed = True
            item["minute_pending"] = False
            item["ready"] = False
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
            kind = item.get("kind") or "stock"
            item["role_label"] = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"
            flag = str(verdict.get("label") or "分时未确认")
            fails = list(item.get("confirm_fail") or [])
            if flag not in fails:
                fails.append(flag)
            item["confirm_fail"] = fails
            # Near-entry + tip/grind fail: keep hard ready blocked, allow probe path.
            tip_related = _is_tip_structure_fail(flag, verdict)
            if tip_related and item.get("near_entry"):
                item["fake_pullback_tip"] = True
                item["role_label"] = (
                    "ETF·贴尖试探" if kind == "etf" else "主线·贴尖试探"
                )
            item["reason"] = (str(item.get("reason") or "") + f"；确认失败：{flag}").strip("；")
        else:
            item["minute_pending"] = False
            item.pop("fake_pullback_tip", None)
    if soft_pending:
        note = str(rec.get("size_note") or "")
        extra = "分时样本不足·软缩仓（不关现买）"
        if extra not in note:
            rec["size_note"] = f"{note}；{extra}" if note else extra
    if not changed:
        rec["items"] = items
        return rec
    rec["items"] = items
    if rec.get("buy") and not any(x.get("ready") for x in items):
        rec["buy"] = False
        rec["title"] = "盯回踩价，先不追"
        note = str(rec.get("size_note") or "")
        extra = "分时未确认，先等回踩缩量"
        rec["size_note"] = note if extra in note else (f"{note}；{extra}" if note else extra)
    return rec


def confirm_sell_take(minutes: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Judge whether minute structure supports soft take-profit / half trim.

    Allow when price has faded from the tip or slipped below the minute average.
    Sample-thin series return ``ok=None`` (soft pass — do not hard-block).
    """
    from market_desk.config import MINUTE_SAMPLE_MIN, MINUTE_SHALLOW

    rows = list(minutes or [])
    prices: list[float] = []
    for row in rows:
        try:
            px = float(row.get("price"))
        except (TypeError, ValueError, AttributeError):
            continue
        if px > 0:
            prices.append(px)
    if len(prices) < int(MINUTE_SAMPLE_MIN):
        return {
            "ok": None,
            "soft": True,
            "label": "分时样本不足",
            "at_tip": False,
            "below_ma": False,
            "pullback_pct": None,
        }

    base = evaluate_minute_structure(rows)
    last = prices[-1]
    ma = base.get("ma")
    below_ma = False
    try:
        if ma is not None and float(ma) > 0:
            below_ma = last < float(ma) * 0.999
    except (TypeError, ValueError):
        below_ma = False
    pb = base.get("pullback_pct")
    tip_fade = False
    try:
        if pb is not None and float(pb) >= float(MINUTE_SHALLOW):
            tip_fade = True
    except (TypeError, ValueError):
        tip_fade = False
    at_tip = bool(base.get("at_tip"))
    if below_ma or tip_fade:
        return {
            "ok": True,
            "soft": False,
            "label": "分时转弱可兑现" if below_ma else "分时已离尖",
            "at_tip": at_tip,
            "below_ma": below_ma,
            "pullback_pct": pb,
        }
    if at_tip or bool(base.get("fails")):
        return {
            "ok": False,
            "soft": False,
            "label": str(base.get("label") or "分时仍在冲高"),
            "at_tip": at_tip,
            "below_ma": below_ma,
            "pullback_pct": pb,
        }
    return {
        "ok": True,
        "soft": False,
        "label": "分时未贴尖",
        "at_tip": at_tip,
        "below_ma": below_ma,
        "pullback_pct": pb,
    }


def apply_sell_minute_gates(
    advice: dict[str, Any] | None,
    minutes_by_code: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    """Gate soft half / light take sells on minute fade; stop & deep clear skip.

    Items must set ``minute_gate=True`` from ``_sell_item``. Thin samples pass
    with ``minute_pending`` only. When ``all_items`` is present, gate that pool
    and keep ``items`` as the top slice.
    """
    from market_desk.config import SELL_MINUTE_GATE_ENABLED
    from market_desk.verdict import _enrich_sell_next_action

    out = dict(advice or {})
    pool_key = "all_items" if out.get("all_items") is not None else "items"
    items = [dict(x) for x in (out.get(pool_key) or [])]
    if not items or not SELL_MINUTE_GATE_ENABLED:
        if pool_key == "all_items":
            out["all_items"] = items
            out["items"] = items[:4]
        else:
            out["items"] = items
        return out
    minutes_by_code = minutes_by_code or {}
    changed = False
    for item in items:
        if not item.get("ready") or not item.get("minute_gate"):
            continue
        code = str(item.get("code") or "").zfill(6)
        series = minutes_by_code.get(code)
        if series is None and code not in minutes_by_code:
            # Cache miss: soft pending, do not hard-block.
            item["minute_sell"] = {
                "ok": None,
                "soft": True,
                "label": "分时未拉·软放行",
            }
            item["minute_pending"] = True
            continue
        verdict = confirm_sell_take(series)
        item["minute_sell"] = verdict
        if verdict.get("ok") is None:
            item["minute_pending"] = True
            continue
        item["minute_pending"] = False
        if verdict.get("ok") is False:
            changed = True
            item["ready"] = False
            item["exit_mode"] = "hold"
            item["sell_pct"] = 0
            item["sell_qty"] = 0
            role = str(item.get("role_label") or "卖点")
            if "待分时确认" not in role:
                item["role_label"] = f"{role}·待分时确认"
            why = str(verdict.get("label") or "分时仍强")
            reason = str(item.get("reason") or "")
            tip = f"待分时确认：{why}"
            if tip not in reason:
                item["reason"] = f"{tip}；{reason}" if reason else tip
            try:
                _enrich_sell_next_action(
                    item,
                    hold_peak=float(item.get("hold_peak") or 0),
                    last_sell_price=item.get("half_anchor_price"),
                    digits=3 if item.get("kind") == "etf" else 2,
                )
            except Exception:
                item["next_action"] = "watch"
                item["next_action_zh"] = "继续观察"
                item["next_action_note"] = tip
    if changed:
        sell_now = [x for x in items if x.get("ready")]
        out["sell"] = bool(sell_now)
        if sell_now:
            primary = sell_now[0]
            out["primary"] = primary
            mode = primary.get("exit_mode") or "half"
            mode_zh = {"clear": "清仓", "half": "先减一半"}.get(str(mode), "减仓")
            out["title"] = "建议卖出"
            out["text"] = (
                f"{primary.get('name') or primary.get('code')} · "
                f"{primary.get('role_label') or mode_zh}"
            )
        else:
            out["title"] = "仓位观察"
            out["primary"] = items[0] if items else None
    if pool_key == "all_items":
        out["all_items"] = items
        out["items"] = items[:4]
    else:
        out["items"] = items
    return out
