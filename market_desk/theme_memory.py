"""Board affinity (similarity) and theme reputation (anti one-day wonder).

Similarity blends theme families with smarter constituent overlap: weighted
members, top movers, leader cross-hit, and board co-move. Reputation records
whether a sticky theme persisted or faded the next session, then soft-feeds
``mainline_score`` — never a hard buy ban.
"""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    THEME_COMOVE_BONUS,
    THEME_FADE_PCT_MAX,
    THEME_FADE_ZT_DROP,
    THEME_LEADER_CROSS_BONUS,
    THEME_MEMBER_SIM_WEIGHT,
    THEME_PERSIST_PCT_MIN,
    THEME_PERSIST_ZT_MIN,
    THEME_REP_ADJ_MAX,
    THEME_REP_ADJ_MIN,
    THEME_REP_CONF_DENOM,
    THEME_REP_DECAY,
    THEME_REP_EXTREME_RATE,
    THEME_REP_MIN_SAMPLES,
    THEME_REP_NEWEST_TIP,
    THEME_REP_RATE_SCALE,
    THEME_REP_SETTLE_MAX,
    THEME_REP_SETTLE_PCT_MIN,
    THEME_REP_SETTLE_ZT_MIN,
    THEME_REP_STREAK_BONUS,
    THEME_REP_THIN_FEED_MULT,
    THEME_REP_THIN_N,
    THEME_SIM_INHERIT,
    THEME_SIM_INHERIT_MIN,
    THEME_SIM_PEER_MIN,
    THEME_SIM_POS_INHERIT,
    THEME_SIM_POS_INHERIT_MIN,
    THEME_TOP_K,
    THEME_TOP_OVERLAP_WEIGHT,
    THEME_WEIGHTED_MEMBER_WEIGHT,
)
from market_desk.filters import normalize_code
from market_desk.mainline import same_theme, theme_key


