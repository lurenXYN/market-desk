"""White-box logistic weights over buy-signal features.

Fits a small L2-regularized logistic model on scored buys to estimate
P(次日红/三日红). Exposes human-readable weights and a soft score nudge —
never a hard buy ban.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from market_desk.config import (
    WHITEBOX_L2,
    WHITEBOX_LR,
    WHITEBOX_MAX_ITERS,
    WHITEBOX_MIN_N,
    WHITEBOX_SCORE_SCALE,
    WHITEBOX_WEEK_SPLIT,
)
from market_desk.settings import setting

_WB_CACHE: dict[str, Any] = {"day": "", "model": None}

# Stable feature order for the white-box model.
FEATURE_KEYS: tuple[str, ...] = (
    "bias",
    "is_etf",
    "phase_panic",
    "phase_diverge",
    "phase_ferment",
    "phase_climax",
    "ready",
    "trend_up",
    "trend_down",
    "board_match",
    "gate_killed",
    "gate_minute",
    "gate_off_high",
    "gate_thin",
    "gate_rel",
)

FEATURE_LABELS: dict[str, str] = {
    "bias": "截距",
    "is_etf": "ETF",
    "phase_panic": "相位·恐慌",
    "phase_diverge": "相位·分歧",
    "phase_ferment": "相位·发酵",
    "phase_climax": "相位·高潮",
    "ready": "ready 卡片",
    "trend_up": "日线上升",
    "trend_down": "日线下降",
    "board_match": "贴合主线",
    "gate_killed": "曾被闸门杀掉",
    "gate_minute": "分时闸门",
    "gate_off_high": "离日高闸门",
    "gate_thin": "薄确认闸门",
    "gate_rel": "相对强弱闸门",
}


def _num(v: Any) -> float | None:
    """Parse a numeric field."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sigmoid(z: float) -> float:
    """Numerically stable sigmoid."""
    if z >= 30:
        return 1.0
    if z <= -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _gate_flags(payload: dict[str, Any]) -> set[str]:
    """Collect confirm-fail gate tokens from payload history."""
    from market_desk.review import _gate_bucket

    raw = (
        payload.get("confirm_fail_hist")
        or payload.get("final_fail")
        or payload.get("confirm_fail")
        or []
    )
    out: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("gate") or item.get("fail") or item.get("text") or "")
        else:
            text = str(item or "")
        if text:
            out.add(_gate_bucket(text))
    return out


def extract_features(row: dict[str, Any]) -> dict[str, float]:
    """Map one buy signal row into a dense feature dict in [0, 1] / bias=1."""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    phase = str(row.get("phase") or "")
    kind = str(row.get("kind") or "stock")
    gates = _gate_flags(payload)
    feat = {k: 0.0 for k in FEATURE_KEYS}
    feat["bias"] = 1.0
    feat["is_etf"] = 1.0 if kind == "etf" else 0.0
    feat["phase_panic"] = 1.0 if phase == "恐慌" else 0.0
    feat["phase_diverge"] = 1.0 if phase == "分歧" else 0.0
    feat["phase_ferment"] = 1.0 if phase == "发酵" else 0.0
    feat["phase_climax"] = 1.0 if phase == "高潮" else 0.0
    feat["ready"] = 1.0 if int(row.get("ready") or 0) else 0.0
    if payload.get("trend_up") or (isinstance(payload.get("trend"), dict) and payload["trend"].get("up")):
        feat["trend_up"] = 1.0
    if payload.get("trend_down") or (isinstance(payload.get("trend"), dict) and payload["trend"].get("down")):
        feat["trend_down"] = 1.0
    if payload.get("board_match") or str(payload.get("vs_mainline") or "") in (
        "same",
        "theme",
        "match",
        "属于主线",
        "接近主线",
    ):
        feat["board_match"] = 1.0
    elif payload.get("vs_mainline") is True:
        feat["board_match"] = 1.0
    if gates:
        feat["gate_killed"] = 1.0
    if "分时" in gates:
        feat["gate_minute"] = 1.0
    if "离日高" in gates:
        feat["gate_off_high"] = 1.0
    if "薄确认/共振" in gates:
        feat["gate_thin"] = 1.0
    if "相对强弱" in gates:
        feat["gate_rel"] = 1.0
    return feat


def _label_win(row: dict[str, Any]) -> int:
    """Binary label: 1 if buy outcome was green."""
    return 1 if str(row.get("outcome_label") or "") in {"次日红", "三日红"} else 0


