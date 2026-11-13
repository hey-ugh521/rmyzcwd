# ADR-017: PG Shadow Validation 使用维护窗口隔离

- 状态：Accepted（验证阶段临时决策）
- 日期：2026-11-13
- 影响范围：4F-2B PG Shadow 验证；不改变 4F-2B 冻结行为

## Context

4F-2B Shadow middleware 当前是进程级开关 `identity.shadow.enabled`：

- 开启后观察所有 `/api` 和 `/v1` 请求
- 没有请求级白名单
- 未覆盖路由也会写 `not_evaluated` 事件

生产环境还有 Studio 浏览器轮询 `/api/tasks` 等请求。如果直接开启全局
Shadow，会产生非受控事件、额外 DB 写入和延迟。请求级白名单会扩大本阶段
实现范围，暂不实施。

## Decision

PG Shadow Validation 采用 **方案 A：维护窗口隔离**。

| 项 | 决定 |
|---|---|
| 验证方式 | 维护窗口 |
| 请求级白名单 | 暂不实施 |
| Shadow 默认状态 | Disabled |
| 验证流量 | 预定义管理请求 |
| 生产用户流量 | 不参与 |
| 身份写入 | 禁止新增 |
| 认证行为 | 不改变 |
| 验证后 | 立即关闭 Shadow |
| 4F-2C | 不进入 |

## Consequences

- 验证前必须关闭 Studio/浏览器轮询和其他 `/api`、`/v1` 调用者
- Shadow 开启窗口必须短，且每次请求可枚举
- 生产故障注入不做；故障隔离用隔离测试环境证明，并明确标注
- 验证完成必须恢复 `enabled=False`
- 请求级隔离留作独立架构改进，不在本阶段实现

## Exit criteria

- PG 事件成功写入并提交
- 事件语义、reason、policy_version 正确
- 无新增 Principal/Credential
- Legacy status/body 一致
- 事件无凭证秘密
- 关闭后不再产生新事件
- 验证报告与 Git 提交归档
