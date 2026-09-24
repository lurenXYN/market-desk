"""FastAPI application serving the battle desk.

Routes live in ``market_desk.routes`` (one ``APIRouter`` per feature); this
module only wires the lifespan, static mount and router includes.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from market_desk.auth import ensure_bootstrap_admin
from market_desk.config import STATIC_DIR
from market_desk.engine import engine
from market_desk.routes import ROUTERS

log = logging.getLogger("market_desk.app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the refresh loop and wait for the first snapshot."""
    from market_desk.logs import setup_file_logging

    log.info("file log: %s", setup_file_logging())
    ensure_bootstrap_admin()
    try:
        from market_desk.patches_apply import apply_pending_daily_patches

        applied = apply_pending_daily_patches()
        if applied:
            log.info("daily patches applied(pre): %s", ",".join(applied))
    except Exception:
        log.exception("daily patch apply failed (pre-start)")
    engine.start()
    for _ in range(120):
        if engine.snapshot.get("updated_at"):
            break
        await asyncio.sleep(0.25)
    # Re-run after first tick so history in memory is rebuilt next refresh.
    try:
        from market_desk.db import load_daily
        from market_desk.patches_apply import apply_pending_daily_patches

        applied = apply_pending_daily_patches()
        if applied:
            log.info("daily patches applied(post): %s", ",".join(applied))
        if isinstance(engine.snapshot, dict):
            engine.snapshot["history"] = load_daily(14)
    except Exception:
        log.exception("daily patch apply failed (post-start)")
    yield
    await engine.stop()


app = FastAPI(title="牛来-作战台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
for _router in ROUTERS:
    app.include_router(_router)
