"""Holiday guards, Sina alias history, mainline debounce and relative theme reputation."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db.market import board_alias_name
from market_desk.gate_ledger import signal_gate_keys
from market_desk.lifecycle import classify_lifecycle
from market_desk.mainline import is_pseudo_board, mainline_pool, pick_mainline
from market_desk.review import board_stage_lookup
from market_desk.theme_memory import (
    _usable_outcome,
    compute_rep_adj_relative,
    theme_base_persist_rate,
)


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _board(name, zt, *, kind="industry", status="确认中", pct=2.0, bk=None):
    return {"name": name, "bk": bk or f"BK_{name}", "kind": kind, "status": status, "zt_n": zt, "pct": pct}


def test_holiday_rows_are_not_persisted_or_loaded(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    desk_db.save_daily("2026-09-25", {"phase": "发酵", "mainline": "电子"})
    desk_db.save_daily("2026-09-24", {"phase": "发酵", "mainline": "电子"})
    desk_db.save_board_daily("2026-09-25", [{"bk": "BK1", "name": "电子", "zt_n": 3}])
    desk_db.save_board_daily("2026-09-24", [{"bk": "BK1", "name": "电子", "zt_n": 3}])
    assert [r["trade_date"] for r in desk_db.load_daily(5)] == ["2026-09-24"]
    assert desk_db.load_board_rows_for_date("2026-09-25") == []
    assert list(desk_db.load_board_hist_map("2026-09-29")) == ["BK1"]


def test_sina_rows_merge_into_same_name_history(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    desk_db.save_board_daily("2026-09-23", [{"bk": "BK0477", "name": "酿酒行业", "zt_n": 2, "pct": 1.0}])
    desk_db.save_board_daily("2026-09-24", [{"bk": "BK0477", "name": "酿酒行业", "zt_n": 3, "pct": 2.0}])
    desk_db.save_board_daily("2026-09-28", [{"bk": "SINA:hangye_ni", "name": "酿酒", "zt_n": 4, "pct": 3.0}])
    hist = desk_db.load_board_hist_map("2026-09-29")
    assert [r["trade_date"] for r in hist["SINA:hangye_ni"]] == ["2026-09-23", "2026-09-24", "2026-09-28"]
    assert [r["trade_date"] for r in hist["BK0477"]] == ["2026-09-23", "2026-09-24", "2026-09-28"]
    assert board_alias_name("猪肉概念") == board_alias_name("猪肉")


def test_sina_board_without_history_has_no_stage():
    card = _board("电子信息", 4, kind="concept", bk="SINA:gn_dzxx")
    card["hist"] = []
    assert classify_lifecycle(card) is None
    card["bk"] = "BK0001"
    assert classify_lifecycle(card) == "starting"


def test_mainline_pool_drops_pseudo_and_joins_concepts_when_thin():
    assert is_pseudo_board("东方财富热股") and is_pseudo_board("QFII重仓") and not is_pseudo_board("半导体")
    thin = [_board("林业Ⅱ", 2), _board("QFII重仓", 12, kind="concept"), _board("CPO概念", 9, kind="concept")]
    names = {b["name"] for b in mainline_pool(thin)}
    assert names == {"林业Ⅱ", "CPO概念"}
    broad = [_board("电子", 6), _board("CPO概念", 9, kind="concept")]
    assert [b["name"] for b in mainline_pool(broad)] == ["电子"]


def test_cross_theme_switch_waits_for_confirmation():
    hot = [_board("银行", 4), _board("煤炭", 14)]
    state: dict = {}
    kw = {"sticky_name": "银行", "margin": 12.0, "confirm_seconds": 600, "state_out": state}
    first = pick_mainline(hot, now_text="2026-09-29 10:00:00", **kw)
    assert first["name"] == "银行"
    assert state["challenger"]["name"] == "煤炭" and state["challenger"]["since"] == "2026-09-29 10:00:00"
    mid = pick_mainline(hot, now_text="2026-09-29 10:05:00", challenger_prev=state["challenger"], **kw)
    assert mid["name"] == "银行"
    done = pick_mainline(hot, now_text="2026-09-29 10:10:00", challenger_prev=state["challenger"], **kw)
    assert done["name"] == "煤炭"
    fading = [_board("银行", 4, status="退潮", pct=-1.0), _board("煤炭", 14)]
    assert pick_mainline(fading, now_text="2026-09-29 10:00:00", **kw)["name"] == "煤炭"


def test_switch_log_drops_return_to_origin(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    row = {"trade_date": "2026-09-29", "action": "观察回踩", "phase": "发酵", "temperature": 50}
    assert desk_db.try_add_mainline_switch(
        {**row, "switched_at": "2026-09-29 10:00:00", "from_name": "房地产", "to_name": "燃料电池"}
    )
    desk_db.try_add_mainline_switch(
        {**row, "switched_at": "2026-09-29 10:02:00", "from_name": "氢能源", "to_name": "房地产"}
    )
    assert desk_db.load_mainline_switches("2026-09-29") == []


def test_relative_reputation_centers_on_base_rate():
    assert compute_rep_adj_relative(0.0, 0.0, 0.25) == 0.0
    assert abs(compute_rep_adj_relative(3.0, 1.0, 0.25)) < 0.01
    thin = compute_rep_adj_relative(2.0, 0.0, 0.25)
    fat = compute_rep_adj_relative(12.0, 0.0, 0.25)
    assert -2.5 < thin < 0 and fat < thin
    assert compute_rep_adj_relative(1.0, 4.0, 0.25) > 3.0
    rows = [{"trade_date": "2026-09-22", "next_date": "2026-09-23", "theme_key": "电子", "outcome": "fade"}] * 20
    rows += [{"trade_date": "2026-09-22", "next_date": "2026-09-23", "theme_key": "电力", "outcome": "persist"}] * 5
    rows += [{"trade_date": "2026-09-24", "next_date": "2026-09-25", "theme_key": "电子", "outcome": "persist"}] * 9
    rows += [{"trade_date": "2026-09-22", "next_date": "2026-09-23", "theme_key": "QFII重仓", "outcome": "persist"}]
    base = theme_base_persist_rate(rows)
    assert base["n"] == 25 and base["rate"] == 0.2 and not base["default"]
    assert not _usable_outcome(rows[-1]) and not _usable_outcome(rows[26])


def test_dragon_row_main_only_and_observe_by_default(monkeypatch):
    import market_desk.leaders as leaders
    from market_desk.verdict import build_dragon_recommend

    def fake(board, zt, **_kw):
        code = {"电子": "600001", "煤炭": "600002"}.get(board.get("name"), "600003")
        return [{"code": code, "name": board.get("name"), "last": 10.0, "buy_price": 10.0, "ready": True}]

    monkeypatch.setattr(leaders, "build_dual_dragon_stocks", fake)
    rec = build_dragon_recommend(
        _board("电子", 6), [], side_board=_board("煤炭", 4), link_board=_board("银行", 3)
    )
    assert [it["dragon_scope"] for it in rec["items"]] == ["main"]
    assert rec["buy"] is False and not rec["items"][0]["ready"]


def test_board_stage_lookup_and_ledger_flag():
    snap = {
        "mainline_lifecycle": {
            "ongoing": [{"name": "电子"}],
            "starting": [],
            "ending": [],
            "stage_map": {"BK2": "starting"},
        },
        "hot_boards": [{"bk": "BK2", "name": "CPO概念"}],
    }
    assert board_stage_lookup(snap) == {"电子": "ongoing", "CPO概念": "starting"}
    keys = {k: p for k, _, p in signal_gate_keys({"payload": {"board_stage": "ongoing"}})}
    assert keys["板块·主升"] == 1
    assert not any(k.startswith("板块·") for k, _, _ in signal_gate_keys({"payload": {"board_stage": "none"}}))
