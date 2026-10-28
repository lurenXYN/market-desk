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
                "leader_rows": [
                    {
                        "code": str(r.get("code") or "").zfill(6),
                        "name": str(r.get("name") or r.get("code") or ""),
                        "last": r.get("price") or r.get("last"),
                        "pct": r.get("pct"),
                        "high": r.get("high"),
                        "low": r.get("low"),
                        "first_seal": r.get("first_seal"),
                        "boards": r.get("boards"),
                    }
                    for r in leaders
                    if str(r.get("code") or "").strip()
                ],
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


def _px(value: Any, digits: int = 2) -> float | None:
    """Round a price-like value, or None when missing."""
    try:
        if value is None or value == "":
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def build_narrative_recommend(
    clusters: list[dict[str, Any]] | None,
    *,
    skip_codes: set[str] | None = None,
    max_items: int = 4,
) -> dict[str, Any] | None:
    """Soft-recommend limit-up leaders from hidden narrative clusters.

    Observe cards only (``ready=False``): surfaces names for the desk fold so the
    graph shadow can auto-list candidates without bypassing buy gates.
    """
    skip = {normalize_code(c) for c in (skip_codes or set()) if normalize_code(c)}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cluster in clusters or []:
        if not cluster.get("hidden"):
            continue
        label = str(cluster.get("label") or "")
        for row in cluster.get("leader_rows") or []:
            code = normalize_code(row.get("code"))
            if not code or code in skip or code in seen:
                continue
            seen.add(code)
            last = row.get("last") or row.get("price")
            items.append(
                {
                    "kind": "stock",
                    "kind_label": "个股",
                    "role": "alt",
                    "role_label": f"图谱·{label}" if label else "图谱·影子",
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
                    "narrative_shadow": True,
                    "desk_source": "narrative",
                    "source_board": label,
                    "reason": (
                        f"图谱隐形簇「{label}」龙头观察 · "
                        f"{cluster.get('zt_n') or '?'}停/"
                        f"{cluster.get('industries') or '?'}行业"
                    ),
                    "qty": 100,
                    "high": row.get("high"),
                    "low": row.get("low"),
                }
            )
            if len(items) >= max(1, int(max_items)):
                break
        if len(items) >= max(1, int(max_items)):
            break
    if not items:
        return None
    return {
        "title": "图谱·影子荐",
        "text": f"图谱影子荐 · {len(items)}只",
        "buy": False,
        "shadow": True,
        "size_note": "隐形概念簇龙头·只观察不自动亮灯；过门槛后可再升软提示",
        "items": items,
    }
