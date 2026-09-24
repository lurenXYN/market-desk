"""HTTP routes grouped by feature; ``ROUTERS`` is included by ``market_desk.app``.

Include order follows the original single-file registration order.
"""

from market_desk.routes import (
    pages,
    auth,
    market,
    review,
    positions,
    settings,
    lists,
    reports,
    ops,
    ma_fan,
    backtest,
)

ROUTERS = [
    pages.router,
    auth.router,
    market.router,
    review.router,
    positions.router,
    settings.router,
    lists.router,
    reports.router,
    ops.router,
    ma_fan.router,
    backtest.router,
]
