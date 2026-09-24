"""Stored backtest runs, fills and run comparison."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


BACKTEST_RUN_KEEP_DEFAULT = 40
_FILL_CORE_KEYS = (
    "id",
    "side",
    "code",
    "name",
    "kind",
    "trade_date",
    "signal_type",
    "desk_source",
    "plan_price",
    "wait_price",
    "chase_price",
    "stop_price",
    "sim_filled",
    "sim_fill_price",
    "sim_fill_date",
    "sim_mode",
    "sim_exit_mode",
    "sim_exec",
    "note",
    "liq_skips",
    "gap_filled",
    "outcome_label",
    "outcome_day1_pct",
    "outcome_day3_pct",
)


def save_backtest_run(
    *,
    result: dict[str, Any],
    created_by: int = 0,
    label: str = "",
    keep: int | None = None,
) -> dict[str, Any]:
    """Persist one backtest result into dedicated run/fill tables.

    Does not touch ``signals`` or ``signal_user_meta``. Returns the run row dict.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError("backtest result missing or not ok")
    if result.get("dry_run"):
        raise ValueError("dry_run results are not persisted")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    realism = result.get("realism") or {}
    summary = result.get("summary") or {}
    sim_exec = summary.get("sim_exec") or {}
    params = {
        "date_from": result.get("date_from"),
        "date_to": result.get("date_to"),
        "mode": result.get("mode"),
        "ready_only": bool(result.get("ready_only")),
        "include_sells": bool(result.get("include_sells")),
        "limit": result.get("n"),
        "realism": realism,
        "max_span_days": result.get("max_span_days"),
    }
    label_s = str(label or "").strip()
    if not label_s:
        label_s = (
            f"{result.get('date_from')}~{result.get('date_to')}"
            f" · {result.get('mode') or 'plan'}"
        )
    items = list(result.get("items") or [])
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO signal_backtest_run(
                created_at, created_by, label,
                date_from, date_to, mode, ready_only, include_sells, limit_n,
                vol_min_ratio, slip_pct, gap_pct,
                params_json, summary_json, note, item_n,
                buy_hit_rate, sell_hit_rate, buy_fill_rate, sim_exec_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                int(created_by or 0),
                label_s[:120],
                str(result.get("date_from") or "")[:10],
                str(result.get("date_to") or "")[:10],
                str(result.get("mode") or "plan")[:16],
                1 if result.get("ready_only") else 0,
                1 if result.get("include_sells", True) else 0,
                int(result.get("n") or len(items) or 0),
                realism.get("vol_min_ratio"),
                realism.get("slip_pct"),
                realism.get("gap_pct"),
                json.dumps(params, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                str(result.get("note") or result.get("disclaimer") or "")[:2000],
                len(items),
                summary.get("buy_hit_rate"),
                summary.get("sell_hit_rate"),
                summary.get("buy_fill_rate"),
                (sim_exec.get("score") if isinstance(sim_exec, dict) else None),
            ),
        )
        run_id = int(cur.lastrowid)
        for item in items:
            if not isinstance(item, dict):
                continue
            extra = {
                k: v
                for k, v in item.items()
                if k not in _FILL_CORE_KEYS and k != "payload_json"
            }
            conn.execute(
                """
                INSERT INTO signal_backtest_fill(
                    run_id, signal_id, side, code, name, kind, trade_date,
                    signal_type, desk_source,
                    plan_price, wait_price, chase_price, stop_price,
                    sim_filled, sim_fill_price, sim_fill_date, sim_mode,
                    sim_exit_mode, sim_exec, note, liq_skips, gap_filled,
                    outcome_label, outcome_day1_pct, outcome_day3_pct, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    item.get("id"),
                    str(item.get("side") or "buy")[:8],
                    str(item.get("code") or "")[:12],
                    str(item.get("name") or "")[:64],
                    str(item.get("kind") or "")[:16],
                    str(item.get("trade_date") or "")[:10],
                    str(item.get("signal_type") or "")[:32],
                    str(item.get("desk_source") or "")[:32],
                    item.get("plan_price"),
                    item.get("wait_price"),
                    item.get("chase_price"),
                    item.get("stop_price"),
                    1 if item.get("sim_filled") else 0,
                    item.get("sim_fill_price"),
                    str(item.get("sim_fill_date") or "")[:10] or None,
                    str(item.get("sim_mode") or "")[:16] or None,
                    str(item.get("sim_exit_mode") or "")[:16] or None,
                    str(item.get("sim_exec") or "")[:32] or None,
                    str(item.get("note") or "")[:240] or None,
                    int(item.get("liq_skips") or 0),
                    1 if item.get("gap_filled") else 0,
                    str(item.get("outcome_label") or "")[:64] or None,
                    item.get("outcome_day1_pct"),
                    item.get("outcome_day3_pct"),
                    json.dumps(extra, ensure_ascii=False, default=str),
                ),
            )
        lim = max(5, min(100, int(keep if keep is not None else BACKTEST_RUN_KEEP_DEFAULT)))
        _prune_backtest_runs(conn, keep=lim)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM signal_backtest_run WHERE id = ?", (run_id,)
        ).fetchone()
    return _backtest_run_row(dict(row) if row else {"id": run_id})


def _prune_backtest_runs(conn: sqlite3.Connection, *, keep: int = 40) -> None:
    """Delete oldest runs (and their fills) beyond ``keep``."""
    ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM signal_backtest_run ORDER BY id DESC"
        ).fetchall()
    ]
    drop = ids[keep:]
    if not drop:
        return
    placeholders = ",".join("?" for _ in drop)
    conn.execute(
        f"DELETE FROM signal_backtest_fill WHERE run_id IN ({placeholders})", drop
    )
    conn.execute(
        f"DELETE FROM signal_backtest_run WHERE id IN ({placeholders})", drop
    )


def _backtest_run_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a run row for API responses."""
    out = dict(row)
    for key, raw in (
        ("params", out.pop("params_json", None)),
        ("summary", out.pop("summary_json", None)),
    ):
        if isinstance(raw, str) and raw.strip():
            try:
                out[key] = json.loads(raw)
            except json.JSONDecodeError:
                out[key] = {}
        elif raw is None and key not in out:
            out[key] = {}
    out["ready_only"] = bool(int(out.get("ready_only") or 0))
    out["include_sells"] = bool(int(out.get("include_sells") or 0))
    return out


def _backtest_fill_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a fill row; merge payload_json extras onto the item."""
    raw_row = dict(row)
    fill_pk = raw_row.pop("id", None)
    payload_raw = raw_row.pop("payload_json", None)
    extra: dict[str, Any] = {}
    if isinstance(payload_raw, str) and payload_raw.strip():
        try:
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError:
            extra = {}
    out: dict[str, Any] = dict(extra)
    out.update(raw_row)
    out["sim_filled"] = bool(int(out.get("sim_filled") or 0))
    out["gap_filled"] = bool(int(out.get("gap_filled") or 0))
    out["fill_row_id"] = fill_pk
    # Table paint expects signal id under ``id``.
    if out.get("signal_id") is not None:
        out["id"] = out["signal_id"]
    return out


def list_backtest_runs(*, limit: int = 30) -> list[dict[str, Any]]:
    """List recent backtest runs (summary headers only)."""
    lim = max(1, min(100, int(limit or 30)))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM signal_backtest_run
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [_backtest_run_row(dict(r)) for r in rows]


def get_backtest_run(run_id: int, *, with_fills: bool = True) -> dict[str, Any] | None:
    """Load one run; optionally attach fill items for UI paint."""
    rid = int(run_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM signal_backtest_run WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            return None
        out = _backtest_run_row(dict(row))
        if with_fills:
            fills = conn.execute(
                """
                SELECT * FROM signal_backtest_fill
                WHERE run_id = ?
                ORDER BY trade_date DESC, id ASC
                """,
                (rid,),
            ).fetchall()
            out["items"] = [_backtest_fill_row(dict(f)) for f in fills]
    return out


def delete_backtest_run(run_id: int) -> bool:
    """Delete one run and its fills. Returns True if a run was removed."""
    rid = int(run_id)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM signal_backtest_fill WHERE run_id = ?", (rid,)
        )
        cur2 = conn.execute(
            "DELETE FROM signal_backtest_run WHERE id = ?", (rid,)
        )
        conn.commit()
        return cur2.rowcount > 0 or cur.rowcount > 0


def clear_backtest_runs(*, keep: int = 0) -> int:
    """Delete runs older than the newest ``keep`` (0 = wipe all). Return deleted count."""
    keep_n = max(0, min(100, int(keep or 0)))
    with _connect() as conn:
        ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM signal_backtest_run ORDER BY id DESC"
            ).fetchall()
        ]
        drop = ids[keep_n:]
        if not drop:
            return 0
        _prune_backtest_runs(conn, keep=keep_n)
        conn.commit()
        return len(drop)


def compare_backtest_runs(run_ids: list[int]) -> dict[str, Any]:
    """Return side-by-side summary metrics for up to a few saved runs."""
    ids: list[int] = []
    for x in run_ids:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))[:6]
    if not ids:
        return {"ok": True, "runs": [], "metrics": []}
    with _connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT * FROM signal_backtest_run WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    runs_map = {int(r["id"]): _backtest_run_row(dict(r)) for r in rows}
    ordered = [runs_map[i] for i in ids if i in runs_map]
    metrics = [
        {"key": "label", "title": "标签"},
        {"key": "date_from", "title": "起"},
        {"key": "date_to", "title": "止"},
        {"key": "mode", "title": "买撮合"},
        {"key": "vol_min_ratio", "title": "量能"},
        {"key": "slip_pct", "title": "滑点%"},
        {"key": "gap_pct", "title": "跳空%"},
        {"key": "buy_fill_rate", "title": "买触达%"},
        {"key": "buy_hit_rate", "title": "买命中%"},
        {"key": "sell_hit_rate", "title": "卖命中%"},
        {"key": "sim_exec_score", "title": "模拟执行分"},
        {"key": "item_n", "title": "条数"},
        {"key": "created_at", "title": "存档时间"},
    ]
    return {"ok": True, "runs": ordered, "metrics": metrics}
