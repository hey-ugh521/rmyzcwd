# D2 报告：watchdog 两段式自愈（重复故障 → DSH 只读诊断）

> 执行时间：2026-11-02 11:41–11:48
> 结果：**通过**（含降级路径与冷却期验证）

---

## 1. 设计

```
故障发生 → 现有规则立即处置（重启等，第一段）
        → 同因故障 30 分钟内第 2 次
        → 唤起 DSH headless（只读 profile）诊断
        → 报告写入 logs/incidents/incident-<时间>-<原因>.md
        → 不自动执行任何处置动作（第二段只诊断）
```

约束：
- 只读：`ops/dsh/mobius-ops.yml` 禁用 pwsh/bash/plugin-manager，仅保留 fs 读取 + 只读 MCP
- 冷却：同因故障 6 小时内最多诊断一次；DSH 不可用时降级为纯规则
- 不阻塞：DSH 以独立进程后台运行，watchdog 主循环不受影响

## 2. 实现

| 文件 | 改动 |
|---|---|
| `qqbot/watchdog.py` | 新增 `check_incident_escalation()` / `_escalate_incident()`，挂在 `restart()` 内 |
| 报告目录 | `mobius/logs/incidents/`（Markdown + 同名 `.stderr.log` 诊断过程） |
| 状态文件 | `qqbot/.incident_state.json`（次数/时间/冷却） |

调用方式：`node bin.js --profile headless --patch ops/dsh/mobius-ops.yml "<诊断提示词>"`，stdout 落报告、stderr 落过程日志。

## 3. 验收

| # | 标准 | 结果 |
|---|---|---|
| D2.1 | 同因第 2 次触发 DSH | ✅ 第 1 次 False、第 2 次 True、第 3 次冷却期 False |
| D2.2 | 报告生成且内容有效 | ✅ 14,861 字节，含「现象/根因/建议/复现条件/读不到项」 |
| D2.3 | 只读约束生效 | ✅ 全程无写操作；DSH 只读 fs 与 MCP |
| D2.4 | DSH 不可用时降级 | ✅ 打印「dsh 或 patch 不存在，跳过」，返回 False，不报错 |
| D2.5 | 不阻塞 watchdog | ✅ 后台进程，主循环正常 |

## 4. DSH 在测试诊断中发现的真实问题（待修）

1. **网关重启会击穿 MCP 会话**：11:37 网关重启后，LangBot 旧会话被拒（`Rejected request with unknown or expired session ID`），约 13–20 秒无响应
2. **watchdog「投递-调用滞后」指标存在时区取数错误**：显示 72,059 秒，疑似 8 小时偏移；曾据此误重启 LangBot
3. **探针只记录到达 Core 的消息**：在 LangBot/OneBot 层被拦下的消息不留痕，不能凭「无记录」判断未丢消息

## 5. 遗留

- D3：定时巡检（每日健康报告 / 周审计 / 月度恢复演练）
- 上面 3 个真实问题按优先级修复（建议先修 #2，因为它会导致误重启）
- incident 报告目前只落本地文件，未接入通知渠道
