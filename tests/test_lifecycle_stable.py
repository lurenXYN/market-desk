"""Lifecycle panel columns stay fixed intraday and move only after the close."""

from __future__ import annotations

import market_desk.lifecycle as lc


def _no_bias(monkeypatch):
    monkeypatch.setattr(lc, "_review_lifecycle_bias", lambda: {"strict": False, "hit_rate": None, "n": 0})


def _hist(bk: str, rows: list[tuple[str, int, float, str]]):
    return [
        {"trade_date": d, "bk": bk, "name": bk, "zt_n": z, "pct": p, "status": s, "leader_boards": 1}
        for d, z, p, s in rows
    ]


def test_frozen_stage_ignores_intraday_flip(monkeypatch):
    _no_bias(monkeypatch)
    hist_map = {
        "BK1": _hist("BK1", [
            ("2026-09-21", 3, 2.0, "确认中"),
            ("2026-09-22", 4, 2.5, "确认中"),
            ("2026-09-23", 5, 3.0, "确认中"),
        ]),
    }
    live_ending = {"bk": "BK1", "name": "BK1", "status": "退潮", "zt_n": 0, "pct": -2.0,
                   "hist": hist_map["BK1"], "tags": []}
    out = lc.build_mainline_lifecycle([live_ending], [], hist_map=hist_map, frozen=None, final=False)
    assert out["mode"] == "frozen"
    assert [r["bk"] for r in out["ongoing"]] == ["BK1"]
    assert out["ending"] == []
    assert out["ongoing"][0]["live_stage"] == "ending"
    assert "盘中阶段像衰退" in out["ongoing"][0]["live_hint"]


def test_frozen_map_wins_and_fresh_not_counted(monkeypatch):
    _no_bias(monkeypatch)
    live_new = {"bk": "BK9", "name": "新题材", "status": "观察", "zt_n": 3, "pct": 2.0,
                "hist": [], "tags": [{"k": "点火", "on": True}]}
    out = lc.build_mainline_lifecycle(
        [live_new], [], hist_map={}, frozen={"BK1": "ending"}, final=False
    )
    assert out["starting"] == []
    assert [r["bk"] for r in out["fresh"]] == ["BK9"]


def test_after_close_uses_live_and_exports_stage_map(monkeypatch):
    _no_bias(monkeypatch)
    live = {"bk": "BK1", "name": "BK1", "status": "退潮", "zt_n": 0, "pct": -2.0,
            "hist": _hist("BK1", [("2026-09-23", 5, 3.0, "确认中")]), "tags": []}
    out = lc.build_mainline_lifecycle([live], [], final=True)
    assert out["mode"] == "close"
    assert out["stage_map"].get("BK1") == "ending"
    assert out["stage_map"].get(lc.lifecycle_stable_key(live)) == "ending"
    assert [r["bk"] for r in out["ending"]] == ["BK1"]


def test_weekend_rows_are_skipped(monkeypatch):
    _no_bias(monkeypatch)
    series = _hist("BK1", [
        ("2026-09-18", 1, 0.5, ""),
        ("2026-09-19", 1, 0.5, ""),
    ])
    closed = lc._closed_series_map({"BK1": series})
    assert [r["trade_date"] for r in closed["BK1"]] == ["2026-09-18"]


def test_tip_peak_is_peak_not_ongoing(monkeypatch):
    _no_bias(monkeypatch)
    hist = _hist("BK1", [
        ("2026-09-21", 3, 2.0, "确认中"),
        ("2026-09-22", 4, 2.5, "确认中"),
        ("2026-09-23", 5, 3.0, "确认中"),
    ])
    card = {
        "bk": "BK1",
        "name": "BK1",
        "status": "尖峰禁追",
        "zt_n": 6,
        "pct": 4.0,
        "hist": hist,
        "tags": [],
    }
    assert lc.classify_lifecycle(card) == "peak"
    out = lc.build_mainline_lifecycle([card], [], final=True)
    assert [r["bk"] for r in out["peak"]] == ["BK1"]
    assert out["ongoing"] == []


def test_first_day_tip_goes_to_starting(monkeypatch):
    """Same-day tip without prior heat is 萌芽, not 尖峰观察."""
    _no_bias(monkeypatch)
    card = {
        "bk": "BK_NEW",
        "name": "新题材",
        "status": "尖峰禁追",
        "zt_n": 5,
        "pct": 5.0,
        "leader_boards": 3,
        "hist": [],
        "tags": [],
    }
    assert lc.classify_lifecycle(card) == "starting"
    out = lc.build_mainline_lifecycle([card], [], final=True)
    assert [r["bk"] for r in out["starting"]] == ["BK_NEW"]
    assert out["peak"] == []


