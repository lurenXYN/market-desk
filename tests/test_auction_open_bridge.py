"""Auction → open 15min bridge: strong auction + weak open demotes buys."""

from __future__ import annotations

import unittest
from datetime import datetime

from market_desk.verdict import apply_auction_open_bridge, apply_mainline_probe


class AuctionOpenBridgeTests(unittest.TestCase):
    def test_auction_open_bridge_demotes_on_weak_open(self) -> None:
        now = datetime(2026, 9, 18, 9, 35, 0)
        act, why, hint, notes, bridge = apply_auction_open_bridge(
            "可买入",
            "主线确认可买",
            "",
            now=now,
            segment_key="open30",
            auction={"median_open": 2.5},
            metrics={"weak_index": True, "hs300_pct": -0.5},
            mainline_pct=-0.2,
            carrier_pct=-0.1,
        )
        self.assertTrue(bridge["active"])
        self.assertTrue(bridge["revoke_probe"])
        self.assertEqual(act, "观察回踩")
        self.assertIn("竞价开盘桥", notes)
        self.assertIn("降仓", hint)
        self.assertIn("开盘偏弱", why)

    def test_auction_open_bridge_inactive_outside_window(self) -> None:
        now = datetime(2026, 9, 18, 10, 5, 0)
        act, _, _, notes, bridge = apply_auction_open_bridge(
            "可买入",
            "主线确认可买",
            "",
            now=now,
            segment_key="morning",
            auction={"median_open": 2.5},
            metrics={"weak_index": True},
            mainline_pct=-1.0,
            carrier_pct=-1.0,
        )
        self.assertFalse(bridge["active"])
        self.assertEqual(act, "可买入")
        self.assertEqual(notes, [])

    def test_auction_open_bridge_inactive_when_auction_not_strong(self) -> None:
        now = datetime(2026, 9, 18, 9, 32, 0)
        act, _, _, _, bridge = apply_auction_open_bridge(
            "可买入",
            "主线确认可买",
            "",
            now=now,
            segment_key="open30",
            auction={"median_open": 0.8},
            metrics={"weak_index": True},
        )
        self.assertFalse(bridge["active"])
        self.assertEqual(act, "可买入")

    def test_auction_open_bridge_inactive_when_open_not_weak(self) -> None:
        now = datetime(2026, 9, 18, 9, 32, 0)
        act, _, _, _, bridge = apply_auction_open_bridge(
            "可买入",
            "主线确认可买",
            "",
            now=now,
            segment_key="open30",
            auction={"median_open": 2.2},
            metrics={"weak_index": False, "hs300_pct": 0.5},
            mainline_pct=1.2,
            carrier_pct=0.8,
        )
        self.assertFalse(bridge["active"])
        self.assertEqual(act, "可买入")

    def test_revoke_probe_blocks_mainline_probe(self) -> None:
        rec = {
            "items": [
                {
                    "code": "600000",
                    "kind": "stock",
                    "ready": False,
                    "near_entry": True,
                    "qty": 400,
                }
            ]
        }
        armed = apply_mainline_probe(rec, block_arm=False)
        self.assertTrue(armed["items"][0].get("probe_ok"))
        blocked = apply_mainline_probe(rec, block_arm=True)
        self.assertIs(blocked["items"][0].get("probe_ok"), False)


if __name__ == "__main__":
    unittest.main()
