"""Environment-adaptive buy-gate score (shadow first; optional soft live).

Builds a day-level ``env_score`` / ``env_bucket`` from the CSI1000 regime ruler,
desk volatility, and a small set of *effective* technical features (Dow-like
structure, Granville-style MA touches, MACD/RSI/ATR expansion, open gap).
Maps to ETF vs stock gate multipliers for ``minute`` / ``off_high`` / ``thin``.

Does **not** turn regime alone into ready=false. Live application is off until
``env_gate_live`` is enabled after shadow evidence.
"""

from __future__ import annotations

from typing import Any, Iterable

from market_desk.numbers import num

GATE_KEYS = ("minute", "off_high", "thin", "rel_strength")
BUCKETS = ("hostile", "neutral", "friendly")


def _closes(closes: Iterable[Any] | None) -> list[tuple[str, float]]:
    """Normalize closes to ``[(YYYY-MM-DD, close)]`` oldest-first."""
    out: list[tuple[str, float]] = []
    for row in closes or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            day = str(row[0] or "")[:10]
            px = num(row[1])
        elif isinstance(row, dict):
            day = str(row.get("date") or row.get("trade_date") or "")[:10]
            px = num(row.get("close") or row.get("price"))
        else:
            continue
        if day and px is not None and float(px) > 0:
            out.append((day, float(px)))
    out.sort(key=lambda x: x[0])
    return out


def _ohlc_bars(bars: Iterable[Any] | None) -> list[dict[str, float]]:
    """Normalize OHLC(V) bars when available (else empty)."""
    out: list[dict[str, float]] = []
    for row in bars or []:
        if not isinstance(row, dict):
            continue
        c = num(row.get("close") or row.get("price"))
        if c is None or float(c) <= 0:
            continue
        o = num(row.get("open"))
        h = num(row.get("high"))
        l = num(row.get("low"))
        v = num(row.get("volume") or row.get("vol") or row.get("amount"))
        item = {
            "close": float(c),
            "open": float(o) if o is not None else float(c),
            "high": float(h) if h is not None else float(c),
            "low": float(l) if l is not None else float(c),
        }
        if v is not None and float(v) > 0:
            item["volume"] = float(v)
        out.append(item)
    return out


def _sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / float(n)


def _atr_series(bars: list[dict[str, float]], n: int = 14) -> list[float | None]:
    """Wilder ATR aligned with ``bars``; leading None until warm."""
    out: list[float | None] = [None] * len(bars)
    if len(bars) < 2:
        return out
    trs: list[float] = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) <= n:
        return out
    atr = sum(trs[1 : n + 1]) / float(n)
    out[n] = atr
    for i in range(n + 1, len(trs)):
        atr = (atr * (n - 1) + trs[i]) / float(n)
        out[i] = atr
    return out


def feature_dow_structure(closes: list[float], lookback: int = 20) -> dict[str, Any]:
    """Dow-like 1D: higher highs/lows vs lower highs/lows on recent swings."""
    if len(closes) < max(10, lookback):
        return {"ok": False, "structure": "unknown", "score": 0.0}
    window = closes[-lookback:]
    mid = len(window) // 2
    left, right = window[:mid], window[mid:]
    lh, ll = max(left), min(left)
    rh, rl = max(right), min(right)
    if rh > lh and rl > ll:
        structure, score = "up", 0.15
    elif rh < lh and rl < ll:
        structure, score = "down", -0.15
    else:
        structure, score = "range", 0.0
    return {
        "ok": True,
        "structure": structure,
        "score": score,
        "left_high": round(lh, 4),
        "right_high": round(rh, 4),
    }


def feature_ma_signals(closes: list[float]) -> dict[str, Any]:
    """Granville-style subset: MA5/10/20 stack + pullback-to-MA20 support."""
    if len(closes) < 25:
        return {"ok": False, "score": 0.0, "tags": []}
    c = closes[-1]
    ma5, ma10, ma20 = _sma(closes, 5), _sma(closes, 10), _sma(closes, 20)
    tags: list[str] = []
    score = 0.0
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20 and c > ma5:
            tags.append("多头排列")
            score += 0.12
        elif ma5 < ma10 < ma20 and c < ma5:
            tags.append("空头排列")
            score -= 0.12
        # Pullback toward MA20 while still above it (support bounce setup).
        if c >= ma20 and abs(c / ma20 - 1.0) <= 0.012 and ma5 and ma5 >= ma20:
            tags.append("回踩MA20支撑")
            score += 0.08
        # Break below MA20 after having been above (risk-off).
        prev = closes[-2]
        if prev >= ma20 > c:
            tags.append("跌破MA20")
            score -= 0.10
    return {"ok": True, "score": round(score, 3), "tags": tags, "ma20": ma20}


