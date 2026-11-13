# 4F-2C 后续问题：线程池与超时资源语义

- 日期：2026-11-14
- 状态：**部署前必须确认；未实现**
- 来源：4F-2C 独立复审
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
- [ ] 是否为 identity 查询使用独立 `CapacityLimiter`/专用有界线程池
- [ ] 是否限制 endpoint 并发数
- [ ] 超时后 worker 的回收/背压策略
- [ ] 线程池占用、超时、排队长度的监控与告警
- [ ] 长稳/故障注入测试

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
