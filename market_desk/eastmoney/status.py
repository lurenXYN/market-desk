"""Aggregated runtime source / backoff status for the health strip."""

from __future__ import annotations

from typing import Any
import time

from market_desk import board_fallback
from market_desk.eastmoney import boards as _boards
from market_desk.eastmoney import client as _client
from market_desk.eastmoney import minute as _minute
from market_desk.eastmoney import quotes as _quotes


def clist_runtime_status() -> dict[str, Any]:
    """Expose preferred clist host and backoff for the health strip.

    Reads sibling module attributes at call time: those globals are rebound by
    their owners, so a ``from ... import`` binding would go stale.
    """
    rem = _client.clist_backoff_remaining()
    return {
        "host": _client._CLIST_HOST_PREF or _client._CLIST_HOSTS[0],
        "pref": _client._CLIST_HOST_PREF,
        "backoff": rem > 0,
        "backoff_sec": round(rem, 1),
        "fail_streak": int(_client._CLIST_FAIL_STREAK),
        "quotes_source": _quotes._MAIN_QUOTES_SOURCE,
        "quotes_pause_sec": round(max(0.0, _quotes._QUOTES_CLIST_PAUSE_UNTIL - time.time()), 1),
        "boards_source": _boards._BOARDS_SOURCE,
        "boards_pause_sec": round(max(0.0, _boards._BOARDS_CLIST_PAUSE_UNTIL - time.time()), 1),
        "boards_switches": _boards.boards_switch_stats(),
        "boards_alias": board_fallback.alias_stats(),
        "minute_source": _minute._MINUTE_SOURCE,
        "minute_pause_sec": round(max(0.0, _minute._MINUTE_EM_PAUSE_UNTIL - time.time()), 1),
    }
