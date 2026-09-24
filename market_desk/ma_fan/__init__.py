"""Daily MA stickiness → upward fan screener (nightly observation layer).

Scans liquid names once per trade day after the close (historical daily bars).
Results are persisted for the「均线发散」tab and soft-tagged onto review signals
when codes intersect. Does not gate ready / buy / sell.

Submodules:
    pattern  — pure MA pattern scoring
    job      — pacing, single-slot claim, progress, post-close score cache
    sources  — Sina amount ranking, East Money industry / market cap / holders
    context  — desk theme / amount-band tags and review intersections
    scan     — slice schedule and the scan pipeline

Patch module state on the owning submodule (e.g. ``ma_fan.scan.load_universe``);
the names re-exported here are for callers only.
"""

from market_desk.ma_fan.context import (
    annotate_hits_with_desk_context,
    attach_review_flags_to_ma_fan,
    enrich_signals_with_ma_fan,
    ma_fan_code_set,
)
from market_desk.ma_fan.job import (
    MA_FAN_BARS_LIMIT,
    MA_FAN_CONCURRENCY,
    MA_FAN_FORCE_COOLDOWN_S,
    MA_FAN_MAX_CONSECUTIVE_FAIL,
    MA_FAN_MIN_INTERVAL_S,
    MA_FAN_PAGE_DELAY_S,
    MA_FAN_SCORE_BARS,
    force_cooldown_left,
    release_scan,
    scan_progress,
    try_claim_scan,
)
from market_desk.ma_fan.pattern import (
    MA_FAN_FORMULA_VERSION,
    MA_PERIODS,
    amount_band_for_rank,
    score_pattern,
)
from market_desk.ma_fan.scan import (
    MA_FAN_SCHEDULE,
    next_due_ma_fan_slice,
    run_ma_fan_all_due_slices,
    run_ma_fan_scan,
    slices_done_for_day,
)
from market_desk.ma_fan.sources import enrich_hits_meta, load_universe

__all__ = [
    "MA_FAN_BARS_LIMIT",
    "MA_FAN_CONCURRENCY",
    "MA_FAN_FORCE_COOLDOWN_S",
    "MA_FAN_FORMULA_VERSION",
    "MA_FAN_MAX_CONSECUTIVE_FAIL",
    "MA_FAN_MIN_INTERVAL_S",
    "MA_FAN_PAGE_DELAY_S",
    "MA_FAN_SCHEDULE",
    "MA_FAN_SCORE_BARS",
    "MA_PERIODS",
    "amount_band_for_rank",
    "annotate_hits_with_desk_context",
    "attach_review_flags_to_ma_fan",
    "enrich_hits_meta",
    "enrich_signals_with_ma_fan",
    "force_cooldown_left",
    "load_universe",
    "ma_fan_code_set",
    "next_due_ma_fan_slice",
    "release_scan",
    "run_ma_fan_all_due_slices",
    "run_ma_fan_scan",
    "scan_progress",
    "score_pattern",
    "slices_done_for_day",
    "try_claim_scan",
]