def feature_macd_rsi(closes: list[float]) -> dict[str, Any]:
    """MACD hist sign + RSI zone (soft env nudge only)."""
    if len(closes) < 35:
        return {"ok": False, "score": 0.0}
    try:
        from market_desk.elliott.indicators import _ema, _rsi
    except Exception:
        return {"ok": False, "score": 0.0}
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if ema12[i] is not None and ema26[i] is not None:
            dif[i] = float(ema12[i]) - float(ema26[i])
    dif_filled = [d if d is not None else 0.0 for d in dif]
    dea_raw = _ema(dif_filled, 9)
    last = len(closes) - 1
    if dif[last] is None or dea_raw[last] is None:
        return {"ok": False, "score": 0.0}
    hist = float(dif[last]) - float(dea_raw[last])
    rsi_series = _rsi(closes, 14)
    rsi = rsi_series[last]
    if rsi is None:
        return {"ok": False, "score": 0.0}
    score = 0.0
    tags: list[str] = []
    if hist > 0:
        tags.append("MACD柱正")
        score += 0.06
    elif hist < 0:
        tags.append("MACD柱负")
        score -= 0.06
    if rsi >= 70:
        tags.append("RSI超买")
        score -= 0.04
    elif rsi <= 30:
        tags.append("RSI超卖")
        score -= 0.02
    elif 45 <= rsi <= 65:
        tags.append("RSI中性偏强")
        score += 0.04
    return {
        "ok": True,
        "score": round(score, 3),
        "tags": tags,
        "macd_hist": round(hist, 4),
        "rsi": round(float(rsi), 2),
    }


def feature_atr_expansion(bars: list[dict[str, float]], n: int = 14, ma_n: int = 20) -> dict[str, Any]:
    """vnpy-style ATR vs ATR_MA: expansion supports trend; crush is quieter."""
    atr = _atr_series(bars, n=n)
    vals = [a for a in atr if a is not None]
    if len(vals) < ma_n:
        return {"ok": False, "score": 0.0}
    last = vals[-1]
    ma = sum(vals[-ma_n:]) / float(ma_n)
    ratio = last / ma if ma > 0 else 1.0
    if ratio >= 1.15:
        score, tag = 0.06, "ATR扩张"
    elif ratio <= 0.85:
        score, tag = -0.04, "ATR收缩"
    else:
        score, tag = 0.0, "ATR中性"
    return {
        "ok": True,
        "score": score,
        "tag": tag,
        "atr": round(last, 4),
        "atr_ma": round(ma, 4),
        "ratio": round(ratio, 3),
    }


