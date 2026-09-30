"""Gate ledger: note normalization, gate keys, same-day attribution and persistence."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.gate_ledger import (
    build_gate_ledger,
    normalize_gate_note,
    normalize_gate_notes,
    signal_gate_keys,
)
from market_desk.review import record_session_signals


def _sig(day: str, d3: float, **payload) -> dict:
    return {
        "trade_date": day, "signal_type": "buy", "kind": "stock", "ready": 0,
        "outcome_day3_pct": d3, "payload": payload,
    }


def test_normalize_gate_note_strips_numbers_and_detail():
    assert normalize_gate_note("复盘命中32.1%·软降") == "复盘命中·软降"
    assert normalize_gate_note("仓位热度×0.85") == "仓位热度"
    assert normalize_gate_note("换防护栏=电子→煤炭") == "换防护栏"
    assert normalize_gate_note("开盘静音+竞价开盘桥叠乘·禁试探（静音5分）") == "开盘静音+竞价开盘桥叠乘·禁试探"
    assert normalize_gate_notes(["相似日n=3", "相似日n=5", "", None]) == ["相似日n"]


def test_signal_gate_keys_collects_row_and_market_gates():
    row = _sig(
        "2026-09-29", 1.0,
        ever_ready=True, near_entry=True, trend_down=True,
        confirm_fail_hist=["分时未站稳均价"], confirm_soft=["分时未验"],
        market_gates=["指数闸门"],
    )
    keys = {k: (scope, pol) for k, scope, pol in signal_gate_keys(row)}
    assert keys["亮过可买"] == ("row", 1)
    assert keys["近买点"] == ("row", 1)
    assert keys["日线向下"] == ("row", -1)
    assert keys["卡·分时"] == ("row", -1)
    assert keys["软·分时未验"] == ("row", -1)
    assert keys["市·指数闸门"] == ("market", -1)


def test_ledger_row_gate_uses_same_day_peers():
    days = ["2026-09-22", "2026-09-23", "2026-09-24"]
    rows = []
    for i, day in enumerate(days):
        base = 5.0 if i == 0 else -3.0  # day effect must cancel out
        rows += [_sig(day, base - 2.0, block_ready=True) for _ in range(3)]
        rows += [_sig(day, base + 1.0) for _ in range(3)]
    out = build_gate_ledger(rows, days=10)
    gl = next(r for r in out["rows"] if r["key"] == "禁亮灯")
    assert gl["n"] == 9 and gl["days"] == 3
    assert gl["excess"] == -3.0 and gl["metric"] == -3.0
    assert gl["tone"] == "good" and gl["verdict"] == "挡对了"


def test_ledger_market_gate_uses_raw_diff_and_skips_non_trading_days():
    rows = []
    for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
        rows += [_sig(day, -2.0, market_gates=["弱竞价闸门"]) for _ in range(3)]
    for day in ("2026-09-28", "2026-09-29"):
        rows += [_sig(day, 2.0) for _ in range(3)]
    rows.append(_sig("2026-09-25", 9.0, market_gates=["弱竞价闸门"]))  # Mid-Autumn holiday
    out = build_gate_ledger(rows, days=10)
    assert out["n"] == 15
    gl = next(r for r in out["rows"] if r["key"] == "市·弱竞价闸门")
    assert gl["excess"] is None and gl["raw_diff"] == -4.0
    assert gl["verdict"] == "挡对了"


def test_ledger_low_sample_and_positive_polarity():
    rows = [_sig("2026-09-22", -1.0, near_entry=True), _sig("2026-09-22", 2.0)]
    out = build_gate_ledger(rows, days=10)
    gl = next(r for r in out["rows"] if r["key"] == "近买点")
    assert gl["verdict"] == "样本不足" and gl["tone"] == "low"
    many = []
    for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
        many += [_sig(day, -1.0, near_entry=True) for _ in range(3)]
        many += [_sig(day, 1.0) for _ in range(3)]
    gl = next(r for r in build_gate_ledger(many, days=10)["rows"] if r["key"] == "近买点")
    assert gl["verdict"] == "反向" and gl["tone"] == "bad"


def test_record_skips_non_trading_day_and_keeps_first_market_gates(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()

    def snap(day: str, at: str, notes: list[str]) -> dict:
        return {
            "trade_date": day, "updated_at": at, "phase": "发酵",
            "verdict": {
                "action": "观察回踩", "algo_notes": notes,
                "recommend": {"items": [{"code": "600001", "name": "A", "buy_price": 10.0}]},
            },
        }

    assert record_session_signals(snap("2026-09-25", "2026-09-25 10:00:00", ["指数闸门"])) == 0
    assert record_session_signals(snap("2026-09-27", "2026-09-27 10:00:00", ["指数闸门"])) == 0
    assert record_session_signals(snap("2026-09-29", "2026-09-29 10:00:00", ["指数闸门", "相似日n=3"])) == 1
    assert record_session_signals(snap("2026-09-29", "2026-09-29 10:30:00", ["弱竞价闸门"])) == 1
    rows = desk_db.load_signals_for_date("2026-09-29")
    assert len(rows) == 1
    assert rows[0]["payload"]["market_gates"] == ["指数闸门", "相似日n"]
    assert desk_db.load_signals_for_date("2026-09-25") == []
