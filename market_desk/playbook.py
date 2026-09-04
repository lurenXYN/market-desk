"""Phase playbooks: three crisp lines for each market regime."""

from __future__ import annotations

from typing import Any


PLAYBOOKS: dict[str, dict[str, Any]] = {
    "恐慌": {
        "title": "恐慌 · 先活下来",
        "lines": (
            "能做：空仓观望，或只留已浮盈的极轻仓。",
            "不做：抄底连板、摊平浮亏、新开题材仓。",
            "仓位：总仓建议 ≤20%，单笔风险尽量关闭。",
        ),
        "size_cap_pct": 20,
    },
    "分歧": {
        "title": "分歧 · 小仓试错",
        "lines": (
            "能做：主线 ETF 回踩试错；确认前不加第二笔。",
            "不做：追高板、无映射主线硬买个股。",
            "仓位：总仓建议 ≤40%，单笔按风险%严格算股数。",
        ),
        "size_cap_pct": 40,
    },
    "发酵": {
        "title": "发酵 · 跟主线不追尖",
        "lines": (
            "能做：主线 ETF / 主板回踩票，分批 1→2 手。",
            "不做：尖峰禁追板块、创业/科创个股。",
            "仓位：总仓建议 ≤60%，优先 ETF，个股更小。",
        ),
        "size_cap_pct": 60,
    },
    "高潮": {
        "title": "高潮 · 兑现优先",
        "lines": (
            "能做：减仓落袋、只盯回踩；午后更谨慎。",
            "不做：高潮里新开重仓、接力高标。",
            "仓位：总仓建议 ≤50%，新开仓默认降级观察。",
        ),
        "size_cap_pct": 50,
    },
}


def build_playbook(
    phase: str | None,
    *,
    action: str | None = None,
    size_hint: str | None = None,
) -> dict[str, Any]:
    """Return the three-line playbook for the current phase."""
    key = str(phase or "").strip() or "分歧"
    base = PLAYBOOKS.get(key) or PLAYBOOKS["分歧"]
    lines = list(base["lines"])
    if size_hint:
        lines[2] = f"{lines[2]}（系统：{size_hint}）"
    if action == "观望" and key != "恐慌":
        lines[0] = f"当前结论观望：{lines[0]}"
    elif action == "观察回踩":
        lines[0] = f"当前观察回踩：{lines[0]}"
    elif action in ("可买入", "可小仓"):
        lines[0] = f"当前可买窗口：{lines[0]}"
    return {
        "phase": key if key in PLAYBOOKS else "分歧",
        "title": base["title"],
        "lines": lines,
        "size_cap_pct": base["size_cap_pct"],
        "do": lines[0],
        "dont": lines[1],
        "size": lines[2],
    }


def suggest_risk_qty(
    *,
    buy: float | None,
    stop: float | None,
    account_equity: float,
    risk_pct: float,
    lot: int = 100,
    kind: str = "stock",
) -> dict[str, Any] | None:
    """Suggest share count from account equity and per-trade risk percent."""
    if buy is None or stop is None:
        return None
    try:
        buy_f = float(buy)
        stop_f = float(stop)
        equity = float(account_equity)
        risk = float(risk_pct)
    except (TypeError, ValueError):
        return None
    if buy_f <= 0 or equity <= 0 or risk <= 0:
        return None
    risk_gap = buy_f - stop_f
    if risk_gap <= 0:
        # Invalid stop above buy — fall back to one lot probe.
        qty = lot
        return {
            "qty": qty,
            "lots": 1,
            "risk_amount": round(equity * risk / 100.0, 2),
            "approx_cost": round(buy_f * qty, 2 if kind != "etf" else 3),
            "note": "止损不低于买价，按 1 手试错",
        }
    risk_amount = equity * risk / 100.0
    raw = risk_amount / risk_gap
    # Round down to lot size; at least 0 (show as none) if too small.
    qty = int(raw // lot) * lot
    if qty < lot and raw >= lot * 0.4:
        qty = lot
    if qty <= 0:
        return {
            "qty": 0,
            "lots": 0,
            "risk_amount": round(risk_amount, 2),
            "approx_cost": 0,
            "note": "风险金额买不起 1 手，先不加",
        }
    # Soft cap: do not exceed ~15% of equity on one name.
    max_by_equity = int((equity * 0.15 / buy_f) // lot) * lot
    if max_by_equity >= lot:
        qty = min(qty, max_by_equity)
    return {
        "qty": qty,
        "lots": qty // lot,
        "risk_amount": round(risk_amount, 2),
        "approx_cost": round(buy_f * qty, 2 if kind != "etf" else 3),
        "note": f"风险{risk:g}%≈{risk_amount:.0f}元 / 价差{risk_gap:.3g}",
    }
