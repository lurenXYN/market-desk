"""Sina board breadth fill from snaps × quotes."""

from __future__ import annotations

from market_desk.board_breadth import (
    apply_board_breadth_from_quotes,
    breadth_from_codes,
    breadth_from_members,
    fill_card_breadth_from_members,
)


def test_breadth_from_codes_and_members() -> None:
    ups, downs, known = breadth_from_codes(
        ["600001", "600002", "600003"],
        {
            "600001": {"pct": 1.2},
            "600002": {"pct": -0.5},
            "600003": {"pct": 0.0},
        },
    )
    assert (ups, downs, known) == (1, 1, 3)
    u2, d2, k2 = breadth_from_members(
        [{"code": "1", "pct": 2}, {"code": "2", "pct": -1}, {"code": "3"}]
    )
    assert (u2, d2, k2) == (1, 1, 2)


def test_apply_board_breadth_from_quotes() -> None:
    boards = [
        {"bk": "BK1", "name": "算力", "up_count": 0, "down_count": 0},
        {"bk": "BK2", "name": "已有", "up_count": 5, "down_count": 2},
    ]
    snaps = {
        "BK1": {"name": "算力", "codes": ["600001", "600002", "600003", "600004"]},
    }
    quotes = [
        {"code": "600001", "pct": 1.0},
        {"code": "600002", "pct": -1.0},
        {"code": "600003", "pct": 0.5},
        {"code": "600004", "pct": -0.2},
    ]
    n = apply_board_breadth_from_quotes(boards, quotes, snaps=snaps, min_known=3)
    assert n == 1
    assert boards[0]["up_count"] == 2 and boards[0]["down_count"] == 2
    assert boards[0]["breadth_from"] == "snap_quotes"
    assert boards[1]["up_count"] == 5


def test_fill_card_breadth_from_members() -> None:
    card = {
        "up_count": 0,
        "down_count": 0,
        "pool": [
            {"code": "600001", "pct": 1},
            {"code": "600002", "pct": -1},
            {"code": "600003", "pct": 2},
        ],
    }
    assert fill_card_breadth_from_members(card) is True
    assert card["up_count"] == 2 and card["down_count"] == 1
