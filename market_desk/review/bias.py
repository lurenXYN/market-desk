"""Sell review bias, fly board, and cached buy-gate bias."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from market_desk.db import load_signals
from market_desk.settings import setting

from market_desk.review.hit_rates import build_gate_kill_stats
from market_desk.review.signals import sell_atr_bucket, sell_rule_of


def build_sell_review_bias(
    rows: list[dict[str, Any]] | None = None,
    *,
    hit_mode: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Derive sell-band widen/tighten from historical sell outcomes.

    ``kind`` = etf | stock filters the sample. Low 卖后回落 hit-rate → widen;
    high hit-rate → slightly tighten take-profit pullback.
    """
    from market_desk.config import (
        SELL_REVIEW_KIND_MIN_N,
        SELL_REVIEW_MIN_N,
        SELL_REVIEW_TIGHTEN_ABOVE,
        SELL_REVIEW_TIGHTEN_MULT,
        SELL_REVIEW_WIDEN_BELOW,
        SELL_REVIEW_WIDEN_MULT,
    )

    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    kind_f = str(kind or "").strip().lower() or None
    try:
        src = rows if rows is not None else load_signals(limit=240)
    except Exception:
        src = []
    sells = [
        r for r in src
        if str(r.get("signal_type") or "") == "sell"
        and not int(r.get("skipped") or 0)
        and r.get("outcome_label")
    ]
    if mode == "traded":
        sells = [r for r in sells if int(r.get("traded") or 0)]
    if kind_f in ("etf", "stock"):
        sells = [r for r in sells if str(r.get("kind") or "stock") == kind_f]
    n = len(sells)
    label = {"etf": "ETF", "stock": "个股"}.get(kind_f or "", "合计")
    min_n = int(SELL_REVIEW_KIND_MIN_N) if kind_f in ("etf", "stock") else int(SELL_REVIEW_MIN_N)
    if n < min_n:
        return {
            "ok": False,
            "kind": kind_f,
            "n": n,
            "hit_rate": None,
            "widen": False,
            "tighten": False,
            "mult": 1.0,
            "note": f"{label}卖点样本不足（n={n}，需≥{min_n}）",
        }
    hit = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖后回落")
    early = sum(
        1
        for r in sells
        if (r.get("outcome_label") or "") in ("卖后继续涨", "卖飞")
    )
    fly_n = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖飞")
    rate = round(100.0 * hit / n, 1)
    # Extra early pressure when 卖飞 share is material.
    fly_share = fly_n / n if n else 0.0
    widen = rate < float(SELL_REVIEW_WIDEN_BELOW) or fly_share >= 0.25
    tighten = (not widen) and rate >= float(SELL_REVIEW_TIGHTEN_ABOVE)
    mult = 1.0
    note = f"{label}卖后回落命中 {rate}%（n={n}，继续涨/卖飞 {early}"
    if fly_n:
        note += f"·卖飞{fly_n}"
    note += "）"
    if widen:
        mult = float(SELL_REVIEW_WIDEN_MULT)
        note += "·偏早→放宽回撤/落袋"
    elif tighten:
        mult = float(SELL_REVIEW_TIGHTEN_MULT)
        note += "·偏准→略收紧止盈回撤"
    return {
        "ok": True,
        "kind": kind_f,
        "n": n,
        "hit_rate": rate,
        "early_n": early,
        "fly_n": fly_n,
        "hit_n": hit,
        "widen": widen,
        "tighten": tighten,
        "mult": mult,
        "note": note,
    }


