"""Pullback sweet zone must come from win-rate by depth band, not winners' p80."""

from __future__ import annotations

import market_desk.adapt.tune as tune


def _row(depth: float, win: bool, i: int) -> dict:
    return {
        "trade_date": f"2026-10-{10 + (i % 18):02d}",
        "signal_type": "buy",
        "traded": 1,
        "outcome_mae_pct": -abs(depth),
        "outcome_label": "次日红" if win else "次日绿",
    }


def _rows():
    rows = []
    # 1-2% 回撤档：20 笔里 16 笔赚（80%），其余档位弱
    for i in range(20):
        rows.append(_row(1.4, i < 16, i))
    for i in range(18):
        rows.append(_row(3.2, i < 6, i))
    for i in range(14):
        rows.append(_row(6.5, i < 4, i))
    return rows


def test_best_layer_wins_and_returns_band(monkeypatch):
    monkeypatch.setattr(tune, "_buy_scored", lambda rows: rows)
    out = tune.build_pullback_sweet(_rows(), use_cache=False)
    assert out["ok"] is True
    assert out["best_layer"]["lo"] == 1.0 and out["best_layer"]["hi"] == 2.0
    assert out["best_layer"]["win_rate"] == 80.0
    # 甜区取该档上沿 2.0%，不超过默认 4.2%
    assert out["sweet_max"] <= 4.2
    assert out["layers"]


def test_no_layer_advantage_keeps_defaults(monkeypatch):
    monkeypatch.setattr(tune, "_buy_scored", lambda rows: rows)
    rows = [_row(1.4, i % 2 == 0, i) for i in range(20)] + [_row(4.0, i % 2 == 0, i) for i in range(20)]
    out = tune.build_pullback_sweet(rows, use_cache=False)
    assert out["ok"] is False
    assert "无显著优势" in out["note"]
    assert out["sweet_max"] == 4.2


def test_insufficient_sample_keeps_defaults(monkeypatch):
    monkeypatch.setattr(tune, "_buy_scored", lambda rows: rows)
    out = tune.build_pullback_sweet([_row(1.5, True, 0)], use_cache=False)
    assert out["ok"] is False
    assert "样本不足" in out["note"]
