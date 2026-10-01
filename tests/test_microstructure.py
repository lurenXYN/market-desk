"""Stage-2 microstructure: auction alpha, volume absorption, slippage filter."""

from __future__ import annotations

import unittest
from datetime import datetime

from market_desk.microstructure import (
    STRONG_LABEL,
    TRAP_LABEL,
    apply_absorption_gates,
    apply_auction_alpha,
    apply_slippage_filter,
    auction_sample,
    compute_alpha,
    estimate_buy_impact,
    evaluate_absorption,
    evaluate_tape,
    record_tape,
    reset_tape,
    tape_snapshot,
    tick_rule_buy_share,
)
from market_desk.review.hit_rates import _gate_bucket
from market_desk.verdict.common import hard_confirm_fails, probe_blocking_fails


def _tape(pcts: list[float], *, vol_ratio: float | None = None, start_min: int = 20) -> list[dict]:
    """Build one tape sample per minute from 09:<start_min> onward."""
    out = []
    for i, pct in enumerate(pcts):
        out.append(
            {
                "t": 9 * 3600 + (start_min + i) * 60,
                "pct": pct,
                "price": 10.0 * (1 + pct / 100.0),
                "match_lots": 1000.0 * (i + 1),
                "unmatched_buy": 500.0,
                "unmatched_sell": 100.0,
                "vol_ratio": vol_ratio,
            }
        )
    return out


def _minutes(prices: list[float], volumes: list[float] | None = None) -> list[dict]:
    """Build minute rows without an official VWAP (cumulative fallback)."""
    vols = volumes or [1000.0] * len(prices)
    return [{"price": p, "volume": v} for p, v in zip(prices, vols)]


class AuctionAlphaTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_tape()

    def test_auction_sample_reads_match_and_imbalance(self) -> None:
        quote = {
            "price": 10.5,
            "prev": 10.0,
            "pct": 5.0,
            "bids": [[10.5, 800.0], [10.5, 200.0], [10.4, 50.0]],
            "asks": [[10.5, 300.0]],
            "vol_ratio": 2.1,
        }
        s = auction_sample(quote, datetime(2026, 10, 9, 9, 22, 30))
        self.assertEqual(s["match_lots"], 300.0)
        self.assertEqual(s["unmatched_buy"], 700.0)
        self.assertEqual(s["unmatched_sell"], 0.0)
        self.assertEqual(s["pct"], 5.0)

    def test_fading_gap_up_is_trap(self) -> None:
        res = evaluate_tape(_tape([5.0, 4.6, 4.0, 3.5, 3.2]), baseline=1.0)
        self.assertEqual(res["tag"], "trap")
        self.assertEqual(res["label"], TRAP_LABEL)
        self.assertGreaterEqual(res["fade_pp"], 1.2)

    def test_yesterday_zt_miss_is_trap(self) -> None:
        res = evaluate_tape(_tape([0.2, 0.0, -0.1, -0.2]), baseline=0.5, yzt=True)
        self.assertEqual(res["tag"], "trap")
        self.assertTrue(any("不及预期" in r for r in res["reasons"]))

    def test_beat_in_divergent_sector_is_strong(self) -> None:
        res = evaluate_tape(
            _tape([3.0, 3.4, 3.9, 4.2, 4.5], vol_ratio=2.5),
            baseline=0.3,
            peer_median=0.3,
            peer_spread=1.0,
        )
        self.assertEqual(res["tag"], "strong")
        self.assertEqual(res["label"], STRONG_LABEL)

    def test_strong_needs_money_confirmation(self) -> None:
        res = evaluate_tape(
            _tape([3.0, 3.4, 3.9, 4.2, 4.5], vol_ratio=0.8),
            baseline=0.3,
            peer_median=0.3,
        )
        self.assertEqual(res["tag"], "neutral")

    def test_too_few_samples_is_unknown(self) -> None:
        res = evaluate_tape(_tape([5.0, 3.0]), baseline=0.0)
        self.assertEqual(res["tag"], "unknown")

    def test_record_tape_keeps_window_only_and_resets_by_day(self) -> None:
        quote = {"600000": {"price": 10.2, "prev": 10.0, "pct": 2.0}}
        record_tape(quote, datetime(2026, 10, 9, 9, 18), trade_date="20261009")
        self.assertEqual(tape_snapshot(), {})
        record_tape(quote, datetime(2026, 10, 9, 9, 21), trade_date="20261009")
        record_tape(quote, datetime(2026, 10, 9, 9, 22), trade_date="20261009")
        self.assertEqual(len(tape_snapshot()["600000"]), 2)
        record_tape(quote, datetime(2026, 10, 10, 9, 21), trade_date="20261010")
        self.assertEqual(len(tape_snapshot()["600000"]), 1)

    def test_compute_alpha_uses_peer_group(self) -> None:
        tape = {
            "600001": _tape([3.0, 3.4, 3.9, 4.2, 4.5], vol_ratio=2.5),
            "600002": _tape([0.2, 0.2, 0.3, 0.3]),
            "600003": _tape([0.0, 0.1, 0.1, 0.2]),
            "600004": _tape([0.4, 0.4, 0.5, 0.5]),
        }
        meta = {c: {"group": "半导体", "yzt": False} for c in tape}
        out = compute_alpha(market_median=1.5, tape=tape, meta=meta)
        self.assertEqual(out["600001"]["tag"], "strong")
        self.assertEqual(out["600001"]["group"], "半导体")
        self.assertEqual(out["600002"]["tag"], "neutral")

    def test_trap_kills_ready_inside_bridge_window(self) -> None:
        rec = {
            "buy": True,
            "items": [{"code": "600001", "ready": True, "qty": 400, "buy_price": 10.0}],
        }
        alpha = {"600001": {"tag": "trap", "label": TRAP_LABEL, "reasons": ["高开5.0%竞价回落1.8pp"]}}
        out = apply_auction_alpha(rec, alpha, now=datetime(2026, 10, 9, 9, 35))
        item = out["items"][0]
        self.assertFalse(item["ready"])
        self.assertIn(TRAP_LABEL, item["confirm_fail"])
        self.assertFalse(out["buy"])
        self.assertIn(TRAP_LABEL, probe_blocking_fails(item["confirm_fail"]))

    def test_trap_soft_shrinks_after_bridge(self) -> None:
        rec = {"items": [{"code": "600001", "ready": True, "qty": 1000}]}
        alpha = {"600001": {"tag": "trap", "label": TRAP_LABEL, "reasons": []}}
        out = apply_auction_alpha(rec, alpha, now=datetime(2026, 10, 9, 10, 0))
        item = out["items"][0]
        self.assertTrue(item["ready"])
        self.assertEqual(item["qty"], 700)
        self.assertIn("竞价诱多·降仓", item["confirm_soft"])
        again = apply_auction_alpha(out, alpha, now=datetime(2026, 10, 9, 10, 1))
        self.assertEqual(again["items"][0]["qty"], 700)

    def test_strong_is_badge_only(self) -> None:
        rec = {"items": [{"code": "600001", "ready": False, "near_entry": True}]}
        alpha = {"600001": {"tag": "strong", "label": STRONG_LABEL, "reasons": []}}
        out = apply_auction_alpha(rec, alpha, now=datetime(2026, 10, 9, 9, 35))
        item = out["items"][0]
        self.assertFalse(item["ready"])
        self.assertEqual(item["auction_badge"], STRONG_LABEL)


