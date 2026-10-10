import json

import market_desk.db as desk_db
from market_desk.db.core import _connect
from market_desk.shadow_health import shadow_check, shadow_health


def _setup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    import market_desk.db.core as core

    monkeypatch.setattr(core, "DB_PATH", tmp_path / "desk.db", raising=False)
    monkeypatch.setattr(core, "DATA_DIR", tmp_path, raising=False)
    desk_db.init_db()


def _card(conn, day: str, code: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO signals(trade_date, signaled_at, signal_type, code, price, payload, owner_user_id) "
        "VALUES (?, ?, 'buy', ?, 1.0, ?, 0)",
        (day, f"{day} 10:00:00", code, json.dumps(payload)),
    )


def test_shadow_health_flags_missing_fields_and_explains_empty_crowd(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path)
    full = {"book_open": {}, "cf": {}, "stop_atr": 1.0, "market_gates": [], "first_last": 1.0}
    with _connect() as conn:
        _card(conn, "2026-10-09", "600001", {**full, "ever_ready": True, "first_ready_px": 1.0})
        _card(conn, "2026-10-09", "600002", full)
        _card(conn, "2026-10-08", "600003", {"cf": {}})
        conn.execute(
            "INSERT INTO board_crowding(trade_date, name, kind, share, turnover, market_turnover, updated_at) "
            "VALUES ('2026-10-09', '电子器件', 'industry', 7.1, 1, 1, '')"
        )
        conn.execute(
            "INSERT INTO auction_lock(trade_date, median_open, high_open_share, tone, payload, updated_at) "
            "VALUES ('20261009', 0, 0, '', '{}', '')"
        )
        conn.commit()
    rows = shadow_health(5)
    assert [r["date"] for r in rows] == ["2026-10-09", "2026-10-08"]
    today, prev = rows
    assert today["cards"] == 2 and today["lit"] == 1 and today["missing"] == []
    assert today["lit_fields"]["first_ready_px"] == 1.0 and today["lit_fields"]["book_ready"] == 0.0
    assert today["auction"] == 1 and today["crowd_max"]["industry"]["share"] == 7.1
    assert "stop_atr" in prev["missing"] and "cf" not in prev["missing"]

    chk = shadow_check(rows, {"pool": "live", "concepts": 20})
    assert chk["level"] == "warn" and "电子器件 7.1%" in chk["detail"]
    assert shadow_check(rows, {"pool": "snap", "concepts": 400})["level"] == "ok"
    assert shadow_check(rows[1:], None)["level"] == "bad"


def test_narrative_rows_keep_pool_of_first_sighting(monkeypatch, tmp_path) -> None:
    _setup(monkeypatch, tmp_path)
    with _connect() as conn:
        _card(conn, "2026-10-12", "600001", {"cf": {}})
        conn.commit()
    cl = [{"label": "固态电池", "zt_n": 3, "industries": 2, "concepts": ["固态电池"]}]
    desk_db.upsert_narrative_shadow("2026-10-12", cl, at="10:00:00", mainline="", pool="snap")
    desk_db.upsert_narrative_shadow("2026-10-12", cl, at="10:05:00", mainline="", pool="live")
    desk_db.upsert_narrative_shadow(
        "2026-10-12", [{**cl[0], "label": "机器人"}], at="10:05:00", mainline="", pool="live"
    )
    rows = desk_db.load_narrative_shadow("2026-10-12")
    assert {r["label"]: r["pool"] for r in rows} == {"固态电池": "snap", "机器人": "live"}
    day = shadow_health(1)[0]
    assert day["narrative"] == 2 and day["narrative_snap"] == 1
    assert "叙事2（快照池 1）" in shadow_check([day], {"pool": "snap", "concepts": 120})["detail"]
