"""Unit tests for dual-dragon / independent-pop helpers."""

from __future__ import annotations

import unittest

from market_desk.leaders import (
    _independent_vs_board,
    _near_day_low,
    board_surge_fresh,
    build_independent_pullback_candidates,
    pick_emotion_dragon,
    stock_liquidity_ok,
    within_n_day_low,
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
        # Mild sync vs board is no longer independent (fallback path covers it).
        self.assertFalse(_independent_vs_board({"pct": 0.6}, 0.5))
        self.assertTrue(_independent_vs_board({"pct": 1.5}, 0.5))

    def test_independent_prefers_diverge_then_near_low_fallback(self) -> None:
        board = {
            "pct": 1.0,
            "leader_code": "600099",
            "pool": [
                {
                    "code": "600001",
                    "name": "独立甲",
                    "pct": 2.2,
                    "price": 10.05,
                    "low": 10.0,
                    "high": 10.4,
                    "mv_yi": 200,
                    "turnover": 4.0,
                    "amount": 3e8,
                },
                {
                    "code": "600002",
                    "name": "同步乙",
                    "pct": 1.1,
                    "price": 20.1,
                    "low": 20.0,
                    "high": 20.5,
                    "mv_yi": 300,
                    "turnover": 3.5,
                    "amount": 5e8,
                },
                {
                    "code": "600003",
                    "name": "冲高丙",
                    "pct": 1.0,
                    "price": 15.0,
                    "low": 14.0,
                    "high": 15.2,
                    "mv_yi": 250,
                    "turnover": 4.0,
                    "amount": 8e8,
                },
            ],
        }
        rows = build_independent_pullback_candidates(board, max_items=5)
        codes = [r["code"] for r in rows]
        self.assertIn("600001", codes)
        self.assertIn("600002", codes)
        self.assertNotIn("600003", codes)
        by_code = {r["code"]: r for r in rows}
        self.assertTrue(by_code["600001"].get("indep_path"))
        self.assertFalse(by_code["600002"].get("indep_path"))
        self.assertIn("板内同步", by_code["600002"]["reason"])

    def test_within_n_day_low(self) -> None:
        lows = [9.5, 9.8, 10.0, 9.7, 9.6]
        self.assertTrue(within_n_day_low(lows, 9.7, n=5, max_pct=3.5))
        self.assertFalse(within_n_day_low(lows, 10.5, n=5, max_pct=3.5))

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
        why = str(hit.get("dragon_why") or "")
        self.assertIn("为何是情绪龙", why)
        self.assertIn("2板", why)

    def test_pick_mid_army_has_why(self) -> None:
        from market_desk.leaders import pick_mid_army_dragon

        board = {
            "pool": [
                {
                    "code": "600001",
                    "name": "甲",
                    "amount": 5e8,
                    "mv_yi": 200,
                    "pct": 2.0,
                    "turnover": 4,
                },
                {
                    "code": "600002",
                    "name": "乙",
                    "amount": 20e8,
                    "mv_yi": 500,
                    "pct": 1.0,
                    "turnover": 3,
                },
            ]
        }
        hit = pick_mid_army_dragon(board, skip_codes={"600099"})
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["code"], "600002")
        self.assertIn("为何是中军龙", str(hit.get("dragon_why") or ""))


if __name__ == "__main__":
    unittest.main()
