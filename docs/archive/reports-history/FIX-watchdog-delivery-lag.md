# P1-fix：watchdog「投递-调用滞后」误判（会误重启 LangBot）

> 修复时间：2026-11-02 11:48–11:50

## 问题（DSH D2 诊断发现）

watchdog 显示「投递-调用滞后 72059s」，曾据此误重启 LangBot。

## 根因（两个数据源都过期/失明）

| 指标 | 原实现 | 实际 |
|---|---|---|
| `last_llm_epoch()` | 读 **迁移前的 SQLite** `monitoring_llm_calls` | 数据停在 11-01 07:26:52（UTC），滞后虚高 20 小时 |
| `last_delivered_event()` | 读 LangBot bot 日志接口的 "delivered to Pipeline" | 该接口在 LangBot 重启后返回空 → 读数恒为 0，判定**完全失明** |

## 修复

| 函数 | 改动 |
|---|---|
| `last_llm_epoch()` | 优先读 PG `monitoring_llm_calls.max(timestamp)`，PG 失败才回退 SQLite |
| `last_delivered_event()` | 优先读 PG `monitoring_messages where role='user'` 的最新时间，bot 日志仅作回退 |

## 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | 滞后指标恢复正常量级 | ✅ `投递-调用滞后 -3s`（修复前 72059s） |
| 2 | 不再误重启 LangBot | ✅ 连续两次 `--once` 无重启动作 |
| 3 | PG 不可用可降级 | ✅ 注入错误密码后回退 SQLite，不崩溃 |
| 4 | 关联：投递/调用时间与真实消息一致 | ✅ 最后投递 846s 前（11:35 最后一条消息）、最后 LLM 842s 前 |

## 说明

- 滞后为负是正常现象：用户消息投递后立即调 LLM，LLM 写入比投递略晚
- 卡死判定逻辑不变：`投递时间 > LLM 时间` 且距今 150–900 秒才重启
