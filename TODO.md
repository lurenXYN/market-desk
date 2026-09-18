# market-desk backlog

Agents: **at the start of each session involving this app, read this file before planning or coding.**

## Open

### 优先

（暂无）

### 有空再加

（暂无）

### 以后可做（刻意缓做）

- [ ] **再叠硬清仓 / 波浪硬关买卖** — 短期别堆阈值。
- [ ] **自动下单 / 券商对接**
- [ ] **大改主线打分** — 近期已够复杂。
- [ ] **AI 荐股层**

## Done recently (context)

- [x] **模拟撮合 / 回测引擎（轻量）** — 日线价带假成交 + 复盘 outcome；页签「回测」；不改真实 traded。*(2026-09-18)*

- [x] **健康条下钻** — banner 点开源 ok/fail/timeout 明细表。*(2026-09-18)*
- [x] **卖侧日记反哺** — sell_exec 贴计划/偏晚/偏早进复盘。*(2026-09-18)*
- [x] **买卡未 ready 短因** — 顶栏 short_miss「还差：…」+ 卡面 progress。*(2026-09-18)*
- [x] **VPS 库备份** — 收盘 JSON + desk.db 副本；`backup_keep` 可配；admin 列表。*(2026-09-18)*
- [x] **早决策可选推送** — 个人开关；约 9:25–9:50 Server酱一次。*(2026-09-18)*
- [x] **收盘一页纸完善** — 盈亏优先、成交/日记、漏买复盘、尖峰一句、明日看点、tune 一行；主线切换压缩；Server酱底部去重。*(2026-09-18)*
- [x] **当日盈亏 lot 分锚 + 加权卖价** — decorate 按 FIFO lot 今买/隔夜分锚；`day_sell_notional` VWAP。*(2026-09-18)*
- [x] **手动仓试匹配信号 + diary source=manual** — 记账默认可匹配当日买信号；无价带不计硬执行分。*(2026-09-18)*
- [x] **仓位页盈亏日历 + 日/周胜率** — `<details>` 默认收起；展示向不进 adapt。*(2026-09-18)*
- [x] **多用户 soft-scoring 隔离** — desk 仓位偏置不再吞跨账号日记。*(2026-09-18)*
- [x] **卖侧分时未验明示 / 调参夹紧可见 / health 探针加深** — *(2026-09-18)*
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
- Prefer picking an **Open → 优先 / 有空再加** item unless the user names something else.
- 「以后可做」默认可做，但先征得用户同意再开工。
- Keep changes small and testable; update this file when closing items.
