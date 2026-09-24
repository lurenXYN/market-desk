"""Dashboard page, the concatenated desk script and the favicon."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from market_desk.config import STATIC_DIR

router = APIRouter()


@router.get("/")
def index() -> FileResponse:
    """Serve the dashboard page."""
    return FileResponse(Path(STATIC_DIR) / "index.html")


@router.get("/assets/desk.js")
def desk_js(request: Request) -> Response:
    """Serve ``static/js/desk/*.js`` concatenated in order as one script."""
    from market_desk.assets import desk_bundle

    body, etag = desk_bundle()
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/javascript; charset=utf-8", headers=headers)


@router.get("/favicon.ico")
def favicon() -> FileResponse:
    """Serve the tab icon (browsers request this at site root)."""
    return FileResponse(Path(STATIC_DIR) / "favicon.ico")
