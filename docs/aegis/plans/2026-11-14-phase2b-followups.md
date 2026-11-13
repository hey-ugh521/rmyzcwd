# 2B 后续问题清单（不并入 4F-2C）

- 日期：2026-11-14
- 状态：**独立问题单；未批准实施**
- 来源：[4F-2C 设计审查](../reports/PHASE4F2B-PG-SHADOW-VALIDATION-REPORT.md)、[4F-2C 设计稿](2026-11-14-phase4f2c-identity-me-design.md)
- 原则：这些问题属于已冻结的 4F-2B 后续变更；4F-2C PR 不得顺手修复

## 1. Shadow 求值/写库异常静默

- 代码位置：`core/server.py` 的 `_shadow_observe` / worker `except Exception: pass`
- 现状：worker 异常被吞；可能没有 Shadow Event、没有日志、没有失败计数
- 影响：看起来“没有事件 = 正常”，实际可能是执行失败
- 预期：
  - 不影响 Legacy 响应
  - 求值失败可观测
  - 写库失败可观测
  - 不能依赖再写 Shadow Event 证明失败
- 归属：2B 后续变更
- 需要修改冻结代码：是
- 独立审批：未批准

## 2. Worker 生命周期与积压

- 代码位置：`core/server.py` 的 daemon thread + `asyncio.wait_for(0.25)`
- 现状：超时只结束等待；daemon 线程可能继续运行；无队列容量/积压限制
- 影响：生产开启时可能线程堆积；关闭后无法确认遗留线程
- 预期：
  - 明确超时后线程行为
  - 队列上限/背压
  - Shadow 关闭后确认 worker 退出
  - Core 重启时未完成任务处理
- 归属：2B 后续变更
- 需要修改冻结代码：是
- 独立审批：未批准

## 3. `latency_ms` 语义与实现

- 代码位置：`core/identity_shadow.py` 表字段与 `record_shadow_event`
- 现状：定义但从未写入；8/8 NULL；不是“正常值”
- 影响：无法从事件表复现延迟；当前延迟只是客户端测量
- 预期：
  - 明确 evaluation latency / persistence latency 语义
  - 写入真实值，或删除死字段
  - 不把历史 NULL 回填为 0
- 归属：2B 后续变更
- 需要修改冻结代码：是
- 独立审批：未批准

## 4. `request_id` 关联

- 代码位置：`core/server.py` `X-Request-Id` → `evaluate_shadow` → `record_shadow_event`
- 现状：链路存在；8/8 NULL 是受控请求未带 header
- 影响：下次窗口可零代码改动补关联
- 预期：
  - 受控请求带 `X-Request-Id`
  - 校验格式/唯一性
  - 事件可按 request_id 关联
- 4F-2C endpoint 在无 header 时会自生成日志 ID；该 ID 与 Shadow Event
  不是同一来源，不能互相校验
- 归属：验证输入规范，不修改冻结代码
- 独立审批：不需要代码变更；窗口执行规则

## 5. 配置快照与事件清理审计

- 现状：2B 只保留关闭态配置备份；开启态未单独留档；事件清理曾执行
- 影响：窗口参数和清理操作不可完整复核
- 预期：
  - 开启态脱敏快照
  - 清理前导出证据
  - 清理操作有独立记录/批准
  - Shadow Event 与安全审计分离
- 归属：2B 运行流程/后续变更
- 需要修改冻结代码：待定
- 独立审批：未批准

## 6. 不并入 4F-2C

以上 1/2/3/5 不得作为 `/api/identity/me` 的实现内容。4F-2C 设计可以引用，
但实现计划必须把 2B 变更单独列为后续审批项。
