"""Launch the local dashboard and open a browser."""

from __future__ import annotations

import logging
import threading
import time
import webbrowser

import uvicorn

from market_desk.config import HOST, PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _open_browser() -> None:
    """Open the dashboard after the server binds."""
    time.sleep(1.4)
    webbrowser.open(f"http://{HOST}:{PORT}/")


def main() -> None:
    """Run uvicorn on the configured loopback port."""
    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"作战台: http://{HOST}:{PORT}/  （关掉本窗口即停止）")
    uvicorn.run(
        "market_desk.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
