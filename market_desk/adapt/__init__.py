"""Adaptive soft feedback: size heat, stock/theme trade reputation, segment sell,
learned pullback sweet-zone, and clamped auto-tune deltas.

All outputs are soft multipliers / score nudges — never hard buy bans.

Split into submodules; this package re-exports every public and private name
so ``from market_desk.adapt import X`` keeps working unchanged.
"""

from __future__ import annotations

from market_desk.adapt.context import (  # noqa: F401
    _ADAPT_CACHE,
    SEG_BUCKET_LABELS,
    VOL_BUCKET_LABELS,
    _cache_day,
    _num,
    _clamp,
    segment_bucket_from_clock,
    vol_bucket_from_metrics,
    make_trade_context,
    context_from_row,
    count_missed_by_context,
    _buy_scored,
    _is_win,
)
from market_desk.adapt.gates import (  # noqa: F401
    build_context_gate_bias,
    resolve_gate_mult,
    build_sticky_margin_bias,
)
from market_desk.adapt.sizing import (  # noqa: F401
    compose_size_mult,
    build_size_heat,
    phase_kind_size_mult,
    build_exec_size_bias,
    build_desk_source_size_bias,
)
from market_desk.adapt.sell_learn import (  # noqa: F401
    _sell_segment_of,
    build_segment_sell_learned,
    segment_sell_mult,
    build_sell_mfe_bias,
)
from market_desk.adapt.tune import (  # noqa: F401
    build_pullback_sweet,
    build_auto_tune_deltas,
    cache_tune_key,
    store_tune_cache,
    get_cached_tune,
    resolve_auto_tune,
)
from market_desk.adapt.bundle import (  # noqa: F401
    _ADAPT_BARS,
    set_adapt_bars,
    get_adapt_bars,
    _remap_rows_for_adapt_standard,
    build_adapt_bundle,
    record_trade_feedback,
    stock_rep_score_adj,
)
