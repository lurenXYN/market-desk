"""Deep-V day classification and zt board-share lift table."""

from __future__ import annotations

import market_desk.db as desk_db
from market_desk.deep_v import build_deep_v_shadow, classify_deep_v_day, freeze_deep_v_fields
from market_desk.zt_share import annotate_lifts, build_zt_share_review, count_zt_by_board


def _points(open_px: float, low_px: float, close_px: float, n: int = 60) -> list[dict]:
    """Synthetic minute path: open → drift to low → rebound to close."""
    mid = n // 2
    out = []
    for i in range(n):
        if i <= mid:
            t = i / max(1, mid)
            px = open_px + (low_px - open_px) * t
        else:
            t = (i - mid) / max(1, n - 1 - mid)
            px = low_px + (close_px - low_px) * t
        out.append({"price": px})
    return out


def test_classify_deep_v_day_thresholds() -> None:
    # 2% drawdown, 60% recover → deep V
    deep = classify_deep_v_day(_points(100.0, 98.0, 99.2), dd_pct=1.5, recover_frac=0.5)
    assert deep["ok"] and deep["deep_v"] is True
    assert deep["dd_pct"] >= 1.5 and deep["recover"] >= 0.5
    # Shallow drawdown
    shallow = classify_deep_v_day(_points(100.0, 99.5, 99.8), dd_pct=1.5, recover_frac=0.5)
    assert shallow["ok"] and shallow["deep_v"] is False
    # Deep but no recover
    no_rec = classify_deep_v_day(_points(100.0, 98.0, 98.2), dd_pct=1.5, recover_frac=0.5)
    assert no_rec["ok"] and no_rec["deep_v"] is False


def test_freeze_deep_v_fields(monkeypatch) -> None:
    monkeypatch.setattr("market_desk.config.DEEP_V_SHADOW_SINCE", "2026-10-31")
    day = {"ok": True, "deep_v": True, "dd_pct": 2.0, "recover": 0.6}
    f = freeze_deep_v_fields(
        trade_date="2026-10-31",
        signaled_at="2026-10-31 14:05:00",
        ready=True,
        day_row=day,
        prev_deep=False,
    )
    assert f.get("deep_v_day") is True and f.get("deep_v_chase") is True
    lag = freeze_deep_v_fields(
        trade_date="2026-11-03",
        signaled_at="2026-11-03 10:00:00",
        ready=True,
        day_row={"ok": True, "deep_v": False},
        prev_deep=True,
    )
    assert lag.get("deep_v_lag1") is True and not lag.get("deep_v_chase")


def test_build_deep_v_shadow_groups(monkeypatch) -> None:
    monkeypatch.setattr("market_desk.config.DEEP_V_SHADOW_SINCE", "2026-10-31")
    rows = [
        {
            "signal_type": "buy",
            "trade_date": "2026-10-31",
            "outcome_day3_pct": -2.0,
            "payload": {"deep_v_chase": True},
        },
        {
            "signal_type": "buy",
            "trade_date": "2026-11-03",
            "outcome_day3_pct": 1.0,
            "payload": {"deep_v_lag1": True},
        },
    ]
    out = build_deep_v_shadow(rows)
    assert out["ok"] and out["a"]["n"] == 1 and out["b"]["n"] == 1
    assert out["delta"] == -3.0


def test_zt_share_count_and_lift() -> None:
    zt = [
        {"code": "600001", "industry": "半导体"},
        {"code": "600002", "industry": "半导体"},
        {"code": "600003", "industry": "半导体"},
        {"code": "600004", "industry": "银行"},
    ]
    concepts = [
        {"name": "芯片", "kind": "concept", "members": [{"code": "600001"}, {"code": "600002"}, {"code": "600003"}]},
    ]
    today = count_zt_by_board(zt, concept_boards=concepts)
    by = {(r["kind"], r["board"]): r for r in today}
    assert by[("industry", "半导体")]["n"] == 3
    assert by[("concept", "芯片")]["share"] == 75.0
    hist = []
    for i in range(20):
        hist.append(
            {
                "trade_date": f"2026-10-{i+1:02d}" if i < 30 else "2026-10-01",
                "kind": "industry",
                "board": "半导体",
                "share": 20.0,
            }
        )
    annotated = annotate_lifts(
        [by[("industry", "半导体")]],
        hist,
        lookback=20,
        z_thr=1.0,
        min_n=3,
    )
    assert annotated[0]["lift"] == 1
    rev = build_zt_share_review(
        [{"trade_date": "2026-10-31", "board": "半导体", "kind": "industry", "n": 3, "share": 75, "z": 3, "lift": 1}]
    )
    assert rev["ok"] and rev["lift_days"] == 1


def test_deep_v_day_db_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    desk_db.init_db()
    from market_desk.db import load_deep_v_day, save_deep_v_day, save_zt_board_share, load_zt_board_share

    save_deep_v_day(
        "2026-10-31",
        {"ok": True, "deep_v": True, "dd_pct": 2.1, "recover": 0.55, "open": 1, "low": 0.9, "close": 0.95, "n": 100, "at": "15:05:00"},
        provisional=False,
    )
    row = load_deep_v_day("2026-10-31")
    assert row and row["deep_v"] is True and row["provisional"] is False
    save_zt_board_share(
        "2026-10-31",
        [{"board": "半导体", "kind": "industry", "n": 5, "total": 40, "share": 12.5, "lift": 1, "z": 1.2}],
        at="15:00:00",
    )
    rows = load_zt_board_share("2026-10-31", "2026-10-31", lifts_only=True)
    assert len(rows) == 1 and rows[0]["board"] == "半导体"
