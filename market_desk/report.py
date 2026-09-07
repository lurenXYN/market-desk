"""Daily markdown report and related desk digests."""

from __future__ import annotations

from typing import Any


def build_morning_brief(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Build a rule-based morning decision brief (no LLM, no push).

    Focuses on what to do before / around the open: phase, auction, mainline,
    similar-day bias, playbook lines and a short checklist.
    """
    snap = snapshot or {}
    verdict = snap.get("verdict") or {}
    ml = verdict.get("mainline") or {}
    carrier = verdict.get("carrier") or {}
    pb = verdict.get("playbook") or {}
    auc = snap.get("auction") or {}
    sim = snap.get("similar_days") or {}
    risk = snap.get("risk_overview") or snap.get("position_summary") or {}
    seg = verdict.get("segment") or {}
    phase = snap.get("phase") or verdict.get("phase") or "—"
    temp = snap.get("temperature")
    action = verdict.get("action") or "观望"
    board = ml.get("name") or "未明"
    etf_mapped = ml.get("etf_mapped")
    hist = snap.get("history") or []
    yday = hist[0] if hist else None

    bullets: list[str] = []
    bullets.append(
        f"相位 {phase}"
        + (f" · 温度 {temp}" if temp is not None else "")
        + f" · 结论 {action}"
    )
    if yday:
        bullets.append(
            f"昨收对照：{yday.get('trade_date') or '—'} "
            f"相位 {yday.get('phase') or '—'} · 温度 "
            f"{yday.get('temperature') if yday.get('temperature') is not None else '—'} · "
            f"涨停 {yday.get('zt') if yday.get('zt') is not None else '—'}"
        )
    if auc.get("tone") or auc.get("median_open") is not None:
        med = auc.get("median_open")
        med_s = "—" if med is None else f"{med}%"
        bullets.append(
            f"竞价：{auc.get('tone') or '未锁定'}（中位开盘 {med_s}，高开≥3% 占比 "
            f"{auc.get('high_open_share') if auc.get('high_open_share') is not None else '—'}%）"
        )
    else:
        bullets.append("竞价：尚未锁定（约 9:25 后写入）")

    etf_note = ""
    if board and board != "未明":
        if etf_mapped is False:
            etf_note = " · 无ETF映射只观察"
        elif carrier.get("code"):
            etf_note = f" · 载体 {carrier.get('name') or ''} {carrier.get('code')}"
    bullets.append(f"主线：{board}（{ml.get('status') or '—'}）{etf_note}".strip())

    if sim.get("bias") or sim.get("gate"):
        gate = sim.get("gate") or "—"
        bullets.append(
            f"相似日：{sim.get('bias') or '无倾向'}（gate={gate}，n={sim.get('n') or 0}）"
        )
    elif sim.get("note"):
        bullets.append(f"相似日：{sim.get('note')}")

    if risk.get("count"):
        bullets.append(
            f"仓位：{risk.get('count')} 只 · 浮盈 "
            f"{risk.get('pnl_pct') if risk.get('pnl_pct') is not None else '—'}%"
            + (f" · {risk.get('risk_note')}" if risk.get("risk_note") else "")
        )
    else:
        bullets.append("仓位：空仓 / 未记账")

    checklist = [
        pb.get("do") or (pb.get("lines") or [None])[0] or "先认主线，不追尖",
        pb.get("dont") or (pb.get("lines") or [None, None])[1] or "不抄冷门、不摊平",
        pb.get("size") or (pb.get("lines") or [None, None, None])[2] or "按风险%算股数",
    ]
    if action == "观望":
        focus = "今早默认：只看不买，等主线与竞价定调。"
    elif action == "观察回踩":
        focus = "今早默认：认主线但等回踩，不在竞价/开盘尖上追。"
    elif action in ("可买入", "可小仓"):
        focus = "今早默认：优先映射 ETF 回踩试错；个股须过日线+分时确认。"
    else:
        focus = f"今早默认：按结论「{action}」执行 Playbook。"

    title = f"早决策摘要 · {snap.get('trade_date') or '—'}"
    as_of = snap.get("updated_at")
    lines_md = [
        f"# {title}",
        "",
        f"_更新于 {as_of or '—'} · 时段 {(seg.get('label') or seg.get('key') or '—')}_",
        "",
        f"**焦点：** {focus}",
        "",
        "## 盘面要点",
    ]
    for b in bullets:
        lines_md.append(f"- {b}")
    lines_md.extend(
        [
            "",
            "## 执行清单",
            f"- 能做：{checklist[0]}",
            f"- 不做：{checklist[1]}",
            f"- 仓位：{checklist[2]}",
            "",
            "---",
            "_由 market-desk 规则生成，仅供盘前对照，不构成投资建议。_",
        ]
    )
    markdown = "\n".join(lines_md)
    return {
        "title": title,
        "as_of": as_of,
        "segment": seg.get("label") or seg.get("key") or "",
        "focus": focus,
        "bullets": bullets,
        "checklist": {"do": checklist[0], "dont": checklist[1], "size": checklist[2]},
        "action": action,
        "phase": phase,
        "mainline": board,
        "markdown": markdown,
    }


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
        and str(r.get("trade_date") or "")
        == str(today.get("date") or snap.get("trade_date") or "")
    ]
    if traded:
        lines.extend(["", "## 今日已交易"])
        for r in traded[:20]:
            lines.append(
                f"- {'买' if r.get('signal_type') == 'buy' else '卖'} "
                f"{r.get('name') or ''} {r.get('code')} "
                f"建议 {r.get('price')} 成交 {r.get('fill_price') or '—'}×{r.get('fill_qty') or '—'}"
            )
    brief = snap.get("morning_brief") or build_morning_brief(snap)
    if brief.get("focus"):
        lines[1:1] = [
            "",
            "## 早决策摘要（对照）",
            f"- 焦点：{brief.get('focus')}",
            *[f"- {b}" for b in (brief.get("bullets") or [])[:6]],
        ]
    lines.extend(["", "---", "_由 market-desk 自动生成，仅供复盘，不构成投资建议。_"])
    return "\n".join(lines)
