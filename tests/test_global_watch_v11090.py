"""Unit tests for 外盘脉搏 quote parse + tone labels (v1.10.90)."""

from __future__ import annotations

from market_desk.tencent import (
    build_global_watch_payload,
    global_tone_label,
    parse_global_quote_chunk,
)


def test_parse_index_tilde_body() -> None:
    body = (
        "200~纳斯达克~.IXIC~25253.14~24579.42~24777.44~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~~"
        "2026-11-09 17:15:59~673.72~2.74~25354.30~24762.57~USD~0~0~~~~~~2.41~~~Nasdaq"
    )
    row = parse_global_quote_chunk("usIXIC", body)
    assert row is not None
    assert row["price"] == 25253.14
    assert row["pct"] == 2.74


def test_parse_hf_comma_body() -> None:
    body = (
        "4080.25,-0.83,4080.60,4080.80,4156.20,4076.20,19:47:05,4114.50,4149.00,"
        "0,1,2,2026-11-10,纽约黄金"
    )
    row = parse_global_quote_chunk("hf_GC", body)
    assert row is not None
    assert row["price"] == 4080.25
    assert row["pct"] == -0.83
    assert "黄金" in (row.get("name_raw") or "")


def test_parse_empty_returns_none() -> None:
    assert parse_global_quote_chunk("hf_NQ", "") is None
    assert parse_global_quote_chunk("jpN225", "   ") is None


def test_tone_thresholds() -> None:
    assert global_tone_label([]) == "—"
    assert global_tone_label([0.6, 0.8]) == "偏强"
    assert global_tone_label([-0.6]) == "偏弱"
    assert global_tone_label([0.1, -0.1, 0.0]) == "平淡"


def test_build_payload_buckets() -> None:
    payload = build_global_watch_payload(
        [
            {"name": "恒生", "group": "index", "tone_bucket": "asia", "pct": 0.8},
            {"name": "黄金", "group": "commodity", "tone_bucket": "commodity", "pct": -1.2},
            {"name": "标普期货", "group": "future", "tone_bucket": "us_fut", "pct": 0.1},
            {"name": "道指", "group": "index", "tone_bucket": None, "pct": 2.0},
        ]
    )
    tone = payload["tone"]
    assert tone["asia"] == "偏强"
    assert tone["commodity"] == "偏弱"
    assert tone["us_fut"] == "平淡"
    assert len(payload["items"]) == 4
