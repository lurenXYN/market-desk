"""Defense tier classification (display/freeze only, no size live)."""

from __future__ import annotations

from market_desk.defense import (
    TIER_D0,
    TIER_D1,
    TIER_D2,
    TIER_D3,
    classify_defense_tier,
    defense_card_tag,
    freeze_defense,
)


def test_classify_d0_d1_d2():
    d0 = classify_defense_tier(
        market_regime={"ok": True, "side": "above"},
        env_gate={"ok": True, "bucket": "friendly"},
        stress={"ok": True, "stress": False},
    )
    assert d0["tier"] == TIER_D0
    assert d0["size_live"] is False

    d1a = classify_defense_tier(
        market_regime={"ok": True, "side": "below"},
        env_gate={"ok": True, "bucket": "neutral"},
        stress={"ok": True, "stress": False},
    )
    assert d1a["tier"] == TIER_D1
    assert "尺下" in d1a["reasons"]

    d1b = classify_defense_tier(
        market_regime={"ok": True, "side": "above"},
        env_gate={"ok": True, "bucket": "hostile"},
        stress={"ok": True, "stress": False},
    )
    assert d1b["tier"] == TIER_D1
    assert "环境敌意" in d1b["reasons"]

    d2 = classify_defense_tier(
        market_regime={"ok": True, "side": "below"},
        env_gate={"ok": True, "bucket": "hostile"},
        stress={"ok": True, "stress": False},
    )
    assert d2["tier"] == TIER_D2
    assert d2["size_mult"] == 0.65


def test_classify_d3_stress_wins():
    d3 = classify_defense_tier(
        market_regime={"ok": True, "side": "above"},
        env_gate={"ok": True, "bucket": "friendly"},
        stress={"ok": True, "stress": True, "loss_streak_hit": True, "loss_streak": 3},
    )
    assert d3["tier"] == TIER_D3
    assert d3["size_mult"] == 0.0


def test_freeze_and_card_tag():
    snap = {
        "defense": classify_defense_tier(
            market_regime={"ok": True, "side": "below"},
            env_gate={"ok": True, "bucket": "hostile"},
            stress={"ok": True, "stress": False},
        )
    }
    frozen = freeze_defense(snap)
    assert frozen is not None
    assert frozen["tier"] == TIER_D2
    assert frozen["size_live"] is False

    tag = defense_card_tag(snap["defense"])
    assert tag and "防守" in tag["label"]

    assert defense_card_tag(
        classify_defense_tier(
            market_regime={"ok": True, "side": "above"},
            env_gate={"ok": True, "bucket": "neutral"},
            stress={"ok": True, "stress": False},
        )
    ) is None
