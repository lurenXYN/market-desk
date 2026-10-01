"""Shared cache, trade-context buckets, and small numeric helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.config import ADAPT_VOL_AMOUNT_PCTILE, ADAPT_VOL_HS300_ABS
from market_desk.settings import setting


_ADAPT_CACHE: dict[str, Any] = {
    "day": "",
    "bundle": None,
    "sweet": None,
    "heat": None,
    "tune": None,
}


SEG_BUCKET_LABELS = {
    "auction": "竞价",
    "open30": "开盘",
    "morning": "午前",
    "afternoon": "午后",
    "closed": "休市",
    "unknown": "未分时",
}


VOL_BUCKET_LABELS = {
    "high": "高波动",
    "low": "低波动",
    "unknown": "波动未标",
}


def _cache_day() -> str:
    """Return today's cache key."""
    return datetime.now().strftime("%Y-%m-%d")


def _num(v: Any) -> float | None:
    """Parse a numeric field; return None when missing/invalid."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp ``v`` into ``[lo, hi]``."""
    return max(float(lo), min(float(hi), float(v)))


def segment_bucket_from_clock(
    signaled_at: str | None = None,
    *,
    segment_key: str | None = None,
) -> str:
    """Map a clock / segment key into a coarse miss-context bucket."""
    if segment_key:
        key = str(segment_key).strip()
        if key in ("auction", "open30", "morning", "afternoon"):
            return key
        if key == "closed":
            return "closed"
    text = str(signaled_at or "").strip()
    if len(text) >= 16:
        try:
            hh = int(text[11:13])
            mm = int(text[14:16])
            minutes = hh * 60 + mm
        except ValueError:
            return "unknown"
        if 9 * 60 + 15 <= minutes < 9 * 60 + 30:
            return "auction"
        if 9 * 60 + 30 <= minutes < 10 * 60:
            return "open30"
        if 10 * 60 <= minutes < 11 * 60 + 30:
            return "morning"
        if 13 * 60 <= minutes < 15 * 60:
            return "afternoon"
        if 11 * 60 + 30 <= minutes < 13 * 60:
            return "morning"  # lunch inherits morning bucket for labeling
    return "unknown"


def vol_bucket_from_metrics(metrics: dict[str, Any] | None) -> str:
    """Classify high/low volatility from live desk metrics (no ATR dependency)."""
    m = metrics or {}
    hs = _num(m.get("hs300_pct"))
    cyb = _num(m.get("cyb_pct"))
    amt = _num(m.get("amount_pctile"))
    big_drop = bool(m.get("big_drop"))
    if hs is None and cyb is None and amt is None and not big_drop:
        return "unknown"
    high = False
    if hs is not None and abs(hs) >= float(ADAPT_VOL_HS300_ABS):
        high = True
    if cyb is not None and abs(cyb) >= float(ADAPT_VOL_HS300_ABS) + 0.3:
        high = True
    if amt is not None and amt >= float(ADAPT_VOL_AMOUNT_PCTILE):
        high = True
    if big_drop:
        high = True
    return "high" if high else "low"


def make_trade_context(
    *,
    segment_key: str | None = None,
    signaled_at: str | None = None,
    metrics: dict[str, Any] | None = None,
    vol: str | None = None,
) -> dict[str, Any]:
    """Build a situational tag: segment × vol (never generalizes across buckets)."""
    seg = segment_bucket_from_clock(signaled_at, segment_key=segment_key)
    vol_b = str(vol or "").strip() or vol_bucket_from_metrics(metrics)
    if vol_b not in ("high", "low", "unknown"):
        vol_b = "unknown"
    key = f"{seg}|{vol_b}"
    label = f"{SEG_BUCKET_LABELS.get(seg, seg)}·{VOL_BUCKET_LABELS.get(vol_b, vol_b)}"
    tagged = seg != "unknown" and vol_b != "unknown"
    return {
        "key": key,
        "segment": seg,
        "vol": vol_b,
        "label": label,
        "tagged": tagged,
    }


def context_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    """Recover context from a signal / missed-buy row (payload or infer)."""
    row = row or {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    saved = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    if saved.get("key") and saved.get("segment"):
        seg = str(saved.get("segment") or "unknown")
        vol = str(saved.get("vol") or "unknown")
        tagged = seg not in ("", "unknown") and vol in ("high", "low")
        return {
            "key": str(saved.get("key")),
            "segment": seg,
            "vol": vol,
            "label": str(saved.get("label") or saved.get("key")),
            "tagged": tagged,
        }
    return make_trade_context(
        segment_key=saved.get("segment") or row.get("segment"),
        signaled_at=str(row.get("signaled_at") or ""),
        vol=saved.get("vol"),
        metrics=None,
    )


def count_missed_by_context(missed: list[dict[str, Any]] | None) -> dict[str, int]:
    """Count missed buys per context key; untagged rows are excluded from write-back."""
    out: dict[str, int] = {}
    for row in missed or []:
        ctx = row.get("context") if isinstance(row.get("context"), dict) else context_from_row(row)
        if not ctx.get("tagged"):
            continue
        key = str(ctx.get("key") or "")
        if not key:
            continue
        out[key] = int(out.get(key) or 0) + 1
    return out


def _buy_scored(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Return scored buy rows respecting hit_rate_mode (newest last)."""
    from market_desk.review import is_buy_signal

    mode = str(setting("hit_rate_mode", "traded") or "traded").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        out.append(row)
    # Prefer chronological order by signal_at / id.
    out.sort(key=lambda r: (str(r.get("signal_at") or ""), int(r.get("id") or 0)))
    return out


def _is_win(label: str) -> bool:
    """Return True for positive buy outcome labels."""
    return label in {"次日红", "三日红"}
