# market-desk backlog

Agents: **at the start of each session involving this app, read this file before planning or coding.**

## Open

- [ ] **执行日记一键** — 买/减/清时一键记下当时建议，清洁复盘样本，自适应更准。
- [ ] **同代码多笔成本** — 分批真实记账（现 batch_plan 偏示意）。
- [ ] **持仓龙虎榜席位变坏 toast** — 已有席位数据，边沿提醒即可。
- [ ] **移动端窄屏卖卡/仓位** — 通勤查看建议。

## Done recently (context)

- [x] **买侧冲高假到位** — 回踩到位但分时仍贴尖 → 只试探不升可买（TIP_PROBE_ALLOW_FAILS / fake_pullback_tip）。*(2026-09-18)*
- [x] **主线换防持仓护栏** — sticky 刚换：旧主题仓进观察卖侧；新主题约45分钟内不因软减误砍强票。*(2026-09-18)*
- [x] **竞价→开盘15分钟桥** — 竞价强+开盘弱 → 降仓/撤销试探（竞价开盘桥）。*(2026-09-18)*
- [x] **龙头为何说明** — 情绪龙/中军龙卡片写「为何是…」选因（连板/封单/成交 vs 成交额+市值）。*(2026-09-18)*
- [x] **买卖冲突协调** — 软卖遇可买→继续持有；已持仓/硬卖→买侧不加仓；主线内强票豁免高开低走。*(2026-09-18)*
- [x] **题材信誉高/中优化** — 结算加宽；fade 收紧；薄样本弱进主线；面板分项 auto/成交/手调；公式 v3。*(2026-09-18)*
- [x] **复盘卖出按账号隔离** — `owner_user_id`；卖点只见本账号；买点仍共享。*(2026-09-18)*
- [x] **卖点落复盘** — 个人层 ready 卖点写入 signals。*(2026-09-18)*
- [x] **卖飞看板展示符号** — 次日/留桌上按卖后股价涨跌展示。*(2026-09-18)*
- [x] **参数面板可滚动 + 可关闭** — 限高滚动；`[hidden]` 覆盖 `display:flex`。*(2026-09-18)*
- [x] **仓位下一动作 + 减半价 + 卖飞看板** — 仓位表「下一动作/已减价」；卖卡触发价；复盘卖飞看板。*(2026-09-18)*
- [x] **Server酱推送** — 每用户 SendKey；admin 允推送；买卖点；无 Key 不推。*(2026-09-17)*
- [x] **卖侧优化 1–5** — 反悔窗、分时止盈闸门、tune_tags/MFE、峰值轻半深清、卖飞复盘。*(2026-09-17)*

## Notes for agents

- Publish via independent repo only: sync `apps/market-desk/` → `D:\Source\Repos\market-desk` → commit/push there. **Never push go-learning.**
- Prefer picking an **Open** item unless the user names something else.
- Keep changes scoped; match existing style; update this file when closing items.
- **题材信誉**：fade/persist → auto_adj；+ manual + trade = score_adj；薄样本 `rep_adj`×0.2 进主线；面板分项展示。
- **复盘卖出**：`owner_user_id`；买点 owner=0 共享；卖点按账号过滤。
- **买卖冲突**：软 half 遇同码 ready 买或「可买入」主题 → hold；硬止损保留；持仓同码买侧降级。
