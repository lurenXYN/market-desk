"""Stage-3 radar: crowding index, broad-ETF pulse, narrative graph (shadow)."""

from __future__ import annotations

from datetime import datetime

import market_desk.db as desk_db
from market_desk.config import CROWD_BAN_FLAG
from market_desk.notify import build_radar_alerts, is_decision_toast, toast_priority
from market_desk.radar import (
    apply_crowding_gate,
    climax_confirm,
    build_narrative_clusters,
    compute_crowding,
    evaluate_board,
    market_pulse_event,
    market_turnover,
    minute_baseline,
    scan_pulses,
    summarize_leads,
)
from market_desk.verdict.buy import _hero_buy_locked, align_action_with_ready
from market_desk.verdict.common import hard_confirm_fails

NOW = datetime(2026, 10, 1, 10, 30)
MARKET = 1.2e12  # 1.2 万亿


def _idx(sh: float | None = 6e11, sz: float | None = 6e11) -> list[dict]:
    return [
        {"code": "000001", "amount": sh},
        {"code": "399001", "amount": sz},
        {"code": "000300", "pct": -1.2},
    ]


def test_market_turnover_sums_sh_sz_and_needs_both() -> None:
    assert market_turnover(_idx()) == 1.2e12
    assert market_turnover(_idx(sz=None)) is None
    assert market_turnover([]) is None


def test_evaluate_board_industry_absolute_bands() -> None:
    assert evaluate_board(13.0, kind="industry", history=None)["level"] == "extreme"
    assert evaluate_board(10.0, kind="industry", history=None)["level"] == "warn"
    assert evaluate_board(5.0, kind="industry", history=None)["level"] == "ok"


def test_evaluate_board_concept_relative_only() -> None:
    assert evaluate_board(20.0, kind="concept", history=None)["level"] == "ok"
    hist = [5.0] * 15 + [9.0]
    ext = evaluate_board(12.0, kind="concept", history=hist)
    assert ext["level"] == "extreme"
    assert ext["mult"] == 2.4
    warn = evaluate_board(8.5, kind="concept", history=hist)
    assert warn["level"] == "warn"
    assert evaluate_board(6.0, kind="concept", history=[3.0] * 15)["level"] == "ok"


def test_evaluate_board_relative_needs_min_history() -> None:
    assert evaluate_board(8.0, kind="industry", history=[3.0] * 5)["level"] == "ok"
    assert evaluate_board(8.0, kind="industry", history=[3.0] * 12)["level"] == "extreme"


def test_compute_crowding_skips_open_and_missing_market() -> None:
    boards = [{"name": "半导体", "kind": "industry", "amount": 1.6e11}]
    early = compute_crowding(boards, MARKET, {}, now=datetime(2026, 10, 1, 9, 40))
    assert not early["ok"]
    assert not compute_crowding(boards, None, {}, now=NOW)["ok"]
    assert not compute_crowding(boards, 1e10, {}, now=NOW)["ok"]


def test_compute_crowding_grades_and_prefers_industry_on_name_clash() -> None:
    boards = [
        {"name": "半导体", "kind": "industry", "amount": 1.6e11},
        {"name": "半导体", "kind": "concept", "amount": 3.0e11},
        {"name": "电池", "kind": "industry", "amount": 1.2e11},
        {"name": "白酒", "kind": "industry", "amount": 2.0e10},
    ]
    out = compute_crowding(boards, MARKET, {}, now=NOW)
    assert out["ok"] and out["market_yi"] == 12000
    assert out["boards"]["半导体"]["kind"] == "industry"
    assert [r["name"] for r in out["extreme"]] == ["半导体"]
    assert [r["name"] for r in out["warn"]] == ["电池"]
    assert out["top"][0]["name"] == "半导体"


def test_compute_crowding_level1_parent_skips_absolute_bands() -> None:
    boards = [
        {"name": "电子", "kind": "industry", "amount": 3.0e11},
        {"name": "半导体", "kind": "industry", "amount": 1.5e11},
    ]
    out = compute_crowding(boards, MARKET, {}, now=NOW)
    assert out["boards"]["电子"]["level"] == "ok" and out["boards"]["电子"]["parent"]
    assert [r["name"] for r in out["extreme"]] == ["半导体"]
    assert [r["name"] for r in out["top"]] == ["半导体"]
    hist = {"电子": [12.0] * 15}
    rel = compute_crowding(boards, MARKET, hist, now=NOW)
    assert rel["boards"]["电子"]["level"] == "extreme"


