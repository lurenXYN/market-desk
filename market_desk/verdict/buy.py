"""Live mainline verdict, ready alignment, and desk gate summary."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.config import ETF_BOUNCE_BUY_MIN
from market_desk.filters import normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import (
    etf_spec_for_name,
    etf_spec_soft_fallback,
    explain_mainline,
    match_mainline_etf,
    pick_mainline,
    theme_key,
)
from market_desk.playbook import build_playbook
from market_desk.session import apply_segment_bias, session_segment
from market_desk.settings import setting

from market_desk.verdict.common import (
    _blocks_chi_star_stocks,
    _BUY_ACTIONS,
    _demote_buy_to_wait,
    _fmt_num,
    _fmt_pct,
    _join_hint,
    _lookup_hot_board,
    hard_confirm_fails,
)
from market_desk.verdict.scoring import (
    _attach_risk_sizing,
    _build_recommend,
    _stock_candidates,
)
from market_desk.verdict.progress import _apply_ready_confirmations, mark_pullback_entries
from market_desk.verdict.gates import (
    _mainline_narrative,
    apply_auction_open_bridge,
    apply_market_gates,
    apply_review_bias,
    apply_similar_gate,
    build_switch_guard,
    inject_switch_from_theme,
)
from market_desk.verdict.branches import _build_link_branch, _build_side_branch
from market_desk.verdict.sell import build_sell_themes
from market_desk.verdict.desk import (
    build_dragon_recommend,
    build_independent_pullback_recommend,
)


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
    now_text = (now.replace(tzinfo=None) if getattr(now, "tzinfo", None) else now).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    # Yesterday's sticky must yield at once on a new session; only intraday flips wait.
    same_day_sticky = bool(sticky_since_prev) and sticky_since_prev[:10] == now_text[:10]
    confirm_s = float(setting("switch_confirm_seconds", 600) or 0) if same_day_sticky else 0.0
    ml_state: dict[str, Any] = {}
    main = pick_mainline(
        hot,
        sticky_name=sticky,
        margin=sticky_margin,
        sticky_held_seconds=sticky_held,
        challenger_prev=prev_ml.get("challenger") if isinstance(prev_ml.get("challenger"), dict) else None,
        confirm_seconds=confirm_s,
        now_text=now_text,
        state_out=ml_state,
    ) or {}
    board_name = main.get("name") or ""
    ml_why = explain_mainline(
        hot,
        main,
        sticky_name=sticky,
        margin=sticky_margin,
        sticky_held_seconds=sticky_held,
        pending=ml_state.get("challenger"),
        confirm_seconds=confirm_s,
        now_text=now_text,
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
    action, reason, size_hint, bridge_notes, auction_open_bridge = apply_auction_open_bridge(
        action,
        reason,
        size_hint,
        now=now,
        segment_key=str(seg.get("key") or "closed"),
        auction=auction,
        metrics=metrics,
        mainline_pct=main.get("pct"),
        carrier_pct=pct,
    )
    algo_notes.extend(bridge_notes)
    # Stack open_mute + auction-open bridge so demote attribution is explicit.
    if bool(seg.get("open_mute")) and bool((auction_open_bridge or {}).get("active")):
        stack = f"开盘静音+竞价开盘桥叠乘·禁试探（静音{mute_m}分）"
        if stack not in algo_notes:
            algo_notes.append(stack)
        size_hint = _join_hint(size_hint or "", "开盘静音×竞价桥叠乘")
        auction_open_bridge = dict(auction_open_bridge or {})
        auction_open_bridge["stacked_with_open_mute"] = True
        auction_open_bridge["open_mute_minutes"] = mute_m
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
            _demote_buy_to_wait(item)
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
                _demote_buy_to_wait(item)
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
    # High-sim / fallback side absorbed into link → drop duplicate side cards.
    if (
        link_info
        and side_info
        and (
            link_info.get("from_side")
            or str(link_info.get("name") or "") == str(side_info.get("name") or "")
        )
    ):
        algo_notes = [n for n in algo_notes if not str(n).startswith("观察支线=")]
        algo_notes.append(f"支线并入联动={link_info.get('name')}")
        side_info = None
        side_recommend = None
    if link_info:
        fb = str(link_info.get("fallback") or "peer")
        sim_pct = int(round(float(link_info.get("sim") or 0) * 100))
        if fb in ("theme", "score"):
            algo_notes.append(f"板块联动={link_info.get('name')}({sim_pct}%·{fb}兜底)")
        elif not any(str(n).startswith("支线并入联动=") for n in algo_notes):
            algo_notes.append(f"板块联动={link_info.get('name')}({sim_pct}%)")
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
    switch_guard = build_switch_guard(
        now=now,
        sticky_since=sticky_since,
        current_name=board_name,
        prev_name=prev_name,
    )
    if switch_guard.get("active") and switch_guard.get("from_name"):
        sell_themes = inject_switch_from_theme(
            sell_themes, switch_guard, hot=hot
        )
        tip = (
            f"换防护栏={switch_guard.get('from_name')}→{switch_guard.get('to_name')}"
        )
        if tip not in algo_notes:
            algo_notes.append(tip)
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
        "switch_guard": switch_guard,
        "auction_open_bridge": auction_open_bridge,
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
            "challenger": ml_state.get("challenger"),
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
    block_arm = (
        bool(seg.get("open_mute"))
        or auction_only
        or bool((auction_open_bridge or {}).get("revoke_probe"))
    )
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
    # Independent popular pullback: observe-only across sticky + side + link.
    try:
        skip_extra = {
            normalize_code(x.get("code"))
            for x in ((out.get("dragon_recommend") or {}).get("items") or [])
            if x.get("code")
        }
        indep_side = None
        indep_link = None
        if side_info:
            indep_side = _lookup_hot_board(
                hot, name=side_info.get("name"), bk=side_info.get("bk")
            )
        if link_info:
            indep_link = _lookup_hot_board(
                hot, name=link_info.get("name"), bk=link_info.get("bk")
            )
        indep = build_independent_pullback_recommend(
            main,
            side_board=indep_side,
            link_board=indep_link,
            recommend=out.get("recommend"),
            side_recommend=out.get("side_recommend"),
            link_recommend=out.get("link_recommend"),
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
                _demote_buy_to_wait(item)
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
            "short_miss": "",
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
    short_miss = f"还差：{miss_txt}" if miss_txt else ""
    return {
        "action": action,
        "can_buy": False,
        "can_probe": can_probe,
        "reasons": reasons[:5],
        "hint": hint,
        "short_miss": short_miss,
        "progress": progress,
    }
