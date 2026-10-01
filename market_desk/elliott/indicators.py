"""EMA / RSI / MACD indicators and divergence detection."""

from __future__ import annotations

from typing import Any


def _ema(values: list[float], span: int) -> list[float | None]:
    """Compute exponential moving average; leading bars stay None until warm."""
    out: list[float | None] = [None] * len(values)
    if span <= 0 or not values:
        return out
    alpha = 2.0 / (span + 1.0)
    prev: float | None = None
    for i, v in enumerate(values):
        if prev is None:
            if i + 1 < span:
                out[i] = None
                continue
            prev = sum(values[i + 1 - span : i + 1]) / float(span)
            out[i] = prev
            continue
        prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI series aligned with ``closes``."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def _build_indicators(series: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute MACD/RSI snapshots and soft divergence hints on index closes."""
    closes = [float(x["close"]) for x in series]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if ema12[i] is not None and ema26[i] is not None:
            dif[i] = float(ema12[i]) - float(ema26[i])
    # DEA = EMA of DIF (skip None gaps by only feeding valid DIF values in place).
    dif_filled = [d if d is not None else 0.0 for d in dif]
    dea_raw = _ema(dif_filled, 9)
    dea: list[float | None] = []
    for i, d in enumerate(dif):
        dea.append(dea_raw[i] if d is not None and dea_raw[i] is not None else None)
    hist: list[float | None] = []
    for i in range(len(closes)):
        if dif[i] is not None and dea[i] is not None:
            hist.append(float(dif[i]) - float(dea[i]))
        else:
            hist.append(None)
    rsi = _rsi(closes, 14)
    div = _detect_divergence(series, closes, dif, rsi)
    last_i = len(closes) - 1
    rsi_v = rsi[last_i]
    rsi_zone = "中性"
    if rsi_v is not None:
        if rsi_v >= 70:
            rsi_zone = "超买"
        elif rsi_v <= 30:
            rsi_zone = "超卖"
        elif rsi_v >= 60:
            rsi_zone = "偏强"
        elif rsi_v <= 40:
            rsi_zone = "偏弱"
    macd_snap = {
        "dif": round(dif[last_i], 3) if dif[last_i] is not None else None,
        "dea": round(dea[last_i], 3) if dea[last_i] is not None else None,
        "hist": round(hist[last_i], 3) if hist[last_i] is not None else None,
        "cross": _macd_cross(dif, dea),
    }
    return {
        "dif": dif,
        "dea": dea,
        "hist": hist,
        "rsi": rsi,
        "macd_snap": macd_snap,
        "rsi_snap": {
            "value": round(rsi_v, 1) if rsi_v is not None else None,
            "zone": rsi_zone,
        },
        "divergence": div,
    }


def _macd_cross(dif: list[float | None], dea: list[float | None]) -> str | None:
    """Detect a fresh DIF/DEA cross on the last two valid bars."""
    pairs = [(d, e) for d, e in zip(dif, dea) if d is not None and e is not None]
    if len(pairs) < 2:
        return None
    a, b = pairs[-2], pairs[-1]
    if a[0] <= a[1] and b[0] > b[1]:
        return "金叉"
    if a[0] >= a[1] and b[0] < b[1]:
        return "死叉"
    return None


def _detect_divergence(
    series: list[dict[str, Any]],
    closes: list[float],
    dif: list[float | None],
    rsi: list[float | None],
) -> dict[str, Any]:
    """Soft local MACD/RSI divergence vs price swings over the last ~40 bars."""
    n = len(closes)
    if n < 25:
        return {"macd": None, "rsi": None, "note": "样本偏短，背离检测降权"}
    window = closes[-40:]
    off = n - len(window)
    hi1 = max(range(len(window)), key=lambda i: window[i])
    # Second high: earlier peak not adjacent to hi1.
    hi_cands = sorted(range(len(window)), key=lambda i: window[i], reverse=True)
    hi2 = None
    for i in hi_cands:
        if abs(i - hi1) >= 5:
            hi2 = i
            break
    lo1 = min(range(len(window)), key=lambda i: window[i])
    lo_cands = sorted(range(len(window)), key=lambda i: window[i])
    lo2 = None
    for i in lo_cands:
        if abs(i - lo1) >= 5:
            lo2 = i
            break
    macd_div = None
    rsi_div = None
    if hi2 is not None and hi1 > hi2 and window[hi1] > window[hi2] * 1.002:
        d1, d2 = dif[off + hi1], dif[off + hi2]
        r1, r2 = rsi[off + hi1], rsi[off + hi2]
        if d1 is not None and d2 is not None and d1 < d2:
            macd_div = {
                "kind": "顶背离",
                "note": "价格抬高而 DIF 未同步抬高",
                "date": series[off + hi1].get("date") or "",
            }
        if r1 is not None and r2 is not None and r1 < r2:
            rsi_div = {
                "kind": "顶背离",
                "note": "价格抬高而 RSI 未同步抬高",
                "date": series[off + hi1].get("date") or "",
            }
    if lo2 is not None and lo1 > lo2 and window[lo1] < window[lo2] * 0.998:
        d1, d2 = dif[off + lo1], dif[off + lo2]
        r1, r2 = rsi[off + lo1], rsi[off + lo2]
        if d1 is not None and d2 is not None and d1 > d2:
            macd_div = {
                "kind": "底背离",
                "note": "价格走低而 DIF 未同步走低",
                "date": series[off + lo1].get("date") or "",
            }
        if r1 is not None and r2 is not None and r1 > r2:
            rsi_div = {
                "kind": "底背离",
                "note": "价格走低而 RSI 未同步走低",
                "date": series[off + lo1].get("date") or "",
            }
    note = "暂无清晰背离"
    if macd_div or rsi_div:
        parts = []
        if macd_div:
            parts.append(f"MACD{macd_div['kind']}")
        if rsi_div:
            parts.append(f"RSI{rsi_div['kind']}")
        note = " / ".join(parts)
    return {"macd": macd_div, "rsi": rsi_div, "note": note}
