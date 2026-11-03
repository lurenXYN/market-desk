"""Agent learning pack: proposals + sync assembly."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.agent_pack import (
    AGENT_PACK_SCHEMA,
    _item_proposal,
    build_adapt_live_snapshot,
    build_agent_pack_sync,
)
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


def test_item_proposal_sample_verdict():
    watch = _item_proposal(
        {
            "id": "edge_reclaim",
            "title": "站回",
            "sample": {"enough_for_todo": False, "days": 3, "n": 10},
            "ui_verdict": "不足",
        }
    )
    assert watch and watch["sample"] == "thin" and watch["verdict"] == "watch"
    hold = _item_proposal(
        {
            "id": "edge_reclaim",
            "title": "站回",
            "level": "ok",
            "sample": {"enough_for_todo": True, "days": 12, "n": 40},
            "ui_verdict": "站回更优",
            "hint": "并表",
            "metrics": {"delta": 1.2, "abs_win": 40.0},
        }
    )
    assert hold and hold["sample"] == "ready" and hold["verdict"] == "hold"
    assert hold["wins"]["abs_win"] == 40.0
    soft = _item_proposal(
        {
            "id": "narrative",
            "title": "叙事",
            "sample": {"enough_for_todo": True, "days": 14, "n": 100},
            "metrics": {"hit_rate": 0.55, "median_lead_min": 30, "days_with_cluster": 10},
        }
    )
    assert soft and soft["verdict"] == "soft_candidate"
    assert _item_proposal({"skip": True, "id": "x"}) is None


def test_adapt_live_snapshot_rebuilt(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    snap = build_adapt_live_snapshot([])
    assert snap["ok"] is True
    assert snap["warm_source"] == "rebuilt"
    assert "size_mult" in snap["size_heat"]
    assert "size_mult" in snap["exec_size"]
    assert snap["buy_gate"]["schema"] == "buy_gate_bias.v2"
    assert "minute" in snap["buy_gate"]["live_resolved"]["stock"]
    assert "minute" in snap["buy_gate"]["live_resolved"]["etf"]
    assert snap["env_gate_live"] is False
    assert snap["pullback_sweet"] is not None
    assert "sweet_max" in snap["pullback_sweet"]
    assert snap["auto_tune"] is not None
    assert "pb_min_mult" in snap["auto_tune"]
    assert snap["sticky_margin"] is not None
    assert "mult" in snap["sticky_margin"]
    assert snap["sell_mfe"] is not None
    assert snap["segment_sell"] is not None
    assert "mult" in snap["segment_sell"]


def test_adapt_live_prefers_warm_engine(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    class _Eng:
        snapshot = {
            "verdict": {
                "adapt": {
                    "ok": True,
                    "outcome_basis": "classic",
                    "outcome_basis_note": "warm",
                    "size_heat": {
                        "ok": True,
                        "n": 8,
                        "size_mult": 0.5,
                        "consec_loss": 3,
                        "note": "连亏3",
                    },
                    "exec_size": {"ok": True, "n": 8, "size_mult": 0.85, "score": 60, "note": "执行一般"},
                    "size_mult": 0.42,
                    "size_compose": {
                        "size_mult": 0.42,
                        "raw_product": 0.425,
                        "clamped": False,
                        "factors": [],
                        "note": "合流×0.42",
                    },
                    "size_factors": [
                        {"key": "heat", "label": "仓位热度", "mult": 0.5},
                        {"key": "exec", "label": "执行分", "mult": 0.85},
                    ],
                    "context_gate": {"ok": False},
                }
            }
        }

    monkeypatch.setattr("market_desk.engine.engine", _Eng())
    snap = build_adapt_live_snapshot([])
    assert snap["warm_source"] == "engine.snapshot"
    assert snap["size_heat"]["size_mult"] == 0.5
    assert snap["exec_size"]["size_mult"] == 0.85
    assert snap["size_mult"] == 0.42


def test_build_agent_pack_sync(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "market_desk.routes.ops.build_ops_check",
        lambda: {"ok": True, "overall": "ok", "checks": [], "host": "test", "pid": 1},
    )
    pack = build_agent_pack_sync()
    assert pack["ok"] is True
    assert pack["schema"] == AGENT_PACK_SCHEMA
    assert pack["schema"] == "agent_pack.v5"
    assert "proposals" in pack and "review_check" in pack and "dual_wins" in pack
    assert "sim" in pack and pack["sim"].get("vs_bench") is not None
    assert any(p.get("id") == "sim_desk_gate" for p in pack["proposals"])
    assert "proposals_hold" in pack["summary"]
    assert pack["curl"]["admin"].startswith("GET /api/ops/agent-pack")
    assert pack["adapt_live"]["ok"] is True
    assert "size_heat" in pack["adapt_live"]
    assert "exec_size" in pack["adapt_live"]
    assert "buy_gate" in pack["adapt_live"]
    assert "auto_tune" in pack["adapt_live"]
    assert "sticky_margin" in pack["adapt_live"]
    assert "adapt_size_mult" in pack["summary"]
    assert pack["adapt_effect"]["ok"] is True
    assert "scorecard" in pack["adapt_effect"]
    assert "miss_trend" in pack["adapt_effect"]
    assert "np_review" in pack
    assert pack["np_review"].get("verdict") in (None, "insufficient", "hold", "soft_candidate")
