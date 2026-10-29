"""Day-loss and consecutive-reject circuit breakers (opens only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from market_desk.config import (
    EXEC_CIRCUIT_ENABLED,
    EXEC_DAY_LOSS_FRAC,
    EXEC_REJECT_STREAK_MAX,
)
from market_desk.exec.store import load_circuit, save_circuit
from market_desk.exec.types import Direction, Offset, OrderRequest


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@dataclass
class CircuitBreaker:
    """Independent of strategy ifs; persists across process restarts.

    - Day loss: freeze day-start equity once per trade date; trip when
      equity falls below day_start * (1 - EXEC_DAY_LOSS_FRAC).
    - Reject streak: count consecutive rejected gateway results; trip at
      EXEC_REJECT_STREAK_MAX until the next calendar day (or ``reset``).
    Closes (sells) are never blocked by these breakers.
    """

    enabled: bool = EXEC_CIRCUIT_ENABLED
    day_loss_frac: float = EXEC_DAY_LOSS_FRAC
    reject_streak_max: int = EXEC_REJECT_STREAK_MAX
    trade_date: str = ""
    day_start_equity: float | None = None
    reject_streak: int = 0
    day_loss_tripped: bool = False
    reject_tripped: bool = False
    last_reject_reason: str = ""
    _dirty: bool = field(default=False, repr=False)

    @classmethod
    def load(cls) -> "CircuitBreaker":
        """Hydrate from SQLite or return a fresh breaker for today."""
        row = load_circuit()
        br = cls()
        if not row:
            br.trade_date = _today()
            return br
        br.trade_date = str(row.get("trade_date") or "")[:10] or _today()
        br.day_start_equity = row.get("day_start_equity")
        br.reject_streak = int(row.get("reject_streak") or 0)
        br.day_loss_tripped = bool(row.get("day_loss_tripped"))
        br.reject_tripped = bool(row.get("reject_tripped"))
        br.last_reject_reason = str(row.get("last_reject_reason") or "")
        br._roll_if_new_day()
        return br

    def _roll_if_new_day(self) -> None:
        """Clear trip flags when the calendar day advances."""
        today = _today()
        if self.trade_date == today:
            return
        self.trade_date = today
        self.day_start_equity = None
        self.reject_streak = 0
        self.day_loss_tripped = False
        self.reject_tripped = False
        self.last_reject_reason = ""
        self._dirty = True

    def ensure_day_start(self, equity: float | None) -> None:
        """Freeze first equity observation of the trade date as the day baseline."""
        self._roll_if_new_day()
        if self.day_start_equity is not None:
            return
        if equity is None:
            return
        eq = float(equity)
        if eq <= 0:
            return
        self.day_start_equity = eq
        self._dirty = True

    def check_open(self, *, equity: float | None = None) -> tuple[bool, str]:
        """Return ``(ok, reason)``; ``ok=False`` blocks new opens."""
        if not self.enabled:
            return True, "circuit_off"
        self._roll_if_new_day()
        self.ensure_day_start(equity)
        if equity is not None and self.day_start_equity and self.day_start_equity > 0:
            floor = self.day_start_equity * (1.0 - float(self.day_loss_frac))
            if float(equity) <= floor + 1e-9:
                if not self.day_loss_tripped:
                    self.day_loss_tripped = True
                    self._dirty = True
                    self.persist()
                return False, "circuit_day_loss"
        if self.day_loss_tripped:
            return False, "circuit_day_loss"
        if self.reject_tripped:
            return False, "circuit_reject_streak"
        return True, "circuit_ok"

    def note_reject(self, reason: str = "") -> None:
        """Increment reject streak; trip when the max is reached."""
        if not self.enabled:
            return
        self._roll_if_new_day()
        # Circuit itself should not feed the streak.
        if str(reason or "").startswith("circuit_"):
            return
        self.reject_streak = int(self.reject_streak or 0) + 1
        self.last_reject_reason = str(reason or "")[:160]
        if self.reject_streak >= int(self.reject_streak_max):
            self.reject_tripped = True
        self._dirty = True
        self.persist()

    def note_fill(self) -> None:
        """Reset reject streak after a successful fill."""
        if not self.enabled:
            return
        self._roll_if_new_day()
        if self.reject_streak == 0 and not self.last_reject_reason:
            return
        self.reject_streak = 0
        self.last_reject_reason = ""
        self._dirty = True
        self.persist()

    def reset(self, *, clear_day_loss: bool = True) -> None:
        """Manual / test reset of trip flags (keeps day-start equity)."""
        self.reject_streak = 0
        self.reject_tripped = False
        self.last_reject_reason = ""
        if clear_day_loss:
            self.day_loss_tripped = False
        self._dirty = True
        self.persist()

    def persist(self) -> None:
        """Write state to SQLite when dirty."""
        if not self._dirty:
            return
        save_circuit(self.to_dict())
        self._dirty = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize for ops / SQLite."""
        return {
            "enabled": self.enabled,
            "trade_date": self.trade_date or _today(),
            "day_start_equity": self.day_start_equity,
            "day_loss_frac": self.day_loss_frac,
            "reject_streak": self.reject_streak,
            "reject_streak_max": self.reject_streak_max,
            "day_loss_tripped": self.day_loss_tripped,
            "reject_tripped": self.reject_tripped,
            "last_reject_reason": self.last_reject_reason,
            "tripped": bool(self.day_loss_tripped or self.reject_tripped),
        }


def is_open_intent(req: OrderRequest) -> bool:
    """Return True for long-open cash buys (circuit applies)."""
    return req.direction == Direction.LONG and req.offset == Offset.OPEN
