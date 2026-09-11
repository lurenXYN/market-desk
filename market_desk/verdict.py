"""Live mainline verdict, recommendation, and snapshot-to-snapshot deltas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.config import (
    CHINEXT_STAR_ETFS,
    ETF_BOUNCE_BUY_MIN,
    ETF_THIN_AMOUNT,
    STOCK_WEAK_VS_ETF_PCT,
)
from market_desk.filters import is_limit_up, is_main_board, is_st, normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import (
    etf_spec_for_name,
    etf_spec_soft_fallback,
    explain_mainline,
    match_mainline_etf,
    pick_mainline,
    pick_side_mainline,
    same_theme,
    theme_key,
)
from market_desk.playbook import build_playbook, suggest_risk_qty
from market_desk.session import apply_segment_bias, session_segment
from market_desk.settings import setting
from market_desk.trend import classify_daily_trend, classify_many, trend_score_adj


def build_verdict(
    now: datetime,
    phase: str,
    metrics: dict[str, Any],
    etfs: list[dict[str, Any]],
    hot: list[dict[str, Any]],
    prev: dict[str, Any] | None,
    zt: list[dict[str, Any]] | None = None,
    auction: dict[str, Any] | None = None,
    similar: dict[str, Any] | None = None,
    zb: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Announce the live mainline and a matching vehicle, without a fixed ticker."""
    prev_ml = ((prev or {}).get("verdict") or {}).get("mainline") or {}
    sticky = prev_ml.get("name")
    sticky_held: float | None = None
    sticky_since_prev = str(prev_ml.get("sticky_since") or "").strip()
    if sticky_since_prev:
        try:
            from datetime import datetime as _dt

            since_dt = _dt.strptime(sticky_since_prev[:19], "%Y-%m-%d %H:%M:%S")
            # ``now`` may be timezone-aware; compare naive wall clocks.
            now_naive = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now
            sticky_held = max(0.0, (now_naive - since_dt).total_seconds())
        except (TypeError, ValueError):
            sticky_held = None
    main = pick_mainline(
        hot,
        sticky_name=sticky,
        sticky_held_seconds=sticky_held,
    ) or {}
    board_name = main.get("name") or ""
    ml_why = explain_mainline(
        hot,
        main,
        sticky_name=sticky,
        sticky_held_seconds=sticky_held,
    )
    if board_name and sticky and board_name == sticky and sticky_since_prev:
        sticky_since = sticky_since_prev
    else:
        sticky_since = now.strftime("%Y-%m-%d %H:%M:%S")
    exact_spec = etf_spec_for_name(board_name) if board_name else None
    exact_etf = bool(exact_spec)
    soft_etf = False
    etf = match_mainline_etf(board_name, etfs) if board_name else None
    algo_notes: list[str] = []
    if board_name and not etf:
        # Soft fallback: nearest mapped industry ETF so the desk still has a vehicle path.
        soft = etf_spec_soft_fallback(board_name)
        if soft:
            _, code, name = soft
            quote = next((x for x in (etfs or []) if x.get("code") == code), None)
            etf = (
                dict(quote)
                if quote
                else {
                    "code": code,
                    "name": name,
                    "price": None,
                    "pct": None,
                    "low": None,
                    "high": None,
                }
            )
            soft_etf = True
            algo_notes.append(f"ETF软映射→{name}")
    # Exact map only; soft never counts as mapped for orphan / 可买入 pricing.
    etf_mapped = exact_etf
    vehicle = etf or {}
    price = vehicle.get("price")
    pct = vehicle.get("pct") if vehicle else main.get("pct")
    low = vehicle.get("low")
    bounce = None
    if price is not None and low not in (None, 0):
        bounce = (price / low - 1.0) * 100.0
    prev_px = (((prev or {}).get("verdict") or {}).get("carrier") or {}).get("price")
    falling = False
    if price is not None and prev_px not in (None, 0):
        try:
            from market_desk.config import SELL_CARRIER_FALL_PCT

            drop_pct = (float(prev_px) - float(price)) / float(prev_px) * 100.0
            falling = drop_pct >= float(SELL_CARRIER_FALL_PCT)
        except (TypeError, ValueError, ZeroDivisionError):
            falling = False
    status = main.get("status") or ""
    life_stage = classify_lifecycle(main) if board_name else None
    mute_m = int(setting("open_mute_minutes", 5))
    seg = session_segment(now, open_mute_minutes=mute_m)
    auction_only = seg.get("key") == "auction"

    if not board_name:
        action = "观望"
        reason = "热点板块尚未形成可识别主线"
    elif auction_only:
        action = "观望"
        reason = f"竞价阶段，实时主线看 {board_name}，9:30 后再定价"
    elif status == "退潮":
        action = "观望"
        reason = f"{board_name} 已转弱，主线身份不稳"
    elif life_stage == "ending":
        action = "观察回踩"
        reason = f"{board_name} 生命周期偏衰退（涨停衰减/走弱），先不追"
        algo_notes.append("主线生命周期=衰退")
    elif status == "尖峰禁追":
        action = "观察回踩"
        reason = f"{board_name} 是当前主线，但已到尖峰，先等回踩再下手"
    elif (
        vehicle.get("price")
        and pct is not None
        and (bounce or 0) >= float(ETF_BOUNCE_BUY_MIN)
        and not falling
        and status in ("确认中", "观察")
    ):
        action = "可买入"
        reason = f"主线 {board_name}，载体离日低回升且未继续下探"
    elif status == "确认中":
        action = "可买入"
        reason = f"主线确认：{board_name}，按你自己的仓位买对应载体"
    else:
        action = "观察回踩"
        reason = f"实时主线倾向 {board_name}，结构未完全确认"

    # No exact ETF map: still surface主板回踩观察票 (soft map is handled below).
    if board_name and not exact_etf and not soft_etf and action == "可买入":
        action = "观察回踩"
        reason = f"{board_name} 暂无映射 ETF，改盯主板回踩票：{reason}"
        algo_notes.append("无ETF映射")

    # Soft ETF is approximate only: never price a ready buy on it.
    soft_size_note = ""
    if soft_etf and action == "可买入":
        action = "观察回踩"
        reason = f"{board_name} 仅近似映射 {vehicle.get('name') or ''}，先观察回踩不直接定价买入"
        algo_notes.append("软ETF仅观察")
        soft_size_note = "近似映射宜更小仓或不做"

    if soft_etf and vehicle.get("name"):
        reason = f"{reason}（载体为近似映射 {vehicle.get('name')}）"
    action, reason, size_hint = apply_segment_bias(
        action,
        reason,
        segment_key=str(seg.get("key") or "closed"),
        status=status,
        phase=phase,
        open_mute=bool(seg.get("open_mute")),
        open_mute_minutes=mute_m,
    )
    if soft_size_note:
        size_hint = _join_hint(size_hint or "", soft_size_note)
    action, reason, size_hint, gate_notes = apply_market_gates(
        action,
        reason,
        size_hint,
        metrics=metrics,
        auction=auction,
        phase=phase,
        segment_key=str(seg.get("key") or "closed"),
    )
    algo_notes.extend(gate_notes)
    action, reason, size_hint, stock_block, review_notes = apply_review_bias(
        action, reason, size_hint, phase=phase
    )
    algo_notes.extend(review_notes)
    action, reason, size_hint, similar_notes = apply_similar_gate(
        action, reason, size_hint, similar=similar
    )
    algo_notes.extend(similar_notes)

    headline = f"实时主线 · {board_name or '未明'}"
    meaning = {
        "观望": "主线未明或已退潮，先看不买。",
        "观察回踩": "主线已经认出来了，但过热或未站稳，等回踩。",
        "可买入": "主线确认。优先 ETF（含创业板ETF、科创50ETF）。个股只给主板回踩票，创业/科创个股不推荐。",
    }.get(action, "")
    if size_hint:
        meaning = f"{meaning}（{size_hint}）"
    if soft_etf and board_name:
        meaning = f"{meaning} 载体为近似行业ETF，个股按无精确映射严筛，默认只观察回踩。"
    elif not exact_etf and board_name:
        meaning = f"{meaning} 当前主线无精确ETF映射，优先看主板回踩票（严筛选）。"
    if vehicle.get("code"):
        detail = (
            f"[{seg.get('label')}] {board_name} · {vehicle.get('name')} {vehicle.get('code')} "
            f"{vehicle.get('price') or '—'} {_fmt_pct(pct)}"
            f"{'，日低回升 ' + _fmt_num(bounce) + '%' if bounce is not None else ''}。{reason}"
        )
    else:
        detail = (
            f"[{seg.get('label')}] {board_name or '—'} { _fmt_pct(main.get('pct')) } · "
            f"总龙头 {main.get('leader_name') or '—'} {main.get('leader_boards') or 0}板。"
            f"{reason}"
        )
    bans = [b["name"] for b in (hot or []) if b.get("status") == "尖峰禁追"][:4]
    stocks: list[dict[str, Any]] = []
    # Without ETF mapping, still allow主板回踩观察票, but never as ready buys.
    allow_stocks = not stock_block and not _blocks_chi_star_stocks(board_name)
    if allow_stocks:
        stocks = _stock_candidates(
            main,
            zt or [],
            zb or [],
            boards=hot or [],
            orphan=not exact_etf,
        )
    elif stock_block and action in ("可买入", "观察回踩"):
        algo_notes.append("复盘命中偏低，本轮禁个股只留 ETF")
    recommend = _build_recommend(action, main, vehicle, bounce, stocks, bans)
    if not exact_etf and board_name:
        stock_n = sum(1 for x in (recommend.get("items") or []) if x.get("kind") == "stock")
        for item in recommend.get("items") or []:
            item["block_ready"] = True
            item["ready"] = False
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
            if item.get("kind") == "stock":
                item["role_label"] = "个股盯回踩"
        recommend["buy"] = False
        if soft_etf:
            recommend["title"] = "近似ETF · 盯回踩"
            recommend["size_note"] = _join_hint(
                str(recommend.get("size_note") or ""),
                f"近似映射 {vehicle.get('name') or ''}，个股更严筛选且禁现买",
            )
        elif stock_n:
            recommend["title"] = "无映射ETF · 盯主板回踩票"
            recommend["size_note"] = _join_hint(
                str(recommend.get("size_note") or ""),
                "无载体定价，以下为主板回踩观察票（严筛选），到价再考虑",
            )
        else:
            recommend["title"] = "暂无映射ETF，只观察"
            recommend["size_note"] = _join_hint(
                str(recommend.get("size_note") or ""),
                "主线无ETF映射，且暂无合格回踩票",
            )
    elif soft_etf:
        # Defensive: exact map path should not also be soft; keep observe block.
        for item in recommend.get("items") or []:
            item["block_ready"] = True
            item["ready"] = False
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
            kind = item.get("kind") or "stock"
            item["role_label"] = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"
        recommend["buy"] = False
        recommend["title"] = "近似ETF · 盯回踩"
        recommend["size_note"] = _join_hint(
            str(recommend.get("size_note") or ""),
            f"近似映射 {vehicle.get('name') or ''}，到位只提示、不点亮可买",
        )
    recommend = _apply_ready_confirmations(recommend, vehicle, metrics, main=main)
    if size_hint and recommend.get("size_note"):
        recommend["size_note"] = f"{size_hint}；{recommend['size_note']}"
    elif size_hint:
        recommend["size_note"] = size_hint
    playbook = build_playbook(phase, action=action, size_hint=size_hint)
    recommend = _attach_risk_sizing(recommend, playbook=playbook)

    side_info, side_recommend = _build_side_branch(
        hot=hot,
        main=main,
        etfs=etfs,
        zt=zt or [],
        zb=zb or [],
        bans=bans,
        stock_block=stock_block,
    )
    if side_info:
        algo_notes.append(f"观察支线={side_info.get('name')}")

    prev_name = (
        (((prev or {}).get("verdict") or {}).get("mainline") or {}).get("name") or ""
    ).strip()
    narrative = _mainline_narrative(
        board_name=board_name,
        status=status,
        action=action,
        phase=phase,
        pct=main.get("pct"),
        zt_n=main.get("zt_n"),
        leader_name=main.get("leader_name"),
        prev_name=prev_name,
        segment_label=str(seg.get("label") or ""),
        size_hint=size_hint,
    )
    if life_stage:
        narrative = narrative.rstrip("。") + f"；生命周期{ {'starting':'萌芽','ongoing':'主升','ending':'衰退'}.get(life_stage, life_stage) }。"
    if side_info and side_info.get("name"):
        narrative = (
            narrative.rstrip("。")
            + f"；观察支线「{side_info.get('name')}」"
            + (
                f"（分差{side_info.get('score_gap')}）"
                if side_info.get("score_gap") is not None
                else ""
            )
            + "，只盯回踩不当现买。"
        )
    if algo_notes:
        narrative = narrative.rstrip("。") + "；算法：" + "、".join(algo_notes[:5]) + "。"
    out = {
        "action": action,
        "headline": headline,
        "meaning": meaning,
        "reason": reason,
        "detail": detail,
        "narrative": narrative,
        "recommend": recommend,
        "side_mainline": side_info,
        "side_recommend": side_recommend,
        "playbook": playbook,
        "algo_notes": algo_notes,
        "mainline": {
            "name": board_name,
            "bk": main.get("bk"),
            "kind": main.get("kind"),
            "status": status,
            "pct": main.get("pct"),
            "zt_n": main.get("zt_n"),
            "leader_name": main.get("leader_name"),
            "leader_code": main.get("leader_code"),
            "leader_boards": main.get("leader_boards"),
            "lifecycle": life_stage,
            "sticky_since": sticky_since,
            "theme": theme_key(board_name),
            "score": ml_why.get("score"),
            "why": ml_why,
            "etf_mapped": etf_mapped and not soft_etf,
            "etf_soft": soft_etf,
            "pool_codes": [
                normalize_code(m.get("code"))
                for m in (main.get("pool") or main.get("members") or [])
                if m.get("code")
            ][:40],
        },
        "carrier": {
            "code": vehicle.get("code"),
            "name": vehicle.get("name"),
            "price": price,
            "pct": pct,
            "low": low,
            "high": vehicle.get("high"),
            "amount": vehicle.get("amount"),
            "volume": vehicle.get("volume"),
            "bounce": None if bounce is None else round(bounce, 2),
            "falling": falling,
            "mapped": etf_mapped,
        },
        "bans": bans,
        "auction_only": auction_only,
        "segment": seg,
        "segment_size_hint": size_hint,
        "phase": phase,
        "temperature": metrics.get("zt"),
        "stock_block": stock_block,
        "similar": similar or {},
    }
    out["recommend"] = mark_pullback_entries(out.get("recommend"))
    out["side_recommend"] = mark_pullback_entries(
        out.get("side_recommend"), observe_only=True
    )
    return align_action_with_ready(out)


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
) -> dict[str, Any]:
    """Flag cards whose last price sits near suggested buy; optionally arm ready.

    observe_only keeps side-branch cards as watch-only even when price tags the wait.
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

        can_arm = (
            near
            and not observe_only
            and not item.get("trend_down")
            and not item.get("block_ready")
            and item.get("minute", {}).get("ok") is not False
        )
        # Do not revive cards already failed by day-high / minute / trend gates.
        fails = item.get("confirm_fail") or []
        if fails:
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


def align_action_with_ready(verdict: dict[str, Any] | None) -> dict[str, Any]:
    """Keep hero action aligned with card-level ready / pullback-entry state.

    - Buyable with no ready cards → 观察回踩.
    - 观察回踩 with a near-entry ready card → 可买入.
    - Soft / unmapped ETF never upgrades to 可买入 (near-entry is watch-only).
    """
    v = dict(verdict or {})
    action = str(v.get("action") or "")
    rec = dict(v.get("recommend") or {})
    items = list(rec.get("items") or [])
    soft = bool((v.get("mainline") or {}).get("etf_soft"))
    no_etf = (v.get("mainline") or {}).get("etf_mapped") is False and not soft
    any_ready = any(bool(x.get("ready")) and not x.get("block_ready") for x in items)
    any_entry = any(
        bool(x.get("near_entry")) and bool(x.get("ready")) and not x.get("block_ready")
        for x in items
    )

    if soft or no_etf:
        # Near-entry may still badge cards, but never keep ready / never upgrade hero.
        demoted = action in ("可买入", "可小仓")
        note = "近似/无映射ETF，不升可买入"
        if demoted:
            v["action"] = "观察回踩"
            v["reason"] = _join_hint(str(v.get("reason") or ""), note)
            v["meaning"] = "主线已认，但载体为近似或无映射，只盯回踩不到位买卖。"
            size_hint = _join_hint(str(v.get("segment_size_hint") or ""), "近似映射宜更小仓或不做")
            v["segment_size_hint"] = size_hint
            phase = str(v.get("phase") or "")
            v["playbook"] = build_playbook(phase, action="观察回踩", size_hint=size_hint)
        for item in items:
            item["block_ready"] = True
            if item.get("ready"):
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
        rec["items"] = items
        rec["buy"] = False
        if "盯回踩" not in str(rec.get("title") or ""):
            rec["title"] = "近似ETF · 盯回踩" if soft else "无映射ETF · 盯回踩"
        v["recommend"] = rec
        notes = list(v.get("algo_notes") or [])
        if "soft禁升可买入" not in notes:
            notes.append("soft禁升可买入")
        v["algo_notes"] = notes
        return v

    if action in ("观察回踩", "观察") and any_entry:
        note = "现价贴近建议买，回踩到位可买"
        v["action"] = "可买入"
        v["reason"] = _join_hint(str(v.get("reason") or ""), note)
        v["meaning"] = "回踩已到建议买附近，可按卡片建议价试探（仍避开不追价）。"
        size_hint = _join_hint(str(v.get("segment_size_hint") or ""), "回踩到位宜小仓试探")
        v["segment_size_hint"] = size_hint
        rec["buy"] = True
        if not str(rec.get("title") or "").strip() or "盯回踩" in str(rec.get("title") or "") or "先不追" in str(rec.get("title") or ""):
            rec["title"] = "回踩到位，可买"
        rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), "现价已到建议买附近")
        v["recommend"] = rec
        notes = list(v.get("algo_notes") or [])
        if "回踩到位可买" not in notes:
            notes.append("回踩到位可买")
        v["algo_notes"] = notes
        phase = str(v.get("phase") or "")
        v["playbook"] = build_playbook(phase, action="可买入", size_hint=size_hint)
        narrative = str(v.get("narrative") or "")
        if note not in narrative:
            v["narrative"] = (narrative.rstrip("。") + f"；{note}。") if narrative else f"{note}。"
        return v

    if action not in ("可买入", "可小仓"):
        return v
    if any_ready and rec.get("buy"):
        return v
    # No ready path left → treat as pullback watch, not a live buy.
    note = "卡片未通过现买确认，改观察回踩防追高"
    v["action"] = "观察回踩"
    v["reason"] = _join_hint(str(v.get("reason") or ""), note)
    v["meaning"] = "主线已认出来，但现买确认未过（贴尖/日线/分时等），等回踩再动手。"
    size_hint = str(v.get("segment_size_hint") or "")
    size_hint = _join_hint(size_hint, "先盯回踩价，不追尖")
    v["segment_size_hint"] = size_hint
    rec["buy"] = False
    if not str(rec.get("title") or "").strip() or "可买" in str(rec.get("title") or ""):
        rec["title"] = "盯回踩价，先不追"
    rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), "现买确认未过")
    for item in items:
        if item.get("ready"):
            continue
        if not item.get("role_label"):
            kind = item.get("kind") or "stock"
            item["role_label"] = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"
    rec["items"] = items
    v["recommend"] = rec
    notes = list(v.get("algo_notes") or [])
    if "ready对齐观察回踩" not in notes:
        notes.append("ready对齐观察回踩")
    v["algo_notes"] = notes
    phase = str(v.get("phase") or "")
    v["playbook"] = build_playbook(phase, action="观察回踩", size_hint=size_hint)
    narrative = str(v.get("narrative") or "")
    if note not in narrative:
        v["narrative"] = (narrative.rstrip("。") + f"；{note}。") if narrative else f"{note}。"
    return v


_BUY_ACTIONS = frozenset({"可买入", "可小仓"})


def _desk_gate_buy_hint(
    action: str,
    ready_items: list[dict[str, Any]],
    rec: dict[str, Any],
) -> str:
    """One-line top-bar hint when live buy is allowed."""
    primary = next(
        (x for x in ready_items if x.get("near_entry")),
        ready_items[0] if ready_items else None,
    )
    if primary:
        name = str(primary.get("name") or primary.get("code") or "").strip()
        if name:
            return f"{action} · {name}"
    title = str(rec.get("title") or "").strip()
    if title:
        return f"{action} · {title}"
    return action


def _desk_gate_block_hint(
    action: str,
    reasons: list[str],
    rec: dict[str, Any],
    mainline: dict[str, Any],
) -> str:
    """One-line top-bar hint when live buy is blocked."""
    if reasons:
        lead = reasons[0]
        if len(lead) <= 26:
            return f"{action} · {lead}"
        return f"{action} · {lead[:24]}…"
    title = str(rec.get("title") or "").strip()
    if title:
        return f"{action} · {title}"
    ml = str(mainline.get("name") or "").strip()
    if ml:
        return f"{action} · {ml}"
    return action


def build_desk_gate_summary(
    verdict: dict[str, Any] | None,
    *,
    phase: str | None = None,
) -> dict[str, Any]:
    """Summarize live-buy permission for the desk top bar (read-only).

    Expects a verdict already passed through ``align_action_with_ready`` and
    related card gates. Does not mutate verdict fields or re-run gates.

    Returns:
        action: Hero action string (e.g. 观望 / 观察回踩 / 可买入).
        can_buy: True when action is buyable and at least one recommend card
            is ``ready`` without ``block_ready``.
        reasons: Up to five short Chinese strings explaining why live buy
            (现买) is blocked; empty when ``can_buy`` is True.
        hint: Single-line label suitable for the top banner.
    """
    v = verdict or {}
    action = str(v.get("action") or "观望")
    rec = dict(v.get("recommend") or {})
    items = list(rec.get("items") or [])
    mainline = dict(v.get("mainline") or {})
    playbook = dict(v.get("playbook") or {})

    ready_items = [
        x
        for x in items
        if bool(x.get("ready")) and not x.get("block_ready")
    ]
    buyable_action = action in _BUY_ACTIONS
    can_buy = buyable_action and bool(ready_items)

    if can_buy:
        return {
            "action": action,
            "can_buy": True,
            "reasons": [],
            "hint": _desk_gate_buy_hint(action, ready_items, rec),
        }

    reasons: list[str] = []
    seen: set[str] = set()

    def _add(reason: str) -> None:
        text = str(reason or "").strip()
        if not text or text in seen or len(reasons) >= 5:
            return
        seen.add(text)
        reasons.append(text)

    ml_name = str(mainline.get("name") or "").strip()

    if action == "观望":
        if v.get("auction_only"):
            _add("竞价阶段，9:30后再定价")
        elif mainline.get("status") == "退潮":
            _add(f"{ml_name}退潮，先观望" if ml_name else "主线退潮，先观望")
        elif not ml_name:
            _add("尚未形成可识别主线")
        else:
            _add("结论观望，先不买")
    elif action in ("观察回踩", "观察"):
        _add("结论观察回踩，等到位再试")
    elif not buyable_action:
        _add(f"结论{action}，不宜现买")

    if mainline.get("etf_soft"):
        _add("近似ETF映射，禁升现买")
    elif mainline.get("etf_mapped") is False:
        _add("无ETF映射，只观察不定价")

    bans = [str(b).strip() for b in (v.get("bans") or []) if str(b).strip()]
    if bans:
        shown = "、".join(bans[:3])
        if len(bans) > 3:
            shown += "等"
        _add(f"尖峰禁追：{shown}")

    if v.get("stock_block"):
        _add("复盘闸门禁个股现买")

    if v.get("auction_only") and "竞价阶段" not in "".join(reasons):
        _add("竞价阶段不作现买")

    size_cap = dict(v.get("size_cap") or {})
    if size_cap.get("hit"):
        _add("总仓触相位上限，先减不加")

    ph = str(phase or v.get("phase") or "").strip()
    if ph == "恐慌" and action != "观望":
        _add("恐慌相位，优先观望")
    elif ph == "高潮" and buyable_action:
        _add("高潮相位，新开宜谨慎")

    fail_counts: dict[str, int] = {}
    blocked_n = 0
    near_only_n = 0
    pending_minute = False

    for item in items:
        if item.get("block_ready"):
            blocked_n += 1
        for flag in item.get("confirm_fail") or []:
            text = str(flag).strip()
            if text:
                fail_counts[text] = fail_counts.get(text, 0) + 1
        if item.get("minute_pending"):
            pending_minute = True
        is_ready = bool(item.get("ready")) and not item.get("block_ready")
        if not is_ready and item.get("near_entry"):
            near_only_n += 1

    for flag, _ in sorted(fail_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        _add(flag)
        if len(reasons) >= 5:
            break

    if blocked_n and items and blocked_n >= len(items):
        _add("全部卡片禁现买")
    elif blocked_n:
        _add("部分卡片禁现买")

    if pending_minute:
        _add("分时未验，先等确认")

    if not ready_items:
        if near_only_n:
            _add("已触及建议价但未过现买确认")
        elif items and not fail_counts and blocked_n == 0:
            _add("卡片未到位，盯回踩价")

    dont = str(playbook.get("dont") or "").strip()
    if dont and len(reasons) < 5:
        short = dont.replace("不做：", "").split("、")[0][:22]
        if short:
            _add(f"纪律：{short}")

    hint = _desk_gate_block_hint(action, reasons, rec, mainline)
    return {
        "action": action,
        "can_buy": False,
        "reasons": reasons[:5],
        "hint": hint,
    }


def _build_side_branch(
    *,
    hot: list[dict[str, Any]],
    main: dict[str, Any],
    etfs: list[dict[str, Any]],
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    bans: list[str],
    stock_block: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build an observation-only side branch; never marks ready buys."""
    side = pick_side_mainline(hot, main)
    if not side:
        return None, None
    side_name = str(side.get("name") or "").strip()
    if not side_name:
        return None, None
    exact = etf_spec_for_name(side_name)
    soft = False
    vehicle = match_mainline_etf(side_name, etfs) if exact else None
    if not vehicle:
        soft_spec = etf_spec_soft_fallback(side_name)
        if soft_spec:
            _, code, name = soft_spec
            quote = next((x for x in (etfs or []) if x.get("code") == code), None)
            vehicle = (
                dict(quote)
                if quote
                else {
                    "code": code,
                    "name": name,
                    "price": None,
                    "pct": None,
                    "low": None,
                    "high": None,
                }
            )
            soft = True
    vehicle = vehicle or {}
    bounce = None
    price = vehicle.get("price")
    low = vehicle.get("low")
    if price is not None and low not in (None, 0):
        bounce = (float(price) / float(low) - 1.0) * 100.0
    allow_stocks = not stock_block and not _blocks_chi_star_stocks(side_name)
    stocks = _stock_candidates(side, zt, zb, boards=hot) if allow_stocks else []
    # Always observation path: wait prices only, never ready.
    rec = _build_recommend("观察回踩", side, vehicle, bounce, stocks, bans)
    for item in rec.get("items") or []:
        item["ready"] = False
        if item.get("wait_price") is not None:
            item["buy_price"] = item.get("wait_price")
        kind = item.get("kind") or "stock"
        item["role_label"] = "支线ETF盯回踩" if kind == "etf" else "支线个股盯回踩"
    rec["buy"] = False
    rec["title"] = f"观察支线 · {side_name}"
    rec["size_note"] = _join_hint(
        "仅观察回踩，不当现买主推；买卖仍跟实时主线",
        f"分差 {side.get('score_gap')}" if side.get("score_gap") is not None else "",
    )
    if soft and vehicle.get("name"):
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            f"支线载体近似映射 {vehicle.get('name')}",
        )
    if not (rec.get("items") or []):
        rec["text"] = f"观察支线 {side_name} · 暂无合格回踩票"
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "可盯板块状态，先不定价",
        )
    life = classify_lifecycle(side)
    info = {
        "name": side_name,
        "bk": side.get("bk"),
        "kind": side.get("kind"),
        "status": side.get("status"),
        "pct": side.get("pct"),
        "zt_n": side.get("zt_n"),
        "leader_name": side.get("leader_name"),
        "leader_code": side.get("leader_code"),
        "lifecycle": life,
        "score": side.get("score"),
        "score_gap": side.get("score_gap"),
        "etf_soft": soft,
        "carrier_code": vehicle.get("code"),
        "carrier_name": vehicle.get("name"),
    }
    return info, rec


