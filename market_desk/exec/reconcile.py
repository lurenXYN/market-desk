"""Position / trade reconciliation (local book vs remote gateway snapshot).

Stage 1 will pass broker ``query_positions`` / ``query_trades`` as remote.
Paper self-check compares two pulls from the same gateway (expect empty diffs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from market_desk.exec.types import PositionData, TradeData
from market_desk.filters import normalize_code


@dataclass
class PositionDiff:
    """One code's local vs remote volume mismatch."""

    code: str
    local_volume: int
    remote_volume: int
    status: str  # match | mismatch | local_only | remote_only
    name: str = ""

    @property
    def delta(self) -> int:
        """Remote minus local."""
        return int(self.remote_volume) - int(self.local_volume)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API."""
        return {
            "code": self.code,
            "name": self.name,
            "local_volume": self.local_volume,
            "remote_volume": self.remote_volume,
            "delta": self.delta,
            "status": self.status,
        }


def _pos_map(rows: Iterable[PositionData]) -> dict[str, PositionData]:
    out: dict[str, PositionData] = {}
    for p in rows or []:
        code = normalize_code(p.code) or str(p.code or "").zfill(6)
        if not code:
            continue
        if code in out:
            # Aggregate duplicate codes (multi-lot gateways).
            prev = out[code]
            out[code] = PositionData(
                code=code,
                volume=int(prev.volume) + int(p.volume),
                price=float(prev.price or 0),
                last=p.last if p.last is not None else prev.last,
                name=prev.name or p.name,
                gateway=prev.gateway or p.gateway,
                yd_volume=int(prev.yd_volume) + int(p.yd_volume),
                reference=prev.reference,
            )
        else:
            out[code] = p
    return out


def reconcile_positions(
    local: Iterable[PositionData],
    remote: Iterable[PositionData],
) -> dict[str, Any]:
    """Diff position volumes by code. ``ok`` when every code matches."""
    left = _pos_map(local)
    right = _pos_map(remote)
    codes = sorted(set(left) | set(right))
    diffs: list[PositionDiff] = []
    for code in codes:
        a = left.get(code)
        b = right.get(code)
        lv = int(a.volume) if a else 0
        rv = int(b.volume) if b else 0
        name = (a.name if a else "") or (b.name if b else "")
        if a and b and lv == rv:
            status = "match"
        elif a and b:
            status = "mismatch"
        elif a:
            status = "local_only"
        else:
            status = "remote_only"
        if status != "match":
            diffs.append(
                PositionDiff(
                    code=code,
                    local_volume=lv,
                    remote_volume=rv,
                    status=status,
                    name=name,
                )
            )
    return {
        "ok": len(diffs) == 0,
        "compared_n": len(codes),
        "mismatch_n": len(diffs),
        "diffs": [d.to_dict() for d in diffs],
    }


def reconcile_trade_counts(
    local: Iterable[TradeData],
    remote: Iterable[TradeData],
) -> dict[str, Any]:
    """Lightweight fill-count check (not tick-level id match)."""
    loc = list(local or [])
    rem = list(remote or [])
    loc_buy = sum(1 for t in loc if t.direction.value == "LONG")
    loc_sell = sum(1 for t in loc if t.direction.value == "SHORT")
    rem_buy = sum(1 for t in rem if t.direction.value == "LONG")
    rem_sell = sum(1 for t in rem if t.direction.value == "SHORT")
    ok = loc_buy == rem_buy and loc_sell == rem_sell and len(loc) == len(rem)
    return {
        "ok": ok,
        "local": {"n": len(loc), "buy": loc_buy, "sell": loc_sell},
        "remote": {"n": len(rem), "buy": rem_buy, "sell": rem_sell},
    }


def self_check_gateway(gateway: Any) -> dict[str, Any]:
    """Pull positions/trades twice from one gateway; expect perfect match."""
    p1 = gateway.query_positions()
    p2 = gateway.query_positions()
    t1 = gateway.query_trades(limit=200)
    t2 = gateway.query_trades(limit=200)
    return {
        "positions": reconcile_positions(p1, p2),
        "trades": reconcile_trade_counts(t1, t2),
        "gateway": getattr(gateway, "gateway_name", ""),
    }