def _card(code: str, *, board: str = "", ready: bool = True, qty: int = 1000) -> dict:
    return {
        "code": code,
        "name": f"N{code}",
        "kind": "stock",
        "ready": ready,
        "probe_ok": True,
        "qty": qty,
        "buy_price": 10.0,
        "wait_price": 9.8,
        "source_board": board,
        "confirm_fail": [],
    }


def _verdict(items: list[dict], *, action: str = "可买入") -> dict:
    return {
        "action": action,
        "mainline": {"name": "半导体", "etf_mapped": True},
        "recommend": {"items": items, "buy": True, "primary": items[0]},
        "algo_notes": [],
    }


def _crowd(level_by_name: dict[str, str]) -> dict:
    rows = {
        n: {"name": n, "kind": "industry", "share": 13.0 if lvl == "extreme" else 10.0, "level": lvl, "reasons": ["x"]}
        for n, lvl in level_by_name.items()
    }
    return {
        "ok": True,
        "boards": rows,
        "top": list(rows.values()),
        "extreme": [r for r in rows.values() if r["level"] == "extreme"],
        "warn": [r for r in rows.values() if r["level"] == "warn"],
    }


def test_climax_confirm_signals() -> None:
    assert climax_confirm(None, phase="高潮") == ["相位高潮"]
    assert climax_confirm({"flags": {"滞涨": True, "加速": True}}, phase="分歧") == ["板块滞涨"]
    assert climax_confirm({"zt_n": 3, "zb_n": 2}) == ["炸板2/5"]
    assert climax_confirm({"zt_n": 6, "zb_n": 2}) == []
    assert climax_confirm({"flags": {"一波": True}}, phase="发酵") == []


def test_crowding_gate_soft_when_extreme_unconfirmed() -> None:
    items = [_card("600001", qty=1000), _card("600002", ready=False)]
    items[1]["near_entry"] = True
    v = apply_crowding_gate(_verdict(items), _crowd({"半导体": "extreme"}), phase="分歧")
    by = {x["code"]: x for x in v["recommend"]["items"]}
    full = by["600001"]
    assert full["ready"] is True and full["qty"] == 500 and not full.get("block_ready")
    assert full["probe_ok"] is False and "极端拥挤·半仓" in full["confirm_soft"]
    assert CROWD_BAN_FLAG not in (full.get("confirm_fail") or [])
    assert by["600002"]["block_ready"] is True
    assert v["crowding"]["modes"] == {"半导体": "soft"}
    assert v["crowding"]["soft"] == ["半导体"] and v["crowding"]["banned"] == []
    assert "极端拥挤·半仓" in v["algo_notes"] and not _hero_buy_locked(v)
    assert v["recommend"]["buy"] is True
    v2 = apply_crowding_gate(v, _crowd({"半导体": "extreme"}), phase="分歧")
    assert v2["recommend"]["items"][0]["qty"] == 500
    hits = v["crowding"]["hits"]
    assert [(h["code"], h["mode"], h["was_ready"]) for h in hits] == [
        ("600001", "soft", True),
        ("600002", "soft", False),
    ]


def test_crowding_gate_soft_demotes_relaxed_ready() -> None:
    card = _card("600001")
    card["ready_relaxed"] = True
    v = apply_crowding_gate(_verdict([card]), _crowd({"半导体": "extreme"}), phase="分歧")
    item = v["recommend"]["items"][0]
    assert item["ready"] is False and item["block_ready"] is True
    assert align_action_with_ready(v)["action"] == "观察回踩"


def test_crowding_gate_card_flag_upgrades_to_hard() -> None:
    cards = [{"name": "半导体", "flags": {"A杀": True}}]
    v = apply_crowding_gate(_verdict([_card("600001")]), _crowd({"半导体": "extreme"}), cards=cards, phase="分歧")
    assert v["crowding"]["modes"] == {"半导体": "hard"}
    assert v["crowding"]["confirm"] == {"半导体": ["板块A杀"]}
    assert v["recommend"]["items"][0]["ready"] is False


