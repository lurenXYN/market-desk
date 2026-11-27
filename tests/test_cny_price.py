"""人民币折算与美元人民币汇率。"""

from __future__ import annotations

import pytest

from market_desk.cny_price import cny_line, cny_prices, parse_fx

BODY = (
    "310~美元人民币~USDCNY~6.6636~0~20261127104535~6.6600~6.6603~6.6639~6.6590~"
    "6.6636~6.6638~0.0036~0.05~-0.12~-0.11~0.14~-0.84~-4.63~7.0832~6.6449"
)

ITEMS = [
    {"sym": "hf_GC", "name": "黄金", "price": 2000.0},
    {"sym": "hf_SI", "name": "白银", "price": 30.0},
    {"sym": "hf_CL", "name": "WTI原油", "price": 80.0},
    {"sym": "hf_CAD", "name": "伦铜", "price": 9000.0},
    {"sym": "hkHSI", "name": "恒生", "price": 25000.0},
]


def test_parse_fx_reads_rate_and_pct():
    fx = parse_fx(BODY)
    assert fx["usdcny"] == 6.6636
    assert fx["prev"] == 6.66
    assert fx["pct"] == 0.05
    assert fx["asof"] == "20261127104535"


def test_parse_fx_rejects_garbage():
    assert parse_fx("") is None
    assert parse_fx('v_pv_none_match="1"') is None


def test_cny_prices_convert_units():
    rows = {r["sym"]: r for r in cny_prices(ITEMS, 7.0)}
    assert set(rows) == {"hf_GC", "hf_SI", "hf_CL", "hf_CAD"}  # index row skipped
    assert rows["hf_GC"]["value"] == pytest.approx(450.11, abs=0.01)   # 元/克
    assert rows["hf_GC"]["unit"] == "元/克"
    assert rows["hf_SI"]["value"] == 6752                              # 元/千克
    assert rows["hf_CL"]["value"] == 560.0                             # 元/桶
    assert rows["hf_CAD"]["value"] == 63000.0                          # 元/吨


def test_missing_rate_yields_no_fake_prices():
    assert cny_prices(ITEMS, None) == []
    assert cny_prices(ITEMS, 0) == []


def test_cny_line_carries_rate_and_prices():
    fx = {"usdcny": 6.6636, "pct": 0.05}
    line = cny_line(fx, cny_prices(ITEMS, 6.6636))
    assert "USDCNY 6.6636" in line and "+0.05%" in line
    assert "黄金" in line and "元/克" in line
    assert cny_line(None, []) == ""
