"""Unit tests for review dragon-hide and buy-signal helpers."""

from __future__ import annotations

import unittest

from market_desk.review import (
    _dragon_hide_from_review,
    is_buy_signal,
    is_sell_signal,
    score_signal_with_closes,
)


class ReviewTests(unittest.TestCase):
    def test_signal_type_helpers(self) -> None:
        self.assertTrue(is_buy_signal("buy"))
        self.assertTrue(is_buy_signal("buy_dragon"))
        self.assertTrue(is_buy_signal("buy_indep"))
        self.assertFalse(is_buy_signal("sell"))
        self.assertTrue(is_sell_signal("sell"))
        self.assertFalse(is_sell_signal("buy"))

    def test_dragon_hide_main_board_limit_up(self) -> None:
        row = {
            "signal_type": "buy_dragon",
            "code": "600519",
            "name": "茅台",
            "pct": 10.01,
        }
        self.assertTrue(_dragon_hide_from_review(row))

    def test_dragon_hide_chinext_not_at_20(self) -> None:
        row = {
            "signal_type": "buy_dragon",
            "code": "300750",
            "name": "宁德",
            "pct": 10.0,
        }
        self.assertFalse(_dragon_hide_from_review(row))
        row["pct"] = 20.1
        self.assertTrue(_dragon_hide_from_review(row))

    def test_dragon_hide_sealed_reason(self) -> None:
        row = {
            "signal_type": "buy_dragon",
            "code": "600000",
            "name": "浦发",
            "pct": 3.0,
            "reason": "情绪龙 2板·确认异动；已封板先观察",
        }
        self.assertTrue(_dragon_hide_from_review(row))

    def test_dragon_hide_ignores_main_buy(self) -> None:
        row = {
            "signal_type": "buy",
            "code": "600519",
            "name": "茅台",
            "pct": 10.01,
        }
        self.assertFalse(_dragon_hide_from_review(row))

    def test_score_buy_next_close_red(self) -> None:
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
        }
        # dates[0] is signal day close; next bar is day1.
        closes = [10.0, 10.2, 10.3]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        out = score_signal_with_closes(sig, closes, dates)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "次日红")
        self.assertAlmostEqual(float(out["outcome_day1_pct"]), 2.0, places=2)

    def test_score_buy_next_close_green(self) -> None:
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
        }
        closes = [10.0, 9.7, 9.6]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        out = score_signal_with_closes(sig, closes, dates)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "次日绿")


if __name__ == "__main__":
    unittest.main()