class AbsorptionTests(unittest.TestCase):
    def test_tick_rule_flat_bars_inherit_direction(self) -> None:
        share, bars = tick_rule_buy_share([10.0, 10.1, 10.1, 10.0], [0, 100, 100, 200], 10)
        self.assertEqual(bars, 3)
        self.assertEqual(share, 0.5)

    def test_falling_vwap_without_buyers_fails(self) -> None:
        prices = [10.0 - 0.02 * i for i in range(30)]
        res = evaluate_absorption(_minutes(prices))
        self.assertIs(res["ok"], False)
        self.assertEqual(res["label"], "均价下拐无主动承接")

    def test_shakeout_drift_with_buyers_is_absorption(self) -> None:
        # Price eases overall but buyer-initiated bars carry the volume.
        prices, vols = [], []
        px = 10.0
        for i in range(30):
            if i % 2 == 0:
                px += 0.01
                vols.append(3000.0)
            else:
                px -= 0.015
                vols.append(800.0)
            prices.append(round(px, 3))
        res = evaluate_absorption(_minutes(prices, vols), {"outer_vol": 6000, "inner_vol": 4000})
        self.assertIs(res["ok"], True)
        self.assertEqual(res["label"], "阴跌有承接（假阴跌）")

    def test_thin_minutes_without_quote_is_unknown(self) -> None:
        res = evaluate_absorption(_minutes([10.0, 10.1, 10.0]))
        self.assertIsNone(res["ok"])

    def test_fail_kills_ready_and_blocks_probe(self) -> None:
        prices = [10.0 - 0.02 * i for i in range(30)]
        rec = {
            "buy": True,
            "items": [
                {"code": "600001", "ready": True, "qty": 400},
                {"code": "600002", "ready": False, "near_entry": False},
            ],
        }
        out = apply_absorption_gates(rec, {"600001": _minutes(prices), "600002": _minutes(prices)})
        first, second = out["items"]
        self.assertFalse(first["ready"])
        self.assertIn("均价下拐无主动承接", hard_confirm_fails(first["confirm_fail"]))
        self.assertIn("均价下拐无主动承接", probe_blocking_fails(first["confirm_fail"]))
        self.assertFalse(out["buy"])
        self.assertNotIn("confirm_fail", second)
        self.assertIn("absorption", second)

    def test_unknown_keeps_ready_but_flags_loudly(self) -> None:
        rec = {"items": [{"code": "600001", "name": "测试", "ready": True, "qty": 400}]}
        out = apply_absorption_gates(rec, {"600001": _minutes([10.0, 10.1, 10.0])})
        item = out["items"][0]
        self.assertTrue(item["ready"])
        self.assertIn("承接数据不足", item["confirm_soft"])
        self.assertTrue(item["absorb_unconfirmed"])
        self.assertIn("分时不足", item["absorb_warn"])
        self.assertTrue(item["reason"].startswith("⚠ 承接未确认"))
        self.assertEqual(out["absorb_unconfirmed"], ["测试"])
        self.assertTrue(out["size_note"].startswith("⚠ 承接未确认：测试"))

    def test_unconfirmed_flag_cleared_when_absorption_confirms(self) -> None:
        prices, vols = [], []
        px = 10.0
        for i in range(30):
            px += 0.01 if i % 2 == 0 else -0.015
            vols.append(3000.0 if i % 2 == 0 else 800.0)
            prices.append(round(px, 3))
        rec = {"items": [{"code": "600001", "ready": True, "absorb_unconfirmed": True, "absorb_warn": "x"}]}
        out = apply_absorption_gates(rec, {"600001": _minutes(prices, vols)})
        item = out["items"][0]
        self.assertNotIn("absorb_unconfirmed", item)
        self.assertEqual(out["absorb_unconfirmed"], [])

    def test_near_entry_unknown_is_not_flagged(self) -> None:
        rec = {"items": [{"code": "600001", "ready": False, "near_entry": True}]}
        out = apply_absorption_gates(rec, {"600001": _minutes([10.0, 10.1, 10.0])})
        self.assertNotIn("absorb_unconfirmed", out["items"][0])