def test_crowding_gate_hard_bans_extreme_board() -> None:
    v = apply_crowding_gate(_verdict([_card("600001")]), _crowd({"半导体": "extreme"}), phase="高潮")
    item = v["recommend"]["items"][0]
    assert item["ready"] is False and item["probe_ok"] is False and item["block_ready"]
    assert item["buy_price"] == 9.8 and item["plan_price"] == 10.0
    assert CROWD_BAN_FLAG in hard_confirm_fails(item["confirm_fail"])
    assert v["recommend"]["buy"] is False
    assert "禁开新仓" in v["recommend"]["title"]
    assert "极端拥挤禁开" in v["algo_notes"]
    assert v["crowding"]["banned"] == ["半导体"]
    assert v["crowding"]["shares"] == {"半导体": 13.0}
    aligned = align_action_with_ready(v)
    assert aligned["action"] == "观察回踩"


def test_crowding_gate_mainline_note_locks_hero_upgrade() -> None:
    v = apply_crowding_gate(_verdict([_card("600001")]), _crowd({"半导体": "extreme"}), phase="高潮")
    assert _hero_buy_locked(v)
    assert v["crowding"]["hits"][0]["mode"] == "hard"


def test_crowding_gate_warn_scales_once_and_keeps_ready() -> None:
    crowd = _crowd({"半导体": "warn"})
    v = apply_crowding_gate(_verdict([_card("600001", qty=1000)]), crowd)
    item = v["recommend"]["items"][0]
    assert item["ready"] and item["qty"] == 800
    assert "板块拥挤·降仓" in item["confirm_soft"]
    v2 = apply_crowding_gate(v, crowd)
    assert v2["recommend"]["items"][0]["qty"] == 800
    assert "极端拥挤禁开" not in v2["algo_notes"]


def test_crowding_gate_only_hits_crowded_source_board() -> None:
    items = [_card("600001", board="银行"), _card("600002", board="半导体")]
    v = apply_crowding_gate(_verdict(items), _crowd({"半导体": "extreme"}), phase="高潮")
    by = {x["code"]: x for x in v["recommend"]["items"]}
    assert by["600001"]["ready"] is True
    assert by["600002"]["ready"] is False
    assert v["recommend"]["buy"] is True


def test_crowding_gate_noop_when_not_ok() -> None:
    v = apply_crowding_gate(_verdict([_card("600001")]), {"ok": False, "note": "x"})
    assert v["recommend"]["items"][0]["ready"] is True
    assert v["crowding"]["ok"] is False


def _pts(day: str, rows: list[tuple[str, float, float]]) -> list[dict]:
    return [{"time": f"{day} {t}", "price": p, "volume": v} for t, p, v in rows]


def _minutes(start: str, n: int) -> list[str]:
    h, m = int(start[:2]), int(start[3:])
    out = []
    for i in range(n):
        mm = h * 60 + m + i
        out.append(f"{mm // 60:02d}:{mm % 60:02d}")
    return out


def test_minute_baseline_excludes_today_and_needs_two_samples() -> None:
    days = {
        "2026-09-28": _pts("2026-09-28", [("10:00", 1.0, 100), ("10:01", 1.0, 300)]),
        "2026-09-29": _pts("2026-09-29", [("10:00", 1.0, 200)]),
        "2026-10-01": _pts("2026-10-01", [("10:00", 1.0, 9999)]),
    }
    base = minute_baseline(days, exclude_day="2026-10-01")
    assert base == {"10:00": 150.0}


def _rescue_series(day: str = "2026-10-01") -> list[dict]:
    rows = []
    for t in _minutes("09:31", 40):
        if t < "09:50":
            rows.append((t, 9.85, 100))
        elif t < "10:00":
            rows.append((t, 9.85, 100))
        elif t <= "10:02":
            rows.append((t, 9.85 + 0.03 * (int(t[3:]) + 1), 600))
        else:
            rows.append((t, 9.95, 100))
    return _pts(day, rows)


