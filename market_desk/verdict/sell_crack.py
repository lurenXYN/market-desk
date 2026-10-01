"""Sector crack alert: detect a held name's theme collapsing intraday."""

from __future__ import annotations

from typing import Any

from market_desk.filters import normalize_code


def detect_sector_crack(theme: dict[str, Any] | None, code: str) -> str:
    """Return why the held name's sell theme looks like it is cracking, or ``""``.

    Triggers (any one):
      - leader touched near limit intraday and gave back hard (炸板回撤);
      - a multi-board leader turned clearly negative;
      - at least N other members are diving.

    The leader checks are skipped when the held name *is* the leader (its own
    pullback belongs to take-profit / stop logic), and the held name is never
    counted among diving members.

    Args:
        theme: Matched ``sell_themes`` entry carrying ``_crack_fields`` marks.
        code: Normalized code of the held position.

    Returns:
        Human-readable reason, or an empty string when nothing triggers.
    """
    from market_desk.config import (
        SELL_SECTOR_CRACK_DIVERGENT_DOWN_N,
        SELL_SECTOR_CRACK_DIVERGENT_DOWN_PCT,
        SELL_SECTOR_CRACK_DRAGON_BLOW_DROP,
        SELL_SECTOR_CRACK_LEADER_PULLBACK,
        SELL_SECTOR_CRACK_LEADER_TOUCH_PCT,
    )

    if not theme:
        return ""
    reason = ""
    if normalize_code(theme.get("leader_code")) != code:
        ld_pct = theme.get("leader_pct")
        ld_pb = theme.get("leader_pullback")
        ld_high = theme.get("leader_high_pct")
        touched = ld_high is None or float(ld_high) >= float(SELL_SECTOR_CRACK_LEADER_TOUCH_PCT)
        if touched and ld_pb is not None and float(ld_pb) >= float(SELL_SECTOR_CRACK_LEADER_PULLBACK):
            reason = f"板块核心龙头炸板回撤 {float(ld_pb):.1f}%"
        elif (
            ld_pct is not None
            and float(ld_pct) <= float(SELL_SECTOR_CRACK_DRAGON_BLOW_DROP)
            and int(theme.get("leader_boards") or 0) >= 2
        ):
            reason = f"板块连板核心走弱（涨跌幅 {float(ld_pct):.1f}%）"

    dive_pct = float(SELL_SECTOR_CRACK_DIVERGENT_DOWN_PCT)
    diving_n = 0
    for m in theme.get("pool") or theme.get("members") or []:
        if not isinstance(m, dict) or normalize_code(m.get("code")) == code:
            continue
        m_pct = m.get("pct")
        if m_pct is not None and float(m_pct) <= dive_pct:
            diving_n += 1
    if diving_n >= int(SELL_SECTOR_CRACK_DIVERGENT_DOWN_N):
        reason = f"板块内有 {diving_n} 只个股跌幅≥{abs(dive_pct):.0f}%跳水"
    return reason
