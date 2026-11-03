"""NP (no-pullback run-up) observation: freeze, cards, paired stats, gate."""

from __future__ import annotations

from typing import Any

from market_desk import np_review as npr
from market_desk.config import NP_GATE_MIN_DAYS, NP_GATE_MIN_N


def test_np_from_bar_flags():
    assert npr.np_from_bar(10.0, 10.2, 10.5)["np"] is True
    # Low touched the plan → TP even when it closed higher.
    assert npr.np_from_bar(10.0, 10.0, 10.5)["np"] is False
    # Closed below plan → not a run-up.
    assert npr.np_from_bar(10.0, 10.1, 9.9)["np"] is False
    assert npr.np_from_bar(None, 10.1, 10.5) is None


def test_freeze_np_values_only_shared_buys_once():
    rows = [
        {"id": 1, "code": "600000", "trade_date": "2026-11-02", "signal_type": "buy",
         "price": 10.0, "payload": {}},
        {"id": 2, "code": "600000", "trade_date": "2026-11-02", "signal_type": "buy",
         "price": 10.0, "owner_user_id": 7, "payload": {}},
        {"id": 3, "code": "600000", "trade_date": "2026-11-02", "signal_type": "buy",
         "price": 10.0, "payload": {"np": {"np": False}}},
        {"id": 4, "code": "600000", "trade_date": "2026-11-03", "signal_type": "buy",
         "price": 10.0, "payload": {}},
    ]
    packed = {
        "600000": (
            ["2026-11-02", "2026-11-03"],
            [10.6, 10.7],
            {"low": [10.2, 10.3], "high": [10.8, 10.9]},
        )
    }
    out = npr.freeze_np_values(rows, packed, today="2026-11-03")
    assert list(out) == [1]  # owner row, already-frozen row and same-day row skipped
    assert out[1][0] == "np" and out[1][1]["np"] is True


def _row(day: str, code: str, *, np: bool, d3: float, first_last: float = 10.1, **payload: Any):
    p = {
        "np": {"np": np, "day_close": 10.3, "day_low": 10.2, "plan": 10.0},
        "first_last": first_last,
        "board_stage": "main",
        "desk_source": "dragon",
        "trend_ok": True,
        **payload,
    }
    return {
        "trade_date": day,
        "code": code,
        "signal_type": "buy",
        "price": 10.0,
        "last": first_last,
        "signaled_at": f"{day} 09:40:00",
        "outcome_day3_pct": d3,
        "outcome_label": "ok",
        "outcome_checked_at": "2099-01-01 15:06:00",
        "payload": p,
    }


def _bench(days: list[str]) -> list[tuple[str, float]]:
    """Flat index with one extra settled session per signal day (bench = 0%)."""
    out = []
    for i, d in enumerate(days):
        out.append((d, 100.0))
    return out


def test_build_np_cards_excess_and_dims(monkeypatch):
    monkeypatch.setattr("market_desk.review.bench.forward_session_ready", lambda d, **k: True)
    days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    bench = _bench(days)
    rows = [_row("2026-11-02", "600001", np=True, d3=6.0)]
    cards, cov = npr.build_np_cards(rows, bench)
    assert cov == {"buys": 1, "with_np": 1, "scored": 1}
    c = cards[0]
    assert c["np"] is True
    # close3 = 10.0*1.06 = 10.6 ; E1 = 10.6/10.1 − 1 = 4.95% ; E2 = 10.6/10.3 − 1 = 2.91%
    assert abs(c["xs_e1"] - 4.95) < 0.01
    assert abs(c["xs_e2"] - 2.91) < 0.01
    assert c["dims"]["gap"] == "gap0-1" or c["dims"]["gap"] == "gap1-3"  # +1.0% sits on the edge
    assert npr.gap_bucket(0.5) == "gap0-1"
    assert npr.gap_bucket(-0.2) == "gap<=0"
    assert npr.gap_bucket(2.0) == "gap1-3"
    assert npr.gap_bucket(3.5) == "gap>3"
    assert c["dims"]["time"] == "<10:00"
    assert c["dims"]["trend"] == "up"


def _cards(n_days: int, diff: float, per_day: int = 3):
    cards = []
    for i in range(n_days):
        day = f"2026-12-{i + 1:02d}"
        for j in range(per_day):
            for is_np, base in ((True, diff), (False, 0.0)):
                cards.append(
                    {
                        "day": day,
                        "code": f"{j}{int(is_np)}",
                        "np": is_np,
                        "xs_e1": base + ((0.1 if i % 2 else -0.1) if is_np else 0.0),
                        "xs_e2": base,
                        "dims": {"gap": "gap0-1", "time": "<10:00", "stage": "main",
                                 "regime": "above", "deep_v": "normal",
                                 "source": "dragon", "trend": "up"},
                    }
                )
    return cards


def test_paired_and_verdict_gate(monkeypatch):
    monkeypatch.setattr("market_desk.config.NP_SHADOW_SINCE", "2026-11-04")
    strong = npr._block(_cards(NP_GATE_MIN_DAYS + 2, diff=1.5, per_day=NP_GATE_MIN_N // 4 + 1))
    assert strong["np_n"] >= NP_GATE_MIN_N
    assert abs(strong["e1"]["diff"] - 1.5) < 0.2
    assert strong["e1"]["halves_same_sign"] is True
    assert npr.np_verdict(strong)["verdict"] == "soft_candidate"

    thin = npr._block(_cards(3, diff=1.5))
    assert npr.np_verdict(thin)["verdict"] == "insufficient"

    weak = npr._block(_cards(NP_GATE_MIN_DAYS + 2, diff=0.1, per_day=NP_GATE_MIN_N // 4 + 1))
    assert npr.np_verdict(weak)["verdict"] == "hold"


def test_build_np_review_empty():
    out = npr.build_np_review([], bench_closes=[])
    assert out["ok"] is False
    assert out["verdict"] == "insufficient"
    assert out["since"] == "2026-11-04"
