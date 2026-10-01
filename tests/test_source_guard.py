"""Board-source flip guard: half-session stickiness, restore streak, toast damping."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import market_desk.eastmoney as em
from market_desk import board_fallback as bf
from market_desk.eastmoney import boards as em_boards
from market_desk.engine.source_guard import SourceGuardMixin
from market_desk.notify import build_damped_toast_alerts

EM_ROWS = [{"bk": "BK0900", "name": "半导体", "kind": "industry", "pct": 2.0}]
SINA_ROWS = [{"bk": "SINA:new_dzqj", "name": "电子器件", "kind": "industry", "pct": 2.1}]


@pytest.fixture()
def feed(monkeypatch):
    """Script East Money responses; Sina always answers. Returns the control dict."""
    ctl = {"em_ok": True, "em_calls": 0, "window": True}
    monkeypatch.setattr(em_boards, "_BOARDS_SOURCE", "eastmoney")
    monkeypatch.setattr(em_boards, "_BOARDS_CLIST_PAUSE_UNTIL", 0.0)
    monkeypatch.setattr(em_boards, "_BOARDS_RESTORE_OK", 0)
    monkeypatch.setattr(em_boards, "_BOARDS_FAIL_STREAK", 0)
    monkeypatch.setattr(em_boards, "_BOARDS_SWITCH_DAY", "")
    monkeypatch.setattr(em_boards, "_BOARDS_SWITCH_LOG", [])
    monkeypatch.setattr(em_boards, "in_decision_window", lambda now=None: ctl["window"])

    async def clist(_client):
        ctl["em_calls"] += 1
        return list(EM_ROWS) if ctl["em_ok"] else []

    async def sina(_client):
        return list(SINA_ROWS)

    monkeypatch.setattr(em_boards, "_hot_boards_from_clist", clist)
    monkeypatch.setattr(bf, "fetch_hot_boards_sina", sina)
    return ctl


def _fetch():
    return asyncio.run(em_boards.fetch_hot_boards(None))


def test_single_in_session_blip_reuses_last_cards(feed) -> None:
    feed["em_ok"] = False
    assert _fetch() == [], "1st in-session failure → empty → refresh keeps last cards"
    assert em_boards._BOARDS_SOURCE == "eastmoney"
    feed["em_ok"] = True
    assert _fetch() == EM_ROWS
    assert em_boards._BOARDS_FAIL_STREAK == 0 and em.boards_switch_stats()["switches"] == 0


def test_degrade_locks_sina_until_break_then_needs_two_probes(feed) -> None:
    feed["em_ok"] = False
    _fetch()
    assert _fetch() == SINA_ROWS and em_boards._BOARDS_SOURCE == "sina"
    stats = em.boards_switch_stats()
    assert stats["switches"] == 1 and stats["locked"] is True

    feed["em_ok"] = True
    em_boards._BOARDS_CLIST_PAUSE_UNTIL = 0.0
    calls = feed["em_calls"]
    assert _fetch() == SINA_ROWS, "EM healthy again but still inside the half-session"
    assert feed["em_calls"] == calls, "no EM probe while locked"

    feed["window"] = False
    assert _fetch() == SINA_ROWS, "1st break probe succeeds but keeps Sina"
    assert em_boards._BOARDS_RESTORE_OK == 1
    assert _fetch() == EM_ROWS and em_boards._BOARDS_SOURCE == "eastmoney"
    stats = em.boards_switch_stats()
    assert stats["switches"] == 2 and [r["to"] for r in stats["log"]] == ["sina", "eastmoney"]


def test_break_failure_resets_restore_streak(feed) -> None:
    feed["window"] = False
    em_boards._BOARDS_SOURCE = "sina"
    _fetch()
    assert em_boards._BOARDS_RESTORE_OK == 1
    feed["em_ok"] = False
    _fetch()
    assert em_boards._BOARDS_RESTORE_OK == 0 and em_boards._BOARDS_SOURCE == "sina"
    assert 0 < em_boards._BOARDS_CLIST_PAUSE_UNTIL, "break retry pause set"


def test_off_session_failure_degrades_at_once(feed) -> None:
    feed["window"] = False
    feed["em_ok"] = False
    assert _fetch() == SINA_ROWS and em_boards._BOARDS_SOURCE == "sina"


def _snap(action: str, ml: str, *, code: str = "600001") -> dict:
    return {
        "ok": True,
        "phase": "发酵",
        "verdict": {
            "action": action,
            "mainline": {"name": ml},
            "recommend": {"primary": {"code": code, "name": "样本", "buy_price": 10.0}},
        },
    }


def test_damping_drops_board_edges_and_replays_surviving_buy() -> None:
    base = _snap("观望", "半导体")
    flipped = _snap("可买入", "电子器件")
    keys = [a[0] for a in build_damped_toast_alerts(base, flipped, damping=True)]
    assert not any(k.startswith(("buy:", "mainline:")) for k in keys)

    still = _snap("可买入", "电子器件")
    plain = [a[0] for a in build_damped_toast_alerts(flipped, still)]
    assert plain == [], "no edge vs the last damped round"
    replay = [a[0] for a in build_damped_toast_alerts(flipped, still, replay_base=base)]
    assert replay == ["buy:600001"], "buy survived the window → fires once; mainline rename never"


def test_replay_skips_buy_that_faded_inside_window() -> None:
    base = _snap("观望", "半导体")
    faded = _snap("观望", "电子器件")
    keys = [a[0] for a in build_damped_toast_alerts(faded, faded, replay_base=base)]
    assert keys == []


class _Guard(SourceGuardMixin):
    def __init__(self) -> None:
        self._src_last = ""
        self._src_shift_from = ""
        self._src_damp_until = 0.0
        self._src_damp_base = None
        self._src_probe_at = 0.0


def test_tracker_opens_window_and_hands_back_pre_flip_base(monkeypatch) -> None:
    src = {"v": "eastmoney"}
    monkeypatch.setattr(
        em, "boards_switch_stats", lambda: {"source": src["v"], "switches": 0, "log": [], "locked": False}
    )
    g = _Guard()
    t0 = datetime(2026, 9, 30, 10, 0, 0).timestamp()

    def at(sec: float) -> datetime:
        return datetime.fromtimestamp(t0 + sec)

    pre = {"ok": True, "tag": "pre"}
    state, replay = g._track_boards_source(at(0), pre)
    assert not state["damping"] and replay is None, "first sighting is not a flip"

    src["v"] = "sina"
    state, replay = g._track_boards_source(at(20), pre)
    assert state["damping"] and state["shift_from"] == "eastmoney" and replay is None
    state, _ = g._track_boards_source(at(100), {"ok": True, "tag": "mid"})
    assert state["damping"] and 0 < state["damp_left"] <= 180

    state, replay = g._track_boards_source(at(220), {"ok": True, "tag": "mid2"})
    assert not state["damping"] and replay == pre, "base handed back once"
    _, replay = g._track_boards_source(at(240), {"ok": True})
    assert replay is None
