# GitHub Actions 自动部署（方案 1：SSH → VPS）

push `main`（或手动 Run workflow）后，GitHub Actions 用 SSH 登录服务器，执行：

1. `git fetch` + `reset --hard origin/main`（仓库克隆目录）
2. `rsync` 到运行目录（默认 `/opt/market-desk`，**保留** `.venv` 与 `data/`）
3. `pip install -r requirements.txt`
4. `systemctl restart market-desk`
5. 访问 `/api/health` 做健康检查

## 一次性准备（服务器）

假设克隆在 `~/github/market-desk`，服务已装好（`./deploy/install-systemd.sh`）。

```bash
# 依赖
apt install -y git rsync curl python3-venv

# 确认仓库能拉到 main
cd ~/github/market-desk
git remote -v
git pull origin main

# 手动演练一次更新脚本
chmod +x deploy/remote-update.sh
SKIP_PULL=1 REPO_DIR="$PWD" INSTALL_DIR=/opt/market-desk ./deploy/remote-update.sh
```

## 一次性准备（SSH 部署密钥）

在**你自己的电脑**生成专用密钥（不要用日常登录私钥提交到 GitHub）：

```bash
ssh-keygen -t ed25519 -C "github-actions-market-desk" -f ./market-desk-deploy -N ""
```

把**公钥**装到服务器（root 示例）：

```bash
# 在服务器上
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "这里粘贴 market-desk-deploy.pub 的整行内容" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

本机先测通（把 HOST 换成 VM 公网 IP）：

```bash
ssh -i ./market-desk-deploy root@HOST "systemctl is-active market-desk"
```

## 配置 GitHub Secrets

打开：https://github.com/lurenXYN/market-desk/settings/secrets/actions

| Name | 必填 | 示例 |
|------|------|------|
| `DEPLOY_HOST` | 是 | `1.2.3.4` 或域名 |
| `DEPLOY_USER` | 是 | `root` |
| `DEPLOY_SSH_KEY` | 是 | `market-desk-deploy` **私钥**全文（含 `BEGIN`/`END`） |
| `DEPLOY_PORT` | 否 | 默认 `22`；改过 SSH 端口再填 |

可选 **Variables**（Settings → Secrets and variables → Actions → Variables）：

| Name | 默认 | 含义 |
|------|------|------|
| `DEPLOY_REPO_DIR` | `$HOME/github/market-desk` | git 克隆路径 |
| `DEPLOY_INSTALL_DIR` | `/opt/market-desk` | systemd 运行目录 |

## 验证

1. 改 README 任意一行 → push `main`
2. 打开 Actions：https://github.com/lurenXYN/market-desk/actions
3. 看 **Deploy** 是否绿；服务器上：

```bash
journalctl -u market-desk -n 30 --no-pager
curl -sS http://127.0.0.1:8765/api/health
```

也可在 Actions 页点 **Run workflow** 手动部署。

## 注意

- `data/desk.db` 不会被 rsync 覆盖（排除了 `data/`）。
- 多用户：首次启动若无用户会创建管理员 `admin` / `admin123`（可用环境变量 `MARKET_DESK_ADMIN_USER` / `MARKET_DESK_ADMIN_PASSWORD` 覆盖）；他人自助注册后需管理员在页头「审批」同意。
- Actions 并发组会取消进行中的旧部署，避免叠两次 restart。
- 私钥只放在 GitHub Secrets，不要提交进仓库；本机测完可删本地 `market-desk-deploy` 或锁进密码器。
- 若仓库是 private，服务器 `git fetch` 需已配置 deploy key / token（当前 public 则无需）。
- 公网务必 Nginx + HTTPS，勿长期裸奔 `0.0.0.0:8765`。
