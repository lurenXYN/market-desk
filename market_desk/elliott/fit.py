"""Scenario and sub-wave fit scoring, invalidation, and key levels."""

from __future__ import annotations

from typing import Any

from market_desk.elliott.catalog import _SUB_ABC, _SUB_IMPULSE_DN, _SUB_IMPULSE_UP


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


def _subwave_catalog(parent_id: str) -> tuple[dict[str, Any], ...] | None:
    """Pick the one-layer internal catalog for a parent scenario id."""
    sid = str(parent_id or "")
    if sid.startswith("imp_up_"):
        return _SUB_IMPULSE_UP
    if sid.startswith("imp_dn_"):
        return _SUB_IMPULSE_DN
    if sid in ("corr_a", "corr_c"):
        return _SUB_IMPULSE_DN
    if sid == "corr_b":
        return _SUB_ABC
    return None


def _score_sub_fit(
    catalog_id: str,
    mode: str,
    st: dict[str, Any],
) -> tuple[int, str]:
    """Score a minor-degree subwave label against fine swing structure."""
    trend = st.get("trend")
    lp = st.get("last_pivot") or {}
    kind = lp.get("kind")
    swing = abs(float(st.get("swing_pct") or 0))
    from_low = st.get("from_last_low_pct")
    from_high = st.get("from_last_high_pct")
    score = 18
    why = "细级结构一般"

    def add(n: int, note: str) -> None:
        nonlocal score, why
        score += n
        why = note

    if mode == "up":
        if catalog_id == "sub_i":
            if kind == "high" and (from_low or 0) >= 1.2 and swing >= 1.2:
                add(40, "细级自低点上冲，像子浪 i")
            elif kind == "high" and (from_low or 0) >= 0.8:
                add(24, "弱上冲，可作子浪 i")
            else:
                add(0, "缺少细级启动上冲")
        elif catalog_id == "sub_ii":
            if kind == "low" and (from_high or 0) <= -1.0:
                add(38, "细级高位回撤，像子浪 ii")
            elif kind == "low" and (from_high or 0) <= -0.6:
                add(24, "浅回撤，偏子浪 ii")
            else:
                add(0, "不是细级回撤低点")
        elif catalog_id == "sub_iii":
            if kind == "high" and st.get("higher_high") and swing >= 1.8:
                add(46, "细级创新高加速，像子浪 iii")
            elif kind == "high" and st.get("higher_high"):
                add(32, "细级抬高高点，偏 iii")
            else:
                add(0, "未见细级主升")
        elif catalog_id == "sub_iv":
            if st.get("contracting"):
                add(40, "细级波动收敛，像子浪 iv")
            elif kind == "low" and (from_high or 0) <= -0.8 and st.get("higher_low"):
                add(34, "上升中的浅回撤整理，偏 iv")
            else:
                add(4, "细级整理不足")
        elif catalog_id == "sub_v":
            if kind == "high" and st.get("higher_high") and swing < 2.2:
                add(36, "创新高但摆动偏弱，像子浪 v")
            elif kind == "high" and st.get("higher_high"):
                add(26, "末段上冲嫌疑")
            else:
                add(6, "细级末升证据一般")
    elif mode == "down":
        if catalog_id == "sub_i":
            if kind == "low" and (from_high or 0) <= -1.2 and swing >= 1.2:
                add(40, "细级自高点下挫，像子浪 i")
            elif kind == "low" and (from_high or 0) <= -0.8:
                add(24, "弱下跌启动，可作子浪 i")
            else:
                add(0, "缺少细级下跌启动")
        elif catalog_id == "sub_ii":
            if kind == "high" and st.get("lower_high") and (from_low or 0) >= 1.0:
                add(38, "细级反抽不过前高，像子浪 ii")
            elif kind == "high" and (from_low or 0) >= 0.6:
                add(24, "下跌中反抽，偏 ii")
            else:
                add(0, "不是细级反抽高点")
        elif catalog_id == "sub_iii":
            if kind == "low" and st.get("lower_low") and swing >= 1.8:
                add(46, "细级创新低加速，像子浪 iii")
            elif kind == "low" and st.get("lower_low"):
                add(32, "细级低点下移，偏 iii")
            else:
                add(0, "未见细级主跌")
        elif catalog_id == "sub_iv":
            if st.get("contracting") and trend == "down":
                add(40, "下跌中收敛反抽，像子浪 iv")
            elif kind == "high" and (from_low or 0) >= 0.8:
                add(30, "主跌后反抽整理，偏 iv")
            else:
                add(4, "细级反抽整理不足")
        elif catalog_id == "sub_v":
            if kind == "low" and st.get("lower_low") and swing < 2.2:
                add(36, "再创新低但跌幅收敛，像子浪 v")
            elif kind == "low" and st.get("lower_low"):
                add(26, "寻底段嫌疑")
            else:
                add(6, "细级寻底证据一般")
    else:  # abc
        if catalog_id == "sub_a":
            if kind == "low" and (from_high or 0) <= -1.2:
                add(40, "细级自高回落，像子浪 a")
            elif kind == "high" and (from_low or 0) >= 1.2 and not st.get("higher_high"):
                add(28, "细级第一段上冲未创新高，可作 a（平台）")
            else:
                add(4, "细级 a 段特征弱")
        elif catalog_id == "sub_b":
            if kind == "high" and st.get("lower_high"):
                add(42, "反抽高点更低，像子浪 b")
            elif kind == "low" and st.get("higher_low") and not st.get("lower_low"):
                add(30, "回撤低点抬高，偏 b（锯齿）")
            else:
                add(4, "细级 b 段特征弱")
        elif catalog_id == "sub_c":
            if kind == "low" and st.get("lower_low") and swing >= 1.5:
                add(44, "再创新低的调整主段，像子浪 c")
            elif kind == "high" and st.get("higher_high") and swing >= 1.5:
                add(36, "再创新高的调整主段，像子浪 c（向上调整）")
            else:
                add(6, "细级 c 段特征一般")

    # Soft prior: sideways tape compresses conviction.
    if trend == "side":
        score = max(10, score - 6)
    score = max(0, min(100, int(score)))
    return score, why


