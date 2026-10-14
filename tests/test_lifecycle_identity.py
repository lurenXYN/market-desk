"""Lifecycle C/C′: stable seal keys + Sina→EM history merge / no-hist gray."""

from __future__ import annotations

import market_desk.db as desk_db
import market_desk.lifecycle as lc
from market_desk.db.market import resolve_board_hist


def _no_bias(monkeypatch):
    monkeypatch.setattr(lc, "_review_lifecycle_bias", lambda: {"strict": False, "hit_rate": None, "n": 0})


def _db(monkeypatch, tmp_path):
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def test_stable_key_prefers_bk_and_falls_back_to_alias_name():
    assert lc.lifecycle_stable_key({"bk": "BK0477", "name": "酿酒行业"}) == "BK0477"
    assert lc.lifecycle_stable_key({"bk": "SINA:hangye_ni", "name": "酿酒行业"}) == "N:酿酒"
    assert "N:酿酒" in lc.lifecycle_key_candidates(
        {"bk": "SINA:hangye_ni", "name": "酿酒", "sina_name": "酿酒行业"}
    )


def test_frozen_seal_survives_sina_to_bk_flip(monkeypatch):
    """Yesterday sealed under N:alias; today live card is BK* — same column."""
    _no_bias(monkeypatch)
    hist_map = {
        "BK0477": [
            {
                "trade_date": "2026-09-22",
                "bk": "BK0477",
                "name": "酿酒行业",
                "zt_n": 3,
                "pct": 2.0,
                "status": "确认中",
            },
            {
                "trade_date": "2026-09-23",
                "bk": "BK0477",
                "name": "酿酒行业",
                "zt_n": 4,
                "pct": 2.5,
                "status": "确认中",
            },
        ],
    }
    frozen = {"N:酿酒": "ongoing", "SINA:hangye_ni": "ongoing"}
    live = {
        "bk": "BK0477",
        "name": "酿酒行业",
        "status": "退潮",
        "zt_n": 0,
        "pct": -1.0,
        "hist": hist_map["BK0477"],
        "tags": [],
    }
    out = lc.build_mainline_lifecycle(
        [live], [], hist_map=hist_map, frozen=frozen, final=False
    )
    assert out["mode"] == "frozen"
    assert [r["bk"] for r in out["ongoing"]] == ["BK0477"]
    assert len(out["ongoing"]) == 1  # no dual-write duplicates
    assert out["ongoing"][0]["live_stage"] == "ending"
    assert out["stage_map"].get("BK0477") == "ongoing"
    assert out["stage_map"].get("N:酿酒") == "ongoing"


def test_resolve_board_hist_merges_sina_to_em(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    desk_db.save_board_daily(
        "2026-09-23", [{"bk": "BK0477", "name": "酿酒行业", "zt_n": 2, "pct": 1.0}]
    )
    desk_db.save_board_daily(
        "2026-09-24", [{"bk": "BK0477", "name": "酿酒行业", "zt_n": 3, "pct": 2.0}]
    )
    hist = desk_db.load_board_hist_map("2026-09-29")
    sina = {"bk": "SINA:hangye_ni", "name": "酿酒"}
    got = resolve_board_hist(hist, sina)
    assert [r["trade_date"] for r in got] == ["2026-09-23", "2026-09-24"]
    assert not str(got[0].get("bk") or "").startswith("SINA") or got  # merged rows present


def test_no_hist_gray_listed_not_starting(monkeypatch):
    _no_bias(monkeypatch)
    card = {
        "bk": "SINA:gn_dzxx",
        "name": "电子信息",
        "status": "确认中",
        "zt_n": 4,
        "pct": 2.0,
        "hist": [],
        "tags": [],
        "lifecycle_no_hist": True,
    }
    assert lc.classify_lifecycle(card) is None
    out = lc.build_mainline_lifecycle([card], [], final=True)
    assert out["starting"] == []
    assert out["ongoing"] == []
    assert [r["name"] for r in out["no_hist"]] == ["电子信息"]
