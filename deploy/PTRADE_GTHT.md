# 国泰海通 PTrade ↔ 作战台桥接（阶段 1 只读）

作战台跑在 **Linux VPS**；PTrade 策略跑在 **券商侧**。不能把 PTrade 装进 VPS。

```text
PTrade 策略（券商云）
  │  POST /api/bridge/ptrade/positions   上报持仓/成交
  │  GET  /api/bridge/ptrade/intents     拉可买/卖点意图（阶段1只看）
  ▼
作战台 VPS（gate_readonly_ok / gate_ok）
```

阶段 1：**禁止**在策略里调用 `order_*`。阶段 2 才允许在 `send_allowed=true` 且意图 `armed` 时下单（仍建议人确认）。

## 1. 开通

1. 国泰海通开 **PTrade** 权限（合并后原国君/海通账户按营业部流程）。
2. 下载券商提供的 PTrade 客户端，用资金账号登录。
3. 确认策略环境能否访问外网作战台域名（白名单 / 出站 HTTP）。若不能，改用可出站的运行时段或联系营业部。

## 2. 作战台配置 token

管理员登录作战台后：

```http
POST /api/ops/ptrade-bridge-token?rotate=true
```

响应里的 `token` **只显示一次**，抄到 PTrade 策略常量 `DESK_BRIDGE_TOKEN`。

查看是否已配置（不回明文）：

```http
GET /api/ops/ptrade-bridge
```

## 3. 策略模板

仓库：`scripts/ptrade/gtht_bridge_strategy.py`

改三处后粘贴进 PTrade 策略编辑器：

- `DESK_BASE_URL`：作战台公网 HTTPS，如 `https://desk.example.com`
- `DESK_BRIDGE_TOKEN`：上一步 token
- `SEND_ALLOWED = False`：阶段 1 必须保持 False

建议每 60–120 秒跑一轮：先 `positions` 上报，再拉 `intents` 写日志。

## 4. 对账

上报成功后，管理员打开模拟页「券商只读对账」，或：

```http
GET /api/ops/exec-reconcile
```

看纸面仓 / 记账仓 vs PTrade 持仓差异。

## 5. 明确不做（本阶段）

- VPS 安装 PTrade / QMT
- 策略里自动 `order_shares`（全自动属阶段 3，须阶段 2 验证 + 你书面确认）
