"""Signal logging and post-trade review helpers.

Split into submodules; this package re-exports every public and private name
so ``from market_desk.review import X`` keeps working unchanged.
"""

from __future__ import annotations

from market_desk.review.signals import (  # noqa: F401
    _CN_TZ,
    _PRICE_TICKS,
    _LIVE_LOOKBACK_SEC,
    _LIVE_KEEP_SEC,
    _LIVE_FLAT_PCT,
    BUY_SIGNAL_TYPES,
    _dragon_hide_from_review,
    purge_unactionable_dragon_signals,
    is_buy_signal,
    is_sell_signal,
    note_quote_ticks,
    live_price_slope,
    lookup_code_boards,
    resolve_day_mainline,
    compare_boards_to_mainline,
    _infer_source_board,
    _desk_source_of,
    enrich_signals_with_holders,
    enrich_signals_with_boards,
    PRE_MATCH_END_HHMM,
    is_pre_match_stamp,
    board_stage_lookup,
)
from market_desk.review.record import (  # noqa: F401
    record_session_signals,
    record_sell_advice_signals,
)
from market_desk.review.outcome import (  # noqa: F401
    _cn_now,
    forward_session_ready,
    _pending_outcome_clear,
    score_signal_with_closes,
    BUY_HIT_LABELS,
    overlay_outcomes_for_standard,
    summarize_signals,
    _flatten_signal_prices,
    OUTCOME_HORIZON_SESSIONS,
    outcome_final_date,
    load_pending_outcomes,
    outcome_is_final,
    apply_outcomes,
    _same_outcome,
)
from market_desk.review.exec_score import (  # noqa: F401
    classify_fill_execution,
    diary_rows_as_exec_fills,
    merge_exec_score_rows,
    classify_sell_fill_execution,
    diary_rows_as_sell_fills,
    merge_sell_exec_score_rows,
    build_sell_exec_score,
    build_exec_score,
    MISS_KIND_LABELS,
    classify_miss_kind,
)
from market_desk.review.hit_rates import (  # noqa: F401
    MIN_HIT_N,
    build_outcome_compare,
    attach_low_n_flags,
    build_missed_buys,
    build_miss_attribution,
    build_desk_source_hit_rates,
    build_theme_hit_rates,
    build_phase_hit_rates,
    build_kind_hit_rates,
    build_phase_kind_hit_rates,
    build_tune_hints,
    _gate_bucket,
    build_gate_kill_stats,
)
from market_desk.review.digest import (  # noqa: F401
    build_today_digest,
    build_week_exec_board,
)
from market_desk.review.bias import (  # noqa: F401
    build_sell_review_bias,
    resolve_sell_kind_bias,
    build_sell_review_bias_bundle,
    build_sell_fly_board,
    _REVIEW_BIAS_CACHE,
    cached_sell_bias_bundle,
    cached_buy_gate_bias,
    build_buy_gate_bias,
)
from market_desk.review.alerts import (  # noqa: F401
    snapshot_quote_map,
    _entry_band_worth_alert,
    build_price_touch_alerts,
    _above_plan_pct,
    signal_plan_price,
    chase_pct,
    build_chase_cost,
    _ever_ready,
    build_ready_monitor,
    build_review_session_hint,
)
from market_desk.review.bench import (  # noqa: F401
    attach_bench_excess,
    bench_window_pct,
    build_bench_excess_summary,
)
from market_desk.review.payload import (  # noqa: F401
    enrich_signals_with_live_marks,
    enrich_signals_with_trends,
    review_trends_fingerprint,
    build_code_signal_history,
    build_review_payload,
)
