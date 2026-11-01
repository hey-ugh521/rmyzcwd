# 阶段 2 执行报告：Gateway 任务存储迁移 PostgreSQL

> 执行时间：2026-11-01 22:10–22:43
> 结果：**验收 2.1–2.7 全部通过**；网关已由 Core 托管启动并确认使用 PostgreSQL

---

## 1. 最终形态

| 项 | 值 |
|---|---|
| 任务存储 | PostgreSQL（`mobius` 库，mobius-pg） |
| 实现 | `media_gateway/pg_taskstore.py`（与 SQLite 版同语义） |
| 切换方式 | 环境变量 `MOBIUS_TASK_STORE=pg` + `MOBIUS_TASK_DSN` |
| 启动配置 | 已写入 `config.json → services.gateway.env`（Core 托管启动生效） |
| 队列并发 | `SELECT ... FOR UPDATE SKIP LOCKED`（多 worker 安全） |
| outbox | 与任务状态同事务（未变） |
| SQLite 回退 | `tasks.db` 保留，`MOBIUS_TASK_STORE=sqlite` 即可回滚 |

## 2. 验收对照

| # | 标准 | 结果 |
|---|---|---|
| 2.1 | PG 实现下 37 项单元测试 | ✅ 37/37 |
| 2.2 | 真实网关 12 项验收 | ✅ 12/12 |
| 2.3 | 事务性 outbox（异常回滚） | ✅ 单测覆盖（含故障注入） |
| 2.4 | 队列并发（多 worker 不重复） | ✅ 3 线程 / 10 任务 / 无重复领取 |
| 2.5 | 崩溃恢复 | ✅ 杀网关→重启→接管→completed，**attempts=1** |
| 2.6 | 数据迁移 | ✅ 30 任务 / 76 outbox 逐表一致 |
| 2.7 | 生图链路 + 无锁错误 | ✅ submit 133ms、completed、无锁错误 |

## 3. 实施要点

- 新增 `PgTaskStore`（psycopg 3，同步连接，短事务）
- 网关层方法（worker/恢复/执行）通过适配类复用到 PG 存储
- 迁移脚本 `ops/migrate_gateway_to_pg.py`（幂等：先 TRUNCATE 再导入 + 序列重置）
- psycopg 安装到网关 venv（`D:\AI\qqbot-mcp`）

## 4. 过程中发现并处理的问题

| # | 问题 | 处理 |
|---|---|---|
| 1 | PG 版缺网关层方法（bootstrap/worker）→ 启动卡住 | 增加 `PgGatewayStore` 适配层，复用 TaskStore 方法 |
| 2 | 验收测试写死查 SQLite | `db_count` 改为后端感知（PG 用 %s 参数） |
| 3 | submit 时延 147s（ComfyUI 冷启动被计入） | 冷启动不计入；预热后 133ms |
| 4 | Core 未重启导致托管启动仍用 SQLite | 重启 Core 后确认走 PostgreSQL |

## 5. 遗留

- `MOBIUS_TASK_DSN` 含密码，写在本地 `config.json`（已被 .gitignore）；建议后续改为从 `.env` 注入
- SQLite 版实现保留（回退路径），长期可考虑移除
- 探针仍读旧 SQLite（阶段 4 处理）
