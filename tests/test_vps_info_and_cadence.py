"""VPS info helpers, board cadence cache and clist backoff knobs."""

from __future__ import annotations

import asyncio

import market_desk.engine.refresh as refresh_mod
from market_desk.engine.core import DeskEngine
from market_desk.eastmoney import client as em_client
from scripts.vps_info import latest_artifact_url, summarize_runs


def test_summarize_runs_flattens_fields():
    payload = {
        "workflow_runs": [
            {"id": 2, "name": "VPS info", "status": "completed", "conclusion": "success",
             "created_at": "2026-11-17T09:00:00Z", "html_url": "https://x/2", "head_sha": "abcdef123456"},
            {"id": 1, "name": "Deploy", "status": "in_progress", "conclusion": None,
             "created_at": "2026-11-17T08:00:00Z", "html_url": "https://x/1", "head_sha": "1234567890ab"},
        ]
    }
    rows = summarize_runs(payload)
    assert rows[0]["id"] == 2 and rows[0]["head_sha"] == "abcdef12"
    assert rows[1]["conclusion"] is None


def test_latest_artifact_skips_unfinished_runs(monkeypatch):
    calls = []

    def fake_api(url, raw=False):
        calls.append(url)
        return {"artifacts": [{"name": "vps-info", "expired": False, "archive_download_url": "https://zip"}]}

    monkeypatch.setattr("scripts.vps_info._api", fake_api)
    runs = [
        {"id": 9, "status": "in_progress"},
        {"id": 8, "status": "completed"},
    ]
    assert latest_artifact_url(runs) == "https://zip"
    assert calls == ["https://api.github.com/repos/lurenXYN/market-desk/actions/runs/8/artifacts"]


def test_board_bundle_is_cached_within_ttl(monkeypatch):
    calls = {"boards": 0, "flow": 0}

    async def fake_boards(client, **kwargs):
        calls["boards"] += 1
        return [{"name": "电子"}]

    async def fake_flow(client, kind, *args, **kwargs):
        calls["flow"] += 1
        return {"ok": True, "kind": kind}

    monkeypatch.setattr(refresh_mod, "fetch_hot_boards", fake_boards)
    monkeypatch.setattr(refresh_mod, "fetch_board_fund_flow", fake_flow)
    monkeypatch.setattr(
        refresh_mod, "setting", lambda key, default=None: 300 if key == "boards_refresh_sec" else default
    )

    eng = DeskEngine()

    async def run_twice():
        first = await eng._boards_bundle(None, [])
        second = await eng._boards_bundle(None, [])
        return first, second

    first, second = asyncio.run(run_twice())
    assert first == second
    assert calls == {"boards": 1, "flow": 2}  # 第二轮命中缓存，三路只各取一次


def test_boards_bundle_call_matches_safe_signature(monkeypatch):
    """refresh() 用 _safe(fn, *args, errors=, label=) 调用；参数错位会让整轮刷新抛 TypeError。"""
    import market_desk.engine.util as util_mod
    from market_desk.engine.refresh import RefreshMixin

    async def fake_boards(client, **kwargs):
        return [{"name": "电子"}]

    async def fake_flow(client, kind, *args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(refresh_mod, "fetch_hot_boards", fake_boards)
    monkeypatch.setattr(refresh_mod, "fetch_board_fund_flow", fake_flow)
    monkeypatch.setattr(
        refresh_mod, "setting", lambda key, default=None: 0 if key == "boards_refresh_sec" else default
    )

    eng = DeskEngine()
    errs: list[str] = []

    async def run_call():
        return await util_mod._safe(
            eng._boards_bundle, None, errs, errors=errs, label="boards-bundle"
        )

    triple = asyncio.run(run_call())
    assert triple and triple[0][0]["name"] == "电子"
    assert errs == []


def test_clist_backoff_is_longer_and_more_tolerant():
    assert em_client._CLIST_BACKOFF_SEC >= 90.0
    assert em_client._CLIST_BACKOFF_AFTER >= 3
    assert {"82.push2.eastmoney.com", "7.push2.eastmoney.com", "push2delay.eastmoney.com"} <= set(
        em_client._CLIST_HOSTS
    )
