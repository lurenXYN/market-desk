"""Stage-1 exit fixes: live sector-crack data, daily ATR bands, break-even edges, sell-rule review."""

from __future__ import annotations

from market_desk.review.bias import build_sell_fly_board
from market_desk.review.signals import sell_atr_bucket, sell_rule_of
from market_desk.trend import daily_atr_pct
from market_desk.verdict import _exit_band_params, _sell_item, build_sell_themes
from market_desk.verdict.common import _crack_fields
from market_desk.verdict.sell_crack import detect_sector_crack


def _card(**kw):
    card = {
        "name": "银行",
        "bk": "BK0475",
        "kind": "industry",
        "status": "确认中",
        "pct": 1.0,
        "zt_n": 1,
        "leader_code": "600001",
        "leader_boards": 1,
        "pool": [
            {"code": "600001", "pct": 5.0, "price": 10.5, "high": 11.0},
            {"code": "600002", "pct": 1.0, "price": 10.1, "high": 10.2},
            {"code": "000001", "pct": 0.5, "price": 10.05, "high": 10.1},
        ],
    }
    card.update(kw)
    return card


def _row(**kw):
    row = {
        "id": 3,
        "code": "000001",
        "name": "平安银行",
        "buy_price": 10.0,
        "last": 10.05,
        "high": 10.10,
        "low": 9.98,
        "qty": 500,
        "last_buy_date": "2026-09-10",
        "entry_board": "银行",
    }
    row.update(kw)
    return row


def _band(amplitude, *, etf=False, source="daily"):
    return _exit_band_params(
        etf=etf,
        on_mainline=False,
        ending=False,
        main_status="none",
        life_stage="",
        phase="震荡",
        soft_exit=False,
        trend={},
        carrier_falling=False,
        amplitude=amplitude,
        amplitude_source=source,
    )


# --- sector crack: live board card → sell theme → trigger ---------------------------------


def test_crack_fields_derive_leader_pullback_from_card() -> None:
    f = _crack_fields(_card())
    # prev close = 10.5 / 1.05 = 10.0 → high% = +10.0, now +5.0 → pullback 5 pts.
    assert f["leader_code"] == "600001"
    assert f["leader_pct"] == 5.0
    assert f["leader_high_pct"] == 10.0
    assert f["leader_pullback"] == 5.0
    assert [m["code"] for m in f["pool"]] == ["600001", "600002", "000001"]
    assert _crack_fields(None) == {}


def test_build_sell_themes_carries_crack_fields_for_primary_and_hot() -> None:
    main = _card()
    peer = _card(name="券商", bk="BK0473", leader_code="600030", pool=[{"code": "600030", "pct": -7.0}])
    themes = build_sell_themes(hot=[main, peer], main=main, vehicle={}, side_info={})
    by_name = {t["name"]: t for t in themes}
    assert by_name["银行"]["leader_pullback"] == 5.0
    assert by_name["银行"]["pool"]
    if "券商" in by_name:
        assert by_name["券商"]["pool"][0]["pct"] == -7.0


def test_sector_crack_fires_live_from_built_themes() -> None:
    main = _card()
    themes = build_sell_themes(hot=[main], main=main, vehicle={}, side_info={})
    item = _sell_item(_row(), {"sell_themes": themes, "segment": {}}, "震荡", trade_date="2026-09-17")
    assert item["role_label"] == "板块塌陷先减"
    assert item["exit_mode"] == "half"
    assert "龙头炸板回撤 5.0%" in item["reason"]


def test_sector_crack_needs_leader_touch_and_skips_self() -> None:
    # Leader only rallied to +5% then faded to +0.5%: not a blown limit-up.
    soft = _crack_fields(_card(pool=[{"code": "600001", "pct": 0.5, "price": 10.05, "high": 10.5}]))
    soft["name"] = "银行"
    assert soft["leader_pullback"] >= 4.0
    assert detect_sector_crack(soft, "000001") == ""
    # Held name is the leader: its own pullback is not a sector signal.
    blown = _crack_fields(_card())
    assert detect_sector_crack(blown, "600001") == ""
    assert detect_sector_crack(blown, "000001").startswith("板块核心龙头炸板回撤")


def test_sector_crack_member_dive_excludes_held_name() -> None:
    theme = {
        "name": "银行",
        "pool": [{"code": "000001", "pct": -8.0}, {"code": "600002", "pct": -6.5}],
    }
    assert detect_sector_crack(theme, "000001") == ""
    theme["pool"].append({"code": "600003", "pct": -7.0})
    assert "2 只个股" in detect_sector_crack(theme, "000001")


# --- ATR: daily ATR% preferred, intraday may only widen ----------------------------------


def test_daily_atr_pct_includes_gaps() -> None:
    closes = [10.0] * 6
    highs = [10.2] * 6
    lows = [9.8] * 6
    assert daily_atr_pct(highs, lows, closes) == 4.0
    # Gap: prior close 10, bar range 10.6–10.8 → TR = 0.8.
    closes2 = closes + [10.7]
    highs2 = highs + [10.8]
    lows2 = lows + [10.6]
    atr = daily_atr_pct(highs2, lows2, closes2)
    assert atr is not None and atr > 4.0 * 10.0 / 10.7
    assert daily_atr_pct([10.2], [9.8], [10.0]) is None