class SlippageTests(unittest.TestCase):
    _NOW = datetime(2026, 10, 9, 10, 30)

    def _deep_quote(self) -> dict:
        return {
            "bids": [[10.00, 5000.0], [9.99, 5000.0]],
            "asks": [[10.01, 5000.0], [10.02, 5000.0], [10.03, 5000.0]],
            "amount": 6e8,
        }

    def test_deep_book_is_ok(self) -> None:
        res = estimate_buy_impact(self._deep_quote(), 1000, now=self._NOW)
        self.assertEqual(res["level"], "ok")
        self.assertEqual(res["mult"], 1.0)
        self.assertLess(res["impact_bps"], 10.0)

    def test_wide_spread_is_thin(self) -> None:
        quote = {"bids": [[9.90, 100.0]], "asks": [[10.00, 100.0]], "amount": 6e8}
        res = estimate_buy_impact(quote, 1000, now=self._NOW)
        self.assertEqual(res["level"], "thin")
        self.assertIn("点差过大", res["flags"])

    def test_low_price_one_tick_spread_is_ok(self) -> None:
        quote = {
            "bids": [[2.00, 50000.0]],
            "asks": [[2.01, 50000.0], [2.02, 50000.0]],
            "amount": 6e8,
        }
        res = estimate_buy_impact(quote, 2000, now=self._NOW)
        self.assertGreater(res["spread_bps"], 40.0)
        self.assertEqual(res["level"], "ok")

    def test_low_price_two_tick_spread_flags(self) -> None:
        quote = {"bids": [[2.00, 50000.0]], "asks": [[2.02, 50000.0]], "amount": 6e8}
        res = estimate_buy_impact(quote, 2000, now=self._NOW)
        self.assertIn("点差过大", res["flags"])

    def test_shallow_book_caps_qty(self) -> None:
        quote = {"bids": [[10.00, 10.0]], "asks": [[10.01, 4.0], [10.02, 4.0]], "amount": 6e8}
        res = estimate_buy_impact(quote, 2000, now=self._NOW)
        self.assertIn("盘口偏薄", res["flags"])
        self.assertEqual(res["qty_cap"], 400)

    def test_sparse_turnover_warns(self) -> None:
        quote = dict(self._deep_quote(), amount=1e6)
        res = estimate_buy_impact(quote, 1000, now=self._NOW)
        self.assertEqual(res["level"], "warn")
        self.assertIn("成交稀疏", res["flags"])

    def test_no_asks_is_na(self) -> None:
        res = estimate_buy_impact({"bids": [[10.0, 100.0]], "asks": []}, 1000)
        self.assertEqual(res["level"], "na")

    def test_filter_shrinks_once_and_keeps_ready(self) -> None:
        quote = {"bids": [[9.90, 500.0]], "asks": [[10.00, 500.0]], "amount": 6e8}
        rec = {"items": [{"code": "600001", "name": "测试", "ready": True, "qty": 1000}]}
        out = apply_slippage_filter(rec, {"600001": quote}, now=self._NOW)
        item = out["items"][0]
        self.assertTrue(item["ready"])
        self.assertEqual(item["qty"], 500)
        self.assertTrue(item["slip_warn"])
        self.assertIn("流动性过滤降仓", out["size_note"])
        again = apply_slippage_filter(out, {"600001": quote}, now=self._NOW)
        self.assertEqual(again["items"][0]["qty"], 500)

    def test_filter_skips_cards_off_the_band(self) -> None:
        quote = {"bids": [[9.90, 500.0]], "asks": [[10.00, 500.0]], "amount": 6e8}
        rec = {"items": [{"code": "600001", "ready": False, "near_entry": False, "qty": 1000}]}
        out = apply_slippage_filter(rec, {"600001": quote}, now=self._NOW)
        self.assertEqual(out["items"][0]["qty"], 1000)
        self.assertNotIn("slippage", out["items"][0])


class UnconfirmedPushTests(unittest.TestCase):
    def _snap(self, action: str, primary: dict) -> dict:
        return {
            "ok": True,
            "phase": "主升",
            "verdict": {
                "action": action,
                "mainline": {"name": "半导体"},
                "recommend": {"primary": primary, "items": [primary]},
            },
        }

    def test_buy_push_carries_unconfirmed_warning(self) -> None:
        from market_desk.notify import build_toast_alerts

        primary = {
            "code": "600001",
            "name": "测试",
            "buy_price": 10.0,
            "ready": True,
            "absorb_unconfirmed": True,
            "absorb_warn": "承接未确认（主买46%）：ready 仅凭形态，宜小仓或等主买≥50%",
        }
        alerts = build_toast_alerts(self._snap("观察回踩", {}), self._snap("可买入", primary))
        buy = [a for a in alerts if a[0].startswith("buy:")]
        self.assertEqual(len(buy), 1)
        self.assertIn("⚠承接未确认", buy[0][1])
        self.assertTrue(buy[0][2].startswith("⚠ 承接未确认（主买46%）"))

    def test_micro_gates_resync_primary(self) -> None:
        from market_desk.engine import engine

        prices = [10.0 - 0.02 * i for i in range(30)]
        item = {"code": "600001", "ready": True, "qty": 400}
        rec = {"buy": True, "primary": item, "items": [item]}
        out = engine._apply_micro_gates(
            rec,
            minutes_by_code={"600001": _minutes(prices)},
            quotes_by_code={},
            now=datetime(2026, 10, 9, 10, 30),
            trade_date="20261009",
        )
        self.assertFalse(out["primary"]["ready"])
        self.assertIs(out["primary"], out["items"][0])


class GateBucketTests(unittest.TestCase):
    def test_new_gate_buckets(self) -> None:
        self.assertEqual(_gate_bucket(TRAP_LABEL), "竞价")
        self.assertEqual(_gate_bucket("均价下拐无主动承接"), "承接")
        self.assertEqual(_gate_bucket("主动买盘承接不足"), "承接")


if __name__ == "__main__":
    unittest.main()