def test_scan_pulses_flags_rescue_after_dip() -> None:
    base = {t: 100.0 for t in _minutes("09:30", 60)}
    hits = scan_pulses(_rescue_series(), base, prev_close=10.0)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["kind"] == "rescue"
    assert hit["ratio"] >= 4.0 and hit["px_move"] >= 0.3
    assert hit["dip"] == 1.5


def test_scan_pulses_surge_without_dip_and_skips_open() -> None:
    base = {t: 100.0 for t in _minutes("09:30", 60)}
    hits = scan_pulses(_rescue_series(), base, prev_close=9.8)
    assert hits and hits[0]["kind"] == "surge"
    opening = _pts("2026-10-01", [(t, 10.0 + 0.1 * i, 900) for i, t in enumerate(_minutes("09:30", 5))])
    assert scan_pulses(opening, base, prev_close=10.0) == []


def test_market_pulse_event_needs_two_etfs_in_window() -> None:
    near = {
        "510300": [{"at": "10:02", "kind": "rescue"}],
        "510500": [{"at": "10:08", "kind": "rescue"}],
    }
    ev = market_pulse_event(near, names={"510300": "沪深300ETF", "510500": "中证500ETF"})
    assert ev["event"] and ev["at"] == "10:08"
    assert ev["label"] == "宽基托底脉冲：沪深300ETF、中证500ETF"
    far = {
        "510300": [{"at": "10:02", "kind": "rescue"}],
        "510500": [{"at": "10:30", "kind": "rescue"}, {"at": "10:40", "kind": "surge"}],
    }
    ev2 = market_pulse_event(far)
    assert not ev2["event"]
    assert ev2["latest"]["at"] == "10:40"


def _zt(code: str, industry: str, boards: int = 1, seal: int = 93500) -> dict:
    return {"code": code, "name": f"Z{code}", "industry": industry, "boards": boards, "first_seal": seal}


def test_narrative_clusters_cross_industry_only() -> None:
    concepts = [
        {"name": "算力", "kind": "concept", "pool": [{"code": c} for c in ("600001", "600002", "600003")]},
        {"name": "液冷", "kind": "concept", "members": [{"code": c} for c in ("600002", "600003", "600004")]},
        {"name": "白酒概念", "kind": "concept", "pool": [{"code": c} for c in ("600009", "600010")]},
    ]
    zt = [
        _zt("600001", "通信设备", boards=2, seal=100500),
        _zt("600002", "通信设备", seal=93100),
        _zt("600003", "电力设备"),
        _zt("600004", "电力设备", boards=2, seal=93000),
        _zt("600009", "白酒"),
        _zt("600010", "白酒"),
    ]
    out = build_narrative_clusters(concepts, zt, mainline="银行")
    assert len(out) == 1
    c = out[0]
    assert c["label"] in ("算力", "液冷") and set(c["concepts"]) == {"算力", "液冷"}
    assert c["zt_n"] == 4 and c["industries"] == 2 and c["hidden"]
    assert c["leaders"][0] == "Z600004"
    assert build_narrative_clusters(concepts, zt, mainline="算力")[0]["hidden"] is False


def test_summarize_leads_counts_hidden_hits() -> None:
    rows = [
        {"trade_date": "2026-09-30", "label": "算力", "concepts": ["算力"], "mainline_at_first": "银行",
         "first_seen": "10:00:00", "became_mainline_at": "10:12:00"},
        {"trade_date": "2026-09-30", "label": "液冷", "concepts": ["液冷"], "mainline_at_first": "银行",
         "first_seen": "10:00:00", "became_mainline_at": None},
        {"trade_date": "2026-10-01", "label": "银行", "concepts": ["银行"], "mainline_at_first": "银行",
         "first_seen": "09:40:00", "became_mainline_at": "09:40:00"},
    ]
    s = summarize_leads(rows)
    assert s == {"days": 2, "sightings": 2, "hits": 1, "median_lead_min": 12, "hit_rate": 0.5}


