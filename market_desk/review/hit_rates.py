"""Outcome comparisons, missed buys, hit-rate tables, and tune hints."""

from __future__ import annotations

from typing import Any
from market_desk.filters import normalize_code
from market_desk.settings import setting

from market_desk.review.signals import is_buy_signal
from market_desk.review.outcome import BUY_HIT_LABELS, score_signal_with_closes
from market_desk.review.exec_score import classify_miss_kind, MISS_KIND_LABELS


MIN_HIT_N = 8


def build_outcome_compare(
    rows: list[dict[str, Any]],
    closes_map: dict[str, Any] | None = None,
    *,
    hit_mode: str | None = None,
) -> dict[str, Any]:
    """Compare classic / same_day_plan / filled hit-rates on the same buy set.

    When ``closes_map`` is provided, non-classic standards are rescored in memory
    (display-only). Classic prefers stored labels, falling back to rescoring.
    Rows that cannot score under a standard are skipped. ``low_n`` marks n<8.
    """
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    if mode not in ("traded", "all"):
        mode = "traded"
    buys = [
        r
        for r in rows
        if is_buy_signal(r.get("signal_type")) and not int(r.get("skipped") or 0)
    ]
    if mode == "traded":
        buys = [r for r in buys if int(r.get("traded") or 0)]
    standards = ("classic", "same_day_plan", "filled")
    labels = {
        "classic": "现行·隔日",
        "same_day_plan": "当日plan·隔日",
        "filled": "实盘成交·隔日",
    }
    skip_labs = {"无成交", "当日未触达", "未触达", "无日线", "样本不足", "待隔日"}
    packed = closes_map or {}

    def _score_one(raw: dict[str, Any], std: str) -> str | None:
        if std == "classic" and raw.get("outcome_label"):
            lab = str(raw.get("outcome_label") or "")
            return lab if lab and lab not in skip_labs else None
        code = normalize_code(raw.get("code"))
        blob = packed.get(code) if code else None
        if not blob:
            if std == "classic" and raw.get("outcome_label"):
                lab = str(raw.get("outcome_label") or "")
                return lab if lab and lab not in skip_labs else None
            return None
        if len(blob) >= 3:
            dates, closes, ohlc = blob[0], blob[1], blob[2] or {}
        else:
            dates, closes = blob[0], blob[1]
            ohlc = {}
        scored = score_signal_with_closes(
            raw,
            closes,
            dates,
            opens=list(ohlc.get("open") or []) or None,
            lows=list(ohlc.get("low") or []) or None,
            highs=list(ohlc.get("high") or []) or None,
            standard=std,
        )
        if not scored:
            return None
        lab = str(scored.get("outcome_label") or "")
        if not lab or lab in skip_labs:
            return None
        return lab

    out_rows: list[dict[str, Any]] = []
    for std in standards:
        scored_n = 0
        hit_n = 0
        for raw in buys:
            lab = _score_one(raw, std)
            if not lab:
                continue
            scored_n += 1
            if lab in BUY_HIT_LABELS:
                hit_n += 1
        rate = round(100.0 * hit_n / scored_n, 1) if scored_n else None
        out_rows.append(
            {
                "standard": std,
                "label": labels[std],
                "scored_n": scored_n,
                "hit_n": hit_n,
                "hit_rate": rate,
                "low_n": scored_n < MIN_HIT_N,
            }
        )
    return {
        "ok": True,
        "hit_mode": mode,
        "rows": out_rows,
        "note": "同批信号三套评测并排；n<8 标低样本，宜灰显。",
    }


