"""MA-fan v8: scan-day checks on top of the sticky → fan pattern."""

from __future__ import annotations

from market_desk.ma_fan import scan as mf_scan
from market_desk.ma_fan.pattern import score_pattern


def _bars(closes: list[float], vols: list[float]) -> list[dict]:
    out = []
    prev = None
    for n, (c, v) in enumerate(zip(closes, vols)):
        out.append({
            "date": f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}",
            "close": c, "high": c, "low": c, "volume": v,
            "pct": None if prev is None else round((c / prev - 1) * 100, 2),
        })
        prev = c
    return out


def _fan() -> tuple[list[float], list[float]]:
    """110 flat sessions (sticky MAs) then an eight-session 2% / day climb on more volume."""
    closes = [10.0] * 110
    for _ in range(8):
        closes.append(round(closes[-1] * 1.02, 4))
    return closes, [1e6] * 110 + [1.8e6] * 8


def test_fresh_fan_reports_scan_day_and_bonus_tags() -> None:
    closes, vols = _fan()
    got = score_pattern(_bars(closes, vols))
    assert got and got["failed"] == []
    assert got["close"] == round(closes[-1], 2) and got["lag"] == 0
    assert "今日发散" in got["tags"] and "量价齐升" in got["tags"]


def test_fan_followed_by_sharp_drop_is_failed_and_reports_latest_close() -> None:
    closes, vols = _fan()
    for _ in range(3):
        closes.append(round(closes[-1] * 0.96, 4))
        vols.append(1.5e6)
    bars = _bars(closes, vols)
    assert score_pattern(bars) is None, "failed fans leave the list"
    got = score_pattern(bars, include_failed=True)
    assert got and got["lag"] >= 1
    assert any(r.startswith("回撤") for r in got["failed"]) and "3日连跌破MA5" in got["failed"]
    assert got["close"] == round(closes[-1], 2), "price describes the scan day, not the fan bar"
    assert got["pct"] == bars[-1]["pct"]


def test_merge_failed_drops_codes_still_listed_as_hits() -> None:
    prev = [{"code": "301316", "failed": ["回撤12%"], "score_base": 49.5}]
    new = [{"code": "600001", "failed": ["3日连跌破MA5"], "score_base": 60.0},
           {"code": "300457", "failed": ["回撤9%"], "score_base": 70.0}]
    out = mf_scan._merge_failed(prev, new, hit_codes={"300457"})
    assert [r["code"] for r in out] == ["600001", "301316"]
