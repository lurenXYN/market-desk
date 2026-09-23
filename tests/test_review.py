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

    def test_same_day_plan_hit(self) -> None:
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
            "fill_price": 10.5,  # ignored under same_day_plan
        }
        # Touched plan on signal day; next close +2% vs plan → 次日红
        closes = [10.2, 10.2, 10.3]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        lows = [9.9, 10.0, 10.1]
        highs = [10.3, 10.4, 10.5]
        opens = [10.0, 10.1, 10.2]
        out = score_signal_with_closes(
            sig, closes, dates, opens=opens, lows=lows, highs=highs, standard="same_day_plan"
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "次日红")
        self.assertEqual(out.get("outcome_standard"), "same_day_plan")
        self.assertAlmostEqual(float(out["outcome_day1_pct"]), 2.0, places=2)

    def test_same_day_plan_miss(self) -> None:
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
        }
        closes = [10.5, 10.6]
        dates = ["2026-09-10", "2026-09-11"]
        lows = [10.2, 10.3]
        out = score_signal_with_closes(
            sig, closes, dates, lows=lows, standard="same_day_plan"
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "当日未触达")

    def test_filled_standard_uses_fill(self) -> None:
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
            "fill_price": 9.8,
        }
        closes = [10.0, 10.0, 10.1]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        out = score_signal_with_closes(sig, closes, dates, standard="filled")
        self.assertIsNotNone(out)
        assert out is not None
        # day1 10.0 vs fill 9.8 → +2.04% → 次日红
        self.assertEqual(out.get("outcome_label"), "次日红")
        self.assertAlmostEqual(float(out["outcome_day1_pct"]), 2.04, places=1)

    def test_filled_standard_no_fill(self) -> None:
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
        }
        closes = [10.0, 10.2]
        dates = ["2026-09-10", "2026-09-11"]
        out = score_signal_with_closes(sig, closes, dates, standard="filled")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "无成交")

    def test_sell_open_grace_ignores_day1_high(self) -> None:
        sig = {
            "signal_type": "sell",
            "trade_date": "2026-09-10",
            "price": 10.0,
            "fill_price": 10.0,
        }
        closes = [10.0, 10.05, 10.1]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        highs = [10.0, 11.0, 10.2]
        lows = [9.9, 10.0, 10.0]
        opens = [10.0, 10.8, 10.05]
        out = score_signal_with_closes(
            sig, closes, dates, opens=opens, lows=lows, highs=highs
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertNotEqual(out.get("outcome_label"), "卖飞")

    def test_watch_track_uses_day0_open_proxy(self) -> None:
        """Early soft-sell print should not mark 卖飞 vs open-proxy decision."""
        sig = {
            "signal_type": "sell",
            "trade_date": "2026-09-10",
            "price": 9.7,
            "fill_price": 9.7,
            "signaled_at": "2026-09-10 09:32:00",
            "payload": {"open_buffer_track": "watch"},
        }
        closes = [10.0, 10.2, 10.1]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        opens = [10.0, 10.15, 10.05]
        highs = [10.1, 10.25, 10.2]
        lows = [9.6, 10.0, 10.0]
        plain = score_signal_with_closes(
            {**sig, "payload": {}},
            closes,
            dates,
            opens=opens,
            lows=lows,
            highs=highs,
        )
        watch = score_signal_with_closes(
            sig, closes, dates, opens=opens, lows=lows, highs=highs
        )
        self.assertIsNotNone(plain)
        self.assertIsNotNone(watch)
        assert plain is not None and watch is not None
        self.assertEqual(plain.get("outcome_label"), "卖飞")
        self.assertEqual(watch.get("outcome_exit_basis"), "watch_945_open_proxy")
        self.assertNotEqual(watch.get("outcome_label"), "卖飞")

    def test_score_rounds_before_label_boundary(self) -> None:
        """Raw -1.498 must round to -1.5 and label 次日绿, not 三日绿."""
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 3.471,
        }
        # day1 close 3.419 → raw ≈ -1.498; day3 close 3.382 → ≈ -2.56
        closes = [3.473, 3.419, 3.40, 3.382]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12", "2026-09-15"]
        opens = [3.496, 3.473, 3.41, 3.404]
        lows = [3.47, 3.409, 3.39, 3.379]
        highs = [3.502, 3.48, 3.42, 3.409]
        out = score_signal_with_closes(
            sig, closes, dates, opens=opens, lows=lows, highs=highs
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "次日绿")
        self.assertAlmostEqual(float(out["outcome_day1_pct"]), -1.5, places=2)

    def test_deep_green_beats_open_fade(self) -> None:
        """Gap-up open must not override a ≤−1.5% close into 冲高回落."""
        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-10",
            "price": 10.0,
        }
        closes = [10.0, 9.7, 9.8]
        dates = ["2026-09-10", "2026-09-11", "2026-09-12"]
        opens = [10.0, 10.2, 9.75]  # day1 open +2%
        lows = [9.9, 9.65, 9.7]
        highs = [10.1, 10.25, 9.9]
        out = score_signal_with_closes(
            sig, closes, dates, opens=opens, lows=lows, highs=highs
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("outcome_label"), "次日绿")

    def test_forward_session_not_ready_intraday(self) -> None:
        from market_desk.review import forward_session_ready
        from datetime import datetime, timezone, timedelta

        cn = timezone(timedelta(hours=8))
        self.assertFalse(
            forward_session_ready("2026-09-23", now=datetime(2026, 9, 23, 9, 34, tzinfo=cn))
        )
        self.assertTrue(
            forward_session_ready("2026-09-23", now=datetime(2026, 9, 23, 15, 5, tzinfo=cn))
        )
        self.assertTrue(
            forward_session_ready("2026-09-22", now=datetime(2026, 9, 23, 9, 34, tzinfo=cn))
        )

    def test_score_waits_for_day1_close(self) -> None:
        """Intraday day1 bar must not lock 次日红 (returns pending clear)."""
        from unittest.mock import patch
        from datetime import datetime, timezone, timedelta

        sig = {
            "signal_type": "buy",
            "trade_date": "2026-09-22",
            "price": 37.35,
        }
        closes = [39.7, 39.7]
        dates = ["2026-09-22", "2026-09-23"]
        opens = [36.8, 39.7]
        lows = [36.45, 38.1]
        highs = [39.7, 39.99]
        cn = timezone(timedelta(hours=8))
        with patch(
            "market_desk.review._cn_now",
            return_value=datetime(2026, 9, 23, 9, 34, tzinfo=cn),
        ):
            out = score_signal_with_closes(
                sig, closes, dates, opens=opens, lows=lows, highs=highs
            )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertTrue(out.get("outcome_pending"))
        self.assertIsNone(out.get("outcome_label"))


if __name__ == "__main__":
    unittest.main()