def _resolve_board_vehicle(
    board_name: str,
    etfs: list[dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """Map a board name to an ETF vehicle; soft fallback when no exact rule."""
    if not board_name:
        return {}, False
    exact = etf_spec_for_name(board_name)
    vehicle = match_mainline_etf(board_name, etfs) if exact else None
    soft = False
    if not vehicle:
        soft_spec = etf_spec_soft_fallback(board_name)
        if soft_spec:
            _, code, name = soft_spec
            quote = next((x for x in (etfs or []) if x.get("code") == code), None)
            vehicle = (
                dict(quote)
                if quote
                else {
                    "code": code,
                    "name": name,
                    "price": None,
                    "pct": None,
                    "low": None,
                    "high": None,
                }
            )
            soft = True
    return vehicle or {}, soft


def _position_tied_to_board(
    row: dict[str, Any],
    board: dict[str, Any],
    vehicle: dict[str, Any],
    recommend: dict[str, Any] | None = None,
) -> bool:
    """Return True when a held name belongs to a favored board plan."""
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code(vehicle.get("code"))
    if carrier and code == carrier:
        return True
    pool = {
        normalize_code(m.get("code"))
        for m in (board.get("pool") or board.get("members") or [])
        if m.get("code")
    }
    if code in pool:
        return True
    for item in ((recommend or {}).get("items") or []):
        if normalize_code(item.get("code")) == code:
            return True
    board_name = str(board.get("name") or "")
    held_board = str(row.get("board") or row.get("sector") or row.get("industry") or "")
    if board_name and held_board and (board_name in held_board or held_board in board_name):
        return True
    return False


def build_favorite_desk_plans(
    *,
    favorite_boards: list[dict[str, Any]] | None,
    etfs: list[dict[str, Any]] | None,
    zt: list[dict[str, Any]] | None,
    zb: list[dict[str, Any]] | None,
    positions: list[dict[str, Any]] | None,
    phase: str,
    bans: list[str] | None = None,
    stock_block: bool = False,
    mainline_name: str = "",
    trade_date: str | None = None,
    metrics: dict[str, Any] | None = None,
    hot_boards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build buy/sell plans for personally favored boards on the battle desk.

    Plans stay secondary to the live mainline: default to pullback watch pricing,
    but near-entry cards can light ready inside this box only when gates pass.
    Soft ETF maps never arm ready.
    """
    boards = list(favorite_boards or [])[:4]
    if not boards:
        return {"ok": True, "empty": True, "boards": [], "title": "暂无看好板块"}
    bans = list(bans or [])
    etfs = list(etfs or [])
    zt = list(zt or [])
    zb = list(zb or [])
    positions = list(positions or [])
    metrics = metrics or {}
    hot = list(hot_boards or [])
    out_boards: list[dict[str, Any]] = []
    for board in boards:
        name = str(board.get("name") or "").strip()
        if not name:
            continue
        vehicle, soft = _resolve_board_vehicle(name, etfs)
        bounce = None
        price = vehicle.get("price")
        low = vehicle.get("low")
        if price is not None and low not in (None, 0):
            bounce = (float(price) / float(low) - 1.0) * 100.0
        allow_stocks = not stock_block and not _blocks_chi_star_stocks(name)
        stocks = _stock_candidates(board, zt, zb, boards=hot) if allow_stocks else []
        buy = _build_recommend("观察回踩", board, vehicle, bounce, stocks, bans)
        for item in buy.get("items") or []:
            kind = item.get("kind") or "stock"
            item["role_label"] = "看好ETF盯回踩" if kind == "etf" else "看好个股盯回踩"
            item["ready"] = False
            if soft:
                item["block_ready"] = True
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
        buy["buy"] = False
        buy["title"] = f"看好 · {name}"
        overlap = bool(mainline_name and name == mainline_name)
        note = "看好板块回踩计划；不替代上方实时主线"
        if overlap:
            note = "与实时主线重合，买卖优先看上方主推"
        if soft and vehicle.get("name"):
            note = _join_hint(note, f"载体近似映射 {vehicle.get('name')}，到位只提示")
        buy["size_note"] = _join_hint(str(buy.get("size_note") or ""), note)
        if not (buy.get("items") or []):
            buy["text"] = f"看好 {name} · 暂无合格回踩票，先盯板块"
        buy = _apply_ready_confirmations(buy, vehicle, metrics, main=board)
        buy = mark_pullback_entries(buy, observe_only=bool(soft))
        buy = _attach_risk_sizing(buy)

        life = classify_lifecycle(board) if board.get("name") else None
        fake_verdict = {
            "mainline": {
                "name": name,
                "bk": board.get("bk"),
                "status": board.get("status"),
                "lifecycle": life,
                "pool_codes": [
                    normalize_code(m.get("code"))
                    for m in (board.get("pool") or board.get("members") or [])
                    if m.get("code")
                ][:40],
            },
            "carrier": {
                "code": vehicle.get("code"),
                "name": vehicle.get("name"),
                "price": vehicle.get("price"),
            },
            "recommend": buy,
        }
        tied = [
            p
            for p in positions
            if int(p.get("qty") or 0) > 0
            and _position_tied_to_board(p, board, vehicle, buy)
        ]
        sell = build_sell_advice(tied, fake_verdict, phase, trade_date=trade_date)
        if tied and not sell.get("items"):
            sell = {
                "sell": False,
                "title": "相关持仓观望",
                "text": f"看好 {name} 有持仓，暂无卖点",
                "size_note": "按浮盈/回撤与板块强弱再评估",
                "items": [],
            }
        elif not tied:
            sell = {
                "sell": False,
                "empty": True,
                "title": "无相关持仓",
                "text": f"看好 {name} · 本地仓位暂无该板块票",
                "size_note": "",
                "items": [],
            }

        out_boards.append(
            {
                "name": name,
                "bk": board.get("bk"),
                "kind": board.get("kind"),
                "status": board.get("status"),
                "pct": board.get("pct"),
                "zt_n": board.get("zt_n"),
                "lifecycle": life,
                "overlap_mainline": overlap,
                "etf_soft": soft,
                "carrier_code": vehicle.get("code"),
                "carrier_name": vehicle.get("name"),
                "favorite_id": board.get("favorite_id"),
                "buy": buy,
                "sell": sell,
            }
        )
    return {
        "ok": True,
        "empty": not out_boards,
        "title": "看好板块 · 推荐买卖",
        "size_note": "默认盯回踩；现价贴近建议买时可试探。卖点只覆盖相关本地持仓。",
        "boards": out_boards,
    }


def _mainline_narrative(
    *,
    board_name: str,
    status: str,
    action: str,
    phase: str,
    pct: Any,
    zt_n: Any,
    leader_name: str | None,
    prev_name: str,
    segment_label: str,
    size_hint: str | None,
) -> str:
    """Build a one-line explanation of why the live mainline looks this way."""
    bits: list[str] = []
    if prev_name and board_name and prev_name != board_name:
        bits.append(f"主线由「{prev_name}」切到「{board_name}」")
    elif board_name:
        bits.append(f"当前主线「{board_name}」")
    else:
        bits.append("主线尚未识别")
    if status:
        bits.append(f"状态{status}")
    if pct is not None:
        try:
            bits.append(f"涨幅{float(pct):+.2f}%")
        except (TypeError, ValueError):
            pass
    if zt_n is not None:
        try:
            bits.append(f"涨停{int(zt_n)}只")
        except (TypeError, ValueError):
            pass
    if leader_name:
        bits.append(f"龙头{leader_name}")
    if phase:
        bits.append(f"大盘{phase}")
    if segment_label:
        bits.append(segment_label)
    bits.append(f"结论{action}")
    if size_hint:
        bits.append(size_hint)
    return "；".join(bits) + "。"


def apply_market_gates(
    action: str,
    reason: str,
    size_hint: str,
    *,
    metrics: dict[str, Any] | None,
    auction: dict[str, Any] | None,
    phase: str,
    segment_key: str,
) -> tuple[str, str, str, list[str]]:
    """Tighten buy actions using volume, index and auction context."""
    notes: list[str] = []
    m = metrics or {}
    hint = size_hint or ""
    act = action
    why = reason

    if phase == "恐慌" and act == "可买入":
        act = "观望"
        why = f"相位恐慌，新开仓关闭：{why}"
        notes.append("恐慌禁开仓")

    if phase == "高潮" and act == "可买入":
        act = "观察回踩"
        why = f"相位高潮，防冲高回落，先等回踩：{why}"
        notes.append("高潮降级")
        hint = _join_hint(hint, "高潮只兑现/等回踩，不追尖")

    if m.get("weak_index") and act == "可买入":
        act = "观察回踩"
        why = f"指数偏弱，降级观察回踩：{why}"
        notes.append("指数闸门")
        hint = _join_hint(hint, "指数偏弱宜更小仓")

    if m.get("style_weak") and act == "可买入":
        act = "观察回踩"
        why = f"成长风格偏弱(创业−沪深300)，降级观察回踩：{why}"
        notes.append("风格闸门")
        hint = _join_hint(hint, "成长风格弱宜更小仓")
    elif m.get("style_hot") and act == "可买入":
        hint = _join_hint(hint, "成长风格偏热仍防追高")
        notes.append("风格偏热防追")

    if float(m.get("avg_explode") or 0) >= 2.0 and act == "可买入":
        hint = _join_hint(hint, "涨停反复炸板，控仓")
        notes.append("炸板质量偏弱")
    if float(m.get("late_seal_rate") or 0) >= 50 and act == "可买入":
        hint = _join_hint(hint, "晚封偏多，控仓")
        notes.append("晚封偏多")

    try:
        prem = float(m.get("premium") or 0)
    except (TypeError, ValueError):
        prem = 0.0
    if prem <= -1.0 and act == "可买入":
        act = "观察回踩"
        why = f"昨停溢价偏弱({prem:.2f}%)，降级观察回踩：{why}"
        notes.append("负溢价闸门")
        hint = _join_hint(hint, "负溢价控仓或不做")
    elif prem <= 0 and act == "可买入":
        hint = _join_hint(hint, "溢价偏弱控仓")
        notes.append("溢价偏弱")
    elif prem >= 3.0 and act == "可买入":
        hint = _join_hint(hint, "溢价偏热仍防追")
        notes.append("溢价偏热防追")

    if m.get("thin_volume") and act == "可买入":
        act = "观察回踩"
        why = f"成交额分位偏低，防缩量假强：{why}"
        notes.append("缩量闸门")
        hint = _join_hint(hint, "缩量行情小仓或不做")

    if int(m.get("big_drop") or 0) >= 100 and act == "可买入":
        act = "观察回踩"
        why = f"大面家数偏多，降级观察：{why}"
        notes.append("大面闸门")

    auc = auction or {}
    med = auc.get("median_open")
    try:
        med_f = float(med) if med is not None else None
    except (TypeError, ValueError):
        med_f = None
    if med_f is not None and segment_key in ("open30", "morning", "afternoon"):
        if med_f <= -1.2 and act == "可买入":
            act = "观察回踩"
            why = f"弱竞价(中位{med_f:.2f}%)，降级观察：{why}"
            notes.append("弱竞价闸门")
            hint = _join_hint(hint, "弱竞价只试错或观望")
        elif med_f <= -0.5:
            hint = _join_hint(hint, "竞价偏弱控仓")
            notes.append("竞价偏弱")
        elif med_f >= 2.0 and act == "可买入":
            hint = _join_hint(hint, "强竞价勿追高")
            notes.append("强竞价防追")

    for g in m.get("context_gates") or []:
        if g not in notes:
            notes.append(str(g))
    return act, why, hint, notes[:6]


def apply_review_bias(
    action: str,
    reason: str,
    size_hint: str,
    *,
    phase: str,
) -> tuple[str, str, str, bool, list[str]]:
    """Use historical phase hit-rate to shrink size or block stocks."""
    notes: list[str] = []
    stock_block = False
    hint = size_hint or ""
    act = action
    why = reason
    try:
        from market_desk.db import load_signals
        from market_desk.review import build_phase_hit_rates

        hits = build_phase_hit_rates(load_signals(limit=240))
    except Exception:
        return act, why, hint, False, notes
    row = next((h for h in hits if str(h.get("phase") or "") == str(phase or "")), None)
    if not row or int(row.get("scored_n") or 0) < 5 or row.get("hit_rate") is None:
        return act, why, hint, False, notes
    rate = float(row["hit_rate"])
    scored = int(row["scored_n"])
    if rate < 35:
        stock_block = True
        hint = _join_hint(hint, f"相位{phase}命中{rate}%（n={scored}）偏低，仅ETF小仓")
        notes.append(f"复盘命中{rate}%")
        if act == "可买入":
            act = "观察回踩"
            why = f"相位{phase}历史命中偏低({rate}%)，降级观察回踩：{why}"
    elif rate < 45:
        hint = _join_hint(hint, f"相位{phase}命中{rate}%一般，偏小仓")
        notes.append(f"复盘命中{rate}%偏弱")
    return act, why, hint, stock_block, notes


def apply_similar_gate(
    action: str,
    reason: str,
    size_hint: str,
    *,
    similar: dict[str, Any] | None,
) -> tuple[str, str, str, list[str]]:
    """Use similar-day cool/hot bias as a real action gate, not only a hint."""
    notes: list[str] = []
    sim = similar or {}
    hint = size_hint or ""
    act = action
    why = reason
    bias = str(sim.get("bias") or "").strip()
    gate = sim.get("gate")
    n = int(sim.get("n") or 0)
    if bias:
        hint = _join_hint(hint, bias)
    if n:
        notes.append(f"相似日n={n}")
    if gate == "cool" and act == "可买入" and n >= 2:
        act = "观察回踩"
        why = f"相似日偏降温，降级观察回踩：{why}"
        notes.append("相似日降温闸门")
        hint = _join_hint(hint, "相似日降温只试错或观望")
    elif gate == "hot" and act == "可买入":
        hint = _join_hint(hint, "相似日偏升温仍防追高")
        notes.append("相似日升温防追")
    return act, why, hint, notes


def _join_hint(base: str, extra: str) -> str:
    """Append a size-hint fragment without duplicating text."""
    base = (base or "").strip()
    extra = (extra or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base:
        return base
    return f"{base}；{extra}"


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
        str(main.get("status") or "") == "确认中"
        and int(main.get("zt_n") or 0) <= int(THIN_CONFIRM_ZT_MAX)
    )
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
        if kind == "stock" and thin_confirm:
            cross_n = int(item.get("cross_n") or 0)
            theme_n = int(item.get("cross_theme_n") or 0)
            ok_cross = cross_n >= int(THIN_CROSS_BOARD_MIN)
            ok_theme = theme_n >= int(THIN_CROSS_THEME_MIN)
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
        if item.get("wait_price") is not None:
            item["buy_price"] = item.get("wait_price")
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
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Attach daily-trend labels, nudge scores, and gate ready buys.

    Stocks: clear up +bonus; clear down / sideways gate ready.
    ETF: clear up +bonus; clear down gates ready; sideways score-only (no gate).
    Missing/thin kline → ``trend_pending`` visible, no ready gate / score nudge.
    """
    del overrides  # Manual overrides removed; trend is informational + ready gate.
    from market_desk.config import STOCK_TREND_DOWN_PENALTY, STOCK_TREND_UP_BONUS

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
            if marked.get("wait_price") is not None:
                marked["buy_price"] = marked.get("wait_price")
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
            # Sideways: stocks gate ready; ETF keeps ready but no up-bonus.
            marked["trend"] = "震荡/非上升"
            marked["trend_ok"] = False
            marked["trend_down"] = False
            marked["trend_unknown"] = True
            marked["trend_pending"] = False
            marked["trend_warn"] = "不是上升趋势"
            if kind == "stock":
                _gate_ready("日线非上升")
                marked["reason"] = (
                    str(marked.get("reason") or "") + "；确认失败：日线非上升"
                ).strip("；")
            else:
                marked["reason"] = _join_hint(
                    str(marked.get("reason") or ""),
                    "日线震荡，ETF 不加上升分",
                )

        marked["trend_adj"] = round(trend_adj, 1)
        marked["score"] = round(base_score + trend_adj, 1)
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


def _build_recommend(
    action: str,
    main: dict[str, Any],
    vehicle: dict[str, Any],
    bounce: float | None,
    stocks: list[dict[str, Any]],
    bans: list[str],
) -> dict[str, Any]:
    """Build ETF-first plus stock-alt cards with suggested buy / wait / stop prices."""
    avoid = "、".join(bans) if bans else "高位连板个股"
    board = main.get("name") or "—"
    ready_action = action == "可买入"
    wait_action = action == "观察回踩"
    items: list[dict[str, Any]] = []

    if ready_action or wait_action:
        if vehicle.get("code"):
            items.append(
                _recommend_item(
                    vehicle,
                    kind="etf",
                    role="primary",
                    ready=ready_action,
                    reason=_etf_reason(board, vehicle, bounce, ready_action, wait_action),
                )
            )
        stock_limit = 2 if items else 3
        taken = 0
        for stock in stocks:
            if vehicle.get("code") and stock.get("code") == vehicle.get("code"):
                continue
            items.append(
                _recommend_item(
                    stock,
                    kind="stock",
                    role="alt" if items else "primary",
                    ready=ready_action and bool(stock.get("ready")),
                    reason=str(stock.get("reason") or f"主线 {board} 回踩票"),
                )
            )
            taken += 1
            if taken >= stock_limit:
                break

        if wait_action:
            for item in items:
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item["wait_price"]
                item["role_label"] = "ETF 盯回踩" if item["kind"] == "etf" else "个股盯回踩"

    primary = next((x for x in items if x.get("role") == "primary"), None) or (
        items[0] if items else None
    )
    if ready_action and items:
        title = "建议买入"
        text = _headline_text(items, board, buying=True)
        has_etf = any(x.get("kind") == "etf" for x in items)
        if _is_chi_star_etf(vehicle.get("code")):
            size_note = "创业板ETF / 科创50ETF 可以买；对应板块个股无权限，不推荐。"
        elif has_etf:
            size_note = "优先 ETF；个股只给主板未封板回踩票（过市值门槛）。创业/科创个股不推荐。"
        else:
            size_note = "该主线暂无映射 ETF，以下为主板未封板回踩票（过市值门槛）。仓位自己定。"
        stop = _stop_line(primary)
    elif wait_action and items:
        title = "盯回踩价，先不追"
        text = _headline_text(items, board, buying=False)
        size_note = "到回踩价再动手。现价靠近日高就不要追。"
        stop = _stop_line(primary)
    else:
        title = "暂不买入"
        text = f"暂不买入。实时主线 {board}。" if board != "—" else "暂不买入，主线未明。"
        size_note = f"实时主线 {board}" if board != "—" else "主线未明"
        stop = "确认前不加仓"

    return {
        "buy": bool(ready_action and items),
        "title": title,
        "code": (primary or {}).get("code"),
        "name": (primary or {}).get("name"),
        "price": (primary or {}).get("buy_price") or (primary or {}).get("last"),
        "qty": 0,
        "amount": 0,
        "size_note": size_note,
        "stop": stop,
        "avoid": (
            f"主线 {board}；不要去追：{avoid}"
            + ("；创业/科创个股无权限，不推荐" if _blocks_chi_star_stocks(board) or _is_chi_star_etf(vehicle.get("code")) else "")
        ),
        "text": text,
        "items": items,
        "primary": primary,
    }


def _board_membership_index(
    boards: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, str]]]:
    """Map ticker → hot-board memberships (name/status/theme) for resonance scoring.

    Skips 退潮 / ice labels so cold boards do not inflate multi-concept names.
    """
    out: dict[str, list[dict[str, str]]] = {}
    skip_status = {"退潮", "冷冻", "相对最冷", "冰点"}
    for board in boards or []:
        name = str(board.get("name") or "").strip()
        if not name:
            continue
        status = str(board.get("status") or "").strip()
        if status in skip_status:
            continue
        theme = theme_key(name) or name
        seen: set[str] = set()
        for member in board.get("pool") or board.get("members") or []:
            code = normalize_code(member.get("code"))
            if not code or code in seen:
                continue
            seen.add(code)
            out.setdefault(code, []).append(
                {"name": name, "status": status, "theme": theme}
            )
    return out


def _cross_board_adj(
    code: str,
    board_name: str,
    membership: dict[str, list[dict[str, str]]],
) -> tuple[float, dict[str, Any]]:
    """Score multi-board resonance relative to the board being recommended from."""
    from market_desk.config import (
        STOCK_CROSS_BOARD_BASE,
        STOCK_CROSS_BOARD_CAP,
        STOCK_CROSS_CONFIRM_BONUS,
        STOCK_CROSS_THEME_BONUS,
    )

    rows = list(membership.get(code) or [])
    main = (board_name or "").strip()
    others: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name or name == main or name in seen_names:
            continue
        seen_names.add(name)
        others.append(row)
    if not others:
        return 0.0, {
            "cross_n": 0,
            "theme_n": 0,
            "confirm_n": 0,
            "boards": [],
        }

    adj = 0.0
    theme_n = 0
    confirm_n = 0
    for row in others:
        adj += float(STOCK_CROSS_BOARD_BASE)
        if main and same_theme(main, row.get("name")):
            adj += float(STOCK_CROSS_THEME_BONUS)
            theme_n += 1
        st = str(row.get("status") or "")
        if st == "确认中":
            adj += float(STOCK_CROSS_CONFIRM_BONUS)
            confirm_n += 1
    adj = min(adj, float(STOCK_CROSS_BOARD_CAP))
    return adj, {
        "cross_n": len(others),
        "theme_n": theme_n,
        "confirm_n": confirm_n,
        "boards": [str(r.get("name") or "") for r in others[:6]],
    }


def _stock_candidates(
    main: dict[str, Any],
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]] | None = None,
    boards: list[dict[str, Any]] | None = None,
    *,
    orphan: bool = False,
) -> list[dict[str, Any]]:
    """Pick unsealed main-board pullbacks from the live mainline constituent pool.

    When ``orphan`` is True (no exact ETF map), apply stricter pullback / market-cap
    filters and skip the soft non-strict fallback pass.
    """
    from market_desk.db import load_blacklist_codes

    sealed = {normalize_code(x.get("code")) for x in zt}
    broken = {normalize_code(x.get("code")) for x in (zb or [])}
    boards_by = {
        normalize_code(x.get("code")): int(x.get("boards") or 0) for x in zt
    }
    explode_by = {
        normalize_code(x.get("code")): int(x.get("explode_count") or 0) for x in zt
    }
    skip = {
        normalize_code(main.get("leader_code")),
        normalize_code(main.get("slot_code")),
    }
    blocked = load_blacklist_codes()
    pool = list(main.get("pool") or main.get("members") or [])
    board_name = str(main.get("name") or "")
    # Index current scoring board + other hot cards so multi-membership is visible.
    index_boards = list(boards or [])
    if main and not any(
        str(b.get("name") or "") == board_name for b in index_boards
    ):
        index_boards = [main] + index_boards
    membership = _board_membership_index(index_boards)
    scored: list[tuple[float, dict[str, Any]]] = []
    for member in pool:
        item = _score_stock(
            member,
            sealed,
            boards_by,
            skip,
            broken,
            explode_by,
            blocked,
            board_name=board_name,
            membership=membership,
            strict=True,
            orphan=orphan,
        )
        if item:
            scored.append(item)
    if len(scored) < 2 and not orphan:
        for member in pool:
            code = normalize_code(member.get("code"))
            if any(code == normalize_code(x[1].get("code")) for x in scored):
                continue
            item = _score_stock(
                member,
                sealed,
                boards_by,
                skip,
                broken,
                explode_by,
                blocked,
                board_name=board_name,
                membership=membership,
                strict=False,
                orphan=False,
            )
            if item:
                scored.append(item)
    scored.sort(key=lambda row: row[0], reverse=True)
    return [row[1] for row in scored[:4]]