def feature_gap(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Open-gap nudge from desk metrics when present (metrics-only fallback)."""
    m = metrics or {}
    gap = num(m.get("index_gap_pct") or m.get("bench_gap_pct") or m.get("gap_pct"))
    if gap is None:
        # Fallback: hs300 open impulse proxy.
        gap = num(m.get("hs300_pct"))
        if gap is None:
            return {"ok": False, "score": 0.0}
        # Intraday % is not a true gap; weak weight only.
        if abs(float(gap)) >= 1.5:
            return {"ok": True, "score": 0.03 if gap > 0 else -0.05, "tag": "指数大幅波动", "gap_pct": gap, "proxy": True}
        return {"ok": True, "score": 0.0, "tag": "", "gap_pct": gap, "proxy": True}
    g = float(gap)
    if g >= 1.0:
        return {"ok": True, "score": 0.05, "tag": "高开缺口", "gap_pct": g}
    if g <= -1.0:
        return {"ok": True, "score": -0.08, "tag": "低开缺口", "gap_pct": g}
    return {"ok": True, "score": 0.0, "tag": "缺口中性", "gap_pct": g}


def feature_adx(bars: list[dict[str, float]], n: int = 14) -> dict[str, Any]:
    """Wilder ADX: strong trend supports env; chop dampens chase friendliness."""
    if len(bars) < n + 2:
        return {"ok": False, "score": 0.0}
    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    trs: list[float] = [0.0]
    for i in range(1, len(bars)):
        up = bars[i]["high"] - bars[i - 1]["high"]
        down = bars[i - 1]["low"] - bars[i]["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) <= n:
        return {"ok": False, "score": 0.0}
    atr = sum(trs[1 : n + 1]) / float(n)
    pdm = sum(plus_dm[1 : n + 1]) / float(n)
    mdm = sum(minus_dm[1 : n + 1]) / float(n)
    dx_vals: list[float] = []
    for i in range(n + 1, len(trs)):
        atr = (atr * (n - 1) + trs[i]) / float(n)
        pdm = (pdm * (n - 1) + plus_dm[i]) / float(n)
        mdm = (mdm * (n - 1) + minus_dm[i]) / float(n)
        if atr <= 0:
            continue
        pdi = 100.0 * pdm / atr
        mdi = 100.0 * mdm / atr
        denom = pdi + mdi
        dx_vals.append(0.0 if denom <= 0 else 100.0 * abs(pdi - mdi) / denom)
    if len(dx_vals) < n:
        return {"ok": False, "score": 0.0}
    adx = sum(dx_vals[:n]) / float(n)
    for dx in dx_vals[n:]:
        adx = (adx * (n - 1) + dx) / float(n)
    tags: list[str] = []
    if adx >= 25:
        tags.append("ADX强趋势")
        score = 0.05
    elif adx <= 15:
        tags.append("ADX弱震荡")
        score = -0.03
    else:
        tags.append("ADX中性")
        score = 0.0
    return {"ok": True, "score": score, "tags": tags, "adx": round(adx, 2)}


def feature_boll(closes: list[float], n: int = 20, k: float = 2.0) -> dict[str, Any]:
    """Bollinger bandwidth + %B: squeeze is cautious; mid-band bounce mild plus."""
    if len(closes) < n:
        return {"ok": False, "score": 0.0}
    window = closes[-n:]
    ma = sum(window) / float(n)
    var = sum((x - ma) ** 2 for x in window) / float(n)
    std = var ** 0.5
    if ma <= 0:
        return {"ok": False, "score": 0.0}
    upper = ma + k * std
    lower = ma - k * std
    width = (upper - lower) / ma
    band = upper - lower
    pct_b = (closes[-1] - lower) / band if band > 1e-12 else 0.5
    # Historical width for squeeze detect.
    widths: list[float] = []
    for i in range(n, len(closes) + 1):
        w = closes[i - n : i]
        m = sum(w) / float(n)
        if m <= 0:
            continue
        s = (sum((x - m) ** 2 for x in w) / float(n)) ** 0.5
        widths.append((2.0 * k * s) / m)
    tags: list[str] = []
    score = 0.0
    if widths:
        med = sorted(widths)[len(widths) // 2]
        if width <= med * 0.75:
            tags.append("BOLL收窄")
            score -= 0.03
        elif width >= med * 1.35:
            tags.append("BOLL扩张")
            score += 0.03
    if 0.35 <= pct_b <= 0.65:
        tags.append("BOLL中轨区")
        score += 0.02
    elif pct_b >= 0.95:
        tags.append("BOLL上沿")
        score -= 0.03
    elif pct_b <= 0.05:
        tags.append("BOLL下沿")
        score -= 0.02
    return {
        "ok": True,
        "score": round(score, 3),
        "tags": tags,
        "bandwidth": round(width, 4),
        "pct_b": round(pct_b, 3),
    }


def feature_volume(bars: list[dict[str, float]], n: int = 20) -> dict[str, Any]:
    """Volume confirmation vs recent mean; skipped when bars lack volume."""
    vols = [float(b["volume"]) for b in bars if b.get("volume") and float(b["volume"]) > 0]
    if len(vols) < max(8, n // 2):
        return {"ok": False, "score": 0.0, "tags": [], "note": "无量能数据"}
    look = vols[-n:] if len(vols) >= n else vols
    mean = sum(look) / float(len(look))
    if mean <= 0:
        return {"ok": False, "score": 0.0}
    last = vols[-1]
    ratio = last / mean
    tags: list[str] = []
    if ratio >= 1.4:
        tags.append("量能放大")
        score = 0.04
    elif ratio <= 0.6:
        tags.append("量能萎缩")
        score = -0.03
    else:
        tags.append("量能中性")
        score = 0.0
    return {
        "ok": True,
        "score": score,
        "tags": tags,
        "ratio": round(ratio, 3),
        "last": round(last, 2),
        "mean": round(mean, 2),
    }


def collect_features(
    *,
    closes: Iterable[Any] | None,
    bars: Iterable[Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the effective feature pack; return parts + sum score contribution."""
    pairs = _closes(closes)
    px = [p[1] for p in pairs]
    ohlc = _ohlc_bars(bars) if bars is not None else []
    if not ohlc and px:
        ohlc = [{"open": c, "high": c, "low": c, "close": c} for c in px]
    try:
        from market_desk.pattern_tags import feature_gap_and_kline

        gap_kline = feature_gap_and_kline(ohlc, metrics=metrics)
        if not gap_kline.get("ok"):
            # Metrics-only gap fallback when OHLC continuity is flat.
            g = feature_gap(metrics)
            gap_kline = {
                "ok": bool(g.get("ok")),
                "score": float(g.get("score") or 0),
                "tags": [g["tag"]] if g.get("tag") else [],
                "gap": g,
                "kline": {"ok": False, "tags": [], "score": 0.0},
            }
    except Exception:
        g = feature_gap(metrics)
        gap_kline = {
            "ok": bool(g.get("ok")),
            "score": float(g.get("score") or 0),
            "tags": [g["tag"]] if g.get("tag") else [],
            "gap": g,
            "kline": {"ok": False, "tags": [], "score": 0.0},
        }
    parts = {
        "dow": feature_dow_structure(px),
        "ma": feature_ma_signals(px),
        "macd_rsi": feature_macd_rsi(px),
        "atr": feature_atr_expansion(ohlc),
        "adx": feature_adx(ohlc),
        "boll": feature_boll(px),
        "volume": feature_volume(ohlc),
        "gap_kline": gap_kline,
        # Keep legacy key for older consumers reading features.gap.
        "gap": gap_kline.get("gap") if isinstance(gap_kline.get("gap"), dict) else feature_gap(metrics),
    }
    feat_score = 0.0
    for key, p in parts.items():
        if key == "gap":
            continue  # counted via gap_kline
        if isinstance(p, dict):
            feat_score += float(p.get("score") or 0)
    tags: list[str] = []
    for key, p in parts.items():
        if key == "gap" or not isinstance(p, dict):
            continue
        if p.get("tag"):
            tags.append(str(p["tag"]))
        for t in p.get("tags") or []:
            tags.append(str(t))
        if p.get("structure") in ("up", "down"):
            tags.append(f"道氏{p['structure']}")
        gap_row = p.get("gap") if key == "gap_kline" else None
        if isinstance(gap_row, dict) and gap_row.get("kind") and gap_row.get("kind") not in ("未知", "无缺口", "unknown"):
            tags.append(str(gap_row["kind"]))
    # Dedup preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return {
        "parts": parts,
        "feat_score": round(feat_score, 3),
        "tags": uniq[:14],
        "bars": len(px),
    }


