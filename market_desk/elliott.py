"""Elliott-wave style multi-scenario index readout (observe-only).

This is a rule-based zigzag + hypothesis board for 上证指数. It never claims a
single true count: every classic regime is scored and listed with a forward path
and an invalidation level. Does not feed desk buy/sell gates.
"""

from __future__ import annotations

from typing import Any


# Full catalog the UI always shows (ranked by fit).
_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "imp_up_w1",
        "family": "上升推动",
        "wave": "第1浪",
        "bias": "bull",
        "title": "上升推动 · 第1浪启动",
        "next": "第1浪常伴随缩量试探；若站稳，预期回撤第2浪（回吐30%–61.8%），再迎更强第3浪。",
        "risk": "跌破启动低点则本浪计数作废，改看下跌或更长调整。",
    },
    {
        "id": "imp_up_w2",
        "family": "上升推动",
        "wave": "第2浪",
        "bias": "bull",
        "title": "上升推动 · 第2浪回调",
        "next": "第2浪多为锯齿/平台回撤；不破第1浪起点则仍偏多。结束后第3浪往往加速。",
        "risk": "跌破第1浪起点→上升推动失效，优先改标下跌推动或调整C浪。",
    },
    {
        "id": "imp_up_w3",
        "family": "上升推动",
        "wave": "第3浪",
        "bias": "bull",
        "title": "上升推动 · 第3浪主升",
        "next": "第3浪通常不是最短浪，量能/斜率偏强。过后常见第4浪横盘或浅回撤，再第5浪末升。",
        "risk": "若此段幅度明显短于第1浪且随后深跌，需警惕失败结构或计数改标。",
    },
    {
        "id": "imp_up_w4",
        "family": "上升推动",
        "wave": "第4浪",
        "bias": "bull",
        "title": "上升推动 · 第4浪整理",
        "next": "第4浪常见平台/三角；理想不深入第1浪价格区间。结束后第5浪冲高，完成五浪。",
        "risk": "深度跌回第1浪区间或破第3浪起点→四浪失败，或整体改计调整浪。",
    },
    {
        "id": "imp_up_w5",
        "family": "上升推动",
        "wave": "第5浪",
        "bias": "bull",
        "title": "上升推动 · 第5浪末升",
        "next": "第5浪可创新高但动量常弱于第3浪；结束后进入A-B-C调整概率上升。",
        "risk": "无法过第3浪高点的「失败第5」→转弱更快，按已完成推动对待。",
    },
    {
        "id": "corr_a",
        "family": "调整浪",
        "wave": "A浪",
        "bias": "bear",
        "title": "调整 · A浪下跌",
        "next": "A浪打开调整空间；之后常见B浪反抽（回撤A浪38%–79%），再C浪下跌。",
        "risk": "若A浪很浅且迅速收复，可能只是上升推动内部的第2/第4浪。",
    },
    {
        "id": "corr_b",
        "family": "调整浪",
        "wave": "B浪",
        "bias": "neutral",
        "title": "调整 · B浪反抽",
        "next": "B浪反抽常诱多；不过前高或仅收回A浪大半。结束后C浪下跌往往更完整。",
        "risk": "强势收复A浪全部并站稳→可能不是调整B，而是新推动第1/第3浪。",
    },
    {
        "id": "corr_c",
        "family": "调整浪",
        "wave": "C浪",
        "bias": "bear",
        "title": "调整 · C浪下跌",
        "next": "C浪常与A浪等长或1.618倍；完成后若见底背离，可能开启新上升推动第1浪。",
        "risk": "跌破关键支撑后仍加速→调整可能演化为下跌推动，而非简单ABC。",
    },
    {
        "id": "imp_dn_w1",
        "family": "下跌推动",
        "wave": "第1浪",
        "bias": "bear",
        "title": "下跌推动 · 第1浪",
        "next": "下跌五浪的第一段；随后第2浪反抽（不破第1浪起点高点），再第3浪主跌。",
        "risk": "快速收复第1浪高点→下跌推动作废，改看上升调整B或新多头。",
    },
    {
        "id": "imp_dn_w2",
        "family": "下跌推动",
        "wave": "第2浪",
        "bias": "bear",
        "title": "下跌推动 · 第2浪反抽",
        "next": "第2浪反抽常诱多；不过前高则仍偏空。结束后第3浪主跌概率上升。",
        "risk": "突破第1浪起点高点→下跌五浪计数失败。",
    },
    {
        "id": "imp_dn_w3",
        "family": "下跌推动",
        "wave": "第3浪",
        "bias": "bear",
        "title": "下跌推动 · 第3浪主跌",
        "next": "主跌段斜率常陡；之后第4浪反抽，再第5浪寻底。",
        "risk": "若此段明显短于第1浪且迅速反包，计数需降权。",
    },
    {
        "id": "imp_dn_w4",
        "family": "下跌推动",
        "wave": "第4浪",
        "bias": "bear",
        "title": "下跌推动 · 第4浪反抽",
        "next": "第4浪反抽后仍看第5浪再下一台阶；完成五浪后或现较大级别反弹。",
        "risk": "反抽过深进入第1浪区间→下跌推动可信度下降。",
    },
    {
        "id": "imp_dn_w5",
        "family": "下跌推动",
        "wave": "第5浪",
        "bias": "bear",
        "title": "下跌推动 · 第5浪寻底",
        "next": "第5浪末跌后，常见较大级别反弹（新上升推动或调整反抽）。可盯背离与关键支撑。",
        "risk": "跌破关键位后仍无止跌结构→下跌可能延伸/扩张。",
    },
    {
        "id": "triangle",
        "family": "盘整",
        "wave": "三角/平台",
        "bias": "neutral",
        "title": "盘整 · 三角或平台整理",
        "next": "波动收敛、高低点交错；突破方向决定下一浪归属（常接在第4浪或B浪位置）。",
        "risk": "假突破后回到箱体很常见；需等收盘站稳再认方向。",
    },
    {
        "id": "complex",
        "family": "盘整",
        "wave": "复合调整",
        "bias": "neutral",
        "title": "盘整 · 复合/延长调整",
        "next": "时间换空间：W-X-Y 一类结构，方向反复。仓位上宜轻、等清晰五浪或ABC完成。",
        "risk": "在复合调整中追涨杀跌胜率差；无效化看箱体上下沿。",
    },
)


