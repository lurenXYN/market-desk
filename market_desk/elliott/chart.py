"""Chart payload and wave-mark placement."""

from __future__ import annotations

from typing import Any

from market_desk.elliott.catalog import _WAVE_MARK_SPEC
from market_desk.elliott.pivots import _alternating_chain


def _build_elliott_chart(
    series: list[dict[str, Any]],
    pivots: list[dict[str, Any]],
    fine_pivots: list[dict[str, Any]],
    last: float,
    *,
    primary: dict[str, Any] | None,
    scenarios: list[dict[str, Any]] | None = None,
    window: int = 120,
) -> dict[str, Any]:
    """Pack closes + zigzag pivots for the observe-only Elliott SVG board.

    ``window`` keeps the most recent bars so the line stays readable. Pivot
    indices are remapped into that window (points before the window are dropped).
    Wave numbers prefer Top1; if Top1 is triangle/complex (hard to number), fall
    back to the first Top5 impulse/ABC scenario that maps cleanly.
    """
    win = max(40, min(int(window or 120), 240))
    slice_bars = series[-win:] if len(series) > win else list(series)
    offset = max(0, len(series) - len(slice_bars))
    closes: list[float] = []
    dates: list[str] = []
    for b in slice_bars:
        try:
            closes.append(round(float(b["close"]), 2))
        except (TypeError, ValueError, KeyError):
            continue
        dates.append(str(b.get("date") or "")[:10])

    def _pack_pivots(src: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in src or []:
            try:
                idx = int(p.get("index"))
                px = float(p.get("price"))
            except (TypeError, ValueError):
                continue
            local = idx - offset
            if local < 0 or local >= len(closes):
                continue
            out.append(
                {
                    "i": local,
                    "price": round(px, 2),
                    "kind": p.get("kind") or "",
                    "date": str(p.get("date") or "")[:10],
                }
            )
        if len(out) > limit:
            out = out[-limit:]
        return out

    coarse = _pack_pivots(pivots, 14)
    fine = _pack_pivots(fine_pivots, 18)
    # If coarse zigzag is too sparse in-window, draw fine pivots as the main zig.
    draw_pivots = coarse if len(coarse) >= 4 else (fine if len(fine) >= 4 else coarse)

    inv_px = None
    levels_out: list[dict[str, Any]] = []
    if primary:
        for lv in primary.get("levels") or []:
            try:
                px = float(lv.get("price"))
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            levels_out.append(
                {
                    "price": round(px, 2),
                    "tag": str(lv.get("tag") or lv.get("role") or ""),
                    "vs_last_pct": lv.get("vs_last_pct"),
                }
            )
        inv_txt = str(primary.get("invalidation") or "")
        digits: list[str] = []
        cur = ""
        for ch in inv_txt:
            if ch.isdigit() or ch == ".":
                cur += ch
            elif cur:
                digits.append(cur)
                cur = ""
        if cur:
            digits.append(cur)
        for raw in digits:
            try:
                v = float(raw)
            except ValueError:
                continue
            if 100 < v < 20000:
                inv_px = round(v, 2)
                break

    sub_pri = ((primary or {}).get("subwaves") or {}).get("primary") or {}
    label_src, wave_marks, wave_marks_note = _pick_wave_mark_source(
        primary,
        scenarios,
        pivots=pivots,
        fine_pivots=fine_pivots,
        last=last,
        offset=offset,
        n_closes=len(closes),
    )
    scenario_marks: dict[str, Any] = {}
    for row in scenarios or []:
        sid = str(row.get("id") or "")
        if not sid or sid not in _WAVE_MARK_SPEC:
            continue
        marks: list[dict[str, Any]] = []
        note = ""
        for src in (pivots, fine_pivots):
            marks, note = _build_wave_marks(
                sid,
                src,
                last,
                offset=offset,
                n_closes=len(closes),
                title=str(row.get("title") or ""),
                wave=str(row.get("wave") or ""),
            )
            if len(marks) >= 2:
                break
        if marks:
            scenario_marks[sid] = {
                "marks": marks,
                "note": note,
                "title": row.get("title") or "",
                "wave": row.get("wave") or "",
            }
    return {
        "closes": closes,
        "dates": dates,
        "pivots": draw_pivots,
        "fine_pivots": fine if draw_pivots is not fine else [],
        "last": round(float(last), 2) if last else None,
        "invalidation": inv_px,
        "levels": levels_out[:4],
        "primary_title": (primary or {}).get("title") or "",
        "primary_wave": (primary or {}).get("wave") or "",
        "subwave_label": sub_pri.get("label") or "",
        "wave_marks": wave_marks,
        "wave_marks_note": wave_marks_note,
        "wave_marks_from": (label_src or {}).get("title") or "",
        "wave_marks_id": (label_src or {}).get("id") or "",
        "scenario_marks": scenario_marks,
    }


def _pick_wave_mark_source(
    primary: dict[str, Any] | None,
    scenarios: list[dict[str, Any]] | None,
    *,
    pivots: list[dict[str, Any]],
    fine_pivots: list[dict[str, Any]],
    last: float,
    offset: int,
    n_closes: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """Choose which scenario's count to paint; skip triangle/complex when possible."""
    soft = {"triangle", "complex"}
    ordered: list[dict[str, Any]] = []
    if primary:
        ordered.append(primary)
    for row in scenarios or []:
        if row is primary:
            continue
        ordered.append(row)

    # Prefer countable impulse/ABC first when Top1 is soft-structure.
    if primary and str(primary.get("id") or "") in soft:
        countable = [
            r
            for r in ordered
            if str(r.get("id") or "") not in soft and str(r.get("id") or "") in _WAVE_MARK_SPEC
        ]
        ordered = countable + [r for r in ordered if r not in countable]

    for row in ordered:
        sid = str(row.get("id") or "")
        if sid not in _WAVE_MARK_SPEC:
            continue
        for src in (pivots, fine_pivots):
            marks, note = _build_wave_marks(
                sid,
                src,
                last,
                offset=offset,
                n_closes=n_closes,
                title=str(row.get("title") or ""),
                wave=str(row.get("wave") or ""),
            )
            if len(marks) >= 2:
                if primary and row is not primary:
                    note = (
                        f"Top1 是盘整难标号，图上数字改按「{row.get('title') or sid}」标注"
                        f"（契合 {row.get('fit')}；仍是多解示意）。"
                    )
                return row, marks, note
    if primary:
        marks, note = _build_wave_marks(
            str(primary.get("id") or ""),
            pivots,
            last,
            offset=offset,
            n_closes=n_closes,
            title=str(primary.get("title") or ""),
            wave=str(primary.get("wave") or ""),
        )
        return primary, marks, note
    return None, [], "暂无浪序标注。"


def _build_wave_marks(
    scenario_id: str,
    pivots: list[dict[str, Any]],
    last: float,
    *,
    offset: int,
    n_closes: int,
    title: str = "",
    wave: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """Map Top1 scenario onto zigzag pivots as readable wave-number marks.

    Each mark sits at the end of a wave (pivot) or at the last bar when the
    current wave is still in progress. Observe-only; not a unique Elliott count.
    """
    spec = _WAVE_MARK_SPEC.get(scenario_id)
    if not spec:
        return [], "当前主情景是复合/盘整，折线不强制标 1-2-3。"
    n = int(spec["n"])
    labels = list(spec["labels"])
    mode = str(spec["mode"])
    if mode == "impulse":
        start_kind = "low" if spec.get("bull") else "high"
    elif mode == "abc":
        start_kind = "high"
    else:
        start_kind = "high"

    # Prefer full chain (start + end of each wave). Else start + ends of prior
    # waves only — pin the current label on the latest close.
    full = _alternating_chain(pivots, n + 1, start_kind)
    partial = _alternating_chain(pivots, n, start_kind) if not full else []
    chain = full or partial
    if len(chain) < 2:
        return [], "枢轴不够，暂时标不出完整浪序；请看下方卡片标题。"

    marks: list[dict[str, Any]] = []
    for wi, lab in enumerate(labels):
        end_pos = wi + 1  # index in chain
        is_current = wi == n - 1
        if end_pos < len(chain):
            p = chain[end_pos]
            try:
                local_i = int(p["index"]) - offset
                px = float(p["price"])
            except (TypeError, ValueError, KeyError):
                continue
            if local_i < 0 or local_i >= n_closes:
                continue
            marks.append(
                {
                    "i": local_i,
                    "price": round(px, 2),
                    "label": lab,
                    "current": is_current,
                    "date": str(p.get("date") or "")[:10],
                }
            )
        elif is_current and n_closes > 0:
            marks.append(
                {
                    "i": n_closes - 1,
                    "price": round(float(last), 2) if last else None,
                    "label": lab,
                    "current": True,
                    "date": "",
                    "in_progress": True,
                }
            )

    if not marks:
        return [], "未能把主情景对齐到折线拐点。"
    head = title or wave or scenario_id
    note = (
        f"图上大号数字/字母 = 按 Top1「{head}」标的浪序"
        f"（多解示意；加粗带圈的是「现在更像」的这一浪）。"
    )
    return marks, note
