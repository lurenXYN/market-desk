"""What-If gate net-value audit: trace, fill simulation, ledger, backtest limits (no network)."""

from __future__ import annotations

from datetime import datetime

import pytest

from market_desk.backtest import _row_buy_slip, simulate_buy_fill, simulate_sell_fill
from market_desk.counterfactual import (
    block_arm_reasons,
    bootstrap_ci,
    build_counterfactual_audit,
    item_blockers,
    merge_trace,
    simulate_cf_trade,
    trace_now,
)
from market_desk.review.record import _slip_fields
from market_desk.verdict.common import atr_shadow_stop, attach_atr_shadow_stops

NOW = datetime(2026, 10, 10, 16, 0)
CAL = [f"2026-09-{d:02d}" for d in range(1, 29)]


def _packed(day_idx: int, path: list[tuple[float, float, float, float]], prev: float = 10.0):
    """Klines: one prior bar, day0 at ``CAL[day_idx]``, then ``path`` (o, h, l, c) bars."""
    dates = [CAL[day_idx - 1]] + [CAL[day_idx + i] for i in range(len(path))]
    bars = [(prev, prev, prev, prev)] + path
    return (
        dates,
        [b[3] for b in bars],
        {"open": [b[0] for b in bars], "high": [b[1] for b in bars], "low": [b[2] for b in bars]},
    )


def _sig(day_idx: int, payload: dict, *, code: str = "600001", typ: str = "buy",
         at: str = "09:40:00", ready: int = 0) -> dict:
    return {
        "trade_date": CAL[day_idx],
        "signaled_at": f"{CAL[day_idx]} {at}",
        "code": code,
        "name": code,
        "signal_type": typ,
        "price": 10.0,
        "ready": ready,
        "skipped": 0,
        "payload": {"plan_price": 10.0, **payload},
    }


TRACED = {"cf": {"seen": 4, "touch_n": 1, "touch_px": 10.0}}


# ---------- trace ----------

def test_item_blockers_maps_channel_trend_cap_minute_and_hard_fails() -> None:
    item = {
        "trend_down": True,
        "block_ready": True,
        "size_cap_block": True,
        "minute": {"ok": False},
        "confirm_fail": ["日线向下", "离日高过近", "总仓触相位上限", "分时未站稳"],
    }
    keys = item_blockers(item, desk_source="side", arm_block=["开盘静默"])
    assert keys[:2] == ["通道·支线观察", "开盘静默"]
    assert "日线下降" in keys and "禁亮灯·仓位上限" in keys and "分时未确认" in keys
    assert "卡·离日高" in keys
    assert "卡·日线" not in keys and "卡·总仓上限" not in keys and "卡·分时" not in keys
    assert item_blockers({}, desk_source="main") == ["未明"]
    assert item_blockers({"dragon_scope": "side"}, desk_source="dragon") == ["通道·非主线龙头"]


def test_block_arm_reasons_from_verdict() -> None:
    v = {"segment": {"open_mute": True}, "auction_only": True, "auction_open_bridge": {"revoke_probe": True}}
    assert block_arm_reasons(v) == ["开盘静默", "仅竞价", "竞价开盘桥"]
    assert block_arm_reasons({}) == []


def test_near_miss_fills_untouched_cards_within_limit() -> None:
    from market_desk.counterfactual.audit import build_near_miss

    path = [(10.3, 10.4, 10.0, 10.2), (10.3, 10.6, 10.2, 10.5), (10.5, 10.6, 10.4, 10.5), (10.5, 10.6, 10.4, 10.5)]
    near = _sig(3, {"cf": {"seen": 5, "lo_px": 10.04}, "stop_price": 9.7})
    far = _sig(3, {"cf": {"seen": 5, "lo_px": 10.25}, "stop_price": 9.7}, code="600002")
    untraced = _sig(3, {"stop_price": 9.7}, code="600003")
    out = build_near_miss([(near, _packed(3, path)), (far, _packed(3, path)), (untraced, _packed(3, path))], now=NOW)
    by_off = {r["off"]: r for r in out["rows"]}
    assert out["traced"] == 2
    assert by_off[0.5]["n"] == 1 and by_off[2.0]["n"] == 1 and by_off[3.0]["n"] == 2
    assert by_off[0.5]["mean"] == pytest.approx((10.5 / 10.05 - 1) * 100 - 0.25, abs=0.01)