def resolve_sell_kind_bias(
    bundle: dict[str, Any] | None,
    *,
    etf: bool,
) -> dict[str, Any]:
    """Pick etf/stock sell bias with conflict damping vs the all-sample row."""
    box = bundle or {}
    kind = dict((box.get("etf") if etf else box.get("stock")) or {})
    overall = dict(box.get("all") or {})
    if kind.get("ok"):
        if overall.get("ok"):
            if bool(kind.get("widen")) and bool(overall.get("tighten")):
                kind = {
                    **kind,
                    "mult": 1.0,
                    "widen": False,
                    "tighten": False,
                    "note": (kind.get("note") or "") + "·与合计冲突取中",
                }
            elif bool(kind.get("tighten")) and bool(overall.get("widen")):
                kind = {
                    **kind,
                    "mult": 1.0,
                    "widen": False,
                    "tighten": False,
                    "note": (kind.get("note") or "") + "·与合计冲突取中",
                }
        return kind
    if overall.get("ok"):
        out = dict(overall)
        note = str(out.get("note") or "")
        tag = "·分品种不足用合计"
        if tag not in note:
            out["note"] = note + tag if note else "分品种不足用合计"
        return out
    return kind or overall or {"ok": False, "mult": 1.0, "widen": False, "tighten": False}


def build_sell_review_bias_bundle(
    rows: list[dict[str, Any]] | None = None,
    *,
    hit_mode: str | None = None,
) -> dict[str, Any]:
    """Return all / etf / stock sell biases for review UI and exit bands."""
    try:
        src = rows if rows is not None else load_signals(limit=240)
    except Exception:
        src = []
    overall = build_sell_review_bias(src, hit_mode=hit_mode, kind=None)
    etf = build_sell_review_bias(src, hit_mode=hit_mode, kind="etf")
    stock = build_sell_review_bias(src, hit_mode=hit_mode, kind="stock")
    return {
        "all": overall,
        "etf": etf,
        "stock": stock,
        "note": " · ".join(
            n for n in (etf.get("note"), stock.get("note"), overall.get("note")) if n
        ),
        "hit_rate": overall.get("hit_rate"),
        "n": overall.get("n"),
        "widen": overall.get("widen"),
        "tighten": overall.get("tighten"),
    }


