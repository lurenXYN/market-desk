"""Pre-trade risk gates for the execution layer (independent of strategy)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable

from market_desk.config import (
    EXEC_MAX_GROSS_FRAC,
    EXEC_MAX_POS_FRAC,
    EXEC_SESSION_GUARD,
)
from market_desk.exec.types import (
    AccountData,
    Direction,
    Offset,
    OrderRequest,
    PositionData,
    Status,
)
from market_desk.filters import normalize_code

if TYPE_CHECKING:
    from market_desk.exec.circuit import CircuitBreaker


@dataclass
class RiskVerdict:
    """Result of a pre-trade check."""

    ok: bool
    reason: str = ""
    status: Status = Status.SUBMITTING

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API / logs."""
        return {
            "ok": self.ok,
            "reason": self.reason,
            "status": self.status.value,
        }


def require_start_gate(*, live: bool) -> RiskVerdict:
    """Block live broker paths until the paper sim start-gate is green.

    Paper gateway always passes. Live (stage 1–3) requires portfolio_stats.gate_ok.
    """
    if not live:
        return RiskVerdict(ok=True, reason="paper")
    try:
        from market_desk.sim_desk import portfolio_stats

        stats = portfolio_stats()
    except Exception as exc:  # pragma: no cover - defensive
        return RiskVerdict(
            ok=False,
            reason=f"gate_unavailable:{exc}"[:120],
            status=Status.REJECTED,
        )
    if not stats.get("gate_ok"):
        return RiskVerdict(
            ok=False,
            reason="start_gate_blocked",
            status=Status.REJECTED,
        )
    return RiskVerdict(ok=True, reason="gate_ok")


def check_order_basics(req: OrderRequest) -> RiskVerdict:
    """Reject malformed A-share cash orders (lot size, price, side)."""
    code = str(req.code or "").zfill(6)
    if len(code) != 6 or not code.isdigit():
        return RiskVerdict(ok=False, reason="bad_code", status=Status.REJECTED)
    if int(req.volume or 0) <= 0:
        return RiskVerdict(ok=False, reason="bad_volume", status=Status.REJECTED)
    if int(req.volume) % 100 != 0:
        return RiskVerdict(ok=False, reason="not_board_lot", status=Status.REJECTED)
    if req.order_type.value == "LIMIT" and float(req.price or 0) <= 0:
        return RiskVerdict(ok=False, reason="bad_price", status=Status.REJECTED)
    if req.direction == Direction.LONG and req.offset == Offset.CLOSE:
        return RiskVerdict(ok=False, reason="long_close_unsupported", status=Status.REJECTED)
    if req.direction == Direction.SHORT and req.offset == Offset.OPEN:
        return RiskVerdict(ok=False, reason="short_open_unsupported", status=Status.REJECTED)
    return RiskVerdict(ok=True, reason="basics_ok")


def check_session(*, now: datetime | None = None, guard: bool | None = None) -> RiskVerdict:
    """Reject outside continuous auction when session guard is on."""
    if not (EXEC_SESSION_GUARD if guard is None else guard):
        return RiskVerdict(ok=True, reason="session_guard_off")
    try:
        from market_desk.sim_desk import is_continuous_session_stamp

        stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        if is_continuous_session_stamp(stamp):
            return RiskVerdict(ok=True, reason="in_session")
    except Exception:
        pass
    return RiskVerdict(ok=False, reason="outside_session", status=Status.REJECTED)


def check_position_limits(
    req: OrderRequest,
    *,
    account: AccountData | None,
    positions: Iterable[PositionData] | None,
    max_pos_frac: float | None = None,
    max_gross_frac: float | None = None,
) -> RiskVerdict:
    """Cap single-name and gross notional vs equity (opens only)."""
    if req.direction != Direction.LONG or req.offset != Offset.OPEN:
        return RiskVerdict(ok=True, reason="limits_skip_close")
    if account is None:
        return RiskVerdict(ok=True, reason="limits_no_account")
    equity = float(account.balance or 0)
    if equity <= 0:
        return RiskVerdict(ok=False, reason="bad_equity", status=Status.REJECTED)
    pos_cap = float(EXEC_MAX_POS_FRAC if max_pos_frac is None else max_pos_frac)
    gross_cap = float(EXEC_MAX_GROSS_FRAC if max_gross_frac is None else max_gross_frac)
    notional = float(req.price) * int(req.volume)
    if notional > equity * pos_cap + 1e-6:
        return RiskVerdict(ok=False, reason="max_pos_frac", status=Status.REJECTED)
    code = normalize_code(req.code) or str(req.code or "").zfill(6)
    held = 0.0
    for p in positions or []:
        px = float(p.last if p.last is not None else p.price or 0)
        held += max(0, int(p.volume)) * px
        if (normalize_code(p.code) or p.code) == code:
            # Same name already counts toward single-name after fill.
            same = max(0, int(p.volume)) * float(p.price or 0)
            if same + notional > equity * pos_cap + 1e-6:
                return RiskVerdict(ok=False, reason="max_pos_frac_existing", status=Status.REJECTED)
    if held + notional > equity * gross_cap + 1e-6:
        return RiskVerdict(ok=False, reason="max_gross_frac", status=Status.REJECTED)
    return RiskVerdict(ok=True, reason="limits_ok")


def check_circuit_open(
    req: OrderRequest,
    *,
    circuit: "CircuitBreaker | None" = None,
    equity: float | None = None,
) -> RiskVerdict:
    """Block long-opens when day-loss / reject-streak breakers are tripped."""
    if circuit is None:
        return RiskVerdict(ok=True, reason="circuit_skip")
    from market_desk.exec.circuit import is_open_intent

    if not is_open_intent(req):
        return RiskVerdict(ok=True, reason="circuit_skip_close")
    ok, reason = circuit.check_open(equity=equity)
    if not ok:
        return RiskVerdict(ok=False, reason=reason, status=Status.REJECTED)
    return RiskVerdict(ok=True, reason=reason)


def check_order(
    req: OrderRequest,
    *,
    live: bool = False,
    account: AccountData | None = None,
    positions: Iterable[PositionData] | None = None,
    session_now: datetime | None = None,
    session_guard: bool | None = None,
    circuit: "CircuitBreaker | None" = None,
) -> RiskVerdict:
    """Run start-gate → basics → circuit → session → position limits."""
    gate = require_start_gate(live=live)
    if not gate.ok:
        return gate
    basic = check_order_basics(req)
    if not basic.ok:
        return basic
    equity = float(account.balance) if account is not None else None
    circ = check_circuit_open(req, circuit=circuit, equity=equity)
    if not circ.ok:
        return circ
    # Session guard applies to live always; paper only when explicitly requested.
    guard = session_guard
    if guard is None:
        guard = bool(live) and bool(EXEC_SESSION_GUARD)
    if guard:
        sess = check_session(now=session_now, guard=True)
        if not sess.ok:
            return sess
    return check_position_limits(req, account=account, positions=positions)
