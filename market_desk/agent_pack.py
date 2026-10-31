"""Agent learning pack: one JSON dump for autonomous review / soft-hard decisions.

The decision-agency contract (TODO 2026-10-31) says the user supplies data and
the Agent judges against pre-registered thresholds. This pack is that data:
review-check items, ops health, sim gate, deep-V / zt-share ledgers, and a
``proposals`` list the Agent can act on without screenshots.
"""

from __future__ import annotations

from typing import Any

from market_desk.calendar import now_cn


AGENT_PACK_SCHEMA = "agent_pack.v2"


def _item_proposal(item: dict[str, Any]) -> dict[str, Any] | None:
    """Map one review-check row into a sample/verdict proposal (or None).

    ``sample=ready`` only means days/n cleared the TODO floor.
    ``verdict=hold`` is the default when ready; ``soft_candidate`` only when
    metrics clearly pass a known TODO shape (still needs Agent to ship).
    """
    if item.get("skip"):
        return None
    from market_desk.dual_score import soft_candidate_hint

    samp = item.get("sample") or {}
    enough = bool(samp.get("enough_for_todo"))
    level = str(item.get("level") or "")
    iid = str(item.get("id") or "")
    title = str(item.get("title") or iid)
    hint = str(item.get("hint") or "")
    ui = str(item.get("ui_verdict") or "")
    metrics = item.get("metrics") or {}
    wins = {
        k: metrics.get(k)
        for k in ("abs_win", "xs_win", "reclaim_abs_win", "wins_note")
        if metrics.get(k) is not None
    }
    # Start gate owns its own readiness (equity > 0), not the 10d/30n floor.
    if iid == "sim_desk_gate":
        gate_ok = bool(metrics.get("gate_ok"))
        vs = metrics.get("vs_bench")
        if gate_ok:
            return {
                "id": iid,
                "title": title,
                "sample": "ready",
                "verdict": "soft_candidate",
                "reason": "gate_ok=true · 可按排期推进券商只读（首次真钱仍须一句确认）",
                "metrics": metrics,
                "wins": wins or None,
            }
        return {
            "id": iid,
            "title": title,
            "sample": "thin",
            "verdict": "watch",
            "reason": f"未过线 · {ui or hint}",
            "metrics": {"vs_bench": vs} if vs else metrics,
            "wins": wins or None,
        }
    if not enough:
        return {
            "id": iid,
            "title": title,
            "sample": "thin",
            "verdict": "watch",
            "reason": f"样本不足（days={samp.get('days')} n={samp.get('n')}）· {ui or hint}",
            "wins": wins or None,
        }
    candidate = soft_candidate_hint(item if isinstance(item, dict) else {})
    verdict = "soft_candidate" if candidate else "hold"
    return {
        "id": iid,
        "title": title,
        "sample": "ready",
        "verdict": verdict,
        "reason": (
            f"样本够门槛 · level={level} · "
            + (
                "指标形似过门槛·仍须 Agent 对照 TODO 全条件再改"
                if candidate
                else f"默认 hold · {ui or hint}"
            )
        ),
        "hint": hint,
        "metrics": metrics,
        "ui_verdict": ui,
        "wins": wins or None,
    }


