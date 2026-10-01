"""Static scenario, sub-wave, and wave-mark catalogs."""

from __future__ import annotations

from typing import Any


# Full catalog the UI always shows (ranked by fit).
_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "imp_up_w1",
        "family": "上升推动",
        "wave": "第1浪",
        "bias": "bull",
        "title": "上升推动 · 第1浪启动",
        "next": "第1浪常伴随缩量试探；若站稳，预期回撤第2浪（回吐30%–61.8%），再迎更强第3浪。",
        "risk": "跌破启动低点则本浪计数作废，改看下跌或更长调整。",
    },
    {
        "id": "imp_up_w2",
        "family": "上升推动",
        "wave": "第2浪",
        "bias": "bull",
        "title": "上升推动 · 第2浪回调",
        "next": "第2浪多为锯齿/平台回撤；不破第1浪起点则仍偏多。结束后第3浪往往加速。",
        "risk": "跌破第1浪起点→上升推动失效，优先改标下跌推动或调整C浪。",
    },
    {
        "id": "imp_up_w3",
        "family": "上升推动",
        "wave": "第3浪",
        "bias": "bull",
        "title": "上升推动 · 第3浪主升",
        "next": "第3浪通常不是最短浪，量能/斜率偏强。过后常见第4浪横盘或浅回撤，再第5浪末升。",
        "risk": "若此段幅度明显短于第1浪且随后深跌，需警惕失败结构或计数改标。",
    },
    {
        "id": "imp_up_w4",
        "family": "上升推动",
        "wave": "第4浪",
        "bias": "bull",
        "title": "上升推动 · 第4浪整理",
        "next": "第4浪常见平台/三角；理想不深入第1浪价格区间。结束后第5浪冲高，完成五浪。",
        "risk": "深度跌回第1浪区间或破第3浪起点→四浪失败，或整体改计调整浪。",
    },
    {
        "id": "imp_up_w5",
        "family": "上升推动",
        "wave": "第5浪",
        "bias": "bull",
        "title": "上升推动 · 第5浪末升",
        "next": "第5浪可创新高但动量常弱于第3浪；结束后进入A-B-C调整概率上升。",
        "risk": "无法过第3浪高点的「失败第5」→转弱更快，按已完成推动对待。",
    },
    {
        "id": "corr_a",
        "family": "调整浪",
        "wave": "A浪",
        "bias": "bear",
        "title": "调整 · A浪下跌",
        "next": "A浪打开调整空间；之后常见B浪反抽（回撤A浪38%–79%），再C浪下跌。",
        "risk": "若A浪很浅且迅速收复，可能只是上升推动内部的第2/第4浪。",
    },
    {
        "id": "corr_b",
        "family": "调整浪",
        "wave": "B浪",
        "bias": "neutral",
        "title": "调整 · B浪反抽",
        "next": "B浪反抽常诱多；不过前高或仅收回A浪大半。结束后C浪下跌往往更完整。",
        "risk": "强势收复A浪全部并站稳→可能不是调整B，而是新推动第1/第3浪。",
    },
    {
        "id": "corr_c",
        "family": "调整浪",
        "wave": "C浪",
        "bias": "bear",
        "title": "调整 · C浪下跌",
        "next": "C浪常与A浪等长或1.618倍；完成后若见底背离，可能开启新上升推动第1浪。",
        "risk": "跌破关键支撑后仍加速→调整可能演化为下跌推动，而非简单ABC。",
    },
    {
        "id": "imp_dn_w1",
        "family": "下跌推动",
        "wave": "第1浪",
        "bias": "bear",
        "title": "下跌推动 · 第1浪",
        "next": "下跌五浪的第一段；随后第2浪反抽（不破第1浪起点高点），再第3浪主跌。",
        "risk": "快速收复第1浪高点→下跌推动作废，改看上升调整B或新多头。",
    },
    {
        "id": "imp_dn_w2",
        "family": "下跌推动",
        "wave": "第2浪",
        "bias": "bear",
        "title": "下跌推动 · 第2浪反抽",
        "next": "第2浪反抽常诱多；不过前高则仍偏空。结束后第3浪主跌概率上升。",
        "risk": "突破第1浪起点高点→下跌五浪计数失败。",
    },
    {
        "id": "imp_dn_w3",
        "family": "下跌推动",
        "wave": "第3浪",
        "bias": "bear",
        "title": "下跌推动 · 第3浪主跌",
        "next": "主跌段斜率常陡；之后第4浪反抽，再第5浪寻底。",
        "risk": "若此段明显短于第1浪且迅速反包，计数需降权。",
    },
    {
        "id": "imp_dn_w4",
        "family": "下跌推动",
        "wave": "第4浪",
        "bias": "bear",
        "title": "下跌推动 · 第4浪反抽",
        "next": "第4浪反抽后仍看第5浪再下一台阶；完成五浪后或现较大级别反弹。",
        "risk": "反抽过深进入第1浪区间→下跌推动可信度下降。",
    },
    {
        "id": "imp_dn_w5",
        "family": "下跌推动",
        "wave": "第5浪",
        "bias": "bear",
        "title": "下跌推动 · 第5浪寻底",
        "next": "第5浪末跌后，常见较大级别反弹（新上升推动或调整反抽）。可盯背离与关键支撑。",
        "risk": "跌破关键位后仍无止跌结构→下跌可能延伸/扩张。",
    },
    {
        "id": "triangle",
        "family": "盘整",
        "wave": "三角/平台",
        "bias": "neutral",
        "title": "盘整 · 三角或平台整理",
        "next": "波动收敛、高低点交错；突破方向决定下一浪归属（常接在第4浪或B浪位置）。",
        "risk": "假突破后回到箱体很常见；需等收盘站稳再认方向。",
    },
    {
        "id": "complex",
        "family": "盘整",
        "wave": "复合调整",
        "bias": "neutral",
        "title": "盘整 · 复合/延长调整",
        "next": "时间换空间：W-X-Y 一类结构，方向反复。仓位上宜轻、等清晰五浪或ABC完成。",
        "risk": "在复合调整中追涨杀跌胜率差；无效化看箱体上下沿。",
    },
)


