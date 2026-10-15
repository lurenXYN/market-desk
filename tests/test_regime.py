"""Soft market ruler: CSI1000 vs MA60 classify, freeze, edge-shadow split."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.edge_shadow import build_edge_shadow
from market_desk.regime import (
    annotate_recommend_regime,
    build_regime_shadow_rows,
    classify_bench_regime,
    freeze_market_regime,
    regime_card_tag,
    regime_side_for_row,
)
from market_desk.review.record import record_session_signals


def _closes(n: int = 70, *, last: float = 100.0, step: float = 0.1) -> list[tuple[str, float]]:
    """Synthetic daily closes ending at ``last`` (oldest first)."""
    out: list[tuple[str, float]] = []
    # Walk upward so MA60 sits below last when step>0.
    start = last - step * (n - 1)
    for i in range(n):
        day = f"2026-07-{(i % 28) + 1:02d}" if i < 28 else f"2026-08-{((i - 28) % 28) + 1:02d}"
        if i >= 56:
            day = f"2026-09-{((i - 56) % 28) + 1:02d}"
        out.append((day, round(start + step * i, 4)))
    out[-1] = ("2026-10-15", last)
    return out


def test_classify_above_and_below():
    above = classify_bench_regime(_closes(70, last=110.0, step=0.2), asof="2026-10-15")
    assert above["ok"] and above["side"] == "above"
    assert "主线回踩" in (above.get("hint") or "")
    assert above.get("short_hint") == "主线回踩为主"
    # Flat then drop last bar below MA.
    flat = [(f"2026-07-{(i % 28) + 1:02d}", 100.0) for i in range(59)]
    flat.append(("2026-10-15", 90.0))
    below = classify_bench_regime(flat, asof="2026-10-15")
    assert below["ok"] and below["side"] == "below"
    assert "日线降" in (below.get("hint") or "")
    assert "少做主题回踩" in (below.get("short_hint") or "")


def test_classify_unknown_when_short():
    got = classify_bench_regime([("2026-10-15", 100.0)] * 10)
    assert not got["ok"] and got["side"] == "unknown"
    assert got.get("short_hint") == "日线不足"


def test_freeze_and_regime_side_prefers_payload():
    snap = {
        "market_regime": {
            "ok": True, "side": "below", "asof": "2026-10-15",
            "close": 90.0, "ma": 100.0, "ma_n": 60, "label": "中证1000", "gap_pct": -10.0,
        }
    }
    frozen = freeze_market_regime(snap)
    assert frozen and frozen["side"] == "below"
    row = {"trade_date": "2026-10-15", "payload": {"market_regime": frozen}}
    assert regime_side_for_row(row, _closes(70, last=110.0)) == "below"


def test_regime_shadow_rows_split_lit_and_weak_subsets():
    rows = [
        {
            "trade_date": "2026-10-15", "code": "1", "outcome_day3_pct": 3.0,
            "ready": 1, "payload": {"ever_ready": True, "market_regime": {"side": "above"}},
        },
        {
            "trade_date": "2026-10-15", "code": "2", "outcome_day3_pct": -1.0,
            "ready": 1, "payload": {"ever_ready": True, "market_regime": {"side": "below"}},
        },
        {
            "trade_date": "2026-10-15", "code": "3", "outcome_day3_pct": 2.0,
            "trend_down": True,
            "payload": {"market_regime": {"side": "below"}, "trend_down": True},
        },
        {
            "trade_date": "2026-10-15", "code": "4", "outcome_day3_pct": -4.0,
            "desk_source": "dragon",
            "payload": {"market_regime": {"side": "below"}},
        },
        {
            "trade_date": "2026-10-08", "code": "5", "outcome_day3_pct": 9.0,
            "ready": 1, "payload": {"ever_ready": True, "market_regime": {"side": "above"}},
        },
    ]
    got = build_regime_shadow_rows(rows, None, since="2026-10-15")
    by = {r["key"]: r for r in got}
    assert by["尺上·亮过灯"]["n"] == 1 and by["尺上·亮过灯"]["d3"] == 3.0
    assert by["尺下·亮过灯"]["n"] == 1 and by["尺下·亮过灯"]["d3"] == -1.0
    assert by["尺下·日线降"]["n"] == 1 and by["尺下·日线降"]["d3"] == 2.0
    assert by["尺下·龙头"]["n"] == 1 and by["尺下·龙头"]["d3"] == -4.0
    assert by["Δ 尺上−尺下（亮过灯）"]["d3"] == 4.0
    assert by["Δ 尺下日线降−亮过灯"]["d3"] == 3.0


def test_regime_card_tags_only_when_below():
    below = {"ok": True, "side": "below"}
    above = {"ok": True, "side": "above"}
    assert regime_card_tag({"trend_down": True}, below)["label"] == "弱市位置型"
    assert regime_card_tag({"ready": True}, below)["label"] == "弱市慎追"
    assert regime_card_tag({"desk_source": "dragon"}, below)["label"] == "弱市慎追"
    assert regime_card_tag({"trend_down": True}, above) is None
    rec = annotate_recommend_regime(
        {"items": [{"code": "1", "trend_down": True}, {"code": "2", "ready": True}]},
        below,
    )
    assert rec["items"][0]["regime_tag"]["label"] == "弱市位置型"
    assert rec["items"][1]["regime_tag"]["label"] == "弱市慎追"


def test_build_edge_shadow_includes_regime_key():
    rows = [
        {
            "trade_date": "2026-10-15", "signaled_at": "2026-10-15 09:40:00",
            "signal_type": "buy", "code": "600001", "price": 10.0,
            "outcome_day3_pct": 1.0, "skipped": 0, "ready": 1,
            "payload": {"plan_price": 10.0, "ever_ready": True,
                        "market_regime": {"side": "above"}},
        },
    ]
    out = build_edge_shadow(rows)
    assert out["ok"] and "regime" in out and out["regime_since"] == "2026-10-15"
    assert any(r["side"] == "above" for r in out["regime"])


def test_market_regime_frozen_on_first_insert(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    snap = {
        "trade_date": "2026-10-15",
        "updated_at": "2026-10-15 10:05:00",
        "phase": "发酵",
        "market_regime": {
            "ok": True, "side": "above", "asof": "2026-10-15",
            "close": 110.0, "ma": 100.0, "ma_n": 60, "label": "中证1000", "gap_pct": 10.0,
        },
        "verdict": {
            "action": "观察回踩",
            "mainline": {"name": "电子"},
            "recommend": {
                "items": [
                    {"code": "600001", "name": "A", "plan_price": 10.0, "last": 9.7},
                ]
            },
        },
    }
    assert record_session_signals(snap) == 1
    p = desk_db.load_signals_for_date("2026-10-15")[0]["payload"]
    assert p["market_regime"]["side"] == "above"
    # Later refresh with flipped side must not overwrite the freeze.
    snap2 = dict(snap)
    snap2["updated_at"] = "2026-10-15 14:00:00"
    snap2["market_regime"] = {
        "ok": True, "side": "below", "asof": "2026-10-15",
        "close": 90.0, "ma": 100.0, "ma_n": 60, "label": "中证1000", "gap_pct": -10.0,
    }
    snap2["verdict"] = {
        "action": "观察回踩",
        "mainline": {"name": "电子"},
        "recommend": {
            "items": [
                {"code": "600001", "name": "A", "plan_price": 10.0, "last": 9.5},
            ]
        },
    }
    assert record_session_signals(snap2) == 1
    p2 = desk_db.load_signals_for_date("2026-10-15")[0]["payload"]
    assert p2["market_regime"]["side"] == "above"
