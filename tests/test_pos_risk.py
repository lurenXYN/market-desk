"""Tests for position theme / single-name concentration in risk overview."""

from __future__ import annotations

from market_desk.config import POSITION_MAX_SINGLE_PCT, POSITION_MAX_THEME_PCT
from market_desk.verdict import build_risk_overview


def test_theme_concentration_over_threshold() -> None:
    rows = [
        {
            "code": "600000",
            "name": "A",
            "qty": 100,
            "cost": 10000,
            "market": 60000,
            "pnl_pct": 1.0,
            "board": "通信设备",
            "entry_board": "通信设备",
        },
        {
            "code": "600001",
            "name": "B",
            "qty": 100,
            "cost": 5000,
            "market": 20000,
            "pnl_pct": -1.0,
            "board": "通信线缆",
            "entry_board": "通信线缆",
        },
        {
            "code": "600002",
            "name": "C",
            "qty": 100,
            "cost": 5000,
            "market": 20000,
            "pnl_pct": 0.5,
            "board": "白酒",
            "entry_board": "白酒",
        },
    ]
    risk = build_risk_overview(rows)
    assert risk["themes"]
    top = risk["top_theme"]
    assert top is not None
    # 通信 siblings should merge; 80k / 100k = 80%
    assert float(top["weight_pct"]) >= 70
    assert top["over_theme"] is True or float(top["weight_pct"]) >= POSITION_MAX_THEME_PCT
    assert any("题材" in t for t in (risk.get("tips") or []))
    assert "max_theme_pct" in (risk.get("caps") or {})


def test_single_name_flag() -> None:
    rows = [
        {
            "code": "600000",
            "name": "重仓票",
            "qty": 100,
            "cost": 10000,
            "market": 80000,
            "pnl_pct": 2.0,
            "board": "光伏",
        },
        {
            "code": "600001",
            "name": "轻仓",
            "qty": 100,
            "cost": 2000,
            "market": 20000,
            "pnl_pct": 0.0,
            "board": "银行",
        },
    ]
    risk = build_risk_overview(rows)
    top = risk["top_single"]
    assert top is not None
    assert float(top["weight_pct"]) >= POSITION_MAX_SINGLE_PCT
    assert top["over_weight"] is True
