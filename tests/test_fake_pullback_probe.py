"""Fake pullback tip: tip minute fails block ready but allow probe."""

from __future__ import annotations

import unittest

from market_desk.minute_confirm import apply_minute_confirmations, evaluate_minute_structure
from market_desk.verdict import apply_mainline_probe, hard_confirm_fails, probe_blocking_fails


def _tip_minutes(*, last: float = 10.0, hi: float = 10.02) -> list[dict]:
    """Build a short tip-sitting series (near recent high)."""
    rows: list[dict] = []
    for i in range(20):
        px = 9.7 + i * 0.015
        rows.append({"price": px, "avg": px * 0.999, "volume": 1000})
    for _ in range(20):
        rows.append({"price": last, "avg": last * 0.999, "volume": 800})
    rows[-5] = {"price": hi, "avg": hi * 0.999, "volume": 900}
    rows[-1] = {"price": last, "avg": last * 0.999, "volume": 700}
    return rows


class FakePullbackProbeTests(unittest.TestCase):
    def test_tip_structure_flags_at_tip(self) -> None:
        v = evaluate_minute_structure(_tip_minutes())
        self.assertIs(v.get("ok"), False)
        self.assertTrue(
            v.get("at_tip") or "分时贴近" in str(v.get("label") or "")
        )

    def test_minute_demote_sets_fake_pullback_tip(self) -> None:
        rec = {
            "buy": True,
            "items": [
                {
                    "code": "600000",
                    "name": "测试",
                    "kind": "stock",
                    "ready": True,
                    "near_entry": True,
                    "buy_price": 10.0,
                    "qty": 400,
                }
            ],
        }
        out = apply_minute_confirmations(rec, {"600000": _tip_minutes()})
        item = out["items"][0]
        self.assertIs(item.get("ready"), False)
        self.assertTrue(item.get("fake_pullback_tip"))
        self.assertTrue(hard_confirm_fails(item.get("confirm_fail") or []))
        self.assertFalse(probe_blocking_fails(item.get("confirm_fail") or []))

    def test_mainline_probe_allows_tip_only_fails(self) -> None:
        rec = {
            "items": [
                {
                    "code": "600000",
                    "kind": "stock",
                    "ready": False,
                    "near_entry": True,
                    "fake_pullback_tip": True,
                    "confirm_fail": ["分时贴近近期高点"],
                    "qty": 400,
                    "reason": "回踩到位",
                }
            ]
        }
        out = apply_mainline_probe(rec, block_arm=False)
        item = out["items"][0]
        self.assertTrue(item.get("probe_ok"))
        self.assertIs(item.get("ready"), False)
        self.assertIn("贴尖", item.get("reason") or "")
        self.assertEqual(item.get("qty"), 200)

    def test_mainline_probe_still_blocks_non_tip_hard(self) -> None:
        rec = {
            "items": [
                {
                    "code": "600000",
                    "kind": "stock",
                    "ready": False,
                    "near_entry": True,
                    "confirm_fail": ["分时贴近近期高点", "分时仍在均价下方"],
                    "qty": 400,
                }
            ]
        }
        out = apply_mainline_probe(rec, block_arm=False)
        self.assertIs(out["items"][0].get("probe_ok"), False)

    def test_probe_blocking_filters_tip_labels(self) -> None:
        flags = ["分时贴近近期高点", "分时回撤过浅", "离日高不足"]
        self.assertEqual(probe_blocking_fails(flags), ["离日高不足"])
        self.assertEqual(set(hard_confirm_fails(flags)), set(flags))


if __name__ == "__main__":
    unittest.main()
