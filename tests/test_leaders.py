"""Unit tests for dual-dragon / independent-pop helpers."""

from __future__ import annotations

import unittest

from market_desk.leaders import (
    _independent_vs_board,
    _near_day_low,
    board_surge_fresh,
    pick_emotion_dragon,
    stock_liquidity_ok,
)


class LeadersTests(unittest.TestCase):
    def test_liquidity_floors(self) -> None:
        self.assertFalse(stock_liquidity_ok(80.0, 10.0))
        self.assertFalse(stock_liquidity_ok(200.0, 2.0))
        self.assertTrue(stock_liquidity_ok(200.0, 3.5))
        self.assertFalse(stock_liquidity_ok(800.0, 1.5))
        self.assertTrue(stock_liquidity_ok(800.0, 2.5))
        # Sealed emotion dragon skips turnover floor.
        self.assertTrue(stock_liquidity_ok(200.0, 0.5, sealed=True))
        self.assertFalse(stock_liquidity_ok(None, 5.0))

    def test_board_surge_fresh(self) -> None:
        self.assertTrue(board_surge_fresh({}, "starting"))
        self.assertTrue(
            board_surge_fresh(
                {"zt_n": 5, "hist": [{"zt_n": 1}]},
                "ongoing",
            )
        )
        self.assertFalse(
            board_surge_fresh(
                {"zt_n": 3, "hist": [{"zt_n": 3}]},
                "ongoing",
            )
        )

    def test_near_day_low_and_independent(self) -> None:
        self.assertTrue(_near_day_low({"price": 10.1, "low": 10.0}, max_pct=2.0))
        self.assertFalse(_near_day_low({"price": 10.5, "low": 10.0}, max_pct=2.0))
        self.assertTrue(_independent_vs_board({"pct": 2.5}, 0.5))
        self.assertFalse(_independent_vs_board({"pct": 0.6}, 0.5))

    def test_pick_emotion_dragon_highest_boards(self) -> None:
        board = {
            "pool": [
                {"code": "600001", "name": "甲", "amount": 1e8},
                {"code": "600002", "name": "乙", "amount": 3e8},
            ]
        }
        zt = [
            {"code": "600001", "boards": 1, "amount": 1e8},
            {"code": "600002", "boards": 2, "amount": 2e8},
        ]
        hit = pick_emotion_dragon(board, zt)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["code"], "600002")


if __name__ == "__main__":
    unittest.main()
