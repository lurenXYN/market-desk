"""Theme / stock reputation scores and per-day theme outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market_desk.db.core import _connect


def upsert_theme_reputation(row: dict[str, Any], *, preserve_manual: bool = True) -> None:
    """Upsert one theme reputation aggregate.

    When ``preserve_manual`` is True, keep existing manual_adj / manual_note /
    trade_adj and recompute ``score_adj = auto_adj + manual_adj + trade_adj``.
    """
    theme = str(row.get("theme_key") or "").strip()
    if not theme:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auto = float(row.get("auto_adj") if row.get("auto_adj") is not None else row.get("score_adj") or 0)
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT manual_adj, manual_note, trade_adj
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
        if preserve_manual and cur is not None:
            manual = float(cur["manual_adj"] or 0)
            note = cur["manual_note"]
            trade = float(cur["trade_adj"] or 0) if "trade_adj" in cur.keys() else 0.0
        else:
            manual = float(row.get("manual_adj") or 0)
            note = row.get("manual_note")
            trade = float(row.get("trade_adj") or 0)
        combined = round(auto + manual + trade, 2)
        from market_desk.theme_memory import label_for_rep

        label = label_for_rep(
            float(row.get("fade_n") or 0),
            float(row.get("persist_n") or 0),
            combined,
        )
        conn.execute(
            """
            INSERT INTO theme_reputation(
                theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                fade_n = excluded.fade_n,
                persist_n = excluded.persist_n,
                score_adj = excluded.score_adj,
                auto_adj = excluded.auto_adj,
                manual_adj = excluded.manual_adj,
                trade_adj = excluded.trade_adj,
                manual_note = excluded.manual_note,
                last_fade_date = excluded.last_fade_date,
                last_persist_date = excluded.last_persist_date,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                theme,
                int(row.get("fade_n") or 0),
                int(row.get("persist_n") or 0),
                combined,
                auto,
                manual,
                trade,
                note,
                row.get("last_fade_date"),
                row.get("last_persist_date"),
                label,
                now,
            ),
        )
        conn.commit()


