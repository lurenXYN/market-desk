"""Side-desk recommenders: watch trial, dragon, independent pop, favorites."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import (
    etf_spec_for_name,
    etf_spec_soft_fallback,
    match_mainline_etf,
)
from market_desk.settings import setting

from market_desk.verdict.common import (
    _blocks_chi_star_stocks,
    _demote_buy_to_wait,
    _is_etf_code,
    _join_hint,
    _px,
)
from market_desk.verdict.scoring import (
    _attach_risk_sizing,
    _build_recommend,
    _recommend_item,
    _stock_candidates,
)
from market_desk.verdict.progress import _apply_ready_confirmations, mark_pullback_entries
from market_desk.verdict.sell_advice import build_sell_advice


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

        diff_str = ""
        try:
            lf = float(last) if last not in (None, "") else None
            sf = float(suggest) if suggest not in (None, "") else None
            if lf is not None and sf is not None and sf > 0:
                diff_pct = (lf - sf) / sf * 100.0
                diff_val = lf - sf
                if abs(diff_pct) < 0.1:
                    diff_str = "现价精准贴合买点"
                elif diff_pct > 0:
                    diff_str = f"距建议买点高{diff_pct:+.1f}%(+{diff_val:.{digits}f}元)"
                else:
                    diff_str = f"距建议买点低{abs(diff_pct):.1f}%({diff_val:.{digits}f}元)"
        except (TypeError, ValueError):
            pass

        reason_parts = [p for p in (note, diff_str, "维持0.75倍轻仓纪律", "不改顶栏主线") if p]
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
                "reason": "；".join(reason_parts),
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
            f"观察页「可试探」同步副卡；严格执行×{float(WATCH_TRIAL_SIZE_MULT):g}轻仓试探纪律，"
            "不改 sticky 主线与顶栏方向"
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
            _demote_buy_to_wait(item)
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

    Side/link dragons are included only with setting ``dragon_side_link`` and
    stay observe-only. Mainline dragons arm ready only with
    ``dragon_ready_enabled``; otherwise the row is observation only.
    Codes already used on a higher-priority board are skipped (main > side > link).
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    main_observe = not bool(setting("dragon_ready_enabled", False))
    scopes_in: list[tuple[str, dict[str, Any] | None, bool, bool]] = [
        ("main", main, surge_fresh, main_observe),
    ]
    if setting("dragon_side_link", False):
        scopes_in += [
            ("side", side_board, side_surge, True),
            ("link", link_board, link_surge, True),
        ]
    for scope, board, surge, observe in scopes_in:
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
        "情绪看确认异动，中军看趋势回踩。"
        + ("主线龙头可到位。" if not main_observe else "龙头排默认只观察、不亮灯。")
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
    side_board: dict[str, Any] | None = None,
    link_board: dict[str, Any] | None = None,
    recommend: dict[str, Any] | None = None,
    side_recommend: dict[str, Any] | None = None,
    link_recommend: dict[str, Any] | None = None,
    skip_codes: set[str] | None = None,
    playbook: dict[str, Any] | None = None,
    adapt: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build observe-only cards for independent popular pullbacks.

    Scans sticky mainline plus observation side / link boards. Does not upgrade
    hero action / sticky. Sell path may exempt「昨买今弱」.
    """
    from market_desk.config import INDEPENDENT_POP_MAX
    from market_desk.leaders import build_independent_pullback_candidates

    skip: set[str] = set()
    for box in (recommend, side_recommend, link_recommend):
        for x in ((box or {}).get("items") or []):
            if x.get("kind") == "stock" and x.get("code"):
                cc = normalize_code(x.get("code"))
                if cc:
                    skip.add(cc)
    for c in skip_codes or set():
        cc = normalize_code(c)
        if cc:
            skip.add(cc)
    raw = build_independent_pullback_candidates(
        main,
        side_board=side_board,
        link_board=link_board,
        skip_codes=skip,
        max_items=int(INDEPENDENT_POP_MAX),
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
                "role_label": str(row.get("role_label") or "独立人气·回踩"),
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
                "indep_scope": row.get("indep_scope") or "main",
                "source_board": row.get("source_board"),
                "reason": str(row.get("reason") or "板内独立人气·近低回踩观察"),
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
            "主线/支线/联动板内近低观察（优先独立发散）；不升顶栏可买入。"
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
            _demote_buy_to_wait(item)
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