def test_trace_now_and_merge_keep_first_blocked_touch() -> None:
    far = trace_now({"last": 10.5, "plan_price": 10.0}, desk_source="main")
    assert far == {"touch": False, "ready": False, "px": 10.5, "blk": []}
    hit = trace_now({"last": 9.98, "plan_price": 10.0, "trend_down": True}, desk_source="main")
    assert hit["touch"] and hit["blk"] == ["日线下降"]
    lit = trace_now({"last": 9.97, "plan_price": 10.0, "ready": True}, desk_source="main")
    cf = merge_trace(None, far, "2026-09-02 09:31:00")
    assert cf == {"seen": 1, "lo_px": 10.5, "lo_at": "09:31:00"}
    cf = merge_trace(cf, hit, "2026-09-02 09:40:00")
    cf = merge_trace(cf, dict(hit, blk=["卡·离日高"]), "2026-09-02 09:41:00")
    cf = merge_trace(cf, lit, "2026-09-02 09:45:00")
    assert cf["seen"] == 4 and cf["touch_n"] == 3 and cf["ready_n"] == 1 and cf["blocked_n"] == 2
    assert cf["first_blk"] == ["日线下降"] and cf["first_blk_at"] == "09:40:00"
    assert cf["touch_at"] == "09:40:00" and cf["touch_px"] == 9.98
    assert cf["blk"] == {"日线下降": 1, "卡·离日高": 1}
    assert cf["lo_px"] == 9.97 and cf["lo_at"] == "09:45:00"


def test_upsert_merges_trace_and_sticky_probe(monkeypatch, tmp_path) -> None:
    import market_desk.db as desk_db

    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    base = {"trade_date": "2026-09-02", "code": "600001", "signal_type": "buy", "name": "x",
            "kind": "stock", "price": 10.0, "action": "观察回踩", "phase": "发酵", "mainline": "m"}
    desk_db.upsert_signal({**base, "signaled_at": "2026-09-02 09:31:00", "last": 10.4, "ready": 0,
                           "payload": {"probe_ok": True, "slip_bps": 12.0,
                                       "cf_now": {"touch": False, "ready": False, "px": 10.4, "blk": []}}})
    desk_db.upsert_signal({**base, "signaled_at": "2026-09-02 09:40:00", "last": 9.99, "ready": 0,
                           "payload": {"probe_ok": False,
                                       "cf_now": {"touch": True, "ready": False, "px": 9.99, "blk": ["日线下降"]}}})
    row = desk_db.find_signal("2026-09-02", "600001", "buy")
    p = row["payload"]
    assert "cf_now" not in p
    assert p["cf"]["seen"] == 2 and p["cf"]["touch_n"] == 1 and p["cf"]["first_blk"] == ["日线下降"]
    assert p["ever_probe"] is True


def test_slip_fields_only_when_measured() -> None:
    assert _slip_fields({}) == {}
    assert _slip_fields({"slippage": {"impact_bps": 23.46, "level": "warn"}}) == {"slip_bps": 23.5, "slip_level": "warn"}


# ---------- simulation ----------

def test_sim_hold_three_days_net_of_cost() -> None:
    packed = _packed(2, [(10.1, 10.2, 9.8, 10.0), (10.0, 10.3, 9.9, 10.2), (10.2, 10.5, 10.1, 10.4), (10.4, 10.7, 10.3, 10.6)])
    out = simulate_cf_trade(_sig(2, {**TRACED, "stop_price": 9.7}), packed, now=NOW)
    assert out["filled"] and out["touch"] == "实测" and not out["stop_hit"]
    assert out["r"] == pytest.approx(6.0 - 0.25, abs=1e-3)
    assert out["r1"] == pytest.approx(2.0 - 0.25, abs=1e-3)


def test_sim_stop_gap_down_fills_at_open() -> None:
    packed = _packed(2, [(10.1, 10.2, 9.8, 10.0), (9.5, 9.6, 9.3, 9.4), (9.4, 9.5, 9.2, 9.3), (9.3, 9.4, 9.2, 9.3)])
    out = simulate_cf_trade(_sig(2, {**TRACED, "stop_price": 9.7}), packed, now=NOW)
    assert out["stop_hit"] and out["exit"] == 9.5 and out["held"] == 1
    assert out["r"] == pytest.approx(-5.25, abs=1e-3)


