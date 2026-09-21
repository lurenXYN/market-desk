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
        if ml.get("etf_soft"):
            etf_note = (
                f" · 近似ETF {carrier.get('name') or ''} {carrier.get('code') or ''}".rstrip()
                + "（只观察回踩）"
            )
        elif etf_mapped is False:
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

    season = snap.get("seasonality") or {}
    desk_season = season.get("desk") or {}
    if desk_season.get("brief"):
        bullets.append(str(desk_season["brief"]))
    elif desk_season.get("line"):
        bullets.append(str(desk_season["line"]))
    elif season.get("active"):
        titles = [
            str(w.get("title") or "")
            for w in (season.get("active") or [])
            if w.get("title")
        ]
        if titles:
            bullets.append("日历：" + " · ".join(titles[:3]))

    if risk.get("count"):
        bullets.append(
            f"仓位：{risk.get('count')} 只 · 浮盈 "
            f"{risk.get('pnl_pct') if risk.get('pnl_pct') is not None else '—'}%"
            + (f" · {risk.get('risk_note')}" if risk.get("risk_note") else "")
        )
    else:
        bullets.append("仓位：空仓 / 未记账")

    nr = snap.get("news_radar") or {}
    try:
        from market_desk.news_radar import news_radar_brief_lines

        for line in news_radar_brief_lines(nr, max_n=3):
            bullets.append(line)
    except Exception:
        pass

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
        f"# 牛来-作战台日报 · {today.get('date') or snap.get('trade_date') or '—'}",
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
                f"- {'卖' if r.get('signal_type') == 'sell' else '买'} "
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


def _fmt_money(v: Any) -> str:
    """Format a P&L number as signed yuan text, or em dash when missing."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{n:+.2f}" if n != 0 else "0.00"


def _eod_day_pnl(pos: dict[str, Any], snap: dict[str, Any]) -> Any:
    """Resolve day P&L from position summary, falling back to row sums."""
    day_pnl = pos.get("day_pnl")
    if day_pnl is not None:
        return day_pnl
    rows = snap.get("positions") or []
    parts = [r for r in rows if isinstance(r, dict) and r.get("day_pnl") is not None]
    if parts:
        return round(sum(float(r.get("day_pnl") or 0) for r in parts), 2)
    return None


def _eod_compress_switches(
    switches: list[dict[str, Any]],
    *,
    board: str,
    switch_n: int,
    switch_guard: dict[str, Any] | None = None,
) -> str | None:
    """Compress mainline flips into net-guard / final board + at most two flips."""
    sg = switch_guard or {}
    flips: list[str] = []
    for s in switches:
        if not isinstance(s, dict):
            continue
        fr = str(s.get("from_name") or "").strip() or "—"
        to = str(s.get("to_name") or "").strip() or "—"
        if fr == to:
            continue
        at = str(s.get("switched_at") or "")[11:16]
        flips.append(f"{at} {fr}→{to}" if at else f"{fr}→{to}")
        if len(flips) >= 2:
            break
    if not flips and not int(switch_n or 0) and not sg.get("active"):
        return None
    chron = [s for s in reversed(switches) if isinstance(s, dict)]
    first_from = ""
    for s in chron:
        first_from = str(s.get("from_name") or "").strip()
        if first_from:
            break
    final = board or "未明"
    for s in switches:
        if isinstance(s, dict) and str(s.get("to_name") or "").strip():
            final = str(s.get("to_name")).strip()
            break
    if sg.get("active") and sg.get("from_name"):
        net = f"净换防 {sg.get('from_name')}→{sg.get('to_name') or final}"
    elif first_from and first_from != final:
        net = f"净换防 {first_from}→{final}"
    else:
        net = f"最终主线 {final}"
    bits = [f"{net}（切换 {int(switch_n or 0)} 次）"]
    if flips:
        bits.append("关键翻转：" + "；".join(flips))
    return " · ".join(bits)


def _eod_tip_peak_line(
    *,
    board: str,
    status: str,
    lifecycle: str,
    action: str,
    reason: str = "",
) -> str | None:
    """One-liner explaining tip-peak / main-up vs observe-pullback posture."""
    life = str(lifecycle or "").strip()
    st = str(status or "").strip()
    act = str(action or "").strip()
    life_zh = {"starting": "萌芽", "ongoing": "主升", "ending": "衰退"}.get(life, life)
    why = str(reason or "").strip()
    if len(why) > 36:
        why = why[:36] + "…"
    if st == "尖峰禁追" or (life == "ongoing" and st in ("尖峰禁追", "禁追")):
        base = f"尖峰主升 vs 观察回踩：{board or '主线'} 处主升·尖峰，只观察回踩不当现买"
        return f"{base}（{why}）" if why else base
    if act == "观察回踩":
        base = f"观察回踩：{board or '主线'}（{st or life_zh or '—'}）等回踩/价带"
        return f"{base} — {why}" if why else base
    if life == "ongoing" and act in ("可买入", "可小仓"):
        return f"主升可做：{board or '主线'} 认回踩/价带，不追尖峰"
    if st == "退潮" or life == "ending":
        return f"主线偏退：{board or '主线'}（{st or life_zh}）优先防旧仓，不新开追高"
    return None


def _eod_diary_line(diary: list[dict[str, Any]] | None) -> str | None:
    """Summarize today's exec-diary fills into one short bullet."""
    rows = [r for r in (diary or []) if isinstance(r, dict)]
    if not rows:
        return None
    buys = [r for r in rows if str(r.get("side") or "").lower() in ("buy", "买入", "")]
    sells = [r for r in rows if str(r.get("side") or "").lower() in ("sell", "卖", "trim", "减仓", "清仓")]
    # Empty side defaults to buy only when not clearly sell-tagged.
    if not buys and not sells:
        buys = list(rows)

    def _bit(r: dict[str, Any]) -> str:
        nm = r.get("name") or r.get("code") or "—"
        px = r.get("price")
        qty = r.get("qty")
        if px is not None and qty is not None:
            return f"{nm}@{px}×{qty}"
        if px is not None:
            return f"{nm}@{px}"
        return str(nm)

    bits: list[str] = []
    if buys:
        bits.append("买 " + " / ".join(_bit(r) for r in buys[:4]))
    if sells:
        bits.append("卖 " + " / ".join(_bit(r) for r in sells[:3]))
    if not bits:
        return None
    extra = ""
    n = len(rows)
    shown = min(n, 4 if not sells else 7)
    if n > shown:
        extra = f" 等共{n}笔"
    return "成交日记：" + "；".join(bits) + extra


