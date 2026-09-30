"""Sell advice aggregation and buy/sell conflict reconciliation."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.filters import normalize_code

from market_desk.verdict.common import _demote_buy_to_wait, _is_etf_code, _join_hint
from market_desk.verdict.progress import finalize_recommend_buy_ux
from market_desk.verdict.tags import _enrich_sell_next_action
from market_desk.verdict.sell import _sell_item


def build_sell_advice(
    positions: list[dict[str, Any]],
    verdict: dict[str, Any] | None,
    phase: str,
    *,
    trade_date: str | None = None,
    trends_by_code: dict[str, dict[str, Any]] | None = None,
    similar: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build sell / hold cards for locally recorded positions."""
    verdict = verdict or {}
    day = str(trade_date or "").strip()[:10]
    trends = trends_by_code or {}
    items: list[dict[str, Any]] = []
    try:
        from market_desk.review import cached_sell_bias_bundle

        sell_bundle = cached_sell_bias_bundle()
    except Exception:
        sell_bundle = {
            "all": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
            "etf": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
            "stock": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
        }
    for row in positions or []:
        if int(row.get("qty") or 0) <= 0:
            continue
        code = normalize_code(row.get("code"))
        is_etf = _is_etf_code(code) if code else False
        try:
            from market_desk.review import resolve_sell_kind_bias

            kind_bias = resolve_sell_kind_bias(sell_bundle, etf=is_etf)
        except Exception:
            kind_bias = (sell_bundle.get("etf") if is_etf else sell_bundle.get("stock")) or {}
            if not kind_bias.get("ok"):
                kind_bias = sell_bundle.get("all") or {}
        item = _sell_item(
            row,
            verdict,
            phase,
            trade_date=day,
            trend=trends.get(code) if code else None,
            sell_bias=kind_bias,
            similar=similar,
            metrics=metrics,
        )
        if item:
            items.append(item)
    sell_bias = sell_bundle.get("all") or {}
    try:
        from market_desk.adapt import build_sell_mfe_bias

        mfe_bias = build_sell_mfe_bias()
    except Exception:
        mfe_bias = {"ok": False, "note": ""}
    sell_bias_out = {
        "hit_rate": sell_bias.get("hit_rate"),
        "n": sell_bias.get("n"),
        "widen": bool(sell_bias.get("widen")),
        "tighten": bool(sell_bias.get("tighten")),
        "note": sell_bias.get("note") or sell_bundle.get("note"),
        "etf": sell_bundle.get("etf"),
        "stock": sell_bundle.get("stock"),
        "all": sell_bias,
        "mfe": mfe_bias,
        "fly_n": sell_bias.get("fly_n"),
    }
    rank = {"stop": 0, "take": 1, "trim": 2, "hold": 3}
    items.sort(key=lambda x: (rank.get(str(x.get("urgency") or "hold"), 9), -(x.get("pnl_pct") or 0)))
    all_items = list(items)
    items = all_items[:4]
    sell_now = [x for x in items if x.get("ready")]
    open_n = sum(1 for r in (positions or []) if int(r.get("qty") or 0) > 0)
    if not open_n:
        return {
            "sell": False,
            "empty": True,
            "title": "暂无仓位",
            "text": "暂无仓位 · 买入记账后这里给出卖出建议",
            "size_note": "仓位页记账后，按浮盈、回撤、主线强弱提示卖点。当日买入受 T+1 限制，隔日才可卖。今日已平不计入卖点。",
            "items": [],
            "all_items": [],
            "sell_bias": sell_bias_out,
        }
    if sell_now:
        primary = sell_now[0]
        mode = primary.get("exit_mode") or "half"
        mode_zh = {"clear": "清仓", "half": "先减一半"}.get(str(mode), "减仓")
        text = (
            f"{primary.get('role_label')} {primary.get('name')} {primary.get('code')}  "
            f"{primary.get('sell_price')} · {mode_zh}"
        )
        size_note = (
            "止损默认清仓；站上MA20且非下降则先减一半。衰退/深回撤→清仓或先减。本地提示，不会下单。"
        )
    else:
        primary = items[0] if items else None
        t1_n = sum(1 for x in all_items if x.get("t1_locked"))
        text = (
            f"继续持有 · 盯 {primary.get('name')} 目标 {primary.get('sell_price')}"
            if primary
            else "继续持有"
        )
        size_note = "未触发卖点时，建议卖=目标价，止损按成本下方。"
        if t1_n:
            size_note = f"有 {t1_n} 只当日买入（T+1），隔日才能卖。" + size_note
    advice = {
        "sell": bool(sell_now),
        "empty": False,
        "title": "建议卖出" if sell_now else "仓位观察",
        "text": text,
        "size_note": size_note,
        "items": items,
        "all_items": all_items,
        "primary": primary if items else None,
        "sell_bias": sell_bias_out,
    }
    from market_desk.config import SELL_OPEN_WATCH_MINUTES
    from market_desk.sell_open_buffer import apply_sell_open_buffer
    from market_desk.settings import setting

    try:
        watch_m = int(setting("sell_open_watch_minutes", SELL_OPEN_WATCH_MINUTES))
    except Exception:
        watch_m = int(SELL_OPEN_WATCH_MINUTES)
    return apply_sell_open_buffer(
        advice, now=now or datetime.now(), watch_minutes=watch_m
    )