def test_intraday_amplitude_never_tightens() -> None:
    low_daily = _band(1.8, source="daily")
    low_intraday = _band(1.8, source="intraday")
    base = _band(None)
    assert low_daily["atr_band_mode"] == "low_beta"
    assert low_intraday["atr_band_mode"] == "normal"
    assert low_intraday["pb_light"] == base["pb_light"]
    high_intraday = _band(7.0, source="intraday")
    assert high_intraday["atr_band_mode"] == "high_beta"
    assert "日内高波动" in high_intraday["mode_zh"]


def test_etf_uses_own_atr_thresholds() -> None:
    assert _band(1.8, etf=True)["atr_band_mode"] == "normal"
    assert _band(0.8, etf=True)["atr_band_mode"] == "low_beta"
    assert _band(3.5, etf=True)["atr_band_mode"] == "high_beta"


def test_sell_item_prefers_trend_atr_over_intraday_range() -> None:
    # Intraday range 0.2% would have tightened before; daily ATR 4% keeps the band standard.
    row = _row(last=10.5, high=10.51, low=10.49, peak_price=10.6, entry_board="无关")
    item = _sell_item(row, {"sell_themes": [], "segment": {}}, "震荡", trade_date="2026-09-17",
                      trend={"atr_pct": 4.0, "label": "震荡/非上升"})
    assert item["atr_source"] == "daily"
    assert item["atr_band_mode"] == "normal"
    item2 = _sell_item(row, {"sell_themes": [], "segment": {}}, "震荡", trade_date="2026-09-17")
    assert item2["atr_source"] == "intraday"
    assert item2["atr_band_mode"] == "normal"


# --- break-even edges ------------------------------------------------------------------


def test_breakeven_label_when_below_cost() -> None:
    row = _row(last=9.95, high=10.05, low=9.9, peak_price=10.40, entry_board="无关")
    item = _sell_item(row, {"sell_themes": [], "segment": {}}, "震荡", trade_date="2026-09-17")
    assert item["role_label"] == "回落防守清仓"
    assert item["exit_mode"] == "clear"
    assert "成本下方" in item["reason"]


def test_breakeven_gap_down_trims_half() -> None:
    row = _row(open=9.96, last=9.97, high=10.0, low=9.9, peak_price=10.40, entry_board="无关")
    item = _sell_item(row, {"sell_themes": [], "segment": {}}, "震荡", trade_date="2026-09-17")
    assert item["role_label"] == "跳空破保本·先减半"
    assert item["exit_mode"] == "half"
    assert item["urgency"] == "trim"


def test_breakeven_same_day_fade_still_clears() -> None:
    # Opened above the line and re-armed intraday (high ≥ +2.5%), then faded back: full clear.
    row = _row(open=10.1, last=10.02, high=10.3, low=10.0, peak_price=10.30, entry_board="无关")
    item = _sell_item(row, {"sell_themes": [], "segment": {}}, "震荡", trade_date="2026-09-17")
    assert item["role_label"] == "保本防守清仓"
    assert item["exit_mode"] == "clear"


def test_breakeven_yields_to_stop_band() -> None:
    row = _row(open=9.5, last=9.5, high=9.6, low=9.4, peak_price=10.40, entry_board="无关")
    item = _sell_item(row, {"sell_themes": [], "segment": {}}, "震荡", trade_date="2026-09-17")
    assert "保本" not in item["role_label"]
    assert "止损" in item["role_label"]


# --- sell-rule review ----------------------------------------------------------------


def test_sell_rule_of_strips_open_buffer_prefix() -> None:
    assert sell_rule_of("开盘必卖（止损/清仓，不等待缓冲）·退潮兑现清仓") == "退潮兑现"
    assert sell_rule_of("开盘必卖（止损/清仓，不等待缓冲）·保本防守清仓") == "保本防守"
    assert sell_rule_of("回落防守清仓") == "保本防守"
    assert sell_rule_of("跳空破保本·先减半") == "跳空破保本"
    assert sell_rule_of("止损带·站上MA20先减") == "止损带减半"
    assert sell_rule_of("板块塌陷先减") == "板块塌陷"
    assert sell_rule_of("") == "其他"


def test_sell_atr_bucket_reads_payload_or_band_text() -> None:
    assert sell_atr_bucket({"payload": {"atr_band_mode": "high_beta"}}) == "高波动"
    assert sell_atr_bucket({"payload": {"band_mode_zh": "标准·低波动ATR"}}) == "低波动"
    assert sell_atr_bucket({"payload": {}}) == "标准"


def test_fly_board_buckets_by_rule_on_all_scored_sells() -> None:
    rows = [
        {"signal_type": "sell", "action": "保本防守清仓", "outcome_label": "卖后回落",
         "outcome_day3_pct": 3.0, "traded": 0, "payload": {}},
        {"signal_type": "sell", "action": "板块塌陷先减", "outcome_label": "卖飞",
         "outcome_day3_pct": -4.0, "traded": 1, "payload": {"exit_rule": "板块塌陷"}},
        {"signal_type": "sell", "action": "保本防守清仓", "outcome_label": "卖后回落",
         "outcome_day3_pct": 1.0, "traded": 1, "payload": {"atr_band_mode": "low_beta"}},
    ]
    board = build_sell_fly_board(rows, hit_mode="traded")
    assert board["n"] == 2
    assert board["rule_n"] == 3
    rules = {r["label"]: r for r in board["by_rule"]}
    assert rules["保本防守"]["n"] == 2
    assert rules["保本防守"]["avg_saved_d3"] == 2.0
    assert rules["板块塌陷"]["fly_rate"] == 100.0
    atr = {r["label"]: r for r in board["by_atr"]}
    assert atr["低波动"]["n"] == 1