def _score_stock(
    member: dict[str, Any],
    sealed: set[str],
    boards_by: dict[str, int],
    skip: set[str],
    broken: set[str],
    explode_by: dict[str, int],
    blocked: set[str] | None = None,
    *,
    board_name: str = "",
    membership: dict[str, list[dict[str, str]]] | None = None,
    strict: bool,
    orphan: bool = False,
) -> tuple[float, dict[str, Any]] | None:
    """Score one constituent as a pullback candidate, or reject it."""
    from market_desk.config import (
        ORPHAN_STOCK_MV_MULT,
        ORPHAN_STOCK_PB_MIN,
        STOCK_PULLBACK_BAND_MAX,
        STOCK_PULLBACK_SWEET_MAX,
        STOCK_READY_PULLBACK_MIN,
    )
    from market_desk.settings import setting

    code = normalize_code(member.get("code"))
    name = str(member.get("name") or "")
    pct = member.get("pct")
    price = member.get("price")
    high = member.get("high")
    if not code or price in (None, 0) or not is_main_board(code) or is_st(name):
        return None
    if blocked and code in blocked:
        return None
    if code in skip or code in sealed or code in broken:
        return None
    if is_limit_up(name, pct):
        return None
    if pct is None:
        return None
    min_mv = float(setting("min_stock_mv_yi", 120.0) or 0.0)
    if orphan and min_mv > 0:
        min_mv *= float(ORPHAN_STOCK_MV_MULT)
    mv_yi = member.get("mv_yi")
    if min_mv > 0:
        if mv_yi is None:
            return None
        if float(mv_yi) < min_mv:
            return None
    try:
        turnover = float(member.get("turnover")) if member.get("turnover") is not None else None
    except (TypeError, ValueError):
        turnover = None
    # Overheated turnover: skip hard; elevated turnover: no ready buys.
    if turnover is not None and turnover >= 25.0:
        return None
    if strict and (pct < 0 or pct > 5.5):
        return None
    if not strict and (pct < -1.5 or pct > 7.0):
        return None

    pb_min = float(ORPHAN_STOCK_PB_MIN if orphan else STOCK_READY_PULLBACK_MIN)
    pb_max = float(STOCK_PULLBACK_BAND_MAX)
    pb_sweet = float(STOCK_PULLBACK_SWEET_MAX)
    pullback = None
    if high and high > 0:
        pullback = (float(high) - float(price)) / float(high) * 100.0
    # Require a modest day-high pullback before listing as ready-quality.
    if strict and (pullback is None or pullback < pb_min or pullback > pb_max):
        return None
    score = 20.0 - abs(float(pct) - 2.0) * 2.0
    if pullback is not None:
        if pb_min <= pullback <= pb_sweet:
            score += 15.0
        elif pullback > pb_sweet:
            score += 4.0
        else:
            score -= 6.0
    # Prefer larger caps slightly when scores are close.
    if mv_yi is not None:
        score += min(6.0, float(mv_yi) / 80.0)
    if turnover is not None:
        if 3.0 <= turnover <= 12.0:
            score += 4.0
        elif turnover > 18.0:
            score -= 8.0
        elif turnover > 15.0:
            score -= 4.0
    explode_n = int(explode_by.get(code) or 0)
    if explode_n >= 2:
        score -= 10.0
    elif explode_n == 1:
        score -= 4.0
    cross_adj, cross_meta = _cross_board_adj(code, board_name, membership or {})
    score += cross_adj
    boards = int(boards_by.get(code) or 0)
    # Ready after enough day-high pullback; still blocks tip-chase names.
    ready = pullback is not None and pullback >= pb_min
    if turnover is not None and turnover >= 15.0:
        ready = False
    if explode_n >= 2:
        ready = False
    reason = _stock_reason(
        pct,
        pullback,
        ready,
        mv_yi=mv_yi,
        turnover=turnover,
        cross_n=int(cross_meta.get("cross_n") or 0),
        theme_n=int(cross_meta.get("theme_n") or 0),
        cross_boards=list(cross_meta.get("boards") or []),
    )
    out = dict(member)
    out["code"] = code
    out["boards"] = boards
    out["mv_yi"] = None if mv_yi is None else round(float(mv_yi), 1)
    out["pullback"] = None if pullback is None else round(pullback, 2)
    out["turnover"] = None if turnover is None else round(float(turnover), 2)
    out["ready"] = ready
    out["cross_n"] = int(cross_meta.get("cross_n") or 0)
    out["cross_theme_n"] = int(cross_meta.get("theme_n") or 0)
    out["cross_boards"] = list(cross_meta.get("boards") or [])
    out["cross_adj"] = round(cross_adj, 1)
    out["score"] = round(score, 1)
    out["reason"] = reason
    return score, out