# Top1 id → how many waves to paint on the zigzag (end-of-wave labels).
_WAVE_MARK_SPEC: dict[str, dict[str, Any]] = {
    "imp_up_w1": {"mode": "impulse", "bull": True, "n": 1, "labels": ["1"]},
    "imp_up_w2": {"mode": "impulse", "bull": True, "n": 2, "labels": ["1", "2"]},
    "imp_up_w3": {"mode": "impulse", "bull": True, "n": 3, "labels": ["1", "2", "3"]},
    "imp_up_w4": {"mode": "impulse", "bull": True, "n": 4, "labels": ["1", "2", "3", "4"]},
    "imp_up_w5": {"mode": "impulse", "bull": True, "n": 5, "labels": ["1", "2", "3", "4", "5"]},
    "imp_dn_w1": {"mode": "impulse", "bull": False, "n": 1, "labels": ["1"]},
    "imp_dn_w2": {"mode": "impulse", "bull": False, "n": 2, "labels": ["1", "2"]},
    "imp_dn_w3": {"mode": "impulse", "bull": False, "n": 3, "labels": ["1", "2", "3"]},
    "imp_dn_w4": {"mode": "impulse", "bull": False, "n": 4, "labels": ["1", "2", "3", "4"]},
    "imp_dn_w5": {"mode": "impulse", "bull": False, "n": 5, "labels": ["1", "2", "3", "4", "5"]},
    "corr_a": {"mode": "abc", "n": 1, "labels": ["A"]},
    "corr_b": {"mode": "abc", "n": 2, "labels": ["A", "B"]},
    "corr_c": {"mode": "abc", "n": 3, "labels": ["A", "B", "C"]},
    "triangle": {"mode": "triangle", "n": 5, "labels": ["a", "b", "c", "d", "e"]},
}


