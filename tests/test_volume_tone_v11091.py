"""Unit tests for 量能旁注 classify_vol_ratio (v1.10.91)."""

from __future__ import annotations

from market_desk.volume_tone import classify_vol_ratio


def test_missing_ratio() -> None:
    assert classify_vol_ratio(None) == {}
    assert classify_vol_ratio("") == {}


def test_shrink_flat_expand() -> None:
    shrink = classify_vol_ratio(0.72)
    assert shrink["tone"] == "shrink"
    assert shrink["label"].startswith("缩量")
    assert shrink["ratio"] == 0.72

    flat = classify_vol_ratio(1.07)
    assert flat["tone"] == "flat"
    assert flat["label"].startswith("平量")

    expand = classify_vol_ratio(1.8)
    assert expand["tone"] == "expand"
    assert expand["label"].startswith("放量")


def test_boundaries() -> None:
    assert classify_vol_ratio(0.8)["tone"] == "flat"
    assert classify_vol_ratio(1.5)["tone"] == "flat"
    assert classify_vol_ratio(0.799)["tone"] == "shrink"
    assert classify_vol_ratio(1.501)["tone"] == "expand"
