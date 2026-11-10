"""Review flatten exposes defense_tier / news_hit for table columns."""

from __future__ import annotations

from market_desk.review.outcome import _flatten_signal_prices


def test_flatten_defense_and_news_hit():
    row = _flatten_signal_prices(
        {
            "signal_type": "buy",
            "payload": {
                "defense": {"tier": "D2", "label": "防守"},
                "news_radar": {"status": "online", "hit": True, "confirm": "resonance"},
            },
        }
    )
    assert row["defense_tier"] == "D2"
    assert row["news_hit"] is True
    assert row["news_confirm"] == "resonance"


def test_flatten_news_placeholder_is_null_hit():
    row = _flatten_signal_prices(
        {
            "signal_type": "buy",
            "payload": {"news_radar": {"status": "missing", "hit": False}},
        }
    )
    assert row["news_hit"] is None
    assert row["news_status"] == "missing"
