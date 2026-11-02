"""v1.10.36: side-branch buys, overlap theme, unmapped-concept sticky ban, pool shadow."""

from __future__ import annotations

from market_desk.board_alias import is_blocked_sina
from market_desk.mainline import (
    build_pool_shadow,
    is_pseudo_board,
    mainline_pool,
    member_overlap,
    pick_mainline,
    pick_side_mainline,
    sticky_eligible_board,
)
from market_desk.notify import build_toast_alerts
from market_desk.sim_desk import size_from_payload
from market_desk.verdict.branches import (
    _build_side_branch,
    cap_side_to_daily_budget,
    dedupe_side_against_main,
    retitle_side_recommend,
)
from market_desk.verdict.buy import align_action_with_ready, reconfirm_recommend_ready
from market_desk.verdict.common import _scale_item_qty
from market_desk.verdict.progress import mark_pullback_entries


def _board(name, zt, *, kind="industry", status="确认中", pct=2.0, members=None, **extra):
    row = {
        "name": name,
        "bk": extra.pop("bk", f"BK_{name}"),
        "kind": kind,
        "status": status,
        "zt_n": zt,
        "pct": pct,
        "focus": 40,
        "hist": [],
    }
    if members is not None:
        row["members"] = [{"code": c} for c in members]
    row.update(extra)
    return row


def _codes(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i:03d}" for i in range(1, n + 1)]


def _patch_setting(monkeypatch, **overrides) -> None:
    """Override ``setting()`` in the modules that already imported it."""
    from market_desk.settings import setting as orig

    def fake(key, default=None):
        if key in overrides:
            return overrides[key]
        return orig(key, default)

    for mod in (
        "market_desk.settings",
        "market_desk.mainline",
        "market_desk.verdict.branches",
        "market_desk.verdict.buy",
    ):
        monkeypatch.setattr(f"{mod}.setting", fake)


def test_member_overlap_blocks_side_when_jaccard_high() -> None:
    bank = _board("银行", 8, members=_codes("600", 8))
    # Same names family would already skip; 华为鸿蒙 is a different theme.
    clone = _board("华为鸿蒙", 7, kind="concept", members=_codes("600", 7), pct=1.8)
    coal = _board("煤炭", 6, members=_codes("601", 8), pct=1.5)
    assert member_overlap(bank, clone) is not None
    assert member_overlap(bank, clone) >= 0.3
    side = pick_side_mainline([bank, clone, coal], bank, pool_mode="all", max_gap=40)
    assert side is not None and side["name"] == "煤炭"


def test_thin_members_fall_back_to_name_theme() -> None:
    a = _board("银行", 8, members=_codes("600", 3))
    b = _board("华为鸿蒙", 7, kind="concept", members=_codes("600", 3))
    assert member_overlap(a, b) is None
    side = pick_side_mainline([a, b], a, pool_mode="all", max_gap=40)
    assert side is not None and side["name"] == "华为鸿蒙"


def test_dedupe_side_drops_main_codes() -> None:
    main = {"items": [{"code": "600001", "kind": "stock"}]}
    side = {"items": [{"code": "600001"}, {"code": "600002"}]}
    out = dedupe_side_against_main(main, side)
    assert [x["code"] for x in out["items"]] == ["600002"]


def test_unmapped_concept_cannot_take_or_keep_sticky() -> None:
    pet = _board("宠物经济", 20, kind="concept")
    bank = _board("银行", 4)
    assert not sticky_eligible_board(pet)
    assert sticky_eligible_board(bank)
    picked = pick_mainline([bank, pet], pool_mode="all")
    assert picked["name"] == "银行"
    kept = pick_mainline(
        [bank, pet], sticky_name="宠物经济", margin=12.0, pool_mode="all"
    )
    assert kept["name"] == "银行"


def test_mapped_concept_can_win_in_all_pool() -> None:
    ai = _board("人工智能", 20, kind="concept")
    bank = _board("银行", 4)
    assert sticky_eligible_board(ai)
    picked = pick_mainline([bank, ai], pool_mode="all")
    assert picked["name"] == "人工智能"


def test_side_observe_when_disabled(monkeypatch) -> None:
    _patch_setting(monkeypatch, side_buy_enabled=False)
    main = _board("银行", 8)
    side = _board("煤炭", 7)
    info, rec = _build_side_branch(
        hot=[main, side],
        main=main,
        etfs=[{"code": "515220", "name": "煤炭ETF", "price": 1.2, "low": 1.1, "pct": 1.0}],
        zt=[],
        zb=[],
        bans=[],
        stock_block=True,
    )
    assert info and info["name"] == "煤炭"
    rec = mark_pullback_entries(rec, observe_only=True)
    assert not rec.get("buy")
    assert all(not it.get("ready") for it in rec.get("items") or [])


