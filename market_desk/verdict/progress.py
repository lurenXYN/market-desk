"""Buy-progress, ready confirmations, probes, fly window, and daily trends."""

from __future__ import annotations

from typing import Any
from market_desk.config import ETF_THIN_AMOUNT, STOCK_WEAK_VS_ETF_PCT
from market_desk.filters import normalize_code
from market_desk.mainline import etf_spec_for_name, etf_spec_soft_fallback
from market_desk.trend import classify_daily_trend, trend_score_adj

from market_desk.verdict.common import (
    _demote_buy_to_wait,
    _join_hint,
    _scale_item_qty,
    hard_confirm_fails,
    is_soft_confirm_flag,
    probe_blocking_fails,
)


def _is_near_buy_price(
    last: Any,
    buy: Any,
    *,
    chase: Any = None,
    stop: Any = None,
    etf: bool = False,
) -> bool:
    """Return True when last trades near the suggested buy level."""
    if last is None or buy is None:
        return False
    try:
        last_f = float(last)
        buy_f = float(buy)
    except (TypeError, ValueError):
        return False
    if last_f <= 0 or buy_f <= 0:
        return False
    if chase is not None:
        try:
            if last_f >= float(chase):
                return False
        except (TypeError, ValueError):
            pass
    from market_desk.config import (
        ETF_NEAR_ENTRY_DOWN,
        ETF_NEAR_ENTRY_UP,
        STOCK_NEAR_ENTRY_DOWN,
        STOCK_NEAR_ENTRY_UP,
    )

    # Soft band around suggested buy counts as「回踩到位」.
    up = ETF_NEAR_ENTRY_UP if etf else STOCK_NEAR_ENTRY_UP
    down = ETF_NEAR_ENTRY_DOWN if etf else STOCK_NEAR_ENTRY_DOWN
    rel = (last_f - buy_f) / buy_f
    if rel > up:
        return False
    if rel < -down:
        if stop is not None:
            try:
                if last_f <= float(stop):
                    return False
            except (TypeError, ValueError):
                return False
        if rel < (-0.018 if etf else -0.025):
            return False
    return True