def test_radar_db_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    desk_db.save_board_crowding("2026-09-29", [{"name": "半导体", "kind": "industry", "share": 6.0}], MARKET)
    desk_db.save_board_crowding("2026-09-30", [{"name": "半导体", "kind": "industry", "share": 7.0}], MARKET)
    desk_db.save_board_crowding("2026-09-30", [{"name": "半导体", "kind": "industry", "share": 7.5}], MARKET)
    desk_db.save_board_crowding("2026-10-01", [{"name": "半导体", "kind": "industry", "share": 9.0}], MARKET)
    assert desk_db.load_board_crowding_history("2026-10-01") == {"半导体": [6.0, 7.5]}

    hits = [{"code": "510300", "at": "10:02", "kind": "rescue", "ratio": 5.0, "px_move": 0.5}]
    desk_db.save_etf_pulse("2026-10-01", hits)
    desk_db.save_etf_pulse("2026-10-01", hits)
    assert len(desk_db.load_etf_pulse("2026-10-01")) == 1

    cl = [{"label": "算力", "concepts": ["算力", "液冷"], "zt_n": 4, "industries": 2}]
    desk_db.upsert_narrative_shadow("2026-10-01", cl, at="10:00:00", mainline="银行")
    cl[0]["zt_n"] = 6
    desk_db.upsert_narrative_shadow("2026-10-01", cl, at="10:10:00", mainline="液冷")
    desk_db.upsert_narrative_shadow("2026-10-01", cl, at="10:20:00", mainline="银行")
    row = desk_db.load_narrative_shadow("2026-10-01")[0]
    assert row["first_seen"] == "10:00:00" and row["last_seen"] == "10:20:00"
    assert row["peak_zt"] == 6 and row["mainline_at_first"] == "银行"
    assert row["became_mainline_at"] == "10:10:00"
    assert summarize_leads([row])["median_lead_min"] == 10

    shadow = [
        {"code": "600001", "name": "A", "board": "半导体", "box": "recommend", "mode": "soft", "was_ready": True, "price": 10.0, "share": 13.0},
    ]
    desk_db.save_crowd_shadow("2026-10-01", shadow, at="10:00:00")
    shadow[0].update(mode="hard", price=11.0)
    desk_db.save_crowd_shadow("2026-10-01", shadow, at="10:30:00")
    got = desk_db.load_crowd_shadow("2026-10-01")
    assert len(got) == 1
    assert got[0]["mode"] == "soft" and got[0]["price"] == 10.0 and got[0]["was_ready"] == 1


def _alert_snap(modes: dict[str, str], *, pulse: bool = False) -> dict:
    return {
        "ok": True,
        "trade_date": "2026-10-01",
        "verdict": {"mainline": {"name": "半导体"}},
        "radar": {
            "crowding": {
                "extreme": list(modes),
                "modes": modes,
                "confirm": {n: ["相位高潮"] for n, m in modes.items() if m == "hard"},
                "banned": [],
                "soft": [],
                "shares": {"半导体": 13.0},
            },
            "etf_pulse": {
                "event": pulse,
                "event_at": "10:08",
                "label": "宽基托底脉冲：A、B",
                "phase_hint": "恐慌拐点候选（只提示）",
            },
        },
    }


def test_radar_alerts_edge_fire_crowd_and_pulse() -> None:
    prev = {"ok": True, "radar": {"crowding": {"extreme": []}, "etf_pulse": {"event": False}}}
    cur = _alert_snap({"半导体": "soft", "白酒": "hard"}, pulse=True)
    alerts = build_radar_alerts(prev, cur)
    keys = [a[0] for a in alerts]
    assert keys == ["crowd:soft:半导体", "pulse:rescue:2026-10-01"]
    assert alerts[0][1] == "【极端拥挤·半仓】半导体"
    assert "13.0%" in alerts[0][2]
    assert "恐慌拐点候选" in alerts[1][2]
    assert build_radar_alerts(cur, cur) == []
    assert all(is_decision_toast(k) for k in keys)
    assert toast_priority("crowd:x") < toast_priority("pulse:x")


def test_radar_alerts_escalate_soft_to_hard_only() -> None:
    soft = _alert_snap({"半导体": "soft"})
    hard = _alert_snap({"半导体": "hard"})
    up = build_radar_alerts(soft, hard)
    assert [a[0] for a in up] == ["crowd:hard:半导体"]
    assert up[0][1] == "【极端拥挤·禁开】半导体" and "相位高潮" in up[0][2]
    assert build_radar_alerts(hard, soft) == []
