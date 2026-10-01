"""Narrative graph (shadow): cross-industry concept clusters of today's limit-ups."""

from __future__ import annotations

from typing import Any

from market_desk.filters import normalize_code


def _pool_codes(board: dict[str, Any]) -> set[str]:
    """Return normalized member codes of one board card."""
    return {
        c
        for c in (normalize_code(m.get("code")) for m in (board.get("pool") or board.get("members") or []))
        if c
    }


def _seal_key(raw: Any) -> int:
    """Sort key for an ``HHMMSS`` first-seal stamp (missing sorts last)."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 999999


def build_narrative_clusters(
    concept_boards: list[dict[str, Any]] | None,
    zt_rows: list[dict[str, Any]] | None,
    *,
    industry_boards: list[dict[str, Any]] | None = None,
    mainline: str = "",
) -> list[dict[str, Any]]:
    """Cluster concept boards by shared limit-ups and keep cross-industry ones.

    Nodes are concept boards holding at least ``NARR_MIN_SHARED`` of today's
    limit-ups; two concepts link when the Jaccard overlap of their limit-up
    sets reaches ``NARR_EDGE_JACCARD``. Connected components become clusters;
    a cluster is a candidate when it spans ``NARR_MIN_ZT`` limit-ups across
    ``NARR_MIN_INDUSTRIES`` industries. Pseudo / junk concepts are skipped.

    Args:
        concept_boards: Concept board cards with ``pool`` / ``members``.
        zt_rows: Today's limit-up rows (``code`` / ``name`` / ``industry`` / ``boards``).
        industry_boards: Industry cards used when a limit-up row lacks ``industry``.
        mainline: Live mainline name, used to flag hidden (non-mainline) clusters.

    Returns:
        Candidate clusters sorted by strength, each with ``label`` / ``concepts`` /
        ``zt_n`` / ``industries`` / ``industry_names`` / ``leaders`` / ``hidden``.
    """
    from market_desk.config import (
        CONCEPT_JUNK_KEYWORDS,
        NARR_EDGE_JACCARD,
        NARR_MIN_INDUSTRIES,
        NARR_MIN_SHARED,
        NARR_MIN_ZT,
    )
    from market_desk.mainline import is_pseudo_board

    zt_by_code = {
        normalize_code(r.get("code")): r for r in zt_rows or [] if normalize_code(r.get("code"))
    }
    if not zt_by_code:
        return []
    industry_of: dict[str, str] = {}
    for code, row in zt_by_code.items():
        ind = str(row.get("industry") or "").strip()
        if ind:
            industry_of[code] = ind
    for board in industry_boards or []:
        name = str(board.get("name") or "").strip()
        for code in _pool_codes(board) & set(zt_by_code):
            industry_of.setdefault(code, name)

    nodes: dict[str, set[str]] = {}
    for board in concept_boards or []:
        name = str(board.get("name") or "").strip()
        if not name or name in nodes or is_pseudo_board(name):
            continue
        if any(k in name for k in CONCEPT_JUNK_KEYWORDS):
            continue
        hit = _pool_codes(board) & set(zt_by_code)
        if len(hit) >= int(NARR_MIN_SHARED):
            nodes[name] = hit
    if not nodes:
        return []

    names = list(nodes)
    parent = {n: n for n in names}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            inter = len(nodes[a] & nodes[b])
            if not inter:
                continue
            jac = inter / len(nodes[a] | nodes[b])
            if jac >= float(NARR_EDGE_JACCARD):
                parent[_find(a)] = _find(b)

    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(_find(n), []).append(n)

    out: list[dict[str, Any]] = []
    for members in groups.values():
        members.sort(key=lambda n: (-len(nodes[n]), n))
        codes = set().union(*(nodes[n] for n in members))
        inds = sorted({industry_of[c] for c in codes if industry_of.get(c)})
        if len(codes) < int(NARR_MIN_ZT) or len(inds) < int(NARR_MIN_INDUSTRIES):
            continue
        leaders = sorted(
            (zt_by_code[c] for c in codes),
            key=lambda r: (-int(r.get("boards") or 1), _seal_key(r.get("first_seal"))),
        )[:3]
        is_main = bool(mainline) and mainline in members
        out.append(
            {
                "label": members[0],
                "concepts": members[:6],
                "zt_n": len(codes),
                "industries": len(inds),
                "industry_names": inds[:5],
                "leaders": [str(r.get("name") or r.get("code") or "") for r in leaders],
                "hidden": not is_main,
                "score": round(len(codes) + 0.5 * len(inds), 1),
            }
        )
    out.sort(key=lambda c: (-c["score"], c["label"]))
    return out[:5]


def summarize_leads(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Summarize shadow lead times (first sighting → became mainline) in minutes.

    Args:
        rows: ``load_narrative_shadow`` rows.

    Returns:
        ``{"days", "sightings", "hits", "median_lead_min", "hit_rate"}``.
    """

    def _mins(t: str) -> int | None:
        try:
            return int(t[:2]) * 60 + int(t[3:5])
        except (TypeError, ValueError):
            return None

    rows = list(rows or [])
    hidden = [
        r
        for r in rows
        if r.get("mainline_at_first") != r.get("label")
        and r.get("mainline_at_first") not in (r.get("concepts") or [])
    ]
    leads: list[int] = []
    for r in hidden:
        a, b = _mins(str(r.get("first_seen") or "")), _mins(str(r.get("became_mainline_at") or ""))
        if a is not None and b is not None and b >= a:
            leads.append(b - a)
    leads.sort()
    med = leads[len(leads) // 2] if leads else None
    return {
        "days": len({r.get("trade_date") for r in rows}),
        "sightings": len(hidden),
        "hits": len(leads),
        "median_lead_min": med,
        "hit_rate": round(len(leads) / len(hidden), 2) if hidden else None,
    }