def mark_pullback_entries(
    recommend: dict[str, Any] | None,
    *,
    observe_only: bool = False,
    require_minute: bool = True,
    block_arm: bool = False,
) -> dict[str, Any]:
    """Flag cards whose last price sits near suggested buy; optionally arm ready.

    observe_only keeps side-branch cards as watch-only even when price tags the wait.
    When ``require_minute`` is True, near-entry arms only after minute.ok is True
    (missing / pending minute does not arm). ``block_arm`` forces watch-only
    (open mute / auction).
    """
    rec = dict(recommend or {})
    items: list[dict[str, Any]] = []
    hit_ready = False
    for raw in rec.get("items") or []:
        item = dict(raw)
        etf = (item.get("kind") or "stock") == "etf"
        near = _is_near_buy_price(
            item.get("last"),
            item.get("buy_price"),
            chase=item.get("chase_price"),
            stop=item.get("stop_price"),
            etf=etf,
        )
        item["near_entry"] = near
        if near:
            try:
                last_f = float(item["last"])
                buy_f = float(item["buy_price"])
                item["near_entry_pct"] = round((last_f - buy_f) / buy_f * 100.0, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                item["near_entry_pct"] = None
        else:
            item["near_entry_pct"] = None

        minute_ok = (item.get("minute") or {}).get("ok")
        minute_pass = (not require_minute) or (minute_ok is True)
        can_arm = (
            near
            and not observe_only
            and not block_arm
            and not item.get("trend_down")
            and not item.get("block_ready")
            and minute_pass
        )
        # Soft confirms (sideways / thin minute) do not block arming.
        hard_fails = hard_confirm_fails(item.get("confirm_fail") or [])
        if hard_fails:
            can_arm = False
        if can_arm:
            was_ready = bool(item.get("ready"))
            item["ready"] = True
            hit_ready = True
            kind_label = "ETF" if etf else "个股"
            if not was_ready:
                item["role_label"] = f"{kind_label} 回踩到位"
                item["reason"] = _join_hint(
                    str(item.get("reason") or ""),
                    "现价贴近建议买，回踩到位可买",
                )
            elif "回踩到位" not in str(item.get("role_label") or ""):
                # Keep primary/alt labels; UI badge carries the near-entry cue.
                pass
        hit_ready = hit_ready or bool(item.get("ready"))
        items.append(item)

    rec["items"] = items
    if hit_ready and not observe_only:
        rec["buy"] = True
        if any(x.get("near_entry") and x.get("ready") for x in items):
            title = str(rec.get("title") or "")
            if (not title) or ("盯回踩" in title) or ("先不追" in title) or ("暂不" in title):
                rec["title"] = "回踩到位，可买"
            rec["size_note"] = _join_hint(
                str(rec.get("size_note") or ""),
                "现价已到建议买附近",
            )
            # Prefer a clearer headline when wait cards just armed.
            if "回踩到位" not in str(rec.get("text") or ""):
                primary = next((x for x in items if x.get("near_entry") and x.get("ready")), None)
                if primary and primary.get("name"):
                    rec["text"] = (
                        f"回踩到位 · {primary.get('name')} {primary.get('code') or ''} "
                        f"现价贴近建议买 {primary.get('buy_price')}"
                    ).strip()
    return rec


def build_item_buy_progress(item: dict[str, Any] | None) -> dict[str, Any]:
    """Build a short checklist of what still blocks a full live buy."""
    it = item or {}
    steps: list[dict[str, Any]] = []
    missing: list[str] = []
    soft_notes: list[str] = []

    near = bool(it.get("near_entry"))
    steps.append(
        {
            "key": "price",
            "label": "价带",
            "ok": near,
            "detail": "已触建议价" if near else "未到位",
        }
    )
    if not near:
        missing.append("价带")

    if it.get("trend_pending"):
        steps.append(
            {
                "key": "trend",
                "label": "日线",
                "ok": None,
                "soft": True,
                "detail": str(it.get("trend") or "暂未取到"),
            }
        )
        soft_notes.append("日线未取到")
    elif it.get("trend_ok"):
        steps.append(
            {
                "key": "trend",
                "label": "日线",
                "ok": True,
                "detail": "上升",
            }
        )
    elif it.get("trend_down"):
        steps.append(
            {
                "key": "trend",
                "label": "日线",
                "ok": False,
                "detail": "下降",
            }
        )
        missing.append("日线上升")
    elif it.get("trend"):
        steps.append(
            {
                "key": "trend",
                "label": "日线",
                "ok": None,
                "soft": True,
                "detail": "震荡·软",
            }
        )
        soft_notes.append("日线震荡")
    else:
        steps.append(
            {
                "key": "trend",
                "label": "日线",
                "ok": None,
                "detail": "未标注",
            }
        )

    hard = hard_confirm_fails(it.get("confirm_fail") or [])
    soft_flags = [
        str(x).strip()
        for x in (it.get("confirm_soft") or [])
        if str(x).strip()
    ]
    for flag in it.get("confirm_fail") or []:
        text = str(flag or "").strip()
        if text and is_soft_confirm_flag(text) and text not in soft_flags:
            soft_flags.append(text)

    off_fail = next((f for f in hard if "离日高" in f), None)
    off_ok: bool | None = None
    off_detail = "待验"
    try:
        last = it.get("last")
        high = it.get("high")
        if last is not None and high not in (None, 0) and float(high) > 0:
            from market_desk.config import ETF_OFF_HIGH_MIN, STOCK_OFF_HIGH_MIN

            kind = str(it.get("kind") or "stock")
            dist = (float(high) - float(last)) / float(high) * 100.0
            need = float(ETF_OFF_HIGH_MIN if kind == "etf" else STOCK_OFF_HIGH_MIN)
            try:
                from market_desk.adapt import resolve_gate_mult

                need *= max(0.75, min(1.25, float(resolve_gate_mult("off_high", 1.0))))
            except Exception:
                pass
            if dist + 1e-9 < need:
                off_ok = False
                off_detail = f"仅{dist:.2f}%（需≥{need:.2f}%）"
            else:
                off_ok = True
                off_detail = f"已离{dist:.2f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        off_ok = None
        off_detail = "无日高"
    if off_fail:
        off_ok = False
        off_detail = str(off_fail)
    if off_ok is False:
        steps.append(
            {
                "key": "off_high",
                "label": "离日高",
                "ok": False,
                "detail": off_detail,
            }
        )
        if "离日高" not in missing:
            missing.append("离日高")
    else:
        steps.append(
            {
                "key": "off_high",
                "label": "离日高",
                "ok": off_ok,
                "detail": off_detail,
            }
        )

    minute = it.get("minute") or {}
    minute_ok = minute.get("ok")
    if minute_ok is True:
        steps.append(
            {
                "key": "minute",
                "label": "分时",
                "ok": True,
                "detail": str(minute.get("label") or "已过"),
            }
        )
    elif minute_ok is False:
        steps.append(
            {
                "key": "minute",
                "label": "分时",
                "ok": False,
                "detail": str(minute.get("label") or "未过"),
            }
        )
        missing.append("分时")
    else:
        steps.append(
            {
                "key": "minute",
                "label": "分时",
                "ok": None,
                "soft": True,
                "detail": str(minute.get("label") or "未验·软"),
            }
        )
        missing.append("分时")
        soft_notes.append("分时未验")

    other_hard = [f for f in hard if "离日高" not in f and not str(f).startswith("分时")]
    if other_hard:
        steps.append(
            {
                "key": "other",
                "label": "其它",
                "ok": False,
                "detail": "、".join(other_hard[:2]),
            }
        )
        for f in other_hard[:2]:
            short = f if len(f) <= 8 else f[:7] + "…"
            if short not in missing:
                missing.append(short)
    elif it.get("block_ready"):
        steps.append(
            {
                "key": "other",
                "label": "其它",
                "ok": False,
                "detail": "禁现买",
            }
        )
        missing.append("禁现买")
    else:
        steps.append(
            {
                "key": "other",
                "label": "其它",
                "ok": True if (it.get("ready") or it.get("probe_ok")) else None,
                "detail": "无硬闸",
            }
        )

    for s in soft_flags:
        if s not in soft_notes:
            soft_notes.append(s)

    passed = sum(1 for s in steps if s.get("ok") is True)
    total = len(steps)
    if it.get("ready") and not it.get("block_ready"):
        summary = "现买确认已过"
        missing = []
    elif it.get("probe_ok"):
        miss_txt = " · ".join(missing[:3]) if missing else "确认中"
        summary = f"可小仓试探 · 还差{miss_txt}"
    elif missing:
        summary = "还差：" + " · ".join(missing[:3])
    else:
        summary = "盯回踩价"
    return {
        "steps": steps,
        "passed": passed,
        "total": total,
        "missing": missing[:4],
        "soft_notes": soft_notes[:4],
        "summary": summary,
    }


def attach_buy_progress(recommend: dict[str, Any] | None) -> dict[str, Any]:
    """Attach ``buy_progress`` checklist onto each recommend card."""
    rec = dict(recommend or {})
    items: list[dict[str, Any]] = []
    for raw in rec.get("items") or []:
        item = dict(raw)
        item["buy_progress"] = build_item_buy_progress(item)
        items.append(item)
    rec["items"] = items
    return rec


def apply_mainline_probe(
    recommend: dict[str, Any] | None,
    *,
    block_arm: bool = False,
) -> dict[str, Any]:
    """Mark near-entry mainline cards as soft probe when full ready is not armed.

    Does not upgrade hero action. Shrinks suggested size by ``PROBE_SIZE_MULT``.
    Hard confirm fails / block_ready / trend_down still forbid probe, except
    tip/shallow minute fails which demote ready but still allow half-size probe.
    """
    from market_desk.config import PROBE_SIZE_MULT

    rec = dict(recommend or {})
    items: list[dict[str, Any]] = []
    any_probe = False
    for raw in rec.get("items") or []:
        item = dict(raw)
        item["probe_ok"] = False
        if (
            block_arm
            or item.get("block_ready")
            or item.get("ready")
            or item.get("trend_down")
            or not item.get("near_entry")
        ):
            items.append(item)
            continue
        hard = hard_confirm_fails(item.get("confirm_fail") or [])
        blocking = probe_blocking_fails(item.get("confirm_fail") or [])
        if blocking:
            items.append(item)
            continue
        tip_only = bool(hard) and not blocking
        fake_tip = bool(item.get("fake_pullback_tip")) or tip_only
        # Soft path: price tagged, probe-blocking gates clear, full ready not armed.
        item["probe_ok"] = True
        any_probe = True
        kind = item.get("kind") or "stock"
        if fake_tip:
            item["role_label"] = "ETF·可试探" if kind == "etf" else "主线·可试探"
            probe_tip = "回踩到位但分时仍贴尖，只试探不升可买"
        else:
            item["role_label"] = "ETF·可试探" if kind == "etf" else "主线·可试探"
            probe_tip = "靠近建议价，可小仓试探（不升顶栏可买入）"
        _scale_item_qty(item, float(PROBE_SIZE_MULT), "主线可试探·半仓")
        item["probe_size_mult"] = round(float(PROBE_SIZE_MULT), 3)
        item["reason"] = _join_hint(str(item.get("reason") or ""), probe_tip)
        items.append(item)
    rec["items"] = items
    if any_probe:
        rec["probe"] = True
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "主线可试探·建议半仓，不改顶栏可买入",
        )
    return rec


