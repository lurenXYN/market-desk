# 从线上拉 desk.db 到本地（避免 malformed）

## 原则

1. **先停本地** market-desk  
2. 删本地 `data/desk.db`、`desk.db-shm`、`desk.db-wal`  
3. 再拷**一份完整一致**的库  
4. 再启动  

切勿只覆盖 `desk.db` 而留下旧 WAL。

## 推荐：拷线上自动备份

```bash
# VPS
ls -lt /opt/market-desk/data/backup/desk-*.db | head
```

本机：

```powershell
cd apps/market-desk
.\scripts\pull-desk-db.ps1 -HostName root@VPS -RemoteDb /opt/market-desk/data/backup/desk-YYYY-MM-DD-HHMMSS.db
```

## 或：停服 checkpoint 后拷主库

```bash
sudo systemctl stop market-desk
sqlite3 /opt/market-desk/data/desk.db "PRAGMA wal_checkpoint(TRUNCATE);"
# scp desk.db …
sudo systemctl start market-desk
```

## Admin 下载

设置页 / 备份列表：`GET /api/backup/auto/download?name=desk-….db`（admin）。
