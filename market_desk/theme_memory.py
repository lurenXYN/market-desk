"""Board affinity (similarity) and theme reputation (anti one-day wonder).

Similarity combines canonical theme groups with constituent-code overlap so
sibling boards (and near-theme concepts) share identity. Reputation records
whether a sticky theme persisted or faded the next session, then soft-feeds
``mainline_score`` — never a hard buy ban.
"""

from __future__ import annotations

from typing import Any

from market_desk.config import (
    THEME_FADE_PCT_MAX,
    THEME_FADE_ZT_DROP,
    THEME_MEMBER_SIM_WEIGHT,
    THEME_PERSIST_PCT_MIN,
    THEME_PERSIST_ZT_MIN,
    THEME_REP_ADJ_MAX,
    THEME_REP_ADJ_MIN,
    THEME_REP_DECAY,
    THEME_REP_EARLY_MULT,
    THEME_REP_MIN_SAMPLES,
    THEME_REP_STREAK_BONUS,
    THEME_SIM_INHERIT,
    THEME_SIM_INHERIT_MIN,
    THEME_SIM_PEER_MIN,
    THEME_SIM_POS_INHERIT,
    THEME_SIM_POS_INHERIT_MIN,
)
from market_desk.filters import normalize_code
from market_desk.mainline import same_theme, theme_key


def _member_codes(board: dict[str, Any] | None) -> set[str]:
    """Collect normalized constituent codes from pool/members."""
    out: set[str] = set()
    b = board or {}
    for key in ("pool", "members"):
        for row in b.get(key) or []:
            code = normalize_code((row or {}).get("code"))
            if code:
                out.add(code)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    """Return Jaccard similarity of two code sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / float(len(a | b))


def board_similarity(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float:
    """Score how related two board cards are in [0, 1].

    Exact same theme_key → at least 0.85; member overlap can push to 1.0.
    Different themes still score via constituent Jaccard (cross-listed concepts).
    """
    if not a or not b:
        return 0.0
    na = str(a.get("name") or "").strip()
    nb = str(b.get("name") or "").strip()
    if not na or not nb or na == nb:
        return 1.0 if na and na == nb else 0.0
    theme_hit = same_theme(na, nb)
    codes_a = _member_codes(a)
    codes_b = _member_codes(b)
    jac = jaccard(codes_a, codes_b)
    if theme_hit:
        return min(1.0, 0.85 + jac * 0.15)
    # Soft name containment (东方通信 vs 通信设备) without group hit.
    soft = 0.0
    if len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na):
        soft = 0.35
    return min(1.0, jac * float(THEME_MEMBER_SIM_WEIGHT) + soft)


def compute_rep_adj(
    fade_n: float,
    persist_n: float,
    *,
    sample_n: float | None = None,
    streak_bonus: float = 0.0,
) -> float:
    """Map fade/persist weights into a soft mainline score adjustment.

    Persist is weighted more symmetrically vs fade so sticky themes can earn a
    meaningful bonus; optional ``streak_bonus`` covers consecutive recent persist.
    """
    f = max(0.0, float(fade_n))
    p = max(0.0, float(persist_n))
    n = float(sample_n) if sample_n is not None else (f + p)
    if n <= 0:
        return 0.0
    if n < float(THEME_REP_MIN_SAMPLES):
        adj = (-1.5 * f + 1.2 * p) * float(THEME_REP_EARLY_MULT)
    else:
        fade_rate = f / float(n) if n else 0.0
        persist_rate = p / float(n) if n else 0.0
        adj = (
            -fade_rate * 10.0
            - f * 1.2
            + persist_rate * 8.0
            + p * 1.2
            + max(0.0, float(streak_bonus))
        )
    return max(float(THEME_REP_ADJ_MIN), min(float(THEME_REP_ADJ_MAX), round(adj, 2)))


def label_for_rep(fade_n: float, persist_n: float, adj: float) -> str:
    """Human label for theme reputation chips."""
    n = float(fade_n) + float(persist_n)
    if n <= 0:
        return "样本不足"
    if n < float(THEME_REP_MIN_SAMPLES):
        return "观察中"
    if adj <= -6:
        return "易一日游"
    if adj <= -2:
        return "偏一日游"
    if adj >= 4:
        return "偏粘"
    if adj >= 2:
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
            sim = board_similarity(board, other)
            if sim < float(THEME_SIM_PEER_MIN):
                continue
            peers.append(
                {
                    "name": other.get("name"),
                    "bk": other.get("bk"),
                    "sim": round(sim, 2),
                    "theme": theme_key(str(other.get("name") or "")),
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
        board["rep_adj"] = total
        board["rep_own_adj"] = own_adj
        board["rep_auto_adj"] = auto_adj
        board["rep_manual_adj"] = manual_adj
        board["rep_label"] = label_for_rep(
            float(own.get("fade_n") or 0),
            float(own.get("persist_n") or 0),
            own_adj,
        )
        board["rep_fade_n"] = int(own.get("fade_n") or 0)
        board["rep_persist_n"] = int(own.get("persist_n") or 0)
    return cards


def theme_stats_from_boards(rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Aggregate board_daily-like rows into theme → strength stats."""
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
            }
            continue
        cur["zt_n"] = int(cur.get("zt_n") or 0) + zt
        cur["pct"] = max(float(cur.get("pct") or 0), pct)
        cur["names"] = list(dict.fromkeys(list(cur.get("names") or []) + [name]))
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
    """Return fade / persist / unclear for one theme across consecutive sessions."""
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
    if not nxt:
        # Missing from today's pool: only fade sticky/mainline or strong prior.
        if was_mainline or p_zt >= 4:
            return "fade"
        return "unclear"
    n_zt = int(nxt.get("zt_n") or 0)
    try:
        n_pct = float(nxt.get("pct") or 0)
    except (TypeError, ValueError):
        n_pct = 0.0
    status = str(nxt.get("status") or "")
    if status == "退潮":
        return "fade"
    if n_zt >= int(THEME_PERSIST_ZT_MIN) or n_pct >= float(THEME_PERSIST_PCT_MIN):
        return "persist"
    drop_thr = max(1, int(round(p_zt * float(THEME_FADE_ZT_DROP))))
    if n_zt <= drop_thr and n_pct <= float(THEME_FADE_PCT_MAX):
        return "fade"
    return "unclear"