def build_elliott_scenarios(
    bars: list[dict[str, Any]] | None,
    *,
    index_name: str = "上证指数",
    index_code: str = "000001",
    last_price: float | None = None,
) -> dict[str, Any]:
    """Score every catalogued Elliott regime against recent index swings.

    ``bars`` items need at least ``close``; ``high``/``low``/``date`` preferred.
    """
    series = _normalize_bars(bars)
    if len(series) < 30:
        return {
            "ok": False,
            "standalone": True,
            "index_name": index_name,
            "index_code": index_code,
            "note": "日线样本不足（需约30根以上），暂无法做浪型情景",
            "pivots": [],
            "structure": {},
            "scenarios": [],
            "primary": None,
            "disclaimer": "波浪计数多解，仅观察；不改作战台买卖结论。",
        }

    pivots = _zigzag_pivots(series, min_move_pct=2.2)
    last = float(last_price) if last_price and last_price > 0 else float(series[-1]["close"])
    structure = _structure_snapshot(pivots, last, series)
    scored: list[dict[str, Any]] = []
    for tpl in _SCENARIOS:
        fit, why = _score_fit(tpl["id"], structure, pivots, last)
        inv = _invalidation(tpl["id"], structure, last)
        levels = _key_levels(structure, last)
        scored.append(
            {
                **tpl,
                "fit": fit,
                "fit_note": why,
                "invalidation": inv,
                "levels": levels,
                "path": tpl["next"],
                "watch": tpl["risk"],
            }
        )
    scored.sort(key=lambda x: (-int(x.get("fit") or 0), str(x.get("id") or "")))
    indicators = _build_indicators(series)
    top = scored[:5]
    for row in top:
        row["turns"] = _scenario_turns(row["id"], structure, pivots, series, last, indicators)
        row["timing"] = _timing_summary(row["id"], indicators, structure)
    primary = top[0] if top else None
    return {
        "ok": True,
        "standalone": True,
        "index_name": index_name,
        "index_code": index_code,
        "last": round(last, 2),
        "bars": len(series),
        "pivot_n": len(pivots),
        "structure": structure,
        "pivots": pivots[-8:],
        "indicators": {
            "macd": indicators.get("macd_snap"),
            "rsi": indicators.get("rsi_snap"),
            "divergence": indicators.get("divergence"),
        },
        "scenarios": top,
        "catalog_n": len(scored),
        "primary": {
            "id": primary.get("id"),
            "title": primary.get("title"),
            "fit": primary.get("fit"),
            "wave": primary.get("wave"),
            "family": primary.get("family"),
        }
        if primary
        else None,
        "note": (
            f"{index_name} 近 {len(series)} 日 · 枢轴 {len(pivots)} 个 · "
            f"当前价 {round(last, 2)}；仅展示契合度最高的 5 个浪型情景"
        ),
        "disclaimer": (
            "艾略特波浪天然多解。本页只列 Top5 情景，并用斐波那契交易日窗口 + "
            "MACD 背离 / RSI 超买超卖做变盘日与点位参考；不改作战台可买入/ready。"
        ),
    }


