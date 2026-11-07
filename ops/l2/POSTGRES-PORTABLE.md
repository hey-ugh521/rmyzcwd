# 便携 PostgreSQL（P4）操作记录

> 状态：2026-11-08 迁移完成。三个库已从容器 `mobius-pg` 迁到本机便携 PG 18.6，Redis 已移除。

## 组成

| 项 | 路径/版本 |
|---|---|
| PostgreSQL | 18.6（EDB Windows x64 binaries）→ `D:\AI\pgsql-portable\pgsql` |
| 数据目录 | `D:\AI\pgsql-portable\data`（initdb，scram-sha-256，UTF8，locale=C） |
| 监听 | `127.0.0.1:55432` |
| pgvector | 0.8.7（`pgvector-v0.8.7-pg18-windows-x64.zip`，DLL 放 `pgsql\lib`，扩展文件放 `pgsql\share\extension`） |
| 日志 | `D:\AI\pgsql-portable\pg.log` |
| 备份 dump | `D:\AI\pgsql-portable\dump\{langbot,mobius,mobius_core}.sql` |

## 数据库

| 库 | 迁移后表数 | 说明 |
|---|---|---|
| langbot | 51 | LangBot 全部数据 |
| mobius | 6 | 记忆 + 网关任务快照（192 行） |
| mobius_core | 9 | Core 的 tasks/audit/context/turns |

## 迁移步骤

1. 容器 PG 里 `drop extension vector`（mobius）与 `drop table langbot_vectors`（langbot）——**注意：LangBot 租户 schema 校验要求该表存在**，后续需从旧 dump 恢复其 DDL。
2. `pg_dump --no-owner --no-privileges` 三库 → 新 dump。
3. `initdb` 便携 PG → 启动 → `createdb` 三库 → `psql -f` 导入（0 ERROR）。
4. 安装 pgvector（LangBot 校验 `langbot_vectors` 的 RLS/策略/约束）。
5. 从基线备份 `pg/langbot.sql` 提取 `langbot_vectors` 的 DDL（CREATE TABLE/约束/索引/RLS/policy）应用：
   - 表存在、`relrowsecurity=true`、`relforcerowsecurity=true`、1 条 policy、9 个约束（含 pk/fk/ck）。
6. LangBot 的 Alembic 已到 head，不会重建该表 → 必须手动恢复。
7. 配置改为 `127.0.0.1:55432`（Core `db_dsn`、LangBot `config.yaml`、gateway `MOBIUS_TASK_DSN` 都一致）。

## Redis

- 删除 gateway 环境变量 `MOBIUS_REDIS_URL`；轮询节流回退到进程内（`pollthrottle.py` 自带 fallback）
- `mobius-redis` 容器已删除（镜像保留）

## 启动顺序（重要）

Core 自身也用 PG（`mobius_core`），因此**必须先起 PG 再起 Core**：

```powershell
# ops/l2/start-postgres.ps1 或手动：
D:\AI\pgsql-portable\pgsql\bin\pg_ctl.exe -D D:\AI\pgsql-portable\data `
  -o "-p 55432 -c listen_addresses=127.0.0.1" -l D:\AI\pgsql-portable\pg.log start
```

P5 启动器负责这个顺序；Core 的 `services.postgres` 也登记了（`autostart=true`，供运行期管理/停止）。

## 验收证据（2026-11-08 01:19）

- Core 服务全 ready：gateway/langbot/napcat/kokoro/gsv/embed/memory/powercontext/postgres
- LangBot 在便携 PG 上启动成功、插件加载正常、OneBot 3001 已连接
- 记忆 MCP：`memory_stats → facts=4`；网关任务快照 192 行
- 容器 PG/Redis 已停（容器保留作回退）
