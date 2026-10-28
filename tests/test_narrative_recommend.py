"""Narrative shadow soft-recommend cards."""

from __future__ import annotations

from market_desk.radar.narrative import build_narrative_recommend


def test_build_narrative_recommend_from_hidden_cluster():
    clusters = [
        {
            "label": "储能概念",
            "hidden": True,
            "zt_n": 12,
            "industries": 10,
            "leader_rows": [
                {"code": "300750", "name": "宁德时代", "last": 180.0, "pct": 3.0},
                {"code": "002594", "name": "比亚迪", "last": 260.0, "pct": 2.0},
            ],
        },
        {
            "label": "虚拟现实",
            "hidden": False,
            "zt_n": 18,
            "leader_rows": [
                {"code": "000001", "name": "主线股", "last": 10.0},
            ],
        },
    ]
    rec = build_narrative_recommend(clusters, max_items=4)
    assert rec and rec["buy"] is False
    assert len(rec["items"]) == 2
    assert rec["items"][0]["code"] == "300750"
    assert rec["items"][0]["ready"] is False
    assert "储能" in rec["items"][0]["role_label"]
    # Skip already-recommended codes.
    rec2 = build_narrative_recommend(clusters, skip_codes={"300750"}, max_items=4)
    assert len(rec2["items"]) == 1
    assert rec2["items"][0]["code"] == "002594"
