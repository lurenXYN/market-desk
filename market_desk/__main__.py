"""Launch the local dashboard and open a browser."""

from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from market_desk.config import BROWSER_HOST, HOST, PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _lan_ips() -> list[str]:
    """Best-effort list of private LAN IPv4 addresses for access hints."""

    def _private(ip: str) -> bool:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            return False
        if a == 10:
            return True
        if a == 192 and b == 168:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        return False

    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and _private(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    if not found:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            probe.close()
            if ip and _private(ip):
                found.append(ip)
        except OSError:
            pass
    return found


def _open_browser() -> None:
    """Open the dashboard after the server binds (always via loopback URL)."""
    time.sleep(1.4)
    webbrowser.open(f"http://{BROWSER_HOST}:{PORT}/")


def main() -> None:
    """Run uvicorn on the configured host; print local (and LAN if bound) URLs."""
    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"作战台本机: http://{BROWSER_HOST}:{PORT}/")
    if HOST in ("0.0.0.0", "::", ""):
        print(f"绑定地址: {HOST}:{PORT}（局域网可访问）")
        for ip in _lan_ips():
            print(f"局域网: http://{ip}:{PORT}/")
    else:
        print(f"仅本机访问: http://{HOST}:{PORT}/")
    print("关掉本窗口即停止")
    uvicorn.run(
        "market_desk.app:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
