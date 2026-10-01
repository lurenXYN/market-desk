"""Refresh loop that assembles the dashboard snapshot.

Split into submodules; this package re-exports every public and private name
so ``from market_desk.engine import X`` keeps working unchanged.
"""

from __future__ import annotations

from market_desk.engine.util import (  # noqa: F401
    _short_exc,
    _map_capped,
    _safe,
    _minutes,
    _prev_trading_day,
    _effective_refresh_seconds,
    _is_weekday,
    _is_session,
)
from market_desk.engine.boards import (  # noqa: F401
    _pick_pin_board,
    _mark_favorite_flags,
    _join_board_note,
    _rank_ice_boards,
    _enrich_board,
    _board_etf_codes,
    _stable_mainline_lifecycle,
)
from market_desk.engine.watch import (  # noqa: F401
    _decorate_watchlist,
    _attach_holders_to_items,
    _annotate_watchlist_observe,
    _decorate_segments,
    _watch_pool,
    _rough_watch_band,
    _decorate_watch_pool,
)
from market_desk.engine.health import (  # noqa: F401
    _build_health,
    _event_line,
    _contagion,
    _today_events,
    _cycle_view,
)
from market_desk.engine.core import (  # noqa: F401
    CN_TZ,
    log,
    DeskEngine,
    engine,
)
