# 4F-2C 强制 worker 回收方案（Lead）

- 日期：2026-11-14
- 状态：**方案评审稿；未实现、未部署**
- 关联：
  - [4F-2C followups](2026-11-14-phase4f2c-followups.md)
  - [4F-2C 部署报告](../reports/PHASE4F2C-IDENTITY-API-REPORT.md)
- 边界：不开启 Shadow，不修改 4F-1/2A/2B，不部署生产

## 1. 现状

生产已部署：

- `asyncio.Semaphore(4)` 限制 in-flight identity 解析
- 槽位由 worker 完成后释放；超时不释放
- `PG_CONNECT_TIMEOUT=5s`
- identity worker `statement_timeout=5000ms`
- 进程内 `stats_snapshot()`
- 周期 INFO 日志（生产 root WARNING，实际不可见）

已证明：

- 超时请求不会创建超过 4 个 DB worker
- worker 完成后槽位恢复
- 超时后 worker 异常可记录

未证明：

- 若 worker 在 `statement_timeout` 之外仍不退出，如何强制回收
- 长时间挂住时线程/进程资源上界
- 生产长稳

## 2. 关键结论

**Python 线程不能安全强制 kill。**

- `ThreadPoolExecutor` 没有 `terminate`
- `asyncio` 取消不能终止正在执行的同步 C/IO 调用
- `psycopg` 查询通常会被 `statement_timeout` 终止，但驱动/网络挂死时
  不能保证

因此“强制回收”不能靠给线程发 kill；只有两类真正方案：

### A. 进程隔离

把 identity DB 解析放进可终止的子进程：

- 超时后 `Process.terminate()` / `kill()`
- 子进程持有的 DB 连接随进程退出
- 父进程槽位在子进程退出后释放
- 线程池不会积累

代价：

- 每次/每槽位进程启动成本
- PG 连接数增加
- 需要 IPC 传递状态，不能传日志秘密
- Windows spawn 行为需单独验证
- 子进程必须只读，不能写身份表

### B. DB 连接取消/关闭

在另一个控制面拿到当前查询的连接，调用 `cancel()` / `close()`：

- 比进程隔离轻
- 需要 store 暴露活动连接句柄/注册表
- 需要保证取消只打中当前请求的连接
- `psycopg` 取消语义、线程安全需生产验证
- 属于 `store.py` 共享基础设施变更，需单独审批

## 3. 推荐路线

**优先 B，失败则 A；不要退回无界线程。**

理由：

- 当前 endpoint 只读、低频；进程隔离的复杂度/资源成本高于先试连接取消
- B 若能验证，改动面小于进程化
- 若 B 无法安全验证，A 是唯一真正的强制回收
- 两者都不能改变 Legacy 放行或身份写入

## 4. B 的实施轮廓（待批准）

1. `store.pg_timeouts` 增加 `cancel_key`（或等价 request 关联键）
2. `_PgConnection` 创建时注册到进程内活动连接表；关闭时注销
3. `store.cancel(cancel_key)` 只取消对应连接
4. `identity_api` 超时路径调用 `store.cancel(state["cancel_key"])`
5. worker 退出后释放槽位；统计 `cancel_requested` / `cancel_failed`
6. 故障注入：
   - 查询被 cancel → worker 退出
   - cancel 失败/连接已关闭 → 槽位仍恢复
   - 并发请求 cancel 不串
7. 需要 `store.py` 单独批准与生产验证

## 5. A 的实施轮廓（B 不可行时）

1. 新建受限 `multiprocessing` context（spawn）
2. 子进程只执行 identity 只读解析
3. 父进程 timeout 后 terminate/kill
4. 最多 4 个 identity 子进程；不引入无界队列
5. 子进程退出后释放槽位
6. 测试：
   - 卡死子进程被 kill
   - 父进程槽位恢复
   - 后续请求 200
   - 并发上限
   - 无身份写入
7. 需要生产资源/安全评审

## 6. 不做什么

- 不 kill 线程
- 不通过 `ThreadPoolExecutor` 队列无限堆积
- 不在 4F-2C 里顺手修改 Shadow
- 不把 `stats_snapshot()` 当生产证据
- 不通过增大 semaphore 隐藏问题

## 7. 验收边界

- 方案正确 != 代码正确
- 代码正确 != 生产正确
- 必须先有故障注入证据，再讨论部署
- 当前生产没有强制回收；只有有界槽位 + statement timeout + 快速失败

## 8. 下一步

1. 批准 B 或 A 的试验范围
2. 在本地实现最小原型 + 故障注入
3. 独立复审
4. 再决定是否生产部署
