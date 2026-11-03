"""Frozen code-board appearance index: write hooks, backfill, code/board reads."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db import (
    backfill_code_board_seen,
    current_boards_for_code,
    load_seen_by_board,
    load_seen_by_code,
    save_board_members_snap,
    save_ma_fan_day,
    upsert_signal,
)
from market_desk.review.range_board import build_board_roster, build_code_appearances


def _init_db(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "seen.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def _buy(code: str, day: str, boards: list[str], price: float = 10.0, **extra) -> dict:
    return {
        "trade_date": day,
        "signaled_at": f"{day} 10:00:00",
        "signal_type": "buy",
        "action": "buy",
        "phase": "盘中",
        "mainline": extra.pop("mainline", None),
        "code": code,
        "name": extra.pop("name", "测试股"),
        "kind": "stock",
        "price": price,
        "last": price,
        "ready": 0,
        "payload": {"board_names": boards, **extra},
    }


def test_signal_upsert_indexes_boards_and_is_idempotent(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    upsert_signal(_buy("600100", "2026-10-20", ["固态电池", "新能源"], price=11.2))
    upsert_signal(_buy("600100", "2026-10-20", ["固态电池", "新能源"], price=11.9))
    rows = load_seen_by_code("600100")
    assert len(rows) == 1
    assert set(rows[0]["boards"]) == {"固态电池", "新能源"}
    assert rows[0]["source"] == "signal"
    assert rows[0]["price"] == 11.2  # first suggested price wins


def test_empty_board_placeholder_is_replaced_and_still_queryable(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    upsert_signal(_buy("600101", "2026-10-21", []))
    rows = load_seen_by_code("600101")
    assert len(rows) == 1 and rows[0]["boards"] == []
    # Empty-board rows never match a board search.
    assert load_seen_by_board("固态") == []
    upsert_signal(_buy("600101", "2026-10-21", ["半导体"]))
    rows = load_seen_by_code("600101")
    assert len(rows) == 1 and rows[0]["boards"] == ["半导体"]


def test_sell_signals_are_not_indexed(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    row = _buy("600102", "2026-10-21", ["白酒"])
    row["signal_type"] = "sell"
    row["owner_user_id"] = 7
    upsert_signal(row)
    assert load_seen_by_code("600102") == []


def test_ma_fan_day_indexes_fan_and_pot_and_replaces_on_resave(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    body = {
        "items": [
            {
                "code": "600200",
                "name": "甲",
                "close": 12.3,
                "pct": 1.2,
                "stage": "初期",
                "industry": "白酒",
                "boards": ["酿酒概念"],
                "fwd": {"d3": -2.5},
            }
        ],
        "pot": [{"code": "600201", "name": "乙", "close": 8.8, "focus": True, "industry": "半导体"}],
    }
    save_ma_fan_day("2026-10-22", body)
    fan = load_seen_by_code("600200")
    assert fan[0]["source"] == "ma_fan" and fan[0]["price"] == 12.3 and fan[0]["d3"] == -2.5
    assert set(fan[0]["boards"]) == {"酿酒概念", "白酒"}
    pot = load_seen_by_code("600201")
    assert pot[0]["source"] == "pot" and "浅跌" in pot[0]["tag"]

    body["items"] = []
    save_ma_fan_day("2026-10-22", body)
    assert load_seen_by_code("600200") == []
    assert load_seen_by_code("600201")


def test_board_search_normalizes_suffix_and_skips_other_boards(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    upsert_signal(_buy("600300", "2026-10-20", ["固态电池"]))
    save_ma_fan_day(
        "2026-10-21",
        {"items": [{"code": "600301", "name": "丙", "close": 5.0, "boards": ["固态电池概念"]}], "pot": []},
    )
    save_ma_fan_day(
        "2026-10-22",
        {"items": [{"code": "600302", "name": "丁", "close": 6.0, "boards": ["半导体"]}], "pot": []},
    )
    hit = build_board_roster("固态电池", days=20)
    codes = {r["code"] for r in hit["items"]}
    assert codes == {"600300", "600301"}
    assert all(r["price"] is not None for r in hit["items"])


def test_backfill_from_existing_rows_once(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    upsert_signal(_buy("600400", "2026-10-20", ["军工"]))
    save_ma_fan_day("2026-10-21", {"items": [{"code": "600401", "name": "戊", "close": 3.3}], "pot": []})
    with desk_db._connect() as conn:
        conn.execute("DELETE FROM code_board_seen")
        conn.commit()
    out = backfill_code_board_seen()
    assert out["signals"] == 1 and out["ma_fan_days"] == 1
    assert load_seen_by_code("600400")[0]["boards"] == ["军工"]
    # Second run is a no-op once the table has rows.
    assert backfill_code_board_seen() == {"signals": 0, "ma_fan_days": 0}


def test_appearances_reads_index_and_current_boards_are_separate(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    save_ma_fan_day(
        "2026-10-22",
        {"items": [{"code": "600500", "name": "己", "close": 9.9, "boards": ["旧概念"]}], "pot": []},
    )
    save_board_members_snap("em", "BK0001", ["600500", "600501"], name="新概念", kind="concept")
    out = build_code_appearances("600500")
    assert out["ok"] is True
    assert out["name"] == "己"
    assert out["ma_fan_n"] == 1 and out["pot_n"] == 0
    assert out["board_set"] == ["旧概念"]  # frozen, not rewritten by today's snap
    assert [b["name"] for b in out["current_boards"]] == ["新概念"]
    assert out["scan_rows"][0]["close"] == 9.9
    assert current_boards_for_code("600599") == []
