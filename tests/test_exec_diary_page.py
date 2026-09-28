"""Exec diary paging: offset / limit windows and per-user totals."""

from __future__ import annotations

import pytest

import market_desk.db as desk_db


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _seed(uid: int, n: int, day: str = "2026-09-28") -> None:
    for i in range(n):
        desk_db.add_exec_diary(
            user_id=uid, trade_date=day, side="buy", code=f"{600000 + i:06d}",
            name=f"S{i}", qty=100, price=10.0, advice={},
        )


def test_pages_are_disjoint_newest_first_and_total_counts_user_only() -> None:
    _seed(1, 45)
    _seed(2, 7)
    p1 = desk_db.load_exec_diary(user_id=1, limit=20, offset=0)
    p2 = desk_db.load_exec_diary(user_id=1, limit=20, offset=20)
    p3 = desk_db.load_exec_diary(user_id=1, limit=20, offset=40)
    assert [len(p1), len(p2), len(p3)] == [20, 20, 5]
    ids = [r["id"] for r in p1 + p2 + p3]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 45
    assert desk_db.count_exec_diary(user_id=1) == 45
    assert desk_db.count_exec_diary(user_id=2) == 7
    assert desk_db.load_exec_diary(user_id=1, limit=20, offset=60) == []


def test_count_by_trade_date() -> None:
    _seed(1, 3, day="2026-09-25")
    _seed(1, 2, day="2026-09-28")
    assert desk_db.count_exec_diary(user_id=1, trade_date="2026-09-28") == 2
    assert len(desk_db.load_exec_diary(user_id=1, trade_date="2026-09-25", limit=2, offset=2)) == 1