def _subwave_invalidation(
    catalog_id: str,
    mode: str,
    st: dict[str, Any],
    last: float,
) -> str:
    """Build a short invalidation hint for the subwave draft."""
    hl = st.get("last_low")
    hh = st.get("last_high")
    pl = st.get("prev_low")
    ph = st.get("prev_high")
    if mode == "up":
        if catalog_id in ("sub_i", "sub_iii", "sub_v"):
            floor = pl if pl is not None else hl
            if floor is not None:
                return f"收盘跌破 {round(float(floor), 2)}（近端细级低点）则本子浪草稿降权"
        if catalog_id in ("sub_ii", "sub_iv") and hl is not None:
            return f"继续跌破 {round(float(hl), 2)} 并失守抬高结构则改标"
    if mode == "down":
        if catalog_id in ("sub_i", "sub_iii", "sub_v"):
            ceil = ph if ph is not None else hh
            if ceil is not None:
                return f"收盘站上 {round(float(ceil), 2)}（近端细级高点）则本子浪草稿降权"
        if catalog_id in ("sub_ii", "sub_iv") and hh is not None:
            return f"反抽站稳 {round(float(hh), 2)} 上方则下跌子浪计数降权"
    if mode == "abc":
        if catalog_id == "sub_a" and hh is not None:
            return f"强势收复并站稳 {round(float(hh), 2)} 则 a 浪草稿可疑"
        if catalog_id == "sub_b" and hh is not None:
            return f"突破前高 {round(float(hh), 2)} 并站稳则改标推动嫌疑"
        if catalog_id == "sub_c" and hl is not None:
            return f"跌破 {round(float(hl), 2)} 后若加速，警惕演化为推动"
    return f"相对现价 {round(float(last), 2)} 的细级结构被破坏则降权"


def _subwave_draft(
    parent_id: str,
    fine_st: dict[str, Any],
    fine_pivots: list[dict[str, Any]],
    last: float,
) -> dict[str, Any]:
    """Build a one-layer internal subwave draft for a parent scenario.

    Uses a finer zigzag than the parent board. Observe-only; never feeds desk
    buy/sell gates. Skips triangle / complex parents.
    """
    catalog = _subwave_catalog(parent_id)
    if not catalog:
        return {
            "ok": False,
            "skipped": True,
            "note": "盘整/复合浪内部不强制数子浪",
            "primary": None,
            "candidates": [],
            "fine_pivot_n": len(fine_pivots or []),
        }
    if len(fine_pivots or []) < 4:
        return {
            "ok": False,
            "skipped": False,
            "note": "细级枢轴不足，暂不展开子浪",
            "primary": None,
            "candidates": [],
            "fine_pivot_n": len(fine_pivots or []),
        }

    if str(parent_id).startswith("imp_up_"):
        mode = "up"
    elif str(parent_id).startswith("imp_dn_") or parent_id in ("corr_a", "corr_c"):
        mode = "down"
    else:
        mode = "abc"

    scored: list[dict[str, Any]] = []
    for tpl in catalog:
        fit, why = _score_sub_fit(str(tpl["id"]), mode, fine_st)
        scored.append(
            {
                "id": tpl["id"],
                "label": tpl["label"],
                "roman": tpl.get("roman") or "",
                "fit": fit,
                "fit_note": why,
                "path": tpl.get("path") or "",
                "invalidation": _subwave_invalidation(str(tpl["id"]), mode, fine_st, last),
                "watch": tpl.get("risk") or "",
            }
        )
    scored.sort(key=lambda x: (-int(x.get("fit") or 0), str(x.get("id") or "")))
    primary = scored[0] if scored else None
    # Keep primary + next two alternatives for the collapsed list.
    alts = scored[1:3]
    return {
        "ok": True,
        "skipped": False,
        "degree": "minor",
        "mode": mode,
        "note": "内部结构草稿 · 多解 · 仅观察，不改买卖结论",
        "fine_pivot_n": len(fine_pivots or []),
        "primary": primary,
        "candidates": ([primary] + alts) if primary else [],
        "alts": alts,
    }
