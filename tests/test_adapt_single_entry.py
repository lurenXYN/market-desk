"""Size composition applies one adjustment; mainline score is normalised."""

from __future__ import annotations

import pytest

from market_desk.adapt.sizing import compose_size_mult
from market_desk.mainline import mainline_score


def test_only_strongest_factor_applies():
    out = compose_size_mult(
        [{"key": "a", "label": "A", "mult": 0.9}, {"key": "b", "label": "B", "mult": 0.75}]
    )
    assert out["size_mult"] == 0.75
    assert out["raw_product"] == pytest.approx(0.675)
    assert out["applied"] == "b"
    assert "a" in out["suppressed"]


def test_single_factor_unchanged():
    out = compose_size_mult([{"key": "a", "label": "A", "mult": 0.8}])
    assert out["size_mult"] == 0.8
    assert out["applied"] == "a"


def test_mainline_breadth_cannot_dominate_alone():
    mega = {"status": "确认中", "zt_n": 40, "pct": 0.2, "hist": []}
    strong = {"status": "确认中", "zt_n": 6, "pct": 5.5, "hist": [{"zt_n": 3, "pct": 2.0}] * 3}
    # 广度仍有优势，但不再由单一原始计数拉开到 90 分级别落差
    assert mainline_score(mega) - mainline_score(strong) < 40
    assert mainline_score(mega) <= 100
