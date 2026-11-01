# 阶段 3 执行报告：Core 迁移 PostgreSQL

> 执行时间：2026-11-01 22:45–22:56
> 结果：**验收 3.1–3.6 全部通过**

---

## 1. 最终形态

| 项 | 值 |
|---|---|
| Core 数据库 | PostgreSQL（`mobius_core` 库，mobius-pg） |
| 切换方式 | `config.json → db_backend=pg` + `db_dsn`（本地文件，已 gitignore） |
| 兼容性 | `core/store.py` 保留 sqlite 后端，`db_backend=sqlite` 即回滚 |
| 表 | 9 张（tasks/audit/turns/user_memory/memory_candidates/timeline/constitution/kv/identities） |

## 2. 验收对照

| # | 标准 | 结果 |
|---|---|---|
| 3.1 | 9 张表逐表行数一致 | ✅ tasks 3 / audit 231 / kv 1，其余 0 |
| 3.2 | 基础端点正常 | ✅ `/health`、`/api/status`、`/api/tasks` |
| 3.3 | 写路径落到 PG | ✅ 写 timeline → PG 1 行，SQLite 仍 0 |
| 3.4 | 并发 5 请求 | ✅ 全 OK |
| 3.5 | 抽查数据可读 | ✅ tasks/audit 正常读出 |
| 3.6 | 回滚可用 | ✅ `data/mobius.db` 保留未动 |

**端到端**：WebChat 发消息 → LangBot（PG）→ 回复正常。

## 3. 改造要点

- `core/store.py` 新增 `_PgConnection` 适配器（`?`→`%s`、dict 行、事务上下文）
- `init_db()` 对 PG 自动把 `INTEGER PRIMARY KEY AUTOINCREMENT` 改为 `BIGSERIAL`
- 迁移脚本 `ops/migrate_core_to_pg.py`（幂等 truncate + 导入 + 逐表校验）

## 4. 遗留

- Core 的 DSN 含密码，写在本地 `config.json`（gitignore）
- 探针仍读旧 SQLite（阶段 4）
- SQLite 后端保留作回滚路径

---

# 三库迁移总览

| 库 | 状态 |
|---|---|
| LangBot | ✅ PostgreSQL（阶段 1） |
| Gateway tasks | ✅ PostgreSQL（阶段 2） |
| Core | ✅ PostgreSQL（阶段 3） |
