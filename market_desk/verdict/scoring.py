"""Recommendation candidates, stock scoring, risk sizing, and size-cap gate."""

from __future__ import annotations

from typing import Any
from market_desk.filters import is_limit_up, is_main_board, is_st, normalize_code
from market_desk.mainline import same_theme, theme_key
from market_desk.playbook import suggest_risk_qty

from market_desk.verdict.common import (
    _batch_plan_lots,
    _blocks_chi_star_stocks,
    _chase_price,
    _demote_buy_to_wait,
    _fmt_num,
    _fmt_pct,
    _headline_text,
    _is_chi_star_etf,
    _is_etf_code,
    _join_hint,
    _px,
    _stop_line,
    _stop_price,
    _wait_price,
)


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
                _demote_buy_to_wait(item)
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
        # Canonical suggest for review/backtest; survives demote-to-wait on the card.
        "plan_price": _px(buy, digits),
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
                _demote_buy_to_wait(item)
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