def test_sim_sealed_limit_down_rolls_stop_to_next_open() -> None:
    packed = _packed(2, [(10.1, 10.2, 9.8, 10.0), (9.0, 9.0, 9.0, 9.0), (8.5, 8.7, 8.3, 8.4), (8.4, 8.5, 8.2, 8.3)])
    out = simulate_cf_trade(_sig(2, {**TRACED, "stop_price": 9.7}), packed, now=NOW)
    assert out["stop_hit"] and out["exit"] == 8.5 and out["held"] == 2


def test_sim_limit_up_day0_and_traced_untouched_do_not_fill() -> None:
    sealed = _packed(2, [(11.0, 11.0, 11.0, 11.0), (11.0, 11.5, 10.8, 11.2)])
    assert simulate_cf_trade(_sig(2, {"plan_price": 11.5}), sealed, now=NOW)["touch"] == "一字涨停"
    packed = _packed(2, [(10.1, 10.2, 9.8, 10.0), (10.0, 10.3, 9.9, 10.2)])
    out = simulate_cf_trade(_sig(2, {"cf": {"seen": 9}}), packed, now=NOW)
    assert out == {"filled": False, "entry": 10.0, "touch": "实测未到"}


def test_sim_touch_tiers_for_untraced_history() -> None:
    path = [(10.3, 10.4, 9.9, 10.2), (10.2, 10.3, 10.1, 10.2), (10.2, 10.3, 10.1, 10.2), (10.2, 10.3, 10.1, 10.2)]
    closed = _packed(2, [(10.3, 10.4, 9.9, 9.95)] + path[1:])
    assert simulate_cf_trade(_sig(2, {}, at="13:30:00"), closed, now=NOW)["touch"] == "收盘"
    early = _packed(2, path)
    assert simulate_cf_trade(_sig(2, {}, at="09:45:00"), early, now=NOW)["touch"] == "早盘"
    assert simulate_cf_trade(_sig(2, {}, at="13:30:00"), early, now=NOW)["touch"] == "触达未知"
    assert simulate_cf_trade(_sig(2, {"near_entry": True}, at="13:30:00"), early, now=NOW)["touch"] == "盘中"


def test_sim_entry_offset_reprices_fill_and_keeps_plan_stop() -> None:
    packed = _packed(2, [(10.1, 10.2, 9.8, 10.0), (10.0, 10.3, 9.9, 10.2), (10.2, 10.5, 10.1, 10.4), (10.4, 10.7, 10.3, 10.6)])
    sig = _sig(2, {"cf": {"seen": 4, "touch_n": 1, "touch_px": 9.9}})
    base = simulate_cf_trade(sig, packed, entry_offset_pct=0.0, now=NOW)
    plus1 = simulate_cf_trade(sig, packed, entry_offset_pct=1.0, now=NOW)
    capped = simulate_cf_trade(sig, packed, entry_offset_pct=3.0, now=NOW)
    assert base["entry"] == 10.0 and plus1["entry"] == 10.1 and capped["entry"] == 10.2
    assert base["stop"] == plus1["stop"] == capped["stop"] == 9.7
    assert plus1["r"] == pytest.approx((10.6 / 10.1 - 1) * 100 - 0.25, abs=1e-3)
    gap = _packed(2, [(9.8, 10.2, 9.7, 10.0), (10.0, 10.3, 9.9, 10.2), (10.2, 10.5, 10.1, 10.4), (10.4, 10.7, 10.3, 10.6)])
    out = simulate_cf_trade(_sig(2, {}, at="09:25:00"), gap, entry_offset_pct=1.5, now=NOW)
    assert out["entry"] == 9.8


def test_sim_pending_without_full_window() -> None:
    packed = _packed(2, [(10.1, 10.2, 9.8, 10.0), (10.0, 10.3, 9.9, 10.2)])
    out = simulate_cf_trade(_sig(2, TRACED), packed, now=NOW)
    assert out["filled"] and out["pending"]


# ---------- audit ----------

def _card(code: str, day_idx: int, final: float, *, blk: list[str] | None = None, lit: bool = False,
          typ: str = "buy", payload: dict | None = None) -> tuple[dict, tuple]:
    cf = {"seen": 5, "touch_n": 2, "touch_px": 10.0}
    if lit:
        cf["ready_n"] = 1
    else:
        cf["blocked_n"] = 2
        cf["first_blk"] = list(blk or [])
    mid = (10.0 + final) / 2.0
    path = [(10.1, 10.2, 9.9, 10.0), (10.0, 10.1, 9.8, mid), (mid, mid + 0.1, min(mid, final) - 0.1, final),
            (final, final + 0.05, final - 0.05, final)]
    sig = _sig(day_idx, {"cf": cf, "stop_price": 8.0, **(payload or {})}, code=code, typ=typ)
    return sig, _packed(day_idx, path)