def _stock_reason(
    pct: float,
    pullback: float | None,
    ready: bool,
    *,
    mv_yi: float | None = None,
    turnover: float | None = None,
    cross_n: int = 0,
    theme_n: int = 0,
    cross_boards: list[str] | None = None,
) -> str:
    """Describe why a stock is listed as a pullback alternative."""
    parts = [f"涨幅 {_fmt_pct(pct)}"]
    if mv_yi is not None:
        parts.append(f"市值 {float(mv_yi):.0f}亿")
    if turnover is not None:
        parts.append(f"换手 {float(turnover):.1f}%")
    if pullback is not None:
        parts.append(f"高点回撤 {pullback:.1f}%")
    parts.append("未封板")
    if cross_n > 0:
        names = [n for n in (cross_boards or []) if n][:2]
        tag = f"跨{cross_n}板块"
        if theme_n > 0:
            tag += f"·同主题{theme_n}"
        if names:
            tag += f"（{'/'.join(names)}）"
        parts.append(tag)
    if ready:
        parts.append("可按建议价试")
    else:
        parts.append("仍偏高，等回踩价")
    return "，".join(parts)


def _etf_reason(
    board: str,
    vehicle: dict[str, Any],
    bounce: float | None,
    ready: bool,
    waiting: bool,
) -> str:
    """Describe why the mapped ETF is the primary vehicle."""
    bits = [f"主线 {board} 映射载体"]
    if bounce is not None:
        bits.append(f"离日低回升 {_fmt_num(bounce)}%")
    pct = vehicle.get("pct")
    if pct is not None:
        bits.append(_fmt_pct(pct))
    if waiting:
        bits.append("尖峰/未站稳，等回踩价")
    elif ready:
        bits.append("可按建议价买")
    if _is_chi_star_etf(vehicle.get("code")):
        bits.append("ETF可买，创业/科创个股无权限不推荐")
    return "，".join(bits)


