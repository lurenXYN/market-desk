"""Daily patch verify must point-read the day, not load_daily(N)."""

from __future__ import annotations


def test_apply_verify_does_not_use_recent_window(monkeypatch, tmp_path):
    from market_desk import patches_apply as pa

    patch = {
        "trade_date": "2026-09-21",
        "note": "test",
        "payload": {
            "ups": 2631,
            "downs": 446,
            "amount_yi": 11615.7,
            "phase": "高潮",
        },
    }
    path = tmp_path / "daily-2026-09-21-vtest.json"
    path.write_text(__import__("json").dumps(patch), encoding="utf-8")

    monkeypatch.setattr(pa, "_PATCH_DIR", tmp_path)
    monkeypatch.setattr(pa, "init_db", lambda: None)
    monkeypatch.setattr(pa, "load_setting", lambda key: [])
    saved: list[tuple] = []

    def _save(day, payload, *, merge=True):
        saved.append((day, dict(payload), merge))

    monkeypatch.setattr(pa, "save_daily", _save)
    # Simulate a long history where Sep-21 is outside the recent window.
    monkeypatch.setattr(
        pa,
        "load_daily_day",
        lambda day: {
            "trade_date": day,
            "ups": 2631,
            "downs": 446,
            "amount_yi": 11615.7,
        }
        if day == "2026-09-21"
        else None,
    )
    wrote = []

    def _save_setting(key, val):
        wrote.append((key, val))

    monkeypatch.setattr(pa, "save_setting", _save_setting)

    done = pa.apply_pending_daily_patches(force=True)
    assert done == ["daily-2026-09-21-vtest"]
    assert saved and saved[0][0] == "2026-09-21" and saved[0][2] is False
    assert wrote

    v = pa.verify_day_breadth("2026-09-21")
    assert v.get("ups") == 2631
    assert not v.get("missing")


def test_verify_missing_day(monkeypatch):
    from market_desk import patches_apply as pa

    monkeypatch.setattr(pa, "load_daily_day", lambda day: None)
    assert pa.verify_day_breadth("2099-01-01")["missing"] is True
