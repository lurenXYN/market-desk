"""Personal P&L calendar helpers."""

from __future__ import annotations

from market_desk.personal_stats import build_personal_pnl_calendar


def test_personal_calendar_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        "market_desk.db.load_exec_diary",
        lambda **kwargs: [],
    )
    monkeypatch.setattr("market_desk.db.load_setting", lambda key: None)
    out = build_personal_pnl_calendar(user_id=1, days=10, trade_date="2026-09-18")
    assert out["ok"] is True
    assert out["items"] == []
    assert out["day_win_rate"] is None


def test_personal_calendar_win_rate(monkeypatch) -> None:
    diary = [
        {
            "trade_date": "2026-09-17",
            "side": "buy",
            "code": "600000",
            "name": "测",
            "qty": 100,
            "price": 10,
        },
        {
            "trade_date": "2026-09-18",
            "side": "clear",
            "code": "600000",
            "name": "测",
            "qty": 100,
            "price": 11,
        },
    ]

    def _setting(key: str):
        if key == "eod:2026-09-17":
            return {"day_pnl": -20.0}
        if key == "eod:2026-09-18":
            return {"day_pnl": 50.0}
        return None

    monkeypatch.setattr(
        "market_desk.db.load_exec_diary",
        lambda **kwargs: diary,
    )
    monkeypatch.setattr("market_desk.db.load_setting", _setting)
    out = build_personal_pnl_calendar(user_id=1, days=10, trade_date="2026-09-18")
    assert out["day_n"] == 2
    assert out["day_wins"] == 1
    assert out["day_win_rate"] == 50.0
    assert len(out["items"]) == 2