def attach_low_n_flags(items: list[dict[str, Any]] | None, *, n_key: str = "scored_n") -> list[dict[str, Any]]:
    """Mark hit-rate rows with low sample size for UI gray-out."""
    out: list[dict[str, Any]] = []
    for raw in items or []:
        row = dict(raw)
        try:
            n = int(row.get(n_key) or row.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            for alt in ("scored", "total", "count", "buy_n"):
                try:
                    n = int(row.get(alt) or 0)
                except (TypeError, ValueError):
                    n = 0
                if n > 0:
                    break
        row["low_n"] = n < MIN_HIT_N
        row["n"] = n
        out.append(row)
    return out


def build_missed_buys(rows: list[dict[str, Any]], *, trade_date: str) -> list[dict[str, Any]]:
    """List same-day buys that were skipped or never traded while price already ran."""
    from market_desk.adapt import context_from_row

    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("trade_date") or "") != trade_date:
            continue
        if str(row.get("signal_type") or "") != "buy":
            continue
        skipped = int(row.get("skipped") or 0)
        traded = int(row.get("traded") or 0)
        flags = row.get("price_flags") or []
        miss = "miss_pullback" in flags or "未回踩" in str(row.get("price_mark") or "")
        if not ((skipped and miss) or (not traded and not skipped and miss)):
            continue
        ctx = context_from_row(row)
        out.append(
            {
                "id": row.get("id"),
                "code": row.get("code"),
                "name": row.get("name"),
                "price": row.get("price"),
                "live_last": row.get("live_last"),
                "dev_pct": row.get("dev_pct"),
                "skipped": skipped,
                "traded": traded,
                "price_mark": row.get("price_mark"),
                "mainline": row.get("mainline"),
                "signaled_at": row.get("signaled_at"),
                "context": ctx,
                "context_label": ctx.get("label"),
                "miss_kind": "never_touched",
                "miss_kind_label": MISS_KIND_LABELS["never_touched"],
            }
        )
    return out


def build_miss_attribution(
    rows: list[dict[str, Any]],
    *,
    trade_date: str,
) -> dict[str, Any]:
    """Bucket untraded same-day buys by miss reason for review display.

    Keeps adapt's ``missed_buys`` (never_touched only) unchanged; this board is
    broader and display-only.
    """
    from market_desk.adapt import context_from_row

    buckets: dict[str, list[dict[str, Any]]] = {
        "never_touched": [],
        "touched_not_bought": [],
        "gate_blocked": [],
    }
    for row in rows:
        if str(row.get("trade_date") or "") != trade_date:
            continue
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("traded") or 0):
            continue
        kind = classify_miss_kind(row)
        if not kind or kind not in buckets:
            continue
        ctx = context_from_row(row)
        buckets[kind].append(
            {
                "id": row.get("id"),
                "code": row.get("code"),
                "name": row.get("name"),
                "price": row.get("price"),
                "live_last": row.get("live_last"),
                "dev_pct": row.get("dev_pct"),
                "skipped": int(row.get("skipped") or 0),
                "price_mark": row.get("price_mark"),
                "mainline": row.get("mainline"),
                "signaled_at": row.get("signaled_at"),
                "context_label": ctx.get("label"),
                "miss_kind": kind,
                "miss_kind_label": MISS_KIND_LABELS.get(kind, kind),
            }
        )
    counts = {k: len(v) for k, v in buckets.items()}
    items: list[dict[str, Any]] = []
    for key in ("never_touched", "touched_not_bought", "gate_blocked"):
        items.extend(buckets[key])
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "labels": dict(MISS_KIND_LABELS),
        "items": items,
    }


def build_desk_source_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by desk source (main / side / link / watch_trial)."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    label_map = {
        "main": "主线",
        "side": "支线回踩",
        "link": "联动回踩",
        "watch_trial": "自选试探",
        "independent_pop": "独立人气",
        "dragon": "龙头",
    }
    type_to_src = {
        "buy": "main",
        "buy_side": "side",
        "buy_link": "link",
        "buy_trial": "watch_trial",
        "buy_indep": "independent_pop",
        "buy_dragon": "dragon",
    }
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in label_map}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        src = str(row.get("desk_source") or "").strip()
        if not src:
            src = type_to_src.get(str(row.get("signal_type") or ""), "main")
        if src in ("emotion_dragon", "mid_army_dragon"):
            src = "dragon"
        if src not in buckets:
            src = "main"
        buckets[src].append(row)
    out: list[dict[str, Any]] = []
    for key in ("main", "dragon", "side", "link", "watch_trial", "independent_pop"):
        items = buckets[key]
        if not items:
            continue
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "source": key,
                "label": label_map[key],
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
    return out


def build_theme_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by signal mainline / theme label."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        theme = str(row.get("mainline") or "").strip() or "未标主线"
        buckets.setdefault(theme, []).append(row)
    out: list[dict[str, Any]] = []
    for theme, items in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "theme": theme,
                "label": theme,
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
        if len(out) >= max(3, int(limit or 8)):
            break
    return out


def build_phase_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by market phase label on the signal row."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        phase = str(row.get("phase") or "未标").strip() or "未标"
        buckets.setdefault(phase, []).append(row)
    out: list[dict[str, Any]] = []
    for phase, items in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "phase": phase,
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1) if items else None,
            }
        )
    return out


