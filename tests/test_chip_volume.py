"""Chip-peak / daily-volume context: math, buckets, caching and signal persistence."""

from __future__ import annotations

import asyncio

import market_desk.chip_volume as cvm
import market_desk.db as desk_db
from market_desk.pick_score import build_history_stats, score_pick


def _bars(n: int, price: float = 10.0, vol: float = 1000.0, turnover: float = 5.0, start_day: int = 1):
    out = []
    for i in range(n):
        out.append({
            "date": f"2026-{6 + (start_day + i) // 28:02d}-{1 + (start_day + i) % 28:02d}",
            "open": price, "close": price, "high": price * 1.01, "low": price * 0.99,
            "volume": vol, "turnover": turnover, "pct": 0.0,
        })
    return out


def test_chip_profile_reads_profit_and_cost():
    bars = _bars(100)
    above = cvm.chip_profile(bars, 10.5)
    below = cvm.chip_profile(bars, 9.5)
    at = cvm.chip_profile(bars, 10.0)
    assert above["profit"] > 99 and below["profit"] < 1
    assert abs(at["vs_cost"]) < 0.5
    assert cvm.chip_profile(bars[:30], 10.0) is None


def test_volume_profile_and_classify():
    bars = _bars(20)
    bars[-1] = dict(bars[-1], volume=500.0, pct=1.2)
    vol = cvm.volume_profile(bars)
    assert vol["vr_prev"] == 0.5 and vol["pv_prev"] == "缩量涨"
    assert cvm.classify({"vr_prev": 2.4})["vol1"] == "spike"
    assert cvm.classify({"vs_cost": 8.0, "profit": 60.0})["chip_pos"] == "high"
    assert cvm.classify({"vs_cost": 2.0, "profit": 85.0})["chip_pos"] == "high"
    assert cvm.classify({"vs_cost": 20.0, "profit": 95.0})["chip_pos"] == "ok"
    assert cvm.classify({"vol_trend3": 0.6})["vol3"] == "fade"


def test_build_cv_tags_high_chip():
    bars = _bars(100, price=10.0)
    cv = cvm.build_cv(bars, 10.8)
    assert cv["chip_pos"] == "high"
    keys = [t["k"] for t in cvm.cv_tags(cv)]
    assert "chip_high" in keys


def test_ivol_profile_classify_and_tags():
    bars = _bars(30)
    for i, b in enumerate(bars):
        b["pct"] = 6.0 if i % 2 else -6.0
    mkt = {b["date"]: 0.0 for b in bars}
    cv = cvm.build_cv(bars, 10.0, mkt)
    assert cv["ivol20"] > 6 and cv["ivol"] == "high"
    assert "ivol_high" in [t["k"] for t in cvm.cv_tags(cv)]
    calm = {b["date"]: float(b["pct"]) for b in bars}
    assert cvm.ivol_profile(bars, calm) == 0.0
    assert cvm.ivol_profile(bars, {}) is None
    assert cvm.ivol_profile(bars[:10], mkt) is None


def test_add_ivol_fills_stored_cv_once():
    bars = _bars(30)
    mkt = {b["date"]: 0.0 for b in bars}
    cv = {"chip_pos": "ok"}
    assert cvm.add_ivol(cv, bars, mkt) and cv["ivol"] == "low" and cv["ivol20"] == 0.0
    assert not cvm.add_ivol(cv, bars, mkt)


def test_pick_score_uses_ivol_factor():
    base = {"code": "600001", "kind": "stock", "manual": True}
    empty = build_history_stats([])
    hot = score_pick({**base, "cv": {"ivol": "high", "ivol20": 5.1}}, empty)
    calm = score_pick({**base, "cv": {"ivol": "low", "ivol20": 1.5}}, empty)
    mid = score_pick({**base, "cv": {"ivol": "mid", "ivol20": 3.0}}, empty)
    assert hot["score"] == 57 and calm["score"] == 62 and mid["score"] == 60
    assert not any(f["key"] == "ivol" for f in mid["factors"])


def test_float_cap_from_prior_bar_and_small_cap_bucket():
    bars = _bars(30, price=20.0, vol=200000.0, turnover=4.0)
    assert cvm.float_cap_yi(bars, "600001") == 100.0
    assert cvm.build_cv(bars, 20.0, code="600001")["cap"] == "other"
    small = _bars(30, price=10.0, vol=200000.0, turnover=4.0)
    cv = cvm.build_cv(small, 10.0, code="300001")
    assert cv["float_cap_yi"] == 50.0 and cv["cap"] == "small"
    assert "small_cap" in [t["k"] for t in cvm.cv_tags(cv)]
    assert cvm.float_cap_yi([{"volume": 1.0, "close": 1.0, "turnover": 0.0}], "600001") is None
    assert cvm.float_cap_yi([], "600001") is None


