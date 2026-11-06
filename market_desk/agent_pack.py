"""Agent learning pack: one JSON dump for autonomous review / soft-hard decisions.

The decision-agency contract (TODO 2026-10-31) says the user supplies data and
the Agent judges against pre-registered thresholds. This pack is that data:
review-check items, ops health, sim gate, deep-V / zt-share ledgers, and a
``proposals`` list the Agent can act on without screenshots.
"""

from __future__ import annotations

from typing import Any

from market_desk.calendar import now_cn


AGENT_PACK_SCHEMA = "agent_pack.v6"

_GATE_LIVE_KEYS = ("minute", "off_high", "thin")


def _slim_size_block(block: dict[str, Any] | None, *, kind: str) -> dict[str, Any]:
    """Keep the knobs Agent needs to audit live size soft-controls."""
    b = block if isinstance(block, dict) else {}
    out: dict[str, Any] = {
        "ok": bool(b.get("ok")),
        "n": b.get("n"),
        "size_mult": b.get("size_mult", 1.0),
        "note": b.get("note") or "",
        "source": kind,
    }
    for key in (
        "win_rate",
        "consec_loss",
        "profit_factor",
        "hit_n",
        "score",
        "chase_n",
        "chase_ratio",
    ):
        if b.get(key) is not None:
            out[key] = b.get(key)
    return out


def _slim_buy_gate_slice(row: dict[str, Any] | None) -> dict[str, Any]:
    """Compress one buy_gate_bias kind slice to mult / gates / note."""
    r = row if isinstance(row, dict) else {}
    gates_raw = r.get("gates") if isinstance(r.get("gates"), dict) else {}
    gates = {
        str(k): {
            "mult": (g or {}).get("mult"),
            "mode": (g or {}).get("mode"),
            "false_kill_n": (g or {}).get("false_kill_n"),
            "true_kill_n": (g or {}).get("true_kill_n"),
        }
        for k, g in gates_raw.items()
        if isinstance(g, dict)
    }
    return {
        "ok": bool(r.get("ok")),
        "loosen": bool(r.get("loosen")),
        "tighten": bool(r.get("tighten")),
        "mult": r.get("mult", 1.0),
        "gates": gates,
        "note": r.get("note") or "",
    }


def _live_gate_resolved() -> dict[str, dict[str, float]]:
    """Resolve stacked live gate mults for stock and ETF (minute/off_high/thin)."""
    from market_desk.adapt import resolve_gate_mult

    out: dict[str, dict[str, float]] = {}
    for kind in ("stock", "etf"):
        out[kind] = {
            key: round(float(resolve_gate_mult(key, 1.0, kind=kind)), 4)
            for key in _GATE_LIVE_KEYS
        }
    return out


def _slim_knob(block: dict[str, Any] | None, *, keys: tuple[str, ...]) -> dict[str, Any] | None:
    """Copy selected knob fields; return None when block is empty."""
    if not isinstance(block, dict):
        return None
    out: dict[str, Any] = {"ok": bool(block.get("ok"))}
    for key in keys:
        if block.get(key) is not None:
            out[key] = block.get(key)
    if block.get("note") is not None:
        out["note"] = block.get("note") or ""
    return out


def _segment_key_from_warm_or_clock(warm: dict[str, Any] | None) -> str:
    """Pick segment for sell-band rebuild: warm context, else clock bucket."""
    if isinstance(warm, dict):
        ctx = warm.get("context") if isinstance(warm.get("context"), dict) else {}
        seg = str(ctx.get("segment") or "").strip()
        if seg:
            return seg
        seg_sell = warm.get("segment_sell") if isinstance(warm.get("segment_sell"), dict) else {}
        seg = str(seg_sell.get("segment") or "").strip()
        if seg:
            return seg
    try:
        from market_desk.adapt import segment_bucket_from_clock
        from market_desk.calendar import now_cn

        return str(segment_bucket_from_clock(now_cn()) or "morning")
    except Exception:
        return "morning"