def _recommend_item(
    quote: dict[str, Any],
    *,
    kind: str,
    role: str,
    ready: bool,
    reason: str,
) -> dict[str, Any]:
    """Attach a buy / wait / stop / chase plan onto a quote."""
    from market_desk.config import ETF_NEAR_HIGH_PCT, STOCK_NEAR_HIGH_PCT

    etf = kind == "etf" or _is_etf_code(str(quote.get("code") or ""))
    digits = 3 if etf else 2
    last = quote.get("price")
    low = quote.get("low")
    high = quote.get("high")
    tip_need = ETF_NEAR_HIGH_PCT if etf else STOCK_NEAR_HIGH_PCT
    near_high = bool(
        last
        and high
        and high > 0
        and (float(high) - float(last)) / float(high) * 100.0 < tip_need
    )
    buy_now = bool(ready and last is not None and not near_high)
    wait = _wait_price(last, low, etf)
    stop = _stop_price(last, low, etf)
    chase = _chase_price(last, high, etf)
    buy = last if buy_now else wait
    if buy is None:
        buy = last
    kind_label = "ETF" if etf else "个股"
    if buy_now:
        role_label = f"{kind_label} 主推" if role == "primary" else f"{kind_label} 备选"
    else:
        role_label = f"{kind_label} 盯回踩"
    item = {
        "kind": "etf" if etf else "stock",
        "kind_label": kind_label,
        "role": role,
        "role_label": role_label,
        "code": normalize_code(quote.get("code")),
        "name": quote.get("name"),
        "last": _px(last, digits),
        "pct": None if quote.get("pct") is None else round(float(quote["pct"]), 2),
        "mv_yi": None if quote.get("mv_yi") is None else round(float(quote["mv_yi"]), 1),
        "amount": quote.get("amount"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "buy_price": _px(buy, digits),
        "wait_price": _px(wait, digits),
        "stop_price": _px(stop, digits),
        "chase_price": _px(chase, digits),
        "low": _px(low, digits),
        "high": _px(high, digits),
        "ready": buy_now,
        "reason": reason,
        "qty": 100,
    }
    item["batch_plan"] = _batch_plan_lots(item.get("buy_price") or item.get("last"), etf=etf)
    return item


def _attach_risk_sizing(
    recommend: dict[str, Any],
    *,
    playbook: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach risk-based share counts onto recommendation cards."""
    from market_desk.settings import setting

    rec = dict(recommend or {})
    equity = float(setting("account_equity", 50000))
    risk_pct = float(setting("risk_pct_per_trade", 1.0))
    # Soft scale risk% by playbook size cap (e.g. panic uses smaller risk).
    cap = float((playbook or {}).get("size_cap_pct") or 100)
    if cap < 40:
        risk_pct = min(risk_pct, 0.6)
    elif cap < 55:
        risk_pct = min(risk_pct, 1.0)
    items = []
    for raw in rec.get("items") or []:
        item = dict(raw)
        buy = item.get("buy_price") or item.get("last")
        stop = item.get("stop_price")
        plan = suggest_risk_qty(
            buy=buy,
            stop=stop,
            account_equity=equity,
            risk_pct=risk_pct,
            kind=str(item.get("kind") or "stock"),
        )
        if plan:
            item["risk_plan"] = plan
            if plan.get("qty"):
                item["qty"] = int(plan["qty"])
        items.append(item)
    rec["items"] = items
    rec["risk_meta"] = {
        "account_equity": equity,
        "risk_pct_per_trade": risk_pct,
        "size_cap_pct": cap,
    }
    return rec


def apply_size_cap_gate(
    verdict: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Hard-hint gate: when open cost already at playbook size cap, kill ready buys.

    Does not auto-trade; only blocks new ready / buy flags and surfaces a size note.
    """
    from market_desk.settings import setting

    v = dict(verdict or {})
    playbook = v.get("playbook") or {}
    cap = float(playbook.get("size_cap_pct") or 100)
    equity = float(setting("account_equity", 50000) or 0)
    if equity <= 0 or cap >= 99.5:
        return v
    open_cost = 0.0
    for row in positions or []:
        if int(row.get("qty") or 0) <= 0:
            continue
        try:
            open_cost += float(row.get("cost") or 0)
        except (TypeError, ValueError):
            pass
    used_pct = open_cost / equity * 100.0
    equity_default = abs(equity - 50000.0) < 0.5
    v["size_cap"] = {
        "cap_pct": cap,
        "used_pct": round(used_pct, 1),
        "open_cost": round(open_cost, 2),
        "equity": equity,
        "hit": used_pct >= cap,
        "equity_default": equity_default,
    }
    if equity_default and open_cost > 0:
        warn = "账户资金仍为默认5万，总仓占比可能失真，请在参数里改真实资金"
        for key in ("recommend", "side_recommend"):
            rec = dict(v.get(key) or {})
            if rec:
                rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), warn)
                v[key] = rec
        v["size_cap"]["note"] = warn
    if used_pct < cap:
        return v
    note = f"总仓已约 {used_pct:.0f}%≥相位上限 {cap:.0f}%，先减不加"
    if equity_default:
        note = f"{note}；账户资金仍为默认5万，请核对"
    for key in ("recommend", "side_recommend"):
        rec = dict(v.get(key) or {})
        items = []
        changed = False
        for raw in rec.get("items") or []:
            item = dict(raw)
            if item.get("ready"):
                changed = True
                item["ready"] = False
                item["block_ready"] = True
                item["size_cap_block"] = True
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
                kind = item.get("kind") or "stock"
                item["role_label"] = "ETF 盯回踩" if kind == "etf" else "个股盯回踩"
                fails = list(item.get("confirm_fail") or [])
                if "总仓触相位上限" not in fails:
                    fails.append("总仓触相位上限")
                item["confirm_fail"] = fails
            items.append(item)
        if items:
            rec["items"] = items
        if changed or rec.get("buy"):
            rec["buy"] = False
            rec["title"] = "总仓已满 · 先减不加"
            rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), note)
        v[key] = rec
    if v.get("action") == "可买入":
        v["action"] = "观察回踩"
        v["reason"] = _join_hint(str(v.get("reason") or ""), note)
        v["algo_notes"] = list(v.get("algo_notes") or []) + ["总仓触相位上限"]
    return v