def apply_band_ready_relax(
    recommend: dict[str, Any] | None,
    *,
    block_arm: bool = False,
) -> dict[str, Any]:
    """Promote near-entry cards to soft ready when style is ``band``.

    Matches the habit of buying inside the band before chase: tip/shallow minute
    fails no longer block ready; size stays at probe half-lot. Hard demote locks
    (``block_ready`` / ``block_arm`` / trend_down / non-tip hard fails) still win.
    """
    from market_desk.config import PROBE_SIZE_MULT
    from market_desk.numbers import num
    from market_desk.settings import setting

    style = str(setting("ready_style", "band") or "band").strip().lower()
    rec = dict(recommend or {})
    if style != "band":
        return rec
    items: list[dict[str, Any]] = []
    any_relaxed = False
    for raw in rec.get("items") or []:
        item = dict(raw)
        if (
            block_arm
            or item.get("block_ready")
            or item.get("ready")
            or item.get("trend_down")
            or not item.get("near_entry")
        ):
            items.append(item)
            continue
        last = num(item.get("last") or item.get("price_now"))
        chase = num(item.get("chase_price") or (item.get("prices") or {}).get("chase"))
        if last is not None and chase is not None and chase > 0 and last >= chase:
            items.append(item)
            continue
        blocking = probe_blocking_fails(item.get("confirm_fail") or [])
        if blocking:
            items.append(item)
            continue
        item["ready"] = True
        item["ready_relaxed"] = True
        item["probe_ok"] = False
        any_relaxed = True
        kind = item.get("kind") or "stock"
        item["role_label"] = "ETF·价带可买" if kind == "etf" else "主线·价带可买"
        tip = "价带放松：已近建议价且未到不追，半仓 ready（分时贴尖不挡）"
        if abs(float(item.get("probe_size_mult") or 0) - float(PROBE_SIZE_MULT)) > 0.01:
            _scale_item_qty(item, float(PROBE_SIZE_MULT), "价带放松·半仓")
            item["probe_size_mult"] = round(float(PROBE_SIZE_MULT), 3)
        item["reason"] = _join_hint(str(item.get("reason") or ""), tip)
        items.append(item)
    rec["items"] = items
    if any_relaxed:
        rec["ready_relaxed"] = True
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "价带放松·未到不追可半仓买入",
        )
    return rec


