# D3 报告：定时巡检（daily / weekly / drill）

> 执行时间：2026-11-02 11:50–11:53

## 1. 交付

| 文件 | 作用 |
|---|---|
| `ops/scheduled_report.py` | 三种模式的巡检报告生成器（MCP 采集 + 降级 + 可选 DSH 叙述） |
| `docs/reports/*.md` | 报告输出目录 |
| Windows 计划任务 ×3 | 见下表 |

| 任务 | 计划 | 命令 |
|---|---|---|
| `Mobius-Daily-Health` | 每天 09:00 | `scheduled_report.py --mode daily` |
| `Mobius-Weekly-Audit` | 每周日 10:00 | `--mode weekly` |
| `Mobius-Monthly-Drill` | 每月 1 日 03:00 | `--mode drill` |

## 2. 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | daily 产出真实数据报告 | ✅ 4,871 字节（系统状态/PG/任务/消息轨迹/watchdog 日志/仓库） |
| 2 | weekly 产出 | ✅ |
| 3 | drill 产出恢复演练结果 | ✅ langbot 52 表 / mobius 3 表 / mobius_core 9 表 全部 OK |
| 4 | MCP 不可用时降级 | ✅ 切换为端口/容器直采，报告仍生成 |
| 5 | 计划任务注册且时间正确 | ✅ 下次运行 11-03 09:00 / 11-08 10:00 / 12-01 03:00 |
| 6 | 任务可手动触发 | ✅ `schtasks /Run` 后新报告 11:53:00 生成 |
| 7 | 并发运行 | ✅ daily + weekly 同时执行，各自独立报告无冲突 |
| 8 | DSH 叙述可选 | ✅ `--with-dsh` 时调用只读 profile，失败自动跳过 |

## 3. 已知限制

- 需要 `D:\AI\qqbot-mcp` 解释器（含 mcp/psycopg）；已固化在任务命令中
- 报告只落本地文件，未接通知渠道
- drill 模式耗时较长（备份 + 恢复到临时库），放在每月凌晨
- 首次创建计划任务需在无 `/RL HIGHEST` 参数下执行（带该参数会因权限失败）
