# watchdog 空闲重启策略 执行报告（P2）

> 执行时间：2026-11-02 00:04–00:08
> 结果：**三个场景全部验证通过**

---

## 1. 问题

watchdog 发现 MCP 异常时会**立即重启 LangBot**，会打断用户正在进行的对话（此前多次发生：生图/回复被中断）。

## 2. 设计

在 `restart()` 内部对 **LangBot 的 MCP 类重启**做统一门控（硬故障如健康检查失败不受影响）：

```
有活动（running run > 0 或 60s 内有消息）→ 推迟，记录日志
最老 running run > 600s              → 判定卡死，强制重启
累计推迟 > 900s                      → 强制重启
无活动                              → 立即重启（与原行为一致）
```

可调参数（环境变量）：

| 变量 | 默认 | 含义 |
|---|---|---|
| `MOBIUS_DEFER_IDLE_SEC` | 60 | 最后消息在多少秒内算"忙碌" |
| `MOBIUS_DEFER_RUN_STUCK_SEC` | 600 | run 超过多少秒算卡死 |
| `MOBIUS_DEFER_MAX_SEC` | 900 | 最多推迟多久后强制重启 |

活动判定直接读 LangBot 的 PG（`agent_run` + `monitoring_messages`），不依赖 API。

## 3. 验证（故障注入）

| 场景 | 构造 | 结果 |
|---|---|---|
| **A. 忙时延迟** | 插入 running run + 注入 MCP 失败日志 | ✅ 输出「重启门控：延迟重启 LangBot…运行中 run=1」，**LangBot 未重启** |
| **B. 空闲重启** | 删除 fake run | ✅ 正常触发重启 |
| **C. 卡死强制** | 插入 700s 前的 running run | ✅ 「最老 run 已 704s（>600s），判定卡死，强制重启」 |

测试后已清理：fake run 删除、注入的日志行移除（容器内清理，0 条残留）。

## 4. 当前状态

- watchdog 已加载最终版（sha256 `e407ade4…`，仓库副本一致）
- LangBot 运行中，MCP 会话正常，NapCat WS ESTABLISHED
- 旧行为保留：健康检查失败等硬故障仍然立即重启（不推迟）

## 5. 遗留

- 门控只覆盖 "MCP" 类原因；如果未来新增自愈动作，需要按同样规则接入
- 门控判定依赖 PG 可读；PG 不可读时按空闲处理（日志可见）
- `qqbot/watchdog.py` 的运行副本已通过 `ops/sync_watchdog.py --capture` 同步到仓库