def tag_fly_window_items(recommend: dict[str, Any] | None) -> dict[str, Any]:
    """Mark half-size windows that may run away without a full pullback.

    Soft UX only: sets ``fly_warn`` / ``fly_note`` for toast and card badges.
    Does not change ready / probe gates.
    """
    from market_desk.config import TIP_PROBE_ALLOW_FAILS

    rec = dict(recommend or {})
    items: list[dict[str, Any]] = []
    any_fly = False
    tip_allow = set(TIP_PROBE_ALLOW_FAILS or ())
    for raw in rec.get("items") or []:
        item = dict(raw)
        item.pop("fly_warn", None)
        item.pop("fly_note", None)
        half = bool(item.get("ready_relaxed") or item.get("probe_ok"))
        near = bool(item.get("near_entry"))
        if not half or not near:
            items.append(item)
            continue
        fails = [str(x) for x in (item.get("confirm_fail") or []) if x]
        minute = item.get("minute") if isinstance(item.get("minute"), dict) else {}
        tippy = bool(minute.get("at_tip")) or any(
            f in tip_allow or "贴" in f or "抬高" in f or "过浅" in f for f in fails
        )
        # Band-relaxed half-ready is itself a "may fly if you wait" window.
        if tippy or bool(item.get("ready_relaxed")):
            item["fly_warn"] = True
            item["fly_note"] = "半仓试探窗口，再等可能飞"
            any_fly = True
        items.append(item)
    rec["items"] = items
    if any_fly:
        rec["fly_warn"] = True
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "浅踩将飞：半仓窗口优先，勿死等完美回踩",
        )
    return rec


