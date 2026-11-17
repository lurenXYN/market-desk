"""Explicit trading-philosophy profiles (策略档案).

The desk's rules grew out of one philosophy — 「只做主线回踩」 — but the
thresholds that encode it live scattered across ``config``/``settings``. This
module makes the philosophy a *selectable, documented* object instead of an
implicit one:

* ``conservative``  — 主线必须确认才做，回踩不到位宁可空仓；
* ``standard``      — 当前默认行为（与 v1.10.101 一致）；
* ``aggressive``    — 允许更早确认、更大单票上限，但仍禁止追高。

Nothing here mutates global config: callers read a profile and apply what they
need. That keeps existing defaults byte-identical until someone opts in.
"""

from __future__ import annotations

from typing import Any

from market_desk.settings import setting

PROFILE_NAMES = ("conservative", "standard", "aggressive")

PROFILES: dict[str, dict[str, Any]] = {
    "conservative": {
        "label": "保守",
        "philosophy": "主线未确认就不做；回踩不到位宁可空仓；单板块不押重注",
        "when_to_use": "连续亏损后、或大盘处于恐慌/退潮相位时手动切换",
        "invalidates_when": "主线确认后连续 5 个交易日无一张卡成交（说明过严，需回看闸门）",
        "gate": {
            "require_mainline_confirmed": True,
            "min_temperature": 45.0,
            "block_on_panic": True,
            "allow_probe": False,
            "min_zt_breadth": 4,
        },
        "size": {
            "risk_pct_per_trade": 0.10,
            "size_cap_pct": 20.0,
            "max_same_board_pct": 25.0,
            "max_positions": 3,
        },
        "entry": {
            "max_chase_pct": 0.0,
            "allow_breakout": False,
            "require_pullback": True,
        },
    },
    "standard": {
        "label": "标准",
        "philosophy": "跟主线、买回踩、用 ETF 承载高风险板块、开盘与午后更谨慎",
        "when_to_use": "默认档案；与历史回测口径一致",
        "invalidates_when": "连续 3 周命中率低于 35% 且样本 ≥30",
        "gate": {
            "require_mainline_confirmed": False,
            "min_temperature": 28.0,
            "block_on_panic": True,
            "allow_probe": True,
            "min_zt_breadth": 2,
        },
        "size": {
            "risk_pct_per_trade": 0.15,
            "size_cap_pct": 40.0,
            "max_same_board_pct": 50.0,
            "max_positions": 4,
        },
        "entry": {
            "max_chase_pct": 1.6,
            "allow_breakout": False,
            "require_pullback": True,
        },
    },
    "aggressive": {
        "label": "激进",
        "philosophy": "确认即做、允许小追，但仍不接受无回踩的高位接力",
        "when_to_use": "发酵相位、主线扩散明显、且账户处于盈利状态时",
        "invalidates_when": "组合回撤超过 8%，必须退回标准档",
        "gate": {
            "require_mainline_confirmed": False,
            "min_temperature": 20.0,
            "block_on_panic": True,
            "allow_probe": True,
            "min_zt_breadth": 1,
        },
        "size": {
            "risk_pct_per_trade": 0.20,
            "size_cap_pct": 60.0,
            "max_same_board_pct": 70.0,
            "max_positions": 5,
        },
        "entry": {
            "max_chase_pct": 2.5,
            "allow_breakout": True,
            "require_pullback": False,
        },
    },
}


def current_profile_name() -> str:
    """Active profile from settings (unknown values fall back to ``standard``)."""
    name = str(setting("strategy_profile", "standard") or "standard").strip().lower()
    return name if name in PROFILES else "standard"


def get_profile(name: str | None = None) -> dict[str, Any]:
    """Return one profile dict (never a shared mutable reference)."""
    key = str(name or current_profile_name()).strip().lower()
    if key not in PROFILES:
        key = "standard"
    prof = dict(PROFILES[key])
    prof["name"] = key
    for section in ("gate", "size", "entry"):
        prof[section] = dict(prof.get(section) or {})
    return prof


def profile_param(section: str, key: str, default: Any = None) -> Any:
    """Read ``profile[section][key]`` for the active profile."""
    return (get_profile().get(section) or {}).get(key, default)


def profile_summary() -> dict[str, Any]:
    """Payload for the UI/API: all profiles plus which one is active."""
    active = current_profile_name()
    return {
        "ok": True,
        "active": active,
        "active_label": (PROFILES.get(active) or {}).get("label") or active,
        "profiles": [
            {
                "name": name,
                "label": data["label"],
                "philosophy": data["philosophy"],
                "when_to_use": data["when_to_use"],
                "invalidates_when": data["invalidates_when"],
                "gate": dict(data["gate"]),
                "size": dict(data["size"]),
                "entry": dict(data["entry"]),
            }
            for name, data in PROFILES.items()
        ],
        "note": "档案只改读取到的阈值；未显式切换时一律按 standard，与历史口径一致",
    }