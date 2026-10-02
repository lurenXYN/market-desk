"""Live mainline verdict, recommendation, sell advice, and position helpers.

Split into submodules; this package re-exports every public and private name
so ``from market_desk.verdict import X`` keeps working unchanged.
"""

from __future__ import annotations

from market_desk.verdict.common import (  # noqa: F401
    _BUY_ACTIONS,
    _pool_codes_from_board,
    _theme_entry,
    _lookup_hot_board,
    _join_hint,
    _SOFT_CONFIRM_FLAGS,
    is_soft_confirm_flag,
    is_tip_probe_allow_fail,
    hard_confirm_fails,
    probe_blocking_fails,
    _scale_item_qty,
    _ensure_plan_price,
    _demote_buy_to_wait,
    _batch_plan_lots,
    _wait_price,
    _stop_price,
    _chase_price,
    _px,
    _is_etf_code,
    _is_chi_star_etf,
    _blocks_chi_star_stocks,
    _headline_text,
    _stop_line,
    _fmt_pct,
    _fmt_num,
)
from market_desk.verdict.bands import (  # noqa: F401
    _exit_band_params,
)
from market_desk.verdict.scoring import (  # noqa: F401
    _build_recommend,
    _board_membership_index,
    _cross_board_adj,
    _stock_candidates,
    _score_stock,
    _stock_reason,
    _etf_reason,
    _recommend_item,
    _attach_risk_sizing,
    apply_size_cap_gate,
)
from market_desk.verdict.progress import (  # noqa: F401
    _is_near_buy_price,
    mark_pullback_entries,
    build_item_buy_progress,
    attach_buy_progress,
    apply_mainline_probe,
    apply_band_ready_relax,
    tag_fly_window_items,
    finalize_recommend_buy_ux,
    _annotate_sole_etf_chase,
    _apply_ready_confirmations,
    attach_board_etf_trends,
    apply_stock_daily_trends,
)
from market_desk.verdict.gates import (  # noqa: F401
    _mainline_narrative,
    apply_market_gates,
    apply_auction_open_bridge,
    _switch_age_from_since,
    build_switch_guard,
    inject_switch_from_theme,
    attach_switch_guard,
    apply_review_bias,
    apply_similar_gate,
)
from market_desk.verdict.branches import (  # noqa: F401
    _build_side_branch,
    _mainline_needs_link,
    _peer_sim_for_name,
    _link_board_ok,
    _pick_link_fallback_board,
    _build_link_branch,
    _recommend_codes,
)
from market_desk.verdict.tags import (  # noqa: F401
    _position_exempt_yday_weak,
    _yday_buy_weak,
    _sell_wave_adj,
    _enrich_sell_next_action,
    attach_position_sell_hints,
    _sell_tune_tags,
)
from market_desk.verdict.sell_theme import (  # noqa: F401
    build_sell_themes,
    _position_tied_to_mainline,
    _match_sell_theme,
    _sell_theme_context,
)
from market_desk.verdict.sell import _sell_item  # noqa: F401
from market_desk.verdict.sell_advice import (  # noqa: F401
    build_sell_advice,
    _ready_buy_codes,
    _desk_theme_buyable,
    _demote_soft_sell_to_hold,
    _refresh_sell_advice_summary,
    reconcile_buy_sell_conflict,
)
from market_desk.verdict.positions import (  # noqa: F401
    quote_prev_close,
    position_day_anchor,
    session_sell_realized,
    session_trade_fees,
    decorate_positions,
    attach_position_daily_trends,
    position_summary,
    build_risk_overview,
    build_deltas,
    _delta,
)
from market_desk.verdict.desk import (  # noqa: F401
    _resolve_board_vehicle,
    _position_tied_to_board,
    build_watch_trial_recommend,
    _dragon_items_for_board,
    build_dragon_recommend,
    build_independent_pullback_recommend,
    build_favorite_desk_plans,
)
from market_desk.verdict.buy import (  # noqa: F401
    build_verdict,
    _hero_buy_locked,
    reconfirm_recommend_ready,
    align_action_with_ready,
    _desk_gate_buy_hint,
    _desk_gate_block_hint,
    build_desk_gate_summary,
)
