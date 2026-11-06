# Redis 用途决策与落地：共享轮询节流

> 时间：2026-11-02 11:54–11:56

## 决策

Redis 不再闲置，**第一个消费方 = 媒体网关的轮询节流（跨进程/多实例一致）**。

理由：
- 网关 `poll_min_interval` 原为**进程内内存**，多实例部署时每个实例各算各的，契约失效
- 轮询节流是纯瞬时状态，天然适合 Redis（`SET NX PX`），不落库、不影响任务状态机
- Redis 已是本机独立实例（`127.0.0.1:56379`，requirepass，AOF everysec），接入零新增组件

未选方案：事件扇出（Core 已改成 PG outbox 拉取，pub/sub 是冗余）、缓存（当前无热点读）。

## 实现

| 文件 | 内容 |
|---|---|
| `media_gateway/pollthrottle.py` | 零依赖最小 RESP 客户端 + `SharedPollGate`（Redis 优先，失败回退进程内） |
| `media_gateway/server.py` | `_poll_gate` 改用共享门；终态任务仍不限速；日志带 `backend=` |
| `config.json` | gateway env 增加 `MOBIUS_REDIS_URL` |

## 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | 跨进程节流生效 | ✅ 进程 A 占用后，进程 B 立即请求被拒（retry≈7.7s） |
| 2 | 间隔到期放行 | ✅ 5.2s 后同 key 放行 |
| 3 | 多线程并发只放行 1 个 | ✅ 3 线程 × 3 轮，每轮放行数=1，backend=redis |
| 4 | Redis 不可用回退 | ✅ 错误密码 URL → backend=local，行为退化为进程内，不报错 |
| 5 | 网关冒烟 | ✅ 两次快速轮询：第 1 次 pending，第 2 次 `poll_too_fast` retry=4734ms，日志 `backend=redis` |
| 6 | 无测试残留 | ✅ 临时脚本与 db9 测试键已清理 |

## 已确认的行为

- 网关日志：`轮询节流共享门：backend=redis`、`poll_too_fast ... backend=redis`
- 终态任务（completed/failed/cancelled）不受节流，避免结果读取被误拦
- Redis 挂掉不影响出图，只退化为单实例节流

## 后续

- 若将来网关多实例化，节流自动变为全局一致，无需改代码
- Redis 的第二用途候选：Core 事件去重缓存（当前 DB 去重已够，暂不接）
