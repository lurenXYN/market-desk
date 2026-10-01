"""Board constituent snapshots and learned Sina → East Money aliases."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def save_board_members_snap(
    source: str,
    key: str,
    codes: list[str],
    *,
    name: str = "",
    kind: str = "",
) -> None:
    """Upsert the full constituent list of one board.

    Args:
        source: ``em`` (key = ``BK…``) or ``sina`` (key = Sina board name).
        key: Board key within the source.
        codes: Six-digit constituent codes.
        name: Display name.
        kind: ``industry`` / ``concept``.
    """
    uniq = sorted({str(c) for c in codes or [] if c})
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO board_members_snap(source, key, name, kind, codes, n, at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, key) DO UPDATE SET
                name = excluded.name, kind = excluded.kind, codes = excluded.codes,
                n = excluded.n, at = excluded.at
            """,
            (
                str(source), str(key), str(name or ""), str(kind or ""),
                json.dumps(uniq), len(uniq), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()


def load_board_members_snap(source: str, *, with_codes: bool = True) -> dict[str, dict[str, Any]]:
    """Return ``{key: {"name", "kind", "n", "at", "codes": set}}`` for one source."""
    cols = "key, name, kind, n, at" + (", codes" if with_codes else "")
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM board_members_snap WHERE source = ?", (str(source),)
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = {"name": row[1] or "", "kind": row[2] or "", "n": int(row[3] or 0), "at": row[4] or ""}
        if with_codes:
            try:
                item["codes"] = set(json.loads(row[5] or "[]"))
            except ValueError:
                item["codes"] = set()
        out[str(row[0])] = item
    return out


def replace_board_alias_learned(rows: list[dict[str, Any]]) -> int:
    """Replace every learned alias row with a fresh learning pass."""
    at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = [
        (
            str(r.get("sina_name") or ""), str(r.get("sina_kind") or ""), str(r.get("bk") or ""),
            str(r.get("em_name") or ""), str(r.get("em_kind") or ""), r.get("jaccard"),
            str(r.get("status") or ""), at,
        )
        for r in rows or []
        if r.get("sina_name") and r.get("bk") and r.get("status")
    ]
    with _connect() as conn:
        conn.execute("DELETE FROM board_alias_learned")
        conn.executemany(
            """
            INSERT OR REPLACE INTO board_alias_learned(
                sina_name, sina_kind, bk, em_name, em_kind, jaccard, status, at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )
        conn.commit()
    return len(data)


def load_board_alias_learned() -> list[dict[str, Any]]:
    """Return every learned alias row (auto / approx / candidate)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT sina_name, sina_kind, bk, em_name, em_kind, jaccard, status, at
            FROM board_alias_learned ORDER BY status, jaccard DESC
            """
        ).fetchall()
    keys = ("sina_name", "sina_kind", "bk", "em_name", "em_kind", "jaccard", "status", "at")
    return [dict(zip(keys, row)) for row in rows]
