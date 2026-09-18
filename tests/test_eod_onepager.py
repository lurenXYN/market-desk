"""Smoke tests for the end-of-day one-pager (P&L-first push bullets)."""

from __future__ import annotations

import unittest

from market_desk.notify import format_serverchan_desp
from market_desk.report import (
    _eod_compress_switches,
    _eod_curate_push,
    build_eod_onepager,
)


class EodOnepagerTests(unittest.TestCase):
    def test_build_includes_day_pnl_in_markdown_and_push(self) -> None:
        snap = {
            "trade_date": "2026-09-18",
            "phase": "高潮",
            "updated_at": "2026-09-18 15:05:00",
            "position_summary": {
                "day_pnl": -120.5,
                "day_pnl_pct": -1.2,
                "floating_pnl": -80.0,
                "realized_pnl": -40.5,
                "count": 2,
            },
            "verdict": {
                "action": "观察回踩",
                "reason": "主线尖峰先等回踩",
                "mainline": {
                    "name": "有色",
                    "status": "尖峰禁追",
                    "lifecycle": "ongoing",
                },
                "switch_guard": {
                    "active": True,
                    "from_name": "煤炭",
                    "to_name": "有色",
                },
            },
            "mainline_switches": [
                {
                    "switched_at": "2026-09-18 10:30:00",
                    "from_name": "煤炭",
                    "to_name": "有色",
                },
                {
                    "switched_at": "2026-09-18 11:05:00",
                    "from_name": "有色",
                    "to_name": "煤炭",
                },
                {
                    "switched_at": "2026-09-18 13:20:00",
                    "from_name": "煤炭",
                    "to_name": "有色",
                },
            ],
        }
        review = {
            "summary": {
                "today": {
                    "date": "2026-09-18",
                    "buy_n": 26,
                    "sell_n": 2,
                    "traded_n": 1,
                    "skipped_n": 10,
                    "miss_pullback_n": 3,
                    "switch_n": 3,
                    "phase": "高潮",
                },
                "exec": {"score": 70, "traded_buy_n": 1, "in_band_n": 1, "chase_n": 0},
                "missed_buys": [
                    {"code": "600111", "name": "北方稀土", "price": 22.1},
                    {"code": "000629", "name": "钒钛股份", "price": 4.5},
                ],
                "tune_hints": ["高潮相位命中 32%：继续默认降观察回踩，勿追尖"],
            },
            "signals": [],
        }
        diary = [
            {
                "side": "buy",
                "code": "518880",
                "name": "黄金ETF",
                "price": 6.12,
                "qty": 1000,
            }
        ]
        brief = build_eod_onepager(
            snapshot=snap,
            review=review,
            diary=diary,
            tomorrow_brief={
                "focus": "2026-09-19默认：先处理风险与禁追，主线只观察回踩，不在开盘尖上动手。"
            },
        )
        self.assertIn("今日盈亏", brief["markdown"])
        self.assertIn("-120.50", brief["markdown"])
        self.assertTrue(brief["bullets"][0].startswith("今日盈亏"))
        self.assertTrue(brief["push_bullets"][0].startswith("今日盈亏"))
        self.assertLessEqual(len(brief["push_bullets"]), 8)
        self.assertTrue(any("成交日记" in b for b in brief["bullets"]))
        self.assertTrue(any("尖峰" in b or "观察回踩" in b for b in brief["bullets"]))
        self.assertTrue(any("净换防" in b or "最终主线" in b for b in brief["bullets"]))
        self.assertTrue(any("漏买" in b or "宜复盘" in b for b in brief["bullets"]))
        self.assertTrue(any(b.startswith("明日看点") for b in brief["bullets"]))
        self.assertTrue(any(b.startswith("调参") for b in brief["bullets"]))
        # Counts stay secondary: not the first push line.
        self.assertFalse(brief["push_bullets"][0].startswith("信号 买"))

    def test_push_curate_never_drops_pnl(self) -> None:
        bullets = [
            "相位 高潮 · 结论 观望",
            "信号 买26/卖2",
            "今日盈亏 +10.00（相对昨收/今日买价；浮盈 +10.00 · 已实现 0.00）",
            "主线切换噪声",
        ]
        push = _eod_curate_push(bullets, cap=2)
        self.assertEqual(len(push), 2)
        self.assertTrue(push[0].startswith("今日盈亏"))

    def test_switch_compress_max_two_flips(self) -> None:
        switches = [
            {"switched_at": "2026-09-18 14:00:00", "from_name": "B", "to_name": "C"},
            {"switched_at": "2026-09-18 11:00:00", "from_name": "A", "to_name": "B"},
            {"switched_at": "2026-09-18 10:00:00", "from_name": "X", "to_name": "A"},
        ]
        line = _eod_compress_switches(
            switches,
            board="C",
            switch_n=3,
            switch_guard={"active": True, "from_name": "B", "to_name": "C"},
        )
        assert line is not None
        self.assertIn("净换防", line)
        self.assertIn("关键翻转", line)
        # At most two flip segments after the label.
        flip_part = line.split("关键翻转：", 1)[-1]
        self.assertLessEqual(flip_part.count("→"), 2)

    def test_serverchan_eod_footer_short(self) -> None:
        desp = format_serverchan_desp(
            "eod:2026-09-18",
            "收盘一页纸 · 2026-09-18",
            "焦点\n· 今日盈亏 +1.00",
            {"trade_date": "2026-09-18", "phase": "高潮", "verdict": {"action": "观望"}},
        )
        self.assertIn("交易日 2026-09-18", desp)
        self.assertNotIn("作战结论", desp)
        self.assertNotIn("- 主线：", desp)
        self.assertNotIn("- 相位：", desp)


if __name__ == "__main__":
    unittest.main()