def _batch_plan_lots(buy: float | None, *, etf: bool) -> list[dict[str, Any]] | None:
    """Build a 1/2/3-lot buy plan when the batch_plan setting is enabled."""
    from market_desk.settings import setting

    if not bool(setting("batch_plan", True)):
        return None
    unit = 100
    labels = ("试错", "确认", "加仓")
    lots: list[dict[str, Any]] = []
    for i, label in enumerate(labels, start=1):
        qty = unit
        cost = None if buy is None else round(float(buy) * qty, 2 if not etf else 3)
        lots.append({"lot": i, "qty": qty, "label": label, "approx_cost": cost})
    return lots


def _wait_price(last: float | None, low: float | None, etf: bool) -> float | None:
    """Return a better pullback entry below the last price."""
    from market_desk.config import ETF_WAIT_GAP, STOCK_WAIT_GAP

    if last is None:
        return None
    gap = ETF_WAIT_GAP if etf else STOCK_WAIT_GAP
    wait = float(last) * gap
    if low not in (None, 0) and float(low) < float(last):
        mid = (float(low) + float(last)) / 2.0
        wait = min(wait, mid)
        wait = max(wait, float(low))
    return wait


def _stop_price(last: float | None, low: float | None, etf: bool) -> float | None:
    """Use the session low as the invalidation level, with a last-price fallback."""
    if low not in (None, 0):
        return float(low)
    if last is None:
        return None
    return float(last) * (0.985 if etf else 0.97)


def _chase_price(last: float | None, high: float | None, etf: bool) -> float | None:
    """Mark the price above which chasing is not allowed."""
    if last is None:
        return None
    bump = float(last) * (1.012 if etf else 1.02)
    if high not in (None, 0) and float(high) >= float(last):
        return float(high)
    return bump


def _px(value: float | None, digits: int) -> float | None:
    """Round a price to ETF or stock precision."""
    if value is None:
        return None
    return round(float(value), digits)


def _is_etf_code(code: str) -> bool:
    """Return True for common mainland ETF code prefixes."""
    c = normalize_code(code)
    return c.startswith(("15", "51", "56", "58"))


def _is_chi_star_etf(code: str | None) -> bool:
    """Return True for ChiNext / STAR ETFs that remain tradable without stock permission."""
    return normalize_code(code) in CHINEXT_STAR_ETFS


def _blocks_chi_star_stocks(board_name: str | None) -> bool:
    """Return True when the live mainline sits on ChiNext or STAR, so stocks are skipped."""
    text = board_name or ""
    return "创业板" in text or "科创" in text


def _headline_text(items: list[dict[str, Any]], board: str, *, buying: bool) -> str:
    """Build the one-line summary above the recommendation cards."""
    primary = items[0]
    verb = "建议买" if buying and primary.get("ready") else "盯回踩"
    px = primary.get("buy_price") or primary.get("last") or "—"
    extra = ""
    alts = [x for x in items[1:] if x.get("code")]
    if alts:
        extra = "；备选 " + "、".join(f"{x['name']} {x['code']}" for x in alts[:2])
    return (
        f"{verb} {primary.get('name') or ''} {primary.get('code') or ''}  "
        f"{px}  （主线 {board}）{extra}"
    )


def _stop_line(primary: dict[str, Any] | None) -> str:
    """One-line stop hint for the summary row."""
    if not primary or primary.get("stop_price") is None:
        return "按你自己的止损"
    return f"参考止损 {primary.get('stop_price')}（跌破日低视为回踩失败）"


def _position_tied_to_mainline(row: dict[str, Any], verdict: dict[str, Any]) -> bool:
    """Return True when a held name is the live carrier or in the mainline pool.

    Soft mainline-fade sells should not fire on unrelated residual positions.
    """
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code((verdict.get("carrier") or {}).get("code"))
    if carrier and code == carrier:
        return True
    main = verdict.get("mainline") or {}
    pool = {normalize_code(c) for c in (main.get("pool_codes") or []) if c}
    if code in pool:
        return True
    for item in ((verdict.get("recommend") or {}).get("items") or []):
        if normalize_code(item.get("code")) == code:
            return True
    board = str(row.get("board") or row.get("sector") or row.get("industry") or "")
    main_name = str(main.get("name") or "")
    if board and main_name and (board in main_name or main_name in board):
        return True
    return False


def build_sell_advice(
    positions: list[dict[str, Any]],
    verdict: dict[str, Any] | None,
    phase: str,
    *,
    trade_date: str | None = None,
    trends_by_code: dict[str, dict[str, Any]] | None = None,
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
        )
        if item:
            items.append(item)
    sell_bias = sell_bundle.get("all") or {}
    sell_bias_out = {
        "hit_rate": sell_bias.get("hit_rate"),
        "n": sell_bias.get("n"),
        "widen": bool(sell_bias.get("widen")),
        "tighten": bool(sell_bias.get("tighten")),
        "note": sell_bias.get("note") or sell_bundle.get("note"),
        "etf": sell_bundle.get("etf"),
        "stock": sell_bundle.get("stock"),
        "all": sell_bias,
    }
    rank = {"stop": 0, "take": 1, "trim": 2, "hold": 3}
    items.sort(key=lambda x: (rank.get(str(x.get("urgency") or "hold"), 9), -(x.get("pnl_pct") or 0)))
    items = items[:4]
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
        t1_n = sum(1 for x in items if x.get("t1_locked"))
        text = (
            f"继续持有 · 盯 {primary.get('name')} 目标 {primary.get('sell_price')}"
            if primary
            else "继续持有"
        )
        size_note = "未触发卖点时，建议卖=目标价，止损按成本下方。"
        if t1_n:
            size_note = f"有 {t1_n} 只当日买入（T+1），隔日才能卖。" + size_note
    return {
        "sell": bool(sell_now),
        "empty": False,
        "title": "建议卖出" if sell_now else "仓位观察",
        "text": text,
        "size_note": size_note,
        "items": items,
        "primary": primary if items else None,
        "sell_bias": sell_bias_out,
    }