def _ready_buy_codes(verdict: dict[str, Any] | None) -> set[str]:
    """Collect codes currently marked ready on buy recommend boards."""
    out: set[str] = set()
    v = verdict or {}
    for key in ("recommend", "side_recommend", "link_recommend"):
        for raw in (v.get(key) or {}).get("items") or []:
            if not raw.get("ready"):
                continue
            code = normalize_code(raw.get("code"))
            if code:
                out.add(code)
    return out


def _desk_theme_buyable(verdict: dict[str, Any] | None) -> bool:
    """Return True when sticky mainline is actively buyable (not just watching)."""
    v = verdict or {}
    if str(v.get("action") or "") != "可买入":
        return False
    main = v.get("mainline") or {}
    status = str(main.get("status") or "")
    life = str(main.get("lifecycle") or "")
    if status == "退潮" or life == "ending":
        return False
    return True


def _demote_soft_sell_to_hold(
    item: dict[str, Any],
    *,
    why: str,
) -> dict[str, Any]:
    """Downgrade a soft half/trim sell into hold when buy-side still bullish."""
    out = dict(item)
    prev_role = str(out.get("role_label") or "")
    prev_reason = str(out.get("reason") or "")
    out["ready"] = False
    out["exit_mode"] = "hold"
    out["urgency"] = "hold"
    out["sell_pct"] = 0
    out["sell_qty"] = 0
    out["buy_conflict_hold"] = True
    out["role_label"] = "主线仍可买·继续持有"
    note = why or "买侧仍偏多，软减改为继续持有"
    out["reason"] = note + ("；" + prev_reason if prev_reason else "")
    if prev_role and prev_role not in note:
        out["conflict_from"] = prev_role
    return _enrich_sell_next_action(
        out,
        hold_peak=float(out.get("hold_peak") or 0),
        last_sell_price=out.get("half_anchor_price"),
        digits=3 if out.get("kind") == "etf" else 2,
    )


def _refresh_sell_advice_summary(advice: dict[str, Any]) -> dict[str, Any]:
    """Rebuild sell-advice header fields after item-level demotions."""
    out = dict(advice or {})
    all_items = list(out.get("all_items") or out.get("items") or [])
    rank = {"stop": 0, "take": 1, "trim": 2, "hold": 3}
    all_items.sort(
        key=lambda x: (
            rank.get(str(x.get("urgency") or "hold"), 9),
            -(x.get("pnl_pct") or 0),
        )
    )
    items = all_items[:4]
    sell_now = [x for x in items if x.get("ready")]
    out["all_items"] = all_items
    out["items"] = items
    out["sell"] = bool(sell_now)
    out["empty"] = False
    if sell_now:
        primary = sell_now[0]
        mode = primary.get("exit_mode") or "half"
        mode_zh = {"clear": "清仓", "half": "先减一半"}.get(str(mode), "减仓")
        out["title"] = "建议卖出"
        out["text"] = (
            f"{primary.get('role_label')} {primary.get('name')} {primary.get('code')}  "
            f"{primary.get('sell_price')} · {mode_zh}"
        )
        out["primary"] = primary
    else:
        primary = items[0] if items else None
        out["title"] = "仓位观察"
        out["text"] = (
            f"继续持有 · 盯 {primary.get('name')} 目标 {primary.get('sell_price')}"
            if primary
            else "继续持有"
        )
        out["primary"] = primary
    return out


