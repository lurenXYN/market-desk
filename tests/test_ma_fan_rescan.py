"""MA-fan rescan: single slot, cooldown, pacing, progress, abort and log tail."""

from __future__ import annotations

import asyncio
import time

import pytest

import market_desk.db as desk_db
import market_desk.ma_fan as mf


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    monkeypatch.setattr(mf, "MA_FAN_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(mf, "MA_FAN_PAGE_DELAY_S", 0.0)
    mf._PROGRESS.clear()
    mf._PROGRESS["running"] = False
    mf._SCORE_CACHE.clear()
    monkeypatch.setattr(mf, "_LAST_FORCE_END", 0.0)
    monkeypatch.setattr(mf, "MA_FAN_CACHE_FROM", (24, 0))
    yield
    mf._PROGRESS.clear()
    mf._PROGRESS["running"] = False
    mf._SCORE_CACHE.clear()


def _universe(n: int) -> list[dict]:
    return [
        {"code": f"{600000 + i:06d}", "name": f"S{i}", "amount": 5e9 - i * 1e6, "pct": 1.0}
        for i in range(n)
    ]


def _flat_bars(n: int = 120) -> list[dict]:
    return [
        {"date": f"2026-01-{i:03d}", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1e6}
        for i in range(n)
    ]


def _run_force():
    return mf.run_ma_fan_all_due_slices(
        trade_date="2026-09-24", minutes=22 * 60, top=80, force_all=True
    )


def test_claim_is_exclusive_and_force_starts_cooldown() -> None:
    assert mf.try_claim_scan("force", "2026-09-24") is True
    assert mf.try_claim_scan("force", "2026-09-24") is False
    assert mf.scan_progress()["running"] is True
    mf.release_scan()
    p = mf.scan_progress()
    assert p["running"] is False and p["phase"] == "done"
    assert 0 < mf.force_cooldown_left() <= mf.MA_FAN_FORCE_COOLDOWN_S


def test_slice_scan_does_not_start_cooldown() -> None:
    assert mf.try_claim_scan("slice", "2026-09-24")
    mf.release_scan()
    assert mf.force_cooldown_left() == 0


def test_pacer_spaces_request_starts() -> None:
    async def go() -> float:
        pacer = mf._Pacer(0.05)
        t0 = time.monotonic()
        await asyncio.gather(*[pacer.wait() for _ in range(4)])
        return time.monotonic() - t0

    assert asyncio.run(go()) >= 0.14


def test_force_loads_universe_once_and_tracks_progress(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_universe(client, *, boards="all", need=400, min_amount_yi=1.2):
        calls.append(need)
        return _universe(1000)

    async def fake_bars(client, code, limit=120):
        return _flat_bars()

    monkeypatch.setattr(mf, "load_universe", fake_universe)
    monkeypatch.setattr(mf, "fetch_daily_bars", fake_bars)
    out = asyncio.run(_run_force())
    assert calls == [1000]
    assert out["slices_done"] == ["0-400", "400-800", "800-1000"]
    p = mf.scan_progress()
    assert p["running"] is False and p["phase"] == "done"
    assert p["total"] == 1000 and p["done"] == 1000 and p["pct"] == 100.0
    assert p["fails"] == 0


def test_force_aborts_when_bar_source_keeps_failing(monkeypatch) -> None:
    fetched: list[str] = []

    async def fake_universe(client, **kw):
        return _universe(1000)

    async def dead_bars(client, code, limit=120):
        fetched.append(code)
        return []

    monkeypatch.setattr(mf, "load_universe", fake_universe)
    monkeypatch.setattr(mf, "fetch_daily_bars", dead_bars)
    with pytest.raises(RuntimeError, match="限流"):
        asyncio.run(_run_force())
    p = mf.scan_progress()
    assert p["running"] is False and p["phase"] == "error" and "限流" in p["error"]
    assert len(fetched) < 400
    assert mf.try_claim_scan("force", "2026-09-24") is True


def test_empty_universe_keeps_existing_day(monkeypatch) -> None:
    desk_db.save_ma_fan_day("2026-09-24", {"items": [{"code": "600000"}], "trade_date": "2026-09-24"})

    async def empty_universe(client, **kw):
        return []

    monkeypatch.setattr(mf, "load_universe", empty_universe)
    with pytest.raises(RuntimeError, match="成交额榜为空"):
        asyncio.run(_run_force())
    body = desk_db.load_ma_fan_day("2026-09-24") or {}
    assert [x["code"] for x in body.get("items") or []] == ["600000"]


def test_busy_slot_returns_busy_without_scanning(monkeypatch) -> None:
    async def boom(client, **kw):
        raise AssertionError("must not scan while busy")

    monkeypatch.setattr(mf, "load_universe", boom)
    assert mf.try_claim_scan("slice", "2026-09-24")
    out = asyncio.run(_run_force())
    assert out["ok"] is False and out["busy"] is True


def test_post_close_rescan_reuses_cached_scores(monkeypatch) -> None:
    fetched: list[str] = []

    async def fake_universe(client, **kw):
        return _universe(1000)

    async def fake_bars(client, code, limit=120):
        fetched.append(code)
        return _flat_bars()

    monkeypatch.setattr(mf, "MA_FAN_CACHE_FROM", (0, 0))
    monkeypatch.setattr(mf, "load_universe", fake_universe)
    monkeypatch.setattr(mf, "fetch_daily_bars", fake_bars)
    asyncio.run(_run_force())
    assert len(fetched) == 1000
    mf.release_scan()
    monkeypatch.setattr(mf, "_LAST_FORCE_END", 0.0)
    asyncio.run(_run_force())
    assert len(fetched) == 1000
    assert mf.scan_progress()["done"] == 1000


def test_intraday_scan_does_not_cache() -> None:
    mf._score_cache_put("600000", None, None)
    assert mf._SCORE_CACHE == {}
    assert mf._score_cache_get("600000") is None


def test_enrich_hits_meta_fills_missing_rows_only(monkeypatch) -> None:
    asked: list[list[str]] = []

    async def fake_meta(client, codes):
        asked.append(list(codes))
        return {c: {"industry": "半导体", "mv_yi": 321.5} for c in codes}

    async def fake_holders(client, codes):
        return {c: {"holder_num": 45678, "holder_chg_pct": -3.2, "holder_end": "2026-06-30"} for c in codes}

    monkeypatch.setattr(mf, "fetch_stock_meta_many", fake_meta)
    monkeypatch.setattr(mf, "fetch_holder_stats_many", fake_holders)
    items = [
        {"code": "600000", "industry": "银行Ⅱ", "mv_yi": 3000.0},
        {"code": "300308", "mv_yi": 10.0},
    ]
    out = asyncio.run(mf.enrich_hits_meta(None, items))
    assert asked == [["300308"]]
    assert out[0]["industry"] == "银行Ⅱ" and "holder_num" not in out[0]
    assert out[1]["industry"] == "半导体" and out[1]["mv_yi"] == 321.5
    assert out[1]["holder_num"] == 45678 and out[1]["holder_chg_pct"] == -3.2


def test_ytd_limit_up_uses_board_threshold() -> None:
    from market_desk.zt_stats import count_limit_ups_ytd_from_bars

    bars = [{"date": "2025-12-31", "close": 10.0}]
    for i, c in enumerate((11.0, 12.1, 14.52), start=2):
        bars.append({"date": f"2026-01-0{i}", "close": c})
    assert count_limit_ups_ytd_from_bars(bars, name="X", code="600000", year=2026) == 3
    assert count_limit_ups_ytd_from_bars(bars, name="X", code="300308", year=2026) == 1


def test_tail_log_filters_level_and_query(monkeypatch, tmp_path) -> None:
    import market_desk.logs as logs

    f = tmp_path / "logs" / "market-desk.log"
    f.parent.mkdir(parents=True)
    f.write_text(
        "2026-09-24 16:00:00,001 INFO market_desk.ma_fan slice ok\n"
        "2026-09-24 16:00:01,002 ERROR market_desk.ma_fan ma_fan force scan failed\n"
        "Traceback (most recent call last):\n"
        "  RuntimeError: boom\n"
        "2026-09-24 16:00:02,003 WARNING market_desk.app client-error where=mafan-load\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(logs, "LOG_FILE", f)
    errs = logs.tail_log(50, level="error")
    assert errs[0].endswith("ma_fan force scan failed") and "RuntimeError: boom" in errs[-1]
    warn = logs.tail_log(50, "client-error", level="warning")
    assert len(warn) == 1 and "mafan-load" in warn[0]
    assert len(logs.tail_log(2)) == 2