def _member_rows(board: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return constituent rows from pool/members (dicts only)."""
    out: list[dict[str, Any]] = []
    b = board or {}
    for key in ("pool", "members"):
        for row in b.get(key) or []:
            if isinstance(row, dict) and normalize_code(row.get("code")):
                out.append(row)
    return out


def _member_codes(board: dict[str, Any] | None) -> set[str]:
    """Collect normalized constituent codes from pool/members."""
    return {normalize_code(r.get("code")) for r in _member_rows(board)}


def _member_weights(
    board: dict[str, Any] | None,
    *,
    extra_leaders: set[str] | None = None,
) -> dict[str, float]:
    """Weight constituents by day strength; leaders get a boost."""
    leaders = set(extra_leaders or set())
    code = normalize_code((board or {}).get("leader_code"))
    if code:
        leaders.add(code)
    weights: dict[str, float] = {}
    for row in _member_rows(board):
        c = normalize_code(row.get("code"))
        if not c:
            continue
        try:
            pct = abs(float(row.get("pct"))) if row.get("pct") is not None else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        w = 1.0 + min(pct, 12.0) / 4.0
        if c in leaders:
            w += 2.5
        try:
            mv = float(row.get("mv_yi")) if row.get("mv_yi") is not None else 0.0
            if mv >= 200:
                w += 0.4
            elif mv >= 80:
                w += 0.2
        except (TypeError, ValueError):
            pass
        weights[c] = max(weights.get(c, 0.0), w)
    return weights


def _top_mover_codes(board: dict[str, Any] | None, k: int) -> set[str]:
    """Codes of the top-|pct| constituents (active overlap lens)."""
    scored: list[tuple[float, str]] = []
    for row in _member_rows(board):
        c = normalize_code(row.get("code"))
        if not c:
            continue
        try:
            pct = abs(float(row.get("pct"))) if row.get("pct") is not None else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        scored.append((pct, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {c for _, c in scored[: max(1, int(k))]}


def jaccard(a: set[str], b: set[str]) -> float:
    """Return Jaccard similarity of two code sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


def weighted_jaccard(wa: dict[str, float], wb: dict[str, float]) -> float:
    """Return weight-aware Jaccard on two code→weight maps."""
    if not wa or not wb:
        return 0.0
    keys = set(wa) | set(wb)
    inter = 0.0
    union = 0.0
    for c in keys:
        a = float(wa.get(c) or 0.0)
        b = float(wb.get(c) or 0.0)
        inter += min(a, b)
        union += max(a, b)
    if union <= 0:
        return 0.0
    return inter / union


def _board_pct(board: dict[str, Any] | None) -> float | None:
    """Parse board-level day percent change."""
    try:
        if (board or {}).get("pct") is None:
            return None
        return float(board.get("pct"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _comove_score(pa: float | None, pb: float | None) -> float:
    """Return 0..1 alignment of two board day moves (same sign + similar size)."""
    if pa is None or pb is None:
        return 0.0
    if pa == 0 and pb == 0:
        return 0.55
    if pa * pb <= 0:
        return 0.0
    mag = min(abs(pa), abs(pb))
    spread = abs(abs(pa) - abs(pb))
    base = min(1.0, mag / 2.5)
    damp = max(0.0, 1.0 - spread / 4.0)
    return max(0.0, min(1.0, 0.35 + 0.65 * base * damp))


def board_similarity_detail(
    a: dict[str, Any] | None,
    b: dict[str, Any] | None,
) -> dict[str, Any]:
    """Explain and score how related two board cards are.

    Blends theme family, full-pool Jaccard, strength-weighted member overlap,
    top-mover overlap, leader cross-hit, and board-level co-movement.
    """
    empty: dict[str, Any] = {
        "sim": 0.0,
        "theme_hit": False,
        "jac": 0.0,
        "wjac": 0.0,
        "top_jac": 0.0,
        "leader_hit": False,
        "comove": 0.0,
        "why": [],
        "shared_n": 0,
    }
    if not a or not b:
        return empty
    na = str(a.get("name") or "").strip()
    nb = str(b.get("name") or "").strip()
    if not na or not nb:
        return empty
    if na == nb:
        return {
            **empty,
            "sim": 1.0,
            "theme_hit": True,
            "jac": 1.0,
            "wjac": 1.0,
            "why": ["同名板块"],
        }

    theme_hit = same_theme(na, nb)
    codes_a = _member_codes(a)
    codes_b = _member_codes(b)
    shared = codes_a & codes_b
    jac = jaccard(codes_a, codes_b)

    lead_a = normalize_code(a.get("leader_code"))
    lead_b = normalize_code(b.get("leader_code"))
    leaders = {c for c in (lead_a, lead_b) if c}
    wa = _member_weights(a, extra_leaders=leaders)
    wb = _member_weights(b, extra_leaders=leaders)
    wjac = weighted_jaccard(wa, wb)

    top_k = int(THEME_TOP_K)
    top_jac = jaccard(_top_mover_codes(a, top_k), _top_mover_codes(b, top_k))

    leader_hit = False
    if lead_a and lead_b and lead_a == lead_b:
        leader_hit = True
    elif lead_a and lead_a in codes_b:
        leader_hit = True
    elif lead_b and lead_b in codes_a:
        leader_hit = True

    pa, pb = _board_pct(a), _board_pct(b)
    comove = _comove_score(pa, pb)

    soft_name = 0.0
    if len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na):
        soft_name = 0.28

    why: list[str] = []
    if theme_hit:
        why.append("同主题族")
    if soft_name:
        why.append("名称包含")
    if shared:
        why.append(f"共用{len(shared)}只成分")
    if wjac >= 0.12:
        why.append("强势股重合")
    if top_jac >= 0.15:
        why.append("活跃股重合")
    if leader_hit:
        why.append("龙头互含")
    if comove >= 0.45:
        why.append("涨跌同向")

    if theme_hit:
        sim = (
            0.78
            + 0.10 * max(jac, wjac)
            + 0.06 * top_jac
            + (float(THEME_LEADER_CROSS_BONUS) if leader_hit else 0.0)
            + 0.04 * comove
        )
    else:
        sim = (
            soft_name
            + float(THEME_MEMBER_SIM_WEIGHT) * jac
            + float(THEME_WEIGHTED_MEMBER_WEIGHT) * wjac
            + float(THEME_TOP_OVERLAP_WEIGHT) * top_jac
            + (float(THEME_LEADER_CROSS_BONUS) if leader_hit else 0.0)
            + float(THEME_COMOVE_BONUS) * comove
        )
        # Thin name-only / co-move without stock evidence stays weak.
        if not shared and not leader_hit and soft_name < 0.2 and comove < 0.5:
            sim *= 0.35

    sim = max(0.0, min(1.0, round(float(sim), 4)))
    return {
        "sim": sim,
        "theme_hit": theme_hit,
        "jac": round(jac, 3),
        "wjac": round(wjac, 3),
        "top_jac": round(top_jac, 3),
        "leader_hit": leader_hit,
        "comove": round(comove, 3),
        "why": why[:4],
        "shared_n": len(shared),
    }


def board_similarity(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float:
    """Score how related two board cards are in [0, 1]."""
    return float(board_similarity_detail(a, b).get("sim") or 0.0)


def _outcome_event_weight(row: dict[str, Any] | None) -> float:
    """Scale one fade/persist event by how informative the heat change was.

    Strong prior boards that collapse hard weigh more as fades; growing
    next-day limit-ups weigh more as persists. Barely-alive persists weigh less.
    """
    r = row or {}
    try:
        zt = int(r.get("zt_n") or 0)
    except (TypeError, ValueError):
        zt = 0
    try:
        nzt = int(r.get("next_zt_n") or 0)
    except (TypeError, ValueError):
        nzt = 0
    try:
        npct = float(r.get("next_pct") or 0)
    except (TypeError, ValueError):
        npct = 0.0
    # Prior heat: thin themes are less diagnostic.
    heat = 0.75 + min(max(zt, 0), 10) / 10.0 * 0.50  # 0.75..1.25
    outcome = str(r.get("outcome") or "")
    if outcome == "fade":
        if zt >= 2:
            ratio = nzt / float(zt)
            if ratio <= 0.35:
                heat *= 1.20
            elif ratio <= 0.55:
                heat *= 1.08
        if npct <= -1.0:
            heat *= 1.08
    elif outcome == "persist":
        if nzt >= max(zt, int(THEME_PERSIST_ZT_MIN)) and nzt >= zt:
            heat *= 1.12
        elif zt >= 3 and nzt <= max(1, zt - 2):
            heat *= 0.85  # survived on paper, but clearly cooled
        if npct >= float(THEME_PERSIST_PCT_MIN) + 1.0:
            heat *= 1.05
    return max(0.55, min(1.45, heat))


def compute_rep_adj(
    fade_n: float,
    persist_n: float,
    *,
    sample_n: float | None = None,
    streak_bonus: float = 0.0,
) -> float:
    """Map fade/persist weights into a soft mainline score adjustment.

    Primary signal is the net persist−fade rate (symmetric). Thin samples are
    shrunk by a confidence curve; lopsided habits and streaks add a small tip.
    Raw counts are no longer double-counted on top of the rate (avoids slamming
    −12 after a few fades while persist struggles to climb).
    """
    f = max(0.0, float(fade_n))
    p = max(0.0, float(persist_n))
    n = float(sample_n) if sample_n is not None else (f + p)
    if n <= 0:
        return 0.0
    fade_rate = f / n
    persist_rate = p / n
    net = persist_rate - fade_rate  # [-1, 1]
    adj = net * float(THEME_REP_RATE_SCALE)

    extreme = float(THEME_REP_EXTREME_RATE)
    if n >= float(THEME_REP_MIN_SAMPLES):
        if fade_rate >= extreme:
            adj -= min(2.5, (fade_rate - 0.50) * 5.0)
        if persist_rate >= extreme:
            adj += min(2.0, (persist_rate - 0.50) * 4.0)

    # Continuous confidence: 1 sample ≈0.3×, ~3.5 weight ≈ full.
    denom = max(1.0, float(THEME_REP_CONF_DENOM))
    conf = min(1.0, n / denom)
    adj *= 0.30 + 0.70 * conf

    adj += float(streak_bonus or 0.0)
    return max(float(THEME_REP_ADJ_MIN), min(float(THEME_REP_ADJ_MAX), round(adj, 2)))


def label_for_rep(fade_n: float, persist_n: float, adj: float) -> str:
    """Human label for theme reputation chips."""
    n = float(fade_n) + float(persist_n)
    if n <= 0:
        return "样本不足"
    if n < float(THEME_REP_MIN_SAMPLES):
        return "观察中"
    if adj <= -5:
        return "易一日游"
    if adj <= -2:
        return "偏一日游"
    if adj >= 4:
        return "偏粘"
    if adj >= 1.5:
        return "偏续热"
    return "中性"


def attach_board_affinity(
    boards: list[dict[str, Any]] | None,
    reputation: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Annotate boards with similar peers and reputation soft adj.

    Mutates cards in place and returns the same list for chaining.
    """
    cards = list(boards or [])
    rep = reputation or {}
    for i, board in enumerate(cards):
        name = str(board.get("name") or "")
        theme = theme_key(name) or name
        peers: list[dict[str, Any]] = []
        for j, other in enumerate(cards):
            if i == j:
                continue
            detail = board_similarity_detail(board, other)
            sim = float(detail.get("sim") or 0)
            if sim < float(THEME_SIM_PEER_MIN):
                continue
            peers.append(
                {
                    "name": other.get("name"),
                    "bk": other.get("bk"),
                    "sim": round(sim, 2),
                    "theme": theme_key(str(other.get("name") or "")),
                    "why": list(detail.get("why") or [])[:3],
                    "shared_n": int(detail.get("shared_n") or 0),
                    "leader_hit": bool(detail.get("leader_hit")),
                    "wjac": detail.get("wjac"),
                    "comove": detail.get("comove"),
                }
            )
        peers.sort(key=lambda x: float(x.get("sim") or 0), reverse=True)
        board["theme"] = theme
        board["similar_peers"] = peers[:4]

        own = rep.get(theme) or {}
        own_adj = float(own.get("score_adj") or 0)
        auto_adj = float(own.get("auto_adj") if own.get("auto_adj") is not None else own_adj)
        manual_adj = float(own.get("manual_adj") or 0)
        inherit = 0.0
        for peer in peers[:3]:
            sim = float(peer.get("sim") or 0)
            pt = str(peer.get("theme") or "")
            if not pt or pt == theme:
                continue
            pref = rep.get(pt) or {}
            padj = float(pref.get("score_adj") or 0)
            if padj < 0 and sim >= float(THEME_SIM_INHERIT_MIN):
                inherit += padj * sim * float(THEME_SIM_INHERIT)
            elif padj > 0 and sim >= float(THEME_SIM_POS_INHERIT_MIN):
                # Milder than negative inherit; only strong peers and sticky labels.
                plabel = str(pref.get("label") or "")
                if plabel in ("偏粘", "偏续热") or float(pref.get("persist_n") or 0) >= 2:
                    inherit += padj * sim * float(THEME_SIM_POS_INHERIT)
        # Cap inheritance so peers cannot dominate own track record.
        if inherit < 0:
            if own_adj < 0:
                inherit = max(inherit, own_adj * 0.5)
            else:
                inherit = max(inherit, -abs(own_adj) - 3.0)
                inherit = max(inherit, -4.0)
        elif inherit > 0:
            inherit = min(inherit, 3.0)
            if own_adj > 0:
                inherit = min(inherit, abs(own_adj) * 0.6 + 1.0)
        total = own_adj + inherit
        total = max(float(THEME_REP_ADJ_MIN), min(float(THEME_REP_ADJ_MAX), round(total, 2)))
        trade_adj = float(own.get("trade_adj") or 0)
        fade_n = int(own.get("fade_n") or 0)
        persist_n = int(own.get("persist_n") or 0)
        sample_n = fade_n + persist_n
        # Thin history: keep full score for UI, shrink what mainline_score eats.
        feed = total
        if sample_n < int(THEME_REP_THIN_N):
            feed = round(float(total) * float(THEME_REP_THIN_FEED_MULT), 2)
        board["rep_adj"] = feed
        board["rep_adj_full"] = total
        board["rep_thin"] = sample_n < int(THEME_REP_THIN_N)
        board["rep_own_adj"] = own_adj
        board["rep_auto_adj"] = auto_adj
        board["rep_manual_adj"] = manual_adj
        board["rep_trade_adj"] = trade_adj
        board["rep_fade_n"] = fade_n
        board["rep_persist_n"] = persist_n
        board["rep_sample_n"] = sample_n
        # Historical next-day heat rate (persist / graded days); None if thin.
        if sample_n >= int(THEME_REP_MIN_SAMPLES):
            board["rep_persist_rate"] = round(100.0 * persist_n / float(sample_n), 1)
        else:
            board["rep_persist_rate"] = None
        board["rep_label"] = label_for_rep(fade_n, persist_n, own_adj)
    return cards


def theme_stats_from_boards(rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Aggregate board_daily-like rows into theme → strength stats.

    Sibling boards in one theme (e.g. 煤炭 / 动力煤) often share the same
    limit-up stocks. Using ``max(zt_n)`` instead of a sum avoids double-counting
    those stocks when grading fade / persist.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        theme = theme_key(name) or name
        zt = int(row.get("zt_n") or 0)
        try:
            pct = float(row.get("pct")) if row.get("pct") is not None else 0.0
        except (TypeError, ValueError):
            pct = 0.0
        status = str(row.get("status") or "")
        cur = out.get(theme)
        if not cur:
            out[theme] = {
                "theme": theme,
                "zt_n": zt,
                "pct": pct,
                "status": status,
                "names": [name],
                "board_n": 1,
            }
            continue
        # Do not sum zt_n across sibling boards — overlapping members inflate.
        cur["zt_n"] = max(int(cur.get("zt_n") or 0), zt)
        cur["pct"] = max(float(cur.get("pct") or 0), pct)
        cur["names"] = list(dict.fromkeys(list(cur.get("names") or []) + [name]))
        cur["board_n"] = int(cur.get("board_n") or 0) + 1
        if status == "退潮" or (status and not cur.get("status")):
            cur["status"] = status
        elif status == "尖峰禁追" and cur.get("status") not in ("退潮",):
            cur["status"] = status
    return out


def classify_theme_next_day(
    prior: dict[str, Any] | None,
    nxt: dict[str, Any] | None,
    *,
    was_mainline: bool = False,
) -> str:
    """Return fade / persist / unclear for one theme across consecutive sessions.

    Fade is intentionally strict: missing boards and bare 「退潮」 alone are not
    enough unless heat also collapsed.
    """
    if not prior:
        return "unclear"
    p_zt = int((prior or {}).get("zt_n") or 0)
    try:
        p_pct = float((prior or {}).get("pct") or 0)
    except (TypeError, ValueError):
        p_pct = 0.0
    # Only grade themes that were meaningfully hot yesterday.
    if p_zt < 2 and p_pct < 1.5:
        return "unclear"
    drop_thr = max(1, int(round(p_zt * float(THEME_FADE_ZT_DROP))))
    if not nxt:
        # Missing from today's pool: only fade sticky/mainline or very strong prior.
        if was_mainline or p_zt >= 5:
            return "fade"
        return "unclear"
    n_zt = int(nxt.get("zt_n") or 0)
    try:
        n_pct = float(nxt.get("pct") or 0)
    except (TypeError, ValueError):
        n_pct = 0.0
    status = str(nxt.get("status") or "")
    collapsed = n_zt <= drop_thr and n_pct <= float(THEME_FADE_PCT_MAX)
    # 「退潮」 is soft: only fade when heat also collapsed (or mainline vanished);
    # still-hot 退潮 stays unclear so we do not credit persist either.
    if status == "退潮":
        if collapsed or was_mainline:
            return "fade"
        return "unclear"
    if n_zt >= int(THEME_PERSIST_ZT_MIN) or n_pct >= float(THEME_PERSIST_PCT_MIN):
        return "persist"
    if collapsed:
        return "fade"
    return "unclear"


def settle_theme_reputation(trade_date: str) -> dict[str, Any]:
    """Settle yesterday→today theme outcomes once per trade date.

    Uses ``daily_snapshot`` mainline plus ``board_daily`` aggregates. Idempotent
    via ``theme_day_outcome`` primary key. Grades mainline plus secondary hot
    themes (wider than the old top-6 / zt≥3 gate).
    """
    from market_desk.db import (
        load_board_rows_for_date,
        load_daily,
        load_theme_day_outcomes,
        record_theme_day_outcome,
    )

    day = str(trade_date or "")[:10]
    if not day:
        return {"ok": False, "reason": "no-date"}
    hist = load_daily(8)
    prior_day = None
    for row in hist:
        d = str(row.get("trade_date") or "")[:10]
        if d and d < day:
            prior_day = d
            break
    if not prior_day:
        return {"ok": True, "settled": 0, "reason": "no-prior"}

    already = {
        str(r.get("theme_key") or "")
        for r in load_theme_day_outcomes(prior_day)
        if str(r.get("next_date") or "")[:10] == day
    }

    prior_boards = load_board_rows_for_date(prior_day)
    today_boards = load_board_rows_for_date(day)
    prior_stats = theme_stats_from_boards(prior_boards)
    today_stats = theme_stats_from_boards(today_boards)

    prior_ml = ""
    for row in hist:
        if str(row.get("trade_date") or "")[:10] == prior_day:
            prior_ml = str(row.get("mainline") or "").strip()
            break
    ml_theme = theme_key(prior_ml) if prior_ml else ""
    focus: list[str] = []
    if ml_theme:
        focus.append(ml_theme)
    ranked = sorted(
        prior_stats.items(),
        key=lambda kv: (int(kv[1].get("zt_n") or 0), float(kv[1].get("pct") or 0)),
        reverse=True,
    )
    settle_max = int(THEME_REP_SETTLE_MAX)
    zt_floor = int(THEME_REP_SETTLE_ZT_MIN)
    pct_floor = float(THEME_REP_SETTLE_PCT_MIN)
    for theme, st in ranked:
        if theme in focus:
            continue
        if int(st.get("zt_n") or 0) < zt_floor and float(st.get("pct") or 0) < pct_floor:
            continue
        focus.append(theme)
        if len(focus) >= settle_max:
            break

    settled = 0
    fades = 0
    persists = 0
    for theme in focus:
        if not theme or theme in already:
            continue
        outcome = classify_theme_next_day(
            prior_stats.get(theme),
            today_stats.get(theme),
            was_mainline=(theme == ml_theme),
        )
        if outcome == "unclear":
            continue
        record_theme_day_outcome(
            {
                "trade_date": prior_day,
                "next_date": day,
                "theme_key": theme,
                "outcome": outcome,
                "mainline": prior_ml or None,
                "zt_n": (prior_stats.get(theme) or {}).get("zt_n"),
                "next_zt_n": (today_stats.get(theme) or {}).get("zt_n"),
                "pct": (prior_stats.get(theme) or {}).get("pct"),
                "next_pct": (today_stats.get(theme) or {}).get("pct"),
            }
        )
        _refresh_theme_rep_from_outcomes(theme)
        settled += 1
        if outcome == "fade":
            fades += 1
        else:
            persists += 1
    return {
        "ok": True,
        "prior_date": prior_day,
        "trade_date": day,
        "settled": settled,
        "fades": fades,
        "persists": persists,
        "mainline": prior_ml or None,
        "focus_n": len(focus),
    }


def _refresh_theme_rep_from_outcomes(theme: str, limit: int = 12) -> None:
    """Rebuild one theme's reputation with time decay on older outcomes."""
    from market_desk.db import load_theme_outcomes_for_theme, upsert_theme_reputation

    rows = load_theme_outcomes_for_theme(theme, limit=limit)
    fade_w = 0.0
    persist_w = 0.0
    raw_fade = 0
    raw_persist = 0
    decay = float(THEME_REP_DECAY)
    for i, r in enumerate(rows):
        w = (decay ** i) * _outcome_event_weight(r)
        if r.get("outcome") == "fade":
            fade_w += w
            raw_fade += 1
        elif r.get("outcome") == "persist":
            persist_w += w
            raw_persist += 1
    streak_side = ""
    streak = 0
    for r in rows:
        oc = str(r.get("outcome") or "")
        if oc not in ("fade", "persist"):
            break
        if not streak_side:
            streak_side = oc
            streak = 1
            continue
        if oc == streak_side:
            streak += 1
        else:
            break
    streak_bonus = 0.0
    if streak >= 2 and streak_side in ("fade", "persist"):
        mag = float(THEME_REP_STREAK_BONUS)
        if streak >= 3:
            mag *= 1.4
        streak_bonus = mag if streak_side == "persist" else -mag
    # Newest tip: small push from the latest graded day (independent of streak).
    if rows:
        newest = str(rows[0].get("outcome") or "")
        tip = float(THEME_REP_NEWEST_TIP)
        if newest == "persist":
            streak_bonus += tip
        elif newest == "fade":
            streak_bonus -= tip
    sample_w = fade_w + persist_w
    adj = compute_rep_adj(
        fade_w, persist_w, sample_n=sample_w, streak_bonus=streak_bonus
    )
    last_fade = next((r.get("next_date") for r in rows if r.get("outcome") == "fade"), None)
    last_persist = next((r.get("next_date") for r in rows if r.get("outcome") == "persist"), None)
    upsert_theme_reputation(
        {
            "theme_key": theme,
            "fade_n": raw_fade,
            "persist_n": raw_persist,
            "auto_adj": adj,
            "score_adj": adj,
            "last_fade_date": last_fade,
            "last_persist_date": last_persist,
            "label": label_for_rep(raw_fade, raw_persist, adj),
        },
        preserve_manual=True,
    )


def rebuild_all_theme_reputation(
    *,
    force: bool = False,
    limit_per_theme: int = 12,
) -> dict[str, Any]:
    """Recompute every theme's ``auto_adj`` from stored day outcomes.

    Compatibility path when the scoring formula changes: historical rows in
    ``theme_day_outcome`` stay the source of truth; ``manual_adj`` / ``trade_adj``
    are preserved by ``upsert_theme_reputation(preserve_manual=True)``.

    Skips work when settings already record the current
    ``THEME_REP_FORMULA_VERSION`` unless ``force`` is set.
    """
    from market_desk.config import THEME_REP_FORMULA_VERSION
    from market_desk.db import (
        list_theme_outcome_keys,
        load_setting,
        load_theme_reputation,
        save_setting,
    )

    ver = int(THEME_REP_FORMULA_VERSION)
    key = "theme_rep_formula_v"
    if not force:
        try:
            cur = int(load_setting(key) or 0)
        except (TypeError, ValueError):
            cur = 0
        if cur == ver:
            return {"ok": True, "skipped": True, "version": ver, "rebuilt": 0}

    # Themes with a reputation row, plus any theme that only exists in outcomes.
    themes: set[str] = set()
    for r in load_theme_reputation():
        t = str(r.get("theme_key") or "").strip()
        if t:
            themes.add(t)
    for t in list_theme_outcome_keys():
        if t:
            themes.add(str(t).strip())

    rebuilt = 0
    for theme in sorted(themes):
        _refresh_theme_rep_from_outcomes(theme, limit=limit_per_theme)
        rebuilt += 1
    save_setting(key, ver)
    return {
        "ok": True,
        "skipped": False,
        "version": ver,
        "rebuilt": rebuilt,
    }


def reputation_map() -> dict[str, dict[str, Any]]:
    """Load theme_key → reputation row for scoring."""
    from market_desk.db import load_theme_reputation

    rows = load_theme_reputation()
    return {str(r.get("theme_key") or ""): r for r in rows if r.get("theme_key")}
