# 4F-2C 后续问题：线程池与超时资源语义

- 日期：2026-11-14
- 状态：**生产仍是部署版；本地 stats/cancel 完成但部署被 store 分叉阻断；强制回收/外部指标/长稳仍待处理**
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
  - 周期日志最多每 60s 输出一次 snapshot
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
- 超时后 worker 异常记录 `error_class`；只有**已超时**的 worker 异常计入
  `background_errors`，未超时异常走 endpoint 的 `error_class` 日志
- `stats_snapshot()` 进程内指标：请求、成功、401、500、两类超时、
  后台异常、active/max_active worker、cancel_requested/cancel_failed
- 周期结构化日志已实现（默认 60s，`identity.me stats ...`）
- **但生产日志级别未打通**：Core 由 uvicorn 启动，默认 logging config
  没有 root 键，root 保持 WARNING；`mobius.identity_api` 的 INFO 被过滤
- 生产目录未找到 `identity.me stats` 文本
- 本地新增 Bearer-protected `/api/identity/stats` 只读诊断面，未部署
- 外部指标导出/告警仍未实现
- 超时后的槽位恢复与后台异常已有回归测试

`core/store.py`：

- `PG_CONNECT_TIMEOUT` 默认 5s
- `_PgConnection` 传 `connect_timeout`
- identity worker 通过 thread-local 设置 `statement_timeout=5000ms`
- 本地已有 `pg_timeouts(cancel_key=...)` + `store.cancel_pg()`；未部署
- 全局 `PG_STATEMENT_TIMEOUT_MS` 默认 0；`CORE_DB_STATEMENT_TIMEOUT_MS` 可覆盖
- connect timeout 影响所有新 PG 连接；statement timeout 仅 identity worker 默认生效
- 配置值解析：只接受整数；0 保持 0；负数/小数/inf/非法值回落默认；
  捕获 `OverflowError`，不再在 import 时崩溃

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

### 3.2.1 超时叠加口径

`connect_timeout=5s` 与 `statement_timeout=5s` 是串行两段：

- 建连最多 5s
- 查询执行最多 5s
- worker 退出上限约 **10s**
- endpoint 在 **5s** 就返回 500

所以槽位最坏可能被占用约 10s，而不是 5s；监控/告警阈值必须按 10s
上界设计，不能把 endpoint 5s 当成 worker 已退出。

### 3.2.2 部署硬依赖

`identity_api._resolve_with_slot` 调用 `store.pg_timeouts(...)`：

- 生产旧 `store.py` 没有该符号
- 只替换 `identity_api.py` 会在运行时抛 `AttributeError`，并被兜底成 500
- `identity_api.py` 与 `store.py` 必须一起替换
- 生产与仓库 `store.py` 差约 214 行（WAL 修复 + timeout 框架），需要单独
  验收/最小补丁策略

### 3.2.3 DSN options 约束

`_PgConnection` 用 `options` 参数设置 `statement_timeout`：

- 会覆盖 DSN 中已有的 `options`
- 当前生产 DSN 没有 `options`，本次安全
- 若未来 DSN 添加 `search_path` 等 options，必须合并而不是覆盖

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

- 生产已部署有界槽位 + `PG_CONNECT_TIMEOUT=5` + identity `statement_timeout=5s`
- 生产 endpoint 200/401 与 PG timeout 上下文已验证
- 本地 stats 诊断面 + PG cancel 原型已验证；未部署
- **部署阻断**：新版 identity_api 需要 `cancel_key`/`cancel_pg`，生产最小补丁
  store.py 没有；必须先走 G-01 路径 B
- 强制回收/外部指标/生产长稳仍未关闭
- 该资源语义未通过生产长稳验证
