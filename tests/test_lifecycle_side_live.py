"""Frozen lifecycle: boards missing from today's hot list get their own live fetch."""

from __future__ import annotations

import asyncio

import market_desk.board_fallback as board_fallback
import market_desk.engine.cards as engine_mod
import market_desk.lifecycle as lc
from market_desk.engine import DeskEngine


def _no_bias(monkeypatch):
    monkeypatch.setattr(lc, "_review_lifecycle_bias", lambda: {"strict": False, "hit_rate": None, "n": 0})


def _hist(bk: str, rows: list[tuple[str, int, float, str]]):
    return [
        {"trade_date": d, "bk": bk, "name": bk, "zt_n": z, "pct": p, "status": s, "leader_boards": 1}
        for d, z, p, s in rows
    ]


HIST = {
    "BK1": _hist("BK1", [
        ("2026-09-23", 3, 2.0, "确认中"),
        ("2026-09-24", 4, 2.5, "确认中"),
        ("2026-09-25", 5, 3.0, "确认中"),
    ]),
}


def test_frozen_off_hot_without_side_uses_last_close(monkeypatch):
    _no_bias(monkeypatch)
    out = lc.build_mainline_lifecycle([], [], hist_map=HIST, frozen={"BK1": "ongoing"}, final=False)
    row = out["ongoing"][0]
    assert row["off_hot"] is True
    assert "上一收盘" in row["live_hint"]


def test_frozen_off_hot_side_card_is_live(monkeypatch):
    _no_bias(monkeypatch)
    side = {"bk": "BK1", "name": "BK1", "status": "确认中", "zt_n": 6, "pct": 3.4,
            "hist": HIST["BK1"], "tags": []}
    out = lc.build_mainline_lifecycle(
        [], [], hist_map=HIST, frozen={"BK1": "ongoing"}, final=False, side_live={"BK1": side}
    )
    assert [r["bk"] for r in out["ongoing"]] == ["BK1"]
    row = out["ongoing"][0]
    assert row["off_hot"] is True
    assert row["zt_n"] == 6
    assert row["live_hint"].startswith("今日未进热门 · 盘中实时")


def test_side_live_ignored_when_board_is_hot(monkeypatch):
    _no_bias(monkeypatch)
    hot = {"bk": "BK1", "name": "BK1", "status": "确认中", "zt_n": 7, "pct": 4.0,
           "hist": HIST["BK1"], "tags": []}
    side = dict(hot, zt_n=1)
    out = lc.build_mainline_lifecycle(
        [hot], [], hist_map=HIST, frozen={"BK1": "ongoing"}, final=False, side_live={"BK1": side}
    )
    row = out["ongoing"][0]
    assert row["zt_n"] == 7
    assert not row.get("off_hot")


def test_side_cards_lookup_and_sina_fallback(monkeypatch):
    enriched: list[str] = []

    async def fake_enrich(client, board, ctx, weakest=False):
        enriched.append(board["bk"])
        pool = [] if board["bk"] == "BK3" else [{"code": "600001"}]
        return dict(board, zt_n=2, pool=pool)

    async def fake_sina(client):
        return [{"bk": "SINA:x", "name": "二号"}, {"bk": "BK3", "name": "三号"}]

    monkeypatch.setattr(engine_mod, "_enrich_board", fake_enrich)
    monkeypatch.setattr(board_fallback, "fetch_hot_boards_sina", fake_sina)
    lifecycle = {
        "starting": [{"bk": "BK1", "name": "一号", "off_hot": True}],
        "ongoing": [
            {"bk": "BK2", "name": "二号", "off_hot": True},
            {"bk": "BK9", "name": "热门", "off_hot": False},
        ],
        "ending": [
            {"bk": "BK3", "name": "三号", "off_hot": True},
            {"bk": "BK4", "name": "查无", "off_hot": True},
        ],
    }
    boards = [{"bk": "bk1", "name": "一号"}]
    eng = DeskEngine.__new__(DeskEngine)
    out = asyncio.run(eng._lifecycle_side_cards(None, boards, lifecycle, {}))
    # BK1 from this tick, BK2 matched by name in Sina, BK3 dropped (no members), BK4 unknown.
    assert set(out) == {"BK1", "BK2"}
    assert sorted(enriched) == ["BK1", "BK2", "BK3"]
    assert out["BK2"]["bk"] == "BK2"