def build_sell_fly_board(
    rows: list[dict[str, Any]] | None = None,
    *,
    hit_mode: str | None = None,
    recent_limit: int = 10,
) -> dict[str, Any]:
    """Summarize early vs correct sells for the review 卖飞看板.

    Buckets: 卖后回落 (hit), 卖后继续涨, 卖飞. Breaks down by desk source and
    kind; lists recent 卖飞 / 卖后继续涨 samples so the UI can reinforce
    rule-based selling feedback.
    """
    mode = str(hit_mode or setting("hit_rate_mode", "traded") or "traded").strip().lower()
    try:
        src = rows if rows is not None else load_signals(limit=240)
    except Exception:
        src = []
    scored_all = [
        r
        for r in src
        if str(r.get("signal_type") or "") == "sell"
        and not int(r.get("skipped") or 0)
        and r.get("outcome_label")
    ]
    sells = list(scored_all)
    if mode == "traded":
        sells = [r for r in sells if int(r.get("traded") or 0)]
    n = len(sells)
    hit_n = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖后回落")
    early_n = sum(
        1
        for r in sells
        if (r.get("outcome_label") or "") in ("卖后继续涨", "卖飞")
    )
    fly_n = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖飞")
    cont_n = sum(1 for r in sells if (r.get("outcome_label") or "") == "卖后继续涨")
    hit_rate = round(100.0 * hit_n / n, 1) if n else None
    early_rate = round(100.0 * early_n / n, 1) if n else None
    fly_rate = round(100.0 * fly_n / n, 1) if n else None

    def _bucket(rows_in: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in rows_in:
            k = str(key_fn(r) or "未标").strip() or "未标"
            groups.setdefault(k, []).append(r)
        out: list[dict[str, Any]] = []
        for lab, grp in groups.items():
            gn = len(grp)
            g_hit = sum(1 for x in grp if (x.get("outcome_label") or "") == "卖后回落")
            g_fly = sum(1 for x in grp if (x.get("outcome_label") or "") == "卖飞")
            g_early = sum(
                1
                for x in grp
                if (x.get("outcome_label") or "") in ("卖后继续涨", "卖飞")
            )
            # Sell day3 pct > 0 means price fell after the exit (drawdown avoided).
            d3 = [float(x["outcome_day3_pct"]) for x in grp if x.get("outcome_day3_pct") is not None]
            out.append(
                {
                    "label": lab,
                    "n": gn,
                    "hit_n": g_hit,
                    "fly_n": g_fly,
                    "early_n": g_early,
                    "hit_rate": round(100.0 * g_hit / gn, 1) if gn else None,
                    "early_rate": round(100.0 * g_early / gn, 1) if gn else None,
                    "fly_rate": round(100.0 * g_fly / gn, 1) if gn else None,
                    "avg_saved_d3": round(sum(d3) / len(d3), 2) if d3 else None,
                }
            )
        out.sort(key=lambda x: (-(x.get("n") or 0), x.get("label") or ""))
        return out

    by_source = _bucket(
        sells,
        lambda r: r.get("desk_source") or r.get("source") or "main",
    )
    by_kind = _bucket(
        sells,
        lambda r: "ETF" if str(r.get("kind") or "") == "etf" else "个股",
    )
    by_phase = _bucket(sells, lambda r: r.get("phase") or "未标")
    # Rule quality is judged on every scored sell signal, executed or not.
    by_rule = _bucket(
        scored_all,
        lambda r: (r.get("payload") or {}).get("exit_rule") or sell_rule_of(r.get("action")),
    )
    by_atr = _bucket(scored_all, sell_atr_bucket)

    early_rows = [
        r
        for r in sells
        if (r.get("outcome_label") or "") in ("卖飞", "卖后继续涨")
    ]
    early_rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
    recent = []
    for r in early_rows[: max(1, int(recent_limit))]:
        recent.append(
            {
                "trade_date": str(r.get("trade_date") or "")[:10],
                "code": r.get("code"),
                "name": r.get("name") or r.get("code"),
                "kind": r.get("kind") or "stock",
                "outcome_label": r.get("outcome_label"),
                "outcome_day1_pct": r.get("outcome_day1_pct"),
                "outcome_mae_pct": r.get("outcome_mae_pct"),
                "price": r.get("price"),
                "desk_source": r.get("desk_source") or r.get("source"),
                "phase": r.get("phase"),
                "mainline": r.get("mainline"),
            }
        )

    verdict = "样本不足"
    note = f"已打分卖出 n={n}"
    if n >= 6:
        if fly_rate is not None and fly_rate >= 20:
            verdict = "偏早·卖飞偏多"
            note = f"卖飞 {fly_n}/{n}（{fly_rate}%）· 卖后回落 {hit_n} · 宜略放宽止盈/回撤"
        elif early_rate is not None and early_rate >= 55:
            verdict = "偏早"
            note = f"续涨/卖飞 {early_n}/{n}（{early_rate}%）· 卖后回落命中 {hit_rate}%"
        elif hit_rate is not None and hit_rate >= 55:
            verdict = "卖对居多"
            note = f"卖后回落 {hit_n}/{n}（{hit_rate}%）· 规则卖点值得跟"
        else:
            verdict = "中性"
            note = f"卖后回落 {hit_rate}% · 续涨/卖飞 {early_rate}%"
    elif n:
        note = f"样本偏少（n={n}，建议≥6）· 卖飞 {fly_n} · 回落 {hit_n}"

    return {
        "ok": n >= 6,
        "n": n,
        "hit_n": hit_n,
        "early_n": early_n,
        "fly_n": fly_n,
        "cont_n": cont_n,
        "hit_rate": hit_rate,
        "early_rate": early_rate,
        "fly_rate": fly_rate,
        "verdict": verdict,
        "note": note,
        "by_source": by_source,
        "by_kind": by_kind,
        "by_phase": by_phase[:6],
        "by_rule": by_rule,
        "by_atr": by_atr,
        "rule_n": len(scored_all),
        "recent_early": recent,
        "hit_mode": mode,
    }


_REVIEW_BIAS_CACHE: dict[str, Any] = {"day": "", "sell": None, "buy_gate": None}


def cached_sell_bias_bundle() -> dict[str, Any]:
    """Day-scoped cache for sell-review bias (avoid per-refresh full scans)."""
    day = datetime.now().strftime("%Y-%m-%d")
    if _REVIEW_BIAS_CACHE.get("day") != day or _REVIEW_BIAS_CACHE.get("sell") is None:
        _REVIEW_BIAS_CACHE["day"] = day
        try:
            _REVIEW_BIAS_CACHE["sell"] = build_sell_review_bias_bundle()
        except Exception:
            _REVIEW_BIAS_CACHE["sell"] = {
                "all": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
                "etf": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
                "stock": {"ok": False, "mult": 1.0, "widen": False, "tighten": False},
                "note": "卖点闭环暂不可用",
            }
        _REVIEW_BIAS_CACHE["buy_gate"] = None
    if _REVIEW_BIAS_CACHE.get("buy_gate") is None:
        try:
            _REVIEW_BIAS_CACHE["buy_gate"] = build_buy_gate_bias()
        except Exception:
            _REVIEW_BIAS_CACHE["buy_gate"] = {
                "ok": False,
                "mult": 1.0,
                "loosen": False,
                "gates": {},
                "note": "买侧闸门闭环暂不可用",
            }
    return dict(_REVIEW_BIAS_CACHE["sell"] or {})


def cached_buy_gate_bias() -> dict[str, Any]:
    """Day-scoped buy-gate loosen multipliers from false-kill stats."""
    cached_sell_bias_bundle()  # ensure day cache warm
    return dict(_REVIEW_BIAS_CACHE.get("buy_gate") or {"ok": False, "mult": 1.0, "loosen": False, "gates": {}})


def build_buy_gate_bias() -> dict[str, Any]:
    """Tune tip / minute / thin-confirm thresholds from review kill stats.

    False-kills loosen (mult < 1); true-kills tighten (mult > 1). Returns
    per-gate multipliers and a global mult for shared knobs.
    """
    from market_desk.config import (
        BUY_GATE_FALSE_KILL_MIN,
        BUY_GATE_KILL_MIN,
        BUY_GATE_LOOSEN_MULT,
        BUY_GATE_TIGHTEN_MULT,
        BUY_GATE_TRUE_KILL_MIN,
    )
    from market_desk.db import load_signals

    kills = build_gate_kill_stats(load_signals(limit=240))
    gates: dict[str, dict[str, Any]] = {}
    loosen_any = False
    tighten_any = False
    mult = 1.0
    note_parts: list[str] = []
    target = {
        "分时": "minute",
        "离日高": "off_high",
        "薄确认/共振": "thin",
        "相对强弱": "rel_strength",
    }
    for row in kills or []:
        gate = str(row.get("gate") or "")
        key = target.get(gate)
        if not key:
            continue
        fk = int(row.get("false_kill_n") or 0)
        tk = int(row.get("true_kill_n") or 0)
        kn = int(row.get("kill_n") or 0)
        if fk >= int(BUY_GATE_FALSE_KILL_MIN) and kn >= int(BUY_GATE_KILL_MIN):
            g_mult = float(BUY_GATE_LOOSEN_MULT)
            gates[key] = {
                "gate": gate,
                "mult": g_mult,
                "false_kill_n": fk,
                "true_kill_n": tk,
                "kill_n": kn,
                "mode": "loosen",
            }
            loosen_any = True
            mult = min(mult, g_mult)
            note_parts.append(f"{gate}假杀{fk}/{kn}→×{g_mult}")
        elif tk >= int(BUY_GATE_TRUE_KILL_MIN) and kn >= int(BUY_GATE_KILL_MIN) and fk <= max(1, tk // 3):
            g_mult = float(BUY_GATE_TIGHTEN_MULT)
            gates[key] = {
                "gate": gate,
                "mult": g_mult,
                "false_kill_n": fk,
                "true_kill_n": tk,
                "kill_n": kn,
                "mode": "tighten",
            }
            tighten_any = True
            mult = max(mult, g_mult)
            note_parts.append(f"{gate}真杀{tk}/{kn}→×{g_mult}")
    return {
        "ok": True,
        "loosen": loosen_any,
        "tighten": tighten_any,
        "mult": mult,
        "gates": gates,
        "note": "；".join(note_parts) if note_parts else "买侧闸门暂不调参",
    }
