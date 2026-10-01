"""Opening Auction Alpha: quantify the 09:20–09:25 non-cancellable auction tape."""

from __future__ import annotations

from datetime import datetime
from statistics import median, pstdev
from typing import Any

_TAPE: dict[str, list[dict[str, Any]]] = {}
_META: dict[str, dict[str, Any]] = {}
_TAPE_DATE = ""

TRAP_LABEL = "竞价诱多闸门"
STRONG_LABEL = "竞价超预期抢筹"


def reset_tape(trade_date: str = "") -> None:
    """Drop all tape samples and candidate metadata (new day or tests)."""
    global _TAPE_DATE
    _TAPE.clear()
    _META.clear()
    _TAPE_DATE = str(trade_date or "")


def tape_snapshot() -> dict[str, list[dict[str, Any]]]:
    """Return a shallow copy of the current tape keyed by code."""
    return {k: list(v) for k, v in _TAPE.items()}


def in_tape_window(now: datetime) -> bool:
    """Return True while the 09:20–09:25 tape should be sampled."""
    from market_desk.config import AUCTION_ALPHA_TAPE_END, AUCTION_ALPHA_TAPE_START

    hhmm = now.hour * 100 + now.minute
    return int(AUCTION_ALPHA_TAPE_START) <= hhmm < int(AUCTION_ALPHA_TAPE_END)


