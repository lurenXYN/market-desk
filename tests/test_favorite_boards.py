"""Favorite boards: per-user flags, off-hot stubs, union lifecycle cache."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.db import add_favorite_board, load_all_favorite_boards, load_favorite_boards
from market_desk.engine.boards import (
    FAV_OFF_HOT_NOTE,
    _mark_favorite_flags,
    decorate_favorite_rows,
)
from market_desk.engine.core import DeskEngine
from market_desk.personal import attach_personal_layer
from market_desk.verdict.desk import empty_favorite_desk, merge_favorite_desk_buys


def _db(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()


def test_load_all_favorite_boards_union_newest_bk(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    u1 = desk_db.create_user("alice", "x", status="active")
    u2 = desk_db.create_user("bob", "x", status="active")
    add_favorite_board("BK0001", "旧名", user_id=int(u1["id"]))
    add_favorite_board("BK0001", "新名", user_id=int(u2["id"]))
    add_favorite_board("BK0002", "乙板", user_id=int(u1["id"]))
    assert load_favorite_boards() == []
    union = load_all_favorite_boards()
    by_bk = {r["bk"]: r for r in union}
    assert set(by_bk) == {"BK0001", "BK0002"}
    assert by_bk["BK0001"]["name"] == "新名"
    assert load_favorite_boards(user_id=int(u1["id"]))[0]["bk"] in {"BK0001", "BK0002"}


def test_decorate_stub_off_hot_and_cancel_flags() -> None:
    rows = [
        {
            "id": 7,
            "bk": "BK0729",
            "name": "虚拟机器人",
            "kind": "concept",
            "note": "",
        }
    ]
    out = decorate_favorite_rows(rows, [], hot_universe=[])
    assert len(out) == 1
    card = out[0]
    assert card["in_favorite"] is True
    assert card["favorite_id"] == 7
    assert card["fav"] is True
    assert card["off_hot"] is True
    assert FAV_OFF_HOT_NOTE in (card.get("note") or "")


def test_decorate_hits_today_hot() -> None:
    rows = [{"id": 3, "bk": "BK0477", "name": "酿酒行业"}]
    hot = [
        {
            "bk": "BK0477",
            "name": "酿酒行业",
            "pct": 2.1,
            "members": [{"name": "茅台", "pct": 1.0}],
        }
    ]
    out = decorate_favorite_rows(rows, [hot], hot_universe=hot)
    assert out[0]["pct"] == 2.1
    assert out[0]["in_favorite"] is True
    assert not out[0].get("off_hot")


def test_mark_favorite_flags_matches_name() -> None:
    cards = [{"bk": "BK0477", "name": "酿酒行业"}]
    _mark_favorite_flags(cards, [{"id": 11, "bk": "", "name": "酿酒行业"}])
    assert cards[0]["in_favorite"] is True
    assert cards[0]["favorite_id"] == 11
    _mark_favorite_flags(cards, [])
    assert cards[0]["in_favorite"] is False
    assert "favorite_id" not in cards[0]


def test_guest_snapshot_strips_favorites() -> None:
    eng = DeskEngine.__new__(DeskEngine)
    eng.snapshot = {
        "ok": True,
        "favorite_boards": [
            {
                "bk": "BK0729",
                "name": "虚拟机器人",
                "in_favorite": True,
                "favorite_id": 9,
            }
        ],
        "favorite_desk": {
            "ok": True,
            "empty": False,
            "boards": [{"name": "虚拟机器人", "bk": "BK0729"}],
        },
        "hot_boards": [
            {
                "bk": "BK0729",
                "name": "虚拟机器人",
                "in_favorite": True,
                "favorite_id": 9,
            }
        ],
        "pin_boards": [],
        "ice_boards": [],
        "verdict": {},
    }
    out = DeskEngine.snapshot_for_user(eng, None)
    assert out["favorite_boards"] == []
    assert (out.get("favorite_desk") or {}).get("boards") == []
    assert out["hot_boards"][0]["in_favorite"] is False
    assert "favorite_id" not in out["hot_boards"][0]
    assert eng.snapshot["hot_boards"][0]["in_favorite"] is True


def test_attach_personal_stamps_hot_and_rebuilds_desk(monkeypatch, tmp_path) -> None:
    _db(monkeypatch, tmp_path)
    user = desk_db.create_user("me", "x", status="active")
    uid = int(user["id"])
    add_favorite_board("BK0729", "虚拟机器人", kind="concept", user_id=uid)
    snap = {
        "ok": True,
        "trade_date": "20261102",
        "phase": "发酵",
        "hot_boards": [{"bk": "BK0477", "name": "酿酒", "in_favorite": False}],
        "pin_boards": [],
        "ice_boards": [],
        "etfs": [],
        "watch": [],
        "verdict": {
            "bans": [],
            "stock_block": False,
            "mainline": {"name": "酿酒"},
        },
        "metrics": {},
        "favorite_boards": [],
        "favorite_desk": empty_favorite_desk(),
        "positions": [],
    }
    out = attach_personal_layer(
        snap,
        uid,
        decorate_favorites=lambda rows: decorate_favorite_rows(
            rows,
            [snap["hot_boards"]],
            hot_universe=snap["hot_boards"],
        ),
    )
    fav = out["favorite_boards"]
    assert fav and fav[0]["bk"] == "BK0729"
    assert fav[0]["in_favorite"] is True
    assert fav[0]["off_hot"] is True
    assert FAV_OFF_HOT_NOTE in (fav[0].get("note") or "")
    assert out["hot_boards"][0]["in_favorite"] is False
    desk = out["favorite_desk"]
    assert desk.get("empty") is False
    assert desk["boards"][0]["bk"] == "BK0729"


def test_merge_favorite_desk_keeps_user_sell() -> None:
    user = {
        "ok": True,
        "boards": [
            {
                "bk": "BK0729",
                "buy": {"title": "user"},
                "sell": {"title": "my-pos"},
            }
        ],
    }
    shared = {
        "boards": [
            {
                "bk": "BK0729",
                "buy": {"title": "shared-enrich"},
                "sell": {"title": "other"},
            }
        ]
    }
    merged = merge_favorite_desk_buys(user, shared)
    assert merged["boards"][0]["buy"]["title"] == "shared-enrich"
    assert merged["boards"][0]["sell"]["title"] == "my-pos"