def _eod_worth_review_buys(
    missed: list[dict[str, Any]],
    signals: list[dict[str, Any]] | None,
    *,
    day: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Pick up to ``limit`` missed / untraded buys most worth an EOD review."""
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _key(row: dict[str, Any]) -> str:
        return str(row.get("code") or row.get("name") or "").zfill(6)

    for m in missed:
        if not isinstance(m, dict):
            continue
        k = _key(m)
        if not k or k in seen:
            continue
        seen.add(k)
        picked.append(m)
        if len(picked) >= limit:
            return picked

    day_s = str(day or "")[:10]
    cands: list[tuple[float, dict[str, Any]]] = []
    for row in signals or []:
        if not isinstance(row, dict):
            continue
        if day_s and str(row.get("trade_date") or "")[:10] != day_s:
            continue
        st = str(row.get("signal_type") or "")
        if not (st == "buy" or st.startswith("buy_")):
            continue
        if int(row.get("traded") or 0) or int(row.get("skipped") or 0):
            continue
        k = _key(row)
        if not k or k in seen:
            continue
        try:
            dev = abs(float(row.get("dev_pct"))) if row.get("dev_pct") is not None else 0.0
        except (TypeError, ValueError):
            dev = 0.0
        score = 0.0
        flags = row.get("price_flags") or []
        if "miss_pullback" in flags or "未回踩" in str(row.get("price_mark") or ""):
            score += 30
        if row.get("ready"):
            score += 20
        score += min(dev, 15.0)
        cands.append((score, row))
    cands.sort(key=lambda x: -x[0])
    for _, row in cands:
        k = _key(row)
        seen.add(k)
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def _eod_curate_push(bullets: list[str], *, cap: int = 8) -> list[str]:
    """Pick a push-sized subset that always keeps the day P&L bullet first."""
    if not bullets:
        return []
    pnl = [b for b in bullets if str(b).startswith("今日盈亏")]
    rest = [b for b in bullets if not str(b).startswith("今日盈亏")]
    ordered = pnl + rest
    return ordered[: max(1, int(cap))]


def build_eod_onepager(
    *,
    snapshot: dict[str, Any] | None,
    review: dict[str, Any] | None,
    diary: list[dict[str, Any]] | None = None,
    tomorrow_brief: dict[str, Any] | None = None,
    tune_hints: list[str] | None = None,
) -> dict[str, Any]:
    """Build a compact end-of-day one-pager for copy / archive / Server酱.

    Prioritizes day P&L and holdings, focus action, diary fills, tip-peak vs
    pullback, compressed mainline flips, worth-review misses, tomorrow watch,
    and one tune hint. Rule-based only; no LLM.
    """
    snap = snapshot or {}
    rev = review or {}
    summary = rev.get("summary") or {}
    today = summary.get("today") or {}
    exec_score = summary.get("exec") or today.get("exec") or {}
    missed = list(summary.get("missed_buys") or [])
    hints = list(tune_hints) if tune_hints is not None else list(summary.get("tune_hints") or [])
    diary_rows = list(diary) if diary is not None else list(summary.get("exec_diary") or [])
    verdict = snap.get("verdict") or {}
    ml = verdict.get("mainline") or {}
    pos = snap.get("position_summary") or {}
    day = today.get("date") or snap.get("trade_date") or "—"
    phase = snap.get("phase") or today.get("phase") or "—"
    board = ml.get("name") or "未明"
    action = verdict.get("action") or "—"
    status = str(ml.get("status") or "")
    lifecycle = str(ml.get("lifecycle") or "")
    reason = str(verdict.get("reason") or ml.get("reason") or "")

    float_pnl = pos.get("floating_pnl")
    if float_pnl is None:
        float_pnl = pos.get("pnl")
    realized = pos.get("realized_pnl")
    if realized is None:
        realized = 0
    day_pnl = _eod_day_pnl(pos, snap)

    switches = list(snap.get("mainline_switches") or [])
    switch_n = today.get("switch_n")
    if switch_n is None:
        switch_n = len(switches)

    pos_n = int(pos.get("count") or 0)
    pnl_line = (
        f"今日盈亏 {_fmt_money(day_pnl)}（相对昨收/今日买价；浮盈 {_fmt_money(float_pnl)}"
        f" · 已实现 {_fmt_money(realized)}）"
        + (f" · {pos.get('day_pnl_pct')}%" if pos.get("day_pnl_pct") is not None else "")
    )
    if pos_n:
        pnl_line += f" · 持仓 {pos_n} 只"
        note = pos.get("risk_note")
        if note:
            pnl_line += f"（{note}）"

    bullets: list[str] = [pnl_line]

    diary_line = _eod_diary_line(diary_rows)
    if diary_line:
        bullets.append(diary_line)
    else:
        traded_n = int(today.get("traded_n") or exec_score.get("traded_buy_n") or 0)
        if traded_n:
            bullets.append(
                f"成交：计划内已交易 {traded_n}"
                + (
                    f" · 执行分 {exec_score.get('score')}"
                    if exec_score.get("score") is not None
                    else ""
                )
            )

    tip_line = _eod_tip_peak_line(
        board=board,
        status=status,
        lifecycle=lifecycle,
        action=action,
        reason=reason,
    )
    if tip_line:
        bullets.append(tip_line)

    switch_line = _eod_compress_switches(
        switches,
        board=board,
        switch_n=int(switch_n or 0),
        switch_guard=verdict.get("switch_guard") if isinstance(verdict.get("switch_guard"), dict) else None,
    )
    if switch_line:
        bullets.append(switch_line)
    else:
        bullets.append(f"最终主线 {board}（{status or '—'}）· 切换 {int(switch_n or 0)} 次")

    worth = _eod_worth_review_buys(
        missed,
        rev.get("signals") if isinstance(rev.get("signals"), list) else None,
        day=str(day),
        limit=3,
    )
    if worth:
        bits = [
            f"{m.get('name') or m.get('code')}@{m.get('price') or '—'}"
            for m in worth
        ]
        label = "漏买/宜复盘" if missed else "未交易宜复盘"
        bullets.append(f"{label} {len(worth)}：{' / '.join(bits)}")

    tm_brief = tomorrow_brief
    if tm_brief is None and (snap or rev):
        try:
            tm_brief = build_tomorrow_brief(snapshot=snap, review=rev)
        except Exception:
            tm_brief = None
    if isinstance(tm_brief, dict):
        tm_focus = str(tm_brief.get("focus") or "").strip()
        if tm_focus:
            if len(tm_focus) > 72:
                tm_focus = tm_focus[:72] + "…"
            bullets.append(f"明日看点：{tm_focus}")

    if hints:
        h0 = str(hints[0] or "").strip()
        if h0:
            if len(h0) > 64:
                h0 = h0[:64] + "…"
            bullets.append(f"调参：{h0}")

    # Secondary / shortened counts — keep after decision content.
    score_s = exec_score.get("score") if exec_score.get("score") is not None else "—"
    bullets.append(
        f"信号 买{today.get('buy_n', 0)}/卖{today.get('sell_n', 0)}"
        f" · 执行分 {score_s}"
        f" · 价带内 {exec_score.get('in_band_n', 0)}/追高 {exec_score.get('chase_n', 0)}"
        + (
            f" · 未回踩上行 {today.get('miss_pullback_n', 0)}"
            if today.get("miss_pullback_n")
            else ""
        )
    )
    bullets.append(
        f"相位 {phase}"
        + (f" · 温度 {snap.get('temperature')}" if snap.get("temperature") is not None else "")
        + f" · 结论 {action}"
    )

    if day_pnl is not None and float(day_pnl) < 0:
        focus = "收盘优先复盘失误与漏买，明日先降风险再谈进攻。"
    elif action in ("可买入", "可小仓"):
        focus = "收盘对照：今日有进攻窗口，核对价带内成交与仓位纪律。"
    elif missed or worth:
        focus = "收盘对照：有漏买/未交易宜复盘项，记下是否规则过严或执行犹豫。"
    else:
        focus = "收盘对照：盈亏/持仓、主线姿势与明日看点一张纸看完即可。"

    push_bullets = _eod_curate_push(bullets, cap=8)

    title = f"收盘一页纸 · {day}"
    as_of = snap.get("updated_at")
    lines_md = [
        f"# {title}",
        "",
        f"_更新于 {as_of or '—'}_",
        "",
        f"**焦点：** {focus}",
        "",
        "## 要点",
    ]
    for b in bullets:
        lines_md.append(f"- {b}")
    lines_md.extend(
        [
            "",
            "---",
            "_由 market-desk 规则生成，仅供复盘，不构成投资建议。_",
        ]
    )
    markdown = "\n".join(lines_md)
    return {
        "title": title,
        "as_of": as_of,
        "date": day,
        "focus": focus,
        "bullets": bullets,
        "push_bullets": push_bullets,
        "phase": phase,
        "mainline": board,
        "switch_n": switch_n,
        "exec": exec_score,
        "missed_n": len(missed),
        "day_pnl": day_pnl,
        "floating_pnl": float_pnl,
        "realized_pnl": realized,
        "markdown": markdown,
    }


def build_tomorrow_brief(
    *,
    snapshot: dict[str, Any] | None,
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an after-close 'tomorrow watch' brief (observe-only, no buy gates).

    Soft narrative only: risk-first sells, bans, mainline watch posture, similar-day
    bias, watchlist notes, and a few review hints. Never flips ready / action.
    """
    from market_desk.calendar import add_trading_days

    snap = snapshot or {}
    rev = review or {}
    summary = rev.get("summary") or {}
    today = summary.get("today") or {}
    verdict = snap.get("verdict") or {}
    ml = verdict.get("mainline") or {}
    pb = verdict.get("playbook") or {}
    sim = snap.get("similar_days") or {}
    pos = snap.get("position_summary") or snap.get("risk_overview") or {}
    sell = snap.get("sell_advice") or {}
    sell_items = [
        x for x in (sell.get("items") or [])
        if isinstance(x, dict)
    ]
    bans = [
        b for b in (verdict.get("bans") or [])
        if isinstance(b, dict) or isinstance(b, str)
    ]
    watch = [
        w for w in (snap.get("watchlist") or [])
        if isinstance(w, dict)
    ]
    missed = summary.get("missed_buys") or []
    hints = summary.get("tune_hints") or []
    day = str(today.get("date") or snap.get("trade_date") or "")[:10]
    next_day = None
    if day:
        try:
            next_day = add_trading_days(day, 1)
        except Exception:
            next_day = None
    next_s = next_day.isoformat() if next_day else "下一交易日"

    phase = snap.get("phase") or today.get("phase") or "—"
    action = verdict.get("action") or "观望"
    board = ml.get("name") or "未明"
    status = ml.get("status") or "—"

    risk_rows: list[str] = []
    for it in sell_items:
        urg = str(it.get("urgency") or it.get("action") or "").strip()
        if urg not in ("stop", "take", "trim", "止损", "止盈", "减仓", "清仓"):
            # Also accept ready sell cards.
            if not it.get("ready"):
                continue
        name = it.get("name") or it.get("code") or "—"
        why = it.get("reason") or it.get("note") or urg or "关注"
        risk_rows.append(f"{name}：{why}")
    if len(risk_rows) > 5:
        risk_rows = risk_rows[:5]

    ban_bits: list[str] = []
    for b in bans[:6]:
        if isinstance(b, str):
            ban_bits.append(b)
        else:
            ban_bits.append(str(b.get("name") or b.get("code") or b.get("reason") or b))

    bullets: list[str] = []
    bullets.append(f"对照日 {day or '—'} → 看点日 {next_s}")
    bullets.append(f"相位 {phase} · 结论 {action} · 主线 {board}（{status}）")

    if risk_rows:
        bullets.append("持仓风险优先：" + "；".join(risk_rows))
    elif pos.get("count"):
        bullets.append(
            f"持仓 {pos.get('count')} 只"
            + (f" · {pos.get('risk_note')}" if pos.get("risk_note") else " · 无紧急卖点，开盘先核对强弱")
        )
    else:
        bullets.append("持仓：空仓 — 开盘默认只看不买，等回踩再谈试错")

    if ban_bits:
        bullets.append("禁追/回避：" + " / ".join(ban_bits))

    do = pb.get("do") or "先认主线，不追尖"
    dont = pb.get("dont") or "不抄冷门、不摊平"
    bullets.append(f"主线观察：{do}；不做：{dont}（观察级，非可现买指令）")

    if sim.get("bias") or sim.get("gate"):
        bullets.append(
            f"相似日倾向：{sim.get('bias') or '—'}（gate={sim.get('gate') or '—'}，n={sim.get('n') or 0}）"
        )

    watch_bits: list[str] = []
    for w in watch[:5]:
        nm = w.get("name") or w.get("code") or "—"
        st = w.get("soft_status") or w.get("note") or w.get("band_note") or ""
        watch_bits.append(f"{nm}{(' · ' + st) if st else ''}")
    if watch_bits:
        bullets.append("自选观察：" + "；".join(watch_bits))

    if missed:
        bits = [
            f"{m.get('name') or m.get('code')}"
            for m in missed[:4]
        ]
        bullets.append("复盘漏买对照：" + " / ".join(bits) + " — 明日勿报复性追回")
    if hints:
        bullets.append("调参提示：" + str(hints[0]))

    # Soft posture score → focus line only.
    cool = str(sim.get("gate") or "").lower() in ("cool", "urgent", "block")
    has_risk = bool(risk_rows) or (
        pos.get("day_pnl") is not None and float(pos.get("day_pnl") or 0) < 0
    )
    if has_risk or cool or action == "观望":
        focus = f"{next_s}默认：先处理风险与禁追，主线只观察回踩，不在开盘尖上动手。"
        posture = "defend"
    elif action in ("可买入", "可小仓", "观察回踩"):
        focus = f"{next_s}默认：认主线 {board}，等回踩/价带；开盘不追高，清单外不新开。"
        posture = "watch_pullback"
    else:
        focus = f"{next_s}默认：按收盘结论「{action}」执行观察清单，先核对持仓再谈进攻。"
        posture = "neutral"

    title = f"明日看点 · {next_s}"
    as_of = snap.get("updated_at")
    lines_md = [
        f"# {title}",
        "",
        f"_基于 {day or '—'} 收盘快照 · 更新于 {as_of or '—'}_",
        "",
        f"**焦点：** {focus}",
        "",
        "## 要点",
    ]
    for b in bullets:
        lines_md.append(f"- {b}")
    lines_md.extend(
        [
            "",
            "---",
            "_由 market-desk 规则生成，仅供次日观察，不构成投资建议；不改可买入闸门。_",
        ]
    )
    return {
        "title": title,
        "as_of": as_of,
        "from_date": day,
        "next_date": next_s,
        "focus": focus,
        "posture": posture,
        "bullets": bullets,
        "phase": phase,
        "mainline": board,
        "action": action,
        "risk_n": len(risk_rows),
        "ban_n": len(ban_bits),
        "markdown": "\n".join(lines_md),
    }
