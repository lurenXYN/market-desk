"""人民币折算：把美元计价的外盘商品换算成人民币价格，并带上美元兑人民币汇率.

腾讯 ``qt.gtimg.cn`` 的 ``whUSDCNY`` 提供在岸美元人民币报价；商品 ``hf_*`` 报价是美元计价：
黄金/白银按金衡盎司（31.1035 克），原油按桶，伦铜按吨。换算只做单位与汇率，不改原始报价，
原始价与汇率都随展示一起给出，便于对账。
"""

from __future__ import annotations

from typing import Any, Sequence

FX_SYMBOL = "whUSDCNY"
OUNCE_GRAMS = 31.1035

# sym -> (unit, multiplier applied after fx, decimals)
COMMODITY_UNITS: dict[str, tuple[str, float, int]] = {
    "hf_GC": ("元/克", 1.0 / OUNCE_GRAMS, 2),
    "hf_SI": ("元/千克", 1000.0 / OUNCE_GRAMS, 0),
    "hf_CL": ("元/桶", 1.0, 2),
    "hf_OIL": ("元/桶", 1.0, 2),
    "hf_CAD": ("元/吨", 1.0, 0),
}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def parse_fx(body: str | None) -> dict[str, Any] | None:
    """Parse a ``qt.gtimg.cn`` ``whUSDCNY`` body into rate / prev / pct / asof."""
    text = str(body or "").strip()
    if not text:
        return None
    fields = text.split("~")
    if len(fields) < 7:
        return None
    price = _num(fields[3])
    prev = _num(fields[6]) if len(fields) > 6 else None
    if price is None:
        return None
    asof = str(fields[5] or "").strip()
    pct = None
    if prev:
        pct = round((price / prev - 1.0) * 100.0, 2)
    return {"usdcny": round(price, 4), "prev": prev, "pct": pct, "asof": asof}


def cny_prices(
    items: Sequence[dict[str, Any]] | None,
    rate: float | None,
) -> list[dict[str, Any]]:
    """Convert USD-quoted commodity rows into CNY prices using ``rate``."""
    fx = _num(rate)
    if fx is None:
        return []
    out: list[dict[str, Any]] = []
    for row in items or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("sym") or "")
        spec = COMMODITY_UNITS.get(sym)
        usd = _num(row.get("price"))
        if not spec or usd is None:
            continue
        unit, mult, digits = spec
        value = round(usd * fx * mult, digits)
        out.append(
            {
                "sym": sym,
                "name": row.get("name") or row.get("code") or sym,
                "unit": unit,
                "value": value,
                "usd": round(usd, 2),
            }
        )
    return out


def cny_line(fx: dict[str, Any] | None, rows: Sequence[dict[str, Any]] | None) -> str:
    """Compact one-line summary for the card, e.g. ``USDCNY 6.6636 · 黄金 452.10 元/克``."""
    bits: list[str] = []
    if fx and fx.get("usdcny"):
        pct = fx.get("pct")
        tail = "" if pct is None else f"（{pct:+.2f}%）"
        bits.append(f"USDCNY {fx['usdcny']:.4f}{tail}")
    for row in list(rows or [])[:5]:
        bits.append(f"{row.get('name')} {row.get('value')} {row.get('unit')}")
    return " · ".join(bits)