_SUB_IMPULSE_UP: tuple[dict[str, Any], ...] = (
    {
        "id": "sub_i",
        "label": "子浪 i",
        "roman": "i",
        "path": "细级启动上冲；若站稳，常接 ii 回撤后再 iii 加速。",
        "risk": "跌破本段启动低点 → 子浪 i 草稿作废。",
    },
    {
        "id": "sub_ii",
        "label": "子浪 ii",
        "roman": "ii",
        "path": "细级回撤；理想不破 i 起点，结束后看 iii。",
        "risk": "跌破 i 起点 → 内部上升推动草稿失效。",
    },
    {
        "id": "sub_iii",
        "label": "子浪 iii",
        "roman": "iii",
        "path": "细级主升段，斜率/幅度常强于 i；过后多见 iv 整理。",
        "risk": "此段明显短于 i 且迅速回吐 → 降权或改标。",
    },
    {
        "id": "sub_iv",
        "label": "子浪 iv",
        "roman": "iv",
        "path": "细级整理/浅回撤；结束后看 v 末升。",
        "risk": "深度跌回 i 区间 → 内部四浪失败嫌疑。",
    },
    {
        "id": "sub_v",
        "label": "子浪 v",
        "roman": "v",
        "path": "细级末升；完成后母浪更可能进入下一段（回撤或调整）。",
        "risk": "无法过 iii 高点的失败 v → 转弱更快。",
    },
)


_SUB_IMPULSE_DN: tuple[dict[str, Any], ...] = (
    {
        "id": "sub_i",
        "label": "子浪 i",
        "roman": "i",
        "path": "细级首段下跌；之后常有 ii 反抽，再 iii 主跌。",
        "risk": "快速收复本段高点 → 下跌子浪草稿作废。",
    },
    {
        "id": "sub_ii",
        "label": "子浪 ii",
        "roman": "ii",
        "path": "细级反抽；不过前高则仍偏空，结束后看 iii。",
        "risk": "突破 i 起点高点 → 内部下跌推动草稿失效。",
    },
    {
        "id": "sub_iii",
        "label": "子浪 iii",
        "roman": "iii",
        "path": "细级主跌段，常加速创新低；过后多见 iv 反抽。",
        "risk": "跌幅明显短于 i 且迅速反包 → 降权。",
    },
    {
        "id": "sub_iv",
        "label": "子浪 iv",
        "roman": "iv",
        "path": "细级反抽整理；结束后看 v 再下一台阶。",
        "risk": "反抽过深进入 i 区间 → 可信度下降。",
    },
    {
        "id": "sub_v",
        "label": "子浪 v",
        "roman": "v",
        "path": "细级寻底段；完成后母浪更可能迎来反抽或更大级别转折。",
        "risk": "破位后仍无止跌 → 下跌可能延伸。",
    },
)


_SUB_ABC: tuple[dict[str, Any], ...] = (
    {
        "id": "sub_a",
        "label": "子浪 a",
        "roman": "a",
        "path": "细级调整第一段；之后常见 b 反抽，再 c 完成。",
        "risk": "很快被完全收复 → 可能不是调整 a，而是推动内部回撤。",
    },
    {
        "id": "sub_b",
        "label": "子浪 b",
        "roman": "b",
        "path": "细级反抽/诱多段；不过前高则仍看 c。",
        "risk": "强势收复 a 全部并站稳 → 改标新推动嫌疑。",
    },
    {
        "id": "sub_c",
        "label": "子浪 c",
        "roman": "c",
        "path": "细级调整主段，常与 a 等长或延伸；完成后母浪阶段更清晰。",
        "risk": "再创新极仍加速 → 可能演化为推动而非简单 abc。",
    },
)