def auction_sample(quote: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """Turn one auction-time Tencent quote into a tape sample.

    During the call auction the quote price is the virtual match price and the
    best bid / ask both sit at it: the smaller side volume is the matched size
    and same-price surplus on deeper levels is the unmatched imbalance.

    Args:
        quote: Tencent quote with ``price`` / ``prev`` / ``pct`` / book levels.
        now: Sampling wall clock.

    Returns:
        ``{"t", "pct", "price", "match_lots", "unmatched_buy",
        "unmatched_sell", "vol_ratio"}`` or ``None`` when no price exists.
    """
    q = quote or {}
    try:
        price = float(q.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return None
    pct = q.get("pct")
    try:
        pct_f = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct_f = None
    if pct_f is None:
        try:
            prev = float(q.get("prev") or 0.0)
        except (TypeError, ValueError):
            prev = 0.0
        if prev <= 0:
            return None
        pct_f = (price - prev) / prev * 100.0
    bids = [lv for lv in (q.get("bids") or []) if len(lv) >= 2]
    asks = [lv for lv in (q.get("asks") or []) if len(lv) >= 2]
    match_lots = unmatched_buy = unmatched_sell = None
    if bids and asks and abs(float(bids[0][0]) - float(asks[0][0])) < 1e-6:
        px = float(bids[0][0])
        b1, a1 = float(bids[0][1]), float(asks[0][1])
        match_lots = min(b1, a1)
        unmatched_buy = max(0.0, b1 - a1) + sum(
            float(v) for p, v in bids[1:] if abs(float(p) - px) < 1e-6
        )
        unmatched_sell = max(0.0, a1 - b1) + sum(
            float(v) for p, v in asks[1:] if abs(float(p) - px) < 1e-6
        )
    vol_ratio = q.get("vol_ratio")
    try:
        vol_ratio = float(vol_ratio) if vol_ratio is not None else None
    except (TypeError, ValueError):
        vol_ratio = None
    return {
        "t": now.hour * 3600 + now.minute * 60 + now.second,
        "pct": round(pct_f, 3),
        "price": price,
        "match_lots": match_lots,
        "unmatched_buy": unmatched_buy,
        "unmatched_sell": unmatched_sell,
        "vol_ratio": vol_ratio,
    }


def record_tape(
    quotes_by_code: dict[str, dict[str, Any]] | None,
    now: datetime,
    *,
    trade_date: str,
    meta: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Append auction samples for each quoted candidate; return samples added.

    Args:
        quotes_by_code: Tencent quotes keyed by code.
        now: Sampling wall clock (only 09:20–09:25 samples are kept).
        trade_date: ``YYYYMMDD``; a new date resets the tape.
        meta: Optional per-code ``{"group": str, "yzt": bool}`` candidate info.

    Returns:
        Number of samples appended this call.
    """
    if str(trade_date) != _TAPE_DATE:
        reset_tape(trade_date)
    for code, info in (meta or {}).items():
        merged = dict(_META.get(code) or {})
        merged.update({k: v for k, v in (info or {}).items() if v not in (None, "")})
        _META[code] = merged
    if not in_tape_window(now):
        return 0
    added = 0
    for raw_code, quote in (quotes_by_code or {}).items():
        code = str(raw_code or "").zfill(6)
        sample = auction_sample(quote, now)
        if not sample:
            continue
        series = _TAPE.setdefault(code, [])
        if series and series[-1]["t"] == sample["t"]:
            series[-1] = sample
        else:
            series.append(sample)
        added += 1
    return added


def _slope_per_min(samples: list[dict[str, Any]]) -> float | None:
    """Least-squares slope of ``pct`` versus minutes."""
    if len(samples) < 2:
        return None
    xs = [s["t"] / 60.0 for s in samples]
    ys = [float(s["pct"]) for s in samples]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def evaluate_tape(
    samples: list[dict[str, Any]] | None,
    *,
    baseline: float,
    peer_median: float | None = None,
    peer_spread: float | None = None,
    yzt: bool = False,
) -> dict[str, Any]:
    """Classify one code's 09:20–09:25 tape against its expectation.

    - Trap (``高开低走诱多闸门``): a gap-up whose grab fades into 09:25, or an
      expected-strong name (yesterday limit-up) printing well below expectation
      while still sliding.
    - Strong (``真强势确认``): beats the peer baseline by a clear margin while
      peers are weak or divergent, the grab keeps climbing, and the volume ratio
      (or matched size growth) confirms real money.

    Args:
        samples: Tape samples ordered by time (09:20 onward).
        baseline: Expected open in percent (peer median or market median).
        peer_median: Median open of sampled peers in the same group.
        peer_spread: Population stdev of peer opens.
        yzt: True when the code closed limit-up yesterday.

    Returns:
        A dict with ``tag`` (``trap`` / ``strong`` / ``neutral`` / ``unknown``),
        ``label``, ``reasons`` and the tape metrics.
    """
    from market_desk.config import (
        AUCTION_ALPHA_BEAT_PP,
        AUCTION_ALPHA_FADE_PCT,
        AUCTION_ALPHA_GAP_MIN,
        AUCTION_ALPHA_MIN_SAMPLES,
        AUCTION_ALPHA_MISS_PP,
        AUCTION_ALPHA_PEER_SPREAD,
        AUCTION_ALPHA_PEER_WEAK,
        AUCTION_ALPHA_SLOPE_FADE,
        AUCTION_ALPHA_TAPE_START,
        AUCTION_ALPHA_VOL_RATIO_MIN,
        AUCTION_ALPHA_YZT_EXPECT,
    )

    start = int(AUCTION_ALPHA_TAPE_START)
    t0 = (start // 100) * 3600 + (start % 100) * 60
    rows = sorted(
        (s for s in (samples or []) if int(s.get("t") or 0) >= t0),
        key=lambda s: s["t"],
    )
    out: dict[str, Any] = {
        "tag": "unknown",
        "label": "竞价样本不足",
        "reasons": [],
        "samples": len(rows),
    }
    if len(rows) < int(AUCTION_ALPHA_MIN_SAMPLES):
        return out
    open_pct = float(rows[-1]["pct"])
    peak = max(float(s["pct"]) for s in rows)
    fade = peak - open_pct
    slope = _slope_per_min(rows)
    first_match = next((s["match_lots"] for s in rows if s.get("match_lots")), None)
    last_match = rows[-1].get("match_lots")
    match_growth = (
        round(float(last_match) / float(first_match), 2)
        if first_match and last_match
        else None
    )
    ub = rows[-1].get("unmatched_buy")
    us = rows[-1].get("unmatched_sell")
    imbalance = None
    if ub is not None and us is not None:
        denom = float(ub) + float(us) + float(last_match or 0.0)
        if denom > 0:
            imbalance = round((float(ub) - float(us)) / denom, 3)
    vol_ratio = rows[-1].get("vol_ratio")
    expected = max(float(baseline), float(AUCTION_ALPHA_YZT_EXPECT)) if yzt else float(baseline)
    gap_vs_exp = open_pct - expected
    out.update(
        open_pct=round(open_pct, 2),
        peak_pct=round(peak, 2),
        fade_pp=round(fade, 2),
        slope=None if slope is None else round(slope, 3),
        match_growth=match_growth,
        imbalance=imbalance,
        vol_ratio=vol_ratio,
        expected=round(expected, 2),
        gap_vs_exp=round(gap_vs_exp, 2),
        peer_median=None if peer_median is None else round(peer_median, 2),
        peer_spread=None if peer_spread is None else round(peer_spread, 2),
        yzt=bool(yzt),
    )

    fading = fade >= float(AUCTION_ALPHA_FADE_PCT) or (
        slope is not None and slope <= float(AUCTION_ALPHA_SLOPE_FADE)
    )
    reasons: list[str] = []
    if open_pct >= float(AUCTION_ALPHA_GAP_MIN) and fading:
        reasons.append(f"高开{open_pct:.1f}%竞价回落{fade:.1f}pp")
    if (
        expected >= float(AUCTION_ALPHA_YZT_EXPECT)
        and gap_vs_exp <= -float(AUCTION_ALPHA_MISS_PP)
        and (slope is None or slope <= 0)
    ):
        reasons.append(f"竞价不及预期（预期{expected:.1f}%实{open_pct:.1f}%）")
    if reasons:
        out.update(tag="trap", label=TRAP_LABEL, reasons=reasons)
        return out

    peers_weak = (peer_median is not None and peer_median <= float(AUCTION_ALPHA_PEER_WEAK)) or (
        peer_spread is not None and peer_spread >= float(AUCTION_ALPHA_PEER_SPREAD)
    )
    beat = open_pct - float(peer_median if peer_median is not None else baseline)
    climbing = (slope is None or slope >= 0) and fade < float(AUCTION_ALPHA_FADE_PCT) / 2.0
    if vol_ratio is not None:
        money = float(vol_ratio) >= float(AUCTION_ALPHA_VOL_RATIO_MIN)
    else:
        money = match_growth is not None and match_growth >= 1.0
    if imbalance is not None and imbalance < 0:
        money = False
    if peers_weak and beat >= float(AUCTION_ALPHA_BEAT_PP) and climbing and money:
        out.update(
            tag="strong",
            label=STRONG_LABEL,
            reasons=[f"板块分化中超预期{beat:.1f}pp抢筹"],
        )
        return out
    out.update(tag="neutral", label="竞价中性")
    return out


def compute_alpha(
    *,
    market_median: float | None,
    tape: dict[str, list[dict[str, Any]]] | None = None,
    meta: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Evaluate every taped code against its group / market baseline.

    Args:
        market_median: Market-wide median open (fallback baseline).
        tape: Samples keyed by code (defaults to the live module tape).
        meta: Candidate info keyed by code (defaults to the live metadata).

    Returns:
        Per-code ``evaluate_tape`` results.
    """
    from market_desk.config import AUCTION_ALPHA_PEER_MIN

    tape = _TAPE if tape is None else tape
    meta = _META if meta is None else meta
    last_pct: dict[str, float] = {}
    for code, rows in tape.items():
        if rows:
            last_pct[code] = float(sorted(rows, key=lambda s: s["t"])[-1]["pct"])
    groups: dict[str, list[str]] = {}
    for code in last_pct:
        group = str((meta.get(code) or {}).get("group") or "")
        if group:
            groups.setdefault(group, []).append(code)
    base_mkt = float(market_median or 0.0)
    out: dict[str, dict[str, Any]] = {}
    for code, rows in tape.items():
        info = meta.get(code) or {}
        group = str(info.get("group") or "")
        peers = [last_pct[c] for c in groups.get(group, []) if c != code]
        peer_med = peer_sd = None
        if len(peers) >= int(AUCTION_ALPHA_PEER_MIN):
            peer_med = float(median(peers))
            peer_sd = float(pstdev(peers))
        baseline = peer_med if peer_med is not None else base_mkt
        res = evaluate_tape(
            rows,
            baseline=baseline,
            peer_median=peer_med,
            peer_spread=peer_sd,
            yzt=bool(info.get("yzt")),
        )
        res["group"] = group or None
        out[code] = res
    return out


def apply_auction_alpha(
    recommend: dict[str, Any] | None,
    alpha_by_code: dict[str, dict[str, Any]] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Project auction-alpha verdicts onto buy cards.

    Trap: inside the open-bridge window (09:30 + N minutes) the card loses
    ``ready`` and carries a hard ``confirm_fail``; afterwards, until
    ``AUCTION_ALPHA_TRAP_SOFT_UNTIL``, it only soft-shrinks size. Strong: a
    badge only; it never unlocks ready by itself.

    Args:
        recommend: A recommend box with ``items``.
        alpha_by_code: ``compute_alpha`` output (or the locked payload).
        now: Wall clock deciding the hard / soft window.

    Returns:
        A copy of ``recommend`` with ``auction_alpha`` attached per matched item.
    """
    from market_desk.config import (
        AUCTION_ALPHA_TRAP_SIZE_MULT,
        AUCTION_ALPHA_TRAP_SOFT_UNTIL,
        AUCTION_OPEN_BRIDGE_MINUTES,
    )
    from market_desk.verdict.common import _demote_buy_to_wait, _join_hint, _scale_item_qty

    rec = dict(recommend or {})
    items = [dict(x) for x in (rec.get("items") or [])]
    if not items or not alpha_by_code:
        return rec
    minutes = now.hour * 60 + now.minute
    open_m = 9 * 60 + 30
    hard_end = open_m + int(AUCTION_OPEN_BRIDGE_MINUTES)
    soft_end_hhmm = int(AUCTION_ALPHA_TRAP_SOFT_UNTIL)
    soft_end = (soft_end_hhmm // 100) * 60 + soft_end_hhmm % 100
    killed = False
    for item in items:
        code = str(item.get("code") or "").zfill(6)
        res = alpha_by_code.get(code)
        if not res:
            continue
        item["auction_alpha"] = res
        tag = res.get("tag")
        if tag == "strong":
            item["auction_badge"] = STRONG_LABEL
            continue
        item.pop("auction_badge", None)
        if tag != "trap":
            continue
        why = "；".join(res.get("reasons") or []) or TRAP_LABEL
        if minutes < hard_end:
            if item.get("ready") or item.get("near_entry"):
                fails = list(item.get("confirm_fail") or [])
                if TRAP_LABEL not in fails:
                    fails.append(TRAP_LABEL)
                item["confirm_fail"] = fails
                item["reason"] = _join_hint(str(item.get("reason") or ""), f"确认失败：{why}")
            if item.get("ready"):
                killed = True
                item["ready"] = False
                _demote_buy_to_wait(item)
        elif minutes < soft_end and item.get("ready"):
            if not item.get("auction_trap_scaled"):
                _scale_item_qty(item, float(AUCTION_ALPHA_TRAP_SIZE_MULT), "竞价诱多·降仓")
                item["auction_trap_scaled"] = True
            soft = list(item.get("confirm_soft") or [])
            if "竞价诱多·降仓" not in soft:
                soft.append("竞价诱多·降仓")
            item["confirm_soft"] = soft
    rec["items"] = items
    if killed and rec.get("buy") and not any(x.get("ready") for x in items):
        rec["buy"] = False
        rec["title"] = "竞价诱多，先不追"
        rec["size_note"] = _join_hint(str(rec.get("size_note") or ""), "竞价高开回落，开盘桥内不现买")
    return rec