def _regime_score(regime: dict[str, Any] | None) -> tuple[float, str]:
    mr = regime if isinstance(regime, dict) else {}
    side = str(mr.get("side") or "")
    gap = num(mr.get("gap_pct")) or 0.0
    if side == "above":
        # Deep above MA = friendlier; barely above = mild.
        base = 0.22 + min(0.12, max(0.0, float(gap)) / 100.0 * 4.0)
        return base, "尺上"
    if side == "below":
        base = -0.22 + max(-0.12, min(0.0, float(gap)) / 100.0 * 4.0)
        return base, "尺下"
    return 0.0, "尺未知"


def _vol_score(vol: str | None) -> tuple[float, str]:
    v = str(vol or "").strip()
    if v == "high":
        return -0.12, "高波动"
    if v == "low":
        return 0.08, "低波动"
    return 0.0, "波动未知"


def score_to_bucket(score: float) -> str:
    """Map continuous score in roughly [-1,1] to hostile/neutral/friendly."""
    if score >= 0.18:
        return "friendly"
    if score <= -0.12:
        return "hostile"
    return "neutral"


def env_mult_table(bucket: str, kind: str) -> dict[str, float]:
    """Return gate multipliers for one env bucket and instrument kind.

    Values < 1 loosen thresholds (friendlier); > 1 tighten. ETF uses a
    compressed scale toward 1.0 because beta is lower.
    """
    from market_desk.config import (
        ENV_ETF_SCALE,
        ENV_FRIENDLY_GATE,
        ENV_HOSTILE_GATE,
        ENV_STOCK_SCALE,
    )

    b = bucket if bucket in BUCKETS else "neutral"
    k = "etf" if str(kind or "").lower() == "etf" else "stock"
    if b == "friendly":
        raw = float(ENV_FRIENDLY_GATE)
    elif b == "hostile":
        raw = float(ENV_HOSTILE_GATE)
    else:
        raw = 1.0
    scale = float(ENV_ETF_SCALE) if k == "etf" else float(ENV_STOCK_SCALE)
    # Compress deviation from 1.0 by scale.
    mult = 1.0 + (raw - 1.0) * scale
    mult = max(0.75, min(1.25, mult))
    out = {key: round(mult, 3) for key in GATE_KEYS}
    # Thin / rel_strength only meaningful for stocks.
    if k == "etf":
        out["thin"] = 1.0
        out["rel_strength"] = 1.0
    return out


