# PG Shadow Validation 执行前证据

- 日期：2026-11-13
- 状态：**准备完成；未开启 Shadow**
- 计划：[2026-11-13-pg-shadow-validation-plan.md](../aegis/plans/2026-11-13-pg-shadow-validation-plan.md)
- 决策：[ADR-017](../adr/ADR-017-pg-shadow-validation.md)

## 1. 当前生产状态

| 项 | 值 |
|---|---|
| Core PID | 63848 |
| Shadow `enabled` | false |
| CFG.identity | None |
| `identity_shadow_events` | 0 |
| Principal | 1（service-legacy / service） |
| LangBot | down |
| Gateway | ready |

## 2. 入口隔离证据

`Get-NetTCPConnection -LocalPort 8900`：

- 3 个 established 连接
- 全部来自本机 Edge/Studio
- 没有外部 IP 连接

结论：**当前未隔离**，Studio 浏览器仍轮询 `/api/tasks`。执行前必须关闭
Studio/浏览器或阻断这些客户端。

## 3. 基线与响应

| endpoint | status | body hash |
|---|---|---|
| `/api/capabilities/permissions` | 200 | `acf335ffe0e6e796` |
| `/studio/` | 200 | `d2856e3e03ad833e` |
| `/api/status` | 200 | 动态（uptime/服务探测） |

`/api/status` 冷启动服务探测约 7.6s，不作为静态 body 兼容基准。

## 4. 活跃任务

`/api/tasks?limit=200`：0 个任务；没有 running/pending/queued。

## 5. 执行前待确认

- [ ] 维护窗口时间
- [ ] 关闭 Studio / Edge 轮询
- [ ] 确认无 `/api`、`/v1` 其他调用者
- [ ] 是否暂停 Gateway
- [ ] 预定义请求清单接受
- [ ] 故障注入只使用隔离环境

以上确认后才可临时开启 Shadow。当前不开启。
