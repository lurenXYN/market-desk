"""Agent learning pack: proposals + sync assembly."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.agent_pack import AGENT_PACK_SCHEMA, _item_proposal, build_agent_pack_sync
from market_desk.db.sim_desk import ensure_sim_desk_tables, get_sim_account, set_sim_account_epoch
from market_desk.config import SIM_BOOK_SINCE, SIM_INITIAL_CASH


def _init_db(tmp_path, monkeypatch):
    db = tmp_path / "agent.db"
    monkeypatch.setattr(desk_db, "DB_PATH", db)
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    with desk_db._connect() as conn:
        ensure_sim_desk_tables(conn)
        conn.commit()
    get_sim_account(SIM_INITIAL_CASH)
    set_sim_account_epoch(str(SIM_BOOK_SINCE)[:10])


def test_item_proposal_watch_vs_judge():
    watch = _item_proposal(
        {
            "id": "edge_reclaim",
            "title": "站回",
            "sample": {"enough_for_todo": False, "days": 3, "n": 10},
            "ui_verdict": "不足",
        }
    )
    assert watch and watch["action"] == "watch"
    judge = _item_proposal(
        {
            "id": "edge_reclaim",
            "title": "站回",
            "level": "ok",
            "sample": {"enough_for_todo": True, "days": 12, "n": 40},
            "ui_verdict": "站回更优",
            "hint": "并表",
            "metrics": {"delta": 1.2},
        }
    )
    assert judge and judge["action"] == "judge" and judge["metrics"]["delta"] == 1.2
    assert _item_proposal({"skip": True, "id": "x"}) is None


def test_build_agent_pack_sync(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "market_desk.routes.ops.build_ops_check",
        lambda: {"ok": True, "overall": "ok", "checks": [], "host": "test", "pid": 1},
    )
    pack = build_agent_pack_sync()
    assert pack["ok"] is True
    assert pack["schema"] == AGENT_PACK_SCHEMA
    assert "proposals" in pack and "review_check" in pack and "ops_check" in pack
    assert "sim" in pack and "deep_v" in pack and "zt_share" in pack
    assert any(p.get("id") == "sim_desk_gate" for p in pack["proposals"])
    assert pack["curl"]["admin"].startswith("GET /api/ops/agent-pack")
