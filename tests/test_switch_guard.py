"""Mainline switch guard: old theme observe sell; protect new-theme soft chops."""

from __future__ import annotations

import unittest
from datetime import datetime

from market_desk.verdict import (
    _sell_item,
    attach_switch_guard,
    build_switch_guard,
    inject_switch_from_theme,
)


def _base_row(**kwargs):
    row = {
        "id": 1,
        "code": "600000",
        "name": "测试强票",
        "buy_price": 10.0,
        "qty": 200,
        "last": 10.8,
        "high": 10.85,
        "low": 10.2,
        "last_pct": 3.5,
        "pnl_pct": 8.0,
        "peak_price": 10.9,
        "last_buy_date": "2026-09-10",
        "day_sold_qty": 0,
        "entry_board": "半导体",
    }
    row.update(kwargs)
    return row


class SwitchGuardTests(unittest.TestCase):
    def test_build_switch_guard_active_on_fresh_flip(self) -> None:
        now = datetime(2026, 9, 18, 10, 5, 0)
        sg = build_switch_guard(
            now=now,
            sticky_since="2026-09-18 10:00:00",
            current_name="半导体",
            prev_name="人工智能",
        )
        self.assertTrue(sg["active"])
        self.assertEqual(sg["from_name"], "人工智能")
        self.assertEqual(sg["to_name"], "半导体")
        self.assertEqual(sg["age_seconds"], 300.0)

    def test_inject_switch_from_theme_adds_fade_peer(self) -> None:
        themes = [
            {
                "name": "半导体",
                "role": "primary",
                "status": "确认中",
                "lifecycle": "ongoing",
                "pool_codes": ["600000"],
            }
        ]
        sg = {
            "active": True,
            "from_name": "人工智能",
            "to_name": "半导体",
            "grace_seconds": 2700,
            "age_seconds": 60,
        }
        out = inject_switch_from_theme(themes, sg, hot=[])
        names = [t["name"] for t in out]
        self.assertIn("人工智能", names)
        old = next(t for t in out if t["name"] == "人工智能")
        self.assertEqual(old["role"], "switch_from")
        self.assertEqual(old["status"], "退潮")
        self.assertTrue(old.get("switch_from"))

    def test_attach_switch_guard_merges_db_switch(self) -> None:
        now = datetime(2026, 9, 18, 10, 20, 0)
        verdict = {
            "mainline": {"name": "半导体"},
            "sell_themes": [
                {
                    "name": "半导体",
                    "role": "primary",
                    "status": "确认中",
                    "lifecycle": "ongoing",
                    "pool_codes": [],
                }
            ],
            "switch_guard": {"active": False},
            "algo_notes": [],
        }
        switches = [
            {
                "from_name": "人工智能",
                "to_name": "半导体",
                "switched_at": "2026-09-18 10:00:00",
            }
        ]
        out = attach_switch_guard(verdict, switches, now=now, hot=[])
        sg = out["switch_guard"]
        self.assertTrue(sg["active"])
        self.assertEqual(sg["from_name"], "人工智能")
        self.assertTrue(any(t.get("role") == "switch_from" for t in out["sell_themes"]))

    def test_sell_item_suppresses_soft_trim_on_new_theme_strong(self) -> None:
        row = _base_row(last_pct=4.0, pnl_pct=5.0, last=10.5, high=10.8)
        verdict = {
            "mainline": {
                "name": "半导体",
                "status": "确认中",
                "lifecycle": "ongoing",
            },
            "sell_themes": [
                {
                    "name": "半导体",
                    "role": "primary",
                    "status": "退潮",
                    "lifecycle": "ending",
                    "pool_codes": ["600000"],
                    "carrier_pct": 1.0,
                    "carrier_falling": False,
                    "main_yi": 0.0,
                }
            ],
            "carrier": {"pct": 1.0, "falling": False},
            "segment": {"key": "morning"},
            "switch_guard": {
                "active": True,
                "from_name": "人工智能",
                "to_name": "半导体",
                "grace_seconds": 2700,
                "age_seconds": 120,
            },
            "elliott": {"ok": False},
        }
        item = _sell_item(
            row,
            verdict,
            "发酵",
            trade_date="2026-09-18",
            trend={
                "up": True,
                "down": False,
                "label": "上升",
                "ma20": 9.5,
                "quality": "ok",
            },
        )
        self.assertIsNotNone(item)
        assert item is not None
        if item.get("switch_guard_held"):
            self.assertIs(item.get("ready"), False)
            self.assertEqual(item.get("exit_mode"), "hold")
            self.assertIn("换防", item.get("role_label") or "")
            self.assertIn("刚换防", item.get("reason") or "")
        elif (
            item.get("ready")
            and item.get("exit_mode") == "half"
            and item.get("urgency") == "trim"
        ):
            self.fail("soft trim should be suppressed under switch_guard")

    def test_sell_item_keeps_hard_stop_under_switch_guard(self) -> None:
        row = _base_row(
            last=8.5,
            high=10.0,
            low=8.4,
            last_pct=-5.0,
            pnl_pct=-15.0,
            peak_price=10.0,
        )
        verdict = {
            "mainline": {
                "name": "半导体",
                "status": "确认中",
                "lifecycle": "ongoing",
            },
            "sell_themes": [
                {
                    "name": "半导体",
                    "role": "primary",
                    "status": "确认中",
                    "lifecycle": "ongoing",
                    "pool_codes": ["600000"],
                    "carrier_pct": 0.0,
                    "carrier_falling": False,
                }
            ],
            "carrier": {"pct": 0.0, "falling": False},
            "segment": {"key": "morning"},
            "switch_guard": {
                "active": True,
                "from_name": "人工智能",
                "to_name": "半导体",
                "grace_seconds": 2700,
                "age_seconds": 60,
            },
            "elliott": {"ok": False},
        }
        item = _sell_item(row, verdict, "发酵", trade_date="2026-09-18")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.get("urgency"), "stop")
        self.assertTrue(item.get("ready"))
        self.assertIs(item.get("switch_guard_held"), False)


if __name__ == "__main__":
    unittest.main()