def finalize_recommend_buy_ux(
    recommend: dict[str, Any] | None,
    *,
    block_arm: bool = False,
    allow_probe: bool = True,
) -> dict[str, Any]:
    """Apply mainline probe, optional band-ready relax, then buy-progress checklists."""
    rec = dict(recommend or {})
    if allow_probe:
        rec = apply_mainline_probe(rec, block_arm=block_arm)
        rec = apply_band_ready_relax(rec, block_arm=block_arm)
        rec = tag_fly_window_items(rec)
    else:
        for raw in rec.get("items") or []:
            raw["probe_ok"] = False
    rec = attach_buy_progress(rec)
    return _annotate_sole_etf_chase(rec)


def _annotate_sole_etf_chase(recommend: dict[str, Any] | None) -> dict[str, Any]:
    """Soft tip when the only priced vehicle is a blocked ETF (cannot arm buy)."""
    rec = dict(recommend or {})
    items = list(rec.get("items") or [])
    if not items:
        return rec
    stocks = [x for x in items if x.get("kind") == "stock"]
    etfs = [x for x in items if x.get("kind") == "etf"]
    if stocks or len(etfs) != 1:
        return rec
    etf = etfs[0]
    if not (etf.get("block_ready") or not etf.get("ready")):
        return rec
    tip = "主线仅 ETF 且禁现买·可盯回踩/分时，勿死等可买满"
    rec["sole_etf_blocked"] = True
    rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), tip)
    etf = dict(etf)
    etf["reason"] = _join_hint(str(etf.get("reason") or ""), tip)
    rec["items"] = [etf]
    return rec


