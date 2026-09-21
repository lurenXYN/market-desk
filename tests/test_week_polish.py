"""Tests for week-exec board, outcome compare, and low-n flags."""

from __future__ import annotations

import unittest

from market_desk.notify import is_risk_toast, is_serverchan_alert
from market_desk.review import (
    MIN_HIT_N,
    attach_low_n_flags,
    build_outcome_compare,
    build_week_exec_board,
)


class WeekPolishTests(unittest.TestCase):
    def test_attach_low_n_flags(self) -> None:
        rows = attach_low_n_flags(
            [{"label": "a", "scored_n": 3}, {"label": "b", "scored_n": MIN_HIT_N}]
        )
        self.assertTrue(rows[0]["low_n"])
        self.assertFalse(rows[1]["low_n"])

    def test_week_exec_follow_rate(self) -> None:
        rows = [
            {
                "trade_date": "2026-09-18",
                "signal_type": "buy",
                "ready": 1,
                "traded": 1,
                "skipped": 0,
                "payload": {"ready_relaxed": True, "near_entry": True},
                "price_flags": [],
            },
            {
                "trade_date": "2026-09-18",
                "signal_type": "buy",
                "ready": 1,
                "traded": 0,
                "skipped": 0,
                "payload": {"probe_ok": True},
                "price_flags": ["miss_pullback"],
                "price_mark": "未回踩",
            },
            {
                "trade_date": "2026-09-17",
                "signal_type": "buy",
                "ready": 0,
                "traded": 0,
                "skipped": 0,
                "payload": {},
                "price_flags": [],
            },
        ]
        board = build_week_exec_board(
            rows,
            dates=["2026-09-18", "2026-09-17"],
            days=5,
        )
        self.assertTrue(board["ok"])
        self.assertEqual(board["window_n"], 2)
        self.assertEqual(board["followed_n"], 1)
        self.assertEqual(board["follow_rate"], 50.0)

    def test_outcome_compare_classic_from_labels(self) -> None:
        rows = [
            {
                "signal_type": "buy",
                "code": "600000",
                "traded": 1,
                "skipped": 0,
                "outcome_label": "次日红",
                "trade_date": "2026-09-10",
                "price": 10.0,
            },
            {
                "signal_type": "buy",
                "code": "600001",
                "traded": 1,
                "skipped": 0,
                "outcome_label": "冲高回落",
                "trade_date": "2026-09-10",
                "price": 10.0,
            },
        ]
        out = build_outcome_compare(rows, None, hit_mode="traded")
        classic = next(r for r in out["rows"] if r["standard"] == "classic")
        self.assertEqual(classic["scored_n"], 2)
        self.assertEqual(classic["hit_n"], 1)
        self.assertEqual(classic["hit_rate"], 50.0)
        self.assertTrue(classic["low_n"])

    def test_ops_toast_policy(self) -> None:
        self.assertTrue(is_risk_toast("ops:backup:2026-09-20"))
        self.assertTrue(is_serverchan_alert("ops:health:2026-09-20"))


if __name__ == "__main__":
    unittest.main()