def _normalize_bars(bars: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in bars or []:
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        high = row.get("high")
        low = row.get("low")
        try:
            hi = float(high) if high is not None else close
            lo = float(low) if low is not None else close
        except (TypeError, ValueError):
            hi, lo = close, close
        if hi < lo:
            hi, lo = lo, hi
        out.append(
            {
                "date": str(row.get("date") or row.get("trade_date") or ""),
                "close": close,
                "high": hi,
                "low": lo,
            }
        )
    return out


def _zigzag_pivots(
    series: list[dict[str, Any]],
    *,
    min_move_pct: float,
) -> list[dict[str, Any]]:
    """Build alternating high/low pivots with a minimum percentage swing."""
    if len(series) < 5:
        return []
    pivots: list[dict[str, Any]] = []
    # Seed with first bar as tentative low/high anchor.
    mode = "low"  # next confirmed extreme we hunt
    anchor_i = 0
    anchor_px = float(series[0]["low"])
    extreme_i = 0
    extreme_px = float(series[0]["high"])

    def _push(kind: str, idx: int, px: float) -> None:
        if pivots and pivots[-1]["kind"] == kind:
            # Keep the more extreme same-kind pivot.
            prev = pivots[-1]
            if kind == "high" and px >= float(prev["price"]):
                pivots[-1] = _pivot(kind, idx, px, series)
            elif kind == "low" and px <= float(prev["price"]):
                pivots[-1] = _pivot(kind, idx, px, series)
            return
        pivots.append(_pivot(kind, idx, px, series))

    for i, bar in enumerate(series):
        hi = float(bar["high"])
        lo = float(bar["low"])
        if mode == "low":
            # Climbing toward a high pivot.
            if hi >= extreme_px:
                extreme_px = hi
                extreme_i = i
            retrace = (extreme_px - lo) / extreme_px * 100.0 if extreme_px > 0 else 0.0
            if retrace >= min_move_pct and extreme_i > anchor_i:
                _push("high", extreme_i, extreme_px)
                mode = "high"
                anchor_i = extreme_i
                anchor_px = extreme_px
                extreme_i = i
                extreme_px = lo
        else:
            if lo <= extreme_px:
                extreme_px = lo
                extreme_i = i
            bounce = (hi - extreme_px) / extreme_px * 100.0 if extreme_px > 0 else 0.0
            if bounce >= min_move_pct and extreme_i > anchor_i:
                _push("low", extreme_i, extreme_px)
                mode = "low"
                anchor_i = extreme_i
                anchor_px = extreme_px
                extreme_i = i
                extreme_px = hi

    # Close out the last extreme if it moved enough from prior pivot.
    if pivots:
        last_kind = pivots[-1]["kind"]
        last_px = float(pivots[-1]["price"])
        if last_kind == "high" and mode == "high":
            move = (last_px - extreme_px) / last_px * 100.0 if last_px else 0.0
            if move >= min_move_pct * 0.8:
                _push("low", extreme_i, extreme_px)
        elif last_kind == "low" and mode == "low":
            move = (extreme_px - last_px) / last_px * 100.0 if last_px else 0.0
            if move >= min_move_pct * 0.8:
                _push("high", extreme_i, extreme_px)
    return pivots


def _pivot(kind: str, idx: int, px: float, series: list[dict[str, Any]]) -> dict[str, Any]:
    bar = series[idx]
    return {
        "kind": kind,
        "index": idx,
        "price": round(float(px), 2),
        "date": bar.get("date") or "",
    }


def _structure_snapshot(
    pivots: list[dict[str, Any]],
    last: float,
    series: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive recent swing context used by scenario scorers."""
    last_n = pivots[-6:] if len(pivots) >= 2 else list(pivots)
    highs = [p for p in last_n if p.get("kind") == "high"]
    lows = [p for p in last_n if p.get("kind") == "low"]
    last_pivot = pivots[-1] if pivots else None
    prev_pivot = pivots[-2] if len(pivots) >= 2 else None
    swing_pct = None
    if last_pivot and prev_pivot:
        a = float(prev_pivot["price"])
        b = float(last_pivot["price"])
        if a > 0:
            swing_pct = round((b - a) / a * 100.0, 2)
    # MA20 slope soft context.
    closes = [float(x["close"]) for x in series]
    ma20 = sum(closes[-20:]) / 20.0 if len(closes) >= 20 else None
    ma20_prev = sum(closes[-40:-20]) / 20.0 if len(closes) >= 40 else ma20
    trend = "up"
    if ma20 is not None and ma20_prev is not None:
        if ma20 > ma20_prev * 1.004 and last >= ma20:
            trend = "up"
        elif ma20 < ma20_prev * 0.996 and last <= ma20:
            trend = "down"
        else:
            trend = "side"
    # Contracting range?
    recent = pivots[-5:]
    contracting = False
    if len(recent) >= 4:
        spans = []
        for i in range(1, len(recent)):
            spans.append(abs(float(recent[i]["price"]) - float(recent[i - 1]["price"])))
        if len(spans) >= 3 and spans[-1] < spans[0] * 0.7:
            contracting = True
    hh = float(highs[-1]["price"]) if highs else None
    hl = float(lows[-1]["price"]) if lows else None
    ph = float(highs[-2]["price"]) if len(highs) >= 2 else None
    pl = float(lows[-2]["price"]) if len(lows) >= 2 else None
    higher_high = bool(hh is not None and ph is not None and hh > ph)
    higher_low = bool(hl is not None and pl is not None and hl > pl)
    lower_high = bool(hh is not None and ph is not None and hh < ph)
    lower_low = bool(hl is not None and pl is not None and hl < pl)
    from_low = None
    from_high = None
    if hl and hl > 0:
        from_low = round((last - hl) / hl * 100.0, 2)
    if hh and hh > 0:
        from_high = round((last - hh) / hh * 100.0, 2)
    return {
        "trend": trend,
        "last_pivot": last_pivot,
        "prev_pivot": prev_pivot,
        "swing_pct": swing_pct,
        "last_high": hh,
        "last_low": hl,
        "prev_high": ph,
        "prev_low": pl,
        "higher_high": higher_high,
        "higher_low": higher_low,
        "lower_high": lower_high,
        "lower_low": lower_low,
        "contracting": contracting,
        "from_last_low_pct": from_low,
        "from_last_high_pct": from_high,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "above_ma20": bool(ma20 is not None and last >= ma20),
    }


def _score_fit(
    sid: str,
    st: dict[str, Any],
    pivots: list[dict[str, Any]],
    last: float,
) -> tuple[int, str]:
    """Return fit 0–100 and a short reason."""
    trend = st.get("trend")
    lp = st.get("last_pivot") or {}
    kind = lp.get("kind")
    swing = abs(float(st.get("swing_pct") or 0))
    from_low = st.get("from_last_low_pct")
    from_high = st.get("from_last_high_pct")
    score = 20
    why = "结构一般"

    def add(n: int, note: str) -> None:
        nonlocal score, why
        score += n
        why = note

    if sid == "imp_up_w1":
        if trend == "up" and kind == "high" and st.get("higher_low") and swing >= 3:
            add(45, "低点抬高后上冲，像新推动第1段")
        elif trend == "side" and kind == "high" and (from_low or 0) >= 3:
            add(25, "箱体上沿试探，弱契合第1浪")
        else:
            add(0, "缺少明确抬高启动段")
    elif sid == "imp_up_w2":
        if trend in ("up", "side") and kind == "low" and st.get("higher_low") is False:
            # Pullback after a rise.
            if (from_high or 0) <= -2 and st.get("above_ma20"):
                add(40, "高位回撤且未明显破位，像第2浪")
            elif (from_high or 0) <= -2:
                add(28, "自高点回撤中，可作第2浪假设")
            else:
                add(5, "回撤幅度尚浅")
        else:
            add(0, "不是典型回撤低点结构")
    elif sid == "imp_up_w3":
        if trend == "up" and kind == "high" and st.get("higher_high") and swing >= 4:
            add(50, "趋势向上且创新高加速段，强契合第3浪")
        elif trend == "up" and st.get("higher_high"):
            add(35, "抬高高点，偏第3浪")
        else:
            add(0, "未见主升加速特征")
    elif sid == "imp_up_w4":
        if trend in ("up", "side") and st.get("contracting"):
            add(42, "波动收敛整理，像第4浪三角/平台")
        elif trend == "up" and kind == "low" and st.get("higher_low") and (from_high or 0) <= -1.5:
            add(36, "上升趋势中的浅回撤整理")
        else:
            add(5, "整理特征不足")
    elif sid == "imp_up_w5":
        if trend == "up" and kind == "high" and st.get("higher_high") and swing < 4:
            add(38, "创新高但摆动偏弱，像末升第5浪")
        elif trend == "up" and st.get("higher_high") and not st.get("above_ma20"):
            add(30, "高点抬高但已弱于均线，末升嫌疑")
        else:
            add(8, "末升证据一般")
    elif sid == "corr_a":
        if trend == "down" and kind == "low" and st.get("lower_high"):
            add(45, "高点下移后的第一段下跌，像A浪")
        elif kind == "low" and (from_high or 0) <= -3 and trend != "up":
            add(30, "自高点明显回落，可作A浪")
        else:
            add(5, "下跌A浪特征弱")
    elif sid == "corr_b":
        if trend in ("down", "side") and kind == "high" and st.get("lower_high"):
            add(44, "下跌后的弱反抽、高点更低，像B浪")
        elif kind == "high" and (from_low or 0) >= 2 and not st.get("higher_high"):
            add(32, "反抽未创新高，偏B浪诱多")
        else:
            add(5, "B浪反抽特征不足")
    elif sid == "corr_c":
        if trend == "down" and kind == "low" and st.get("lower_low") and swing >= 3:
            add(48, "再创新低的下跌段，强契合C浪")
        elif trend == "down" and st.get("lower_low"):
            add(35, "低点下移，偏C浪")
        else:
            add(5, "C浪主跌特征弱")
    elif sid == "imp_dn_w1":
        if trend == "down" and kind == "low" and not st.get("lower_low"):
            add(36, "趋势转弱后的首段下跌")
        elif trend == "down" and kind == "low":
            add(28, "下跌启动段可能")
        else:
            add(5, "下跌第1浪证据弱")
    elif sid == "imp_dn_w2":
        if trend == "down" and kind == "high" and st.get("lower_high"):
            add(40, "下跌中的反抽不过前高，像第2浪")
        else:
            add(8, "下跌反抽结构一般")
    elif sid == "imp_dn_w3":
        if trend == "down" and kind == "low" and st.get("lower_low") and swing >= 4:
            add(50, "主跌加速创新低，强契合下跌第3浪")
        elif trend == "down" and st.get("lower_low"):
            add(34, "低点下移主跌段")
        else:
            add(5, "主跌第3浪特征弱")
    elif sid == "imp_dn_w4":
        if trend == "down" and st.get("contracting"):
            add(40, "下跌中的收敛反抽，像第4浪")
        elif trend == "down" and kind == "high" and (from_low or 0) >= 1.5:
            add(30, "主跌后的反抽整理")
        else:
            add(8, "下跌第4浪特征一般")
    elif sid == "imp_dn_w5":
        if trend == "down" and kind == "low" and st.get("lower_low") and swing < 4:
            add(38, "再创新低但跌幅收敛，像寻底第5浪")
        else:
            add(10, "寻底第5浪证据一般")
    elif sid == "triangle":
        if st.get("contracting"):
            add(48, "高低点波动收敛，三角/平台特征明显")
        elif trend == "side":
            add(30, "均线走平，偏箱体盘整")
        else:
            add(10, "收敛不足")
    elif sid == "complex":
        if trend == "side" and len(pivots) >= 6 and not st.get("higher_high") and not st.get("lower_low"):
            add(36, "方向反复、无清晰五浪，像复合调整")
        elif trend == "side":
            add(24, "震荡市，复合调整备选")
        else:
            add(8, "趋势仍偏单边")

    score = max(0, min(100, score))
    # Soft length prior: very few pivots → compress conviction.
    if len(pivots) < 4:
        score = int(score * 0.75)
        why = why + "（枢轴偏少，降权）"
    return score, why


def _invalidation(sid: str, st: dict[str, Any], last: float) -> str:
    hh = st.get("last_high")
    hl = st.get("last_low")
    ph = st.get("prev_high")
    pl = st.get("prev_low")
    ma20 = st.get("ma20")
    if sid in ("imp_up_w1", "imp_up_w2", "imp_up_w3", "imp_up_w4", "imp_up_w5"):
        floor = hl or pl
        if floor:
            return f"日线收盘跌破 {round(float(floor), 2)}（近端摆动低点）则升浪计数降权/作废"
        if ma20:
            return f"日线收盘跌破 MA20 {ma20} 并转弱，则升浪假设降权"
    if sid in ("corr_a", "corr_c", "imp_dn_w1", "imp_dn_w3", "imp_dn_w5"):
        ceil = hh or ph
        if ceil:
            return f"日线收盘站上 {round(float(ceil), 2)}（近端摆动高点）则偏空浪计数降权"
    if sid in ("corr_b", "imp_dn_w2", "imp_dn_w4"):
        ceil = ph or hh
        if ceil:
            return f"强势突破 {round(float(ceil), 2)} 且站稳，则「反抽浪」改为新推动可能"
        return "收复前高并站稳则反抽浪假设失败"
    if sid in ("triangle", "complex"):
        if hh and hl:
            return (
                f"收盘有效突破上沿 {round(float(hh), 2)} 或跌破下沿 {round(float(hl), 2)} "
                f"后按突破方向重标推动/调整"
            )
    return f"以现价 {round(last, 2)} 近端高低点失守为主要否决"


def _key_levels(st: dict[str, Any], last: float) -> dict[str, Any]:
    return {
        "last": round(last, 2),
        "swing_high": st.get("last_high"),
        "swing_low": st.get("last_low"),
        "prev_high": st.get("prev_high"),
        "prev_low": st.get("prev_low"),
        "ma20": st.get("ma20"),
    }


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


def _timing_summary(
    sid: str,
    ind: dict[str, Any],
    st: dict[str, Any],
) -> dict[str, Any]:
    """Attach MACD/RSI context that soft-confirms or challenges a scenario."""
    macd = ind.get("macd_snap") or {}
    rsi = ind.get("rsi_snap") or {}
    div = ind.get("divergence") or {}
    bias = "neutral"
    if sid.startswith("imp_up") or sid in ("corr_b",):
        bias = "bull"
    elif sid.startswith("imp_dn") or sid in ("corr_a", "corr_c"):
        bias = "bear"
    notes: list[str] = []
    weight = 50
    macd_div = (div.get("macd") or {}).get("kind")
    rsi_div = (div.get("rsi") or {}).get("kind")
    zone = rsi.get("zone")
    if bias == "bull":
        if macd_div == "底背离" or rsi_div == "底背离":
            notes.append("底背离支持偏多浪")
            weight += 18
        if zone == "超卖":
            notes.append("RSI 超卖，利于反抽/第2浪结束假设")
            weight += 10
        if macd.get("cross") == "金叉":
            notes.append("MACD 金叉")
            weight += 8
        if macd_div == "顶背离" or zone == "超买":
            notes.append("顶背离/超买对升浪末段示警")
            weight -= 12
    elif bias == "bear":
        if macd_div == "顶背离" or rsi_div == "顶背离":
            notes.append("顶背离支持偏空浪")
            weight += 18
        if zone == "超买":
            notes.append("RSI 超买，利于回落/B浪结束假设")
            weight += 10
        if macd.get("cross") == "死叉":
            notes.append("MACD 死叉")
            weight += 8
        if macd_div == "底背离" or zone == "超卖":
            notes.append("底背离/超卖对下跌末段示警")
            weight -= 12
    else:
        if zone in ("超买", "超卖"):
            notes.append(f"RSI {zone}，突破方向前先防假突破")
        if macd_div or rsi_div:
            notes.append(div.get("note") or "存在背离，宜等收盘确认")
    weight = max(0, min(100, weight))
    return {
        "macd": macd,
        "rsi": rsi,
        "divergence": div.get("note"),
        "weight": weight,
        "notes": notes or ["指标中性，以结构为主"],
    }


def _scenario_turns(
    sid: str,
    st: dict[str, Any],
    pivots: list[dict[str, Any]],
    series: list[dict[str, Any]],
    last: float,
    ind: dict[str, Any],
) -> list[dict[str, Any]]:
    """Project Fibonacci time windows and price levels for one wave scenario."""
    from market_desk.calendar import add_trading_days

    lp = st.get("last_pivot") or {}
    pp = st.get("prev_pivot") or {}
    anchor_date = str(lp.get("date") or (series[-1].get("date") if series else "") or "")
    if not anchor_date:
        return []
    swing_bars = 8
    if lp.get("index") is not None and pp.get("index") is not None:
        swing_bars = max(3, abs(int(lp["index"]) - int(pp["index"])))
    hh = st.get("last_high") or last
    hl = st.get("last_low") or last
    try:
        hi = float(hh)
        lo = float(hl)
    except (TypeError, ValueError):
        hi, lo = last, last
    span = abs(hi - lo)
    if span <= 0:
        span = abs(last) * 0.02 or 1.0

    # Classic Fib session counts + swing-length multiples.
    fib_days = [5, 8, 13, 21, 34]
    for r in (0.382, 0.618, 1.0, 1.618):
        d = max(3, int(round(swing_bars * r)))
        if d not in fib_days:
            fib_days.append(d)
    fib_days = sorted(set(fib_days))[:7]

    bullish = sid.startswith("imp_up") or sid in ("corr_b",)
    bearish = sid.startswith("imp_dn") or sid in ("corr_a", "corr_c")
    # Price targets depend on whether the next event is a pullback or extension.
    price_ratios = (0.382, 0.5, 0.618, 1.0, 1.272, 1.618)
    levels: list[dict[str, Any]] = []
    for r in price_ratios:
        if bullish:
            # Prefer retracement of last up-swing for w2/w4; extension for w3/w5.
            if sid in ("imp_up_w2", "imp_up_w4", "corr_b", "triangle", "complex"):
                px = hi - span * r
                tag = f"回撤 {r:g}"
            else:
                px = lo + span * r if r <= 1 else hi + span * (r - 1)
                tag = f"延伸 {r:g}" if r > 1 else f"推进 {r:g}"
        elif bearish:
            if sid in ("imp_dn_w2", "imp_dn_w4", "corr_b"):
                px = lo + span * r
                tag = f"反抽 {r:g}"
            else:
                px = hi - span * r if r <= 1 else lo - span * (r - 1)
                tag = f"延伸 {r:g}" if r > 1 else f"下探 {r:g}"
        else:
            px = lo + span * r
            tag = f"箱体 {r:g}"
        levels.append({"ratio": r, "price": round(float(px), 2), "tag": tag})

    timing = _timing_summary(sid, ind, st)
    div_note = (ind.get("divergence") or {}).get("note") or ""
    rsi_zone = ((ind.get("rsi_snap") or {}).get("zone")) or ""
    turns: list[dict[str, Any]] = []
    for i, days in enumerate(fib_days):
        when = add_trading_days(anchor_date, days)
        if when is None:
            continue
        # Pair each time window with the nearest Fib price of matching order.
        lvl = levels[min(i, len(levels) - 1)]
        conf = 40
        conf_notes: list[str] = []
        if days in (8, 13, 21):
            conf += 12
            conf_notes.append("经典斐波那契交易日")
        if timing.get("weight", 50) >= 60:
            conf += 10
            conf_notes.append("MACD/RSI 同向")
        if timing.get("weight", 50) <= 40:
            conf -= 8
            conf_notes.append("指标逆势示警")
        if "背离" in div_note and days <= 13:
            conf += 8
            conf_notes.append(div_note)
        if rsi_zone in ("超买", "超卖") and days <= 8:
            conf += 6
            conf_notes.append(f"RSI {rsi_zone}")
        conf = max(0, min(100, conf))
        turns.append(
            {
                "date": when.isoformat(),
                "bars_ahead": days,
                "fib": f"{days} 个交易日",
                "anchor": anchor_date,
                "price": lvl["price"],
                "price_tag": lvl["tag"],
                "level": round(last, 2),
                "macd_hint": ((ind.get("macd_snap") or {}).get("cross")) or div_note or "—",
                "rsi_hint": (
                    f"RSI {((ind.get('rsi_snap') or {}).get('value'))} · {rsi_zone}"
                    if (ind.get("rsi_snap") or {}).get("value") is not None
                    else rsi_zone or "—"
                ),
                "confidence": conf,
                "note": "；".join(conf_notes) or "纯时间/点位投影",
            }
        )
    turns.sort(key=lambda x: (-int(x.get("confidence") or 0), int(x.get("bars_ahead") or 0)))
    return turns[:5]
