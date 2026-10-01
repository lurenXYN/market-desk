"""Sina → East Money alias table: seed / normalize / learned resolution, blocklist, overlap learning."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

import market_desk.board_alias as ba
import market_desk.board_fallback as bf
import market_desk.db as desk_db
from market_desk.engine.alias_learn import AliasLearnMixin

EM = {
    "半导体": ("BK1036", "industry"),
    "钠离子电池": ("BK1100", "concept"),
    "电力": ("BK0428", "industry"),
    "鸡肉概念": ("BK0900", "concept"),
    "鸡肉": ("BK0901", "industry"),
}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ba, "_LEARNED", {})
    monkeypatch.setattr(ba, "_NORM_CACHE", None)


def test_seed_targets_exist_and_blocklist_is_disjoint() -> None:
    seed = json.loads(ba.ALIAS_SEED_PATH.read_text(encoding="utf-8"))
    names = {n for n, _k in json.loads(bf.SEED_PATH.read_text(encoding="utf-8"))["boards"].values()}
    missing = [f"{s}→{t}" for s, t in seed["alias"].items() if t not in names]
    assert not missing, f"alias targets not in em_boards.json: {missing}"
    assert not set(seed["alias"]) & set(seed["block"])
    assert len(set(seed["alias"].values())) == len(seed["alias"]), "one EM board per seed alias"


def test_resolution_order() -> None:
    assert ba.resolve_sina_board("半导体", "industry", EM)["how"] == "exact"
    seed = ba.resolve_sina_board("钠电池", "concept", EM)
    assert seed["how"] == "seed" and seed["bk"] == "BK1100" and seed["name"] == "钠离子电池"
    norm = ba.resolve_sina_board("电力行业", "industry", EM)
    assert norm["how"] == "norm" and norm["name"] == "电力"
    assert ba.resolve_sina_board("鸡肉", "concept", EM)["how"] == "exact"
    same_kind = ba.resolve_sina_board("鸡肉板块", "concept", EM)
    assert same_kind["bk"] == "BK0900", "normalized match prefers the same kind"
    assert ba.resolve_sina_board("电子器件", "industry", EM) is None

    ba.set_learned_aliases([
        {"sina_name": "电子器件", "bk": "bk1036", "em_name": "半导体", "em_kind": "industry", "status": "approx"},
        {"sina_name": "触摸屏", "bk": "BK0999", "em_name": "触控", "em_kind": "concept", "status": "candidate"},
    ])
    hit = ba.resolve_sina_board("电子器件", "industry", EM)
    assert hit["how"] == "learned" and hit["approx"] and hit["bk"] == "BK1036"
    assert ba.resolve_sina_board("触摸屏", "concept", EM) is None, "candidates are never applied"


def _sina_parts(node: str, name: str) -> list[str]:
    return [node, name, "10", "5", "0.1", "1.5", "1", "2000", "sh600001", "9.9", "10", "1", "样本"]


def test_board_rows_alias_block_and_collision(monkeypatch) -> None:
    monkeypatch.setattr(bf, "_EM_BY_NAME", dict(EM))
    ba.set_learned_aliases([
        {"sina_name": "电子器件", "bk": "BK1036", "em_name": "半导体", "em_kind": "industry", "status": "approx"},
    ])
    rows = {
        "industry": [_sina_parts("new_dlhy", "电力行业"), _sina_parts("new_dzqj", "电子器件"),
                     _sina_parts("new_kfq", "开发区")],
        "concept": [_sina_parts("gn_ndc", "钠电池"), _sina_parts("gn_bdt", "半导体"),
                    _sina_parts("gn_hHg", "含H股")],
    }
    monkeypatch.setattr(bf, "_BOARDS_CACHE", None)
    monkeypatch.setattr(bf, "_NODE_BY_KEY", {})
    monkeypatch.setattr(bf, "_NODE_BY_NAME", {})
    monkeypatch.setattr(bf, "_parse_sina_board_js", lambda text: rows[text])

    class _Resp:
        def __init__(self, kind: str) -> None:
            self.content = kind.encode("gbk")

        def raise_for_status(self) -> None:
            return None

    class _Client:
        async def get(self, url, **_k):
            return _Resp("industry" if "newSinaHy" in url else "concept")

    out = asyncio.run(bf.fetch_hot_boards_sina(_Client()))
    assert [r["sina_node"] for r in out] == ["new_dlhy", "new_dzqj", "gn_ndc", "gn_bdt"], "list order kept"
    by_name = {r["name"]: r for r in out}
    assert "开发区" not in by_name and "含H股" not in by_name, "blocklisted"
    assert by_name["电力"]["bk"] == "BK0428" and by_name["电力"]["sina_name"] == "电力行业"
    assert by_name["钠离子电池"]["alias"] == "seed"
    exact = by_name["半导体"]
    assert exact["bk"] == "BK1036" and exact["sina_node"] == "gn_bdt", "exact name beats an approx alias"
    loser = by_name["电子器件"]
    assert loser["bk"] == "SINA:new_dzqj" and "alias_approx" not in loser and "sina_name" not in loser
    assert bf._NODE_BY_KEY["BK1036"] == "gn_bdt"
    stats = bf.alias_stats()
    assert stats == {"total": 4, "exact": 1, "alias": 2, "approx": 0, "sina": 1, "blocked": 2}

    monkeypatch.setattr(bf, "_BOARDS_CACHE", None)
    rows["concept"] = [_sina_parts("gn_ndc", "钠电池")]
    out = asyncio.run(bf.fetch_hot_boards_sina(_Client()))
    approx = {r["name"]: r for r in out}["半导体"]
    assert approx["alias_approx"] and approx["sina_name"] == "电子器件" and approx["bk"] == "BK1036"


def _codes(lo: int, hi: int) -> set[str]:
    return {f"{i:06d}" for i in range(lo, hi)}


def test_learn_aliases_statuses() -> None:
    em = {
        "BK0001": {"name": "电池", "kind": "industry", "codes": _codes(0, 40)},
        "BK0002": {"name": "电子", "kind": "industry", "codes": _codes(0, 400)},  # SW L1 → excluded
        "BK0003": {"name": "固态电池", "kind": "concept", "codes": _codes(100, 120)},
        "BK0004": {"name": "储能", "kind": "concept", "codes": _codes(200, 230)},
    }
    sina = {
        "锂电": {"kind": "concept", "codes": _codes(100, 121)},          # J≈0.95 → auto
        "老电器": {"kind": "industry", "codes": _codes(0, 120)},          # J=40/120 → approx
        "储能概念": {"kind": "concept", "codes": _codes(200, 215) | _codes(500, 515)},  # J≈0.33 → candidate
        "微小": {"kind": "concept", "codes": _codes(0, 3)},               # too few members
        "锂电2": {"kind": "concept", "codes": _codes(100, 125)},         # contests BK0003 → candidate
    }
    rows = {r["sina_name"]: r for r in ba.learn_aliases(sina, em)}
    assert rows["锂电"]["status"] == "auto" and rows["锂电"]["bk"] == "BK0003"
    assert rows["老电器"]["status"] == "approx" and rows["老电器"]["bk"] == "BK0001"
    assert rows["储能概念"]["status"] == "candidate"
    assert "微小" not in rows
    assert rows["锂电2"]["status"] == "candidate", "one EM board serves one Sina board"
    assert all(r["bk"] != "BK0002" for r in rows.values())


def test_alias_db_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    desk_db.save_board_members_snap("em", "BK0001", ["000002", "000001", "000001"], name="电池", kind="industry")
    snap = desk_db.load_board_members_snap("em")
    assert snap["BK0001"]["codes"] == {"000001", "000002"} and snap["BK0001"]["n"] == 2
    assert "codes" not in desk_db.load_board_members_snap("em", with_codes=False)["BK0001"]
    desk_db.replace_board_alias_learned([
        {"sina_name": "老电器", "bk": "BK0001", "em_name": "电池", "status": "approx", "jaccard": 0.33},
    ])
    desk_db.replace_board_alias_learned([
        {"sina_name": "锂电", "bk": "BK0003", "em_name": "固态电池", "status": "auto", "jaccard": 0.95},
    ])
    rows = desk_db.load_board_alias_learned()
    assert [r["sina_name"] for r in rows] == ["锂电"], "a learning pass replaces the table"


class _Learner(AliasLearnMixin):
    def __init__(self) -> None:
        self._alias_tick_at = 0.0
        self._alias_em_backoff_until = 0.0
        self._alias_learned_day = ""
        self._alias_progress = {}


def test_em_snapshot_backs_off_after_two_empties(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    monkeypatch.setattr(bf, "_EM_BY_NAME", {"甲": ("BK0001", "concept"), "乙": ("BK0002", "concept"),
                                             "丙": ("BK0003", "concept"), "丁": ("BK0004", "concept")})
    answers = {"BK0001": [], "BK0002": ["000001"], "BK0003": [], "BK0004": []}

    async def fake_codes(_client, bk):
        return list(answers[bk])

    import market_desk.eastmoney as em
    from market_desk.engine import alias_learn

    monkeypatch.setattr(em, "fetch_board_codes_em", fake_codes)
    monkeypatch.setattr(alias_learn, "ALIAS_LEARN_EM_PER_TICK", 4)
    g = _Learner()
    now = datetime(2026, 10, 3, 20, 0, 0)
    todo = asyncio.run(g._alias_snap_em(None, now))
    assert todo == 4
    snap = desk_db.load_board_members_snap("em", with_codes=False)
    assert set(snap) == {"BK0001", "BK0002"}, "single empty saved once a later board succeeds"
    assert snap["BK0001"]["n"] == 0
    assert g._alias_em_backoff_until > now.timestamp(), "two empties in a row → back off"
    assert asyncio.run(g._alias_snap_em(None, now)) == 2
    assert set(desk_db.load_board_members_snap("em", with_codes=False)) == {"BK0001", "BK0002"}


def test_em_snapshot_skips_when_clist_recently_blocked(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    monkeypatch.setattr(bf, "_EM_BY_NAME", {"甲": ("BK0001", "concept")})
    calls: list[str] = []

    async def fake_codes(_client, bk):
        calls.append(bk)
        return ["000001"]

    import market_desk.eastmoney as em
    from market_desk.eastmoney import avail

    monkeypatch.setattr(em, "fetch_board_codes_em", fake_codes)
    for _ in range(3):
        avail.note("push2/clist", False, "RemoteProtocolError")
    g = _Learner()
    now = datetime(2026, 10, 3, 20, 0, 0)
    assert asyncio.run(g._alias_snap_em(None, now)) == 1
    assert calls == [] and g._alias_em_backoff_until > now.timestamp(), "blocked edge → no crawl"