def build_agent_pack_sync() -> dict[str, Any]:
    """Build the fast (no kline) half of the pack."""
    from market_desk.calendar import today_cn
    from market_desk.config import (
        DEEP_V_SHADOW_SINCE,
        EDGE_SHADOW_SINCE,
        REGIME_SHADOW_SINCE,
        ZT_SHARE_SHADOW_SINCE,
    )
    from market_desk.db import load_deep_v_day, load_signals, load_zt_board_share
    from market_desk.deep_v import build_deep_v_shadow
    from market_desk.notify import push_origin, serverchan_quota_snapshot
    from market_desk.review.signals import _dragon_hide_from_review, is_buy_signal
    from market_desk.review_check import build_review_check_sync
    from market_desk.routes.ops import build_ops_check
    from market_desk.shadow_health import shadow_check, shadow_health
    from market_desk.sim_desk import build_sim_attribution, start_gate_snapshot
    from market_desk.zt_share import build_zt_share_review

    asof = now_cn().strftime("%Y-%m-%d %H:%M:%S")
    day = today_cn().isoformat() if hasattr(today_cn(), "isoformat") else str(today_cn())[:10]

    review = build_review_check_sync()
    ops = build_ops_check()
    gate = start_gate_snapshot()
    try:
        attr = build_sim_attribution()
    except Exception as exc:
        attr = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}

    buy_rows = [
        r
        for r in load_signals(limit=4000)
        if is_buy_signal(r.get("signal_type")) and not _dragon_hide_from_review(r)
    ]
    deep_v_shadow = build_deep_v_shadow(buy_rows)
    deep_v_today = load_deep_v_day(day) or {"ok": False, "note": "今日未分类"}
    zt_share = build_zt_share_review(load_zt_board_share(str(ZT_SHARE_SHADOW_SINCE)[:10]))

    try:
        shadows = shadow_check(shadow_health(3))
    except Exception as exc:
        shadows = {"id": "shadows", "level": "warn", "detail": f"{type(exc).__name__}: {exc}"[:120]}

    try:
        quota = serverchan_quota_snapshot()
    except Exception as exc:
        quota = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    proposals = []
    seen: set[str] = set()
    for it in review.get("items") or []:
        p = _item_proposal(it if isinstance(it, dict) else {})
        if not p:
            continue
        iid = str(p.get("id") or "")
        if iid and iid in seen:
            continue
        if iid:
            seen.add(iid)
        proposals.append(p)

    # Sim gate fallback when review-check omitted the row.
    if "sim_desk_gate" not in seen:
        vs = gate.get("vs_bench") if isinstance(gate.get("vs_bench"), dict) else {}
        if gate.get("gate_ok"):
            proposals.append(
                {
                    "id": "sim_desk_gate",
                    "title": "模拟开工闸",
                    "sample": "ready",
                    "verdict": "soft_candidate",
                    "reason": "gate_ok=true · 可按排期推进券商只读（首次真钱仍须一句确认）",
                    "metrics": {
                        "cum_pnl_pct": gate.get("cum_pnl_pct"),
                        "closed_n": gate.get("closed_n"),
                        "vs_bench": vs,
                    },
                }
            )
        else:
            proposals.append(
                {
                    "id": "sim_desk_gate",
                    "title": "模拟开工闸",
                    "sample": "thin",
                    "verdict": "watch",
                    "reason": f"未过线 · {gate.get('gate_label') or 'gate_ok=false'}",
                    "metrics": {"vs_bench": vs} if vs else None,
                }
            )

    ready_n = sum(1 for p in proposals if p.get("sample") == "ready")
    hold_n = sum(1 for p in proposals if p.get("verdict") == "hold")
    soft_n = sum(1 for p in proposals if p.get("verdict") == "soft_candidate")
    watch_n = sum(1 for p in proposals if p.get("verdict") == "watch")
    dual = review.get("dual_wins") if isinstance(review.get("dual_wins"), dict) else {}
    vs_bench = gate.get("vs_bench") if isinstance(gate.get("vs_bench"), dict) else {}

    return {
        "ok": True,
        "schema": AGENT_PACK_SCHEMA,
        "asof": asof,
        "day": day,
        "origin": push_origin(),
        "since": {
            "edge": EDGE_SHADOW_SINCE,
            "regime": REGIME_SHADOW_SINCE,
            "deep_v": DEEP_V_SHADOW_SINCE,
            "zt_share": ZT_SHARE_SHADOW_SINCE,
        },
        "summary": {
            "ops_overall": ops.get("overall"),
            "sim_gate_ok": bool(gate.get("gate_ok")),
            "sim_cum_pnl_pct": gate.get("cum_pnl_pct"),
            "sim_vs_bench": vs_bench,
            "dual_wins": {
                "all": (dual.get("all") or {}) if dual.get("ok") else dual,
                "lit": (dual.get("lit") or {}) if dual.get("ok") else None,
            },
            "proposals_ready": ready_n,
            "proposals_hold": hold_n,
            "proposals_soft_candidate": soft_n,
            "proposals_watch": watch_n,
            "note": (
                "sample=ready 只表示天数/张数够看；verdict=hold 默认不改；"
                "soft_candidate 仅形似过门槛，仍须对照 TODO 全条件。一次最多改一处。"
                "abs_win=绝对胜率，xs_win=跑赢大盘胜率。"
            ),
        },
        "proposals": proposals,
        "dual_wins": dual,
        "review_check": review,
        "ops_check": {
            "overall": ops.get("overall"),
            "checks": ops.get("checks"),
            "host": ops.get("host"),
            "pid": ops.get("pid"),
        },
        "sim": {"gate": gate, "attribution": attr, "vs_bench": vs_bench},
        "deep_v": {"today": deep_v_today, "shadow": deep_v_shadow},
        "zt_share": zt_share,
        "shadows": shadows,
        "serverchan_quota": quota,
        "curl": {
            "admin": "GET /api/ops/agent-pack",
            "admin_cf": "GET /api/ops/agent-pack?cf=true&cf_days=20",
            "internal": "GET /api/internal/agent-pack  (localhost only)",
            "parts": [
                "GET /api/ops/review-check",
                "GET /api/ops/check",
                "GET /api/ops/sim-desk",
                "GET /api/ops/shadows",
                "GET /api/ops/serverchan-quota",
                "GET /api/ops/exec-desk",
            ],
        },
    }


async def build_agent_pack(*, cf: bool = False, cf_days: int = 20) -> dict[str, Any]:
    """Build the full pack; optionally merge counterfactual What-If into review_check."""
    pack = build_agent_pack_sync()
    if not cf:
        pack["review_check"]["counterfactual"] = {"ok": False, "skipped": True}
        pack["cf"] = {"ok": False, "skipped": True}
        return pack
    try:
        from market_desk.engine import engine
        from market_desk.review_check import merge_counterfactual

        audit = await engine.build_counterfactual(days=int(cf_days), strict=True)
        pack["review_check"] = merge_counterfactual(pack.get("review_check") or {}, audit)
        pack["cf"] = {"ok": bool(audit.get("ok")), "days": cf_days}
    except Exception as exc:
        pack["cf"] = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}
        pack["review_check"]["counterfactual"] = pack["cf"]
    return pack
