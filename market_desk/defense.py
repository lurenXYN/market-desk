"""Defense tier (逆境档位) — soft observation overlay for adverse markets.

Maps mature risk overlays (trend filter + vol regime + book drawdown) onto
desk UX without touching ready / buy gates. Size / ntfy multipliers stay off
until shadow evidence clears the pre-registered thresholds in TODO.
"""

from __future__ import annotations

from typing import Any

# Tier codes (stable for freeze / pack / UI).
TIER_D0 = "D0"
TIER_D1 = "D1"
TIER_D2 = "D2"
TIER_D3 = "D3"

_TIER_LABEL = {
    TIER_D0: "顺境",
    TIER_D1: "谨慎",
    TIER_D2: "防守",
    TIER_D3: "保命",
}

_TIER_TONE = {
    TIER_D0: "good",
    TIER_D1: "soft",
    TIER_D2: "warn",
    TIER_D3: "warn",
}


def _regime_below(market_regime: dict[str, Any] | None) -> bool:
    mr = market_regime if isinstance(market_regime, dict) else {}
    return bool(mr.get("ok")) and str(mr.get("side") or "") == "below"


def _env_hostile(env_gate: dict[str, Any] | None) -> bool:
    eg = env_gate if isinstance(env_gate, dict) else {}
    return bool(eg.get("ok")) and str(eg.get("bucket") or "") == "hostile"


def sim_stress_signals(
    *,
    loss_streak_n: int = 3,
    lookback_days: int = 5,
    drawdown_pct: float = 3.0,
) -> dict[str, Any]:
    """Measure consecutive losing sells and recent realized drawdown.

    Uses ``sim_fill`` sell rows only (paper book). Drawdown is the worst
    peak-to-trough of cumulative realized PnL% over the last ``lookback_days``
    trading dates with sells — a conservative proxy until a full equity curve
    is persisted.
    """
    from market_desk.config import (
        DEFENSE_D3_DRAWDOWN_PCT,
        DEFENSE_D3_LOOKBACK_DAYS,
        DEFENSE_D3_LOSS_STREAK,
    )
    from market_desk.db.sim_desk import load_sim_fills
    from market_desk.sim_desk import account_snapshot

    streak_need = max(1, int(loss_streak_n or DEFENSE_D3_LOSS_STREAK))
    days_need = max(1, int(lookback_days or DEFENSE_D3_LOOKBACK_DAYS))
    dd_need = float(drawdown_pct if drawdown_pct is not None else DEFENSE_D3_DRAWDOWN_PCT)

    sells = load_sim_fills(limit=120, side="sell")
    # Newest first for streak.
    streak = 0
    for row in sells:
        try:
            pnl = float(row.get("pnl_cny"))
        except (TypeError, ValueError):
            break
        if pnl < 0:
            streak += 1
        else:
            break

    # Oldest→newest within lookback window for cumulative path.
    by_day: dict[str, float] = {}
    for row in sells:
        day = str(row.get("trade_date") or "")[:10]
        if not day:
            continue
        try:
            by_day[day] = by_day.get(day, 0.0) + float(row.get("pnl_cny") or 0.0)
        except (TypeError, ValueError):
            continue
    days_sorted = sorted(by_day.keys())[-days_need:]
    initial = 20000.0
    try:
        acct = account_snapshot()
        initial = float(acct.get("initial_cash") or acct.get("initial") or initial) or initial
    except Exception:  # noqa: BLE001
        pass
    peak = 0.0
    cum = 0.0
    max_dd_pct = 0.0
    for day in days_sorted:
        cum += float(by_day[day])
        if cum > peak:
            peak = cum
        dd = peak - cum
        if initial > 0:
            max_dd_pct = max(max_dd_pct, 100.0 * dd / initial)

    streak_hit = streak >= streak_need
    dd_hit = max_dd_pct >= dd_need
    return {
        "ok": True,
        "loss_streak": streak,
        "loss_streak_need": streak_need,
        "loss_streak_hit": streak_hit,
        "lookback_days": len(days_sorted),
        "lookback_need": days_need,
        "drawdown_pct": round(max_dd_pct, 3),
        "drawdown_need": dd_need,
        "drawdown_hit": dd_hit,
        "stress": bool(streak_hit or dd_hit),
    }


