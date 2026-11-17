"""VPS DB snapshot pull: name parsing, retention window and verification."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from scripts.pull_vps_db import prune_old, snapshot_day, verify_db


def test_snapshot_day_parses_gz_and_plain():
    assert snapshot_day("desk-2026-11-17.db.gz") == date(2026, 11, 17)
    assert snapshot_day("desk-2026-11-17.db") == date(2026, 11, 17)
    assert snapshot_day("weird.db") is None
    assert snapshot_day("desk-not-a-date.db") is None


def test_prune_keeps_window_and_deletes_older(tmp_path):
    keep = []
    old = []
    for day in ("2026-10-01", "2026-10-17", "2026-10-18", "2026-11-16"):
        p = tmp_path / f"desk-{day}.db"
        p.write_bytes(b"x")
        (old if day < "2026-10-18" else keep).append(p)
    removed = prune_old([*keep, *old], keep_days=30, today=date(2026, 11, 17))
    assert sorted(p.name for p in removed) == ["desk-2026-10-01.db", "desk-2026-10-17.db"]
    assert all(p.exists() for p in keep)


def test_prune_ignores_unparsable_names(tmp_path):
    junk = tmp_path / "README.txt"
    junk.write_text("keep me", encoding="utf-8")
    assert prune_old([junk], keep_days=1, today=date(2026, 11, 17)) == []
    assert junk.exists()


def test_verify_db_reports_counts(tmp_path):
    p = tmp_path / "desk-2026-11-17.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE signals(id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE market_bar_daily(code TEXT, trade_date TEXT)")
    conn.execute("INSERT INTO signals DEFAULT VALUES")
    conn.commit()
    conn.close()

    out = verify_db(p)
    assert out["ok"] is True
    assert out["tables"] == 2
    assert out["row_counts"]["signals"] == 1
    assert out["row_counts"]["decision_trace"] is None  # 表不存在时报 None，不抛错


def test_verify_db_flags_corruption(tmp_path):
    p = tmp_path / "desk-2026-11-17.db"
    p.write_bytes(b"not a sqlite database at all" * 10)
    try:
        out = verify_db(p)
    except sqlite3.DatabaseError:
        return  # sqlite 直接拒绝打开也算检出损坏
    assert out["ok"] is False


def test_vps_backup_prune_by_age(tmp_path, monkeypatch):
    """VPS 侧备份：超过 30 天必须删，未超期的保留。"""
    import os
    import time

    import market_desk.backup_store as bs

    monkeypatch.setattr(bs, "BACKUP_DIR", tmp_path)
    old = tmp_path / "desk-2026-09-01-150000.db"
    new = tmp_path / "desk-2026-11-16-150000.db"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))

    bs._prune_backups(limit=99, keep_days=30)
    assert not old.exists()
    assert new.exists()
