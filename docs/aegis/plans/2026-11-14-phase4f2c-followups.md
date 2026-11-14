# 4F-2C 后续问题：线程池与超时资源语义

- 日期：2026-11-14
- 状态：**本地已有有界槽位 + statement timeout + 进程内指标/回收测试；未部署；外部指标/长稳仍待处理**
- 来源：4F-2C 独立复审
- 部署门禁：[4F-2C 前置清单 §E](2026-11-14-phase4f2c-preflight.md)
- 原则：不在 4F-2C 实现中顺手修改冻结行为；需单独审批

## 1. 现象

`/api/identity/me` 通过 AnyIO 线程执行同步 DB 解析，并用
`asyncio.wait_for` 做 5s 响应超时。

旧实现实测：

- 超时后 HTTP 快速返回 500
- 底层 worker 线程不会随 `wait_for` 取消而终止
- 8 个并发超时请求可以同时占用 8 个 worker 线程
- AnyIO 默认线程池上限约 40

## 2. 风险

如果生产 PG 长时间不可用：

- 超时请求持续占用 worker 线程
- worker 池可能被耗尽
- 其他同步 `def` endpoint 和 `run_in_threadpool` 调用会被排队
- 表现为“认证查询慢”扩散为“Core 其他接口慢/不可用”

这不是认证绕过，也不是身份写入；是资源隔离问题。

## 3. 部署前必须决定

- [x] PG `connect_timeout` 已设置
  - 本地 `store.PG_CONNECT_TIMEOUT` 默认 5s
  - `CORE_DB_CONNECT_TIMEOUT` 可覆盖；0 禁用
- [x] PG `statement_timeout` 是否设置
  - identity worker 通过 thread-local 设置 5000ms
  - 全局默认 0；`CORE_DB_STATEMENT_TIMEOUT_MS` 可覆盖
- [x] identity 查询有界并发槽位
  - 每 loop `asyncio.Semaphore(4)`
  - 槽位由 worker 完成后释放，请求超时**不释放**
- [x] endpoint 并发/等待上限
  - 最多 4 个 DB worker；其余请求等待槽位并受总超时约束
- [x] 超时 worker 的槽位回收
  - worker finally 释放槽位；statement_timeout 确保 DB 调用退出
  - 超时后 worker 异常现在会记录 error_class
  - [ ] 强制 kill/主动背压：Python 线程不可安全 kill，仍无强制回收
- [x] 进程内指标/结构化日志
  - `stats_snapshot()` 统计请求/成功/超时/后台异常/active/max_active
  - [ ] 外部指标导出与告警仍缺
- [x] 本地槽位恢复/后台异常回归测试
  - [ ] 生产长稳/故障注入仍缺

## 3.1 本地已实现

`core/identity_api.py`：

- per-loop `asyncio.Semaphore(4)`
- 先获取槽位，再提交 worker；槽位在 worker 完成后释放
- 请求超时后 worker 继续运行、继续持有槽位；后续请求只能等待或超时
- `asyncio.shield(task)` 避免响应超时取消 worker
- 总超时 5s；超时返回 500
- `slot_timeout` / `db_timeout` 分别记录
- 超时后 worker 异常记录 `error_class`
- `stats_snapshot()` 进程内指标：请求、成功、401、500、两类超时、
  后台异常、active/max_active worker
- 超时后的槽位恢复与后台异常已有回归测试

`core/store.py`：

- `PG_CONNECT_TIMEOUT` 默认 5s
- `_PgConnection` 传 `connect_timeout`
- identity worker 通过 thread-local 设置 `statement_timeout=5000ms`
- 全局 `PG_STATEMENT_TIMEOUT_MS` 默认 0；`CORE_DB_STATEMENT_TIMEOUT_MS` 可覆盖
- connect timeout 影响所有新 PG 连接；statement timeout 仅 identity worker 默认生效
- 配置值解析：`0` 保持 0；非法值回落默认，不再在 import 时崩溃

## 3.2 超时路径实测

本地测试 `test_timeout_path_is_bounded`：

```text
limit=1
6 个错开时序超时请求（每 10ms 一个）
DB sleep 0.4s > timeout 0.05s
结果：全部 500，started=1
```

说明槽位在超时后仍被 worker 持有，后续请求不能创建新 worker。

旧 limiter 实测（已废弃）会 started=6、peak=6；因此旧实现不能防线程累积。

## 3.3 仍未解决

- 强制 kill/主动背压（线程不可安全 kill；只能限制新 worker）
- 外部指标导出与告警
- 生产长稳与故障注入
- 生产部署验证
- 当前版本不得视为资源风险已完全关闭

## 4. 不进入 4F-2C 实现

- 不通过修改 4F-1/2A/2B 冻结内容解决
- 不作为 4F-2C 默认补丁
- 如需继续治理，必须单独批准并验收：
  - Legacy 行为不变
  - endpoint 只读不变
  - 线程池不会因超时耗尽
  - 关闭/重启后无遗留 worker

## 5. 当前状态

- 本地实现使用有界槽位 + `PG_CONNECT_TIMEOUT=5`
- 未部署生产；生产仍是旧 endpoint / 旧 `store.py`
- 该资源语义未通过生产长稳验证
