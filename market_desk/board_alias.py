"""Sina → East Money board alias table.

Sina's board universe (≈50 old-style industries + ≈170 concepts) only shares a
few exact names with East Money. Resolving a Sina board to an East Money
``BK`` / name keeps the mainline name, ``board_hist``, ETF mapping and
name-keyed histories continuous across a source flip. Resolution order:

1. exact East Money name;
2. curated seed alias (``seeds/sina_em_alias.json``);
3. normalized name (strip 行业 / 概念 / 板块 / Ⅱ / Ⅲ suffixes);
4. constituent-overlap alias learned at runtime (``board_alias_learned``).

Old-style Sina industries are broader than any East Money industry; their
learned aliases are flagged ``approx`` and must not feed turnover crowding.
Attribute / region boards on the seed blocklist never enter the hot pool.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from market_desk.config import (
    ALIAS_APPROX_JACCARD,
    ALIAS_AUTO_JACCARD,
    ALIAS_CANDIDATE_JACCARD,
    ALIAS_MIN_MEMBERS,
    CROWD_ABS_EXEMPT,
)

log = logging.getLogger("market_desk.board_alias")

ALIAS_SEED_PATH = Path(__file__).with_name("seeds") / "sina_em_alias.json"
_SUFFIX_RE = re.compile(r"(行业|概念|板块|Ⅱ|Ⅲ)$")

_SEED: dict[str, Any] | None = None
# sina_name -> {"bk", "em_name", "em_kind", "status", "jaccard"} (applied rows only).
_LEARNED: dict[str, dict[str, Any]] = {}
# (len(em map), id(em map)) -> normalized name -> [(name, bk, kind)]
_NORM_CACHE: tuple[tuple[int, int], dict[str, list[tuple[str, str, str]]]] | None = None


def normalize_board_name(name: str | None) -> str:
    """Strip one trailing 行业 / 概念 / 板块 / Ⅱ / Ⅲ suffix and inner spaces."""
    text = str(name or "").strip().replace(" ", "").replace("\u3000", "")
    return _SUFFIX_RE.sub("", text) or text


def _seed() -> dict[str, Any]:
    """Load the curated alias / blocklist seed once."""
    global _SEED
    if _SEED is None:
        _SEED = {"alias": {}, "block": set()}
        try:
            body = json.loads(ALIAS_SEED_PATH.read_text(encoding="utf-8"))
            _SEED["alias"] = {str(k): str(v) for k, v in (body.get("alias") or {}).items()}
            _SEED["block"] = {str(x) for x in (body.get("block") or [])}
        except Exception:
            log.exception("load sina alias seed failed")
    return _SEED


def is_blocked_sina(name: str | None) -> bool:
    """Return True for Sina attribute / region boards that must not enter the hot pool."""
    return str(name or "").strip() in _seed()["block"]


def set_learned_aliases(rows: list[dict[str, Any]] | None) -> int:
    """Replace the in-memory learned aliases; only ``auto`` / ``approx`` rows apply."""
    global _LEARNED
    learned: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        status = str(row.get("status") or "")
        name = str(row.get("sina_name") or "")
        if status not in ("auto", "approx") or not name or not row.get("bk"):
            continue
        learned[name] = {
            "bk": str(row["bk"]).upper(),
            "em_name": str(row.get("em_name") or ""),
            "em_kind": str(row.get("em_kind") or ""),
            "status": status,
            "jaccard": row.get("jaccard"),
        }
    _LEARNED = learned
    return len(learned)


def _norm_index(em_by_name: dict[str, tuple[str, str]]) -> dict[str, list[tuple[str, str, str]]]:
    global _NORM_CACHE
    sig = (len(em_by_name), id(em_by_name))
    if _NORM_CACHE and _NORM_CACHE[0] == sig:
        return _NORM_CACHE[1]
    index: dict[str, list[tuple[str, str, str]]] = {}
    for em_name, (bk, kind) in em_by_name.items():
        index.setdefault(normalize_board_name(em_name), []).append((em_name, bk, kind))
    _NORM_CACHE = (sig, index)
    return index


def resolve_sina_board(
    name: str,
    kind: str,
    em_by_name: dict[str, tuple[str, str]],
) -> dict[str, Any] | None:
    """Resolve one Sina board to an East Money board.

    Args:
        name: Sina board name.
        kind: Sina list kind (``industry`` / ``concept``).
        em_by_name: East Money ``name -> (bk, kind)`` map.

    Returns:
        ``{"bk", "name", "kind", "how", "approx"}`` with ``how`` in
        exact / seed / norm / learned, or None when unmapped.
    """
    hit = em_by_name.get(name)
    if hit:
        return {"bk": hit[0], "name": name, "kind": hit[1], "how": "exact", "approx": False}
    target = _seed()["alias"].get(name)
    if target and em_by_name.get(target):
        bk, em_kind = em_by_name[target]
        return {"bk": bk, "name": target, "kind": em_kind, "how": "seed", "approx": False}
    cands = _norm_index(em_by_name).get(normalize_board_name(name)) or []
    if cands:
        same = [c for c in cands if c[2] == kind]
        em_name, bk, em_kind = (same or cands)[0]
        return {"bk": bk, "name": em_name, "kind": em_kind, "how": "norm", "approx": False}
    row = _LEARNED.get(name)
    if row and row.get("em_name"):
        return {
            "bk": row["bk"],
            "name": row["em_name"],
            "kind": row.get("em_kind") or kind,
            "how": "learned",
            "approx": row.get("status") == "approx",
        }
    return None


def _jaccard(a: set[str], b: set[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def learn_aliases(
    sina_sets: dict[str, dict[str, Any]],
    em_sets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match Sina boards to East Money boards by constituent overlap.

    Args:
        sina_sets: ``{sina_name: {"kind", "codes": set}}`` for boards to learn.
        em_sets: ``{bk: {"name", "kind", "codes": set}}``.

    Returns:
        Rows ``{sina_name, sina_kind, bk, em_name, em_kind, jaccard, status}``:
        ``auto`` (one-to-one, applied), ``approx`` (old-style Sina industry →
        best non-L1 East Money industry, applied but kept out of crowding) or
        ``candidate`` (weak / contested match, recorded for review only).
    """
    min_n = int(ALIAS_MIN_MEMBERS)
    em_ok = {
        bk: e for bk, e in em_sets.items()
        if len(e.get("codes") or ()) >= min_n and str(e.get("name") or "") not in CROWD_ABS_EXEMPT
    }
    picks: list[dict[str, Any]] = []
    for sina_name, s in sina_sets.items():
        codes = set(s.get("codes") or ())
        if len(codes) < min_n:
            continue
        kind = str(s.get("kind") or "concept")
        pool = em_ok.items()
        if kind == "industry":
            pool = [(bk, e) for bk, e in em_ok.items() if e.get("kind") == "industry"]
        best_bk, best_j = "", 0.0
        for bk, e in pool:
            j = _jaccard(codes, set(e["codes"]))
            if j > best_j:
                best_bk, best_j = bk, j
        if not best_bk:
            continue
        if best_j >= float(ALIAS_AUTO_JACCARD):
            status = "auto"
        elif kind == "industry" and best_j >= float(ALIAS_APPROX_JACCARD):
            status = "approx"
        elif best_j >= float(ALIAS_CANDIDATE_JACCARD):
            status = "candidate"
        else:
            continue
        e = em_ok[best_bk]
        picks.append({
            "sina_name": sina_name,
            "sina_kind": kind,
            "bk": best_bk,
            "em_name": str(e.get("name") or ""),
            "em_kind": str(e.get("kind") or ""),
            "jaccard": round(best_j, 3),
            "status": status,
        })
    # One East Money board serves one Sina board; the weaker claimants become candidates.
    owner: dict[str, dict[str, Any]] = {}
    for row in sorted(picks, key=lambda r: -float(r["jaccard"])):
        if row["status"] == "candidate":
            continue
        if row["bk"] in owner:
            row["status"] = "candidate"
        else:
            owner[row["bk"]] = row
    return picks