def build_env_gate(
    *,
    regime: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    closes: Iterable[Any] | None = None,
    bars: Iterable[Any] | None = None,
    vol: str | None = None,
    asof: str | None = None,
) -> dict[str, Any]:
    """Build day-level environment gate snapshot for desk + freeze."""
    from market_desk.adapt.context import vol_bucket_from_metrics
    from market_desk.config import ENV_GATE_SHADOW_SINCE, REVIEW_BENCH_LABEL

    vol_b = str(vol or "").strip() or vol_bucket_from_metrics(metrics)
    if vol_b not in ("high", "low", "unknown"):
        vol_b = "unknown"
    r_score, r_tag = _regime_score(regime)
    v_score, v_tag = _vol_score(vol_b)
    feats = collect_features(closes=closes, bars=bars, metrics=metrics)
    score = r_score + v_score + float(feats.get("feat_score") or 0)
    score = max(-1.0, min(1.0, score))
    bucket = score_to_bucket(score)
    tags = [t for t in (r_tag, v_tag, *(feats.get("tags") or [])) if t]
    stock_m = env_mult_table(bucket, "stock")
    etf_m = env_mult_table(bucket, "etf")
    return {
        "ok": True,
        "schema": "env_gate.v1",
        "asof": str(asof or (regime or {}).get("asof") or "")[:10] or None,
        "label": REVIEW_BENCH_LABEL,
        "score": round(score, 3),
        "bucket": bucket,
        "vol": vol_b,
        "regime_side": str((regime or {}).get("side") or "unknown"),
        "tags": tags[:14],
        "features": feats.get("parts") or {},
        "feat_score": feats.get("feat_score"),
        "mult": {"stock": stock_m, "etf": etf_m},
        "note": (
            f"{bucket}·score={score:.2f} · 影子记账"
            f"（自 {ENV_GATE_SHADOW_SINCE}）· 默认不改 ready"
        ),
        "live": False,
        "shadow_since": ENV_GATE_SHADOW_SINCE,
    }


def freeze_env_gate(
    snapshot: dict[str, Any] | None,
    *,
    kind: str = "stock",
) -> dict[str, Any] | None:
    """Compact env_gate for signal payload freeze (kind-specific mult slice)."""
    eg = (snapshot or {}).get("env_gate") if isinstance(snapshot, dict) else None
    if not isinstance(eg, dict) or not eg.get("ok"):
        return None
    bucket = str(eg.get("bucket") or "")
    if bucket not in BUCKETS:
        return None
    k = "etf" if str(kind or "").lower() == "etf" else "stock"
    mults = (eg.get("mult") or {}).get(k) or env_mult_table(bucket, k)
    return {
        "schema": "env_gate.v1",
        "asof": eg.get("asof"),
        "score": eg.get("score"),
        "bucket": bucket,
        "vol": eg.get("vol"),
        "regime_side": eg.get("regime_side"),
        "kind": k,
        "mult": {key: mults.get(key) for key in GATE_KEYS},
        "tags": list(eg.get("tags") or [])[:10],
        "live": bool(eg.get("live")),
    }