def settle_theme_reputation(trade_date: str) -> dict[str, Any]:
    """Settle yesterday→today theme outcomes once per trade date.

    Uses ``daily_snapshot`` mainline plus ``board_daily`` aggregates. Idempotent
    via ``theme_day_outcome`` primary key.
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
    for theme, st in ranked:
        if theme in focus:
            continue
        if int(st.get("zt_n") or 0) < 3 and float(st.get("pct") or 0) < 2.0:
            continue
        focus.append(theme)
        if len(focus) >= 6:
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
        w = decay ** i
        if r.get("outcome") == "fade":
            fade_w += w
            raw_fade += 1
        elif r.get("outcome") == "persist":
            persist_w += w
            raw_persist += 1
    streak = 0
    for r in rows:
        if r.get("outcome") == "persist":
            streak += 1
        else:
            break
    streak_bonus = float(THEME_REP_STREAK_BONUS) if streak >= 2 else 0.0
    if streak >= 3:
        streak_bonus += float(THEME_REP_STREAK_BONUS) * 0.5
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
            "label": label_for_rep(fade_w, persist_w, adj),
        },
        preserve_manual=True,
    )


def reputation_map() -> dict[str, dict[str, Any]]:
    """Load theme_key → reputation row for scoring."""
    from market_desk.db import load_theme_reputation

    rows = load_theme_reputation()
    return {str(r.get("theme_key") or ""): r for r in rows if r.get("theme_key")}
