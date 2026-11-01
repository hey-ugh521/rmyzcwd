# 阶段 0 执行报告：PostgreSQL 18 + Redis 7（独立容器）

> 执行时间：2026-11-01 15:47
> 脚本：`ops/stage0-pg-redis.ps1`
> 结果：**全部验收项通过**

---

## 1. 运行形态

| 项 | 值 |
|---|---|
| 网络 | Docker `mobius_data` |
| PG 容器 | `mobius-pg`，`postgres:18`，宿主 `127.0.0.1:55432`，数据在 named volume `mobius_pgdata` |
| Redis 容器 | `mobius-redis`，`redis:7`，宿主 `127.0.0.1:56379`，数据在 named volume `mobius_redisdata` |
| 密码 | 32 位随机，写在 `D:\AI\mobius\.env`（ACL 已收紧，不入库） |
| 备份目录 | `D:\AI\mobius\pgbackup` |
| 重启策略 | 两者均 `unless-stopped` |

## 2. PG 参数（已落地）

```
shared_buffers=512MB  max_connections=100  work_mem=4MB
effective_cache_size=4GB  max_wal_size=4GB
checkpoint_completion_target=0.9  synchronous_commit=on
log_min_duration_statement=200ms
```

## 3. 数据库

- `mobius`（Core + Gateway 未来使用）
- `langbot`（阶段 1 迁移目标，已预建）

## 4. 验收结果

| # | 验收项 | 结果 |
|---|---|---|
| 0.1 | 容器运行 | ✅ 两个容器均 healthy |
| 0.2 | 宿主机直连 | ✅ `psql -h 127.0.0.1 -p 55432` 返回 `mobius / 172.22.0.2` |
| 0.3 | 容器间连通 | ✅ `psql -h mobius-pg` 返回 `172.22.0.2`；`redis-cli -h mobius-redis` 返回 PONG |
| 0.4 | 认证 | ✅ Redis 无密码 → `NOAUTH`；有密码 → `PONG`；PG 密码生效 |
| 0.5 | 参数落地 | ✅ SHOW 输出与配置一致 |
| 0.6 | Redis 持久化 | ✅ `appendonly=yes`、`appendfsync=everysec`，`/data/appendonlydir` 存在 |
| 0.7 | 重启存活 | ✅ 建表→`docker restart`→数据仍在（`2026-11-01 07:45:46+00`） |
| 0.8 | 端口绑定 | ✅ 仅 `127.0.0.1:55432` / `127.0.0.1:56379` |

## 5. 执行中修正的问题

| 问题 | 处理 |
|---|---|
| 脚本里 `docker run ... psql` 无 `PGPASSWORD`，会卡在密码提示 | 全部加 `-e PGPASSWORD` |
| `D:\AI\mobius` 目录不存在，`.env` 写入会失败 | 先 `New-Item -Force` |
| 重启后立即查询可能失败 | 加 `pg_isready` 重试循环 |
| **Redis 健康检查里的 `$REDIS_PASSWORD` 未展开**，会导致 unhealthy | 改为容器内 env + 单引号 health-cmd，重建后 healthy |

## 6. 已知注意事项

- scoop 安装的 Redis（宿主机 6379）保留不动；实测 `dbsize=0`（空库）
- PG 18 官方镜像默认 PGDATA 已变，本方案显式 `PGDATA=/var/lib/postgresql/data/pgdata` + named volume
- 宿主机 psql 客户端是 scoop 装的，可直连 PG 18（本次验证通过）