def _exit_band_params(
    *,
    etf: bool,
    on_mainline: bool,
    ending: bool,
    main_status: str,
    life_stage: str,
    phase: str,
    soft_exit: bool,
    trend: dict[str, Any] | None,
    carrier_falling: bool,
    sell_bias: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pick stop / pullback / take-profit bands from multi-module context.

    Regimes:
    - give: tied to live mainline that is still confirming/rising, daily up,
      and not in soft-exit — wider stop, tolerate deeper pullback.
    - tight: fade/ending/panic/climax/daily down/carrier falling — tighter.
    - neutral: default fixed bands.

    Optional ``sell_bias`` from review sell outcomes scales pb/take/pocket.
    """
    from market_desk.config import SELL_BAND_ETF, SELL_BAND_STOCK

    trend = trend or {}
    # Lifecycle keys: starting | ongoing | ending (see lifecycle.py).
    rising_life = life_stage in ("starting", "ongoing")
    confirming = main_status == "确认中"
    strong_hold = bool(
        on_mainline
        and confirming
        and rising_life
        and trend.get("up")
        and not soft_exit
        and not ending
    )
    weak_context = bool(
        soft_exit
        or ending
        or trend.get("down")
        or phase == "恐慌"
        or (on_mainline and carrier_falling)
        or (on_mainline and main_status == "退潮")
    )
    if strong_hold:
        mode = "give"
        mode_zh = "主升放宽"
    elif weak_context:
        mode = "tight"
        mode_zh = "退潮收紧"
    else:
        mode = "neutral"
        mode_zh = "标准"
    table = SELL_BAND_ETF if etf else SELL_BAND_STOCK
    band = dict(table[mode])
    bias = sell_bias or {}
    mult = float(bias.get("mult") or 1.0)
    if bias.get("widen") and mult > 1.0:
        for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
            band[key] = float(band[key]) * mult
        # Slightly wider stop floor when historically selling too early.
        band["pnl_stop"] = float(band["pnl_stop"]) * (1.0 + (mult - 1.0) * 0.5)
        mode_zh = f"{mode_zh}·卖早放宽"
    elif bias.get("tighten") and 0 < mult < 1.0:
        for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
            band[key] = float(band[key]) * mult
        mode_zh = f"{mode_zh}·卖准收紧"
    band["mode"] = mode
    band["mode_zh"] = mode_zh
    band["sell_bias"] = {
        "widen": bool(bias.get("widen")),
        "tighten": bool(bias.get("tighten")),
        "mult": mult,
        "hit_rate": bias.get("hit_rate"),
        "n": bias.get("n"),
        "note": bias.get("note"),
    }
    return band


def _sell_item(
    row: dict[str, Any],
    verdict: dict[str, Any],
    phase: str,
    *,
    trade_date: str | None = None,
    trend: dict[str, Any] | None = None,
    sell_bias: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Decide whether a held name should be sold, trimmed, or held.

    Exit is layered: stop → clear; deep take / lifecycle ending → clear or half;
    soft mainline fade → half first. ``exit_mode`` drives UI defaults (half/clear).

    Daily trend (once per day): if stop band hits but trend is still up and price
    holds above MA20, soften to half; clear downtrend / MA20 break stays hard stop.

    Stop width and pullback thresholds adapt via ``_exit_band_params`` (mainline /
    lifecycle / phase / daily trend).
    """
    from market_desk.db import is_t1_locked, position_buy_day
    from market_desk.lots import clear_sell_qty, half_sell_qty

    code = normalize_code(row.get("code"))
    name = str(row.get("name") or code)
    last = row.get("last")
    buy = float(row.get("buy_price") or 0)
    if not code or not buy:
        return None
    etf = _is_etf_code(code)
    digits = 3 if etf else 2
    high = row.get("high")
    low = row.get("low")
    pct = row.get("last_pct")
    pnl_pct = row.get("pnl_pct")
    if pnl_pct is None and last is not None and buy:
        pnl_pct = (float(last) / buy - 1.0) * 100.0
    # Hold-peak (cross-day) anchors take-profit pullback; day high alone is too myopic.
    peak_candidates = [buy]
    for raw in (row.get("peak_price"), high, last):
        try:
            if raw not in (None, "", 0) and float(raw) > 0:
                peak_candidates.append(float(raw))
        except (TypeError, ValueError):
            pass
    hold_peak = max(peak_candidates)
    pullback = None
    if last is not None and hold_peak > 0:
        pullback = (hold_peak - float(last)) / hold_peak * 100.0

    action = verdict.get("action") or ""
    mainline = verdict.get("mainline") or {}
    main_status = mainline.get("status") or ""
    life_stage = mainline.get("lifecycle") or ""
    ending = life_stage == "ending"
    carrier = verdict.get("carrier") or {}
    on_mainline = _position_tied_to_mainline(row, verdict)
    auction_only = bool(verdict.get("auction_only"))
    # Soft exits: panic always; climax only with higher profit bar later.
    # Mainline fade = 退潮/ending — not bare「观望」(auction also sets 观望).
    phase_panic = phase == "恐慌"
    phase_climax = phase == "高潮"
    mainline_fade = (not auction_only) and (
        main_status == "退潮" or ending
    )
    soft_exit = phase_panic or (mainline_fade and on_mainline)
    t1_locked = is_t1_locked(row, trade_date)
    buy_day = position_buy_day(row)
    hold_qty = int(row.get("qty") or 0)
    trend = trend or {}
    ma20 = trend.get("ma20")

    band = _exit_band_params(
        etf=etf,
        on_mainline=on_mainline,
        ending=ending,
        main_status=str(main_status),
        life_stage=str(life_stage),
        phase=phase,
        soft_exit=soft_exit,
        trend=trend,
        carrier_falling=bool(carrier.get("falling")),
        sell_bias=sell_bias,
    )
    stop = buy * float(band["stop_buy"])
    if low not in (None, 0) and float(low) < buy:
        stop = max(float(low), buy * float(band["stop_floor"]))
    target = buy * (1.03 if etf else 1.05)
    if high not in (None, 0) and float(high) > target:
        target = float(high) * (0.995 if etf else 0.99)

    urgency = "hold"
    ready = False
    exit_mode = "hold"  # hold | half | clear
    role_label = "继续持有"
    sell_price = target
    sell_pct = 0
    reason_parts: list[str] = []

    pb_light = float(band["pb_light"])
    pb_deep = float(band["pb_deep"])
    pnl_stop = float(band["pnl_stop"])
    take_pnl = float(band["take_pnl"])
    take_deep_pnl = float(band["take_deep_pnl"])
    pocket_pnl = float(band["pocket_pnl"])
    # Ending on live mainline: still nudge take-profit earlier on top of tight mode.
    if ending and on_mainline and band["mode"] != "tight":
        pb_light *= 0.75
        pb_deep *= 0.85
        take_pnl = min(take_pnl, 4.0 if not etf else 2.5)
        take_deep_pnl = min(take_deep_pnl, 5.0 if not etf else 3.5)
        pocket_pnl = min(pocket_pnl, 6.0 if not etf else 4.0)

    if last is None:
        role_label = "待行情"
        reason_parts.append("尚无现价，先不判卖点")
    elif float(last) <= stop or (pnl_pct is not None and pnl_pct <= pnl_stop):
        deep_pnl = pnl_pct is not None and pnl_pct <= pnl_stop
        # Soften on MA20 hold unless day-once trend is clearly down (do not require trend.up).
        above_ma20 = ma20 is not None and float(last) > float(ma20)
        soft_ok = above_ma20 and not deep_pnl and not bool(trend.get("down"))
        if soft_ok:
            urgency = "stop"
            ready = True
            exit_mode = "half"
            role_label = "止损带·站上MA20先减"
            sell_price = float(last)
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，触及止损带（{band['mode_zh']}），"
                f"现价仍站上 MA20 {ma20}，建议先减一半"
            )
        else:
            urgency = "stop"
            ready = True
            exit_mode = "clear"
            role_label = "止损清仓"
            sell_price = float(last)
            sell_pct = 100
            if deep_pnl and trend.get("up"):
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，深亏止损（{band['mode_zh']}）；"
                    f"虽日线标上升仍建议清仓"
                )
            elif trend.get("down"):
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，触及止损且日线下降（{band['mode_zh']}），建议清仓"
                )
            elif trend.get("up") and ma20 is not None and float(last) <= float(ma20):
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，触及止损且跌破日线 MA20 {ma20}"
                    f"（{band['mode_zh']}），建议清仓"
                )
            else:
                reason_parts.append(
                    f"浮盈 {_fmt_pct(pnl_pct)}，触及止损带（{band['mode_zh']}），建议清仓"
                )
    elif (
        pnl_pct is not None
        and pnl_pct >= take_pnl
        and pullback is not None
        and pullback >= pb_light
    ):
        urgency = "take"
        ready = True
        sell_price = float(last)
        deep = pullback >= pb_deep and pnl_pct >= take_deep_pnl
        if deep or (
            ending
            and on_mainline
            and pullback >= pb_light
            and pnl_pct >= (3.0 if etf else 5.0)
        ):
            exit_mode = "clear"
            role_label = "结构回撤清仓" if deep else "退潮兑现清仓"
            sell_pct = 100
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%"
                + ("，生命周期偏衰退" if ending and on_mainline else "")
                + f"（{band['mode_zh']}），建议清仓"
            )
        else:
            exit_mode = "half"
            role_label = "冲高回落先减"
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%"
                f"（{band['mode_zh']}），先减一半"
            )
    elif pnl_pct is not None and pnl_pct >= pocket_pnl:
        urgency = "take"
        ready = True
        sell_price = float(last)
        if ending and on_mainline:
            exit_mode = "clear"
            role_label = "衰退落袋清仓"
            sell_pct = 100
            reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，主线生命周期偏衰退，建议清仓")
        else:
            exit_mode = "half"
            role_label = "落袋先减"
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}（{band['mode_zh']}），建议先减一半，余仓盯止损"
            )
    elif (soft_exit or phase_climax) and pnl_pct is not None:
        from market_desk.config import (
            SELL_SOFT_CLIMAX_MIN_PNL_ETF,
            SELL_SOFT_CLIMAX_MIN_PNL_STOCK,
            SELL_SOFT_DEEP_PNL_ETF,
            SELL_SOFT_DEEP_PNL_STOCK,
            SELL_SOFT_MIN_PNL_ENDING_ETF,
            SELL_SOFT_MIN_PNL_ENDING_STOCK,
            SELL_SOFT_MIN_PNL_ETF,
            SELL_SOFT_MIN_PNL_STOCK,
        )

        if ending and on_mainline:
            soft_min = SELL_SOFT_MIN_PNL_ENDING_ETF if etf else SELL_SOFT_MIN_PNL_ENDING_STOCK
        else:
            soft_min = SELL_SOFT_MIN_PNL_ETF if etf else SELL_SOFT_MIN_PNL_STOCK
        # Climax alone (no panic / no 退潮) needs a higher profit floor.
        if phase_climax and not soft_exit:
            soft_min = max(
                soft_min,
                SELL_SOFT_CLIMAX_MIN_PNL_ETF if etf else SELL_SOFT_CLIMAX_MIN_PNL_STOCK,
            )
        if pnl_pct > soft_min:
            urgency = "trim"
            ready = True
            sell_price = float(last)
            deep_need = SELL_SOFT_DEEP_PNL_ETF if etf else SELL_SOFT_DEEP_PNL_STOCK
            deep_fade = ending and on_mainline and (
                (pullback is not None and pullback >= pb_light)
                or pnl_pct >= deep_need
            )
            if deep_fade:
                exit_mode = "clear"
                role_label = "衰退清仓"
                sell_pct = 100
                reason_parts.append(
                    f"生命周期/主线偏弱，浮盈 {_fmt_pct(pnl_pct)}"
                    + (f"，回撤 {pullback:.1f}%" if pullback is not None else "")
                    + "，建议清仓"
                )
            else:
                exit_mode = "half"
                role_label = "衰退先减" if ending and on_mainline else "建议先减"
                sell_pct = 50
                why = (
                    "生命周期偏衰退"
                    if ending and on_mainline
                    else (
                        "相位恐慌"
                        if phase_panic
                        else ("相位高潮" if phase_climax else "主线退潮")
                    )
                )
                reason_parts.append(
                    f"{why}，浮盈 {_fmt_pct(pnl_pct)}（门槛 {soft_min:.1f}%），先减一半"
                )
        # else: below soft floor — fall through to carrier / ending / hold
    if not ready and (
        not etf
        and on_mainline
        and carrier.get("falling")
        and pnl_pct is not None
        and pnl_pct > -1.0
        and last is not None
    ):
        from market_desk.config import SELL_CARRIER_FALL_PCT

        urgency = "trim"
        ready = True
        exit_mode = "half"
        role_label = "载体走弱先减"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(
            f"主线 ETF/载体较上一轮回落≥{float(SELL_CARRIER_FALL_PCT):.2f}%，个股先减一半"
        )
    if not ready and ending and on_mainline and pnl_pct is not None and last is not None:
        from market_desk.config import SELL_ENDING_DEFENSE_PNL

        if pnl_pct <= float(SELL_ENDING_DEFENSE_PNL):
            urgency = "trim"
            ready = True
            exit_mode = "half"
            role_label = "衰退防守先减"
            sell_price = float(last)
            sell_pct = 50
            reason_parts.append(f"生命周期偏衰退且浮盈 {_fmt_pct(pnl_pct)}，先减仓防守")
    if not ready and last is not None:
        role_label = "继续持有"
        sell_price = target
        sell_pct = 0
        exit_mode = "hold"
        if not reason_parts:
            reason_parts.append(f"浮盈 {_fmt_pct(pnl_pct)}，未到卖点，盯目标价")
            if pullback is not None:
                reason_parts.append(f"高点回撤 {pullback:.1f}%")
            if soft_exit or phase_climax:
                reason_parts.append("主线/相位偏弱但未过软减门槛")
            if ending and on_mainline:
                reason_parts.append("主线生命周期偏衰退，反抽优先减")
            elif mainline_fade and not on_mainline:
                reason_parts.append("非当前主线持仓，主线转弱不自动减")
            if band["mode"] != "neutral":
                reason_parts.append(f"波段口径 {band['mode_zh']}")

    if t1_locked and ready:
        ready = False
        role_label = f"T+1锁定·{role_label}"
        sell_pct = 0
        exit_mode = "hold"
        reason_parts.insert(0, f"当日买入（{buy_day or '今'}）隔日才能卖")
    elif t1_locked:
        role_label = "T+1锁定"
        exit_mode = "hold"
        reason_parts.insert(0, f"当日买入（{buy_day or '今'}）隔日才能卖")

    half_q = half_sell_qty(hold_qty)
    clear_q = clear_sell_qty(hold_qty)
    sell_qty = clear_q if exit_mode == "clear" else (half_q if exit_mode == "half" else 0)
    stop_pct = round((1.0 - float(band["stop_buy"])) * 100.0, 2)

    return {
        "id": row.get("id"),
        "kind": "etf" if etf else "stock",
        "kind_label": "ETF" if etf else "个股",
        "role_label": role_label,
        "urgency": urgency,
        "exit_mode": exit_mode,
        "ready": ready,
        "t1_locked": t1_locked,
        "last_buy_date": buy_day or None,
        "daily_trend": None if not trend else trend.get("label"),
        "daily_trend_zh": None if not trend else trend.get("label"),
        "band_mode": band["mode"],
        "band_mode_zh": band["mode_zh"],
        "stop_pct": stop_pct,
        "pb_light": round(pb_light, 2),
        "pb_deep": round(pb_deep, 2),
        "code": code,
        "name": name,
        "qty": hold_qty,
        "last": _px(last, digits),
        "pct": None if pct is None else round(float(pct), 2),
        "pnl_pct": None if pnl_pct is None else round(float(pnl_pct), 2),
        "buy_price": _px(buy, digits),
        "sell_price": _px(sell_price, digits),
        "sell_pct": sell_pct,
        "sell_qty": sell_qty,
        "sell_qty_half": half_q,
        "sell_qty_clear": clear_q,
        "stop_price": _px(stop, digits),
        "target_price": _px(target, digits),
        "reason": "，".join(reason_parts),
    }