def _run(cards: list[tuple[dict, tuple]], **kw) -> dict:
    rows = [c[0] for c in cards]
    klines = {c[0]["code"]: c[1] for c in cards}
    return build_counterfactual_audit(rows, klines, now=NOW, **kw)


def _gate(out: dict, key: str) -> dict:
    return next(g for g in out["gates"] if g["key"] == key)


def test_audit_verdicts_hero_loss_and_tape() -> None:
    cards = []
    for i, d in enumerate(range(2, 8)):
        cards.append(_card(f"6000{i}1", d, 9.6 - 0.05 * (i % 2), blk=["日线下降"]))
        cards.append(_card(f"6000{i}2", d, 10.4 + 0.02 * (i % 3), blk=["卡·离日高"]))
        cards.append(_card(f"6000{i}3", d, 10.45, lit=True))
        cards.append(_card(f"6000{i}4", d, 10.6 + 0.03 * (i % 2), blk=["通道·联动观察"]))
    out = _run(cards)
    hero = _gate(out, "日线下降")
    assert hero["verdict"] == "功臣" and hero["tone"] == "good" and hero["ci_lo"] > 0
    assert hero["saved_n"] == 6 and hero["stable"] == "稳定"
    tape = _gate(out, "卡·离日高")
    assert tape["verdict"] == "随大盘" and tape["vs_released"] <= 0.5
    loss = _gate(out, "通道·联动观察")
    assert loss["verdict"] == "损耗" and loss["miss_n"] == 6 and loss["vs_released"] > 0.5
    s = out["summary"]
    assert s["touched"] == 24 and s["released"]["n"] == 6 and s["blocked"]["n"] == 18
    assert s["exact_n"] == 18 and s["tiers"] == {"实测": 24}
    assert "功臣：日线下降" in out["headline"]
    sens = out["entry_sens"]["rows"]
    assert [r["off"] for r in sens] == [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
    assert sens[0]["delta"] == 0 and sens[0]["n"] == 24 and sens[0]["rel_n"] == 6
    assert all(a["mean"] >= b["mean"] for a, b in zip(sens, sens[1:]))
    assert sens[-1]["delta"] < 0


def test_audit_split_weight_unknown_and_small_sample() -> None:
    cards = [_card("600011", 2, 9.5, blk=["日线下降", "卡·离日高"]), _card("600012", 2, 10.5, blk=[])]
    out = _run(cards)
    a = _gate(out, "日线下降")
    assert a["eff_n"] == 0.5 and a["sole_n"] == 0 and a["verdict"] == "样本不足"
    assert sum(g["net"] for g in out["gates"]) == pytest.approx(out["summary"]["gates_net"], abs=0.02)
    unk = _gate(out, "未明")
    assert unk["verdict"] == "待查" and out["summary"]["unknown_n"] == 1


def test_audit_strict_drops_early_tier_and_dedupes_channels() -> None:
    path = [(10.3, 10.4, 9.9, 10.2), (10.2, 10.3, 10.1, 10.6), (10.6, 10.7, 10.5, 10.7), (10.7, 10.8, 10.6, 10.8)]
    early = (_sig(3, {"trend_down": True}, code="600021", at="09:45:00"), _packed(3, path))
    blocked_main = _card("600022", 3, 10.5, blk=["日线下降"])
    lit_dragon = _card("600022", 3, 10.5, lit=True, typ="buy_dragon")
    strict = _run([early, blocked_main, lit_dragon])
    assert strict["summary"]["weak_dropped"] == 1 and strict["summary"]["touched"] == 1
    assert strict["summary"]["released"]["n"] == 1 and strict["summary"]["blocked"]["n"] == 0
    loose = _run([early, blocked_main, lit_dragon], strict=False)
    assert loose["summary"]["tiers"].get("早盘") == 1 and loose["summary"]["blocked"]["n"] == 1


def test_atr_shadow_stop_clamps_and_never_tightens() -> None:
    assert atr_shadow_stop(10.0, 9.95, 4.0, False) == pytest.approx(9.7)
    assert atr_shadow_stop(10.0, 9.95, 10.0, False) == pytest.approx(9.5)
    assert atr_shadow_stop(10.0, 9.95, None, False) == pytest.approx(9.8)
    assert atr_shadow_stop(10.0, 9.0, 4.0, False) == pytest.approx(9.0)
    assert atr_shadow_stop(10.0, 10.2, 4.0, False) == pytest.approx(9.7)
    assert atr_shadow_stop(10.0, 9.99, 1.0, True) == pytest.approx(9.9)
    assert atr_shadow_stop(None, 9.9, 4.0, False) is None
    rec = {"items": [
        {"code": "600001", "kind": "stock", "plan_price": 10.0, "stop_price": 9.95},
        {"code": "600002", "kind": "stock", "plan_price": 10.0, "stop_price": 9.95},
    ]}
    attach_atr_shadow_stops(rec, {"600001": 4.0})
    a, b = rec["items"]
    assert a["stop_atr"] == 9.7 and a["atr_pct"] == 4.0 and a["stop_price"] == 9.95
    assert "stop_atr" not in b


def test_audit_stop_compare_live_vs_shadow() -> None:
    path = [(10.1, 10.2, 9.9, 10.0), (10.0, 10.1, 9.9, 10.0), (10.0, 10.3, 10.0, 10.2), (10.2, 10.5, 10.1, 10.4)]
    tight = (_sig(2, {**TRACED, "stop_price": 9.95}, code="600031", ready=1), _packed(2, path))
    rec = (_sig(2, {**TRACED, "stop_price": 9.95, "stop_atr": 9.85}, code="600032", ready=1), _packed(2, path))
    cmp_ = _run([tight, rec])["stop_cmp"]
    live, shadow = cmp_["rows"]
    assert live["label"].startswith("现状") and shadow["label"].startswith("影子")
    assert cmp_["recorded_n"] == 1
    assert live["stop_rate"] == 100.0 and shadow["stop_rate"] == 0.0
    assert shadow["mean"] > live["mean"] and shadow["gap_med"] > live["gap_med"]


def test_bootstrap_ci_deterministic_and_needs_three() -> None:
    pairs = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (-1.0, 0.5)]
    assert bootstrap_ci(pairs) == bootstrap_ci(pairs)
    lo, hi = bootstrap_ci(pairs)
    assert lo <= hi
    assert bootstrap_ci(pairs[:2]) == (None, None)


