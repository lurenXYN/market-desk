"""Main-board filters and limit-up helpers."""

from __future__ import annotations


def normalize_code(code: str | int | None) -> str:
    """Pad a ticker to six digits."""
    if code is None:
        return ""
    return str(code).strip().zfill(6)


def exchange_prefix(code: str | int | None) -> str:
    """Return Xueqiu/East-Money style exchange prefix: SH or SZ."""
    c = normalize_code(code)
    if not c:
        return ""
    # Shanghai: A-shares 6*, funds/ETFs 5*, B 9*; STAR 688/689
    if c.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def xueqiu_symbol(code: str | int | None) -> str:
    """Build a Xueqiu symbol such as SH600519 or SZ159915."""
    c = normalize_code(code)
    prefix = exchange_prefix(c)
    if not c or not prefix:
        return ""
    return f"{prefix}{c}"


def xueqiu_url(code: str | int | None) -> str:
    """Build a Xueqiu quote page URL for the given ticker."""
    sym = xueqiu_symbol(code)
    if not sym:
        return ""
    return f"https://xueqiu.com/S/{sym}"


def is_main_board(code: str | int | None) -> bool:
    """Return True for Shanghai/Shenzhen main-board names, excluding ChiNext and STAR."""
    c = normalize_code(code)
    if not c:
        return False
    if c.startswith(("300", "301", "688", "689", "8", "4", "9")):
        return False
    return c.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))


def is_st(name: str | None) -> bool:
    """Return True if the display name is ST or *ST."""
    n = name or ""
    return "ST" in n.upper()


def limit_up_threshold(name: str | None) -> float:
    """Return the percentage threshold treated as a limit-up."""
    return 4.85 if is_st(name) else 9.85


def is_limit_up(name: str | None, pct: float | None) -> bool:
    """Return True if the daily change qualifies as a limit-up."""
    if pct is None:
        return False
    return pct >= limit_up_threshold(name)


def is_limit_down(name: str | None, pct: float | None) -> bool:
    """Return True if the daily change qualifies as a limit-down."""
    if pct is None:
        return False
    thr = -4.85 if is_st(name) else -9.85
    return pct <= thr