def _scored_buys(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Filter scored buy rows respecting hit_rate_mode."""
    mode = str(setting("hit_rate_mode", "traded") or "traded").strip().lower()
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if str(row.get("signal_type") or "") != "buy":
            continue
        if int(row.get("skipped") or 0):
            continue
        if mode == "traded" and not int(row.get("traded") or 0):
            continue
        if not row.get("outcome_label"):
            continue
        out.append(row)
    out.sort(key=lambda r: (str(r.get("signal_at") or r.get("trade_date") or ""), int(r.get("id") or 0)))
    return out


def _fit_logistic(
    xs: list[list[float]],
    ys: list[int],
    *,
    l2: float,
    lr: float,
    max_iters: int,
) -> list[float]:
    """Fit L2 logistic regression with plain gradient descent (no deps)."""
    dim = len(xs[0]) if xs else 0
    w = [0.0] * dim
    n = max(1, len(xs))
    for _ in range(int(max_iters)):
        grad = [0.0] * dim
        for x, y in zip(xs, ys):
            z = sum(wi * xi for wi, xi in zip(w, x))
            p = _sigmoid(z)
            err = p - float(y)
            for j in range(dim):
                grad[j] += err * x[j]
        for j in range(dim):
            # Do not L2-penalize the bias term.
            pen = 0.0 if j == 0 else float(l2) * w[j]
            w[j] -= float(lr) * (grad[j] / n + pen)
    return w


def _pack_weights(w: list[float]) -> list[dict[str, Any]]:
    """Attach labels and sort by absolute weight (bias last among ties)."""
    rows = []
    for key, val in zip(FEATURE_KEYS, w):
        rows.append(
            {
                "key": key,
                "label": FEATURE_LABELS.get(key, key),
                "weight": round(float(val), 4),
            }
        )
    rows.sort(key=lambda r: (0 if r["key"] == "bias" else 1, -abs(float(r["weight"]))))
    return rows


def _holdout_metrics(
    xs: list[list[float]],
    ys: list[int],
    w: list[float],
    *,
    hold_frac: float = 0.35,
) -> dict[str, Any]:
    """Evaluate a fitted weight vector on the chronological holdout tail."""
    n = len(ys)
    hold_n = max(0, int(round(n * float(hold_frac))))
    if hold_n < 4 or n - hold_n < int(WHITEBOX_MIN_N):
        return {"ok": False, "n": hold_n, "note": "样本外窗口不足"}
    start = n - hold_n
    # Refit on train-only so holdout is truly out-of-sample.
    w_train = _fit_logistic(
        xs[:start],
        ys[:start],
        l2=float(WHITEBOX_L2),
        lr=float(WHITEBOX_LR),
        max_iters=int(WHITEBOX_MAX_ITERS),
    )
    correct = 0
    brier = 0.0
    for x, y in zip(xs[start:], ys[start:]):
        z = sum(wi * xi for wi, xi in zip(w_train, x))
        p = _sigmoid(z)
        pred = 1 if p >= 0.5 else 0
        if pred == int(y):
            correct += 1
        brier += (p - float(y)) ** 2
    acc = correct / float(hold_n)
    brier /= float(hold_n)
    base = sum(ys[start:]) / float(hold_n)
    # Lift vs always-predict-majority baseline accuracy.
    maj = max(base, 1.0 - base)
    return {
        "ok": True,
        "n": hold_n,
        "accuracy": round(acc * 100.0, 1),
        "brier": round(brier, 4),
        "base_rate": round(base * 100.0, 1),
        "lift_pp": round((acc - maj) * 100.0, 1),
        "note": f"样本外 n={hold_n} 准确率{acc * 100:.0f}%（基准{maj * 100:.0f}%）",
    }


def fit_whitebox(
    rows: list[dict[str, Any]] | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fit current + prior-window weights; return panel payload and soft map."""
    day = datetime.now().strftime("%Y-%m-%d")
    if use_cache and rows is None and _WB_CACHE.get("day") == day and _WB_CACHE.get("model"):
        return dict(_WB_CACHE["model"])
    try:
        if rows is None:
            from market_desk.db import load_signals

            rows = load_signals(limit=400)
    except Exception:
        rows = []
    scored = _scored_buys(rows)
    n = len(scored)
    if n < int(WHITEBOX_MIN_N):
        out = {
            "ok": False,
            "n": n,
            "need": int(WHITEBOX_MIN_N),
            "weights": [],
            "deltas": [],
            "holdout": {"ok": False},
            "note": f"白盒样本不足（n={n}，需≥{WHITEBOX_MIN_N}）",
            "hit_rate": None,
        }
        if use_cache and rows is None:
            _WB_CACHE["day"] = day
            _WB_CACHE["model"] = out
        return out

    xs = [[float(extract_features(r)[k]) for k in FEATURE_KEYS] for r in scored]
    ys = [_label_win(r) for r in scored]
    w = _fit_logistic(
        xs,
        ys,
        l2=float(WHITEBOX_L2),
        lr=float(WHITEBOX_LR),
        max_iters=int(WHITEBOX_MAX_ITERS),
    )
    weights = _pack_weights(w)
    holdout = _holdout_metrics(xs, ys, w)

    # Prior window for week-over-week style delta (last half / older half).
    split = max(int(WHITEBOX_MIN_N), int(n * float(WHITEBOX_WEEK_SPLIT)))
    deltas: list[dict[str, Any]] = []
    if n >= split + int(WHITEBOX_MIN_N):
        older = scored[:-split]
        xs_o = [[float(extract_features(r)[k]) for k in FEATURE_KEYS] for r in older]
        ys_o = [_label_win(r) for r in older]
        w_o = _fit_logistic(
            xs_o,
            ys_o,
            l2=float(WHITEBOX_L2),
            lr=float(WHITEBOX_LR),
            max_iters=int(WHITEBOX_MAX_ITERS),
        )
        for key, cur, old in zip(FEATURE_KEYS, w, w_o):
            if key == "bias":
                continue
            diff = float(cur) - float(old)
            if abs(diff) < 0.05:
                continue
            deltas.append(
                {
                    "key": key,
                    "label": FEATURE_LABELS.get(key, key),
                    "from": round(float(old), 3),
                    "to": round(float(cur), 3),
                    "delta": round(diff, 3),
                }
            )
        deltas.sort(key=lambda d: -abs(float(d["delta"])))
        deltas = deltas[:6]

    hit = sum(ys) / float(n)
    soft = {k: float(v) for k, v in zip(FEATURE_KEYS, w) if k != "bias"}
    # Soft: when holdout underperforms majority, damp live score influence.
    score_scale = float(WHITEBOX_SCORE_SCALE)
    if holdout.get("ok") and float(holdout.get("lift_pp") or 0) < -2:
        score_scale *= 0.5
    note_bits = [f"白盒 n={n} 样本命中{hit * 100:.0f}%"]
    if holdout.get("ok"):
        note_bits.append(str(holdout.get("note") or ""))
    if deltas:
        top = deltas[0]
        note_bits.append(
            f"{top['label']} {top['from']:+.2f}→{top['to']:+.2f}"
        )
    out = {
        "ok": True,
        "n": n,
        "hit_rate": round(hit * 100.0, 1),
        "weights": weights,
        "deltas": deltas,
        "holdout": holdout,
        "score_scale": round(score_scale, 3),
        "soft": soft,
        "note": " · ".join(note_bits),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if use_cache and rows is None:
        _WB_CACHE["day"] = day
        _WB_CACHE["model"] = out
    return out


def whitebox_score_adj(
    *,
    kind: str = "stock",
    phase: str = "",
    ready: bool = False,
    trend: dict[str, Any] | None = None,
    board_match: bool = False,
    confirm_fail: list[Any] | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a soft score adjustment for one live recommend candidate."""
    m = model if model is not None else fit_whitebox()
    if not m.get("ok"):
        return {"ok": False, "score_adj": 0.0, "note": m.get("note") or "", "contribs": []}
    soft = m.get("soft") or {}
    fake = {
        "kind": kind,
        "phase": phase,
        "ready": 1 if ready else 0,
        "payload": {
            "trend_up": bool((trend or {}).get("up")),
            "trend_down": bool((trend or {}).get("down")),
            "board_match": board_match,
            "confirm_fail": list(confirm_fail or []),
        },
    }
    feat = extract_features(fake)
    raw = 0.0
    parts: list[str] = []
    contribs: list[dict[str, Any]] = []
    for key, x in feat.items():
        if key == "bias" or abs(x) < 1e-9:
            continue
        w = float(soft.get(key) or 0.0)
        if abs(w) < 1e-9:
            continue
        c = w * x
        raw += c
        if abs(c) >= 0.05:
            label = FEATURE_LABELS.get(key, key)
            parts.append(f"{label}{c:+.2f}")
            contribs.append({"key": key, "label": label, "value": round(c, 3)})
    contribs.sort(key=lambda c: -abs(float(c["value"])))
    scale = float(m.get("score_scale") or WHITEBOX_SCORE_SCALE)
    adj = max(-6.0, min(6.0, raw * scale))
    return {
        "ok": True,
        "score_adj": round(adj, 2),
        "raw": round(raw, 4),
        "parts": parts[:4],
        "contribs": contribs[:5],
        "note": ("白盒 " + " ".join(parts[:3])).strip() if parts else "白盒≈0",
    }