def reconcile_buy_sell_conflict(
    verdict: dict[str, Any] | None,
    sell_advice: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve contradictory ready buy vs soft sell for the same account.

    Soft half/trim sells demote to hold when the same code is a ready buy, or when
    the name sits on a sell-theme while the desk is still buy-side. Hard stop /
    clear exits stay. Held codes (and remaining hard sells) demote buy ready.
    """
    v = dict(verdict or {})
    advice = dict(sell_advice or {})
    held: set[str] = set()
    for row in positions or []:
        if int(row.get("qty") or 0) <= 0:
            continue
        code = normalize_code(row.get("code"))
        if code:
            held.add(code)

    ready_buys = _ready_buy_codes(v)
    theme_buyable = _desk_theme_buyable(v)

    soft_demoted = 0
    all_items: list[dict[str, Any]] = []
    for raw in advice.get("all_items") or advice.get("items") or []:
        item = dict(raw)
        code = normalize_code(item.get("code"))
        is_soft = bool(
            item.get("ready")
            and str(item.get("exit_mode") or "") == "half"
            and str(item.get("urgency") or "") in ("trim", "take")
        )
        if is_soft and code:
            same_buy = code in ready_buys
            theme_hold = bool(item.get("on_sell_theme") and theme_buyable)
            if same_buy or theme_hold:
                why = (
                    "同码买侧仍 ready，软减改为继续持有"
                    if same_buy
                    else "作战台可买入且属卖侧主题，软减改为继续持有"
                )
                item = _demote_soft_sell_to_hold(item, why=why)
                soft_demoted += 1
        all_items.append(item)
    if soft_demoted:
        advice["all_items"] = all_items
        advice = _refresh_sell_advice_summary(advice)
        advice["buy_conflict_demoted"] = soft_demoted

    hard_sell: set[str] = set()
    for item in advice.get("all_items") or advice.get("items") or []:
        if not item.get("ready"):
            continue
        code = normalize_code(item.get("code"))
        if not code:
            continue
        mode = str(item.get("exit_mode") or "")
        urg = str(item.get("urgency") or "")
        if mode == "clear" or urg == "stop":
            hard_sell.add(code)

    buy_demoted = 0
    for key in ("recommend", "side_recommend", "link_recommend"):
        rec = dict(v.get(key) or {})
        items = list(rec.get("items") or [])
        if not items:
            continue
        changed = False
        new_items: list[dict[str, Any]] = []
        for raw in items:
            item = dict(raw)
            code = normalize_code(item.get("code"))
            if item.get("ready") and code and (code in held or code in hard_sell):
                changed = True
                buy_demoted += 1
                item["ready"] = False
                item["block_ready"] = True
                if code in hard_sell:
                    item["sell_conflict_block"] = True
                    fail = "作战台建议减仓中"
                    item["role_label"] = (
                        "减仓中·不加仓"
                        if (item.get("kind") or "stock") == "stock"
                        else "减仓中·ETF不加"
                    )
                else:
                    item["held_block"] = True
                    fail = "已持有"
                    item["role_label"] = (
                        "已持有·不加仓"
                        if (item.get("kind") or "stock") == "stock"
                        else "已持有·ETF不加"
                    )
                _demote_buy_to_wait(item)
                fails = list(item.get("confirm_fail") or [])
                if fail not in fails:
                    fails.append(fail)
                item["confirm_fail"] = fails
            new_items.append(item)
        if changed:
            rec["items"] = new_items
            still_ready = any(x.get("ready") for x in new_items)
            if not still_ready and rec.get("buy"):
                rec["buy"] = False
                if any(x.get("held_block") for x in new_items):
                    rec["title"] = "已持有 · 不加仓"
                elif any(x.get("sell_conflict_block") for x in new_items):
                    rec["title"] = "减仓中 · 不加仓"
                note = "持仓同码或硬卖点冲突，买侧降为观察"
                rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), note)
            v[key] = finalize_recommend_buy_ux(
                rec,
                allow_probe=(key == "recommend"),
            )
        elif rec:
            v[key] = rec

    if buy_demoted:
        v["buy_held_demoted"] = buy_demoted
    advice["conflict"] = {
        "soft_sell_to_hold": soft_demoted,
        "buy_demoted": buy_demoted,
    }
    return v, advice