def test_float_cap_star_volume_is_shares_and_needs_code():
    bars = _bars(30, price=20.0, vol=20000000.0, turnover=4.0)
    assert cvm.float_cap_yi(bars, "688001") == 100.0
    assert cvm.float_cap_yi(bars, None) is None
    assert "float_cap_yi" not in cvm.build_cv(bars, 20.0)


def test_add_ivol_backfills_float_cap_on_stored_cv():
    bars = _bars(30, price=10.0, vol=200000.0, turnover=4.0)
    cv = {"chip_pos": "ok", "ivol20": 1.0, "ivol": "low"}
    assert cvm.add_ivol(cv, bars, {}, code="600001") and cv["cap"] == "small" and cv["ivol"] == "low"
    assert not cvm.add_ivol(cv, bars, {}, code="600001")


def test_pick_score_small_cap_and_watch_tags():
    empty = build_history_stats([])
    base = {"code": "600001", "kind": "stock", "manual": True}
    small = score_pick({**base, "cv": {"cap": "small", "float_cap_yi": 50.0}}, empty)
    big = score_pick({**base, "cv": {"cap": "other", "float_cap_yi": 500.0}}, empty)
    assert small["score"] == 63 and big["score"] == 60
    watch = score_pick({**base, "open_gap_pct": 3.2, "lhb_recent": {"date": "2026-09-28", "net_yi": -0.5}}, empty)
    by = {f["key"]: f for f in watch["factors"]}
    assert watch["score"] == 60 and by["gap"]["points"] == 0 and by["lhb"]["points"] == 0
    assert "09-28" in by["lhb"]["detail"] and "-0.50" in by["lhb"]["detail"]
    low_gap = score_pick({**base, "open_gap_pct": 1.5}, empty)
    assert not any(f["key"] == "gap" for f in low_gap["factors"])
    etf = score_pick({**base, "kind": "etf", "open_gap_pct": 3.0, "lhb_recent": {"date": "2026-09-28"}}, empty)
    assert not any(f["key"] in ("gap", "lhb") for f in etf["factors"])


def test_bars_before_many_excludes_signal_day(monkeypatch):
    cvm._BARS_CACHE.clear()
    calls: list[str] = []

    async def fake_fetch(client, code, limit=130):
        calls.append(code)
        return [{"date": "2026-09-28", "close": 1.0}, {"date": "2026-09-29", "close": 1.1}]

    monkeypatch.setattr(cvm, "fetch_bars_with_turnover", fake_fetch)
    out = asyncio.run(cvm.bars_before_many(None, ["600001"], "2026-09-29"))
    assert [b["date"] for b in out["600001"]] == ["2026-09-28"]
    asyncio.run(cvm.bars_before_many(None, ["600001"], "2026-09-29"))
    assert calls == ["600001"]


def test_pick_score_uses_cv_factors():
    row = {"code": "600001", "kind": "stock", "manual": True,
           "cv": {"chip_pos": "high", "profit": 88.0, "vs_cost": 7.0, "vol1": "shrink", "vr_prev": 0.6,
                  "vol3": "fade", "vol_trend3": 0.7}}
    out = score_pick(row, build_history_stats([]))
    by = {f["key"]: f["points"] for f in out["factors"]}
    assert by["chip"] == -2.0 and by["vol1"] == 0.0 and by["vol3"] == -2.0
    assert out["score"] == 56


def test_upsert_keeps_first_cv(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    base = {"trade_date": "2026-09-29", "signaled_at": "2026-09-29 10:00:00", "signal_type": "buy",
            "code": "600001", "name": "A", "kind": "stock", "price": 10.0, "ready": 0}
    desk_db.upsert_signal({**base, "payload": {"cv": {"chip_pos": "high"}}})
    desk_db.upsert_signal({**base, "payload": {"cv": {"chip_pos": "ok"}, "pct": 1.0}})
    desk_db.upsert_signal({**base, "payload": {"cv": None}})
    row = desk_db.load_signals_for_date("2026-09-29")[0]
    assert row["payload"]["cv"] == {"chip_pos": "high"}
    assert row["payload"]["pct"] == 1.0


def test_upsert_keeps_first_live_price(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    base = {"trade_date": "2026-09-30", "signaled_at": "2026-09-30 10:00:00", "signal_type": "buy",
            "code": "600001", "name": "A", "kind": "stock", "price": 10.0, "ready": 0, "payload": {}}
    desk_db.upsert_signal({**base, "last": 10.1})
    desk_db.upsert_signal({**base, "last": 10.6})
    row = desk_db.load_signals_for_date("2026-09-30")[0]
    assert row["payload"]["first_last"] == 10.1 and row["last"] == 10.6
