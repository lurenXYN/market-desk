"""Environment buy-gate score, ETF/stock mults, lit shadow, and feature pack."""

from __future__ import annotations

from market_desk import env_gate as eg
from market_desk import pattern_tags as pt


def _closes(n: int = 80, *, up: bool = True) -> list[tuple[str, float]]:
    from datetime import date, timedelta

    out: list[tuple[str, float]] = []
    px = 1000.0
    day = date(2026, 1, 2)
    for _ in range(n):
        px *= 1.003 if up else 0.997
        out.append((day.isoformat(), px))
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return out


def _ohlc_trend(n: int = 60, *, up: bool = True) -> list[dict]:
    closes = _closes(n, up=up)
    bars = []
    for i, (_, c) in enumerate(closes):
        prev = closes[i - 1][1] if i else c
        o = prev
        h = max(o, c) * 1.008
        l = min(o, c) * 0.992
        bars.append({"open": o, "high": h, "low": l, "close": c, "volume": 1e6 + i * 1e4})
    return bars


def test_friendly_above_ma_loosens_stock_more_than_etf():
    closes = _closes(90, up=True)
    regime = {
        "ok": True,
        "side": "above",
        "gap_pct": 3.0,
        "asof": closes[-1][0],
    }
    out = eg.build_env_gate(regime=regime, closes=closes, metrics={"hs300_pct": 0.2}, vol="low")
    assert out["ok"]
    assert out["bucket"] in ("friendly", "neutral")
    stock = out["mult"]["stock"]["minute"]
    etf = out["mult"]["etf"]["minute"]
    assert stock <= 1.0
    # ETF deviation from 1.0 is smaller or equal.
    assert abs(etf - 1.0) <= abs(stock - 1.0) + 1e-9


def test_hostile_below_ma_tightens():
    closes = _closes(90, up=False)
    regime = {"ok": True, "side": "below", "gap_pct": -4.0, "asof": closes[-1][0]}
    out = eg.build_env_gate(regime=regime, closes=closes, metrics={"big_drop": True}, vol="high")
    assert out["bucket"] == "hostile"
    assert out["mult"]["stock"]["off_high"] >= 1.0
    assert out["mult"]["etf"]["thin"] == 1.0


def test_freeze_kind_slice():
    snap = {
        "env_gate": eg.build_env_gate(
            regime={"ok": True, "side": "above", "gap_pct": 1.0, "asof": "2026-11-02"},
            closes=_closes(80, up=True),
            vol="low",
            asof="2026-11-02",
        )
    }
    frozen = eg.freeze_env_gate(snap, kind="etf")
    assert frozen and frozen["kind"] == "etf"
    assert frozen["bucket"] in eg.BUCKETS
    assert "minute" in frozen["mult"]


def test_live_mult_off_by_default(monkeypatch):
    monkeypatch.setattr(
        "market_desk.settings.setting",
        lambda key, default=None: False if key == "env_gate_live" else default,
    )
    assert eg.env_gate_mult_for_key("minute", kind="stock") == 1.0


def test_live_keys_minute_only(monkeypatch):
    monkeypatch.setattr(
        "market_desk.settings.setting",
        lambda key, default=None: {
            "env_gate_live": True,
            "env_gate_live_keys": "minute",
        }.get(key, default),
    )
    from market_desk.adapt import context as ctx

    ctx._ADAPT_CACHE["env_gate"] = {
        "ok": True,
        "bucket": "hostile",
        "mult": {
            "stock": {"minute": 1.1, "off_high": 1.1, "thin": 1.1, "rel_strength": 1.1},
            "etf": {"minute": 1.05, "off_high": 1.05, "thin": 1.0, "rel_strength": 1.0},
        },
    }
    assert eg.env_gate_mult_for_key("minute", kind="stock") == 1.1
    assert eg.env_gate_mult_for_key("off_high", kind="stock") == 1.0


def test_features_tags_present():
    closes = [1000.0 * (1.001 ** i) for i in range(80)]
    bars = _ohlc_trend(80, up=True)
    feats = eg.collect_features(closes=list(enumerate(closes)), bars=bars, metrics={"hs300_pct": 0.5})
    assert "parts" in feats
    assert "adx" in feats["parts"]
    assert "boll" in feats["parts"]
    assert "volume" in feats["parts"]
    assert "gap_kline" in feats["parts"]
    assert feats["parts"]["dow"]["ok"] or feats["bars"] >= 20


def test_pattern_gap_breakaway():
    gap = pt.classify_gap(open_px=110.0, prev_close=100.0, prev_high=105.0, prev_low=98.0)
    assert gap["ok"]
    assert gap["kind"] == "突破缺口"
    assert "向上突破缺口" in gap["tags"]


def test_pattern_engulfing():
    bars = [
        {"open": 10.0, "high": 10.2, "low": 9.5, "close": 9.6},
        {"open": 9.5, "high": 10.5, "low": 9.4, "close": 10.4},
    ]
    out = pt.detect_kline_tags(bars)
    assert "阳包阴" in out["tags"]


def test_env_lit_shadow_recommend():
    rows = []
    for i in range(12):
        day = f"2026-11-{i + 1:02d}"
        for bucket, lit in (("friendly", True), ("hostile", False)):
            rows.append(
                {
                    "trade_date": day,
                    "kind": "stock",
                    "ready": 1 if lit else 0,
                    "skipped": 0,
                    "payload": {
                        "ever_ready": lit,
                        "env_gate": {"bucket": bucket, "kind": "stock"},
                    },
                    "outcome_day3_pct": 1.0 if lit else -1.0,
                    "excess_d3_pct": 0.5 if lit else -0.5,
                    "signal_type": "buy",
                }
            )
    lit = eg.build_env_lit_shadow(rows, since="2026-11-01", min_days=10)
    assert lit["enough_days"]
    assert lit["live_recommend"]
    assert lit["live_keys_suggest"] == ["minute"] or "minute" in lit["live_keys_suggest"]
