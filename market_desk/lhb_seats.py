"""Heuristic dragon-tiger seat style labels from common market lore.

Labels are experience tags (not exchange classifications). Prefer hard
facts (buy/sell amounts) in the UI; treat styles as secondary hints.
"""

from __future__ import annotations

from typing import Any


# Ordered rules: first match wins. Patterns are substrings of 营业部全称.
# style keys stay stable for CSS / filters.
_SEAT_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    # Official / channel seats
    (("机构专用",), "institution", "机构", "交易所披露「机构专用」席位"),
    (("沪股通专用", "深股通专用", "港股通专用", "沪股通", "深股通"), "northbound", "北向", "互联互通通道席位"),
    (("券商自营", "自营专用"), "prop", "自营", "券商自营相关席位（名称匹配）"),
    (("资管", "理财产品", "基金专用"), "product", "产品", "资管/产品类席位（名称匹配）"),
    # Widely cited "凶席 / 砸盘" branches (community lore)
    (("华泰证券股份有限公司上海分公司",), "smash", "砸盘王", "常见凶席口碑：华泰上海"),
    (("中信证券股份有限公司上海溧阳路",), "smash", "砸盘王", "常见凶席口碑：中信溧阳路"),
    (("中信证券股份有限公司总部", "中信证券股份有限公司北京总部"), "smash", "砸盘王", "常见凶席口碑：中信总部"),
    (("中国国际金融股份有限公司上海分公司", "中金公司上海"), "smash", "砸盘王", "常见凶席口碑：中金上海"),
    (("中国中金财富证券", "中金财富"), "smash", "砸盘王", "常见凶席口碑：中金财富系"),
    # Quant / algo-adjacent lore (soft): some electronic / remote desks
    (("东方财富证券股份有限公司拉萨", "东方财富证券拉萨"), "quant", "量化倾向", "网友常归为量化/线上席（经验）"),
    (("华泰证券股份有限公司深圳益田路",), "quant", "量化倾向", "大额进出频繁，常被归为量化通道（经验）"),
    (("中信建投证券股份有限公司北京东城分公司",), "quant", "量化倾向", "常见量化相关口碑（经验）"),
    # Famous 游资 nicknames (community)
    (("国泰君安证券股份有限公司南京太平南路",), "hot_money", "赵老哥", "知名游资席位口碑"),
    (("国泰君安证券股份有限公司成都北一环路",), "hot_money", "章盟主", "知名游资席位口碑"),
    (("国泰君安证券股份有限公司上海江苏路",), "hot_money", "知名游资", "江苏路系游资口碑"),
    (("国泰君安证券股份有限公司深圳益田路",), "hot_money", "小鳄鱼", "知名游资席位口碑"),
    (("中信证券股份有限公司杭州延安路",), "hot_money", "杭州帮", "杭州延安路游资口碑"),
    (("财通证券股份有限公司杭州上塘路",), "hot_money", "财通帮", "杭州上塘路游资口碑"),
    (("中国银河证券股份有限公司北京中关村大街",), "hot_money", "知名游资", "中关村大街游资口碑"),
    (("光大证券股份有限公司宁波解放南路", "光大证券股份有限公司宁波桑田路"), "hot_money", "宁波系", "宁波游资席位口碑"),
    (("湘财证券股份有限公司上海陆家嘴",), "hot_money", "知名游资", "陆家嘴游资口碑"),
    (("申万宏源证券有限公司上海陆家嘴",), "hot_money", "知名游资", "陆家嘴游资口碑"),
    (("招商证券股份有限公司深圳蛇口",), "hot_money", "知名游资", "蛇口游资口碑"),
    (("国盛证券有限责任公司宁波桑田路",), "hot_money", "宁波桑田路", "著名游资席位口碑"),
    (("国泰君安证券股份有限公司上海分公司",), "hot_money", "知名游资", "国君上海分公司口碑"),
)


def classify_seat(name: str | None) -> dict[str, Any]:
    """Return style metadata for one营业部 name.

    Unknown seats get ``broker`` / 「营业部」 so the UI stays honest.
    """
    raw = str(name or "").strip()
    if not raw:
        return {
            "name": "",
            "style": "unknown",
            "label": "未知",
            "note": "席位名为空",
            "alias": None,
        }
    for patterns, style, label, note in _SEAT_RULES:
        for pat in patterns:
            if pat and pat in raw:
                alias = label if style == "hot_money" and label not in ("知名游资",) else None
                return {
                    "name": raw,
                    "style": style,
                    "label": label,
                    "note": note,
                    "alias": alias,
                }
    # Soft keyword fallbacks
    low = raw
    if "量化" in low or "程序化" in low:
        return {
            "name": raw,
            "style": "quant",
            "label": "量化倾向",
            "note": "名称含量化相关字样",
            "alias": None,
        }
    if "机构" in low:
        return {
            "name": raw,
            "style": "institution",
            "label": "机构相关",
            "note": "名称含机构字样（非「机构专用」）",
            "alias": None,
        }
    return {
        "name": raw,
        "style": "broker",
        "label": "营业部",
        "note": "未归入常见席位词典",
        "alias": None,
    }


def style_brief(styles: list[str]) -> str:
    """Join unique style labels for a one-line summary."""
    order = [
        "institution",
        "northbound",
        "smash",
        "quant",
        "hot_money",
        "prop",
        "product",
        "broker",
        "unknown",
    ]
    labels = {
        "institution": "机构",
        "northbound": "北向",
        "smash": "砸盘王",
        "quant": "量化倾向",
        "hot_money": "游资",
        "prop": "自营",
        "product": "产品",
        "broker": "营业部",
        "unknown": "未知",
    }
    seen: list[str] = []
    for key in order:
        if key in styles and labels[key] not in seen:
            seen.append(labels[key])
    return " · ".join(seen[:4]) if seen else "—"
