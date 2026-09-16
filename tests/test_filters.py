"""Unit tests for board / limit-up helpers."""

from __future__ import annotations

import unittest

from market_desk.filters import (
    is_bj_exchange,
    is_chinext_or_star,
    is_limit_down,
    is_limit_up,
    is_main_board,
    is_st,
    limit_up_threshold,
    normalize_code,
)


class FilterTests(unittest.TestCase):
    def test_normalize_code(self) -> None:
        self.assertEqual(normalize_code(1), "000001")
        self.assertEqual(normalize_code("600519"), "600519")

    def test_board_flags(self) -> None:
        self.assertTrue(is_main_board("600519"))
        self.assertFalse(is_main_board("300750"))
        self.assertTrue(is_chinext_or_star("300750"))
        self.assertTrue(is_chinext_or_star("688981"))
        self.assertTrue(is_bj_exchange("830799"))
        self.assertTrue(is_st("*ST某某"))
        self.assertFalse(is_st("贵州茅台"))

    def test_limit_up_thresholds(self) -> None:
        self.assertAlmostEqual(limit_up_threshold(None, "600519"), 9.85)
        self.assertAlmostEqual(limit_up_threshold("ST假", "000001"), 4.85)
        self.assertAlmostEqual(limit_up_threshold(None, "300750"), 19.5)
        self.assertAlmostEqual(limit_up_threshold(None, "688001"), 19.5)
        self.assertAlmostEqual(limit_up_threshold(None, "830799"), 29.5)

    def test_is_limit_up_by_board(self) -> None:
        self.assertTrue(is_limit_up(None, 10.0, "600519"))
        self.assertFalse(is_limit_up(None, 10.0, "300750"))
        self.assertTrue(is_limit_up(None, 20.0, "300750"))
        self.assertTrue(is_limit_up("ST假", 5.0, "000001"))
        self.assertTrue(is_limit_down(None, -10.0, "600519"))
        self.assertFalse(is_limit_down(None, -10.0, "300750"))


if __name__ == "__main__":
    unittest.main()
