"""Live mainline verdict, recommendation, and snapshot-to-snapshot deltas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.config import (
    CHINEXT_STAR_ETFS,
    ETF_BOUNCE_BUY_MIN,
    ETF_THIN_AMOUNT,
    STOCK_WEAK_VS_ETF_PCT,
    TRADE_FEE_CNY,
)
from market_desk.filters import is_limit_up, is_main_board, is_st, normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import (
    etf_spec_for_name,
    etf_spec_soft_fallback,
    explain_mainline,
    mainline_score,
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
    sticky_bias: dict[str, Any] = {}
    sticky_margin: float | None = None
    try:
        from market_desk.adapt import build_sticky_margin_bias

        sticky_bias = build_sticky_margin_bias()
        if sticky_bias.get("margin") is not None:
            sticky_margin = float(sticky_bias["margin"])
    except Exception:
        sticky_bias = {}
    main = pick_mainline(
        hot,
        sticky_name=sticky,
        margin=sticky_margin,
        sticky_held_seconds=sticky_held,
    ) or {}
    board_name = main.get("name") or ""
    ml_why = explain_mainline(
        hot,
        main,
        sticky_name=sticky,
        margin=sticky_margin,
        sticky_held_seconds=sticky_held,
    )
    if sticky_bias.get("ok") and sticky_bias.get("note"):
        # Defer algo note until algo_notes exists below.
        pass
    if board_name and sticky and board_name == sticky and sticky_since_prev:
        sticky_since = sticky_since_prev
    else:
        sticky_since = now.strftime("%Y-%m-%d %H:%M:%S")
    exact_spec = etf_spec_for_name(board_name) if board_name else None
    exact_etf = bool(exact_spec)
    soft_etf = False
    etf = match_mainline_etf(board_name, etfs) if board_name else None
    algo_notes: list[str] = []
    if sticky_bias.get("ok") and sticky_bias.get("note"):
        algo_notes.append(str(sticky_bias["note"]))
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
        # Confirmed identity alone is not a chase signal — wait bounce or near-entry.
        action = "观察回踩"
        reason = f"主线确认：{board_name}，等载体离日低回升或回踩到位再买"
        algo_notes.append("确认中待回升")
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
    if bool(seg.get("open_mute")):
        algo_notes.append("开盘静音")
    if auction_only:
        algo_notes.append("竞价观望")
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
    action, reason, size_hint, similar_notes, similar_size_mult = apply_similar_gate(
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
    # Warm adaptive tune (contextual missed) before stock scoring uses the cache.
    adapt_bundle: dict[str, Any] = {}
    try:
        from market_desk.adapt import build_adapt_bundle

        adapt_bundle = build_adapt_bundle(
            phase=phase,
            segment_key=str(seg.get("key") or "closed"),
            metrics=metrics if isinstance(metrics, dict) else None,
            trade_date=str(now.strftime("%Y-%m-%d")),
        )
    except Exception:
        adapt_bundle = {}
    if sticky_bias:
        adapt_bundle["sticky_margin"] = sticky_bias
    # Soft size factors collected here; final product clamped in _attach_risk_sizing.
    if any("软降" in str(n) for n in review_notes):
        adapt_bundle["phase_soft_size"] = True
        adapt_bundle["phase_soft_mult"] = 0.75
    # Similar-day cool → soft size only (no action demote).
    try:
        sm = float(similar_size_mult)
    except (TypeError, ValueError):
        sm = 1.0
    if sm < 0.999:
        adapt_bundle["similar_size_mult"] = round(max(0.5, min(1.0, sm)), 3)
    # Without ETF mapping, still allow主板回踩观察票, but never as ready buys.
    allow_stocks = not stock_block and not _blocks_chi_star_stocks(board_name)
    surge_fresh = False
    try:
        from market_desk.leaders import board_surge_fresh

        surge_fresh = board_surge_fresh(main, life_stage)
        if surge_fresh:
            algo_notes.append("板块暴起·龙头排先观察持续性")
    except Exception:
        pass
    stocks = []
    if allow_stocks:
        # Track A: original constituent pullbacks (may include mid/back-row).
        stocks = _stock_candidates(
            main,
            zt or [],
            zb or [],
            boards=hot or [],
            orphan=("hard" if not exact_etf and not soft_etf else ("soft" if soft_etf else False)),
            phase=phase,
        )
    elif stock_block and action in ("可买入", "观察回踩"):
        algo_notes.append("复盘命中偏低（已改软降，个股仍可观察）")
    recommend = _build_recommend(action, main, vehicle, bounce, stocks, bans)
    if board_name and not exact_etf and not soft_etf:
        # Hard orphan: no vehicle path — stocks observe only.
        stock_n = sum(1 for x in (recommend.get("items") or []) if x.get("kind") == "stock")
        for item in recommend.get("items") or []:
            item["block_ready"] = True
            item["ready"] = False
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
            if item.get("kind") == "stock":
                item["role_label"] = "个股盯回踩"
        recommend["buy"] = False
        if stock_n:
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
        # Soft map: ETF never ready; stocks may ready after confirmations (milder orphan).
        for item in recommend.get("items") or []:
            if item.get("kind") == "etf":
                item["block_ready"] = True
                item["ready"] = False
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
                item["role_label"] = "ETF 盯回踩"
        recommend["buy"] = False
        recommend["title"] = "近似ETF · 个股可试回踩"
        recommend["size_note"] = _join_hint(
            str(recommend.get("size_note") or ""),
            f"近似映射 {vehicle.get('name') or ''}，ETF 只观察；个股经确认后可小仓",
        )
    recommend = _apply_ready_confirmations(recommend, vehicle, metrics, main=main)
    if size_hint and recommend.get("size_note"):
        recommend["size_note"] = f"{size_hint}；{recommend['size_note']}"
    elif size_hint:
        recommend["size_note"] = size_hint
    # Precompose size (incl. phase soft / similar / float cool) for playbook + qty.
    try:
        from market_desk.adapt import compose_size_mult
        from market_desk.db import load_positions
        from market_desk.settings import setting as _setting

        factors = [
            dict(f)
            for f in (adapt_bundle.get("size_factors") or [])
            if isinstance(f, dict)
        ]
        if not factors and adapt_bundle.get("size_mult") is not None:
            factors.append(
                {"key": "adapt", "label": "自适应包", "mult": float(adapt_bundle.get("size_mult") or 1.0)}
            )
        if adapt_bundle.get("phase_soft_size"):
            factors.append(
                {
                    "key": "phase_soft",
                    "label": "相位软降",
                    "mult": float(adapt_bundle.get("phase_soft_mult") or 0.75),
                }
            )
        if float(adapt_bundle.get("similar_size_mult") or 1.0) < 0.999:
            factors.append(
                {
                    "key": "similar",
                    "label": "相似日降温",
                    "mult": float(adapt_bundle.get("similar_size_mult") or 1.0),
                }
            )
        cool_n = int(_setting("cool_after_losses", 3) or 3)
        open_rows = [r for r in (load_positions() or []) if int(r.get("qty") or 0) > 0]
        losers = sum(1 for r in open_rows if (r.get("pnl_pct") or 0) < 0)
        if cool_n > 0 and losers >= cool_n:
            factors.append({"key": "float_cool", "label": f"浮亏{losers}只", "mult": 0.75})
        pre = compose_size_mult(factors)
        adapt_bundle["size_compose"] = pre
        adapt_bundle["size_mult"] = pre.get("size_mult")
        adapt_bundle["size_factors_live"] = factors
    except Exception:
        pass
    playbook = build_playbook(phase, action=action, size_hint=size_hint, adapt=adapt_bundle)
    recommend = _attach_risk_sizing(recommend, playbook=playbook, adapt=adapt_bundle)
    meta = recommend.get("risk_meta") if isinstance(recommend.get("risk_meta"), dict) else {}
    if isinstance(meta.get("size_compose"), dict):
        adapt_bundle["size_compose"] = meta["size_compose"]
        adapt_bundle["size_mult"] = meta.get("size_mult", adapt_bundle.get("size_mult"))

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

    link_info, link_recommend = _build_link_branch(
        hot=hot or [],
        main=main,
        etfs=etfs,
        zt=zt or [],
        zb=zb or [],
        bans=bans,
        stock_block=stock_block,
        recommend=recommend,
        side_info=side_info,
        orphan=("hard" if not exact_etf and not soft_etf else ("soft" if soft_etf else False)),
        phase=phase,
    )
    if link_info:
        algo_notes.append(
            f"板块联动={link_info.get('name')}({int(round(float(link_info.get('sim') or 0) * 100))}%)"
        )
        from market_desk.config import BOARD_LINK_SIZE_MULT

        link_adapt = dict(adapt_bundle)
        link_adapt["board_link"] = True
        link_m = float(BOARD_LINK_SIZE_MULT)
        bias = adapt_bundle.get("desk_source_bias") or {}
        try:
            link_m *= float(bias.get("link_mult") or 1.0)
        except (TypeError, ValueError):
            pass
        link_adapt["board_link_mult"] = round(max(0.5, min(1.0, link_m)), 3)
        link_recommend = _attach_risk_sizing(
            link_recommend or {}, playbook=playbook, adapt=link_adapt
        )
    else:
        link_recommend = None

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
    if link_info and link_info.get("name"):
        sim_pct = int(round(float(link_info.get("sim") or 0) * 100))
        why = str(link_info.get("why") or "主线暂无现买点")
        narrative = (
            narrative.rstrip("。")
            + f"；{why}，联动相似板块「{link_info.get('name')}」"
            + (f"（相似{sim_pct}%）" if sim_pct else "")
            + "，小仓盯回踩不改主线。"
        )
    if algo_notes:
        narrative = narrative.rstrip("。") + "；算法：" + "、".join(algo_notes[:5]) + "。"
    sell_themes = build_sell_themes(
        hot=hot,
        main=main,
        vehicle=vehicle,
        side_info=side_info,
        recommend=recommend,
        side_recommend=side_recommend,
        link_info=link_info,
        link_recommend=link_recommend,
        life_stage=life_stage,
    )
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
        "link_mainline": link_info,
        "link_recommend": link_recommend,
        "dragon_recommend": None,
        "independent_recommend": None,
        "sell_themes": sell_themes,
        "playbook": playbook,
        "algo_notes": algo_notes,
        "mainline": {
            "name": board_name,
            "bk": main.get("bk"),
            "kind": main.get("kind"),
            "status": status,
            "pct": main.get("pct"),
            "zt_n": main.get("zt_n"),
            "main_yi": main.get("main_yi"),
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
        "adapt": adapt_bundle,
    }
    block_arm = bool(seg.get("open_mute")) or auction_only
    out["recommend"] = mark_pullback_entries(
        out.get("recommend"), block_arm=block_arm
    )
    out["side_recommend"] = mark_pullback_entries(
        out.get("side_recommend"), observe_only=True, block_arm=block_arm
    )
    out["link_recommend"] = mark_pullback_entries(
        out.get("link_recommend"), observe_only=True, block_arm=block_arm
    )
    # Track B: emotion + mid-army dragons for sticky + side + link boards.
    try:
        from market_desk.leaders import board_surge_fresh as _board_surge_fresh

        side_board = None
        side_surge = False
        if side_info:
            side_board = _lookup_hot_board(
                hot, name=side_info.get("name"), bk=side_info.get("bk")
            )
            side_surge = _board_surge_fresh(
                side_board, side_info.get("lifecycle")
            )
        link_board = None
        link_surge = False
        if link_info:
            link_board = _lookup_hot_board(
                hot, name=link_info.get("name"), bk=link_info.get("bk")
            )
            link_surge = _board_surge_fresh(
                link_board, (link_info or {}).get("lifecycle")
            )
        dragon = build_dragon_recommend(
            main,
            zt or [],
            side_board=side_board,
            link_board=link_board,
            surge_fresh=surge_fresh,
            side_surge=side_surge,
            link_surge=link_surge,
            phase=phase,
            playbook=playbook,
            adapt=adapt_bundle,
            action=action,
        )
        if dragon:
            out["dragon_recommend"] = mark_pullback_entries(
                dragon, observe_only=False, block_arm=block_arm or surge_fresh
            )
            # Side/link dragons stay observe even if mainline gates arm.
            for it in (out["dragon_recommend"].get("items") or []):
                if str(it.get("dragon_scope") or "main") != "main":
                    it["ready"] = False
            algo_notes.append(f"龙头排={(len((dragon.get('items') or [])))}只")
            out["algo_notes"] = algo_notes
    except Exception:
        out["dragon_recommend"] = None
    # Independent popular pullback: observe-only module on sticky mainline pool.
    try:
        skip_extra = {
            normalize_code(x.get("code"))
            for x in ((out.get("dragon_recommend") or {}).get("items") or [])
            if x.get("code")
        }
        indep = build_independent_pullback_recommend(
            main,
            recommend=out.get("recommend"),
            skip_codes=skip_extra,
            playbook=playbook,
            adapt=adapt_bundle,
        )
        if indep:
            out["independent_recommend"] = mark_pullback_entries(
                indep, observe_only=True, block_arm=block_arm
            )
            algo_notes.append(
                f"独立人气回踩={(len((indep.get('items') or [])))}只"
            )
            out["algo_notes"] = algo_notes
    except Exception:
        out["independent_recommend"] = None
    out = reconfirm_recommend_ready(out, metrics)
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


def _hero_buy_locked(verdict: dict[str, Any]) -> bool:
    """Return True when market/segment gates forbid upgrading hero to 可买入."""
    from market_desk.config import BUY_DEMOTE_LOCK_NOTES

    notes = [str(x) for x in (verdict.get("algo_notes") or [])]
    for token in BUY_DEMOTE_LOCK_NOTES:
        if any(token in n for n in notes):
            return True
    seg = verdict.get("segment") or {}
    if seg.get("open_mute") or str(seg.get("key") or "") == "auction":
        return True
    if bool(verdict.get("auction_only")):
        return True
    return False


def reconfirm_recommend_ready(
    verdict: dict[str, Any] | None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-run day-high / flow / thin-confirm gates after near-entry arming."""
    v = dict(verdict or {})
    carrier = v.get("carrier") or {}
    main = v.get("mainline") or {}
    v["recommend"] = _apply_ready_confirmations(
        v.get("recommend") or {},
        carrier,
        metrics,
        main=main,
    )
    return v


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

    buy_locked = _hero_buy_locked(v)
    if action in ("观察回踩", "观察") and any_entry and not buy_locked:
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
    if action in ("观察回踩", "观察") and any_entry and buy_locked:
        # Keep near-entry badge but do not upgrade hero past market/segment locks.
        notes = list(v.get("algo_notes") or [])
        if "到位·闸门未开" not in notes:
            notes.append("到位·闸门未开")
        v["algo_notes"] = notes
        rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), "已到建议买，但市场闸门未开")
        v["recommend"] = rec
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
        can_probe: True when a mainline card is near-entry probe (soft half size).
        reasons: Up to five short Chinese strings explaining why live buy
            (现买) is blocked; empty when ``can_buy`` is True.
        hint: Single-line label suitable for the top banner.
        progress: Best near-entry card's buy_progress (if any).
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
    probe_items = [
        x
        for x in items
        if bool(x.get("probe_ok")) and not x.get("block_ready")
    ]
    near_items = [
        x
        for x in items
        if bool(x.get("near_entry")) and not x.get("block_ready")
    ]
    buyable_action = action in _BUY_ACTIONS
    can_buy = buyable_action and bool(ready_items)
    can_probe = (not can_buy) and bool(probe_items)

    def _progress_of(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
        for x in pool:
            bp = x.get("buy_progress")
            if isinstance(bp, dict) and bp:
                return bp
        return None

    progress = (
        _progress_of(ready_items)
        or _progress_of(probe_items)
        or _progress_of(near_items)
    )

    if can_buy:
        return {
            "action": action,
            "can_buy": True,
            "can_probe": False,
            "reasons": [],
            "hint": _desk_gate_buy_hint(action, ready_items, rec),
            "progress": progress,
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
    miss = list((progress or {}).get("missing") or [])
    miss_txt = " · ".join(str(x) for x in miss[:3] if x)

    if can_probe:
        _add("已触建议价，可小仓试探")
        if miss_txt:
            _add(f"全仓还差：{miss_txt}")
    elif near_items:
        _add("已触建议价，现买确认未齐")
        if miss_txt:
            _add(f"还差：{miss_txt}")
    elif action == "观望":
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
        _add("个股通道关闭（非硬禁）")
    elif any("缩仓加严" in str(x) for x in (v.get("review_hints") or [])):
        _add("复盘相位命中偏低·个股软降级")

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
        for flag in hard_confirm_fails(item.get("confirm_fail") or []):
            fail_counts[flag] = fail_counts.get(flag, 0) + 1
        for flag in item.get("confirm_soft") or []:
            text = str(flag).strip()
            if text and text not in fail_counts:
                # Soft notes go later; count for display only if space.
                pass
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

    if pending_minute and "分时" not in "".join(reasons):
        _add("分时未验（软，不关现买）")

    if not ready_items and not can_probe:
        if near_only_n and not miss_txt:
            _add("已触及建议价但未过现买确认")
        elif items and not fail_counts and blocked_n == 0 and not near_items:
            _add("卡片未到位，盯回踩价")

    dont = str(playbook.get("dont") or "").strip()
    if dont and len(reasons) < 5:
        short = dont.replace("不做：", "").split("、")[0][:22]
        if short:
            _add(f"纪律：{short}")

    if can_probe:
        if miss_txt:
            hint = f"靠近买点·可小仓试探 · 还差{miss_txt}"
        else:
            hint = "靠近买点·可小仓试探"
    elif near_items and miss_txt:
        hint = f"靠近买点 · 还差{miss_txt}"
    elif near_items:
        hint = "靠近买点 · 现买确认未齐"
    else:
        hint = _desk_gate_block_hint(action, reasons, rec, mainline)
    return {
        "action": action,
        "can_buy": False,
        "can_probe": can_probe,
        "reasons": reasons[:5],
        "hint": hint,
        "progress": progress,
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
        "pool_codes": _pool_codes_from_board(side),
    }
    return info, rec


def _mainline_needs_link(recommend: dict[str, Any] | None) -> tuple[bool, str]:
    """Decide whether soft sibling-board cards should surface.

    Triggers when sticky mainline has no ready entry, or every priced card hugs
    the chase band (overheated / near 不追价) even if some ready flags linger.
    """
    from market_desk.config import BOARD_LINK_CHASE_RATIO

    items = list((recommend or {}).get("items") or [])
    if not items:
        return True, "主线暂无卡片"
    if not any(bool(i.get("ready")) for i in items):
        return True, "主线暂无现买点"
    priced = 0
    at_chase = 0
    for item in items:
        last = item.get("last")
        chase = item.get("chase_price")
        if last is None or chase is None:
            continue
        try:
            priced += 1
            if float(last) >= float(chase) * float(BOARD_LINK_CHASE_RATIO):
                at_chase += 1
        except (TypeError, ValueError):
            continue
    if priced > 0 and at_chase >= priced:
        return True, "主线全贴不追价/过热"
    return False, ""


def _build_link_branch(
    *,
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any] | None,
    etfs: list[dict[str, Any]] | None,
    zt: list[dict[str, Any]],
    zb: list[dict[str, Any]],
    bans: list[str],
    stock_block: bool,
    recommend: dict[str, Any] | None,
    side_info: dict[str, Any] | None = None,
    orphan: bool | str = False,
    phase: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build soft sibling-board cards when sticky mainline lacks a calm entry.

    Does not switch sticky mainline or upgrade hero action; cards stay observation
    path with an extra size damp (BOARD_LINK_SIZE_MULT).
    """
    from market_desk.config import BOARD_LINK_MAX_STOCKS, BOARD_LINK_SIM_MIN, BOARD_LINK_SIZE_MULT

    main = main or {}
    main_name = str(main.get("name") or "").strip()
    if not main_name or stock_block:
        return None, None
    need_link, link_why = _mainline_needs_link(recommend)
    if not need_link:
        return None, None

    peers = list(main.get("similar_peers") or [])
    if not peers:
        return None, None
    by_name = {
        str(b.get("name") or "").strip(): b
        for b in (hot or [])
        if str(b.get("name") or "").strip()
    }
    side_name = str((side_info or {}).get("name") or "").strip()
    chosen: dict[str, Any] | None = None
    chosen_sim = 0.0
    best = -1.0
    for peer in peers:
        name = str(peer.get("name") or "").strip()
        if not name or name == main_name or name == side_name:
            continue
        try:
            sim = float(peer.get("sim") or 0)
        except (TypeError, ValueError):
            sim = 0.0
        if sim < float(BOARD_LINK_SIM_MIN):
            continue
        board = by_name.get(name)
        if not board:
            continue
        status = str(board.get("status") or "")
        if status in ("尖峰禁追", "退潮"):
            continue
        if _blocks_chi_star_stocks(name):
            continue
        rank = sim + (0.12 if status == "确认中" else 0.0) + min(
            float(board.get("zt_n") or 0) * 0.01, 0.08
        )
        if rank > best:
            best = rank
            chosen = board
            chosen_sim = sim
    if not chosen:
        return None, None

    peer_name = str(chosen.get("name") or "").strip()
    exact = etf_spec_for_name(peer_name)
    soft = False
    vehicle = match_mainline_etf(peer_name, etfs) if exact else None
    if not vehicle:
        soft_spec = etf_spec_soft_fallback(peer_name)
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

    allow_stocks = not _blocks_chi_star_stocks(peer_name)
    stocks = (
        _stock_candidates(
            chosen, zt, zb, boards=hot or [], orphan=orphan, phase=phase
        )
        if allow_stocks
        else []
    )
    skip = set(_recommend_codes(recommend))
    stocks = [s for s in stocks if normalize_code(s.get("code")) not in skip][
        : int(BOARD_LINK_MAX_STOCKS)
    ]
    if not stocks and not vehicle.get("code"):
        return None, None

    rec = _build_recommend("观察回踩", chosen, vehicle, bounce, stocks, bans)
    for item in rec.get("items") or []:
        item["ready"] = False
        item["link_board"] = True
        item["link_sim"] = round(chosen_sim, 2)
        if item.get("wait_price") is not None:
            item["buy_price"] = item.get("wait_price")
        kind = item.get("kind") or "stock"
        item["role_label"] = (
            f"联动ETF·{peer_name}" if kind == "etf" else f"联动·{peer_name}"
        )
        reason = str(item.get("reason") or "")
        tip = f"相似主线 {main_name}（{int(round(chosen_sim * 100))}%）"
        item["reason"] = f"{tip}；{reason}" if reason else tip
    rec["buy"] = False
    rec["link"] = True
    rec["link_sim"] = round(chosen_sim, 2)
    rec["link_why"] = link_why
    rec["title"] = f"板块联动 · {peer_name}"
    rec["size_note"] = _join_hint(
        f"{link_why}；相似板块回踩可小仓（建议再×{BOARD_LINK_SIZE_MULT:g}），不改 sticky 主线",
        f"相似 {int(round(chosen_sim * 100))}%"
        + (f" · 载体近似 {vehicle.get('name')}" if soft and vehicle.get("name") else ""),
    )
    if not (rec.get("items") or []):
        return None, None

    life = classify_lifecycle(chosen)
    info = {
        "name": peer_name,
        "bk": chosen.get("bk"),
        "kind": chosen.get("kind"),
        "status": chosen.get("status"),
        "pct": chosen.get("pct"),
        "zt_n": chosen.get("zt_n"),
        "leader_name": chosen.get("leader_name"),
        "leader_code": chosen.get("leader_code"),
        "lifecycle": life,
        "score": chosen.get("score"),
        "sim": round(chosen_sim, 2),
        "etf_soft": soft,
        "carrier_code": vehicle.get("code"),
        "carrier_name": vehicle.get("name"),
        "pool_codes": _pool_codes_from_board(chosen),
        "mainline_name": main_name,
        "why": link_why,
    }
    return info, rec


def _pool_codes_from_board(board: dict[str, Any] | None) -> list[str]:
    """Collect normalized member codes from a hot-board card."""
    out: list[str] = []
    for m in (board or {}).get("pool") or (board or {}).get("members") or []:
        raw = m.get("code") if isinstance(m, dict) else m
        code = normalize_code(raw)
        if code and code not in out:
            out.append(code)
    return out[:40]


def _recommend_codes(recommend: dict[str, Any] | None) -> list[str]:
    """Collect codes from a recommend / side_recommend payload."""
    out: list[str] = []
    for item in ((recommend or {}).get("items") or []):
        code = normalize_code(item.get("code"))
        if code and code not in out:
            out.append(code)
    return out


def _theme_entry(
    *,
    name: str,
    role: str,
    status: str,
    lifecycle: str | None,
    pool_codes: list[str] | None = None,
    carrier_code: str | None = None,
    rec_codes: list[str] | None = None,
    score: float | None = None,
    main_yi: float | None = None,
) -> dict[str, Any]:
    """Build one sell-theme descriptor (buy path never reads this list)."""
    return {
        "name": name,
        "role": role,
        "status": status or "",
        "lifecycle": lifecycle or "",
        "pool_codes": list(pool_codes or [])[:40],
        "carrier_code": normalize_code(carrier_code) if carrier_code else None,
        "rec_codes": list(rec_codes or [])[:20],
        "score": score,
        "main_yi": main_yi,
    }


def build_sell_themes(
    *,
    hot: list[dict[str, Any]] | None,
    main: dict[str, Any] | None,
    vehicle: dict[str, Any] | None,
    side_info: dict[str, Any] | None,
    recommend: dict[str, Any] | None = None,
    side_recommend: dict[str, Any] | None = None,
    link_info: dict[str, Any] | None = None,
    link_recommend: dict[str, Any] | None = None,
    life_stage: str | None = None,
) -> list[dict[str, Any]]:
    """Build multi-theme set for sells; buys still follow sticky mainline only.

    Includes sticky primary, observation side branch, soft link peer, and other
    hot boards within ``SELL_THEME_GAP`` of the sticky score (退潮 peers kept so
    residual positions can still soft-exit on their own theme fade).
    """
    from market_desk.config import SELL_THEME_GAP, SELL_THEME_MAX

    main = main or {}
    vehicle = vehicle or {}
    primary = str(main.get("name") or "").strip()
    themes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(entry: dict[str, Any]) -> None:
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            return
        if len(themes) >= int(SELL_THEME_MAX):
            return
        seen.add(name)
        themes.append(entry)

    if primary:
        _add(
            _theme_entry(
                name=primary,
                role="primary",
                status=str(main.get("status") or ""),
                lifecycle=life_stage or classify_lifecycle(main),
                pool_codes=_pool_codes_from_board(main),
                carrier_code=vehicle.get("code"),
                rec_codes=_recommend_codes(recommend),
                score=mainline_score(main) if main else None,
                main_yi=main.get("main_yi"),
            )
        )

    side = side_info or {}
    side_name = str(side.get("name") or "").strip()
    if side_name:
        _add(
            _theme_entry(
                name=side_name,
                role="side",
                status=str(side.get("status") or ""),
                lifecycle=side.get("lifecycle") or "",
                pool_codes=list(side.get("pool_codes") or []),
                carrier_code=side.get("carrier_code"),
                rec_codes=_recommend_codes(side_recommend),
                score=side.get("score"),
                main_yi=side.get("main_yi"),
            )
        )

    link = link_info or {}
    link_name = str(link.get("name") or "").strip()
    if link_name:
        _add(
            _theme_entry(
                name=link_name,
                role="link",
                status=str(link.get("status") or ""),
                lifecycle=link.get("lifecycle") or "",
                pool_codes=list(link.get("pool_codes") or []),
                carrier_code=link.get("carrier_code"),
                rec_codes=_recommend_codes(link_recommend),
                score=link.get("score"),
                main_yi=link.get("main_yi"),
            )
        )

    gap = float(SELL_THEME_GAP)
    boards = list(hot or [])
    industries = [b for b in boards if b.get("kind") == "industry"]
    pool = industries or boards
    main_score = mainline_score(main) if primary else 0.0
    # Pass 1: competitive peers within gap. Pass 2: fading leftovers (any gap).
    ranked = sorted(pool, key=mainline_score, reverse=True)

    def _maybe_add(board: dict[str, Any], *, allow_outside_fade: bool) -> None:
        name = str(board.get("name") or "").strip()
        if not name or name in seen:
            return
        sc = mainline_score(board)
        status = str(board.get("status") or "")
        life = classify_lifecycle(board)
        outside = bool(primary) and (main_score - sc) > gap
        fading = status == "退潮" or life == "ending"
        if outside and not (allow_outside_fade and fading):
            return
        _add(
            _theme_entry(
                name=name,
                role="hot",
                status=status,
                lifecycle=life,
                pool_codes=_pool_codes_from_board(board),
                carrier_code=None,
                score=round(sc, 1),
                main_yi=board.get("main_yi"),
            )
        )

    for board in ranked:
        _maybe_add(board, allow_outside_fade=False)
    for board in ranked:
        _maybe_add(board, allow_outside_fade=True)
    return themes


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


def build_watch_trial_recommend(
    watchlist: list[dict[str, Any]] | None,
    *,
    playbook: dict[str, Any] | None = None,
    adapt: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build observe-only desk cards for watchlist rows marked 可试探.

    Same soft tier as board linkage: does not upgrade hero action or sticky mainline.
    """
    from market_desk.config import WATCH_TRIAL_MAX_ITEMS, WATCH_TRIAL_SIZE_MULT

    items: list[dict[str, Any]] = []
    for row in watchlist or []:
        if str(row.get("observe_status") or "") != "可试探":
            continue
        code = normalize_code(row.get("code"))
        if not code:
            continue
        etf = _is_etf_code(code)
        digits = 3 if etf else 2
        last = row.get("last")
        suggest = row.get("suggest_price")
        stop = row.get("stop_price")
        chase = row.get("chase_price")
        buy = suggest if suggest not in (None, "") else last
        kind = "etf" if etf else "stock"
        note = str(row.get("observe_note") or "靠近建议价且作战台未锁买")
        items.append(
            {
                "kind": kind,
                "kind_label": "ETF" if etf else "个股",
                "role": "alt",
                "role_label": "自选·可试探",
                "code": code,
                "name": row.get("name") or code,
                "last": _px(last, digits),
                "pct": None
                if row.get("last_pct") is None
                else round(float(row["last_pct"]), 2),
                "buy_price": _px(buy, digits),
                "wait_price": _px(suggest, digits),
                "stop_price": _px(stop, digits),
                "chase_price": _px(chase, digits),
                "ready": False,
                "watch_trial": True,
                "reason": f"{note}；小仓试探，不改顶栏结论",
                "qty": 100,
            }
        )
        if len(items) >= int(WATCH_TRIAL_MAX_ITEMS):
            break
    if not items:
        return None
    rec: dict[str, Any] = {
        "title": "自选可试探",
        "text": f"自选可试探 · {len(items)}只",
        "buy": False,
        "watch_trial": True,
        "items": items,
        "size_note": (
            f"观察页「可试探」同步副卡；建议再×{float(WATCH_TRIAL_SIZE_MULT):g}，"
            "不改 sticky 主线 / 顶栏"
        ),
    }
    trial_adapt = dict(adapt or {})
    trial_adapt["watch_trial"] = True
    try:
        base = float(WATCH_TRIAL_SIZE_MULT)
        damp = float((adapt or {}).get("desk_source_bias", {}).get("trial_mult") or 1.0)
        trial_adapt["watch_trial_mult"] = round(max(0.5, min(1.0, base * damp)), 3)
    except (TypeError, ValueError):
        trial_adapt["watch_trial_mult"] = float(WATCH_TRIAL_SIZE_MULT)
    return _attach_risk_sizing(rec, playbook=playbook, adapt=trial_adapt)


def _lookup_hot_board(
    hot: list[dict[str, Any]] | None,
    *,
    name: str | None = None,
    bk: Any = None,
) -> dict[str, Any] | None:
    """Resolve a full board card from the hot list by name or bk code."""
    want_name = str(name or "").strip()
    want_bk = str(bk or "").strip()
    for card in hot or []:
        if want_bk and str(card.get("bk") or "").strip() == want_bk:
            return card
        if want_name and str(card.get("name") or "").strip() == want_name:
            return card
    return None


def _dragon_items_for_board(
    board: dict[str, Any] | None,
    zt: list[dict[str, Any]] | None,
    *,
    scope: str,
    phase: str = "",
    surge_fresh: bool = False,
    observe_only: bool = False,
    skip_codes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Build emotion/mid-army dragon cards for one board with scope tags."""
    from market_desk.leaders import build_dual_dragon_stocks

    if not board or not str(board.get("name") or "").strip():
        return []
    raw = build_dual_dragon_stocks(
        board, zt, surge_fresh=surge_fresh, phase=phase
    )
    board_name = str(board.get("name") or "").strip()
    scope_prefix = {
        "main": "",
        "side": "支线·",
        "link": "联动·",
    }.get(scope, "")
    skip = {normalize_code(c) for c in (skip_codes or set()) if normalize_code(c)}
    items: list[dict[str, Any]] = []
    for row in raw:
        code = normalize_code(row.get("code"))
        if not code or code in skip:
            continue
        ready = bool(row.get("ready")) and not surge_fresh and not observe_only
        item = _recommend_item(
            row,
            kind="stock",
            role="alt",
            ready=ready,
            reason=str(row.get("reason") or "龙头观察"),
        )
        base_role = str(row.get("role_label") or "龙头")
        item["role_label"] = f"{scope_prefix}{base_role}" if scope_prefix else base_role
        item["dragon_kind"] = row.get("dragon_kind")
        item["dragon_why"] = row.get("dragon_why")
        item["desk_source"] = str(row.get("desk_source") or "dragon")
        item["dragon_row"] = True
        item["dragon_scope"] = scope
        item["source_board"] = board_name
        # Keep selection rationale first if _recommend_item only got timing text.
        why = str(row.get("dragon_why") or "").strip()
        if why and why not in str(item.get("reason") or ""):
            item["reason"] = f"{why}。{item.get('reason') or ''}".strip("。")
        if surge_fresh or observe_only:
            item["ready"] = False
            if item.get("wait_price") is not None:
                item["buy_price"] = item.get("wait_price")
        if observe_only and scope != "main":
            item["reason"] = _join_hint(
                str(item.get("reason") or ""),
                f"{'支线' if scope == 'side' else '联动'}龙头仅观察，不升顶栏",
            )
        items.append(item)
        skip.add(code)
    return items


def build_dragon_recommend(
    main: dict[str, Any] | None,
    zt: list[dict[str, Any]] | None,
    *,
    side_board: dict[str, Any] | None = None,
    link_board: dict[str, Any] | None = None,
    surge_fresh: bool = False,
    side_surge: bool = False,
    link_surge: bool = False,
    phase: str = "",
    playbook: dict[str, Any] | None = None,
    adapt: dict[str, Any] | None = None,
    action: str = "",
) -> dict[str, Any] | None:
    """Build one desk dragon row covering sticky + side + link boards.

    Mainline dragons may arm ready after gates; side/link dragons stay observe-only.
    Codes already used on a higher-priority board are skipped (main > side > link).
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for scope, board, surge, observe in (
        ("main", main, surge_fresh, False),
        ("side", side_board, side_surge, True),
        ("link", link_board, link_surge, True),
    ):
        chunk = _dragon_items_for_board(
            board,
            zt,
            scope=scope,
            phase=phase,
            surge_fresh=surge,
            observe_only=observe,
            skip_codes=seen,
        )
        for it in chunk:
            code = normalize_code(it.get("code"))
            if code:
                seen.add(code)
            items.append(it)
    if not items:
        return None
    scopes = sorted({str(x.get("dragon_scope") or "main") for x in items})
    tip = (
        "情绪龙=涨停高度梯队（连板→封单→成交）；中军龙=成分成交额+市值核心（可不涨停）。"
        "情绪看确认异动，中军看趋势回踩；主线可到位，支线/联动只观察。"
    )
    if surge_fresh or side_surge or link_surge:
        tip += " 暴起当日该板龙头只观察。"
    bits = [f"{len(items)}只"]
    if "side" in scopes:
        bits.append("含支线")
    if "link" in scopes:
        bits.append("含联动")
    rec: dict[str, Any] = {
        "title": "龙头（情绪/中军）",
        "text": f"龙头排 · {' · '.join(bits)}"
        + (f" · 主线动作 {action}" if action else ""),
        "buy": any(x.get("ready") for x in items),
        "dragon_row": True,
        "items": items,
        "size_note": tip,
        "scopes": scopes,
    }
    dragon_adapt = dict(adapt or {})
    dragon_adapt["dragon_row"] = True
    return _attach_risk_sizing(rec, playbook=playbook, adapt=dragon_adapt)


def build_independent_pullback_recommend(
    main: dict[str, Any] | None,
    *,
    recommend: dict[str, Any] | None = None,
    skip_codes: set[str] | None = None,
    playbook: dict[str, Any] | None = None,
    adapt: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build observe-only cards for mainline independent popular pullbacks.

    Does not upgrade hero action / sticky. Sell path may exempt「昨买今弱」.
    """
    from market_desk.config import INDEPENDENT_POP_MAX
    from market_desk.leaders import build_independent_pullback_candidates

    skip = {
        normalize_code(x.get("code"))
        for x in ((recommend or {}).get("items") or [])
        if x.get("kind") == "stock" and x.get("code")
    }
    for c in skip_codes or set():
        cc = normalize_code(c)
        if cc:
            skip.add(cc)
    raw = build_independent_pullback_candidates(
        main, skip_codes=skip, max_items=int(INDEPENDENT_POP_MAX)
    )
    items: list[dict[str, Any]] = []
    for row in raw:
        code = normalize_code(row.get("code"))
        if not code:
            continue
        last = row.get("price") or row.get("last")
        items.append(
            {
                "kind": "stock",
                "kind_label": "个股",
                "role": "alt",
                "role_label": "独立人气·回踩",
                "code": code,
                "name": row.get("name") or code,
                "last": _px(last, 2),
                "pct": None if row.get("pct") is None else round(float(row["pct"]), 2),
                "buy_price": _px(last, 2),
                "wait_price": _px(row.get("low") or last, 2),
                "stop_price": _px(
                    (float(last) * 0.97) if last not in (None, 0) else None, 2
                ),
                "chase_price": _px(
                    (float(last) * 1.03) if last not in (None, 0) else None, 2
                ),
                "ready": False,
                "independent_pop": True,
                "desk_source": "independent_pop",
                "reason": str(row.get("reason") or "主线板内独立人气·近低回踩观察"),
                "qty": 100,
                "high": row.get("high"),
                "low": row.get("low"),
                "mv_yi": row.get("mv_yi"),
            }
        )
    if not items:
        return None
    rec: dict[str, Any] = {
        "title": "独立人气回踩",
        "text": f"独立人气回踩 · {len(items)}只",
        "buy": False,
        "independent_pop": True,
        "items": items,
        "size_note": (
            "主线板内走独立行情、近低回踩观察；不升顶栏可买入。"
            "记仓后豁免「昨买今弱」轻减，破近5日低/止损仍提醒。"
        ),
    }
    indep_adapt = dict(adapt or {})
    indep_adapt["independent_pop"] = True
    return _attach_risk_sizing(rec, playbook=playbook, adapt=indep_adapt)


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
    """Use historical phase hit-rate and adaptive size heat to shrink size.

    Soft DSS: low phase hit-rate demotes action and size hints but never hard-bans
    the stock pool (``stock_block`` stays False). Hard bans remain blacklist /
    panic / size-cap only.
    """
    notes: list[str] = []
    stock_block = False  # kept for API compat; no longer hard-blocks stocks
    hint = size_hint or ""
    act = action
    why = reason
    try:
        from market_desk.db import load_signals
        from market_desk.review import build_phase_hit_rates

        hits = build_phase_hit_rates(load_signals(limit=240))
    except Exception:
        hits = []
    row = next((h for h in hits if str(h.get("phase") or "") == str(phase or "")), None)
    if row and int(row.get("scored_n") or 0) >= 5 and row.get("hit_rate") is not None:
        rate = float(row["hit_rate"])
        scored = int(row["scored_n"])
        if rate < 35:
            hint = _join_hint(
                hint,
                f"相位{phase}命中{rate}%（n={scored}）偏低·缩仓加严（个股仍可观察）",
            )
            notes.append(f"复盘命中{rate}%·软降")
            if act == "可买入":
                act = "观察回踩"
                why = f"相位{phase}历史命中偏低({rate}%)，降级观察回踩：{why}"
        elif rate < 45:
            hint = _join_hint(hint, f"相位{phase}命中{rate}%一般，偏小仓")
            notes.append(f"复盘命中{rate}%偏弱")
    try:
        from market_desk.adapt import build_size_heat

        heat = build_size_heat()
        if heat.get("ok") and abs(float(heat.get("size_mult") or 1.0) - 1.0) > 0.02:
            hint = _join_hint(hint, str(heat.get("note") or f"仓位热度×{heat.get('size_mult')}"))
            notes.append(f"仓位热度×{heat.get('size_mult')}")
    except Exception:
        pass
    return act, why, hint, stock_block, notes


def apply_similar_gate(
    action: str,
    reason: str,
    size_hint: str,
    *,
    similar: dict[str, Any] | None,
) -> tuple[str, str, str, list[str], float]:
    """Apply similar-day cool/hot as soft size bias (does not demote action).

    Returns ``(action, reason, size_hint, notes, size_mult)``. Cool days shrink
    suggested risk size; hot days only add chase-caution copy. Sell-side
    ``sell_gate`` remains unchanged elsewhere.
    """
    notes: list[str] = []
    sim = similar or {}
    hint = size_hint or ""
    act = action
    why = reason
    size_mult = 1.0
    bias = str(sim.get("bias") or "").strip()
    gate = sim.get("gate")
    sell_gate = str(sim.get("sell_gate") or "")
    n = int(sim.get("n") or 0)
    if bias:
        hint = _join_hint(hint, bias)
    if n:
        notes.append(f"相似日n={n}")
    if gate == "cool" and n >= 2:
        # Prefer size over action demote; urgent cool presses a bit harder.
        size_mult = 0.65 if sell_gate == "urgent" else 0.75
        notes.append(f"相似日降温·缩仓×{size_mult}")
        hint = _join_hint(hint, f"相似日降温·建议仓位×{size_mult}")
    elif gate == "hot" and act == "可买入":
        hint = _join_hint(hint, "相似日偏升温仍防追高")
        notes.append("相似日升温防追")
    return act, why, hint, notes, size_mult


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


# Soft confirm flags: score/size only — never hard-kill ready / never block arm.
_SOFT_CONFIRM_FLAGS = frozenset(
    {
        "日线非上升",
        "分时样本不足",
        "分时未验",
    }
)


def is_soft_confirm_flag(flag: str) -> bool:
    """Return True when a confirm_fail / soft label is advisory only."""
    text = str(flag or "").strip()
    if not text:
        return False
    if text in _SOFT_CONFIRM_FLAGS:
        return True
    if text.startswith("分时样本"):
        return True
    return False


def hard_confirm_fails(flags: list[Any] | None) -> list[str]:
    """Filter confirm_fail down to hard gates that block ready / arming."""
    out: list[str] = []
    for raw in flags or []:
        text = str(raw or "").strip()
        if text and not is_soft_confirm_flag(text):
            out.append(text)
    return out


def _scale_item_qty(item: dict[str, Any], mult: float, tip: str) -> None:
    """Lot-round scale qty / risk_plan after soft probe or trend damp."""
    try:
        qty = int(item.get("qty") or 0)
    except (TypeError, ValueError):
        return
    if qty <= 0 or abs(float(mult) - 1.0) < 0.01:
        return
    new_qty = max(100, int(round(qty * float(mult) / 100.0) * 100))
    item["qty"] = new_qty
    plan = item.get("risk_plan")
    if isinstance(plan, dict):
        plan = dict(plan)
        plan["qty"] = new_qty
        plan["note"] = _join_hint(str(plan.get("note") or ""), tip)
        item["risk_plan"] = plan


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
    Hard confirm fails / block_ready / trend_down still forbid probe.
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
        if hard_confirm_fails(item.get("confirm_fail") or []):
            items.append(item)
            continue
        # Soft path: price tagged, hard gates clear, full ready not armed yet.
        item["probe_ok"] = True
        any_probe = True
        kind = item.get("kind") or "stock"
        item["role_label"] = "ETF·可试探" if kind == "etf" else "主线·可试探"
        _scale_item_qty(item, float(PROBE_SIZE_MULT), "主线可试探·半仓")
        item["probe_size_mult"] = round(float(PROBE_SIZE_MULT), 3)
        item["reason"] = _join_hint(
            str(item.get("reason") or ""),
            "靠近建议价，可小仓试探（不升顶栏可买入）",
        )
        items.append(item)
    rec["items"] = items
    if any_probe:
        rec["probe"] = True
        rec["size_note"] = _join_hint(
            str(rec.get("size_note") or ""),
            "主线可试探·建议半仓，不改顶栏可买入",
        )
    return rec


def finalize_recommend_buy_ux(
    recommend: dict[str, Any] | None,
    *,
    block_arm: bool = False,
    allow_probe: bool = True,
) -> dict[str, Any]:
    """Apply mainline probe (optional) then attach buy-progress checklists."""
    rec = dict(recommend or {})
    if allow_probe:
        rec = apply_mainline_probe(rec, block_arm=block_arm)
    else:
        for raw in rec.get("items") or []:
            raw["probe_ok"] = False
    return attach_buy_progress(rec)


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
    gate_bias: dict[str, Any] = {}
    try:
        from market_desk.adapt import resolve_gate_mult
        from market_desk.review import cached_buy_gate_bias

        gate_bias = cached_buy_gate_bias() or {}
        off_mult = float(resolve_gate_mult("off_high", 1.0))
        thin_mult = float(resolve_gate_mult("thin", 1.0))
    except Exception:
        gate_bias = {}
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
    """Attach daily-trend labels, nudge scores, and soft/hard ready gates.

    Stocks: clear up +bonus; clear down hard-gates ready; sideways soft score+size.
    ETF: clear up +bonus; clear down gates ready; sideways score-only (no gate).
    Missing/thin kline → ``trend_pending`` visible, no ready gate / score nudge.
    """
    del overrides  # Manual overrides removed; trend is informational + ready gate.
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
    orphan: bool | str = False,
    phase: str = "",
) -> list[dict[str, Any]]:
    """Pick unsealed main-board pullbacks from the live mainline constituent pool.

    ``orphan``: False | True/"hard" | "soft". Hard = no exact ETF (strictest);
    soft = approximate ETF map (milder listing band).
    """
    from market_desk.db import load_blacklist_codes

    orphan_mode = "hard" if orphan in (True, "hard") else ("soft" if orphan == "soft" else "")
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
    board_pct = main.get("pct")
    board_flow = main.get("main_yi")
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
            orphan=orphan_mode,
            board_pct=board_pct,
            board_main_yi=board_flow,
            phase=phase,
        )
        if item:
            scored.append(item)
    if len(scored) < 2 and orphan_mode != "hard":
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
                orphan="",
                board_pct=board_pct,
                board_main_yi=board_flow,
                phase=phase,
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
    orphan: bool | str = False,
    board_pct: float | None = None,
    board_main_yi: float | None = None,
    phase: str = "",
) -> tuple[float, dict[str, Any]] | None:
    """Score one constituent as a pullback candidate, or reject it."""
    from market_desk.config import (
        ORPHAN_STOCK_MV_MULT,
        ORPHAN_STOCK_PB_MIN,
        SOFT_STOCK_MV_MULT,
        SOFT_STOCK_PB_MIN,
        STOCK_FLOW_OUT_SCORE_PEN,
        STOCK_PULLBACK_BAND_MAX,
        STOCK_PULLBACK_SWEET_MAX,
        STOCK_READY_PULLBACK_MIN,
        STOCK_VS_BOARD_WEAK_PCT,
    )
    from market_desk.settings import setting

    orphan_mode = "hard" if orphan in (True, "hard") else ("soft" if orphan == "soft" else "")
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
    from market_desk.config import STOCK_MV_HARD_MIN_YI

    min_mv = max(min_mv, float(STOCK_MV_HARD_MIN_YI))
    if orphan_mode == "hard" and min_mv > 0:
        min_mv *= float(ORPHAN_STOCK_MV_MULT)
    elif orphan_mode == "soft" and min_mv > 0:
        min_mv *= float(SOFT_STOCK_MV_MULT)
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
    from market_desk.leaders import stock_liquidity_ok

    if not stock_liquidity_ok(
        None if mv_yi is None else float(mv_yi), turnover, sealed=False
    ):
        return None
    # Overheated turnover: skip hard; elevated turnover: no ready buys.
    if turnover is not None and turnover >= 25.0:
        return None
    if strict and (pct < 0 or pct > 5.5):
        return None
    if not strict and (pct < -1.5 or pct > 7.0):
        return None

    if orphan_mode == "hard":
        pb_min = float(ORPHAN_STOCK_PB_MIN)
    elif orphan_mode == "soft":
        pb_min = float(SOFT_STOCK_PB_MIN)
    else:
        pb_min = float(STOCK_READY_PULLBACK_MIN)
    # Gate loosen/tighten applies ONLY via auto_tune (single channel, ±CLAMP).
    pb_max = float(STOCK_PULLBACK_BAND_MAX)
    pb_sweet = float(STOCK_PULLBACK_SWEET_MAX)
    try:
        from market_desk.adapt import build_pullback_sweet, resolve_auto_tune
        from market_desk import adapt as adapt_mod

        ctx = adapt_mod._ADAPT_CACHE.get("context")
        if not isinstance(ctx, dict):
            ctx = None
        tune = resolve_auto_tune(current_context=ctx)
        t_mult = float(tune.get("pb_min_mult") or 1.0)
        if abs(t_mult - 1.0) > 0.01:
            pb_min = float(pb_min) * t_mult
        sweet = build_pullback_sweet()
        if sweet.get("ok"):
            pb_sweet = float(sweet.get("sweet_max") or pb_sweet)
            pb_max = float(sweet.get("band_max") or pb_max)
    except Exception:
        pass
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
    try:
        from market_desk.adapt import stock_rep_score_adj

        srep = stock_rep_score_adj(code)
        if srep.get("ok"):
            score += float(srep.get("score_adj") or 0)
            if srep.get("watch") and strict:
                # Watch-list names need deeper pullback confirmation.
                if pullback is None or pullback < max(pb_min + 0.8, pb_min * 1.15):
                    return None
                score -= 3.0
    except Exception:
        pass
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
    try:
        from market_desk.whitebox import fit_whitebox, whitebox_score_adj

        # Affinity already in cross_adj — never also feed whitebox board_match.
        wb = whitebox_score_adj(
            kind="stock",
            phase=str(phase or member.get("_phase") or ""),
            ready=True,
            trend=member.get("trend") if isinstance(member.get("trend"), dict) else None,
            board_match=False,
            model=fit_whitebox(),
        )
        if wb.get("ok"):
            score += float(wb.get("score_adj") or 0)
            out_wb = {
                "score_adj": wb.get("score_adj"),
                "parts": list(wb.get("parts") or []),
                "contribs": list(wb.get("contribs") or []),
                "note": wb.get("note"),
            }
        else:
            out_wb = None
    except Exception:
        out_wb = None
    # Relative strength vs board + board fund-flow quality.
    try:
        if board_pct is not None and float(pct) < float(board_pct) - float(STOCK_VS_BOARD_WEAK_PCT):
            score -= 5.0
    except (TypeError, ValueError):
        pass
    try:
        if board_main_yi is not None and float(board_main_yi) <= -1.0:
            score -= float(STOCK_FLOW_OUT_SCORE_PEN)
    except (TypeError, ValueError):
        pass
    boards = int(boards_by.get(code) or 0)
    # Ready only inside the sweet pullback band (not merely past pb_min).
    ready = pullback is not None and pb_min <= pullback <= pb_sweet
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
    if out_wb:
        out["whitebox"] = out_wb
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
    adapt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach risk-based share counts onto recommendation cards."""
    from market_desk.adapt import compose_size_mult
    from market_desk.settings import setting

    rec = dict(recommend or {})
    adapt = adapt if isinstance(adapt, dict) else {}
    equity = float(setting("account_equity", 50000))
    risk_pct = float(setting("risk_pct_per_trade", 1.0))
    # Soft scale risk% by playbook size cap (e.g. panic uses smaller risk).
    cap = float((playbook or {}).get("size_cap_pct") or 100)
    if cap < 40:
        risk_pct = min(risk_pct, 0.6)
    elif cap < 55:
        risk_pct = min(risk_pct, 1.0)

    # Prefer atomic factors from adapt bundle; fall back to precomposed size_mult.
    factors: list[dict[str, Any]] = []
    base_factors = adapt.get("size_factors")
    if isinstance(base_factors, list) and base_factors:
        factors.extend(dict(f) for f in base_factors if isinstance(f, dict))
    else:
        try:
            base_m = float(adapt.get("size_mult") or 1.0)
        except (TypeError, ValueError):
            base_m = 1.0
        factors.append({"key": "adapt", "label": "自适应包", "mult": base_m})

    if adapt.get("phase_soft_size"):
        try:
            ps = float(adapt.get("phase_soft_mult") or 0.75)
        except (TypeError, ValueError):
            ps = 0.75
        factors.append({"key": "phase_soft", "label": "相位软降", "mult": max(0.5, min(1.0, ps))})

    try:
        sim_m = float(adapt.get("similar_size_mult") or 1.0)
    except (TypeError, ValueError):
        sim_m = 1.0
    if sim_m < 0.999:
        factors.append(
            {
                "key": "similar",
                "label": "相似日降温",
                "mult": max(0.5, min(1.0, sim_m)),
            }
        )

    if adapt.get("board_link"):
        try:
            link_m = float(adapt.get("board_link_mult") or 0.75)
        except (TypeError, ValueError):
            link_m = 0.75
        factors.append(
            {
                "key": "board_link",
                "label": "板块联动",
                "mult": max(0.5, min(1.0, link_m)),
            }
        )

    if adapt.get("watch_trial"):
        try:
            wt_m = float(adapt.get("watch_trial_mult") or 0.75)
        except (TypeError, ValueError):
            wt_m = 0.75
        factors.append(
            {
                "key": "watch_trial",
                "label": "自选试探",
                "mult": max(0.5, min(1.0, wt_m)),
            }
        )

    cool_n = int(setting("cool_after_losses", 3) or 3)
    try:
        from market_desk.db import load_positions

        open_rows = [r for r in (load_positions() or []) if int(r.get("qty") or 0) > 0]
        losers = sum(1 for r in open_rows if (r.get("pnl_pct") or 0) < 0)
    except Exception:
        losers = 0
    if cool_n > 0 and losers >= cool_n:
        factors.append(
            {
                "key": "float_cool",
                "label": f"浮亏{losers}只",
                "mult": 0.75,
            }
        )

    composed = compose_size_mult(factors)
    size_mult = float(composed.get("size_mult") or 1.0)
    risk_pct = round(max(0.15, risk_pct * size_mult), 3)
    heat_note = str((adapt.get("size_heat") or {}).get("note") or "")
    cool_note = str(composed.get("note") or "")
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
        if abs(size_mult - 1.0) > 0.02:
            item["size_mult"] = size_mult
        items.append(item)
    rec["items"] = items
    rec["risk_meta"] = {
        "account_equity": equity,
        "risk_pct_per_trade": risk_pct,
        "size_cap_pct": cap,
        "size_mult": size_mult,
        "size_heat_note": heat_note,
        "cool_note": cool_note,
        "size_compose": composed,
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
        for key in ("recommend", "side_recommend", "link_recommend"):
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
    for key in ("recommend", "side_recommend", "link_recommend"):
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
                if item.get("link_board"):
                    item["role_label"] = (
                        "联动ETF盯回踩" if kind == "etf" else "联动盯回踩"
                    )
                else:
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
    Prefer ``_sell_theme_context`` for sells (multi-theme); this remains the
    sticky-only fallback when ``sell_themes`` is absent.
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
    if board and main_name and same_theme(board, main_name):
        return True
    return False


def _match_sell_theme(row: dict[str, Any], theme: dict[str, Any]) -> bool:
    """Return True when a position belongs to one sell-theme descriptor."""
    code = normalize_code(row.get("code"))
    if not code:
        return False
    carrier = normalize_code(theme.get("carrier_code"))
    if carrier and code == carrier:
        return True
    pool = {normalize_code(c) for c in (theme.get("pool_codes") or []) if c}
    if code in pool:
        return True
    rec = {normalize_code(c) for c in (theme.get("rec_codes") or []) if c}
    if code in rec:
        return True
    name = str(theme.get("name") or "")
    labels: list[str] = []
    for raw in (
        row.get("board"),
        row.get("sector"),
        row.get("industry"),
        row.get("entry_board"),
    ):
        text = str(raw or "").strip()
        if text:
            labels.append(text)
    for raw in row.get("board_names") or []:
        text = str(raw or "").strip()
        if text:
            labels.append(text)
    for held in labels:
        if name and (held in name or name in held or same_theme(held, name)):
            return True
    return False


def _sell_theme_context(row: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Resolve which sell-theme(s) a position belongs to and the active context.

    Buys ignore this. Sells use multi-theme membership so a side/hot peer fade
    can trim the right bags without requiring them to be today's sticky name.
    When both strong and fading themes match, fade wins (safer exit bias).
    """
    themes = list(verdict.get("sell_themes") or [])
    if not themes:
        tied = _position_tied_to_mainline(row, verdict)
        main = verdict.get("mainline") or {}
        status = str(main.get("status") or "")
        life = str(main.get("lifecycle") or "")
        fade = status == "退潮" or life == "ending"
        return {
            "tied": tied,
            "name": main.get("name") if tied else None,
            "role": "primary" if tied else None,
            "status": status if tied else "",
            "lifecycle": life if tied else "",
            "fade": bool(tied and fade),
            "carrier_falling": bool(tied and (verdict.get("carrier") or {}).get("falling")),
            "carrier_pct": (verdict.get("carrier") or {}).get("pct") if tied else None,
            "matched_names": [main.get("name")] if tied and main.get("name") else [],
        }

    matches = [t for t in themes if _match_sell_theme(row, t)]
    if not matches:
        return {
            "tied": False,
            "name": None,
            "role": None,
            "status": "",
            "lifecycle": "",
            "fade": False,
            "carrier_falling": False,
            "carrier_pct": None,
            "matched_names": [],
        }

    def _is_fade(t: dict[str, Any]) -> bool:
        return str(t.get("status") or "") == "退潮" or str(t.get("lifecycle") or "") == "ending"

    def _is_strong(t: dict[str, Any]) -> bool:
        return str(t.get("status") or "") == "确认中" and str(t.get("lifecycle") or "") != "ending"

    fade_hits = [t for t in matches if _is_fade(t)]
    strong_hits = [t for t in matches if _is_strong(t)]
    # Prefer primary among equals so sticky context stays visible in reasons.
    def _rank(t: dict[str, Any]) -> tuple[int, float]:
        role_rank = {"primary": 0, "side": 1, "hot": 2}.get(str(t.get("role") or ""), 9)
        try:
            sc = -float(t.get("score") or 0)
        except (TypeError, ValueError):
            sc = 0.0
        return (role_rank, sc)

    fade_hits.sort(key=_rank)
    strong_hits.sort(key=_rank)
    matches_sorted = sorted(matches, key=_rank)
    ctx = fade_hits[0] if fade_hits else (strong_hits[0] if strong_hits else matches_sorted[0])
    tied_primary = any(str(t.get("role") or "") == "primary" for t in matches)
    return {
        "tied": True,
        "name": ctx.get("name"),
        "role": ctx.get("role"),
        "status": str(ctx.get("status") or ""),
        "lifecycle": str(ctx.get("lifecycle") or ""),
        "fade": bool(fade_hits),
        "carrier_falling": bool(
            tied_primary and (verdict.get("carrier") or {}).get("falling")
        ),
        "carrier_pct": (verdict.get("carrier") or {}).get("pct") if tied_primary else None,
        "matched_names": [str(t.get("name")) for t in matches_sorted if t.get("name")],
    }


def build_sell_advice(
    positions: list[dict[str, Any]],
    verdict: dict[str, Any] | None,
    phase: str,
    *,
    trade_date: str | None = None,
    trends_by_code: dict[str, dict[str, Any]] | None = None,
    similar: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
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
    return {
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
                if item.get("wait_price") is not None:
                    item["buy_price"] = item.get("wait_price")
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
    rel_strong: bool = False,
    segment_key: str | None = None,
) -> dict[str, Any]:
    """Pick stop / pullback / take-profit bands from multi-module context.

    Regimes:
    - give: tied to live mainline that is still confirming/rising, daily up,
      and not in soft-exit — wider stop, tolerate deeper pullback.
    - tight: fade/ending/panic/climax/daily down/carrier falling — tighter.
    - neutral: default fixed bands.

    Optional ``sell_bias`` from review sell outcomes scales pb/take/pocket.
    ``rel_strong`` (day green / beats index) blocks panic-alone tight bands.
    Session ``segment_key`` soft-widens open / soft-tightens afternoon.
    """
    from market_desk.config import SELL_BAND_ETF, SELL_BAND_STOCK, SELL_UPTREND_BAND_MULT

    trend = trend or {}
    daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
    daily_down = bool(trend.get("down"))
    # Lifecycle keys: starting | ongoing | ending (see lifecycle.py).
    rising_life = life_stage in ("starting", "ongoing")
    confirming = main_status == "确认中"
    strong_hold = bool(
        on_mainline
        and confirming
        and rising_life
        and daily_up
        and not soft_exit
        and not ending
    )
    # Daily uptrend: do not let soft fade / carrier tick alone force tight bands.
    fade_pressure = bool(
        soft_exit
        or (on_mainline and carrier_falling)
        or (on_mainline and main_status == "退潮")
    )
    panic_tight = phase == "恐慌" and not rel_strong
    weak_context = bool(
        daily_down
        or ending
        or panic_tight
        or (fade_pressure and not daily_up)
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
    if rel_strong and mode == "neutral":
        mode_zh = f"{mode_zh}·相对偏强"
    table = SELL_BAND_ETF if etf else SELL_BAND_STOCK
    band = dict(table[mode])
    if daily_up:
        up_mult = float(SELL_UPTREND_BAND_MULT)
        for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
            band[key] = float(band[key]) * up_mult
        # More negative stop = wider room (e.g. -3.0 → -3.54).
        band["pnl_stop"] = float(band["pnl_stop"]) * up_mult
        mode_zh = f"{mode_zh}·日线上升"
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
    seg_info: dict[str, Any] = {}
    try:
        from market_desk.adapt import build_sell_mfe_bias, segment_sell_mult

        seg_info = segment_sell_mult(segment_key)
        seg_m = float(seg_info.get("mult") or 1.0)
        # Stack with review bias but clamp product so bands stay sane.
        if abs(seg_m - 1.0) > 0.02:
            stacked = max(0.75, min(1.25, mult * seg_m))
            residual = stacked / max(mult, 1e-6) if mult > 0 else seg_m
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * residual
            if seg_info.get("widen"):
                band["pnl_stop"] = float(band["pnl_stop"]) * (1.0 + (residual - 1.0) * 0.4)
                mode_zh = f"{mode_zh}·{seg_info.get('note')}"
            elif seg_info.get("tighten"):
                mode_zh = f"{mode_zh}·{seg_info.get('note')}"
        mfe_info = build_sell_mfe_bias()
        mfe_m = float(mfe_info.get("mult") or 1.0)
        if mfe_info.get("ok") and abs(mfe_m - 1.0) > 0.02:
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * mfe_m
            mode_zh = f"{mode_zh}·{mfe_info.get('note')}"
            seg_info = dict(seg_info)
            seg_info["mfe"] = mfe_info
    except Exception:
        seg_info = {}
    band["mode"] = mode
    band["mode_zh"] = mode_zh
    band["daily_up"] = daily_up
    band["rel_strong"] = bool(rel_strong)
    band["sell_bias"] = {
        "widen": bool(bias.get("widen")),
        "tighten": bool(bias.get("tighten")),
        "mult": mult,
        "hit_rate": bias.get("hit_rate"),
        "n": bias.get("n"),
        "note": bias.get("note"),
    }
    band["segment_sell"] = seg_info
    return band


def _position_exempt_yday_weak(row: dict[str, Any]) -> bool:
    """Return True when a position should skip「昨买今弱」half-trim."""
    from market_desk.config import SELL_INDEPENDENT_POP_EXEMPT_YDAY

    if not SELL_INDEPENDENT_POP_EXEMPT_YDAY:
        return False
    note = str(row.get("note") or "")
    if "独立人气" in note or "independent_pop" in note:
        return True
    if str(row.get("desk_source") or "").strip().lower() == "independent_pop":
        return True
    return False


def _yday_buy_weak(
    *,
    buy_day: str | None,
    trade_date: str | None,
    t1_locked: bool,
    day_pct: float | None,
) -> dict[str, Any]:
    """Detect prior-day buys facing a weak session (anti panic full-exit window).

    Active when the bag is first sellable (not T+1), bought within a few calendar
    days, and today's change is at/below the weak threshold.
    """
    from market_desk.config import (
        SELL_YDAY_BUY_MAX_AGE_DAYS,
        SELL_YDAY_BUY_WEAK_PCT,
    )

    out: dict[str, Any] = {
        "active": False,
        "age_days": None,
        "weak_pct": float(SELL_YDAY_BUY_WEAK_PCT),
    }
    if t1_locked or day_pct is None:
        return out
    day = str(trade_date or "").strip()[:10]
    bought = str(buy_day or "").strip()[:10]
    if not day or not bought or bought >= day:
        return out
    try:
        from datetime import datetime

        age = (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(bought, "%Y-%m-%d")).days
    except ValueError:
        return out
    out["age_days"] = age
    if age < 1 or age > int(SELL_YDAY_BUY_MAX_AGE_DAYS):
        return out
    if float(day_pct) > float(SELL_YDAY_BUY_WEAK_PCT):
        return out
    out["active"] = True
    return out


def _sell_wave_adj(verdict: dict[str, Any] | None) -> dict[str, Any]:
    """Soft sell-band nudge from index Elliott primary scenario (observe-only)."""
    from market_desk.config import SELL_WAVE_END_SOFT_DELTA, SELL_WAVE_W3_SOFT_EXTRA

    ew = (verdict or {}).get("elliott") if isinstance(verdict, dict) else None
    if not isinstance(ew, dict) or not ew.get("ok"):
        return {}
    primary = ew.get("primary") or {}
    wid = str(primary.get("id") or "")
    if wid == "imp_up_w3":
        return {
            "soft_extra": float(SELL_WAVE_W3_SOFT_EXTRA),
            "take_mult": 1.08,
            "tag": "浪3持有",
        }
    if wid in ("imp_up_w5", "corr_c"):
        return {
            "soft_extra": float(SELL_WAVE_END_SOFT_DELTA),
            "take_mult": 0.95,
            "tag": "浪末偏紧",
        }
    return {}


def _enrich_sell_next_action(
    item: dict[str, Any],
    *,
    hold_peak: float,
    last_sell_price: Any = None,
    digits: int = 2,
) -> dict[str, Any]:
    """Attach next-action label and trigger prices for position-row guidance.

    Actions: hold / half / clear / watch. After a half-trim, prefer the regret
    window anchors (break last sell price or deep pullback from hold peak).
    """
    ready = bool(item.get("ready"))
    exit_mode = str(item.get("exit_mode") or "hold")
    regret = bool(item.get("regret_hold"))
    partial = bool(item.get("partial_done"))
    t1 = bool(item.get("t1_locked"))
    pb_deep = float(item.get("pb_deep") or 0)
    stop = item.get("stop_price")
    target = item.get("target_price")
    sell_px = item.get("sell_price")

    half_anchor = None
    try:
        if last_sell_price not in (None, "", 0) and partial:
            half_anchor = _px(float(last_sell_price), digits)
    except (TypeError, ValueError):
        half_anchor = None
    if half_anchor is None and item.get("half_anchor_price") not in (None, ""):
        try:
            half_anchor = _px(float(item["half_anchor_price"]), digits)
        except (TypeError, ValueError):
            half_anchor = None

    deep_clear = None
    try:
        peak = float(hold_peak or item.get("hold_peak") or 0)
    except (TypeError, ValueError):
        peak = 0.0
    if peak > 0 and pb_deep > 0:
        deep_clear = _px(peak * (1.0 - pb_deep / 100.0), digits)

    if t1:
        action, zh = "watch", "继续观察"
        trigger = None
        note = "T+1 隔日可卖"
    elif ready and exit_mode == "clear":
        action, zh = "clear", "清仓"
        trigger = sell_px
        note = str(item.get("role_label") or "建议清仓")
    elif ready and exit_mode == "half":
        action, zh = "half", "减半"
        trigger = sell_px
        note = str(item.get("role_label") or "建议先减一半")
    elif regret or (partial and not ready):
        action, zh = "watch", "继续观察"
        trigger = half_anchor if half_anchor is not None else deep_clear
        bits: list[str] = []
        if half_anchor is not None:
            bits.append(f"破减仓价 {half_anchor}")
        if deep_clear is not None:
            bits.append(f"深回撤线 {deep_clear}")
        note = ("再清：" + " / ".join(bits)) if bits else "余仓盯止损"
    else:
        action, zh = "hold", "持有"
        trigger = stop
        bits = []
        if stop is not None:
            bits.append(f"止损 {stop}")
        if target is not None:
            bits.append(f"目标 {target}")
        note = " · ".join(bits) if bits else "继续持有"

    item["next_action"] = action
    item["next_action_zh"] = zh
    item["next_action_note"] = note.strip()
    item["trigger_price"] = trigger
    item["half_anchor_price"] = half_anchor
    item["deep_clear_price"] = deep_clear
    if peak > 0:
        item["hold_peak"] = round(peak, 4)
    return item


def attach_position_sell_hints(
    positions: list[dict[str, Any]] | None,
    sell_items: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge sell next-action / half-anchor fields onto position rows."""
    by_id: dict[Any, dict[str, Any]] = {}
    by_code: dict[str, dict[str, Any]] = {}
    for it in sell_items or []:
        if it.get("id") is not None:
            by_id[it.get("id")] = it
        code = normalize_code(it.get("code"))
        if code:
            by_code[code] = it
    out: list[dict[str, Any]] = []
    for row in positions or []:
        item = dict(row)
        if item.get("closed") or int(item.get("qty") or 0) <= 0:
            # Still surface today's trim price on closed / flat rows.
            try:
                if item.get("last_sell_price") not in (None, "") and int(
                    item.get("day_sold_qty") or 0
                ) > 0:
                    item["half_anchor_price"] = float(item["last_sell_price"])
            except (TypeError, ValueError):
                pass
            out.append(item)
            continue
        hint = by_id.get(item.get("id"))
        if hint is None:
            hint = by_code.get(normalize_code(item.get("code")))
        if not hint:
            out.append(item)
            continue
        item["next_action"] = hint.get("next_action")
        item["next_action_zh"] = hint.get("next_action_zh")
        item["next_action_note"] = hint.get("next_action_note")
        item["trigger_price"] = hint.get("trigger_price")
        item["half_anchor_price"] = hint.get("half_anchor_price")
        item["deep_clear_price"] = hint.get("deep_clear_price")
        item["exit_mode"] = hint.get("exit_mode")
        item["sell_ready"] = bool(hint.get("ready"))
        item["regret_hold"] = bool(hint.get("regret_hold"))
        item["role_label"] = hint.get("role_label")
        out.append(item)
    return out


def _sell_tune_tags(
    *,
    band: dict[str, Any],
    daily_up: bool,
    at_tip: bool,
    rel_strong: bool,
    carrier_rel_strong: bool,
    carrier_rel_weak: bool,
    flow_pressure: bool,
    wave_tag: str | None,
    yday_repaired: bool,
    regret: bool,
) -> list[str]:
    """Build up to four short attribution chips for the sell card."""
    tags: list[str] = []
    sb = band.get("sell_bias") or {}
    if sb.get("widen"):
        tags.append("复盘卖早")
    elif sb.get("tighten"):
        tags.append("复盘卖准")
    mfe = (band.get("segment_sell") or {}).get("mfe") or {}
    if mfe.get("widen"):
        tags.append("MFE放宽")
    elif mfe.get("tighten"):
        tags.append("MFE收紧")
    if daily_up:
        tags.append("日线↑")
    if at_tip:
        tags.append("贴尖")
    if rel_strong or carrier_rel_strong:
        tags.append("相对强")
    elif carrier_rel_weak:
        tags.append("弱于载体")
    if flow_pressure:
        tags.append("资金流出")
    if wave_tag:
        tags.append(str(wave_tag))
    if yday_repaired:
        tags.append("今弱已修复")
    if regret:
        tags.append("反悔窗")
    # De-dupe preserve order, cap 4.
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= 4:
            break
    return out


def _sell_item(
    row: dict[str, Any],
    verdict: dict[str, Any],
    phase: str,
    *,
    trade_date: str | None = None,
    trend: dict[str, Any] | None = None,
    sell_bias: dict[str, Any] | None = None,
    similar: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Decide whether a held name should be sold, trimmed, or held.

    Exit is layered: stop → clear; deep take / lifecycle ending → clear or half;
    soft mainline fade → half first. ``exit_mode`` drives UI defaults (half/clear).

    Daily trend (once per day): if stop band hits but trend is still up and price
    holds above MA20, soften to half; clear downtrend / MA20 break stays hard stop.

    Prior-day buy + weak session (「昨买今弱」): prefer half first and raise the
    hard-clear bar so open weakness does not panic-exit the whole bag.

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

    mainline = verdict.get("mainline") or {}
    sticky_status = str(mainline.get("status") or "")
    sticky_life = str(mainline.get("lifecycle") or "")
    sticky_fade = sticky_status == "退潮" or sticky_life == "ending"
    theme_ctx = _sell_theme_context(row, verdict)
    on_mainline = bool(theme_ctx.get("tied"))
    main_status = str(theme_ctx.get("status") or "") if on_mainline else ""
    life_stage = str(theme_ctx.get("lifecycle") or "") if on_mainline else ""
    ending = life_stage == "ending"
    auction_only = bool(verdict.get("auction_only"))
    # Soft exits: panic always; climax only with higher profit bar later.
    # Theme fade = matched sell-theme 退潮/ending (multi-theme; not sticky-only).
    phase_panic = phase == "恐慌"
    phase_climax = phase == "高潮"
    mainline_fade = (not auction_only) and bool(theme_ctx.get("fade"))
    # Relative strength / tip / day-weak context for soft & light takes.
    day_pct = row.get("last_pct")
    if day_pct is None:
        day_pct = row.get("pct")
    try:
        day_pct_f = float(day_pct) if day_pct is not None else None
    except (TypeError, ValueError):
        day_pct_f = None
    day_pb = None
    try:
        if high not in (None, 0) and last is not None and float(high) > 0:
            day_pb = (float(high) - float(last)) / float(high) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        day_pb = None
    from market_desk.config import (
        SELL_DAY_WEAK_PCT,
        SELL_PANIC_REL_STRONG_PCT,
        SELL_REL_STRONG_PCT,
        SELL_TIP_HOLD_PB,
        SELL_VS_INDEX_EDGE,
    )

    at_tip = day_pb is not None and day_pb < float(SELL_TIP_HOLD_PB)
    rel_strong = False
    if day_pct_f is not None and day_pct_f >= float(SELL_REL_STRONG_PCT):
        rel_strong = True
    else:
        try:
            idx = (metrics or {}).get("hs300_pct")
            if day_pct_f is not None and idx is not None:
                if float(day_pct_f) - float(idx) >= float(SELL_VS_INDEX_EDGE):
                    rel_strong = True
        except (TypeError, ValueError):
            pass
    panic_rel_strong = bool(
        phase_panic
        and day_pct_f is not None
        and day_pct_f >= float(SELL_PANIC_REL_STRONG_PCT)
    )
    day_weak = day_pct_f is not None and day_pct_f <= float(SELL_DAY_WEAK_PCT)
    partial_done = int(row.get("day_sold_qty") or 0) > 0
    carrier_pct = theme_ctx.get("carrier_pct")
    vs_carrier = None
    carrier_rel_strong = False
    carrier_rel_weak = False
    try:
        if day_pct_f is not None and carrier_pct is not None:
            vs_carrier = round(float(day_pct_f) - float(carrier_pct), 2)
            if vs_carrier >= float(SELL_VS_INDEX_EDGE):
                carrier_rel_strong = True
                rel_strong = True
            elif vs_carrier <= -float(SELL_VS_INDEX_EDGE):
                carrier_rel_weak = True
    except (TypeError, ValueError):
        vs_carrier = None
    soft_exit = (phase_panic and not panic_rel_strong) or (mainline_fade and on_mainline)
    t1_locked = is_t1_locked(row, trade_date)
    buy_day = position_buy_day(row)
    yday_weak = _yday_buy_weak(
        buy_day=buy_day,
        trade_date=trade_date,
        t1_locked=t1_locked,
        day_pct=day_pct_f,
    )
    if yday_weak.get("active") and _position_exempt_yday_weak(row):
        yday_weak = dict(yday_weak)
        yday_weak["active"] = False
        yday_weak["exempt"] = "independent_pop"
    # 昨买今弱 repair: price reclaimed cost or day % recovered from the weak print.
    yday_repaired = False
    if yday_weak.get("active"):
        from market_desk.config import SELL_YDAY_RECOVER_PCT

        recovered = False
        try:
            if last is not None and buy > 0 and float(last) >= buy:
                recovered = True
        except (TypeError, ValueError):
            pass
        try:
            if day_pct_f is not None and float(day_pct_f) > float(SELL_YDAY_RECOVER_PCT):
                recovered = True
        except (TypeError, ValueError):
            pass
        if recovered:
            yday_weak = dict(yday_weak)
            yday_weak["active"] = False
            yday_weak["repaired"] = True
            yday_repaired = True
    hold_qty = int(row.get("qty") or 0)
    trend = trend or {}
    ma20 = trend.get("ma20")
    theme_label = str(theme_ctx.get("name") or "")
    theme_role = str(theme_ctx.get("role") or "")

    band = _exit_band_params(
        etf=etf,
        on_mainline=on_mainline,
        ending=ending,
        main_status=str(main_status),
        life_stage=str(life_stage),
        phase=phase,
        soft_exit=soft_exit,
        trend=trend,
        carrier_falling=bool(theme_ctx.get("carrier_falling")),
        sell_bias=sell_bias,
        rel_strong=rel_strong or panic_rel_strong,
        segment_key=str((verdict.get("segment") or {}).get("key") or ""),
    )
    # Similar-day sell urgency + theme fund outflow: nudge soft floors earlier.
    sell_gate = str((similar or {}).get("sell_gate") or "")
    theme_yi = None
    for t in verdict.get("sell_themes") or []:
        if str(t.get("name") or "") == theme_label:
            theme_yi = t.get("main_yi")
            break
    flow_pressure = False
    flow_fade = False
    try:
        from market_desk.config import SELL_FLOW_OUT_YI

        if theme_yi is not None and float(theme_yi) <= float(SELL_FLOW_OUT_YI):
            flow_pressure = True
            flow_fade = bool(mainline_fade)
    except (TypeError, ValueError):
        flow_pressure = False
        flow_fade = False
    wave_adj = _sell_wave_adj(verdict)
    if sell_gate == "urgent" or flow_fade:
        from market_desk.config import SELL_SIM_URGENT_FLOOR

        pb_scale = 0.88 if sell_gate == "urgent" else 0.93
        # Soft floor when review already tightened — avoid double-cut.
        if sell_bias and sell_bias.get("tighten"):
            pb_scale = max(float(SELL_SIM_URGENT_FLOOR), pb_scale)
        band["pb_light"] = float(band["pb_light"]) * pb_scale
        band["pb_deep"] = float(band["pb_deep"]) * pb_scale
        band["take_pnl"] = float(band["take_pnl"]) * pb_scale
        if sell_gate == "urgent":
            band["mode_zh"] = f"{band['mode_zh']}·相似日急"
        elif flow_fade:
            band["mode_zh"] = f"{band['mode_zh']}·资金+退潮"
    elif sell_gate == "hold" and band["mode"] != "tight":
        band["pb_light"] = float(band["pb_light"]) * 1.06
        band["take_pnl"] = float(band["take_pnl"]) * 1.06
        band["mode_zh"] = f"{band['mode_zh']}·相似日持"
    # Wave soft take mult (observe-only; does not flip regime).
    try:
        w_mult = float(wave_adj.get("take_mult") or 1.0)
        if abs(w_mult - 1.0) > 0.01:
            for key in ("pb_light", "pb_deep", "take_pnl", "take_deep_pnl", "pocket_pnl"):
                band[key] = float(band[key]) * w_mult
            if wave_adj.get("tag"):
                band["mode_zh"] = f"{band['mode_zh']}·{wave_adj['tag']}"
    except (TypeError, ValueError):
        pass
    if flow_pressure and not flow_fade:
        band["mode_zh"] = f"{band['mode_zh']}·资金流出"
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
        and not (at_tip and rel_strong and not (ending and on_mainline))
    ):
        urgency = "take"
        ready = True
        sell_price = float(last)
        # Peak layering: light pullback → half only; deep / peak-fail → clear.
        deep = pullback >= pb_deep and pnl_pct >= take_deep_pnl
        peak_fail = False
        try:
            last_sell_px = row.get("last_sell_price")
            if (
                partial_done
                and last_sell_px not in (None, "", 0)
                and float(last) < float(last_sell_px)
                and pullback >= pb_light
            ):
                peak_fail = True
            if (
                high not in (None, 0)
                and hold_peak > 0
                and (hold_peak - float(high)) / hold_peak * 100.0
                < float(SELL_TIP_HOLD_PB)
                and pullback >= pb_deep
                and pnl_pct >= take_deep_pnl
            ):
                peak_fail = True
        except (TypeError, ValueError, ZeroDivisionError):
            peak_fail = False
        ending_clear = (
            ending
            and on_mainline
            and pullback >= pb_light
            and pnl_pct >= (3.0 if etf else 5.0)
            and not at_tip
        )
        if deep or peak_fail:
            exit_mode = "clear"
            role_label = (
                "破峰失败清仓"
                if peak_fail and not deep
                else "结构回撤清仓"
            )
            sell_pct = 100
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%"
                + ("，破减仓价/破峰失败" if peak_fail else "")
                + f"（{band['mode_zh']}），建议清仓"
            )
        elif ending_clear:
            exit_mode = "clear"
            role_label = "退潮兑现清仓"
            sell_pct = 100
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%，生命周期偏衰退"
                f"（{band['mode_zh']}），建议清仓"
            )
        else:
            # Light band: never clear on light pullback alone.
            exit_mode = "half"
            role_label = "冲高回落先减"
            sell_pct = 50
            reason_parts.append(
                f"浮盈 {_fmt_pct(pnl_pct)}，高点回撤 {pullback:.1f}%"
                f"（轻回撤·{band['mode_zh']}），先减一半"
            )
    elif pnl_pct is not None and pnl_pct >= pocket_pnl and not (at_tip and rel_strong):
        urgency = "take"
        ready = True
        sell_price = float(last)
        # Ending pocket clear requires leaving tip (no zero-pullback dump).
        if ending and on_mainline and not at_tip:
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
    elif (soft_exit or phase_climax) and pnl_pct is not None and not (at_tip and rel_strong):
        from market_desk.config import (
            SELL_CARRIER_WEAK_SOFT_DELTA,
            SELL_DAY_WEAK_SOFT_DELTA,
            SELL_SOFT_CLIMAX_MIN_PNL_ETF,
            SELL_SOFT_CLIMAX_MIN_PNL_STOCK,
            SELL_SOFT_DEEP_PNL_ETF,
            SELL_SOFT_DEEP_PNL_STOCK,
            SELL_SOFT_MIN_PNL_ENDING_ETF,
            SELL_SOFT_MIN_PNL_ENDING_STOCK,
            SELL_SOFT_MIN_PNL_ETF,
            SELL_SOFT_MIN_PNL_STOCK,
            SELL_SOFT_UPTREND_EXTRA,
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
        daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
        if daily_up:
            soft_min = float(soft_min) + float(SELL_SOFT_UPTREND_EXTRA)
        if day_weak:
            soft_min = float(soft_min) + float(SELL_DAY_WEAK_SOFT_DELTA)
        # Flow alone (no theme fade): nudge soft floor only, no pb scale.
        if flow_pressure and not flow_fade:
            soft_min = float(soft_min) + float(SELL_DAY_WEAK_SOFT_DELTA) * 0.5
        # Lagging carrier (not at tip): easier soft trim.
        if carrier_rel_weak and not at_tip:
            soft_min = float(soft_min) + float(SELL_CARRIER_WEAK_SOFT_DELTA)
        # Elliott soft nudge.
        try:
            soft_min = float(soft_min) + float(wave_adj.get("soft_extra") or 0.0)
        except (TypeError, ValueError):
            pass
        # Relative strength: climax soft needs more profit; skip mild climax alone.
        if phase_climax and rel_strong and not soft_exit:
            soft_min = float(soft_min) + 1.0
        # Carrier-strong bags: never soft-clear.
        block_soft_clear = bool(carrier_rel_strong or (at_tip and rel_strong))
        if pnl_pct > soft_min:
            urgency = "trim"
            ready = True
            sell_price = float(last)
            deep_need = SELL_SOFT_DEEP_PNL_ETF if etf else SELL_SOFT_DEEP_PNL_STOCK
            if daily_up:
                deep_need = float(deep_need) + float(SELL_SOFT_UPTREND_EXTRA)
            deep_fade = ending and on_mainline and (
                (pullback is not None and pullback >= pb_light)
                or pnl_pct >= deep_need
            )
            # Uptrend / carrier-strong: soft fade only halves; never clear on soft branch.
            if deep_fade and not daily_up and not block_soft_clear:
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
                        if phase_panic and soft_exit
                        else (
                            "相位高潮"
                            if phase_climax
                            else (
                                f"主题「{theme_label}」退潮"
                                if theme_label
                                else "主线退潮"
                            )
                        )
                    )
                )
                reason_parts.append(
                    f"{why}，浮盈 {_fmt_pct(pnl_pct)}（门槛 {soft_min:.1f}%"
                    + ("·日线上升抬高" if daily_up else "")
                    + ("·当日偏弱放宽" if day_weak else "")
                    + ("·弱于载体" if carrier_rel_weak and not at_tip else "")
                    + ("·资金流出" if flow_pressure and not flow_fade else "")
                    + "），先减一半"
                )
        # else: below soft floor — fall through to carrier / ending / hold
    if not ready and (
        not etf
        and on_mainline
        and theme_ctx.get("carrier_falling")
        and pnl_pct is not None
        and pnl_pct > -1.0
        and last is not None
        and not rel_strong
        and not (bool(trend.get("up")) and not bool(trend.get("down")))
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

        daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
        if (not daily_up) and pnl_pct <= float(SELL_ENDING_DEFENSE_PNL):
            urgency = "trim"
            ready = True
            exit_mode = "half"
            role_label = "衰退防守先减"
            sell_price = float(last)
            sell_pct = 50
            reason_parts.append(f"生命周期偏衰退且浮盈 {_fmt_pct(pnl_pct)}，先减仓防守")
    # Overnight gap-fade: prior-day bag opens strong then fails from open.
    # On sell-theme: skip when still strong (carrier / tip / daily up); weak members
    # may still half-trim. Hard stops are unaffected (this branch only runs if !ready).
    gap_fade_skipped = False
    gap_fade_note = ""
    if (
        not ready
        and not t1_locked
        and last is not None
        and pnl_pct is not None
        and pnl_pct > -1.5
    ):
        try:
            from market_desk.config import GAP_FADE_DROP_PCT, GAP_FADE_OPEN_PCT

            prev = row.get("prev")
            open_px = row.get("open")
            if prev not in (None, 0) and open_px not in (None, 0):
                open_gap = (float(open_px) / float(prev) - 1.0) * 100.0
                from_open = (float(last) / float(open_px) - 1.0) * 100.0
                if open_gap >= float(GAP_FADE_OPEN_PCT) and from_open <= -float(
                    GAP_FADE_DROP_PCT
                ):
                    daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
                    protect = bool(
                        on_mainline
                        and (carrier_rel_strong or at_tip or daily_up)
                    )
                    if protect:
                        gap_fade_skipped = True
                        gap_fade_note = (
                            f"高开 {open_gap:.1f}% 后回落 {abs(from_open):.1f}%，"
                            "但主线内偏强（强于载体/贴尖/日线上升），先不因形态减"
                        )
                    else:
                        urgency = "trim"
                        ready = True
                        exit_mode = "half"
                        role_label = (
                            "主线内·高开低走先减"
                            if on_mainline and carrier_rel_weak
                            else "隔夜高开低走先减"
                        )
                        sell_price = float(last)
                        sell_pct = 50
                        reason_parts.append(
                            f"开盘相对昨收高开 {open_gap:.1f}% 后回落 "
                            f"{abs(from_open):.1f}%，先减一半"
                        )
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 「昨买今弱」: if nothing fired yet, start with a half trim (not a full dump).
    if (
        not ready
        and yday_weak.get("active")
        and last is not None
        and not partial_done
        and not rel_strong
        and not (at_tip and day_pct_f is not None and day_pct_f >= 0)
    ):
        urgency = "trim"
        ready = True
        exit_mode = "half"
        role_label = "昨买今弱·先减半"
        sell_price = float(last)
        sell_pct = 50
        reason_parts.append(
            f"昨买窗内（持仓约 {yday_weak.get('age_days')} 日）当日 "
            f"{_fmt_pct(day_pct_f)}≤{yday_weak.get('weak_pct')}%，默认先减一半"
        )

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
            elif phase_panic and panic_rel_strong:
                reason_parts.append(
                    f"相位恐慌但当日 {_fmt_pct(day_pct_f)} 偏强，先不因恐慌软减"
                )
            elif at_tip and rel_strong:
                reason_parts.append(
                    f"当日仍贴近高点（回撤 {day_pb:.1f}%）且偏强，先不轻减"
                    if day_pb is not None
                    else "当日仍贴近高点且偏强，先不轻减"
                )
            if ending and on_mainline:
                reason_parts.append(
                    f"主题「{theme_label}」生命周期偏衰退，反抽优先减"
                    if theme_label
                    else "主线生命周期偏衰退，反抽优先减"
                )
            elif sticky_fade and not on_mainline:
                reason_parts.append("非卖侧主题持仓，主线转弱不自动减")
            if band["mode"] != "neutral":
                reason_parts.append(f"波段口径 {band['mode_zh']}")
            if on_mainline and theme_role and theme_role != "primary" and theme_label:
                reason_parts.append(f"卖侧归属「{theme_label}」（{ {'side':'支线','hot':'热点同伴'}.get(theme_role, theme_role) }）")
            if carrier_rel_strong and vs_carrier is not None:
                reason_parts.append(f"强于主线载体 {vs_carrier:+.1f}pt，先不轻减")
            elif carrier_rel_weak and vs_carrier is not None:
                reason_parts.append(f"弱于主线载体 {vs_carrier:+.1f}pt")
            if gap_fade_skipped and gap_fade_note:
                reason_parts.append(gap_fade_note)

    # 「昨买今弱」: demote panic clears to half unless loss is clearly deeper.
    if ready and yday_weak.get("active") and exit_mode == "clear":
        from market_desk.config import SELL_YDAY_BUY_CLEAR_EXTRA

        keep_clear = False
        clear_floor = float(pnl_stop) + float(SELL_YDAY_BUY_CLEAR_EXTRA)
        if urgency == "stop" and pnl_pct is not None and float(pnl_pct) <= clear_floor:
            keep_clear = True
        elif (
            urgency == "take"
            and pullback is not None
            and pullback >= pb_deep
            and pnl_pct is not None
            and float(pnl_pct) >= take_deep_pnl
        ):
            keep_clear = True
        if not keep_clear:
            exit_mode = "half"
            sell_pct = 50
            if "清仓" in role_label:
                role_label = role_label.replace("清仓", "先减")
            if "昨买今弱" not in role_label:
                role_label = f"昨买今弱·{role_label}"
            reason_parts.insert(
                0,
                f"昨买今弱：默认先减一半；清仓需更深亏（约≤{clear_floor:.1f}%）或深结构回撤",
            )

    # Already trimmed today: do not keep nagging half on soft/take; keep stop/clear.
    if (
        ready
        and partial_done
        and exit_mode == "half"
        and urgency in ("trim", "take")
    ):
        ready = False
        exit_mode = "hold"
        sell_pct = 0
        role_label = "今日已减·盯止损"
        sell_price = target
        reason_parts.insert(0, "今日已减，余仓盯止损")

    # Regret window: after a half-trim, don't escalate soft/take clears while still strong.
    regret_hold = False
    if ready and partial_done and exit_mode == "clear" and urgency in ("take", "trim"):
        from market_desk.config import SELL_REGRET_ENABLED

        if SELL_REGRET_ENABLED:
            still_strong = bool(at_tip or rel_strong or carrier_rel_strong)
            allow_clear = False
            if (
                pullback is not None
                and pullback >= pb_deep
                and pnl_pct is not None
                and float(pnl_pct) >= take_deep_pnl
            ):
                allow_clear = True
            try:
                last_sell_px = row.get("last_sell_price")
                if last_sell_px not in (None, "", 0) and float(last) < float(last_sell_px):
                    allow_clear = True
            except (TypeError, ValueError):
                pass
            if day_weak and not rel_strong:
                allow_clear = True
            if still_strong and not allow_clear:
                ready = False
                exit_mode = "hold"
                sell_pct = 0
                regret_hold = True
                role_label = "今日已减·继续观察"
                sell_price = target
                reason_parts.insert(
                    0,
                    "反悔窗：已减半后仍贴尖/相对强，暂不清仓；破减仓价或深结构再清",
                )

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
    daily_up = bool(trend.get("up")) and not bool(trend.get("down"))
    tune_tags = _sell_tune_tags(
        band=band,
        daily_up=daily_up,
        at_tip=bool(at_tip),
        rel_strong=bool(rel_strong),
        carrier_rel_strong=bool(carrier_rel_strong),
        carrier_rel_weak=bool(carrier_rel_weak),
        flow_pressure=bool(flow_pressure),
        wave_tag=str(wave_adj.get("tag") or "") or None,
        yday_repaired=bool(yday_repaired),
        regret=bool(regret_hold),
    )
    mfe_info = (band.get("segment_sell") or {}).get("mfe") or {}
    sb = band.get("sell_bias") or {}
    sell_tune = {
        "review_note": sb.get("note"),
        "mfe_note": mfe_info.get("note"),
        "widen": bool(sb.get("widen") or mfe_info.get("widen")),
        "tighten": bool(sb.get("tighten") or mfe_info.get("tighten")),
        "mult": sb.get("mult"),
        "mfe_mult": mfe_info.get("mult"),
    }
    # Soft half / light take need minute fade; stop & deep clear skip the gate.
    minute_gate = bool(
        ready
        and exit_mode == "half"
        and urgency in ("take", "trim")
    )

    out = {
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
        "trend": None if not trend else trend.get("label"),
        "trend_ok": bool(trend.get("up")) and not bool(trend.get("down")),
        "trend_down": bool(trend.get("down")),
        "trend_pending": (trend.get("quality") in ("fetch_fail", "thin")) if trend else True,
        "ma5": None if not trend else trend.get("ma5"),
        "ma10": None if not trend else trend.get("ma10"),
        "ma20": None if not trend else trend.get("ma20"),
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
        "on_sell_theme": on_mainline,
        "sell_theme": theme_label or None,
        "sell_theme_role": theme_role or None,
        "rel_strong": rel_strong,
        "panic_rel_strong": panic_rel_strong,
        "carrier_falling": bool(theme_ctx.get("carrier_falling")),
        "carrier_rel_strong": carrier_rel_strong,
        "carrier_rel_weak": carrier_rel_weak,
        "vs_carrier": vs_carrier,
        "carrier_compare_absent": bool(
            on_mainline and carrier_pct is None and not theme_ctx.get("carrier_falling")
        ),
        "partial_done": partial_done,
        "tune_tags": tune_tags,
        "sell_tune": sell_tune,
        "minute_gate": minute_gate,
        "regret_hold": regret_hold,
        "yday_repaired": yday_repaired,
        "hold_peak": round(hold_peak, 4) if hold_peak else None,
    }
    return _enrich_sell_next_action(
        out,
        hold_peak=float(hold_peak or 0),
        last_sell_price=row.get("last_sell_price"),
        digits=digits,
    )


def quote_prev_close(
    quote: dict[str, Any] | None,
    *,
    last: float | None = None,
) -> float | None:
    """Return yesterday's close from a quote, recovering via pct when needed."""
    q = quote or {}
    prev = q.get("prev")
    try:
        if prev not in (None, "", 0):
            return float(prev)
    except (TypeError, ValueError):
        pass
    pct_q = q.get("pct")
    mark = last if last is not None else q.get("price")
    if mark is None or pct_q is None:
        return None
    try:
        pct_f = float(pct_q)
        if pct_f <= -99.999:
            return None
        return float(mark) / (1.0 + pct_f / 100.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def position_day_anchor(
    buy_price: float,
    *,
    buy_day: str | None,
    trade_day: str | None,
    prev_close: float | None,
) -> float | None:
    """Pick the session P&L anchor: buy price if bought today, else 昨收."""
    day = str(trade_day or "").strip()[:10]
    bday = str(buy_day or "").strip()[:10]
    buy = float(buy_price or 0)
    if day and bday and bday == day and buy > 0:
        return buy
    try:
        if prev_close not in (None, "") and float(prev_close) > 0:
            return float(prev_close)
    except (TypeError, ValueError):
        pass
    return buy if buy > 0 else None


def session_sell_realized(
    sell_price: float | None,
    sold_qty: int,
    *,
    buy_price: float,
    buy_day: str | None,
    trade_day: str | None,
    prev_close: float | None,
    fee: float | None = None,
) -> float:
    """Compute today's realized P&L for shares sold today (vs day anchor − sell fee).

    Overnight lots use 昨收 as the anchor so overnight gains are not counted as
    today's P&L. Lots bought today use the buy price. A flat sell commission is
    deducted once when ``sold_qty > 0`` (buy commission is applied separately).
    """
    qty = int(sold_qty or 0)
    if qty <= 0 or sell_price in (None, ""):
        return 0.0
    try:
        px = float(sell_price)
    except (TypeError, ValueError):
        return 0.0
    anchor = position_day_anchor(
        float(buy_price or 0),
        buy_day=buy_day,
        trade_day=trade_day,
        prev_close=prev_close,
    )
    if anchor is None or anchor <= 0:
        return 0.0
    fee_v = float(TRADE_FEE_CNY if fee is None else fee)
    if fee_v < 0:
        fee_v = 0.0
    return round((px - float(anchor)) * qty - fee_v, 2)


def session_trade_fees(
    *,
    bought_today: bool,
    sold_today: bool,
    fee: float | None = None,
) -> float:
    """Return flat commissions: once for today's buy and once for today's sell."""
    fee_v = float(TRADE_FEE_CNY if fee is None else fee)
    if fee_v < 0:
        fee_v = 0.0
    n = (1 if bought_today else 0) + (1 if sold_today else 0)
    return round(fee_v * n, 2)


def decorate_positions(
    rows: list[dict[str, Any]],
    quotes: dict[str, Any],
    *,
    trade_date: str | None = None,
    boards: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach mark-to-market and day-realized fields used by the position tab."""
    from market_desk.review import lookup_code_boards

    day = str(trade_date or "").strip()[:10]
    board_pool = list(boards or [])
    fee = float(TRADE_FEE_CNY)
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
        sell_day = str(row.get("last_sell_date") or "")[:10]
        if day and sell_day and sell_day != day:
            day_sold = 0
            sell_px = None
        buy_day = str(row.get("last_buy_date") or row.get("created_at") or "")[:10]
        bought_today = bool(day and buy_day and buy_day == day)
        sold_today = day_sold > 0
        buy_fee = fee if bought_today else 0.0
        sell_fee = fee if sold_today else 0.0
        trade_fee = session_trade_fees(
            bought_today=bought_today, sold_today=sold_today, fee=fee
        )
        prev = quote_prev_close(q, last=float(last) if last is not None else None)
        # Recompute today's sell-side realized (vs day-anchor − sell fee).
        sell_realized = (
            session_sell_realized(
                sell_px,
                day_sold,
                buy_price=buy,
                buy_day=buy_day,
                trade_day=day,
                prev_close=prev,
                fee=fee,
            )
            if sold_today
            else 0.0
        )
        # Closed same-day lots fold buy fee into 已实现 so day_pnl matches.
        day_realized = (
            round(sell_realized - buy_fee, 2) if (closed and sold_today) else sell_realized
        )
        if closed:
            mark = float(sell_px) if sell_px not in (None, "") else (
                float(last) if last is not None else buy
            )
            sold_qty = day_sold or int(row.get("day_sold_qty") or 0)
            cost = round(buy * sold_qty, 2) if sold_qty else None
            market = round(mark * sold_qty, 2) if sold_qty and mark is not None else None
            try:
                gross_total = (float(mark) - buy) * sold_qty if sold_qty and mark is not None else None
            except (TypeError, ValueError):
                gross_total = None
            closed_fee = sell_fee + buy_fee
            pnl = (
                round(float(gross_total) - closed_fee, 2)
                if gross_total is not None
                else day_realized
            )
            pnl_pct = round((mark / buy - 1.0) * 100.0, 2) if mark and buy else None
            last = mark
            day_pnl = day_realized if sold_today else None
        else:
            cost = round(buy * qty, 2)
            market = round(last * qty, 2) if last is not None else None
            pnl = (
                round(market - cost - buy_fee, 2)
                if market is not None
                else None
            )
            pnl_pct = round((last / buy - 1.0) * 100.0, 2) if last and buy else None
            pct_q = q.get("pct")
            day_mtm = None
            try:
                if last is not None and qty > 0 and bought_today and buy > 0:
                    day_mtm = (float(last) - buy) * qty
                elif last is not None and prev not in (None, 0, "") and qty > 0:
                    day_mtm = (float(last) - float(prev)) * qty
                elif pct_q is not None and prev not in (None, 0, "") and qty > 0 and not bought_today:
                    day_mtm = float(prev) * qty * float(pct_q) / 100.0
            except (TypeError, ValueError):
                day_mtm = None
            if day_mtm is not None or sold_today or bought_today:
                day_pnl = round((day_mtm or 0.0) + sell_realized - buy_fee, 2)
            else:
                day_pnl = None
        item = dict(row)
        item["code"] = code
        item["name"] = row.get("name") or q.get("name") or code
        item["last"] = last
        item["last_pct"] = None if closed else q.get("pct")
        item["high"] = q.get("high")
        item["low"] = q.get("low")
        item["open"] = None if closed else q.get("open")
        item["prev"] = None if closed else prev
        item["bought_today"] = False if closed else bought_today
        item["cost"] = cost
        item["market"] = market
        item["pnl"] = pnl
        item["pnl_pct"] = pnl_pct
        item["day_pnl"] = day_pnl
        item["closed"] = closed
        item["closed_date"] = closed_date
        item["day_sold_qty"] = day_sold
        item["day_realized_pnl"] = day_realized
        item["trade_fee"] = trade_fee
        item["buy_fee"] = buy_fee
        item["sell_fee"] = sell_fee
        item["status"] = "今日已平" if closed else ("部分兑现" if day_sold > 0 else "持仓")
        # Sell-theme membership helpers for orphan bags.
        names = lookup_code_boards(code, board_pool) if board_pool else []
        entry = str(row.get("entry_board") or "").strip()
        if entry and entry not in names:
            names = [entry] + names
        item["board_names"] = names[:4]
        item["board"] = names[0] if names else (entry or None)
        item["entry_board"] = entry or None
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


def attach_position_daily_trends(
    positions: list[dict[str, Any]] | None,
    trends_by_code: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Attach daily-trend labels onto position rows for the 仓位 tab / sell cards."""
    trends = trends_by_code or {}
    out: list[dict[str, Any]] = []
    for row in positions or []:
        item = dict(row)
        code = normalize_code(item.get("code"))
        trend = trends.get(code) if code else None
        if not trend:
            item.setdefault("daily_trend", None)
            item.setdefault("trend_ok", False)
            item.setdefault("trend_down", False)
            out.append(item)
            continue
        label = trend.get("label")
        item["daily_trend"] = label
        item["daily_trend_zh"] = label
        item["trend"] = label
        item["trend_ok"] = bool(trend.get("up")) and not bool(trend.get("down"))
        item["trend_down"] = bool(trend.get("down"))
        item["trend_pending"] = trend.get("quality") in ("fetch_fail", "thin")
        item["ma5"] = trend.get("ma5")
        item["ma10"] = trend.get("ma10")
        item["ma20"] = trend.get("ma20")
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
    # Day P&L = sum of row day_pnl (vs 昨收/今日买价 + 已实现), NOT floating+realized.
    day_parts = [r for r in rows if r.get("day_pnl") is not None]
    day_total = (
        round(sum(float(r.get("day_pnl") or 0) for r in day_parts), 2) if day_parts else None
    )
    day_total_pct = None
    day_basis = 0.0
    for r in open_rows:
        qty = int(r.get("qty") or 0)
        if qty <= 0:
            continue
        buy = float(r.get("buy_price") or 0)
        prev = r.get("prev")
        if r.get("bought_today") and buy > 0:
            day_basis += buy * qty
        elif prev not in (None, 0, "") and float(prev) > 0:
            day_basis += float(prev) * qty
        elif buy > 0:
            day_basis += buy * qty
    for r in closed_rows:
        sold = int(r.get("day_sold_qty") or 0)
        buy = float(r.get("buy_price") or 0)
        if sold > 0 and buy > 0:
            day_basis += buy * sold
    if day_total is not None and day_basis > 0:
        day_total_pct = round(day_total / day_basis * 100.0, 2)
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
        tips.append(f"浮亏标的 {losers} 只 ≥ 连亏降温阈值 {cool_n}，建议手数×0.75、先冷静再开新仓")
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
