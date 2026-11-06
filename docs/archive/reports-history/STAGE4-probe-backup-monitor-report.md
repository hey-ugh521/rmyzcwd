# 阶段 4 执行报告：探针/备份/监控切换 PostgreSQL

> 执行时间：2026-11-01 22:52–22:58
> 结果：**验收 4.1–4.6 全部通过**

---

## 1. 探针（4.1 / 4.2）

| 项 | 变更 |
|---|---|
| 数据源 | 旧 SQLite（已冻结）→ **PG 双库**（`langbot` + `mobius`） |
| 密码 | 探针自读 `D:/AI/mobius/.env`，无需外部注入 |
| 时间戳 | PG 返回 datetime → 统一转本地 `+08:00` + epoch |
| 实测 | 采集 11 条消息轨迹，过程树正常（含跨天数据） |

## 2. 备份（4.3 / 4.4）

新增 `ops/pg_backup.py`：

```
--backup   pg_dump 三个库（langbot / mobius / mobius_core）→ D:\AI\mobius\pgbackup
           保留 7 天（自动清理过期）
--drill    用最新 dump 恢复到临时库并校验表数
--list     列出备份
```

实测结果：

```
langbot      4097.4 KB  OK
mobius         26.6 KB  OK
mobius_core    18.0 KB  OK

恢复演练：
langbot      -> langbot_restore_test     表数=52  OK
mobius       -> mobius_restore_test      表数=3   OK
mobius_core  -> mobius_core_restore_test 表数=9   OK
```

## 3. 监控（4.5 / 4.6）

watchdog 新增：

| 检查 | 行为 |
|---|---|
| `mobius-pg` / `mobius-redis` 容器 | 未运行 → `docker start` + 日志 |
| PG 备份新鲜度 | 超过 26h 未备份 → 自动补做 |
| 旧 SQLite 备份 | **调用已停用**（4.6） |

**故障注入验证**：手动 `docker stop mobius-pg` → watchdog 下一轮输出
`mobius-pg 未运行，已尝试拉起`，容器恢复 healthy。

## 4. 验收对照

| # | 标准 | 结果 |
|---|---|---|
| 4.1 | 探针从 PG 还原进程树 | ✅ |
| 4.2 | 探针读双库 | ✅ langbot + mobius |
| 4.3 | pg_dump 每日 + 保留 | ✅ 3 库成功，保留策略生效 |
| 4.4 | 恢复演练 | ✅ 52/3/9 表还原 |
| 4.5 | PG/Redis 停止检测 | ✅ 自动拉起 |
| 4.6 | 旧 SQLite 备份停用 | ✅ 调用已移除 |

## 5. 遗留

- `qqbot/watchdog.py` 不在 git 仓库内（该目录无 `.git`），**改动未纳入版本控制**，建议后续单独建仓或复制进 mobius 仓库
- watchdog 偶发 `读取网关/LangBot 启动时间失败: TimeoutError`（服务忙时 HTTP 超时），不影响功能
- LangBot 在 PG 停机窗口内会报 401（容器重连），恢复后正常