def test_confirm_multi_day_is_ongoing_not_peak(monkeypatch):
    _no_bias(monkeypatch)
    hist = _hist("BK2", [
        ("2026-09-22", 3, 2.0, "确认中"),
        ("2026-09-23", 4, 2.5, "确认中"),
    ])
    card = {
        "bk": "BK2",
        "name": "BK2",
        "status": "确认中",
        "zt_n": 4,
        "pct": 2.0,
        "leader_boards": 1,
        "hist": hist,
        "tags": [],
    }
    assert lc.classify_lifecycle(card) == "ongoing"
    out = lc.build_mainline_lifecycle([card], [], final=True)
    assert [r["bk"] for r in out["ongoing"]] == ["BK2"]
    assert out["peak"] == []


def test_hist_does_not_invent_column_members(monkeypatch):
    _no_bias(monkeypatch)
    hist_map = {
        "BK1": _hist("BK1", [("2026-09-23", 4, 2.0, "确认中")]),
        "BK_JUNK": [
            {
                "trade_date": "2026-09-23",
                "bk": "BK_JUNK",
                "name": "破净股",
                "zt_n": 8,
                "pct": 3.0,
                "status": "确认中",
                "leader_boards": 1,
            }
        ],
        "BK9": _hist("BK9", [("2026-09-23", 5, 3.0, "确认中")]),
    }
    out = lc.build_mainline_lifecycle(
        [], [], hist_map=hist_map, frozen={"BK1": "ongoing"}, final=False
    )
    names = [r["name"] for col in ("starting", "ongoing", "peak", "ending") for r in out[col]]
    assert names == ["BK1"]
    assert out["ongoing"][0]["off_hot"] is True


def test_pseudo_frozen_key_dropped(monkeypatch):
    _no_bias(monkeypatch)
    hist_map = {
        "BK_QFII": [
            {
                "trade_date": "2026-09-23",
                "bk": "BK_QFII",
                "name": "QFII重仓",
                "zt_n": 6,
                "pct": 2.0,
                "status": "确认中",
                "leader_boards": 1,
            }
        ],
        "BK1": _hist("BK1", [("2026-09-23", 4, 2.0, "确认中")]),
    }
    out = lc.build_mainline_lifecycle(
        [],
        [],
        hist_map=hist_map,
        frozen={"BK_QFII": "ongoing", "BK1": "ongoing"},
        final=False,
    )
    names = [r["name"] for col in ("starting", "ongoing", "peak", "ending") for r in out[col]]
    assert names == ["BK1"]


def test_pseudo_not_sealed_on_close(monkeypatch):
    _no_bias(monkeypatch)
    real = {
        "bk": "BK1",
        "name": "电子",
        "status": "确认中",
        "zt_n": 4,
        "pct": 2.0,
        "hist": _hist("BK1", [("2026-09-23", 3, 1.5, "确认中")]),
        "tags": [{"k": "点火", "on": True}],
    }
    junk = {
        "bk": "BK_QFII",
        "name": "QFII重仓",
        "status": "确认中",
        "zt_n": 8,
        "pct": 3.0,
        "hist": _hist("BK_QFII", [("2026-09-23", 6, 2.0, "确认中")]),
        "tags": [{"k": "点火", "on": True}],
    }
    out = lc.build_mainline_lifecycle([real, junk], [], final=True)
    names = [r["name"] for col in ("starting", "ongoing", "peak", "ending") for r in out[col]]
    assert "电子" in names
    assert "QFII重仓" not in names


def test_fav_joins_close_cast(monkeypatch):
    _no_bias(monkeypatch)
    hot = {
        "bk": "BK1",
        "name": "银行",
        "status": "确认中",
        "zt_n": 3,
        "pct": 1.5,
        "hist": [],
        "tags": [{"k": "点火", "on": True}],
    }
    fav = {
        "bk": "BK2",
        "name": "煤炭",
        "status": "确认中",
        "zt_n": 4,
        "pct": 2.0,
        "hist": [],
        "tags": [{"k": "点火", "on": True}],
    }
    out = lc.build_mainline_lifecycle([hot], [], favs=[fav], final=True)
    names = [r["name"] for col in ("starting", "ongoing", "peak", "ending") for r in out[col]]
    assert set(names) == {"银行", "煤炭"}


def test_attach_display_prefers_close_column(monkeypatch):
    _no_bias(monkeypatch)
    verdict = {
        "mainline": {
            "bk": "BK1",
            "name": "半导体",
            "lifecycle": "ending",
            "status": "退潮",
        }
    }
    life = {
        "mode": "frozen",
        "stage_map": {"BK1": "ongoing"},
        "ongoing": [{"bk": "BK1", "name": "半导体", "stage": "ongoing"}],
    }
    lc.attach_mainline_lifecycle_display(verdict, life)
    ml = verdict["mainline"]
    assert ml["lifecycle"] == "ending"
    assert ml["lifecycle_live"] == "ending"
    assert ml["lifecycle_close"] == "ongoing"
    assert ml["lifecycle_display"] == "ongoing"
    assert "盘中阶段像衰退" in ml["lifecycle_hint"]
