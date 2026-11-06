# 阶段 1 执行报告：LangBot 迁移 PostgreSQL

> 执行时间：2026-11-01 15:52–16:24
> 方式：停服影子迁移（不做双跑）；数据集 50 表 / 20,174 行
> 结果：**验收 1.1–1.8 全部通过**

---

## 1. 最终形态

| 项 | 值 |
|---|---|
| LangBot 数据库 | PostgreSQL（`langbot` 库，mobius-pg:5432） |
| 表数量 | 52（alembic head: 0010_scope_resources 之上） |
| 数据行数 | 20,174（与 SQLite 源逐表一致） |
| 连接方式 | 容器 `langbot` 已接入 `mobius_data` 网络，服务名 `mobius-pg` |
| SQLite 原文件 | **保留未删**（回滚用），`data/langbot.db` |
| 配置备份 | `data/config.yaml.bak-pg-20261101-160506` |

## 2. 验收对照

| # | 标准 | 结果 |
|---|---|---|
| 1.1 | 预演库逐表行数一致 | ✅ 20,174 / 20,174 |
| 1.2 | 启动日志显示 postgresql | ✅ `Initializing database type: postgresql...` |
| 1.3 | 生产库逐表行数一致 | ✅ 20,174 / 20,174 |
| 1.4 | pipeline 配置完整 | ✅ prompt 6948 字符（含 P0.4-A）、`exception-handling=show-hint` |
| 1.5 | 消息链路 | ✅ WebChat 发送 → PG 记录 user/assistant |
| 1.6 | 生图链路 | ✅ submit→poll→completed，99s，`mobius_portrait_00063_.png` |
| 1.7 | 无 `database is locked` | ✅ 日志无锁错误（LiteLLM info 除外） |
| 1.8 | 回滚可用 | ✅ SQLite 文件 + 配置备份均在 |

## 3. 并发测试

- 5 条消息并发 → 5 问 5 答全部成功
- PG 连接数 8（远低于 100 上限）
- 无锁错误、无 traceback

## 4. 迁移中遇到并解决的问题

| # | 问题 | 处理 |
|---|---|---|
| 1 | LangBot 需要 **pgvector** 扩展，`postgres:18` 没有 | 镜像换成 `pgvector/pgvector:pg18`，数据卷重建 |
| 2 | PG18 官方镜像 PGDATA 路径变化 | 显式 `PGDATA=/var/lib/postgresql/data/pgdata` |
| 3 | 预演容器挂载父目录导致配置不可见 | 改为挂载 `data` 目录本身 |
| 4 | 简写 config 被应用重写成默认（SQLite） | 改为「字节级复制生产配置 + 仅替换 database 块」 |
| 5 | 空库直接跑 alembic CLI 失败（FK 依赖顺序） | 改为让应用自身 `create_tables + alembic` 建表 |
| 6 | ETL 后用 TRUNCATE CASCADE 破坏了已导入表 | 改为「先统一 TRUNCATE 再插入」，ETL 幂等 |
| 7 | ETL 清掉了 PG 专有元数据 `oss_workspace_uuid`，导致启动失败 | 从 `workspaces.uuid` 补写 |
| 8 | **恢复 prompt 时漏写 `role` 字段** → 消息处理报 pydantic 错误 | 恢复为 `{"role":"system","content":...}` |
| 9 | ETL 序列重置对 text 型 id 报错 | 仅对整数 id 重置序列 |

## 5. 发现的风险（需跟进）

| 风险 | 说明 |
|---|---|
| **LangBot 云端同步会覆盖 pipeline** | 15:54 时 `legacy_pipelines.config` 被改成 1284 字符默认模板（`updated_at=07:54:26Z`），导致 P0.4 提示词丢失；已用快照恢复。**需确认触发条件并关闭云端覆盖** |
| 探针读取的是旧 SQLite | 探针目前仍读 `qqbot/langbot/data/langbot.db`（已冻结），**数据会变陈旧**，需在阶段 4 改为读 PG |
| watchdog 的 SQLite 备份 | 仍在备份已停用的 langbot.db，需调整 |
| `langbot_plugin_runtime` 未验证连 PG | 它不直接连 DB（走 LangBot API），暂不受影响 |
