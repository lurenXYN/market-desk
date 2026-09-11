# market-desk backlog

Agents: **at the start of each session involving this app, read this file before planning or coding.**

## Open

（暂无大项；小优化随用随记）

## Done recently (context)

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
