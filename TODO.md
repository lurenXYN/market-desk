# market-desk backlog

Agents: **at the start of each session involving this app, read this file before planning or coding.**

## Open

- [ ] **趋势分权加重** — 个股 ±8→±12、主线 ETF ±6→±9，并可选上升×1.1/下降×0.75 软仓位（待拍板）。
- [ ] （可选）自选「可试探」时是否进作战台副卡 — 暂保持仅观察页状态列+toast

## Done recently (context)

- [x] **板块相似度智能化** — 成分加权/活跃股/龙头互含/涨跌同向 + why 文案。*(2026-09-14)*
- [x] **板块联动买点（软）** — 主线无 ready 时从相似同伴挖回踩副卡，仓位×0.75，不改 sticky。*(2026-09-14)*
- [x] **当日盈亏汇总修复** — 汇总=各行 day_pnl（非浮盈）；今日新买按买价计。*(2026-09-14)*
- [x] **自选观察即时反馈** — 观察页切片含 watchlist；POST 后立即刷列表。*(2026-09-14)*
- [x] **自选软观察状态** — 可试探/到位·闸门未开/勿追/止损等，不改顶栏。*(2026-09-14)*
- [x] **仓位合流控制器** — 因子相乘 + ADAPT_META 夹紧 + 归因条。*(2026-09-11)*
- [x] **闸门情景自适应** — 时段×波动假杀/真杀微调离日高/分时/薄确认。*(2026-09-11)*
- [x] **Playbook size_cap 自适应** — 随仓位合流软调上限（恐慌不抬高）。*(2026-09-11)*
- [x] **卖侧 MFE 学习** — 早卖留下涨幅中位 → 放宽/收紧止盈回撤。*(2026-09-11)*
- [x] **主线粘性学习** — 换防频次 → sticky margin 软调。*(2026-09-11)*
- [x] **白盒校准** — 样本外 + 贡献条。*(2026-09-11)*
- [x] **exec_score → 轻缩仓** — *(2026-09-11)*
- [x] **分段卖点学习化** — *(2026-09-11)*
- [x] **相似日 cool 偏软** — *(2026-09-11)*
- [x] **一致性修复 P0–P5** — *(2026-09-11)*

## Won’t do (by design)

- Emotion / seasonality → hard buy
- True Kelly full-size
- ATR replaces all pullback bands
- Black-box ML predictors
- Untagged / cross-bucket missed-buy generalization into auto_tune
