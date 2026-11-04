"""Buy trade-log notes: expand cf_touch into readable Chinese reasons."""

from __future__ import annotations

from market_desk.sim_desk import enrich_buy_trade_note, format_buy_fill_note


def test_format_buy_fill_note_has_channel_and_reason():
    note = format_buy_fill_note(
        signal_type="buy",
        reason="cf_touch",
        size_tag="half",
        plan_px=17.23,
        fill_px=17.23,
        mainline="传媒",
    )
    assert "主线回踩" in note
    assert "反事实到价" in note
    assert "计划17.23" in note
    assert "半仓" in note
    assert "传媒" in note


def test_enrich_expands_bare_cf_touch():
    fill = {
        "side": "buy",
        "note": "cf_touch",
        "signal_type": "buy_side",
        "size_tag": "side",
        "px": 10.5,
        "open_px": 10.4,
        "lot_payload": {"fill_reason": "cf_touch", "mainline": "汽车"},
    }
    out = enrich_buy_trade_note(fill)
    assert out["note"] != "cf_touch"
    assert "支线回踩" in out["note"]
    assert "反事实到价" in out["note"]
    assert out.get("note_code") == "cf_touch"


def test_enrich_keeps_rich_note():
    rich = "主线回踩 · 触达计划价 · 计划10 · 满仓档"
    out = enrich_buy_trade_note({"side": "buy", "note": rich, "signal_type": "buy"})
    assert out["note"] == rich


def test_sell_rows_untouched():
    row = {"side": "sell", "note": "开盘必卖 · 保本"}
    assert enrich_buy_trade_note(row)["note"] == "开盘必卖 · 保本"