def _apply_ready_confirmations(
    recommend: dict[str, Any],
    vehicle: dict[str, Any],
    metrics: dict[str, Any] | None,
    *,
    main: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Downgrade ready cards that fail relative-strength or day-high checks."""
    from market_desk.config import (
        THIN_CONFIRM_ZT_MAX,
        THIN_CROSS_BOARD_MIN,
        THIN_CROSS_THEME_MIN,
    )

    rec = dict(recommend or {})
    items = [dict(x) for x in (rec.get("items") or [])]
    if not items:
        return rec
    m = metrics or {}
    v_pct = vehicle.get("pct")
    main = main or {}
    thin_confirm = (
        str(main.get("status") or "") in ("确认中", "观察")
        and int(main.get("zt_n") or 0) <= int(THIN_CONFIRM_ZT_MAX)
    )
    try:
        from market_desk.adapt import resolve_gate_mult
        from market_desk.review import cached_buy_gate_bias

        cached_buy_gate_bias()  # warms the day cache resolve_gate_mult reads
        off_mult = float(resolve_gate_mult("off_high", 1.0))
        thin_mult = float(resolve_gate_mult("thin", 1.0))
    except Exception:
        off_mult = 1.0
        thin_mult = 1.0
    changed = False
    for item in items:
        if not item.get("ready"):
            continue
        flags: list[str] = []
        last = item.get("last")
        high = item.get("high")
        kind = item.get("kind") or "stock"
        try:
            if last is not None and high not in (None, 0) and float(high) > 0:
                from market_desk.config import ETF_OFF_HIGH_MIN, STOCK_OFF_HIGH_MIN

                dist = (float(high) - float(last)) / float(high) * 100.0
                need = ETF_OFF_HIGH_MIN if kind == "etf" else STOCK_OFF_HIGH_MIN
                need = float(need) * max(0.75, min(1.25, off_mult))
                if dist < need:
                    flags.append("离日高过近")
        except (TypeError, ValueError):
            pass
        if kind == "stock" and v_pct is not None and item.get("pct") is not None:
            try:
                if float(item["pct"]) < float(v_pct) - float(STOCK_WEAK_VS_ETF_PCT):
                    flags.append("弱于主线ETF")
            except (TypeError, ValueError):
                pass
        if kind == "stock" and m.get("weak_index"):
            flags.append("指数弱禁个股现买")
        if kind == "stock":
            try:
                from market_desk.config import MAINLINE_FLOW_OUT_YI

                yi = main.get("main_yi")
                if yi is not None and float(yi) <= float(MAINLINE_FLOW_OUT_YI):
                    flags.append("板块主力流出")
            except (TypeError, ValueError):
                pass
        if kind == "stock" and thin_confirm:
            thin_m = max(0.75, min(1.25, thin_mult))
            cross_need = max(1, int(round(float(THIN_CROSS_BOARD_MIN) * thin_m)))
            theme_need = max(1, int(round(float(THIN_CROSS_THEME_MIN) * thin_m)))
            # Loosen: lower required cross counts when false-kills high.
            if thin_m < 1.0:
                cross_need = max(1, int(THIN_CROSS_BOARD_MIN) - 1)
                theme_need = max(0, int(THIN_CROSS_THEME_MIN) - 1)
            elif thin_m > 1.0:
                cross_need = max(cross_need, int(THIN_CROSS_BOARD_MIN) + 1)
                theme_need = max(theme_need, int(THIN_CROSS_THEME_MIN) + 1)
            cross_n = int(item.get("cross_n") or 0)
            theme_n = int(item.get("cross_theme_n") or 0)
            ok_cross = cross_n >= cross_need
            ok_theme = theme_n >= theme_need
            if not (ok_cross or ok_theme):
                flags.append("薄确认缺跨板块共振")
        if kind == "etf":
            # Thin ETF amount while green = fake strength (amount in 元).
            amt = item.get("amount")
            if amt is None:
                amt = vehicle.get("amount") if normalize_code(vehicle.get("code")) == normalize_code(item.get("code")) else None
            try:
                pct_i = float(item.get("pct")) if item.get("pct") is not None else None
            except (TypeError, ValueError):
                pct_i = None
            if amt is not None and pct_i is not None and pct_i >= 0.8 and float(amt) < float(ETF_THIN_AMOUNT):
                flags.append("ETF量能偏弱")
        if not flags:
            continue
        changed = True
        item["ready"] = False
        _demote_buy_to_wait(item)
        item["role_label"] = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"
        item["reason"] = (str(item.get("reason") or "") + "；确认失败：" + "、".join(flags)).strip("；")
        item["confirm_fail"] = flags
    if not changed:
        return rec
    rec["items"] = items
    if rec.get("buy") and not any(x.get("ready") for x in items):
        rec["buy"] = False
        rec["title"] = "盯回踩价，先不追"
        rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), "确认条件未过，先等回踩")
    return rec


def attach_board_etf_trends(
    boards: list[dict[str, Any]] | None,
    trends_by_code: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach carrier-ETF daily-trend adj onto hot boards for mainline scoring.

    Exact map uses full ±adj; soft map uses a smaller coeff (never unlocks ready).
    Unclear / missing trends leave etf_trend_adj at 0 (no nudge).
    """
    from market_desk.config import (
        MAINLINE_ETF_TREND_DOWN,
        MAINLINE_ETF_TREND_UP,
        MAINLINE_SOFT_ETF_TREND_DOWN,
        MAINLINE_SOFT_ETF_TREND_UP,
    )

    trends = trends_by_code or {}
    out: list[dict[str, Any]] = []
    for raw in boards or []:
        board = dict(raw)
        name = str(board.get("name") or "")
        spec = etf_spec_for_name(name)
        soft = None if spec else etf_spec_soft_fallback(name)
        adj = 0.0
        label = None
        code = None
        soft_map = False
        if spec:
            code = normalize_code(spec[1])
            trend = trends.get(code) or {}
            adj = trend_score_adj(
                trend,
                up_bonus=MAINLINE_ETF_TREND_UP,
                down_penalty=MAINLINE_ETF_TREND_DOWN,
            )
            if trend.get("quality") == "ok" and (trend.get("up") or trend.get("down")):
                label = trend.get("label")
        elif soft:
            soft_map = True
            code = normalize_code(soft[1])
            trend = trends.get(code) or {}
            adj = trend_score_adj(
                trend,
                up_bonus=MAINLINE_SOFT_ETF_TREND_UP,
                down_penalty=MAINLINE_SOFT_ETF_TREND_DOWN,
            )
            if trend.get("quality") == "ok" and (trend.get("up") or trend.get("down")):
                label = trend.get("label")
        board["etf_trend_code"] = code
        board["etf_trend"] = label
        board["etf_trend_soft"] = soft_map
        board["etf_trend_adj"] = round(adj, 1)
        out.append(board)
    return out


def apply_stock_daily_trends(
    recommend: dict[str, Any] | None,
    closes_by_code: dict[str, list[float]],
    fetch_ok_by_code: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Attach daily-trend labels, nudge scores, and soft/hard ready gates.

    Stocks: clear up +bonus; clear down hard-gates ready; sideways soft score+size.
    ETF: clear up +bonus; clear down gates ready; sideways score-only (no gate).
    Missing/thin kline → ``trend_pending`` visible, no ready gate / score nudge.
    """
    from market_desk.config import (
        STOCK_TREND_DOWN_PENALTY,
        STOCK_TREND_SIDEWAYS_PENALTY,
        STOCK_TREND_UP_BONUS,
    )

    def _apply_trend_size_soft(item: dict[str, Any]) -> None:
        """Soft-scale suggested lot size after daily trend classify."""
        from market_desk.config import (
            TREND_SIZE_DOWN_MULT,
            TREND_SIZE_SIDEWAYS_MULT,
            TREND_SIZE_UP_MULT,
        )

        mult = None
        tip = ""
        if item.get("trend_ok"):
            mult = float(TREND_SIZE_UP_MULT)
            tip = "日线上升略加仓"
        elif item.get("trend_down"):
            mult = float(TREND_SIZE_DOWN_MULT)
            tip = "日线下降略减仓"
        elif (
            item.get("kind") == "stock"
            and not item.get("trend_pending")
            and not item.get("trend_ok")
            and not item.get("trend_down")
            and item.get("trend")
        ):
            mult = float(TREND_SIZE_SIDEWAYS_MULT)
            tip = "日线震荡软缩仓"
        if mult is None or abs(mult - 1.0) < 0.01:
            return
        try:
            qty = int(item.get("qty") or 0)
        except (TypeError, ValueError):
            return
        if qty <= 0:
            return
        new_qty = max(100, int(round(qty * mult / 100.0) * 100))
        item["trend_size_mult"] = round(mult, 3)
        if new_qty == qty:
            return
        item["qty"] = new_qty
        plan = item.get("risk_plan")
        if isinstance(plan, dict):
            plan = dict(plan)
            plan["qty"] = new_qty
            plan["note"] = _join_hint(str(plan.get("note") or ""), tip)
            item["risk_plan"] = plan

    rec = dict(recommend or {})
    items = list(rec.get("items") or [])
    if not items:
        return rec
    fetch_ok_by_code = fetch_ok_by_code or {}

    out_items: list[dict[str, Any]] = []
    gated = False
    for item in items:
        kind = item.get("kind") or "stock"
        if kind not in ("stock", "etf"):
            out_items.append(item)
            continue
        code = normalize_code(item.get("code"))
        if code not in closes_by_code:
            fetch_ok = False
            closes: list[float] = []
        else:
            fetch_ok = fetch_ok_by_code.get(code, True)
            closes = list(closes_by_code.get(code) or [])
        trend = classify_daily_trend(closes, fetch_ok=fetch_ok)
        marked = dict(item)
        quality = trend.get("quality")
        marked["trend_quality"] = quality
        marked["trend_manual"] = None
        marked["ma5"] = trend.get("ma5")
        marked["ma10"] = trend.get("ma10")
        marked["ma20"] = trend.get("ma20")
        base_score = float(marked.get("score") or 0.0)
        trend_adj = 0.0
        role_wait = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"

        def _gate_ready(flag: str) -> None:
            nonlocal gated
            if not marked.get("ready"):
                return
            gated = True
            marked["ready"] = False
            _demote_buy_to_wait(marked)
            marked["role_label"] = role_wait
            fails = list(marked.get("confirm_fail") or [])
            if flag not in fails:
                fails.append(flag)
            marked["confirm_fail"] = fails

        if trend.get("up"):
            marked["trend"] = "上升趋势"
            marked["trend_ok"] = True
            marked["trend_down"] = False
            marked["trend_unknown"] = False
            marked["trend_pending"] = False
            marked["trend_warn"] = None
            trend_adj = float(STOCK_TREND_UP_BONUS)
            marked["reason"] = (
                f"日线上升趋势（MA5 {trend.get('ma5')} / MA20 {trend.get('ma20')}）；"
                + str(marked.get("reason") or "")
            )
        elif quality in ("fetch_fail", "thin"):
            # Missing/thin: surface pending; do not gate or score-nudge.
            marked["trend"] = "行情未取到" if quality == "fetch_fail" else "样本不足"
            marked["trend_ok"] = False
            marked["trend_down"] = False
            marked["trend_unknown"] = True
            marked["trend_pending"] = True
            marked["trend_warn"] = "日线未确认"
            marked["ma5"] = None
            marked["ma10"] = None
            marked["ma20"] = None
            marked["reason"] = _join_hint(
                str(marked.get("reason") or ""),
                "日线暂未取到，不据此否决",
            )
        elif trend.get("down"):
            marked["trend"] = "下降趋势"
            marked["trend_ok"] = False
            marked["trend_down"] = True
            marked["trend_unknown"] = False
            marked["trend_pending"] = False
            marked["trend_warn"] = None
            trend_adj = -float(STOCK_TREND_DOWN_PENALTY)
            _gate_ready("日线下降")
            marked["reason"] = _join_hint(
                str(marked.get("reason") or ""),
                "日线下降趋势，减分",
            )
        else:
            # Sideways: stocks soft score+size (no ready kill); ETF no up-bonus.
            marked["trend"] = "震荡/非上升"
            marked["trend_ok"] = False
            marked["trend_down"] = False
            marked["trend_unknown"] = True
            marked["trend_pending"] = False
            marked["trend_warn"] = "不是上升趋势"
            if kind == "stock":
                trend_adj = -float(STOCK_TREND_SIDEWAYS_PENALTY)
                soft = list(marked.get("confirm_soft") or [])
                if "日线非上升" not in soft:
                    soft.append("日线非上升")
                marked["confirm_soft"] = soft
                marked["reason"] = _join_hint(
                    str(marked.get("reason") or ""),
                    "日线震荡，软减分缩仓（不关现买）",
                )
            else:
                marked["reason"] = _join_hint(
                    str(marked.get("reason") or ""),
                    "日线震荡，ETF 不加上升分",
                )

        marked["trend_adj"] = round(trend_adj, 1)
        marked["score"] = round(base_score + trend_adj, 1)
        _apply_trend_size_soft(marked)
        out_items.append(marked)

    # Keep ETF first; re-rank stocks by score after trend nudge.
    etf_items = [x for x in out_items if x.get("kind") == "etf"]
    stock_items = [x for x in out_items if x.get("kind") != "etf"]
    stock_items.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    for idx, item in enumerate(stock_items):
        has_etf = bool(etf_items)
        item["role"] = "alt" if has_etf or idx > 0 else "primary"
        if item.get("ready"):
            item["role_label"] = (
                "个股 主推" if item.get("role") == "primary" else "个股 备选"
            )
        elif not item.get("role_label"):
            item["role_label"] = "个股盯回踩"

    merged = etf_items + stock_items
    rec["items"] = merged
    primary = next(
        (x for x in merged if x.get("kind") == "etf"),
        None,
    ) or next(
        (x for x in merged if x.get("ready")),
        None,
    ) or (merged[0] if merged else None)
    if primary and primary.get("kind") == "etf":
        primary["role"] = "primary"
    rec["primary"] = primary
    if primary:
        rec["code"] = primary.get("code")
        rec["name"] = primary.get("name")
        rec["price"] = primary.get("buy_price") or primary.get("last")
    if gated and rec.get("buy") and not any(x.get("ready") for x in merged):
        rec["buy"] = False
        rec["title"] = "盯回踩价，先不追"
        rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), "日线未确认上升，先等回踩")
    return rec