def build_adapt_live_snapshot(
    buy_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Snapshot today's soft size / gate / band knobs for Agent audit.

    Prefers the warm ``verdict.adapt`` on the engine snapshot when present so the
    pack matches what the desk is already applying; otherwise rebuilds from
    signals. Never flips settings — read-only.
    """
    from market_desk.settings import setting

    warm: dict[str, Any] | None = None
    warm_src = "none"
    try:
        from market_desk.engine import engine

        adapt = ((engine.snapshot or {}).get("verdict") or {}).get("adapt")
        if isinstance(adapt, dict) and adapt.get("ok"):
            warm = adapt
            warm_src = "engine.snapshot"
    except Exception:
        warm = None

    rows = buy_rows
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=240)
    except Exception:
        rows = []

    sweet_raw: dict[str, Any] | None = None
    tune_raw: dict[str, Any] | None = None
    sell_mfe_raw: dict[str, Any] | None = None
    seg_sell_raw: dict[str, Any] | None = None
    phase_kind_raw: dict[str, Any] | None = None
    desk_src_raw: dict[str, Any] | None = None

    if warm is not None:
        heat = _slim_size_block(warm.get("size_heat"), kind="warm")
        exec_size = _slim_size_block(warm.get("exec_size"), kind="warm")
        size_compose = warm.get("size_compose") if isinstance(warm.get("size_compose"), dict) else {}
        size_mult = warm.get("size_mult", size_compose.get("size_mult", 1.0))
        size_factors = warm.get("size_factors") or size_compose.get("factors") or []
        outcome_basis = warm.get("outcome_basis") or "classic"
        outcome_basis_note = warm.get("outcome_basis_note") or ""
        context_gate = warm.get("context_gate") if isinstance(warm.get("context_gate"), dict) else {}
        sweet_raw = warm.get("pullback_sweet") if isinstance(warm.get("pullback_sweet"), dict) else None
        tune_raw = warm.get("auto_tune") if isinstance(warm.get("auto_tune"), dict) else None
        sell_mfe_raw = warm.get("sell_mfe") if isinstance(warm.get("sell_mfe"), dict) else None
        seg_sell_raw = warm.get("segment_sell") if isinstance(warm.get("segment_sell"), dict) else None
        phase_kind_raw = warm.get("phase_kind") if isinstance(warm.get("phase_kind"), dict) else None
        desk_src_raw = (
            warm.get("desk_source_bias") if isinstance(warm.get("desk_source_bias"), dict) else None
        )
    else:
        from market_desk.adapt import (
            build_exec_size_bias,
            build_pullback_sweet,
            build_sell_mfe_bias,
            build_size_heat,
            compose_size_mult,
            resolve_auto_tune,
            segment_sell_mult,
        )
        from market_desk.adapt.bundle import (
            _remap_rows_for_adapt_standard,
            closes_map_from_bar_cache,
        )

        # Align cold rebuild with warm adapt: same_day_plan remap (strip classic
        # when bars missing) so Actions dump / offline pack matches live learning.
        learn_rows = list(rows or [])
        remapped, n_hit = _remap_rows_for_adapt_standard(
            learn_rows,
            "same_day_plan",
            closes_map=closes_map_from_bar_cache(
                [str(r.get("code") or "") for r in learn_rows]
            )
            or None,
        )
        learn_rows = remapped
        outcome_basis = "same_day_plan"
        if n_hit > 0:
            outcome_basis_note = (
                f"pack 重建（引擎无暖 adapt）· same_day_plan 已重算 {n_hit} 笔"
            )
        else:
            outcome_basis_note = (
                "pack 重建（引擎无暖 adapt）· same_day_plan 暂无日线，"
                "已清空不可核验的 classic 三日%"
            )

        heat_raw = build_size_heat(learn_rows, use_cache=False)
        exec_raw = build_exec_size_bias(learn_rows, use_cache=False)
        heat = _slim_size_block(heat_raw, kind="rebuilt")
        exec_size = _slim_size_block(exec_raw, kind="rebuilt")
        composed = compose_size_mult(
            [
                {"key": "heat", "label": "仓位热度", "mult": float(heat.get("size_mult") or 1.0)},
                {"key": "exec", "label": "执行分", "mult": float(exec_size.get("size_mult") or 1.0)},
            ]
        )
        size_compose = composed
        size_mult = composed.get("size_mult", 1.0)
        size_factors = composed.get("factors") or []
        context_gate = {}
        warm_src = "rebuilt"
        sweet_raw = build_pullback_sweet(learn_rows, use_cache=False)
        tune_raw = resolve_auto_tune()
        sell_mfe_raw = build_sell_mfe_bias(learn_rows, use_cache=False)
        seg_sell_raw = segment_sell_mult(
            _segment_key_from_warm_or_clock(None), learn_rows, use_cache=False
        )

    try:
        from market_desk.adapt import build_sticky_margin_bias

        sticky_raw = build_sticky_margin_bias(use_cache=True)
    except Exception as exc:
        sticky_raw = {"ok": False, "mult": 1.0, "note": f"{type(exc).__name__}: {exc}"[:120]}

    try:
        from market_desk.review import cached_buy_gate_bias

        gate_bundle = cached_buy_gate_bias() or {}
    except Exception as exc:
        gate_bundle = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    buy_gate = {
        "ok": bool(gate_bundle.get("ok")),
        "schema": gate_bundle.get("schema") or "buy_gate_bias.v2",
        "note": gate_bundle.get("note") or "",
        "all": _slim_buy_gate_slice(gate_bundle.get("all") or gate_bundle),
        "etf": _slim_buy_gate_slice(gate_bundle.get("etf")),
        "stock": _slim_buy_gate_slice(gate_bundle.get("stock")),
        "live_resolved": _live_gate_resolved(),
    }

    ctx_slim: dict[str, Any] | None = None
    if isinstance(context_gate, dict) and context_gate.get("ok"):
        ctx_slim = {
            "ok": True,
            "mult": context_gate.get("mult"),
            "gates": context_gate.get("gates") or {},
            "note": context_gate.get("note") or "",
            "matched_n": context_gate.get("matched_n"),
        }

    phase_slim: dict[str, Any] | None = None
    if isinstance(phase_kind_raw, dict):
        phase_slim = {}
        for k in ("stock", "etf"):
            row = phase_kind_raw.get(k) if isinstance(phase_kind_raw.get(k), dict) else None
            if row:
                phase_slim[k] = _slim_size_block(row, kind=warm_src)

    return {
        "ok": True,
        "warm_source": warm_src,
        "adapt_follow_outcome": bool(setting("adapt_follow_outcome", False)),
        "env_gate_live": bool(setting("env_gate_live", False)),
        "outcome_basis": outcome_basis,
        "outcome_basis_note": outcome_basis_note,
        "size_heat": heat,
        "exec_size": exec_size,
        "size_mult": size_mult,
        "size_compose": {
            "size_mult": size_compose.get("size_mult") if isinstance(size_compose, dict) else size_mult,
            "raw_product": size_compose.get("raw_product") if isinstance(size_compose, dict) else None,
            "clamped": size_compose.get("clamped") if isinstance(size_compose, dict) else None,
            "factors": size_factors,
            "note": (size_compose.get("note") if isinstance(size_compose, dict) else "") or "",
        },
        "phase_kind": phase_slim,
        "desk_source_bias": _slim_knob(
            desk_src_raw, keys=("link_mult", "trial_mult")
        ),
        "buy_gate": buy_gate,
        "context_gate": ctx_slim,
        "pullback_sweet": _slim_knob(
            sweet_raw, keys=("n", "sweet_max", "band_max", "mae_p50", "mae_p80")
        ),
        "auto_tune": _slim_knob(
            tune_raw,
            keys=(
                "pb_min_mult",
                "gate_mult",
                "miss_mult",
                "kind_mult",
                "conflict",
                "clamp",
                "missed_bucket_n",
            ),
        ),
        "sticky_margin": _slim_knob(
            sticky_raw, keys=("mult", "margin", "base_margin", "stats")
        ),
        "sell_mfe": _slim_knob(
            sell_mfe_raw, keys=("n", "mult", "p50", "widen", "tighten")
        ),
        "segment_sell": _slim_knob(
            seg_sell_raw,
            keys=("segment", "mult", "prior", "widen", "tighten", "source"),
        ),
        "note": (
            "size_heat/exec_size 为当日软仓位；buy_gate.live_resolved 已叠假杀×情景×"
            "（可选）env；env_gate_live 默认关时 env 项为 1.0。"
            "另含回踩甜区/auto_tune/sticky/卖带宽现倍率。"
        ),
    }


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
    # Start gate: absolute → 真钱；relative excess → 券商只读 only.
    if iid == "sim_desk_gate":
        gate_ok = bool(metrics.get("gate_ok"))
        readonly = bool(metrics.get("gate_readonly_ok"))
        vs = metrics.get("vs_bench")
        if gate_ok:
            return {
                "id": iid,
                "title": title,
                "sample": "ready",
                "verdict": "soft_candidate",
                "reason": "gate_ok=true · 绝对转正，可议真钱阶段（首次仍须一句确认）",
                "metrics": metrics,
                "wins": wins or None,
            }
        if readonly:
            return {
                "id": iid,
                "title": title,
                "sample": "ready",
                "verdict": "soft_candidate",
                "reason": "gate_readonly_ok · 相对大盘超额>0，可推进券商只读（不下单）",
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
        DEFENSE_SHADOW_SINCE,
        EDGE_SHADOW_SINCE,
        ENV_GATE_SHADOW_SINCE,
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
    pool_shadow = None
    try:
        from market_desk.engine import engine

        pool_shadow = ((engine.snapshot or {}).get("verdict") or {}).get("pool_shadow")
    except Exception:
        pool_shadow = None

    buy_rows = [
        r
        for r in load_signals(limit=4000)
        if is_buy_signal(r.get("signal_type")) and not _dragon_hide_from_review(r)
    ]
    try:
        from market_desk.db.sim_desk import load_sim_fills
        from market_desk.sim_desk import sim_channel_split

        sim_channels = sim_channel_split(load_sim_fills(limit=300), buy_rows=buy_rows)
    except Exception as exc:
        sim_channels = (attr.get("by_channel") if isinstance(attr, dict) else None) or {
            "ok": False,
            "note": f"{type(exc).__name__}: {exc}"[:120],
        }
    deep_v_shadow = build_deep_v_shadow(buy_rows)
    deep_v_today = load_deep_v_day(day) or {"ok": False, "note": "今日未分类"}
    zt_share = build_zt_share_review(load_zt_board_share(str(ZT_SHARE_SHADOW_SINCE)[:10]))

    try:
        shadows = shadow_check(shadow_health(3))
    except Exception as exc:
        shadows = {"id": "shadows", "level": "warn", "detail": f"{type(exc).__name__}: {exc}"[:120]}

    try:
        from market_desk.env_gate import summarize_env_gate_coverage

        env_gate_summary = summarize_env_gate_coverage(buy_rows)
    except Exception as exc:
        env_gate_summary = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    try:
        from market_desk.gate_replay import build_gate_replay_from_db

        gate_replay = build_gate_replay_from_db(days=20)
    except Exception as exc:
        gate_replay = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    try:
        from market_desk.fill_rules import fill_rules_parity

        fill_rules = fill_rules_parity()
        fill_comparable = fill_rules.get("comparable_roundtrip")
    except Exception as exc:
        fill_rules = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}
        fill_comparable = None

    try:
        quota = serverchan_quota_snapshot()
    except Exception as exc:
        quota = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    try:
        # Prefer engine warm adapt; builders reload ≤240 signals when rebuilt.
        adapt_live = build_adapt_live_snapshot(None)
    except Exception as exc:
        adapt_live = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}

    try:
        from market_desk.adapt_effect import build_adapt_effect_panel

        adapt_effect = build_adapt_effect_panel(buy_rows)
    except Exception as exc:
        adapt_effect = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}

    try:
        from market_desk.np_review import build_np_review, signal_bar_np_coverage

        np_review = build_np_review(buy_rows)
        np_review["bar_coverage"] = signal_bar_np_coverage(buy_rows)
    except Exception as exc:
        np_review = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:160]}

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
        metrics = {
            "cum_pnl_pct": gate.get("cum_pnl_pct"),
            "closed_n": gate.get("closed_n"),
            "gate_ok": gate.get("gate_ok"),
            "gate_readonly_ok": gate.get("gate_readonly_ok"),
            "vs_bench": vs,
        }
        if gate.get("gate_ok"):
            proposals.append(
                {
                    "id": "sim_desk_gate",
                    "title": "模拟开工闸",
                    "sample": "ready",
                    "verdict": "soft_candidate",
                    "reason": "gate_ok=true · 绝对转正，可议真钱阶段（首次仍须一句确认）",
                    "metrics": metrics,
                }
            )
        elif gate.get("gate_readonly_ok"):
            proposals.append(
                {
                    "id": "sim_desk_gate",
                    "title": "模拟开工闸",
                    "sample": "ready",
                    "verdict": "soft_candidate",
                    "reason": "gate_readonly_ok · 相对大盘超额>0，可推进券商只读（不下单）",
                    "metrics": metrics,
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
                    "metrics": metrics,
                }
            )

    ready_n = sum(1 for p in proposals if p.get("sample") == "ready")
    hold_n = sum(1 for p in proposals if p.get("verdict") == "hold")
    soft_n = sum(1 for p in proposals if p.get("verdict") == "soft_candidate")
    watch_n = sum(1 for p in proposals if p.get("verdict") == "watch")
    dual = review.get("dual_wins") if isinstance(review.get("dual_wins"), dict) else {}
    vs_bench = gate.get("vs_bench") if isinstance(gate.get("vs_bench"), dict) else {}

    defense_snap: dict[str, Any] | None = None
    try:
        from market_desk.defense import build_defense_from_snapshot
        from market_desk.engine import engine

        defense_snap = build_defense_from_snapshot(engine.snapshot or {})
    except Exception as exc:  # noqa: BLE001
        defense_snap = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    return {
        "ok": True,
        "schema": AGENT_PACK_SCHEMA,
        "asof": asof,
        "day": day,
        "origin": push_origin(),
        "since": {
            "edge": EDGE_SHADOW_SINCE,
            "regime": REGIME_SHADOW_SINCE,
            "env_gate": ENV_GATE_SHADOW_SINCE,
            "defense": DEFENSE_SHADOW_SINCE,
            "deep_v": DEEP_V_SHADOW_SINCE,
            "zt_share": ZT_SHARE_SHADOW_SINCE,
        },
        "summary": {
            "ops_overall": ops.get("overall"),
            "sim_gate_ok": bool(gate.get("gate_ok")),
            "sim_gate_readonly_ok": bool(gate.get("gate_readonly_ok")),
            "sim_cum_pnl_pct": gate.get("cum_pnl_pct"),
            "sim_vs_bench": vs_bench,
            "defense_tier": (defense_snap.get("tier") if isinstance(defense_snap, dict) else None),
            "defense_label": (defense_snap.get("label") if isinstance(defense_snap, dict) else None),
            "defense_reasons": (
                list(defense_snap.get("reasons") or [])[:4]
                if isinstance(defense_snap, dict)
                else []
            ),
            "dual_wins": {
                "all": (dual.get("all") or {}) if dual.get("ok") else dual,
                "lit": (dual.get("lit") or {}) if dual.get("ok") else None,
                "touch": dual.get("touch") if isinstance(dual.get("touch"), dict) else None,
            },
            "proposals_ready": ready_n,
            "proposals_hold": hold_n,
            "proposals_soft_candidate": soft_n,
            "proposals_watch": watch_n,
            "adapt_size_mult": (adapt_live.get("size_mult") if isinstance(adapt_live, dict) else None),
            "adapt_heat_mult": (
                ((adapt_live.get("size_heat") or {}).get("size_mult"))
                if isinstance(adapt_live, dict)
                else None
            ),
            "adapt_exec_mult": (
                ((adapt_live.get("exec_size") or {}).get("size_mult"))
                if isinstance(adapt_live, dict)
                else None
            ),
            "note": (
                "sample=ready 只表示天数/张数够看；verdict=hold 默认不改；"
                "soft_candidate 仅形似过门槛，仍须对照 TODO 全条件。一次最多改一处。"
                "abs_win=绝对胜率，xs_win=跑赢大盘胜率。"
                "env_gate 默认影子；env_gate_live 才软乘阈值（默认仅 minute）。"
                "adapt_live=当日现倍率；adapt_effect=分档成绩/今日卡/漏买/题材（看效果用，不自动改闸）。"
                "defense=逆境档位旁注（v1.10.80 size_live 关）。"
            ),
        },
        "adapt_live": adapt_live,
        "adapt_effect": adapt_effect,
        "np_review": np_review,
        "defense": defense_snap,
        "proposals": proposals,
        "dual_wins": dual,
        "review_check": review,
        "ops_check": {
            "overall": ops.get("overall"),
            "checks": ops.get("checks"),
            "host": ops.get("host"),
            "pid": ops.get("pid"),
        },
        "sim": {
            "gate": gate,
            "attribution": attr,
            "vs_bench": vs_bench,
            "by_channel": sim_channels,
        },
        "pool_shadow": pool_shadow,
        "regime_survival": next(
            (
                (it.get("metrics") or {}).get("survival")
                for it in (review.get("items") or [])
                if isinstance(it, dict) and it.get("id") == "regime"
            ),
            None,
        ),
        "env_gate": env_gate_summary,
        "env_lit": (env_gate_summary.get("lit") if isinstance(env_gate_summary, dict) else None),
        "gate_replay": gate_replay,
        "fill_rules": {
            "ok": bool(fill_rules.get("ok")),
            "comparable_roundtrip": fill_comparable,
            "aligned": {
                "t1": (fill_rules.get("t1") or {}).get("aligned"),
                "session": (fill_rules.get("session") or {}).get("aligned"),
            },
            "curl": "GET /api/ops/fill-rules",
        },
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
                "GET /api/ops/gate-replay",
                "GET /api/ops/fill-rules",
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