def test_side_can_ready_scaled_hero_stays(monkeypatch) -> None:
    _patch_setting(monkeypatch, side_buy_enabled=True, side_size_mult=0.6)
    item = {"code": "600001", "qty": 1000, "kind": "stock", "buy_price": 10.0}
    _scale_item_qty(item, 0.6, "支线×0.6")
    assert item["qty"] == 600

    main = _board("银行", 8)
    side = _board("煤炭", 7)
    info, rec = _build_side_branch(
        hot=[main, side],
        main=main,
        etfs=[
            {
                "code": "515220",
                "name": "煤炭ETF",
                "price": 1.20,
                "low": 1.19,
                "last": 1.20,
                "pct": 0.5,
            }
        ],
        zt=[],
        zb=[],
        bans=[],
        stock_block=True,
    )
    assert info and rec
    for it in rec.get("items") or []:
        it["last"] = it.get("buy_price") or it.get("last") or 1.20
        it["minute"] = {"ok": True}
        assert abs(float(it.get("size_mult") or 0) - 0.6) < 0.05
        assert it.get("role_label") == "支线回踩"
    rec = mark_pullback_entries(rec, observe_only=False, require_minute=True)
    rec = retitle_side_recommend(rec, side_name="煤炭")
    assert rec.get("buy") or any(it.get("ready") for it in rec.get("items") or [])
    if rec.get("buy"):
        assert "支线可买" in str(rec.get("title") or "")

    v = align_action_with_ready(
        {
            "action": "观察回踩",
            "mainline": {"name": "银行", "etf_mapped": True},
            "recommend": {
                "items": [{"code": "512800", "ready": False, "near_entry": False}],
                "buy": False,
            },
            "side_recommend": rec,
            "algo_notes": [],
        }
    )
    assert v["action"] == "观察回踩"


def test_mainline_unmapped_etf_still_blocks_main_ready() -> None:
    v = align_action_with_ready(
        {
            "action": "可买入",
            "mainline": {"name": "宠物经济", "etf_mapped": False, "etf_soft": False},
            "recommend": {
                "items": [
                    {
                        "code": "600001",
                        "ready": True,
                        "near_entry": True,
                        "kind": "stock",
                    }
                ],
                "buy": True,
                "title": "建议买入",
            },
            "algo_notes": [],
        }
    )
    assert v["action"] == "观察回踩"
    assert not v["recommend"]["items"][0]["ready"]


def test_notify_buy_keys_ignore_side_ready() -> None:
    prev = {
        "ok": True,
        "phase": "发酵",
        "verdict": {
            "action": "观察回踩",
            "mainline": {"name": "银行", "lifecycle": "ongoing", "status": "确认中"},
            "recommend": {"items": []},
            "side_recommend": {"items": []},
        },
    }
    cur_side = {
        "ok": True,
        "phase": "发酵",
        "verdict": {
            "action": "观察回踩",
            "mainline": {"name": "银行", "lifecycle": "ongoing", "status": "确认中"},
            "recommend": {"items": []},
            "side_recommend": {
                "items": [{"code": "600001", "name": "侧", "ready": True, "buy_price": 10}],
                "buy": True,
            },
        },
    }
    keys = [a[0] for a in build_toast_alerts(prev, cur_side)]
    assert not any(k.startswith("buy:") for k in keys)

    cur_main = {
        "ok": True,
        "phase": "发酵",
        "verdict": {
            "action": "可买入",
            "mainline": {"name": "银行", "lifecycle": "ongoing", "status": "确认中"},
            "recommend": {
                "primary": {"code": "512800", "name": "银行ETF", "buy_price": 1.2},
                "items": [{"code": "512800", "ready": True}],
            },
            "side_recommend": {
                "items": [{"code": "600001", "ready": True}],
                "buy": True,
            },
            "playbook": {},
        },
    }
    keys_main = [a[0] for a in build_toast_alerts(prev, cur_main)]
    assert any(k.startswith("buy:") for k in keys_main)
    assert not any("600001" in k for k in keys_main)


def test_size_from_payload_side_not_full() -> None:
    assert size_from_payload({}, ready=True) == (1.0, "full")
    assert size_from_payload({"early_half": {"px": 10}}, ready=True) == (0.5, "half")
    assert size_from_payload({"size_mult": 0.5}, ready=True) == (0.5, "half")
    w, tag = size_from_payload({"size_mult": 0.6}, ready=True)
    assert abs(w - 0.6) < 1e-6 and tag == "side"
    w2, tag2 = size_from_payload({}, ready=True, signal_type="buy_side")
    assert abs(w2 - 0.6) < 1e-6 and tag2 == "side"


