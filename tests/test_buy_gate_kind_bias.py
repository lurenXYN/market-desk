"""Buy-gate false/true kill learning split by ETF vs stock."""

from __future__ import annotations

from market_desk.review.bias import (
    build_buy_gate_bias,
    build_buy_gate_bias_for_kind,
    resolve_buy_kind_bias,
)
from market_desk.review.hit_rates import build_gate_kill_stats


def _kill_row(*, kind: str, false_kill: bool, gate: str = "分时未确认") -> dict:
    return {
        "signal_type": "buy_main",
        "kind": kind,
        "skipped": 0,
        "ready": 0,
        "outcome_label": "三日红" if false_kill else "三日绿",
        "payload": {"confirm_fail_hist": [gate]},
    }


def test_gate_kill_stats_kind_filter():
    rows = [
        _kill_row(kind="etf", false_kill=True),
        _kill_row(kind="etf", false_kill=True),
        _kill_row(kind="stock", false_kill=False),
        _kill_row(kind="stock", false_kill=False),
        _kill_row(kind="stock", false_kill=False),
    ]
    etf = build_gate_kill_stats(rows, kind="etf")
    stock = build_gate_kill_stats(rows, kind="stock")
    assert etf and etf[0]["false_kill_n"] == 2
    assert stock and stock[0]["true_kill_n"] == 3


def test_buy_gate_bias_bundle_kind_slices(monkeypatch):
    rows = []
    # Enough false kills on ETF minute to loosen; true kills on stock to tighten.
    for _ in range(8):
        rows.append(_kill_row(kind="etf", false_kill=True))
    for _ in range(8):
        rows.append(_kill_row(kind="stock", false_kill=False))

    monkeypatch.setattr(
        "market_desk.config.BUY_GATE_FALSE_KILL_MIN",
        3,
    )
    monkeypatch.setattr("market_desk.config.BUY_GATE_TRUE_KILL_MIN", 3)
    monkeypatch.setattr("market_desk.config.BUY_GATE_KILL_MIN", 3)

    bundle = build_buy_gate_bias(rows)
    assert bundle["schema"] == "buy_gate_bias.v2"
    assert bundle["etf"].get("loosen") is True
    assert "minute" in (bundle["etf"].get("gates") or {})
    assert bundle["stock"].get("tighten") is True
    assert float((bundle["stock"].get("gates") or {}).get("minute", {}).get("mult") or 1.0) > 1.0

    etf_row = resolve_buy_kind_bias(bundle, kind="etf")
    assert float((etf_row.get("gates") or {}).get("minute", {}).get("mult") or 1.0) < 1.0
    # stock tighten vs all-sample loosen on same gate → per-gate damp
    stock_row = resolve_buy_kind_bias(bundle, kind="stock")
    assert "minute" not in (stock_row.get("gates") or {})
    assert "冲突" in str(stock_row.get("note") or "")


def test_resolve_conflict_damps_per_gate():
    bundle = {
        "all": {
            "ok": True,
            "loosen": False,
            "tighten": True,
            "mult": 1.05,
            "gates": {"minute": {"mult": 1.05, "mode": "tighten"}},
        },
        "etf": {
            "ok": True,
            "loosen": True,
            "tighten": False,
            "mult": 0.9,
            "gates": {
                "minute": {"mult": 0.9, "mode": "loosen"},
                "thin": {"mult": 0.9, "mode": "loosen"},
            },
        },
        "stock": {"ok": False},
    }
    row = resolve_buy_kind_bias(bundle, kind="etf")
    # minute conflicts with all → dropped; thin kept.
    assert "minute" not in (row.get("gates") or {})
    assert "thin" in (row.get("gates") or {})
    assert "冲突" in str(row.get("note") or "")


def test_for_kind_empty_ok():
    out = build_buy_gate_bias_for_kind([], kind="etf")
    assert out["ok"]
    assert out["gates"] == {}