def set_theme_manual_adj(
    theme_key: str,
    *,
    manual_adj: float | None = None,
    delta: float | None = None,
    note: str | None = None,
    clear: bool = False,
) -> dict[str, Any] | None:
    """Set or nudge manual theme reputation; returns the updated row."""
    from market_desk.config import THEME_MANUAL_ADJ_MAX, THEME_MANUAL_ADJ_MIN, THEME_REP_ADJ_MAX, THEME_REP_ADJ_MIN
    from market_desk.mainline import theme_key as canon_theme

    theme = canon_theme(str(theme_key or "").strip()) or str(theme_key or "").strip()
    if not theme:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
        trade = 0.0
        if cur is None:
            auto = 0.0
            fade_n = 0
            persist_n = 0
            last_fade = None
            last_persist = None
            old_manual = 0.0
            old_note = None
        else:
            row = dict(cur)
            trade = float(row.get("trade_adj") or 0)
            auto = float(row.get("auto_adj") or 0)
            if abs(auto) < 1e-9 and abs(float(row.get("score_adj") or 0)) > 1e-9:
                auto = (
                    float(row.get("score_adj") or 0)
                    - float(row.get("manual_adj") or 0)
                    - trade
                )
            fade_n = int(row.get("fade_n") or 0)
            persist_n = int(row.get("persist_n") or 0)
            last_fade = row.get("last_fade_date")
            last_persist = row.get("last_persist_date")
            old_manual = float(row.get("manual_adj") or 0)
            old_note = row.get("manual_note")
        if clear:
            manual = 0.0
            note_out = None
        elif manual_adj is not None:
            manual = float(manual_adj)
            note_out = note if note is not None else old_note
        elif delta is not None:
            manual = old_manual + float(delta)
            note_out = note if note is not None else old_note
        else:
            manual = old_manual
            note_out = note if note is not None else old_note
        manual = max(float(THEME_MANUAL_ADJ_MIN), min(float(THEME_MANUAL_ADJ_MAX), round(manual, 2)))
        combined = max(
            float(THEME_REP_ADJ_MIN),
            min(float(THEME_REP_ADJ_MAX), round(auto + manual + trade, 2)),
        )
        from market_desk.theme_memory import label_for_rep

        label = label_for_rep(fade_n, persist_n, combined)
        conn.execute(
            """
            INSERT INTO theme_reputation(
                theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                score_adj = excluded.score_adj,
                auto_adj = excluded.auto_adj,
                manual_adj = excluded.manual_adj,
                trade_adj = excluded.trade_adj,
                manual_note = excluded.manual_note,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                theme,
                fade_n,
                persist_n,
                combined,
                round(auto, 2),
                manual,
                round(trade, 2),
                note_out,
                last_fade,
                last_persist,
                label,
                now,
            ),
        )
        conn.commit()
        out = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
    return dict(out) if out else None


def bump_theme_trade_adj(theme_key: str, delta: float) -> dict[str, Any] | None:
    """Apply a decaying trade-PnL nudge onto theme ``trade_adj``."""
    from market_desk.config import (
        THEME_REP_ADJ_MAX,
        THEME_REP_ADJ_MIN,
        THEME_TRADE_ADJ_MAX,
        THEME_TRADE_ADJ_MIN,
    )
    from market_desk.mainline import theme_key as canon_theme
    from market_desk.theme_memory import label_for_rep

    theme = canon_theme(str(theme_key or "").strip()) or str(theme_key or "").strip()
    if not theme or abs(float(delta)) < 1e-9:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT fade_n, persist_n, auto_adj, manual_adj, trade_adj, score_adj
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
        if cur is None:
            auto = 0.0
            manual = 0.0
            trade = 0.0
            fade_n = 0
            persist_n = 0
        else:
            row = dict(cur)
            auto = float(row.get("auto_adj") or 0)
            manual = float(row.get("manual_adj") or 0)
            trade = float(row.get("trade_adj") or 0) * 0.92  # mild forget before bump
            fade_n = int(row.get("fade_n") or 0)
            persist_n = int(row.get("persist_n") or 0)
        trade = max(
            float(THEME_TRADE_ADJ_MIN),
            min(float(THEME_TRADE_ADJ_MAX), round(trade + float(delta), 2)),
        )
        combined = max(
            float(THEME_REP_ADJ_MIN),
            min(float(THEME_REP_ADJ_MAX), round(auto + manual + trade, 2)),
        )
        label = label_for_rep(fade_n, persist_n, combined)
        conn.execute(
            """
            INSERT INTO theme_reputation(
                theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                trade_adj, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(theme_key) DO UPDATE SET
                score_adj = excluded.score_adj,
                trade_adj = excluded.trade_adj,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (theme, fade_n, persist_n, combined, auto, manual, trade, label, now),
        )
        conn.commit()
        out = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation WHERE theme_key = ?
            """,
            (theme,),
        ).fetchone()
    return dict(out) if out else None


def upsert_stock_reputation(
    *,
    code: str,
    name: str = "",
    win: bool = False,
    loss: bool = False,
    fake: bool = False,
    pnl_pct: float | None = None,
    trade_date: str | None = None,
) -> dict[str, Any] | None:
    """Update one ticker's trade reputation from a closed-lot outcome."""
    from market_desk.config import (
        ADAPT_STOCK_REP_ADJ_MAX,
        ADAPT_STOCK_REP_ADJ_MIN,
        ADAPT_STOCK_WATCH_STREAK,
    )
    from market_desk.filters import normalize_code

    c = normalize_code(code)
    if len(c) != 6:
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    day = str(trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                   last_pnl_pct, last_trade_date, label
            FROM stock_reputation WHERE code = ?
            """,
            (c,),
        ).fetchone()
        if cur is None:
            win_n = loss_n = fake_n = streak = 0
            adj = 0.0
            nm = name or c
        else:
            row = dict(cur)
            win_n = int(row.get("win_n") or 0)
            loss_n = int(row.get("loss_n") or 0)
            fake_n = int(row.get("fake_n") or 0)
            streak = int(row.get("streak_loss") or 0)
            adj = float(row.get("score_adj") or 0) * 0.94
            nm = name or str(row.get("name") or c)
        if win:
            win_n += 1
            streak = 0
            adj += 1.2
        if loss:
            loss_n += 1
            streak += 1
            adj -= 2.0
        if fake:
            fake_n += 1
            adj -= 1.5
        adj = max(float(ADAPT_STOCK_REP_ADJ_MIN), min(float(ADAPT_STOCK_REP_ADJ_MAX), round(adj, 2)))
        if streak >= int(ADAPT_STOCK_WATCH_STREAK):
            label = "观察名单"
        elif adj <= -6:
            label = "易骗线"
        elif adj <= -2:
            label = "偏坑"
        elif adj >= 3:
            label = "偏顺"
        else:
            label = "中性"
        conn.execute(
            """
            INSERT INTO stock_reputation(
                code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                last_pnl_pct, last_trade_date, label, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                win_n = excluded.win_n,
                loss_n = excluded.loss_n,
                fake_n = excluded.fake_n,
                score_adj = excluded.score_adj,
                streak_loss = excluded.streak_loss,
                last_pnl_pct = excluded.last_pnl_pct,
                last_trade_date = excluded.last_trade_date,
                label = excluded.label,
                updated_at = excluded.updated_at
            """,
            (
                c,
                nm,
                win_n,
                loss_n,
                fake_n,
                adj,
                streak,
                round(float(pnl_pct), 2) if pnl_pct is not None else None,
                day,
                label,
                now,
            ),
        )
        conn.commit()
        out = conn.execute(
            """
            SELECT code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                   last_pnl_pct, last_trade_date, label, updated_at
            FROM stock_reputation WHERE code = ?
            """,
            (c,),
        ).fetchone()
    return dict(out) if out else None


def load_stock_reputation_one(code: str) -> dict[str, Any] | None:
    """Load one stock reputation row by code."""
    from market_desk.filters import normalize_code

    c = normalize_code(code)
    if len(c) != 6:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT code, name, win_n, loss_n, fake_n, score_adj, streak_loss,
                   last_pnl_pct, last_trade_date, label, updated_at
            FROM stock_reputation WHERE code = ?
            """,
            (c,),
        ).fetchone()
    return dict(row) if row else None


def load_theme_reputation() -> list[dict[str, Any]]:
    """Return all theme reputation rows."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT theme_key, fade_n, persist_n, score_adj, auto_adj, manual_adj,
                   trade_adj, manual_note, last_fade_date, last_persist_date, label, updated_at
            FROM theme_reputation
            ORDER BY score_adj ASC, fade_n DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        trade = float(item.get("trade_adj") or 0)
        if item.get("auto_adj") is None:
            item["auto_adj"] = (
                float(item.get("score_adj") or 0)
                - float(item.get("manual_adj") or 0)
                - trade
            )
        if item.get("manual_adj") is None:
            item["manual_adj"] = 0.0
        if item.get("trade_adj") is None:
            item["trade_adj"] = 0.0
        out.append(item)
    return out


def record_theme_day_outcome(row: dict[str, Any]) -> None:
    """Insert or replace one prior→next theme outcome."""
    theme = str(row.get("theme_key") or "").strip()
    prior = str(row.get("trade_date") or "")[:10]
    nxt = str(row.get("next_date") or "")[:10]
    outcome = str(row.get("outcome") or "").strip()
    if not theme or not prior or not nxt or outcome not in ("fade", "persist"):
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO theme_day_outcome(
                trade_date, next_date, theme_key, outcome, mainline,
                zt_n, next_zt_n, pct, next_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, next_date, theme_key) DO UPDATE SET
                outcome = excluded.outcome,
                mainline = excluded.mainline,
                zt_n = excluded.zt_n,
                next_zt_n = excluded.next_zt_n,
                pct = excluded.pct,
                next_pct = excluded.next_pct,
                updated_at = excluded.updated_at
            """,
            (
                prior,
                nxt,
                theme,
                outcome,
                row.get("mainline"),
                row.get("zt_n"),
                row.get("next_zt_n"),
                row.get("pct"),
                row.get("next_pct"),
                now,
            ),
        )
        conn.commit()


def load_theme_day_outcomes(trade_date: str) -> list[dict[str, Any]]:
    """Return theme outcomes recorded for a prior trade date."""
    day = str(trade_date or "")[:10]
    if not day:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, next_date, theme_key, outcome, mainline,
                   zt_n, next_zt_n, pct, next_pct, updated_at
            FROM theme_day_outcome
            WHERE trade_date = ?
            """,
            (day,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_theme_outcomes_for_theme(theme_key: str, limit: int = 12) -> list[dict[str, Any]]:
    """Return newest-first outcomes for one theme."""
    theme = str(theme_key or "").strip()
    if not theme:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT trade_date, next_date, theme_key, outcome, mainline,
                   zt_n, next_zt_n, pct, next_pct, updated_at
            FROM theme_day_outcome
            WHERE theme_key = ?
            ORDER BY next_date DESC
            LIMIT ?
            """,
            (theme, max(1, int(limit))),
        ).fetchall()
    return [dict(r) for r in rows]


def list_theme_outcome_keys() -> list[str]:
    """Return distinct theme keys that have at least one day outcome."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT theme_key
            FROM theme_day_outcome
            WHERE theme_key IS NOT NULL AND theme_key <> ''
            ORDER BY theme_key
            """
        ).fetchall()
    return [str(r["theme_key"]) for r in rows if r["theme_key"]]
