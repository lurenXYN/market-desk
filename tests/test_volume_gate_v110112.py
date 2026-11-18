"""Thin-volume gate must only fire on a formed volume sample."""

from __future__ import annotations

from market_desk.verdict.gates import apply_market_gates


def _gates(metrics: dict) -> tuple[str, list[str], str]:
    act, _why, hint, notes = apply_market_gates(
        "可买入",
        "测试",
        "",
        metrics=metrics,
        auction={},
        phase="发酵",
        segment_key="morning",
    )
    return act, notes, hint


def test_unformed_volume_does_not_downgrade_buy():
    act, notes, hint = _gates({"volume_ready": False, "thin_volume": False})
    assert act == "可买入"
    assert "量能未走完" in notes
    assert "缩量闸门" not in notes
    assert "缩量闸门暂缓" in hint


def test_thin_volume_still_downgrades_on_a_formed_sample():
    act, notes, hint = _gates({"volume_ready": True, "thin_volume": True, "amount_pctile": 12.0})
    assert act == "观察回踩"
    assert "缩量闸门" in notes