def test_pool_shadow_all_does_not_move_live_sticky() -> None:
    ai = _board("人工智能", 20, kind="concept")
    bank = _board("银行", 4)
    live = pick_mainline([bank, ai])
    assert live["name"] == "银行"
    shadow = build_pool_shadow([bank, ai], live_main=live)
    assert shadow["mode"] == "all"
    assert shadow["main"]["name"] == "人工智能"
    assert shadow["would_switch"] is True
    assert pick_mainline([bank, ai])["name"] == "银行"


def test_region_and_pseudo_stay_out_of_pool() -> None:
    assert is_blocked_sina("上海本地")
    assert is_pseudo_board("QFII重仓")
    assert is_pseudo_board("追近多板")
    names = {
        b["name"]
        for b in mainline_pool(
            [
                _board("电子", 6),
                _board("QFII重仓", 12, kind="concept"),
                _board("东方财富热股", 9, kind="concept"),
            ]
        )
    }
    assert names == {"电子"}


def test_cap_side_cuts_before_main(monkeypatch) -> None:
    _patch_setting(monkeypatch, target_total_cost=10000.0, side_buy_enabled=True)
    monkeypatch.setattr("market_desk.db.load_positions", lambda: [])
    main = {
        "items": [
            {"code": "512800", "ready": True, "qty": 1000, "buy_price": 10.0, "kind": "etf"}
        ]
    }
    side = {
        "items": [
            {
                "code": "515220",
                "ready": True,
                "qty": 1000,
                "buy_price": 10.0,
                "kind": "etf",
                "role_label": "支线回踩",
            }
        ]
    }
    out = cap_side_to_daily_budget(main, side)
    assert not out["items"][0]["ready"]
    assert "支线仓位封顶" in (out["items"][0].get("confirm_fail") or [])


def _side_verdict(*, last: float, high: float, ready: bool = True) -> dict:
    return {
        "action": "观察回踩",
        "mainline": {"name": "银行", "etf_mapped": True, "status": "确认中", "zt_n": 8},
        "carrier": {"code": "512800", "pct": 1.2},
        "recommend": {"items": [], "buy": False},
        "side_mainline": {
            "name": "煤炭",
            "status": "确认中",
            "zt_n": 6,
            "carrier_code": "515220",
            "carrier_name": "煤炭ETF",
            "main_yi": 2.0,
        },
        "side_recommend": {
            "items": [
                {
                    "code": "600001",
                    "kind": "stock",
                    "ready": ready,
                    "last": last,
                    "high": high,
                    "pct": 1.5,
                    "role_label": "支线回踩",
                    "desk_source": "side",
                    "qty": 600,
                    "size_mult": 0.6,
                    "buy_price": last,
                }
            ],
            "buy": ready,
            "title": "支线可买 · 煤炭",
        },
    }


def test_side_off_high_demotes(monkeypatch) -> None:
    _patch_setting(monkeypatch, side_buy_enabled=True)
    # dist = 0.1% << 0.75% stock floor.
    out = reconfirm_recommend_ready(_side_verdict(last=99.9, high=100.0))
    item = out["side_recommend"]["items"][0]
    assert item["ready"] is False
    assert any("离日高" in str(f) for f in (item.get("confirm_fail") or []))
    assert item["role_label"] == "支线回踩"
    assert out["action"] == "观察回踩"
    assert "支线可买" in str(out["side_recommend"].get("title") or "")


def test_side_off_high_passes_when_far(monkeypatch) -> None:
    _patch_setting(monkeypatch, side_buy_enabled=True)
    out = reconfirm_recommend_ready(_side_verdict(last=98.0, high=100.0))
    item = out["side_recommend"]["items"][0]
    assert item["ready"] is True
    assert not item.get("confirm_fail")


def test_side_observe_only_skips_off_high_reconfirm(monkeypatch) -> None:
    _patch_setting(monkeypatch, side_buy_enabled=False)
    out = reconfirm_recommend_ready(_side_verdict(last=99.9, high=100.0, ready=True))
    item = out["side_recommend"]["items"][0]
    assert item["ready"] is True
    assert not item.get("confirm_fail")


def test_side_index_weak_demotes_stock(monkeypatch) -> None:
    _patch_setting(monkeypatch, side_buy_enabled=True)
    out = reconfirm_recommend_ready(
        _side_verdict(last=98.0, high=100.0),
        metrics={"weak_index": True},
    )
    item = out["side_recommend"]["items"][0]
    assert item["ready"] is False
    assert "指数弱禁个股现买" in (item.get("confirm_fail") or [])
    assert item["role_label"] == "支线回踩"
