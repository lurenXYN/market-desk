# market-desk backlog

Agents: **at the start of each session involving this app, read this file before planning or coding.**

## Open

### 以后可做（刻意缓做）

- [ ] **再叠硬清仓 / 波浪硬关买卖** — 短期别堆阈值。
- [ ] **自动下单 / 券商对接**
- [ ] **大改主线打分** — 近期已够复杂。
- [ ] **AI 荐股层**
- [ ] **模拟撮合 / 回测引擎**

## Done recently (context)

- [x] **换防解释对齐 pick + 联动兜底/支线并入 + 尖峰主升提示 + 竞价桥叠乘归因** — *(2026-09-18)*
- [x] **参数导出/导入 + 防守/平衡/进攻预设** — *(2026-09-18)*
- [x] **仓位目标 vs 实际常驻条** — 作战台/仓位页进度条。*(2026-09-18)*
- [x] **主题链时间线** — 点题材信誉卡看 persist/fade。*(2026-09-18)*
- [x] **盘后自动一页纸** — eod:{date} 落库 + Server酱每日一次。*(2026-09-18)*
- [x] **LHB 席位变坏走 Server酱** — *(2026-09-18)*
- [x] **买卡分批档一键记独立 lot** — *(2026-09-18)*
- [x] **主线质量仪表** — 顶栏主题/分差/持有/粘滞短因；详情仍在 mlWhy。*(2026-09-18)*
- [x] **执行日记反哺** — diary→伪信号并入执行分；同码去重；adapt 近20笔含日记。*(2026-09-18)*
- [x] **复盘调参闭环** — tune_hints 加题材/交叉与可操作回踩幅度；传入当前 context。*(2026-09-18)*
- [x] **数据韧性提示** — 源失败率/超时/热点偏少 → degraded；banner 明示降级。*(2026-09-18)*
- [x] **执行日记一键 / 分批 FIFO / LHB 席位 toast / 窄屏卖卡仓位** — *(2026-09-18)*
- [x] **冲高假到位 / 换防护栏 / 竞价开盘桥 / 龙头为何 / 买卖冲突 / 题材信誉 v3** — *(2026-09-18)*
- [x] **卖点落复盘 / 卖飞看板 / 仓位下一动作 / Server酱 / 卖侧 1–5** — *(2026-09-17~18)*

## Notes for agents

- Publish via independent repo only: sync `apps/market-desk/` → `D:\Source\Repos\market-desk` → commit/push there. **Never push go-learning.**
- Prefer picking an **Open → 有空再加** item unless the user names something else. (Open 有空再加 currently empty.)
- 「以后可做」默认可做，但先征得用户同意再开工。
- Keep changes small and testable; update this file when closing items.
