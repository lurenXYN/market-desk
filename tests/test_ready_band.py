"""Unit tests for band-ready relax."""

from __future__ import annotations

from market_desk.verdict import apply_band_ready_relax, apply_mainline_probe


def test_band_ready_promotes_near_entry(monkeypatch) -> None:
    from market_desk import settings as settings_mod

    monkeypatch.setattr(settings_mod, "setting", lambda k, d=None: "band" if k == "ready_style" else d)
    rec = {
        "items": [
            {
                "code": "600000",
                "kind": "stock",
                "near_entry": True,
                "ready": False,
                "last": 10.0,
                "chase_price": 10.8,
                "confirm_fail": ["分时贴近近期高点"],
                "qty": 200,
                "reason": "主线",
            }
        ]
    }
    probed = apply_mainline_probe(rec)
    out = apply_band_ready_relax(probed)
    item = out["items"][0]
    assert item.get("ready") is True
    assert item.get("ready_relaxed") is True
    assert int(item.get("qty") or 0) == 100


def test_band_ready_skips_when_at_chase(monkeypatch) -> None:
    from market_desk import settings as settings_mod

    monkeypatch.setattr(settings_mod, "setting", lambda k, d=None: "band" if k == "ready_style" else d)
    rec = {
        "items": [
            {
                "code": "600000",
                "near_entry": True,
                "ready": False,
                "last": 10.9,
                "chase_price": 10.8,
                "confirm_fail": [],
                "qty": 200,
            }
        ]
    }
    out = apply_band_ready_relax(rec)
    assert out["items"][0].get("ready") is not True
