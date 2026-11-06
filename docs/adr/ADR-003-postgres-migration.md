# ADR-003：状态层迁移到独立 PostgreSQL 实例

- 状态：已采纳并完成（2026-11-01 至 2026-11-02）
- 背景：多任务、多组件并发后，SQLite 的写锁（`database is locked`）会阻断 LangBot 流水线；Gateway 任务状态与 Core 任务投影也不适合继续放在进程内/单文件里。

## 决策

1. 独立 PostgreSQL 实例（`mobius-pg`，pgvector/pgvector:pg18），宿主端口 `127.0.0.1:55432`，仅本机可访问。
2. 三个数据库分域：
   - `langbot` —— LangBot 全部数据（含 monitoring/agent_run/pipeline）
   - `mobius` —— Media Gateway 任务快照 + 事务性 outbox
   - `mobius_core` —— Core 业务库（任务、会话、审计）
3. Redis（`mobius-redis`，`127.0.0.1:56379`）作为瞬时共享状态：轮询节流（`SET NX PX`），不承载持久数据。
4. Gateway 任务存储双后端：`MOBIUS_TASK_STORE=pg|sqlite`，PG 实现与原 SQLite 语义一致（含幂等、事务 outbox、恢复探测）。

## 迁移与验证

| 阶段 | 内容 | 结果 |
|---|---|---|
| stage1 | LangBot SQLite → PG（20,174 行） | 行数/内容一致 |
| stage2 | Gateway SQLite → PG（30 任务 / 76 outbox） | 37/37 测试，12/12 验收，3 线程领取无重复，崩溃恢复 attempts=1 |
| stage3 | Core SQLite → PG（9 表） | 行数一致，写路径证明（timeline PG 1 / SQLite 0），5 并发通过 |
| stage4 | 探针切 PG、备份+恢复演练、PG 宕机自愈 | 11 条轨迹、三库 dump 恢复 OK、PG 停机自动重启 |

## 约束与回滚

- 所有连接串来自 `D:/AI/mobius/.env` 与 `config.json`（不入库）
- 回滚：`MOBIUS_TASK_STORE=sqlite` + Core `db_backend` 改回 sqlite（数据不回写，需重新导入）
- 备份：`ops/pg_backup.py`（每日 + 7 天保留 + 月度恢复演练由 D3 定时任务执行）

## 相关报告

`docs/archive/reports-history/STAGE0..STAGE4-*.md`（五份分阶段报告）。
原 `docs/ARCHITECTURE-MIGRATION-plan.md` 已不在仓库中，迁移设计以本文为准。