# ---------- backtest limit constraints / slippage ----------

def test_backtest_buy_skips_sealed_limit_up() -> None:
    bars = {
        "2026-09-16": {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
        "2026-09-17": {"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
        "2026-09-18": {"open": 11.2, "high": 11.6, "low": 10.9, "close": 11.3},
    }
    sim = simulate_buy_fill(trade_date="2026-09-17", bars_by_day=bars, wait=11.5, plan=11.5, chase=None,
                            mode="plan", look_ahead=1, vol_min_ratio=0, slip_pct=0, gap_pct=0)
    assert sim["filled"] and sim["fill_date"] == "2026-09-18" and sim["limit_skips"] == 1
    only = {k: bars[k] for k in ("2026-09-16", "2026-09-17")}
    miss = simulate_buy_fill(trade_date="2026-09-17", bars_by_day=only, wait=11.5, plan=11.5, chase=None,
                             mode="plan", look_ahead=0, vol_min_ratio=0, slip_pct=0, gap_pct=0)
    assert not miss["filled"] and miss["note"] == "一字涨停买不进"


def test_backtest_sell_rolls_past_sealed_limit_down() -> None:
    bars = {
        "2026-09-16": {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0},
        "2026-09-17": {"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0},
        "2026-09-18": {"open": 8.8, "high": 9.1, "low": 8.6, "close": 8.9},
    }
    sim = simulate_sell_fill(trade_date="2026-09-17", bars_by_day=bars, sell=None, stop=9.5,
                             look_ahead=0, slip_pct=0, gap_pct=0)
    assert sim["filled"] and sim["fill_date"] == "2026-09-18" and sim["fill_price"] == 8.8
    assert sim["limit_skips"] == 1 and "一字跌停顺延" in sim["note"]


def test_backtest_prefers_recorded_book_slippage() -> None:
    row = {"payload": {"slip_bps": 32.0}}
    assert _row_buy_slip(row, 0.15, explicit=False) == (0.32, "盘口")
    assert _row_buy_slip(row, 0.4, explicit=True) == (0.4, "手动")
    assert _row_buy_slip({"payload": {}}, 0.15, explicit=False) == (0.15, "默认")
