"""Elliott-wave style multi-scenario index readout (observe-only).

Split into submodules; this package re-exports every public and private name
so ``from market_desk.elliott import X`` keeps working unchanged.
"""

from __future__ import annotations

from market_desk.elliott.catalog import (  # noqa: F401
    _SCENARIOS,
    _WAVE_MARK_SPEC,
    _SUB_IMPULSE_UP,
    _SUB_IMPULSE_DN,
    _SUB_ABC,
)
from market_desk.elliott.pivots import (  # noqa: F401
    _normalize_bars,
    _alternating_chain,
    _zigzag_pivots,
    _pivot,
    _structure_snapshot,
)
from market_desk.elliott.indicators import (  # noqa: F401
    _ema,
    _rsi,
    _build_indicators,
    _macd_cross,
    _detect_divergence,
)
from market_desk.elliott.fit import (  # noqa: F401
    _score_fit,
    _invalidation,
    _key_levels,
    _subwave_catalog,
    _score_sub_fit,
    _subwave_invalidation,
    _subwave_draft,
)
from market_desk.elliott.timing import (  # noqa: F401
    _timing_summary,
    _scenario_turns,
    _fib_days_for_scenario,
    _wave_clock_note,
    _scenario_levels,
)
from market_desk.elliott.chart import (  # noqa: F401
    _build_elliott_chart,
    _pick_wave_mark_source,
    _build_wave_marks,
)
from market_desk.elliott.board import (  # noqa: F401
    build_elliott_scenarios,
)