def _live_gate_keys() -> set[str]:
    """Keys allowed to receive env live mults (default: minute only)."""
    try:
        from market_desk.settings import setting

        raw = setting("env_gate_live_keys", None)
    except Exception:
        raw = None
    if raw is None:
        try:
            from market_desk.config import ENV_GATE_LIVE_KEYS

            keys = ENV_GATE_LIVE_KEYS
        except Exception:
            keys = ("minute",)
    elif isinstance(raw, (list, tuple, set)):
        keys = raw
    else:
        keys = [x.strip() for x in str(raw).replace(";", ",").split(",") if x.strip()]
    out = {str(k).strip() for k in keys if str(k).strip() in GATE_KEYS}
    return out or {"minute"}


def env_gate_mult_for_key(gate_key: str, kind: str = "stock", default: float = 1.0) -> float:
    """Read live env multiplier for one gate when ``env_gate_live`` is on.

    Only keys listed in ``env_gate_live_keys`` / ``ENV_GATE_LIVE_KEYS`` apply
    (default ``minute``); others stay at ``default`` even when live is enabled.
    """
    try:
        from market_desk.settings import setting

        if not bool(setting("env_gate_live", False)):
            return float(default)
    except Exception:
        return float(default)
    if str(gate_key or "") not in _live_gate_keys():
        return float(default)
    try:
        from market_desk.adapt.context import _ADAPT_CACHE

        eg = _ADAPT_CACHE.get("env_gate")
        if not isinstance(eg, dict) or not eg.get("ok"):
            return float(default)
        k = "etf" if str(kind or "").lower() == "etf" else "stock"
        mults = (eg.get("mult") or {}).get(k) or {}
        m = mults.get(gate_key)
        if m is None:
            return float(default)
        return max(0.75, min(1.25, float(m)))
    except Exception:
        return float(default)


def cache_env_gate(env: dict[str, Any] | None) -> None:
    """Store day env_gate into adapt cache for optional live mult lookup."""
    try:
        from market_desk.adapt.context import _ADAPT_CACHE, _cache_day

        _ADAPT_CACHE["day"] = _cache_day()
        _ADAPT_CACHE["env_gate"] = dict(env) if isinstance(env, dict) else None
    except Exception:
        pass


def summarize_env_gate_coverage(rows: list[dict[str, Any]], *, since: str | None = None) -> dict[str, Any]:
    """Shadow coverage helper for ops / agent-pack."""
    from market_desk.config import ENV_GATE_SHADOW_SINCE

    cut = str(since or ENV_GATE_SHADOW_SINCE)[:10]
    buy = [r for r in rows or [] if str(r.get("trade_date") or "")[:10] >= cut]
    n = len(buy)
    frozen = 0
    by_bucket: dict[str, int] = {}
    for r in buy:
        p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        eg = p.get("env_gate") if isinstance(p.get("env_gate"), dict) else {}
        b = str(eg.get("bucket") or "")
        if b in BUCKETS:
            frozen += 1
            by_bucket[b] = by_bucket.get(b, 0) + 1
    lit = build_env_lit_shadow(rows, since=cut)
    return {
        "ok": True,
        "since": cut,
        "n": n,
        "frozen_n": frozen,
        "cover": round(frozen / n, 3) if n else None,
        "by_bucket": by_bucket,
        "lit": lit,
        "note": "买卡 payload.env_gate 冻结覆盖；live 默认关；亮灯率见 lit",
    }


def _row_lit(row: dict[str, Any]) -> bool:
    p = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return bool(p.get("ever_ready") or row.get("ready") or p.get("first_ready_at"))


