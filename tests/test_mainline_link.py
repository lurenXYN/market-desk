"""Mainline switch explain sync + soft link fallback."""

from __future__ import annotations

from market_desk.config import MAINLINE_FADE_SWITCH_MULT, MAINLINE_THEME_SWITCH_MULT
from market_desk.mainline import explain_mainline, pick_mainline, switch_margin_need
from market_desk.verdict import _build_link_branch, _mainline_needs_link


def _board(name: str, *, status: str = "确认中", zt: int = 8, pct: float = 2.0, **extra):
    row = {
        "name": name,
        "kind": "industry",
        "status": status,
        "zt_n": zt,
        "pct": pct,
        "focus": 40,
        "hist": [],
    }
    row.update(extra)
    return row


def test_switch_margin_fade_eases_need() -> None:
    sticky = _board("煤炭", status="退潮", zt=3, pct=-0.8)
    chall = _board("动力煤", status="确认中", zt=10, pct=3.0)
    meta = switch_margin_need(
        margin=12.0,
        incumbent=sticky,
        challenger=chall,
        sticky_name="煤炭",
        sticky_held_seconds=600,
    )
    assert meta["force_flip_fade"] is True
    assert meta["need"] == 0.0


def test_explain_need_matches_pick_for_ending() -> None:
    sticky = _board("煤炭", status="确认中", zt=6, pct=0.5, hist=[
        {"zt_n": 10}, {"zt_n": 8}, {"zt_n": 6},
    ])
    # Force ending via decay/slope path when status still 确认中.
    sticky["pct"] = -0.6
    chall = _board("有色", status="确认中", zt=12, pct=3.5)
    hot = [sticky, chall]
    picked = pick_mainline(hot, sticky_name="煤炭", margin=12.0, sticky_held_seconds=900)
    why = explain_mainline(
        hot, picked, sticky_name="煤炭", sticky_held_seconds=900, margin=12.0
    )
    meta = switch_margin_need(
        margin=12.0,
        incumbent=sticky,
        challenger=max(hot, key=lambda b: b["zt_n"]),
        sticky_name="煤炭",
        sticky_held_seconds=900,
    )
    assert why["need"] == meta["need"]
    if meta.get("fade_mult_applied"):
        assert why["need"] <= round(12.0 * float(MAINLINE_FADE_SWITCH_MULT) * float(MAINLINE_THEME_SWITCH_MULT), 1) + 0.1


def test_link_theme_fallback_when_no_peers() -> None:
    main = _board("煤炭", zt=10)
    main["similar_peers"] = []
    sibling = _board("动力煤", zt=7, pct=1.8)
    # Empty recommend items → needs link.
    need, why = _mainline_needs_link({"items": []})
    assert need and why
    info, rec = _build_link_branch(
        hot=[main, sibling],
        main=main,
        etfs=[{"code": "515220", "name": "煤炭ETF", "price": 1.2, "low": 1.1}],
        zt=[],
        zb=[],
        bans=[],
        stock_block=False,
        recommend={"items": []},
        side_info=None,
    )
    assert info is not None
    assert info.get("fallback") in ("theme", "score")
    assert info.get("name") == "动力煤"
    assert rec and rec.get("link") is True


def test_side_absorbed_into_link_when_sim_high() -> None:
    main = _board("煤炭", zt=10)
    main["similar_peers"] = [{"name": "动力煤", "sim": 0.62}]
    side = _board("动力煤", zt=8, pct=2.2)
    info, rec = _build_link_branch(
        hot=[main, side],
        main=main,
        etfs=[{"code": "515220", "name": "煤炭ETF", "price": 1.2, "low": 1.1}],
        zt=[],
        zb=[],
        bans=[],
        stock_block=False,
        recommend={"items": [{"ready": False, "last": 10, "chase_price": 10}]},
        side_info={"name": "动力煤", "status": "确认中", "zt_n": 8},
    )
    assert info and info.get("from_side") is True
    assert info.get("name") == "动力煤"
    assert rec and any("支线并入" in str(i.get("reason") or "") for i in (rec.get("items") or []))
