# MEMORY.md — market-desk 会话记忆（仓库根目录）

> 用途：跨会话/跨模型的可读记忆。只记录决策、约束与现状，**不记录任何密钥、token、密码或服务器凭据**。
> 规则来源：`D:\Source\Repos\mydemo\go-learning\.cursor\rules`（branch-policy / release / data-compat / todo）。

## 1. 仓库与发布约定（硬规则）

- 开发仓：`D:\Source\Repos\mydemo\go-learning\apps\market-desk`（分支 `market-desk-local`，**永不 push**）。
- 发布仓：`D:\Source\Repos\market-desk` → `https://github.com/lurenXYN/market-desk.git`（main，push 即部署 VPS）。
- 流程：独立仓先 `git fetch`，若 `origin/main` 领先需 `pull` 后反向同步回 `apps/market-desk`；改完 sync 回独立仓 → commit → push。
- 数据兼容：改 schema/公式/枚举必须可跑迁移、幂等、保留手工标注（见 `market-desk-data-compat.mdc`）。
- 每次功能更新须在 `CHANGELOG.md` 写 Why/What/影响，并同步 `TODO.md`。
- 单文件约 1300 行上限；新逻辑进对应子包，不堆回单个文件。

## 2. 运行形态

- 单进程 FastAPI（uvicorn，无 workers），后台刷新协程发布快照；前端为原生 JS/CSS，服务端拼接 `/assets/desk.js|css`。
- 存储：`data/desk.db`（SQLite WAL），当前 45+ 表；新增 `decision_trace`（v1.10.102）。
- 数据源：东财（push2 系 / push2his / datacenter）、腾讯、新浪、同花顺（v1.10.103 起作为分时第三源）。

## 3. 2026-11-17 会话记录（v1.10.95 → v1.10.104）

| 版本 | 提交 | 内容 |
|---|---|---|
| v1.10.95 | `601eafa` | 切片竞态、循环看护、`/api/health` 真值、推送移出事件循环、命中率最小样本 |
| v1.10.96 | `a06b8b3` | 数据层加固（连接/迁移/备份/EOD）、日志与缓存、CI、题材 T+1、白盒 n=40、止损风险距离 |
| v1.10.97 | `375fb54` | 回测 `signaled_at` 保守成交、成本/净值/基准超额、转义与请求封装 |
| v1.10.98 | `9b2fd92` | 写请求失败可见提示、板块备注转义 |
| v1.10.99 | `8c160e1` | 趋势去当日 bar、卖出带宽夹紧、命中口径拆分 |
| v1.10.100 | `4c6495e` | 甜区分层胜率、单一校准入口、主线归一化、复盘双命中列 |
| v1.10.101 | `869c747` | CSV 跟随筛选、表单防抖、剩余转义 |
| v1.10.102 | `c84ab75` | 策略档案、组合层风控、CUSUM 变点、决策日志、实验框架 |
| v1.10.103 | `b881809` | 东财备用边沿 + 同花顺分时兜底 |
| v1.10.104 | 本次 | clist 冷却 90s/3 次、板块与资金流 TTL 300s、边沿池扩至 7 个、仓库记忆文件、GitHub 取 VPS 信息 |

## 4. 关键设计决策（避免重复讨论）

- 止损不再等于当日最低价，保留 1.2%(ETF)/1.5%(个股) 最小风险距离。
- 相位命中率闸门要求 n≥30；低于此不改动作。
- 组合层风控为软倍率（0.4–1.0），**不阻断买入**；只覆盖已持仓，开仓前相关性尚未实现。
- 变点检测（CUSUM）目前只作快照旁注，不接 ready。
- 回测 `ready_only` 用 `ever_ready`；`signal_at` 晚于 09:35 且无分钟数据时当日不计成交。
- 同花顺日 K 未接入：复权口径未确证，避免污染 `market_bar_daily` 复权族。

## 5. VPS 与数据源现状

- 本机实测（2026-11-17）：东财、腾讯、新浪、同花顺均 200；网易/雪球/上交所接口在本地 SSL 失败（本地网络策略，不代表 VPS）。
- VPS 上东财「被禁」判断为 IP 段级风控，不是接口失效；应对顺序：换出口 IP > 降频错峰 > 备用边沿 > 板块缓存兜底。
- VPS 凭据只存在于仓库 Secrets：`DEPLOY_HOST` / `DEPLOY_USER` / `DEPLOY_SSH_KEY` / `DEPLOY_PORT`；**本文件不记录任何值**。
- 通过 GitHub 拿 VPS 信息：`.github/workflows/vps-info.yml`（手动触发，SSH 采集系统与健康状态，产出 `vps-info.json` artifact 与 summary）；本地用 `python scripts/vps_info.py` 读运行列表与最新 artifact。

## 6. 待办（与 TODO.md 保持同步）

- 开仓前组合相关性（候选卡与持仓同涨同跌）。
- 信号半衰期统计、FDR 校正。
- 板块数据缓存兜底 + `board_degraded` 标记（东财被禁时保住主线判断）。
- 模型侧：Kelly/波动率目标仓位、执行算法与冲击成本、数据质量异常检测、主线生存分析。