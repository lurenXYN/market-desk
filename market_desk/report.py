"""Daily markdown report and related desk digests."""

from __future__ import annotations

from typing import Any


def build_daily_report(
    *,
    snapshot: dict[str, Any] | None,
    review: dict[str, Any] | None,
) -> str:
    """Build a plain-text / Markdown daily journal from live + review payloads."""
    snap = snapshot or {}
    rev = review or {}
    summary = rev.get("summary") or {}
    today = summary.get("today") or {}
    exec_score = summary.get("exec") or today.get("exec") or {}
    phase_hits = summary.get("phase_hits") or []
    verdict = snap.get("verdict") or {}
    ml = verdict.get("mainline") or {}
    lines = [
        f"# A股情绪作战台日报 · {today.get('date') or snap.get('trade_date') or '—'}",
        "",
        "## 大盘与主线",
        f"- 相位：{snap.get('phase') or today.get('phase') or '—'}",
        f"- 温度：{snap.get('temperature') if snap.get('temperature') is not None else '—'}",
        f"- 主线：{ml.get('name') or '—'}（{ml.get('status') or '—'}）",
        f"- 结论：{verdict.get('action') or '—'}",
        f"- 叙事：{verdict.get('narrative') or verdict.get('reason') or '—'}",
        "",
        "## 今日信号",
        f"- 买入 {today.get('buy_n', 0)} / 卖出 {today.get('sell_n', 0)}",
        f"- 已交易 {today.get('traded_n', 0)} / 未交易 {today.get('skipped_n', 0)}",
        f"- 未回踩上行 {today.get('miss_pullback_n', 0)} / 价带内 {today.get('in_band_n', 0)}",
        f"- 主线切换 {today.get('switch_n', 0)} 次",
        "",
        "## 计划执行",
        f"- 执行分：{exec_score.get('score') if exec_score.get('score') is not None else '—'} "
        f"（已成交买 {exec_score.get('traded_buy_n', 0)}）",
        f"- 价带内成交 {exec_score.get('in_band_n', 0)} / 追高 {exec_score.get('chase_n', 0)} / "
        f"更低更好 {exec_score.get('below_n', 0)} / 其他 {exec_score.get('other_n', 0)}",
        "",
        "## 命中与相位",
        f"- 今日命中率：{today.get('buy_hit_rate') if today.get('buy_hit_rate') is not None else '—'}%"
        if today.get("buy_hit_rate") is not None
        else "- 今日命中率：—（待隔日打分）",
        f"- 累计买入命中率：{summary.get('buy_hit_rate') if summary.get('buy_hit_rate') is not None else '—'}%",
    ]
    if phase_hits:
        lines.append("- 相位对照：")
        for row in phase_hits:
            rate = row.get("hit_rate")
            rate_s = "—" if rate is None else f"{rate}%"
            lines.append(
                f"  - {row.get('phase') or '未标'}：命中 {rate_s}（样本 {row.get('scored_n', 0)}）"
            )
    risk = snap.get("risk_overview") or {}
    if risk:
        lines.extend(
            [
                "",
                "## 风控快照",
                f"- 持仓 {risk.get('count', 0)} 只 · 成本 {risk.get('cost', '—')} · "
                f"市值 {risk.get('market', '—')} · 浮盈 {risk.get('pnl', '—')} "
                f"({risk.get('pnl_pct', '—')}%)",
                f"- 软限制：{risk.get('risk_note') or '未触线'}",
            ]
        )
        for item in (risk.get("items") or [])[:8]:
            lines.append(
                f"  - {item.get('name') or item.get('code')} 占比 {item.get('weight_pct')}% "
                f"浮盈 {item.get('pnl_pct')}%"
            )
    traded = [
        r
        for r in (rev.get("signals") or [])
        if int(r.get("traded") or 0)
        and str(r.get("trade_date") or "") == str(today.get("date") or snap.get("trade_date") or "")
    ]
    if traded:
        lines.extend(["", "## 今日已交易"])
        for r in traded[:20]:
            lines.append(
                f"- {'买' if r.get('signal_type') == 'buy' else '卖'} "
                f"{r.get('name') or ''} {r.get('code')} "
                f"建议 {r.get('price')} 成交 {r.get('fill_price') or '—'}×{r.get('fill_qty') or '—'}"
            )
    lines.extend(["", "---", "_由 market-desk 自动生成，仅供复盘，不构成投资建议。_"])
    return "\n".join(lines)