def decorate_positions(
    rows: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    *,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """Attach mark-to-market and day-realized fields used by the position tab."""
    day = str(trade_date or "").strip()[:10]
    out: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code") or "").zfill(6)
        q = quotes.get(code) or {}
        last = q.get("price")
        buy = float(row.get("buy_price") or 0)
        qty = int(row.get("qty") or 0)
        closed_date = str(row.get("closed_date") or "").strip()[:10] or None
        closed = qty <= 0 and bool(closed_date)
        sell_px = row.get("last_sell_price")
        day_sold = int(row.get("day_sold_qty") or 0)
        day_realized = round(float(row.get("day_realized_pnl") or 0), 2)
        sell_day = str(row.get("last_sell_date") or "")[:10]
        if day and sell_day and sell_day != day:
            day_sold = 0
            day_realized = 0.0
        if closed:
            mark = float(sell_px) if sell_px not in (None, "") else (
                float(last) if last is not None else buy
            )
            sold_qty = day_sold or int(row.get("day_sold_qty") or 0)
            cost = round(buy * sold_qty, 2) if sold_qty else None
            market = round(mark * sold_qty, 2) if sold_qty and mark is not None else None
            pnl = day_realized
            pnl_pct = round((mark / buy - 1.0) * 100.0, 2) if mark and buy else None
            last = mark
            day_pnl = day_realized if day_realized else None
        else:
            cost = round(buy * qty, 2)
            market = round(last * qty, 2) if last is not None else None
            pnl = round(market - cost, 2) if market is not None else None
            pnl_pct = round((last / buy - 1.0) * 100.0, 2) if last and buy else None
            # Session P&L vs yesterday close (+ today realized from partial sells).
            prev = q.get("prev")
            day_mtm = None
            try:
                if last is not None and prev not in (None, 0) and qty > 0:
                    day_mtm = (float(last) - float(prev)) * qty
                elif q.get("pct") is not None and cost:
                    day_mtm = float(cost) * float(q.get("pct")) / 100.0
            except (TypeError, ValueError):
                day_mtm = None
            if day_mtm is not None or day_realized:
                day_pnl = round((day_mtm or 0.0) + day_realized, 2)
            else:
                day_pnl = None
        item = dict(row)
        item["code"] = code
        item["name"] = row.get("name") or q.get("name") or code
        item["last"] = last
        item["last_pct"] = None if closed else q.get("pct")
        item["high"] = q.get("high")
        item["low"] = q.get("low")
        item["prev"] = None if closed else q.get("prev")
        item["cost"] = cost
        item["market"] = market
        item["pnl"] = pnl
        item["pnl_pct"] = pnl_pct
        item["day_pnl"] = day_pnl
        item["closed"] = closed
        item["closed_date"] = closed_date
        item["day_sold_qty"] = day_sold
        item["day_realized_pnl"] = day_realized
        item["status"] = "今日已平" if closed else ("部分兑现" if day_sold > 0 else "持仓")
        # Maintain hold-peak for cross-day take-profit pullback.
        if not closed and qty > 0:
            peak_vals = [buy]
            stored = row.get("peak_price")
            try:
                if stored not in (None, "") and float(stored) > 0:
                    peak_vals.append(float(stored))
            except (TypeError, ValueError):
                pass
            for raw in (q.get("high"), last):
                try:
                    if raw not in (None, "") and float(raw) > 0:
                        peak_vals.append(float(raw))
                except (TypeError, ValueError):
                    pass
            item["peak_price"] = round(max(peak_vals), 4)
            item["peak_dirty"] = (
                stored in (None, "")
                or abs(float(item["peak_price"]) - float(stored or 0)) > 1e-6
            )
        else:
            item["peak_price"] = row.get("peak_price")
            item["peak_dirty"] = False
        out.append(item)
    return out


def position_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate open book and today realized P&L for the position tab."""
    from market_desk.config import (
        POSITION_MAX_NAMES,
        POSITION_MAX_SINGLE_PCT,
        POSITION_MAX_TOTAL_COST,
    )

    open_rows = [r for r in rows if int(r.get("qty") or 0) > 0]
    closed_rows = [r for r in rows if int(r.get("qty") or 0) <= 0]
    cost = sum(float(r.get("cost") or 0) for r in open_rows)
    marked = [r for r in open_rows if r.get("market") is not None]
    market = sum(float(r.get("market") or 0) for r in marked)
    floating = round(market - cost, 2) if marked else None
    floating_pct = round((market / cost - 1.0) * 100.0, 2) if marked and cost else None
    realized = round(sum(float(r.get("day_realized_pnl") or 0) for r in rows), 2)
    day_total = round((floating or 0) + realized, 2) if marked or realized else (realized if realized else None)
    day_total_pct = None
    if day_total is not None and cost:
        # Percent vs remaining open cost; closed-only days use realized alone.
        day_total_pct = round(day_total / cost * 100.0, 2)
    elif day_total is not None and not open_rows and realized:
        closed_cost = sum(
            float(r.get("buy_price") or 0) * int(r.get("day_sold_qty") or 0) for r in closed_rows
        )
        if closed_cost:
            day_total_pct = round(day_total / closed_cost * 100.0, 2)
    notes: list[str] = []
    if len(open_rows) > POSITION_MAX_NAMES:
        notes.append(f"持仓只数 {len(open_rows)} 超过软上限 {POSITION_MAX_NAMES}")
    if cost > POSITION_MAX_TOTAL_COST:
        notes.append(f"总成本 {cost:.0f} 超过软上限 {POSITION_MAX_TOTAL_COST:.0f}")
    if market > 0:
        for r in marked:
            share = float(r.get("market") or 0) / market * 100.0
            if share >= POSITION_MAX_SINGLE_PCT:
                notes.append(
                    f"{r.get('name') or r.get('code')} 占比 {share:.0f}% ≥ {POSITION_MAX_SINGLE_PCT:.0f}%"
                )
    return {
        "count": len(open_rows),
        "closed_count": len(closed_rows),
        "cost": round(cost, 2),
        "market": round(market, 2) if marked else None,
        "pnl": floating,
        "pnl_pct": floating_pct,
        "floating_pnl": floating,
        "floating_pct": floating_pct,
        "realized_pnl": realized,
        "day_pnl": day_total,
        "day_pnl_pct": day_total_pct,
        "priced": len(marked),
        "risk_note": "；".join(notes) if notes else "",
    }


def build_risk_overview(
    rows: list[dict[str, Any]],
    *,
    size_cap_pct: float | None = None,
) -> dict[str, Any]:
    """Build a richer risk panel: weights, soft caps, and P&L distribution."""
    from market_desk.config import (
        POSITION_MAX_NAMES,
        POSITION_MAX_SINGLE_PCT,
        POSITION_MAX_TOTAL_COST,
    )
    from market_desk.settings import setting

    open_rows = [r for r in rows if int(r.get("qty") or 0) > 0]
    base = position_summary(rows)
    market = float(base.get("market") or 0)
    cost = float(base.get("cost") or 0)
    target_cost = float(setting("target_total_cost", POSITION_MAX_TOTAL_COST))
    equal_w = bool(setting("equal_weight_target", True))
    loss_cap = float(setting("daily_loss_cap_pct", -3.0))
    cool_n = int(setting("cool_after_losses", 3))
    equity = float(setting("account_equity", 50000) or 0)
    equal_share = round(100.0 / len(open_rows), 1) if equal_w and open_rows else None
    items: list[dict[str, Any]] = []
    for r in open_rows:
        mkt = float(r.get("market") or 0) if r.get("market") is not None else None
        weight = round(mkt / market * 100.0, 1) if market > 0 and mkt is not None else None
        weight_dev = (
            round(weight - equal_share, 1)
            if weight is not None and equal_share is not None
            else None
        )
        items.append(
            {
                "id": r.get("id"),
                "code": r.get("code"),
                "name": r.get("name"),
                "cost": r.get("cost"),
                "market": r.get("market"),
                "pnl": r.get("pnl"),
                "pnl_pct": r.get("pnl_pct"),
                "day_realized_pnl": r.get("day_realized_pnl"),
                "weight_pct": weight,
                "target_weight_pct": equal_share,
                "weight_dev_pct": weight_dev,
                "over_weight": bool(weight is not None and weight >= POSITION_MAX_SINGLE_PCT),
            }
        )
    items.sort(key=lambda x: float(x.get("weight_pct") or 0), reverse=True)
    winners = sum(1 for r in open_rows if (r.get("pnl_pct") or 0) > 0)
    losers = sum(1 for r in open_rows if (r.get("pnl_pct") or 0) < 0)
    day_pct = base.get("day_pnl_pct")
    if day_pct is None:
        day_pct = base.get("pnl_pct")
    target_dev = round((cost / target_cost - 1.0) * 100.0, 1) if target_cost > 0 else None
    loss_cap_hit = bool(day_pct is not None and float(day_pct) <= loss_cap)
    cool_hit = bool(losers >= cool_n)
    used_pct = (cost / equity * 100.0) if equity > 0 else None
    cap = float(size_cap_pct) if size_cap_pct is not None else None
    size_cap_hit = bool(cap is not None and used_pct is not None and used_pct >= cap)
    tips: list[str] = []
    if size_cap_hit:
        tips.append(
            f"总成本约占账户 {used_pct:.0f}% ≥ 相位仓位上限 {cap:.0f}%，建议先减不加"
        )
    if loss_cap_hit:
        tips.append(
            f"今日盈亏已触及单日亏损帽 {loss_cap:g}%（当前 {day_pct}%），建议停手、只减不加"
        )
    if cool_hit:
        tips.append(f"浮亏标的 {losers} 只 ≥ 连亏降温阈值 {cool_n}，先冷静再开新仓")
    if target_dev is not None and abs(target_dev) >= 15:
        tips.append(f"总成本相对目标 {target_cost:.0f} 偏差 {target_dev:+.1f}%")
    if int(base.get("closed_count") or 0):
        tips.append(f"今日已平 {base['closed_count']} 只，已实现 {base.get('realized_pnl')}，次日自动移出")
    for it in items:
        if it.get("weight_dev_pct") is not None and abs(float(it["weight_dev_pct"])) >= 12:
            tips.append(
                f"{it.get('name') or it.get('code')} 相对等权偏差 "
                f"{float(it['weight_dev_pct']):+.1f}%"
            )
            if len(tips) >= 6:
                break
    return {
        **base,
        "items": items,
        "winners": winners,
        "losers": losers,
        "flat": max(0, len(open_rows) - winners - losers),
        "caps": {
            "max_names": POSITION_MAX_NAMES,
            "max_single_pct": POSITION_MAX_SINGLE_PCT,
            "max_total_cost": POSITION_MAX_TOTAL_COST,
        },
        "target_total_cost": target_cost,
        "target_dev_pct": target_dev,
        "equal_weight_target": equal_w,
        "daily_loss_cap_pct": loss_cap,
        "cool_after_losses": cool_n,
        "loss_cap_hit": loss_cap_hit,
        "cool_hit": cool_hit,
        "size_cap_pct": cap,
        "size_used_pct": None if used_pct is None else round(used_pct, 1),
        "size_cap_hit": size_cap_hit,
        "tips": tips,
    }


def build_deltas(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compare key gauges with the previous refresh."""
    prev_m = (previous or {}).get("metrics") or {}
    cur_m = current.get("metrics") or {}
    prev_c = ((previous or {}).get("verdict") or {}).get("carrier") or {}
    cur_c = (current.get("verdict") or {}).get("carrier") or {}
    prev_ml = ((previous or {}).get("verdict") or {}).get("mainline") or {}
    cur_ml = (current.get("verdict") or {}).get("mainline") or {}
    items = [
        _delta("温度", current.get("temperature"), previous.get("temperature") if previous else None, 0),
        _delta("涨停", cur_m.get("zt"), prev_m.get("zt"), 0),
        _delta("跌停", cur_m.get("dt"), prev_m.get("dt"), 0, invert=True),
        _delta("炸板%", cur_m.get("zb_rate"), prev_m.get("zb_rate"), 1, invert=True),
        _delta("晋级%", cur_m.get("promotion"), prev_m.get("promotion"), 1),
        _delta("1→2%", cur_m.get("promo_1_2"), prev_m.get("promo_1_2"), 1),
        _delta("2→3%", cur_m.get("promo_2_3"), prev_m.get("promo_2_3"), 1),
        _delta("溢价%", cur_m.get("premium"), prev_m.get("premium"), 2),
        _delta("主线", cur_ml.get("pct"), prev_ml.get("pct"), 2, unit="%"),
        _delta("载体", cur_c.get("pct"), prev_c.get("pct"), 2, unit="%"),
    ]
    return items


def _delta(
    label: str,
    cur: float | None,
    prev: float | None,
    digits: int,
    unit: str = "",
    invert: bool = False,
) -> dict[str, Any]:
    if cur is None:
        return {"label": label, "value": None, "delta": None, "arrow": "→", "dir": "flat", "unit": unit}
    value = round(float(cur), digits)
    if prev is None:
        return {
            "label": label,
            "value": value,
            "delta": None,
            "arrow": "→",
            "dir": "flat",
            "unit": unit,
            "text": "较上轮 —",
        }
    raw = float(cur) - float(prev)
    delta = round(raw, digits)
    if abs(raw) < 10 ** (-max(digits, 1)):
        arrow, direction = "→", "flat"
    elif raw > 0:
        arrow, direction = "↑", "up"
    else:
        arrow, direction = "↓", "down"
    better = direction == "down" if invert else direction == "up"
    tone = "flat" if direction == "flat" else ("good" if better else "bad")
    if direction == "flat":
        text = "较上轮 持平"
    else:
        sign = "+" if delta > 0 else ""
        text = f"较上轮 {arrow}{sign}{delta}{unit}"
    return {
        "label": label,
        "value": value,
        "delta": delta,
        "arrow": arrow,
        "dir": direction,
        "tone": tone,
        "unit": unit,
        "text": text,
    }


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}"