def build_env_lit_shadow(
    rows: list[dict[str, Any]] | None,
    *,
    since: str | None = None,
    min_days: int | None = None,
) -> dict[str, Any]:
    """Report lit-rate × env bucket × kind; gate live recommendation (minute-first).

    Live is recommended only when frozen shadow days ≥ ``min_days`` and friendly
    stock lit-rate clearly beats hostile. Does not flip ``env_gate_live`` itself.
    """
    from market_desk.config import ENV_GATE_LIT_MIN_DAYS, ENV_GATE_LIVE_KEYS, ENV_GATE_SHADOW_SINCE
    from market_desk.numbers import num

    cut = str(since or ENV_GATE_SHADOW_SINCE)[:10]
    need = int(min_days if min_days is not None else ENV_GATE_LIT_MIN_DAYS)
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    days: set[str] = set()
    for row in rows or []:
        day = str(row.get("trade_date") or "")[:10]
        if not day or day < cut:
            continue
        if int(row.get("skipped") or 0):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        eg = payload.get("env_gate") if isinstance(payload.get("env_gate"), dict) else {}
        bucket = str(eg.get("bucket") or "")
        if bucket not in BUCKETS:
            continue
        kind = "etf" if str(row.get("kind") or eg.get("kind") or "") == "etf" else "stock"
        days.add(day)
        key = (bucket, kind)
        cell = cells.setdefault(
            key,
            {"bucket": bucket, "kind": kind, "n": 0, "lit_n": 0, "abs": [], "xs": []},
        )
        cell["n"] += 1
        if _row_lit(row):
            cell["lit_n"] += 1
        d3 = num(row.get("outcome_day3_pct"))
        if d3 is not None:
            cell["abs"].append(float(d3))
        xs = num(row.get("excess_d3_pct"))
        if xs is not None:
            cell["xs"].append(float(xs))

    table: list[dict[str, Any]] = []
    for (bucket, kind), cell in sorted(cells.items(), key=lambda x: (x[0][0], x[0][1])):
        n = int(cell["n"])
        lit_n = int(cell["lit_n"])
        abs_vals = cell["abs"]
        xs_vals = cell["xs"]
        table.append(
            {
                "bucket": bucket,
                "kind": kind,
                "n": n,
                "lit_n": lit_n,
                "lit_rate": round(100.0 * lit_n / n, 1) if n else None,
                "abs_win": (
                    round(100.0 * sum(1 for v in abs_vals if v > 0) / len(abs_vals), 1)
                    if abs_vals
                    else None
                ),
                "xs_win": (
                    round(100.0 * sum(1 for v in xs_vals if v > 0) / len(xs_vals), 1)
                    if xs_vals
                    else None
                ),
                "mean_abs": round(sum(abs_vals) / len(abs_vals), 2) if abs_vals else None,
                "mean_xs": round(sum(xs_vals) / len(xs_vals), 2) if xs_vals else None,
            }
        )

    def _lit_rate(bucket: str, kind: str) -> float | None:
        for row in table:
            if row["bucket"] == bucket and row["kind"] == kind and row["lit_rate"] is not None:
                return float(row["lit_rate"])
        return None

    day_n = len(days)
    fri = _lit_rate("friendly", "stock")
    hos = _lit_rate("hostile", "stock")
    spread = (fri - hos) if fri is not None and hos is not None else None
    enough_days = day_n >= need
    # Friendly should light more often than hostile by ≥5pp with both sides present.
    spread_ok = spread is not None and spread >= 5.0
    live_recommend = bool(enough_days and spread_ok)
    live_keys = list(ENV_GATE_LIVE_KEYS)
    if live_recommend:
        note = (
            f"影子满 {day_n} 日≥{need} · stock 亮灯 friendly−hostile="
            f"{spread:.1f}pp → 可开 live（建议先 {'/'.join(live_keys)}）"
        )
    elif not enough_days:
        note = f"影子交易日 {day_n}/{need} · 继续记账，勿开 live"
    else:
        note = (
            f"日数够但 stock 亮灯差不足"
            f"（friendly={fri} hostile={hos} spread={spread}）· 维持影子"
        )
    return {
        "ok": True,
        "schema": "env_lit_shadow.v1",
        "since": cut,
        "day_n": day_n,
        "min_days": need,
        "enough_days": enough_days,
        "table": table,
        "stock_spread_pp": round(spread, 1) if spread is not None else None,
        "live_recommend": live_recommend,
        "live_keys_suggest": live_keys,
        "note": note,
    }
