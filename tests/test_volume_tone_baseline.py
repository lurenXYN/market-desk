"""量比 label must be judged against the same clock time, not a flat 1.5."""

from __future__ import annotations

import pytest

from market_desk.volume_pace import expected_vol_ratio
from market_desk.volume_tone import classify_vol_ratio


def test_baseline_reclassifies_an_opening_print():
    # 09:46 measured on 2026-11-27: share 0.209 over 16 minutes -> ~3.14 typical.
    base = expected_vol_ratio(0.209, 16, "prev")
    assert base == pytest.approx(3.13, abs=0.02)

    hot = classify_vol_ratio(9.10, baseline=base)
    assert hot["tone"] == "expand" and hot["label"] == f"放量 {9.1 / base:.2f}x"
    assert hot["ratio"] == 9.1 and hot["rel"] == pytest.approx(9.1 / base, abs=0.01)
    assert "原始量比 9.10x" in hot["title"] and f"{base:.2f}x" in hot["title"]

    quiet = classify_vol_ratio(1.31, baseline=base)
    assert quiet["tone"] == "shrink" and quiet["label"] == f"缩量 {1.31 / base:.2f}x"

    flat = classify_vol_ratio(3.45, baseline=base)
    assert flat["tone"] == "flat" and flat["label"] == f"平量 {3.45 / base:.2f}x"


def test_without_a_baseline_the_raw_ratio_is_used():
    out = classify_vol_ratio(1.9)
    assert out["tone"] == "expand" and out["label"] == "放量 1.90x"
    assert out["baseline"] is None and out["rel"] == 1.9


def test_missing_ratio_stays_empty():
    assert classify_vol_ratio(None) == {}
    assert classify_vol_ratio("") == {}


def test_expected_ratio_needs_a_real_session_shape():
    assert expected_vol_ratio(0.209, 16, "linear") is None
    assert expected_vol_ratio(0.209, 4, "prev") is None
    assert expected_vol_ratio(None, 16, "prev") is None
    # Clamped so a first-minute print cannot produce an absurd baseline.
    assert expected_vol_ratio(0.02, 6, "prev") == pytest.approx(0.8)
    assert expected_vol_ratio(0.9, 5, "prev") == pytest.approx(8.0)
