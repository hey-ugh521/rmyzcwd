# 4F-2C 后续问题：线程池与超时资源语义

- 日期：2026-11-14
- 状态：**部分推进；limiter 只约束等待并发，不约束超时 worker；PG timeout/监控仍待处理**
- 来源：4F-2C 独立复审
- 部署门禁：[4F-2C 前置清单 §E](2026-11-14-phase4f2c-preflight.md)
- 原则：不在 4F-2C 实现中顺手修改；需单独审批

## 1. 现象

`/api/identity/me` 用 `run_in_threadpool` 执行同步 DB 解析，并用
`asyncio.wait_for` 做 5s 响应超时。

实测：

- 超时后 HTTP 快速返回 500
- 底层 worker 线程不会随 `wait_for` 取消而终止，继续运行
- 8 个并发超时请求可以同时占用 8 个 AnyIO worker 线程
- AnyIO 默认线程池上限约 40

## 2. 风险

如果生产 PG 长时间不可用：

- 超时请求持续占用 worker 线程
- worker 池可能被耗尽
- 其他同步 `def` endpoint 和 `run_in_threadpool` 调用会被排队
- 表现为“认证查询慢”扩散为“Core 其他接口慢/不可用”

这不是认证绕过，也不是身份写入；是资源隔离问题。

## 3. 部署前必须决定

- [ ] PG `connect_timeout` / `statement_timeout` 是否设置
- [x] 是否为 identity 查询使用独立 `CapacityLimiter`/专用有界线程池
  - 本地只用 AnyIO `CapacityLimiter(4)` 限制**等待中的解析并发**
  - 这不是专用有界线程池，也不限制超时后继续运行的 OS 线程
- [x] 是否限制 endpoint 并发数
  - 等待并发上限 4（`IDENTITY_ME_MAX_CONCURRENCY`）
  - 超时后 token 释放，后续请求可继续创建 worker
- [ ] 超时后 worker 的回收/背压策略
  - 当前没有主动回收；limiter 不提供该能力
- [ ] 线程池占用、超时、排队长度的监控与告警
  - 当前只有 timeout 结构化日志
- [ ] 长稳/故障注入测试

## 3.1 本地已实现

- `core/identity_api.py`：per-loop AnyIO `CapacityLimiter(4)`
- `abandon_on_cancel=True`，响应超时不等待 worker
- 并发上限测试：6 个**正常完成**的慢请求，峰值不超过 2
- 超时仍返回 500，超时日志包含 sanitized request_id
- 未部署生产；生产仍是旧 endpoint 版本

**边界：** 上述上限只约束正常完成路径上同时等待执行的解析；超时路径的
worker 不受该上限约束。

## 3.2 实测：超时 worker 不受 limiter 约束

独立复审用错开时序探针（limit=1，6 个依次发出的超时请求，
DB sleep 0.4s > timeout 0.05s）得到：

| 指标 | 结果 |
|---|---|
| 状态 | 全部 500 |
| 耗时 | 约 0.06s |
| `started` / `peak_active` | 6 / 6 |
| limiter borrowed | 0 / 1 |
| 响应后仍在跑的 worker | 5 |
| `abandon_on_cancel` True/False | 行为一致 |

结论：

- 超时后 limiter token 立即释放
- 后续请求继续进入 DB 解析，创建新 worker
- limiter 不能防止 PG 挂住时线程累积
- 真正的兜底仍是 PG `connect_timeout` / `statement_timeout` 与有界线程池

## 3.3 仍未解决

- PG 连接/语句超时
- 超时 worker 主动回收
- 指标/告警
- 长稳与故障注入
- 生产部署验证
- limiter 版本不得视为线程池风险关闭

## 4. 不进入 4F-2C 实现

本问题不通过修改 4F-1/2A/2B 冻结内容解决；也不作为 4F-2C 默认补丁。

如果要落地，必须单独批准并单独验收，证明：

- Legacy 行为不变
- endpoint 只读不变
- 线程池不会因超时耗尽
- 关闭/重启后无遗留 worker

## 5. 当前状态

- 4F-2C 本地实现仍使用 `run_in_threadpool` + 5s timeout
- 未部署生产
- 该资源语义未通过生产长稳验证
