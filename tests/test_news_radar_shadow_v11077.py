"""News-radar freeze blob + empty shadow summary (no network)."""

from __future__ import annotations

from market_desk.news_radar import freeze_news_radar, news_radar_shadow_summary


def test_freeze_news_radar_hit_and_miss():
    snap = {
        "news_radar": {
            "enabled": True,
            "ok": True,
            "status": "online",
            "status_label": "在线·同主线",
            "sectors": [
                {
                    "sector": "半导体",
                    "confirm": "resonance",
                    "confirm_label": "共振",
                    "score": 12,
                    "board_pct": 2.1,
                    "delta": 3.0,
                },
                {
                    "sector": "白酒/消费",
                    "confirm": "news_only",
                    "confirm_label": "仅新闻",
                    "score": 20,
                    "board_pct": 0.2,
                    "delta": 5.0,
                },
            ],
        }
    }
    hit = freeze_news_radar(snap, board_names=["半导体设备"], mainline="半导体")
    assert hit is not None
    assert hit["hit"] is True
    assert hit["confirm"] == "resonance"
    assert hit["sector"] == "半导体"

    miss = freeze_news_radar(snap, board_names=["军工"], mainline="军工")
    assert miss is not None
    assert miss["hit"] is False


def test_freeze_disabled_and_missing():
    missing = freeze_news_radar({})
    assert missing is not None
    assert missing["hit"] is False
    assert missing["status"] == "missing"
    assert missing["matches_n"] == 0
    off = freeze_news_radar({"news_radar": {"enabled": False}})
    assert off is not None
    assert off["hit"] is False
    assert off["status"] == "disabled"


def test_shadow_summary_runs(tmp_path, monkeypatch):
    import market_desk.db as desk_db
    from market_desk.db.schema import init_db

    monkeypatch.setattr(desk_db, "DB_PATH", tmp_path / "desk.db")
    monkeypatch.setattr(desk_db, "DATA_DIR", tmp_path)
    init_db()
    out = news_radar_shadow_summary(days=30)
    assert out["ok"] is True
    assert "with_news_hit" in out
    assert "without_news_hit" in out
