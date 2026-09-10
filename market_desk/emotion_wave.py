"""A-share emotion-structure waves (observe-only; more local than Elliott).

Maps live sentiment metrics into five classic short-term emotion stages and
lists every stage with a forward path + invalidation. Does not change desk buys.
"""

from __future__ import annotations

from typing import Any


_STAGES: tuple[dict[str, Any], ...] = (
    {
        "id": "thaw",
        "step": 1,
        "title": "冰点修复",
        "bias": "repair",
        "path": "跌停收敛、温度回升；宜轻仓试错主线ETF/最强修复方向，忌抄跌停地天。",
        "invalid": "跌停再度放大或指数破位加速 → 仍处恐慌，修复浪作废。",
    },
    {
        "id": "confirm",
        "step": 2,
        "title": "主线确认",
        "bias": "bull",
        "path": "低位连板/辨认主线；优先精确映射 ETF 回踩，个股等确认中+回撤。",
        "invalid": "主线一日游且晋级塌陷 → 回到修复或假突破。",
    },
    {
        "id": "spread",
        "step": 3,
        "title": "扩散加速",
        "bias": "bull",
        "path": "晋级与跟风变多；可顺着主线做回踩扩散，仍防追尖峰龙头。",
        "invalid": "高度快速拔高但梯队断层/炸板升 → 加速变拥挤前兆。",
    },
    {
        "id": "crowd",
        "step": 4,
        "title": "高潮拥挤",
        "bias": "hot",
        "path": "溢价高、高度高；默认降仓、只做核心兑现，不新开拥挤题材。",
        "invalid": "溢价转负且大面/炸板恶化 → 已切退潮，勿当高潮持有。",
    },
    {
        "id": "fade",
        "step": 5,
        "title": "退潮切换",
        "bias": "bear",
        "path": "主线退潮或切换；先减旧主线相关仓，等新主线确认再动手。",
        "invalid": "新主线快速确认中且晋级修复 → 可能结束退潮进入确认浪。",
    },
)


def build_emotion_wave(
    *,
    phase: str | None,
    temperature: int | float | None,
    metrics: dict[str, Any] | None,
    mainline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score all five emotion stages; highlight the best-fit current stage."""
    m = metrics or {}
    ml = mainline or {}
    phase_s = str(phase or "").strip()
    temp = int(temperature or 0)
    scored: list[dict[str, Any]] = []
    for st in _STAGES:
        fit, why = _score_stage(st["id"], phase_s, temp, m, ml)
        scored.append(
            {
                **st,
                "fit": fit,
                "fit_note": why,
            }
        )
    scored.sort(key=lambda x: (-int(x.get("fit") or 0), int(x.get("step") or 0)))
    primary = scored[0] if scored else None
    # Keep display order as wave steps 1→5 for the strip, but mark primary.
    by_step = sorted(scored, key=lambda x: int(x.get("step") or 0))
    return {
        "ok": True,
        "standalone": True,
        "title": "情绪浪（大A结构）",
        "phase": phase_s,
        "temperature": temp,
        "primary": {
            "id": primary.get("id"),
            "title": primary.get("title"),
            "fit": primary.get("fit"),
            "step": primary.get("step"),
        }
        if primary
        else None,
        "stages": by_step,
        "ranked": scored,
        "note": (
            f"当前相位 {phase_s or '—'} · 温度 {temp} · "
            f"最高契合「{(primary or {}).get('title') or '—'}」"
        ),
        "disclaimer": "情绪浪用涨停结构/溢价/晋级等本地指标；仅观察，不改可买入/ready。",
    }


def _score_stage(
    sid: str,
    phase: str,
    temp: int,
    m: dict[str, Any],
    ml: dict[str, Any],
) -> tuple[int, str]:
    zt = int(m.get("zt") or 0)
    dt = int(m.get("dt") or 0)
    height = int(m.get("height") or 0)
    promo = float(m.get("promotion") or 0)
    prem = float(m.get("premium") or 0)
    zb = float(m.get("zb_rate") or 0)
    ladder_gap = bool(m.get("ladder_gap"))
    status = str(ml.get("status") or "")
    life = str(ml.get("lifecycle") or "")
    score = 15
    why = "一般"

    if sid == "thaw":
        if phase == "恐慌" or dt >= 25 or (zt <= 15 and prem < 0):
            score = 70
            why = "恐慌/跌停多/溢价弱，偏冰点修复"
        elif temp < 35 and prem <= 1:
            score = 55
            why = "温度偏低，修复浪候选"
        elif phase == "分歧" and prem < 0:
            score = 40
            why = "分歧且昨停溢价弱"
        else:
            why = "未明显冰点"
    elif sid == "confirm":
        if phase in ("分歧", "发酵") and status == "确认中" and height <= 4:
            score = 72
            why = "主线确认中且高度不高"
        elif phase == "发酵" and 20 <= zt <= 55 and promo >= 18:
            score = 60
            why = "发酵中晋级尚可，像主线确认"
        elif status == "确认中":
            score = 48
            why = "有确认中主线"
        else:
            why = "主线确认特征一般"
    elif sid == "spread":
        if phase == "发酵" and promo >= 25 and height >= 3 and not ladder_gap:
            score = 74
            why = "发酵+晋级抬升，扩散加速"
        elif phase == "发酵" and zt >= 40 and prem >= 1:
            score = 58
            why = "涨停与溢价同步抬"
        elif life == "ongoing" and phase in ("发酵", "分歧"):
            score = 45
            why = "主线主升生命周期"
        else:
            why = "扩散证据不足"
    elif sid == "crowd":
        if phase == "高潮" or (height >= 6 and prem >= 3 and zt >= 50):
            score = 78
            why = "高潮或高度/溢价拥挤"
        elif height >= 5 and zb >= 35:
            score = 55
            why = "高度不低且炸板升，拥挤风险"
        elif phase == "发酵" and height >= 5 and prem >= 4:
            score = 50
            why = "发酵末段溢价偏高"
        else:
            why = "拥挤特征不强"
    elif sid == "fade":
        if status == "退潮" or life == "ending":
            score = 76
            why = "主线退潮/生命周期衰退"
        elif phase == "高潮" and (prem < 0 or zb >= 45):
            score = 62
            why = "高潮后溢价/炸板恶化，像切退潮"
        elif phase == "分歧" and promo < 15 and height >= 4:
            score = 48
            why = "高位分歧且晋级弱"
        else:
            why = "退潮证据一般"

    return max(0, min(100, score)), why