def build_kind_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by instrument kind (etf / stock)."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[str, list[dict[str, Any]]] = {"etf": [], "stock": []}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        kind = "etf" if str(row.get("kind") or "") == "etf" else "stock"
        buckets[kind].append(row)
    out: list[dict[str, Any]] = []
    for kind in ("etf", "stock"):
        items = buckets[kind]
        if not items:
            continue
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "kind": kind,
                "label": "ETF" if kind == "etf" else "个股",
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
    return out


def build_phase_kind_hit_rates(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate buy hit-rate by phase × kind for the review strip."""
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        phase = str(row.get("phase") or "未标").strip() or "未标"
        kind = "etf" if str(row.get("kind") or "") == "etf" else "stock"
        buckets.setdefault((phase, kind), []).append(row)
    out: list[dict[str, Any]] = []
    for (phase, kind), items in sorted(
        buckets.items(), key=lambda x: (-len(x[1]), x[0][0], x[0][1])
    ):
        hit = sum(1 for r in items if (r.get("outcome_label") or "") in BUY_HIT_LABELS)
        out.append(
            {
                "phase": phase,
                "kind": kind,
                "label": "ETF" if kind == "etf" else "个股",
                "scored_n": len(items),
                "hit_n": hit,
                "hit_rate": round(100.0 * hit / len(items), 1),
            }
        )
    return out


def build_tune_hints(
    *,
    missed: list[dict[str, Any]] | None,
    phase_hits: list[dict[str, Any]] | None,
    kind_hits: list[dict[str, Any]] | None,
    gate_kills: list[dict[str, Any]] | None = None,
    sell_bias: dict[str, Any] | None = None,
    current_context: dict[str, Any] | None = None,
    theme_hits: list[dict[str, Any]] | None = None,
    phase_kind_hits: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return short threshold-tuning hints from review buckets (not orders)."""
    from market_desk.adapt import count_missed_by_context
    from market_desk.config import ADAPT_MISSED_MIN_N, ADAPT_TUNE_CLAMP

    hints: list[str] = []
    missed_n = len(missed or [])
    by_ctx = count_missed_by_context(missed)
    clamp_pct = int(float(ADAPT_TUNE_CLAMP) * 100)
    clamp_frac = float(ADAPT_TUNE_CLAMP)
    ctx = current_context or {}
    if ctx.get("tagged") and ctx.get("key"):
        bucket_n = int(by_ctx.get(str(ctx["key"])) or 0)
        if bucket_n >= int(ADAPT_MISSED_MIN_N):
            loosen = max(1, int(round(clamp_frac * 50)))
            hints.append(
                f"{ctx.get('label')}漏买 {bucket_n} 笔：建议回踩门槛放宽约 {loosen}%"
                f"（auto_tune 夹紧±{clamp_pct}%·不串桶）"
            )
        elif missed_n >= 3:
            top = sorted(by_ctx.items(), key=lambda kv: -kv[1])[:2]
            extra = "、".join(f"{k}×{v}" for k, v in top) if top else "他桶不足"
            hints.append(
                f"漏买合计 {missed_n}，当前桶 {ctx.get('label')} 仅 {bucket_n}："
                f"不调参（{extra}）"
            )
    elif missed_n >= 3:
        hints.append(
            f"漏买 {missed_n} 笔缺情景标签：只提示不调参"
            f"（需时段×波动；夹紧±{clamp_pct}%）"
        )
    for row in kind_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        if rate is None or n < 5:
            continue
        label = row.get("label") or row.get("kind")
        if float(rate) < 35:
            hints.append(f"{label}命中 {rate}%（n={n}）偏低：该品种宜更小仓或更严 ready")
        elif float(rate) >= 55 and n >= 8:
            hints.append(f"{label}命中 {rate}%（n={n}）尚可：可维持当前回撤门槛")
    for row in phase_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        phase = str(row.get("phase") or "")
        if rate is None or n < 5:
            continue
        if phase == "高潮" and float(rate) < 40:
            hints.append(f"高潮相位命中 {rate}%：继续默认降观察回踩，勿追尖")
        if phase == "恐慌" and float(rate) < 30:
            hints.append(f"恐慌相位命中 {rate}%：维持禁开仓")
    for row in theme_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        if rate is None or n < 5:
            continue
        label = row.get("label") or row.get("theme") or "题材"
        if float(rate) < 35:
            hints.append(f"题材「{label}」命中 {rate}%（n={n}）偏低：该主题宜更小仓")
        elif float(rate) >= 55 and n >= 8:
            hints.append(f"题材「{label}」命中 {rate}%（n={n}）尚可：可维持当前门槛")
    for row in phase_kind_hits or []:
        rate = row.get("hit_rate")
        n = int(row.get("scored_n") or 0)
        if rate is None or n < 5:
            continue
        phase = str(row.get("phase") or "")
        label = row.get("label") or row.get("kind") or ""
        if float(rate) < 32:
            hints.append(
                f"{phase}·{label}命中 {rate}%（n={n}）偏低：该交叉情景宜更严 ready / 更小仓"
            )
    for row in (gate_kills or [])[:3]:
        fk = int(row.get("false_kill_n") or 0)
        kn = int(row.get("kill_n") or 0)
        gate = row.get("gate") or ""
        if fk >= 3 and kn >= 5:
            hints.append(
                f"闸门「{gate}」误杀偏多（假杀 {fk}/{kn}）：已自动略放宽该确认门槛"
                f"（夹紧±{clamp_pct}%）"
            )
    sb = sell_bias or {}
    if sb.get("widen") and int(sb.get("n") or 0) >= 6:
        hints.append(
            f"卖点偏早（卖后回落命中 {sb.get('hit_rate')}%，n={sb.get('n')}）："
            f"已自动放宽回撤/落袋阈值"
        )
    elif sb.get("tighten") and int(sb.get("n") or 0) >= 6:
        hints.append(
            f"卖点偏准（卖后回落命中 {sb.get('hit_rate')}%，n={sb.get('n')}）："
            f"已略收紧止盈回撤"
        )
    return hints[:8]


def _gate_bucket(flag: str) -> str:
    """Map a confirm_fail string to a stable attribution bucket."""
    text = str(flag or "").strip()
    if not text:
        return "其它"
    if text.startswith("日线") or "日线" in text:
        return "日线"
    if text.startswith("分时") or "分时" in text:
        return "分时"
    if "薄确认" in text or "跨板块" in text:
        return "薄确认/共振"
    if "总仓" in text or "相位上限" in text:
        return "总仓上限"
    if "离日高" in text:
        return "离日高"
    if "弱于" in text:
        return "相对强弱"
    if "量能" in text:
        return "ETF量能"
    if "指数" in text:
        return "指数弱"
    return text[:12]


def build_gate_kill_stats(
    rows: list[dict[str, Any]],
    *,
    hit_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Attribute ready kills to confirm_fail history; flag false kills via outcomes.

    Kill counts use confirm_fail_hist (same-day gate evolution). False/true kill
    only when the final same-day state stayed ready=0.
    """
    del hit_mode  # Paper outcomes OK for gated cards; final ready state still required.
    kills: dict[str, int] = {}
    false_kills: dict[str, int] = {}
    true_kills: dict[str, int] = {}
    for row in rows or []:
        if not is_buy_signal(row.get("signal_type")):
            continue
        if int(row.get("skipped") or 0):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        fails = [
            str(x)
            for x in (
                payload.get("confirm_fail_hist")
                or payload.get("final_fail")
                or payload.get("confirm_fail")
                or []
            )
            if str(x).strip()
        ]
        if not fails:
            continue
        label = str(row.get("outcome_label") or "")
        ready_now = int(row.get("ready") or 0)
        buckets = {_gate_bucket(f) for f in fails}
        for bucket in buckets:
            kills[bucket] = kills.get(bucket, 0) + 1
            if ready_now or not label:
                continue
            if label in BUY_HIT_LABELS:
                false_kills[bucket] = false_kills.get(bucket, 0) + 1
            elif label in {"次日绿", "三日绿"}:
                true_kills[bucket] = true_kills.get(bucket, 0) + 1
    out: list[dict[str, Any]] = []
    for gate, kn in sorted(kills.items(), key=lambda x: (-x[1], x[0])):
        fk = int(false_kills.get(gate) or 0)
        tk = int(true_kills.get(gate) or 0)
        scored_n = fk + tk
        false_rate = round(100.0 * fk / scored_n, 1) if scored_n else None
        out.append(
            {
                "gate": gate,
                "kill_n": kn,
                "false_kill_n": fk,
                "true_kill_n": tk,
                "false_kill_rate": false_rate,
                "note": (
                    "误杀偏多" if fk >= 3 and (false_rate or 0) >= 50 else
                    ("挡得住" if tk >= 3 and fk == 0 else "")
                ),
            }
        )
    return out[:8]