def classify_defense_tier(
    *,
    market_regime: dict[str, Any] | None = None,
    env_gate: dict[str, Any] | None = None,
    stress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify D0–D3 defense tier from regime, env, and sim stress.

    Rules (pre-registered):
      D3 — sim stress (loss streak or recent drawdown)
      D2 — regime below AND env hostile
      D1 — regime below OR env hostile
      D0 — otherwise

    Display / freeze only in v1.10.80 — no size or push side effects yet.
    """
    from market_desk.config import (
        DEFENSE_D1_SIZE_MULT,
        DEFENSE_D2_SIZE_MULT,
        DEFENSE_SHADOW_SINCE,
    )

    below = _regime_below(market_regime)
    hostile = _env_hostile(env_gate)
    st = stress if isinstance(stress, dict) else {}
    if not st.get("ok"):
        try:
            st = sim_stress_signals()
        except Exception as exc:  # noqa: BLE001
            st = {"ok": False, "stress": False, "note": f"{type(exc).__name__}: {exc}"[:120]}

    reasons: list[str] = []
    if below:
        reasons.append("尺下")
    if hostile:
        reasons.append("环境敌意")
    if st.get("loss_streak_hit"):
        reasons.append(f"连亏{st.get('loss_streak')}")
    if st.get("drawdown_hit"):
        reasons.append(f"近窗回撤{st.get('drawdown_pct')}%")

    if st.get("stress"):
        tier = TIER_D3
    elif below and hostile:
        tier = TIER_D2
    elif below or hostile:
        tier = TIER_D1
    else:
        tier = TIER_D0

    size_mult = {
        TIER_D0: 1.0,
        TIER_D1: float(DEFENSE_D1_SIZE_MULT),
        TIER_D2: float(DEFENSE_D2_SIZE_MULT),
        TIER_D3: 0.0,
    }.get(tier, 1.0)

    return {
        "ok": True,
        "schema": "defense.v1",
        "tier": tier,
        "label": _TIER_LABEL.get(tier, tier),
        "tone": _TIER_TONE.get(tier, "soft"),
        "below": below,
        "hostile": hostile,
        "reasons": reasons,
        # Recorded for shadow; live size apply stays off until TODO clears.
        "size_mult": size_mult,
        "size_live": False,
        "stress": {
            "ok": bool(st.get("ok")),
            "stress": bool(st.get("stress")),
            "loss_streak": st.get("loss_streak"),
            "drawdown_pct": st.get("drawdown_pct"),
        },
        "since": str(DEFENSE_SHADOW_SINCE),
        "note": (
            "逆境档位只旁注/冻结；v1.10.80 不改 ready、不加仓位乘、不改推送。"
            "D1=尺下或敌意；D2=尺下且敌意；D3=模拟连亏/近窗回撤。"
        ),
        "hint": {
            TIER_D0: "顺境：主线回踩照常",
            TIER_D1: "谨慎：少追主题，人工半仓优先",
            TIER_D2: "防守：只跟曾亮灯，主题/龙头观察",
            TIER_D3: "保命：今日不追新买，只盯卖点/止损",
        }.get(tier, ""),
    }


def build_defense_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Build defense tier from an engine snapshot (regime + env_gate)."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    return classify_defense_tier(
        market_regime=snap.get("market_regime")
        if isinstance(snap.get("market_regime"), dict)
        else None,
        env_gate=snap.get("env_gate") if isinstance(snap.get("env_gate"), dict) else None,
    )


def freeze_defense(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact defense blob for buy-signal payload freeze."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    raw = snap.get("defense") if isinstance(snap.get("defense"), dict) else None
    if not isinstance(raw, dict) or not raw.get("ok"):
        try:
            raw = build_defense_from_snapshot(snap)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(raw, dict) or not raw.get("ok"):
        return None
    tier = str(raw.get("tier") or "")
    if tier not in (TIER_D0, TIER_D1, TIER_D2, TIER_D3):
        return None
    return {
        "schema": "defense.v1",
        "tier": tier,
        "label": raw.get("label"),
        "below": bool(raw.get("below")),
        "hostile": bool(raw.get("hostile")),
        "reasons": list(raw.get("reasons") or [])[:6],
        "size_mult": raw.get("size_mult"),
        "size_live": False,
        "since": raw.get("since"),
    }


def defense_card_tag(defense: dict[str, Any] | None) -> dict[str, str] | None:
    """Soft card badge for D1+ (display only)."""
    d = defense if isinstance(defense, dict) else {}
    if not d.get("ok"):
        return None
    tier = str(d.get("tier") or "")
    if tier in ("", TIER_D0):
        return None
    return {
        "label": f"逆境·{d.get('label') or tier}",
        "tone": str(d.get("tone") or "warn"),
        "note": str(d.get("hint") or d.get("note") or "逆境档位旁注，不关亮灯"),
    }


def annotate_recommend_defense(
    recommend: dict[str, Any] | None,
    defense: dict[str, Any] | None,
) -> dict[str, Any]:
    """Stamp ``defense_tag`` on each recommend item (soft UX only)."""
    rec = dict(recommend or {})
    tag = defense_card_tag(defense)
    items: list[dict[str, Any]] = []
    for raw in rec.get("items") or []:
        item = dict(raw)
        item.pop("defense_tag", None)
        if tag:
            item["defense_tag"] = dict(tag)
        items.append(item)
    rec["items"] = items
    return rec


def annotate_verdict_defense(
    verdict: dict[str, Any] | None,
    defense: dict[str, Any] | None,
) -> dict[str, Any]:
    """Annotate all recommend pools with soft defense tags."""
    v = dict(verdict or {})
    for key in (
        "recommend",
        "side_recommend",
        "link_recommend",
        "dragon_recommend",
        "independent_recommend",
        "watch_trial_recommend",
    ):
        if isinstance(v.get(key), dict):
            v[key] = annotate_recommend_defense(v.get(key), defense)
    return v
