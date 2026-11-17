"""Parameter experiment framework (预注册实验).

The desk already pre-registers out-of-sample dates in ``config``. What was
missing is a place to state, before running: *what is the hypothesis, which
switch flips it, how many samples are needed, and how large a lift counts*.

Experiments live in ``experiments.json`` (repo root). This module loads and
validates them, decides which ones are due on a given day, and scores a run
against those pre-registered criteria — so a result cannot be re-interpreted
after the fact.

No experiment auto-changes behaviour: it reports ``pass`` / ``fail`` /
``inconclusive`` and the caller decides.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from market_desk.review.outcome import BUY_HIT_LABELS

ALLOWED_METRICS = ("hit_rate", "avg_day1", "avg_day3", "excess_day3")
REQUIRED_FIELDS = (
    "id",
    "hypothesis",
    "switch",
    "metric",
    "min_n",
    "min_lift_pp",
    "start",
    "end",
)


def default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "experiments.json"


def _parse_day(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def validate_experiment(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalise one experiment definition; returns (clean, errors)."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["条目不是对象"]
    for key in REQUIRED_FIELDS:
        if raw.get(key) in (None, ""):
            errors.append(f"缺少字段 {key}")
    metric = str(raw.get("metric") or "").strip().lower()
    if metric and metric not in ALLOWED_METRICS:
        errors.append(f"metric 仅支持 {ALLOWED_METRICS}")
    start, end = _parse_day(raw.get("start")), _parse_day(raw.get("end"))
    if raw.get("start") and start is None:
        errors.append("start 需为 YYYY-MM-DD")
    if raw.get("end") and end is None:
        errors.append("end 需为 YYYY-MM-DD")
    if start and end and end < start:
        errors.append("end 早于 start")
    try:
        min_n = int(raw.get("min_n") or 0)
    except (TypeError, ValueError):
        min_n = 0
        errors.append("min_n 需为整数")
    if min_n and min_n < 10:
        errors.append("min_n 至少 10（低于此样本不构成证据）")
    try:
        lift = float(raw.get("min_lift_pp") or 0)
    except (TypeError, ValueError):
        lift = 0.0
        errors.append("min_lift_pp 需为数字")
    if errors:
        return None, errors
    return (
        {
            "id": str(raw["id"]).strip(),
            "hypothesis": str(raw["hypothesis"]).strip(),
            "switch": str(raw["switch"]).strip(),
            "metric": metric,
            "min_n": min_n,
            "min_lift_pp": round(lift, 2),
            "start": start,
            "end": end,
            "status": str(raw.get("status") or "planned").strip().lower(),
            "note": str(raw.get("note") or "").strip(),
        },
        [],
    )


def load_experiments(path: str | Path | None = None) -> dict[str, Any]:
    """Load + validate ``experiments.json``; returns experiments and errors."""
    target = Path(path) if path else default_path()
    if not target.exists():
        return {"ok": False, "path": str(target), "experiments": [], "errors": ["experiments.json 不存在"]}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "path": str(target), "experiments": [], "errors": [f"解析失败：{exc}"]}
    items = raw.get("experiments") if isinstance(raw, dict) else raw
    clean: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(items or []):
        exp, errs = validate_experiment(item)
        if errs:
            errors.append(f"#{idx}: " + "；".join(errs))
            continue
        if exp["id"] in seen:
            errors.append(f"#{idx}: id 重复（{exp['id']}）")
            continue
        seen.add(exp["id"])
        clean.append(exp)
    return {"ok": not errors, "path": str(target), "experiments": clean, "errors": errors}


def due_experiments(day: str | date | None = None, path: str | Path | None = None) -> list[dict[str, Any]]:
    """Experiments whose pre-registered window covers ``day``."""
    today = _parse_day(day or datetime.now().strftime("%Y-%m-%d")) or ""
    loaded = load_experiments(path)
    return [
        exp
        for exp in loaded.get("experiments") or []
        if exp.get("start") and exp.get("end") and exp["start"] <= today <= exp["end"]
    ]


def _metric(rows: Iterable[dict[str, Any]], metric: str) -> tuple[float | None, int]:
    vals: list[float] = []
    if metric == "hit_rate":
        for row in rows:
            if str(row.get("outcome_label") or "") in BUY_HIT_LABELS:
                vals.append(1.0)
            elif str(row.get("outcome_label") or ""):
                vals.append(0.0)
        n = len(vals)
        return (round(100.0 * sum(vals) / n, 2) if n else None), n
    field = {
        "avg_day1": "outcome_day1_pct",
        "avg_day3": "outcome_day3_pct",
        "excess_day3": "excess_d3_pct",
    }.get(metric, "outcome_day1_pct")
    for row in rows:
        try:
            vals.append(float(row.get(field)))
        except (TypeError, ValueError):
            continue
    n = len(vals)
    return (round(sum(vals) / n, 2) if n else None), n


def evaluate_experiment(
    experiment: dict[str, Any],
    *,
    treatment_rows: list[dict[str, Any]],
    control_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score a run against the pre-registered sample size and lift."""
    metric = str(experiment.get("metric") or "hit_rate")
    treat, n_t = _metric(treatment_rows or [], metric)
    ctrl, n_c = _metric(control_rows or [], metric)
    min_n = int(experiment.get("min_n") or 0)
    need_lift = float(experiment.get("min_lift_pp") or 0)
    if treat is None or ctrl is None:
        return {
            "ok": True,
            "id": experiment.get("id"),
            "verdict": "inconclusive",
            "metric": metric,
            "treatment_n": n_t,
            "control_n": n_c,
            "note": "一侧无可用样本",
        }
    if min(n_t, n_c) < min_n:
        return {
            "ok": True,
            "id": experiment.get("id"),
            "verdict": "inconclusive",
            "metric": metric,
            "treatment": treat,
            "control": ctrl,
            "lift_pp": round(treat - ctrl, 2),
            "treatment_n": n_t,
            "control_n": n_c,
            "note": f"样本不足（需要各 ≥{min_n}）",
        }
    lift = round(treat - ctrl, 2)
    return {
        "ok": True,
        "id": experiment.get("id"),
        "verdict": "pass" if lift >= need_lift else "fail",
        "metric": metric,
        "treatment": treat,
        "control": ctrl,
        "lift_pp": lift,
        "threshold_pp": need_lift,
        "treatment_n": n_t,
        "control_n": n_c,
        "note": f"提升{lift:+.2f}pp，门槛{need_lift:+.2f}pp",
    }